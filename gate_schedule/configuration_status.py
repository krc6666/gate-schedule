"""Generation receipts: saved configuration is distinct from an old schedule."""
import hashlib
import json
from .domain import empty_work_rules, single_switch_role_settings, phase_role_config, ImportedRoleConfig


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def generation_context(state, plan):
    people = [{k: p.get(k, default) for k,default in
               [('id',None),('name',None),('active',True),('deleted',False),('status','正常'),('teamLeader',False)]}
              | {'eligibleRoleNames': sorted(p['eligibleRoleNames'])} for p in state['people'] if not p.get('deleted', False)]
    configuration = dict(state['configuration'])
    configuration['roleConfigs'] = [single_switch_role_settings({**r,
        'workMode':r.get('workMode',phase_role_config(ImportedRoleConfig(r['roleName'],r['requiredCount'])).work_mode),
        'exclusiveSameFlight':r.get('exclusiveSameFlight',False)}) for r in configuration['roleConfigs']]
    facts = {
        '航班与岗位': [sorted(state['flightLibrary'],key=lambda f:f['flightNo']), configuration],
        '人员与资质': sorted(people, key=lambda p: p['id']),
        '接续兼职': state['handoffPreferences'],
        '大件与岗位限制': state.get('workRules', empty_work_rules()),
        '保岗与均衡': [state['balanceSettings'], state['teamLeaderSettings'],
                      state['halfRestSettings'], {k:v for k,v in state['relaxedShiftSettings'].items() if k != 'statisticsFlightNos'}, state['dutyRoster']],
        '当天输入': [plan['day'], plan['rotationEnabled'], plan['rotationMinutes']],
    }
    return {'version': 1, 'categories': {name: _digest(value) for name, value in facts.items()}}


def configuration_status(state, plan):
    baseline = plan.get('configurationBaseline')
    if baseline is None:
        legacy = bool(plan.get('draft') or plan.get('assignments') or plan.get('adopted') or plan.get('lastEdit'))
        return {'stale': legacy, 'changed': ['旧班表尚未核对当前设置'] if legacy else []}
    current = generation_context(state, plan)
    changed = [name for name, value in current['categories'].items()
               if baseline.get('version') != current['version'] or baseline['categories'].get(name) != value]
    return {'stale': bool(changed), 'changed': changed}


def validate_generation_baseline(value):
    if (not isinstance(value, dict) or set(value) != {'version', 'categories'} or value['version'] != 1
            or not isinstance(value['categories'], dict)
            or any(not isinstance(k, str) or not isinstance(v, str) or len(v) != 64
                   or any(c not in '0123456789abcdef' for c in v) for k, v in value['categories'].items())):
        raise ValueError('班表配置版本记录无效，原资料未改变')
