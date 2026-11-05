"""A/B workspaces and shared flights, committed together in one SQLite CAS.

Scheduling and statistics consume an ordinary Workbench, already scoped to a
group. The active group is only a startup preference, never a request scope.
"""
from copy import deepcopy
from datetime import date, timedelta
from io import BytesIO

from openpyxl import load_workbook

from .storage import LocalStateStore
from .workbench import Workbench
from .workspace_document import WorkspaceError, normalize_document, validate_document
from .configuration_transfer import prepare_import, prepare_export


GROUPS = ('A', 'B')


def check_group(group):
    if group not in GROUPS:
        raise WorkspaceError('班组不存在，请选择A组或B组')
    return group


def _shared(state):
    return {
        'flightTemplates': [{k: f[k] for k in ('flightNo', 'windowStart', 'windowEnd')}
                            for f in state['flightLibrary']],
        'weeklyFlightPresets': deepcopy(state.get('weeklyFlightPresets', {})),
    }


def _project(state, shared):
    state = deepcopy(state)
    local = {f['flightNo']: f for f in state['flightLibrary']}
    state['flightLibrary'] = [{**deepcopy(local.get(f['flightNo'], {
        'roles': [], 'roleNames': [r['roleName'] for r in state['configuration']['roleConfigs']]})),
        **deepcopy(f)} for f in shared['flightTemplates']]
    if shared['weeklyFlightPresets'] or 'weeklyFlightPresets' in state:
        state['weeklyFlightPresets'] = deepcopy(shared['weeklyFlightPresets'])
    return state


class _GroupStore:
    def __init__(self, owner, group):
        self.owner, self.group = owner, check_group(group)

    def read_document(self, key):
        root = self.owner._load()
        return _project(root['groups'][self.group], root['shared'])

    def compare_and_save(self, key, expected_revision, state):
        for _ in range(5):
            root = self.owner._load()
            if root['groups'][self.group]['revision'] != expected_revision:
                raise WorkspaceError('班组数据已更新，请重新读取；当前输入未被覆盖')
            root_revision = root['revision']
            shared = _shared(state)
            root['groups'][self.group] = deepcopy(state)
            if shared != root['shared']:
                root['shared'] = shared
                for other in GROUPS:
                    if other != self.group:
                        root['groups'][other] = _project(root['groups'][other], shared)
                        root['groups'][other]['revision'] += 1
            try:
                self.owner._save(root)
                return
            except ValueError:
                if self.owner._load()['revision'] == root_revision:
                    raise
                # Another group or startup preference may change independently.
                # Reload and recheck this group's revision before merging again.
        raise WorkspaceError('班组数据正在更新，请稍后重试；当前输入未被覆盖')


class GroupWorkspaces:
    KEY = 'group-workspaces-v1'

    def __init__(self, database_path, *, store=None):
        self.path = database_path
        self.store = store if store is not None else LocalStateStore(database_path)

    def initialize(self):
        if self.store.read_document(self.KEY) is not None:
            self._validate(self._load())
            return
        a = normalize_document(self.store.read_document(Workbench.KEY))
        validate_document(a)
        b = deepcopy(a)
        b.update(revision=0, people=[], plans={}, selectedPlanId=None, migrationReport=[])
        b.pop('trialInfo', None)
        b['balanceSettings']['priorityAdjustments'] = []
        b['dutyRoster']['anchorDate'] = (date.fromisoformat(a['dutyRoster']['anchorDate']) + timedelta(days=1)).isoformat()
        b['dutyRoster']['overrides'] = []
        # Qualification pools have no people until the owner configures B.
        for key, value in b['dutyRoster'].items():
            if isinstance(value, list):
                b['dutyRoster'][key] = []
        root = {'formatVersion': 3, 'revision': 0, 'activeGroupId': 'A',
                'shared': _shared(a), 'groups': {'A': a, 'B': b}}
        self._validate(root)
        self.store.compare_and_save(self.KEY, 0, root)

    def _load(self):
        root = self.store.read_document(self.KEY)
        if root is None:
            self.initialize()
            root = self.store.read_document(self.KEY)
        return root

    def _validate(self, root):
        if (not isinstance(root, dict) or root.get('formatVersion') != 3
                or set(root.get('groups', {})) != set(GROUPS)
                or root.get('activeGroupId') not in GROUPS
                or type(root.get('revision')) is not int or root['revision'] < 0):
            raise WorkspaceError('A/B班组数据缺失或版本无效；当前数据保持')
        for group in GROUPS:
            state = root['groups'][group]
            validate_document(state)
            if _shared(state) != root['shared']:
                raise WorkspaceError('班组共享航班数据不一致；当前数据保持')

    def _save(self, root):
        expected = root['revision']
        root['revision'] += 1
        self._validate(root)
        self.store.compare_and_save(self.KEY, expected, root)

    def workbench(self, group):
        return Workbench(self.path, store=_GroupStore(self, group))

    def info(self):
        root = self._load()
        return {'activeGroupId': root['activeGroupId'],
                'groups': [{'id': g, 'name': f'{g}组'} for g in GROUPS]}

    def select(self, group):
        check_group(group)
        for _ in range(5):
            root = self._load()
            root_revision = root['revision']
            root['activeGroupId'] = group
            try:
                self._save(root)
                return self.info()
            except ValueError:
                if self._load()['revision'] == root_revision:
                    raise
        raise WorkspaceError('班组数据正在更新，请稍后切换')

    def backup(self):
        return deepcopy(self._load())

    def preview_restore(self, group, backup):
        wb = self.workbench(group)
        if not isinstance(backup, dict):
            raise WorkspaceError('备份必须为完整对象')
        if backup.get('formatVersion') != 3:
            return {**wb.preview_restore(backup), 'scopeLabel': f'仅替换{group}组（共享航班同步）'}
        candidate = deepcopy(backup)
        self._validate(candidate)
        root = self._load()
        return {'expectedRevision': root['groups'][check_group(group)]['revision'], 'expectedGroupsRevision': root['revision'],
                'scopeLabel': '替换A/B两组全部数据',
                'historyPlanCount': sum(len(s['plans']) for s in candidate['groups'].values()),
                'peopleCount': sum(len(s['people']) for s in candidate['groups'].values())}

    def restore(self, group, expected, backup, expected_groups=None):
        check_group(group)
        if not isinstance(backup, dict):
            raise WorkspaceError('备份必须为完整对象')
        if backup.get('formatVersion') != 3:
            return self.workbench(group).restore(expected, backup)
        candidate = deepcopy(backup)
        self._validate(candidate)
        root = self._load()
        if (root['groups'][group]['revision'] != expected
                or expected_groups is not None and root['revision'] != expected_groups):
            raise WorkspaceError('预览后班组数据已更新，请重新导入')
        for g in GROUPS:
            old_revision = candidate['groups'][g]['revision']
            new_revision = root['groups'][g]['revision'] + 1
            for plan in candidate['groups'][g]['plans'].values():
                undo = plan.get('undoEdit')
                if undo and undo['atRevision'] == old_revision:
                    undo['atRevision'] = new_revision
                else:
                    plan.pop('undoEdit', None)
            candidate['groups'][g]['revision'] = new_revision
        candidate['revision'] = root['revision']
        self._save(candidate)
        return self.workbench(group).snapshot()

    def export_configuration(self, group, plan_id=None, service_date=None):
        from .group_excel import append_groups
        root = self._load()
        # Export both from the same captured revision, even during background work.
        check_group(group)
        prepared = {}
        for g in GROUPS:
            prepared[g] = prepare_export(_project(root['groups'][g], root['shared']),
                                         plan_id if g == group else None, service_date)
        configs = {g: prepared[g].payload for g in GROUPS}
        book = load_workbook(BytesIO(prepared[group].excel()))
        append_groups(book, {'formatVersion': 1, 'selectedGroupId': group, 'configurations': configs})
        stream = BytesIO()
        book.save(stream)
        book.close()
        return stream.getvalue()

    def _configuration_candidate(self, group, payload):
        root = self._load()
        transfer = payload.get('groupTransfer')
        if transfer is None:
            return None
        if (not isinstance(transfer, dict) or transfer.get('formatVersion') != 1 or transfer.get('selectedGroupId') != group
                or set(transfer.get('configurations', {})) != set(GROUPS)):
            raise WorkspaceError('请切换到文件标注的班组后导入；A/B附表必须完整')
        for g in GROUPS:
            data = payload if g == group else transfer['configurations'][g]
            state = _project(root['groups'][g], root['shared'])
            # The same candidate preparation owns IDs, history and validation in
            # both single-group and all-group imports. Nothing is saved here.
            root['groups'][g] = prepare_import(state, data).state
        root['shared'] = _shared(root['groups'][group])
        root['activeGroupId'] = group
        for g in GROUPS:
            root['groups'][g] = _project(root['groups'][g], root['shared'])
        self._validate(root)
        return root

    def preview_configuration(self, group, payload):
        wb = self.workbench(group)
        result = wb.preview_complete_configuration(payload)
        candidate = self._configuration_candidate(group, payload)
        if candidate is not None:
            result['payload']['groupTransfer'] = deepcopy(payload['groupTransfer'])
            result['payload']['expectedGroupsRevision'] = candidate['revision']
            result['scopeLabel'] = '导入A/B两组配置与历史；前面维护表属于' + group + '组'
        else:
            result['scopeLabel'] = f'旧单组文件：仅导入{group}组，共享航班同步'
        return result

    def import_configuration(self, group, expected, payload):
        candidate = self._configuration_candidate(group, payload)
        if candidate is None:
            return self.workbench(group).execute(expected, 'import-complete-configuration', payload)
        if (candidate['groups'][group]['revision'] != expected + 1
                or payload.get('expectedGroupsRevision') != candidate['revision']):
            raise WorkspaceError('预览后班组数据已改变，请重新导入；两组原数据保持')
        self._save(candidate)
        return self.workbench(group).snapshot()
