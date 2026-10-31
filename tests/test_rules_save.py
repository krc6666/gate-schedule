from copy import deepcopy
import unittest
import test_balance_workflow as fixtures
from gate_schedule.workbench import Workbench, WorkspaceError
from gate_schedule.excel_io import import_complete_configuration


class RulesSaveTests(unittest.TestCase):
    setUp = fixtures.BalanceWorkflowTests.setUp
    command = fixtures.BalanceWorkflowTests.command
    setup_plan = fixtures.BalanceWorkflowTests.setup_plan
    fill = fixtures.BalanceWorkflowTests.fill
    def payload(self):
        state = self.workbench.snapshot()
        return {'configuration': {**state['configuration'], 'transitionMinutes': 7},
                'balanceSettings': {**state['balanceSettings'], 'flightCountGap': 1},
                'preferences': state['handoffPreferences']}

    def test_unified_save_is_atomic_and_preserves_plans(self):
        self.setup_plan()
        before = deepcopy(self.workbench.snapshot())
        state = self.command('save-rules', self.payload())
        self.assertEqual(before['revision'] + 1, state['revision'])
        self.assertEqual(7, state['configuration']['transitionMinutes'])
        self.assertEqual(1, state['balanceSettings']['flightCountGap'])
        self.assertEqual(before['plans'], state['plans'])
        reopened = Workbench(self.path).snapshot()
        self.assertEqual(state['balanceSettings'], reopened['balanceSettings'])
        self.assertEqual(state['configuration'], reopened['configuration'])

    def test_late_invalid_field_and_stale_version_never_partially_save(self):
        self.setup_plan()
        before = self.workbench.backup()
        payload = self.payload()
        payload['preferences'] = [{'flightNo': '*', 'arrivalRole': '不存在', 'boardingRoles': ['不存在'], 'enabled': True}]
        with self.assertRaises((WorkspaceError, ValueError)):
            self.command('save-rules', payload)
        self.assertEqual(before, self.workbench.backup())
        with self.assertRaises(WorkspaceError):
            self.workbench.execute(-1, 'save-rules', self.payload())
        self.assertEqual(before, self.workbench.backup())

    def test_day_change_keeps_formal_and_clears_invalidated_draft(self):
        pid = self.setup_plan()
        self.fill(pid)
        self.command('adopt', {}, pid)
        before = deepcopy(self.workbench.snapshot()['plans'][pid])
        payload = self.payload()
        payload['input'] = {'day': {**before['day'], 'transitionMinutes': 0}, 'rotationEnabled': False, 'rotationMinutes': 50}
        state = self.command('save-rules', payload, pid)
        current = state['plans'][pid]
        self.assertEqual(before['adopted'], current['adopted'])
        self.assertEqual([], current['draft'])
        self.assertEqual(before['inputRevision'] + 1, current['inputRevision'])
        self.assertEqual(50, current['rotationMinutes'])

    def test_unified_save_excel_empty_device_restart_and_actual_use(self):
        pid = self.setup_plan()
        for role in self.workbench.snapshot()['configuration']['roleConfigs']:
            if role['roleName'] != '接机1':
                self.command('save-role', {'roleName': role['roleName'], 'requiredCount': 0})
        payload = self.payload()
        payload['configuration']['transitionMinutes'] = 0
        payload['configuration']['rotationMinutes'] = 45
        payload['balanceSettings']['flightCountGap'] = 0
        payload['balanceSettings']['priorityEnabled'] = True
        payload['balanceSettings']['priorityRoleNames'] = ['接机1']
        self.command('save-rules', payload)
        incoming = import_complete_configuration(self.workbench.export_complete_configuration(pid))
        other_path = self.path.with_name('other-device.sqlite3')
        other = Workbench(other_path)
        other.execute(other.snapshot()['revision'], 'import-complete-configuration', incoming)
        other = Workbench(other_path)
        state = other.snapshot()
        self.assertEqual(payload['balanceSettings'], state['balanceSettings'])
        self.assertEqual(45, state['configuration']['rotationMinutes'])
        pid = state['selectedPlanId']
        solved = other.execute(state['revision'], 'solve', {}, pid)['plans'][pid]
        self.assertTrue(solved['valid'])
        self.assertEqual([1, 1], sorted(row['count'] for row in solved['schedule']['memberFlights']))
        self.assertEqual(0, solved['balanceSettings']['flightCountGap'])
