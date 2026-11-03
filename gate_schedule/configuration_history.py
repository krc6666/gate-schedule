"""Merge validated configuration history without replacing local plans."""
from copy import deepcopy
from .priority_statistics import record_date


def _remap_members(value, identities, field=None):
    if field in {'memberId', 'dutyMemberId'}:
        return identities.get(value, value)
    if field in {'lockedMemberIds', 'candidateMemberIds', 'teamLeaderIds'}:
        return [identities.get(v, v) for v in value]
    if field == 'memberIds':
        return {name: identities.get(identity, identity) for name, identity in value.items()}
    if field == 'personnelStates':
        return {identities.get(identity, identity): status for identity, status in value.items()}
    if field == 'members':
        return [{**deepcopy(member), 'id': identities.get(member['id'], member['id'])} for member in value]
    if isinstance(value, dict):
        return {key: _remap_members(child, identities, key) for key, child in value.items()}
    if isinstance(value, list):
        return [_remap_members(child, identities) for child in value]
    return value


def merge_history(state, incoming):
    """Caller validates the source and merged document before one CAS commit."""
    by_id = {p['id']: p for p in state['people']}
    by_name = {p['name']: p for p in state['people'] if not p.get('deleted', False)}
    identities = {}
    for person in incoming['people']:
        local = by_id.get(person['id'])
        if local is None and not person.get('deleted', False):
            local = by_name.get(person['name'])
        if local is None:
            local = deepcopy(person)
            state['people'].append(local)
            by_id[local['id']] = local
            if not local.get('deleted', False):
                by_name[local['name']] = local
        elif local.get('deleted', False) != person.get('deleted', False):
            raise ValueError('历史人员身份的删除状态与本机冲突，请先核对人员；原数据未改变')
        identities[person['id']] = local['id']
    local_dates = {record_date(p['adopted']) for p in state['plans'].values() if p.get('adopted')}
    imported = {}
    retained = 0
    # A source may contain several formals on one date. Keep their original rank.
    ranked = sorted(enumerate(incoming['plans'].items()), key=lambda item:
        (item[1][1].get('adopted') or {}).get('savedRevision', -1))
    for _, (identity, source) in ranked:
        if identity in state['plans']:
            retained += 1
            continue
        plan = _remap_members(source, identities)
        formal = plan.get('adopted')
        if formal and record_date(formal) in local_dates:
            # Existing device history wins on that date, including old unranked formals.
            formal.pop('savedRevision', None)
        undo = plan.get('undoEdit')
        if undo:
            if undo['atRevision'] == incoming['revision']:
                undo['atRevision'] = state['revision'] + 1
            else:
                plan.pop('undoEdit')
        imported[identity] = plan
    state['plans'] = {**imported, **state['plans']}
    return {
        'historyDayCount': len({p['day']['serviceDate'] for p in incoming['plans'].values()}),
        'historyPlanCount': len(incoming['plans']),
        'newHistoryPlanCount': len(imported),
        'retainedHistoryPlanCount': retained,
    }
