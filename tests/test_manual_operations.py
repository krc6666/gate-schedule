from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gate_schedule.workbench import Workbench, WorkspaceError, schedule_data
from gate_schedule.excel_io import import_complete_configuration
from gate_schedule.group_workspaces import GroupWorkspaces
from gate_schedule.workspace_document import validate_document, plan_scenario, archive_review
from tests.test_balance_workflow import small_configuration


class ManualOperationsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp', prefix='manual-operations-')
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)/'state.sqlite3'
        self.w = Workbench(self.path)
        self.s = self.w.execute(0, 'import-complete-configuration', small_configuration(None))
        self.pid = self.s['selectedPlanId']
        self.people = {p['name']: p['id'] for p in self.s['people']}

    def command(self, action, payload=None):
        self.s = self.w.execute(self.s['revision'], action, payload or {}, self.pid)
        return self.s['plans'][self.pid]

    def counts(self, p):
        return {r['memberName']: (r['count'], r['flightNos']) for r in p['schedule']['memberFlights']}

    def test_edit_reuses_unchanged_history_and_keeps_today_checks_and_undo(self):
        self.command('edit-cell', {'cellId': 'c0', 'text': '甲'})
        self.command('adopt')
        history_id = self.pid
        current = small_configuration(None)
        current['day']['serviceDate'] = '2026-08-03'
        self.command('import-complete-configuration', current)
        self.pid = self.s['selectedPlanId']
        groups = GroupWorkspaces(self.path)
        groups.initialize()
        self.w = groups.workbench('A')
        self.s = self.w.snapshot()
        history = deepcopy(self.s['plans'][history_id])
        with patch('gate_schedule.workbench.archive_review', wraps=archive_review) as review, \
                patch('gate_schedule.workbench.schedule_data', wraps=schedule_data) as view:
            self.command('edit-slot', {'taskId': 'imported-c0', 'slot': 0, 'memberId': self.people['甲']})
            self.w = groups.workbench('A')
            today = self.command('move-assignment', {
                'memberId': self.people['甲'], 'sourceTaskId': 'imported-c0', 'targetTaskId': 'imported-c1',
            })
            self.assertTrue(today['editFeedback']['hasWarnings'])
            self.assertEqual({'甲': (1, ['F1']), '乙': (0, [])}, self.counts(today))
            self.assertEqual(history['formalSchedule'], self.s['plans'][history_id]['formalSchedule'])
            self.assertFalse(any(call.args[0].service_date != current['day']['serviceDate'] for call in view.call_args_list))
            review.assert_not_called()
        self.assertEqual({'甲': (1, ['F0']), '乙': (0, [])}, self.counts(self.command('undo-edit')))
        self.assertEqual(GroupWorkspaces(self.path).workbench('A').snapshot(), self.s)

    def test_restoring_changed_history_replaces_the_previous_view_and_rejects_bad_archive(self):
        self.command('edit-cell', {'cellId': 'c0', 'text': '甲'})
        self.command('adopt')
        groups = GroupWorkspaces(self.path)
        groups.initialize()
        self.w = groups.workbench('A')
        self.s = self.w.snapshot()
        original = groups.backup()
        self.command('edit-cell', {'cellId': 'c0', 'text': '乙'})
        changed = self.command('adopt')
        self.assertEqual(self.people['乙'], changed['formalSchedule']['assignments'][0]['memberId'])
        modified_backup = deepcopy(original)
        modified_backup['groups']['A']['plans'][self.pid]['adopted']['issues'] = []
        before = groups.backup()
        with self.assertRaisesRegex(WorkspaceError, '归档问题清单'):
            groups.preview_restore('A', modified_backup)
        self.assertEqual(before, groups.backup())
        restored = groups.restore('A', self.s['revision'], original)
        self.assertEqual(self.people['甲'], restored['plans'][self.pid]['formalSchedule']['assignments'][0]['memberId'])
        self.assertEqual(before['groups']['B']['plans'], groups.backup()['groups']['B']['plans'])
        self.assertEqual(GroupWorkspaces(self.path).workbench('A').snapshot(), restored)

    def test_move_replace_clear_undo_and_restart_keep_flight_counts_current(self):
        self.command('edit-cell', {'cellId':'c0', 'text':'甲'})
        self.command('edit-cell', {'cellId':'c1', 'text':'乙'})
        formal = deepcopy(self.command('adopt')['adopted'])
        p = self.command('move-assignment', {'memberId':self.people['甲'], 'sourceTaskId':'imported-c0', 'targetTaskId':'imported-c1', 'targetMemberId':self.people['乙']})
        self.assertEqual({'甲':(1,['F1']), '乙':(1,['F0'])}, self.counts(p))
        p = self.command('edit-slot', {'taskId':'imported-c0', 'slot':0, 'memberId':self.people['甲']})
        self.assertEqual({'甲':(2,['F0','F1']), '乙':(0,[])}, self.counts(p))
        p = self.command('clear-position', {'cellIds':['c0']})
        self.assertEqual({'甲':(1,['F1']), '乙':(0,[])}, self.counts(p))
        self.assertIn('coverage_shortfall', {i['code'] for i in p['issues']})
        self.assertTrue(p['editFeedback']['hasWarnings'])
        self.assertEqual(formal, p['adopted'])
        self.w = Workbench(self.path)
        p = self.command('undo-edit')
        self.assertEqual({'甲':(2,['F0','F1']), '乙':(0,[])}, self.counts(p))
        self.assertEqual(formal, p['adopted'])
        self.assertEqual(2, sum(r['count'] for r in self.w.role_statistics('2026-08')['totals']))

    def test_clear_position_removes_all_partners_and_pending_text_atomically(self):
        day = deepcopy(self.s['plans'][self.pid]['day'])
        day['roleConfigs'][0]['requiredCount'] = 2
        self.command('save-input', {'day':day})
        self.command('edit-cell', {'cellId':'c0', 'text':'甲/乙'})
        self.command('edit-cell', {'cellId':'c1', 'text':'乙'})
        before = self.w.backup()
        with self.assertRaises(WorkspaceError):
            self.command('clear-position', {'cellIds':['c0', 'missing']})
        self.assertEqual(before, self.w.backup())
        p = self.command('clear-position', {'cellIds':['c0']})
        self.assertEqual([{'taskId':'imported-c1','memberId':self.people['乙']}], p['draft'])
        day = deepcopy(p['day'])
        day['cells'][0]['requiredCountOverride'] = 0
        self.command('save-input', {'day':day})
        self.command('edit-cell', {'cellId':'c0', 'text':'甲'})
        self.assertIn('c0', self.s['plans'][self.pid]['cellDrafts'])
        p = self.command('clear-position', {'cellIds':['c0']})
        self.assertNotIn('c0', p.get('cellDrafts',{}))

    def test_cleared_draft_excel_restart_keeps_zero_statistics_and_formal_history(self):
        self.command('edit-cell', {'cellId':'c0', 'text':'甲'})
        self.command('edit-cell', {'cellId':'c1', 'text':'乙'})
        formal = deepcopy(self.command('adopt')['adopted'])
        self.command('clear-position', {'cellIds':['c0','c1']})
        self.assertEqual(0, sum(r['count'] for r in self.w.role_statistics('2026-08')['totals']))
        payload = import_complete_configuration(self.w.export_complete_configuration(self.pid))
        target_path = Path(self.directory.name)/'imported.sqlite3'
        target = Workbench(target_path)
        state = target.execute(0, 'import-complete-configuration', payload)
        imported_id = state['selectedPlanId']
        target = Workbench(target_path)
        self.assertEqual([], target.snapshot()['plans'][imported_id]['draft'])
        self.assertEqual(formal, target.backup()['state']['plans'][self.pid]['adopted'])
        self.assertEqual(0, sum(r['count'] for r in target.role_statistics('2026-08')['totals']))
        # Exporting the imported empty result again must keep its explicit state.
        again = Workbench(Path(self.directory.name)/'again.sqlite3')
        again.execute(0, 'import-complete-configuration', import_complete_configuration(target.export_complete_configuration(imported_id)))
        self.assertEqual(0, sum(r['count'] for r in again.role_statistics('2026-08')['totals']))
        # A new, unedited empty input is not a deliberate clearing of a formal.
        blank = small_configuration(None)
        target.execute(target.snapshot()['revision'], 'import-complete-configuration', blank)
        self.assertEqual(2, sum(r['count'] for r in target.role_statistics('2026-08')['totals']))

    def test_exporting_other_cleared_plan_uses_its_state_but_new_date_stays_unedited(self):
        self.command('edit-cell', {'cellId':'c0', 'text':'甲'})
        self.command('edit-cell', {'cellId':'c1', 'text':'乙'})
        self.command('adopt')
        self.command('clear-position', {'cellIds':['c0','c1']})
        cleared = self.pid
        other = small_configuration(None)
        other['day']['serviceDate'] = '2026-08-03'
        self.command('import-complete-configuration', other)
        payload = import_complete_configuration(self.w.export_complete_configuration(cleared))
        target = Workbench(Path(self.directory.name)/'other-selected.sqlite3')
        state = target.execute(0, 'import-complete-configuration', payload)
        self.assertEqual(0, sum(r['count'] for r in target.role_statistics('2026-08')['totals']))
        self.assertTrue(state['plans'][state['selectedPlanId']].get('lastEdit'))
        payload['day']['serviceDate'] = '2026-08-04'
        state = target.execute(state['revision'], 'import-complete-configuration', payload)
        self.assertFalse(state['plans'][state['selectedPlanId']].get('lastEdit'))
        self.assertEqual(2, sum(r['count'] for r in target.role_statistics('2026-08')['totals']))

    def test_undo_to_explicit_empty_draft_preserves_zero_counts_and_rejects_bad_marker(self):
        self.command('edit-cell', {'cellId':'c0', 'text':'甲'})
        self.command('edit-cell', {'cellId':'c1', 'text':'乙'})
        formal = deepcopy(self.command('adopt')['adopted'])
        self.command('clear-position', {'cellIds':['c0','c1']})
        self.command('edit-cell', {'cellId':'c0', 'text':'甲'})
        self.w = Workbench(self.path)
        p = self.command('undo-edit')
        self.assertEqual([], p['draft'])
        self.assertEqual(formal, p['adopted'])
        self.assertEqual(0, sum(r['count'] for r in self.w.role_statistics('2026-08')['totals']))
        self.command('edit-cell', {'cellId':'c0', 'text':'甲'})
        broken = self.w.backup()['state']
        broken['plans'][self.pid]['undoEdit']['before']['lastEdit'] = {'cellIds': 'invalid'}
        with self.assertRaises(WorkspaceError):
            validate_document(broken)

    def test_history_undo_validation_visits_each_plan_at_most_twice(self):
        self.command('edit-cell', {'cellId':'c0','text':'甲'})
        state = self.w.backup()['state']
        plan = state['plans'][self.pid]
        state['plans'] = {f'p{i}': {**deepcopy(plan), 'id':f'p{i}'} for i in range(8)}
        state['selectedPlanId'] = 'p0'
        with patch('gate_schedule.workspace_document.plan_scenario', wraps=plan_scenario) as build:
            validate_document(state)
        self.assertLessEqual(build.call_count, 16, '每个当前计划及其撤销各检查一次，禁止每条撤销重扫全组历史')
        broken = deepcopy(state)
        broken['plans']['p7']['undoEdit']['before']['draft'] = [{'taskId':'missing','memberId':'missing'}]
        with self.assertRaises(WorkspaceError):
            validate_document(broken)

    def test_shared_baggage_clear_keeps_other_roles_and_restores_all_rotations(self):
        config = small_configuration(None)
        day = config['day']
        day['flights'][0].update(windowStart='10:00',windowEnd='12:00')
        day['flights'][1].update(windowStart='10:30',windowEnd='12:30')
        day['roleNames'].append('大件行李拦截')
        day['roleConfigs'].append({'roleName':'大件行李拦截','requiredCount':1,'workMode':'continuous','concurrent':False})
        for person in day['memberConfigs']:
            person['eligibleRoleNames'].append('大件行李拦截')
        day['cells'].extend([{**deepcopy(c),'id':'bag'+str(i),'roleName':'大件行李拦截'} for i,c in enumerate(day['cells'])])
        self.s = self.w.execute(self.s['revision'],'import-complete-configuration',config)
        self.pid = self.s['selectedPlanId']
        p = self.s['plans'][self.pid]
        values = [{'taskId':t['id'],'memberId':self.people['甲']} for t in p['schedule']['tasks']]
        self.command('assign',{'assignments':values})
        before = deepcopy(self.s['plans'][self.pid]['draft'])
        baggage = {t['id'] for t in p['schedule']['tasks'] if t['roleId']=='大件行李拦截'}
        self.assertGreater(len(baggage),1)
        p = self.command('clear-position',{'cellIds':['bag0','bag1']})
        self.assertEqual([a for a in before if a['taskId'] not in baggage],p['draft'])
        self.assertEqual(before,self.command('undo-edit')['draft'])
