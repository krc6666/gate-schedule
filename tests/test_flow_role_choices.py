from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from gate_schedule.domain import flow_rule_roles
from gate_schedule.excel_io import import_complete_configuration
from gate_schedule.group_workspaces import GroupWorkspaces
from gate_schedule.workbench import Workbench
from tests.test_coverage_order import configured


ROOT = Path(__file__).resolve().parents[1]


class FlowRoleChoiceTests(unittest.TestCase):
    def test_dedicated_roles_excluded_and_baggage_aliases_follow_physical_role(self):
        names = ['督导', '接机1', '登机1', '大件行李拦截']
        configs = [{'roleName': role, 'exclusiveSameFlight': role in {'督导', '大件行李拦截'}} for role in names]
        old = [{'flightNo': '*', 'arrivalRole': '大件9', 'boardingRoles': ['督导'], 'enabled': True}]
        self.assertEqual(['接机1', '登机1'], flow_rule_roles(names, old, role_configs=configs))
        configs[-1]['exclusiveSameFlight'] = False
        result = flow_rule_roles(names, old, role_configs=configs)
        self.assertTrue({'大件行李拦截', '大件1', '大件2', '大件3', '大件9'} <= set(result))
        self.assertNotIn('督导', result)
        self.assertIn('督导', flow_rule_roles(names), '旧调用无专岗事实时不推测')

    def test_save_restart_legacy_references_migration_and_cancel_dedicated(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='flow-role-choices-') as directory:
            work = Workbench(Path(directory) / 'source.db')
            data = configured()
            data['handoffPreferences'] = [{'flightNo': '*', 'arrivalRole': '登机主验', 'boardingRoles': ['秩序维护'], 'enabled': True}]
            state = work.execute(work.snapshot()['revision'], 'import-complete-configuration', data)
            before = deepcopy(state['handoffPreferences'])
            self.assertNotIn('登机主验', state['flowRuleRoles'])
            self.assertNotIn('秩序维护', state['flowRuleRoles'])
            self.assertEqual(before, state['handoffPreferences'], '旧引用不得被过滤目录自动改写')
            state = work.execute(state['revision'], 'save-role', {'roleName': '秩序维护', 'exclusiveSameFlight': False})
            self.assertIn('秩序维护', state['flowRuleRoles'])
            self.assertNotIn('登机主验', state['flowRuleRoles'])
            incoming = import_complete_configuration(work.export_complete_configuration())
            target = Workbench(Path(directory) / 'target.db')
            target.execute(target.snapshot()['revision'], 'import-complete-configuration', incoming)
            target = Workbench(Path(directory) / 'target.db')
            state = target.snapshot()
            self.assertEqual(before, state['handoffPreferences'])
            self.assertIn('秩序维护', state['flowRuleRoles'])
            self.assertNotIn('登机主验', state['flowRuleRoles'])
            settings = {**state['balanceSettings'], 'coverageRoleOrder': []}
            state = target.execute(state['revision'], 'update-balance-settings', {'balanceSettings': settings})
            self.assertEqual(before, state['handoffPreferences'], '保存无关规则不清除旧专岗引用')

    def test_group_isolation(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='flow-role-groups-') as directory:
            groups = GroupWorkspaces(Path(directory) / 'state.db')
            groups.initialize()
            for group in ('A', 'B'):
                work = groups.workbench(group)
                work.execute(work.snapshot()['revision'], 'import-complete-configuration', configured())
            work = groups.workbench('B')
            work.execute(work.snapshot()['revision'], 'save-role', {'roleName': '登机主验', 'exclusiveSameFlight': False})
            self.assertNotIn('登机主验', groups.workbench('A').snapshot()['flowRuleRoles'])
            self.assertIn('登机主验', groups.workbench('B').snapshot()['flowRuleRoles'])
