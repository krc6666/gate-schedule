from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from gate_schedule.workbench import Workbench, WorkspaceError
from gate_schedule.group_workspaces import GroupWorkspaces
from gate_schedule.excel_io import import_complete_configuration
from tests.test_balance_workflow import small_configuration


class SimpleWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp')
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)/'state.sqlite3'
        self.wb = Workbench(self.path)

    def command(self, action, payload=None, pid=None, wb=None):
        wb = wb or self.wb
        return wb.execute(wb.backup()['state']['revision'], action, payload or {}, pid)

    def setup_plan(self):
        state = self.command('import-complete-configuration', small_configuration(None))
        pid = state['selectedPlanId']
        self.command('solve', pid=pid)
        return pid

    def test_same_day_reuses_result_and_failed_reschedule_keeps_everything(self):
        pid = self.setup_plan()
        self.command('adopt', pid=pid)
        before = self.wb.backup()['state']
        day = deepcopy(before['plans'][pid]['day'])
        day['memberAvailability'][0]['participating'] = False
        state = self.command('prepare-solve', {'day': day}, pid)
        self.assertEqual(pid, state['selectedPlanId'])
        self.assertEqual(set(before['plans']), set(state['plans']))
        self.assertEqual(before['plans'][pid]['adopted'], state['plans'][pid]['adopted'])
        saved = self.wb.backup()
        bad = deepcopy(day)
        bad['memberAvailability'] = [{**a, 'participating': False} for a in day['memberAvailability']]
        with self.assertRaises(WorkspaceError):
            self.command('prepare-solve', {'day': bad}, pid)
        self.assertEqual(saved, self.wb.backup())
        state = self.command('new-plan', {'serviceDate': day['serviceDate']})
        self.assertEqual(pid, state['selectedPlanId'])
        self.assertEqual(1, len(state['plans']))

    def test_new_date_carries_flights_and_attendance_across_month_restart_and_excel(self):
        config = small_configuration(None)
        config['day']['serviceDate'] = '2026-11-29'
        config['day']['memberAvailability'][1]['participating'] = False
        state = self.command('import-complete-configuration', config)
        pid = state['selectedPlanId']
        self.wb = Workbench(self.path)
        state = self.command('new-plan', {'serviceDate': '2026-12-01'})
        current = state['plans'][state['selectedPlanId']]
        self.assertEqual(['F0', 'F1'], [f['flightNo'] for f in current['day']['flights']])
        self.assertEqual({'甲': True, '乙': False}, {a['memberName']: a['participating'] for a in current['day']['memberAvailability']})
        self.assertEqual([], current['draft'])
        person = next(p for p in state['people'] if p['name'] == '甲')
        self.command('save-person', {**person, 'status': '休假'})
        state = self.command('new-plan', {'serviceDate': '2027-01-01'})
        current = state['plans'][state['selectedPlanId']]
        self.assertFalse(next(a for a in current['day']['memberAvailability'] if a['memberName'] == '甲')['participating'])
        data = import_complete_configuration(self.wb.export_complete_configuration(current['id']))
        target = Workbench(Path(self.directory.name)/'target.sqlite3')
        self.command('import-complete-configuration', data, wb=target)
        target = Workbench(Path(self.directory.name)/'target.sqlite3')
        state = self.command('new-plan', {'serviceDate': '2027-02-01'}, wb=target)
        self.assertEqual(['F0', 'F1'], [f['flightNo'] for f in state['plans'][state['selectedPlanId']]['day']['flights']])
        self.assertEqual('休假', next(p for p in state['people'] if p['name'] == '甲')['status'])
        person = next(p for p in state['people'] if p['name'] == '甲')
        self.command('save-person', {**person, 'status': '正常'}, wb=target)
        state = self.command('new-plan', {'serviceDate': '2027-03-01'}, wb=target)
        attendance = state['plans'][state['selectedPlanId']]['day']['memberAvailability']
        self.assertEqual({'甲': True, '乙': False}, {a['memberName']: a['participating'] for a in attendance})

    def test_configuration_reimport_has_one_current_result_and_one_archived_date(self):
        pid = self.setup_plan()
        self.command('adopt', pid=pid)
        before = self.wb.backup()['state']['plans'][pid]['adopted']
        state = self.command('import-complete-configuration', small_configuration(None))
        current_id = state['selectedPlanId']
        self.assertEqual([pid], state['historyPlanIds'])
        state = self.command('new-plan', {'serviceDate': '2026-08-02'})
        self.assertEqual(current_id, state['selectedPlanId'])
        state = self.command('prepare-solve', {'day': state['plans'][current_id]['day']}, current_id)
        self.command('adopt', pid=current_id)
        self.assertEqual([current_id], self.wb.snapshot()['historyPlanIds'])
        self.assertEqual(before, state['plans'][pid]['adopted'])

    def test_new_date_carries_manually_added_flight_without_changing_library(self):
        pid = self.setup_plan()
        state = self.wb.snapshot()
        day = deepcopy(state['plans'][pid]['day'])
        day['flights'].append({'id': 'manual', 'flightNo': 'MANUAL', 'windowStart': '17:00', 'windowEnd': '18:00'})
        day['cells'].append({**deepcopy(day['cells'][0]), 'id': 'manual-cell', 'flightId': 'manual', 'flightNo': 'MANUAL', 'rawValue': '甲', 'fixedMemberNames': ['甲']})
        self.command('save-input', {'day': day}, pid)
        state = self.command('new-plan', {'serviceDate': '2026-09-01'})
        current = state['plans'][state['selectedPlanId']]
        self.assertEqual(['F0', 'F1', 'MANUAL'], [f['flightNo'] for f in current['day']['flights']])
        self.assertEqual([], next(c for c in current['day']['cells'] if c['flightNo'] == 'MANUAL')['fixedMemberNames'])
        self.assertNotIn('MANUAL', {f['flightNo'] for f in state['flightLibrary']})

    def test_clear_history_keeps_current_data_and_other_group_and_rejects_stale_command(self):
        pid = self.setup_plan()
        self.command('adopt', pid=pid)
        self.command('new-plan', {'serviceDate': '2026-08-04'})
        groups = GroupWorkspaces(self.path)
        other = groups.workbench('B')
        self.command('import-complete-configuration', small_configuration(None), wb=other)
        other_before = other.backup()
        before = self.wb.backup()['state']
        current_id = before['selectedPlanId']
        state = self.command('clear-history')
        self.assertEqual([current_id], list(state['plans']))
        self.assertEqual(before['plans'][current_id]['day'], state['plans'][current_id]['day'])
        self.assertEqual(before['plans'][current_id]['draft'], state['plans'][current_id]['draft'])
        self.assertFalse(state['plans'][current_id]['adopted'])
        self.assertEqual([], state['historyPlanIds'])
        self.assertEqual(other_before, other.backup())
        for key in ('people', 'configuration', 'balanceSettings', 'flightLibrary', 'dutyRoster'):
            self.assertEqual(before[key], state[key])
        saved = self.wb.backup()
        with self.assertRaises(WorkspaceError):
            self.wb.execute(before['revision'], 'clear-history', {})
        self.assertEqual(saved, Workbench(self.path).backup())

    def test_clear_archived_current_keeps_arrangements_and_empty_workspace_is_safe(self):
        state = self.command('clear-history')
        self.assertEqual({}, state['plans'])
        pid = self.setup_plan()
        state = self.command('adopt', pid=pid)
        draft = deepcopy(state['plans'][pid]['draft'])
        state = self.command('clear-history')
        self.assertEqual(draft, state['plans'][pid]['draft'])
        self.assertIsNone(state['plans'][pid]['adopted'])
        self.assertEqual([], state['historyPlanIds'])
        self.assertTrue(state['plans'][pid]['valid'])
        target = Workbench(Path(self.directory.name)/'clear-restored.sqlite3')
        restored = target.restore(0, self.wb.backup())
        self.assertEqual(draft, restored['plans'][pid]['draft'])
        self.assertEqual([], restored['historyPlanIds'])
