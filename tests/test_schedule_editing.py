from copy import deepcopy
from pathlib import Path
import unittest

import test_workspace as fixtures
from gate_schedule.workbench import Workbench, WorkspaceError


class ScheduleEditingTests(unittest.TestCase):
    setUp = fixtures.WorkspaceTests.setUp
    tearDown = fixtures.WorkspaceTests.tearDown
    command = fixtures.WorkspaceTests.command
    complete_plan = fixtures.WorkspaceTests.complete_plan
    def test_typing_and_temporary_role_keep_other_assignments_and_formal(self):
        plan_id, people = self.complete_plan()
        self.command('solve', {}, plan_id)
        formal = self.command('adopt', {}, plan_id)['plans'][plan_id]['adopted']
        p = self.command('edit-cell', {'cellId': 'c1', 'text': '甲/乙'}, plan_id)['plans'][plan_id]
        self.assertEqual({people[0]['id'], people[1]['id']}, {a['memberId'] for a in p['draft']})
        before = self.workspace.backup()
        with self.assertRaises(WorkspaceError):
            self.command('edit-cell', {'cellId': 'c1', 'text': '不存在'}, plan_id)
        self.assertEqual(before, self.workspace.backup())
        day = deepcopy(p['day'])
        day['roleNames'].append('特服')
        day['roleConfigs'].append({'roleName': '特服', 'requiredCount': 0})
        day['cells'].append({**deepcopy(day['cells'][0]), 'id': 'temp', 'roleName': '特服', 'workStart': None, 'workEnd': None})
        self.command('save-input', {'day': day}, plan_id)
        self.command('edit-cell', {'cellId': 'c1', 'text': '甲/乙'}, plan_id)
        p = self.command('edit-cell', {'cellId': 'temp', 'text': '丙'}, plan_id)['plans'][plan_id]
        self.assertEqual('丙', p['cellDrafts']['temp'])
        self.assertFalse(p['valid'])
        self.assertEqual('丙', Workbench(self.path).snapshot()['plans'][plan_id]['cellDrafts']['temp'])
        with self.assertRaises(WorkspaceError):
            self.command('adopt', {}, plan_id)
        p = self.command('configure-cell', {'cellId': 'temp', 'requiredCount': 1, 'workStart': '20:00', 'workEnd': '21:00'}, plan_id)['plans'][plan_id]
        self.assertEqual(3, len(p['draft']), '新增岗位不能清空已排的人')
        self.assertTrue(any(i['code'] == 'ineligible' for i in p['issues']), '输入姓名不能授予资质')
        self.assertEqual(formal, p['adopted'])
        self.assertNotIn('temp', p.get('cellDrafts', {}))

    def test_drag_moves_one_partner_swaps_atomically_and_revalidates(self):
        plan_id, people = self.complete_plan()
        day = self.workspace.snapshot()['plans'][plan_id]['day']
        day['flights'].append({'id': 'f2', 'flightNo': 'CX937', 'windowStart': '20:00', 'windowEnd': '21:00'})
        day['cells'].append({**deepcopy(day['cells'][0]), 'id': 'c2', 'flightId': 'f2', 'flightNo': 'CX937', 'workStart': '20:00', 'workEnd': '21:00', 'requiredCountOverride': 1})
        self.command('save-input', {'day': day}, plan_id)
        p = self.command('edit-cell', {'cellId': 'c1', 'text': '甲/乙'}, plan_id)['plans'][plan_id]
        p = self.command('move-assignment', {'memberId': people[0]['id'], 'sourceTaskId': 'imported-c1', 'targetTaskId': 'imported-c2'}, plan_id)['plans'][plan_id]
        self.assertIn({'taskId': 'imported-c1', 'memberId': people[1]['id']}, p['draft'])
        self.assertIn({'taskId': 'imported-c2', 'memberId': people[0]['id']}, p['draft'])
        self.assertFalse(p['valid'])
        p = self.command('move-assignment', {'memberId': people[1]['id'], 'sourceTaskId': 'imported-c1', 'targetTaskId': 'imported-c2', 'targetMemberId': people[0]['id']}, plan_id)['plans'][plan_id]
        self.assertEqual(2, len(p['draft']))
        self.assertIn({'taskId': 'imported-c1', 'memberId': people[0]['id']}, p['draft'])
        before = self.workspace.backup()
        with self.assertRaises(WorkspaceError):
            self.command('move-assignment', {'memberId': people[2]['id'], 'sourceTaskId': 'imported-c1', 'targetTaskId': 'imported-c2'}, plan_id)
        self.assertEqual(before, self.workspace.backup())

    def test_archive_and_next_day_is_atomic_and_does_not_overwrite_same_date(self):
        plan_id, _ = self.complete_plan()
        self.command('solve', {}, plan_id)
        source = self.workspace.snapshot()['plans'][plan_id]
        day = deepcopy(source['day']); day['serviceDate'] = '2026-08-04'
        before = self.workspace.backup()
        bad = deepcopy(day); bad['serviceDate'] = '2026-08-02'
        with self.assertRaises(WorkspaceError):
            self.command('archive-next', {'day': bad}, plan_id)
        self.assertEqual(before, self.workspace.backup())
        state = self.command('archive-next', {'day': day}, plan_id)
        first_next = state['selectedPlanId']
        self.assertNotEqual(first_next, plan_id)
        self.assertIsNotNone(state['plans'][plan_id]['adopted'])
        self.assertEqual(source['draft'], state['plans'][plan_id]['draft'])
        self.assertEqual('2026-08-04', state['plans'][first_next]['day']['serviceDate'])
        self.assertTrue(state['plans'][first_next]['valid'])
        self.assertIsNone(state['plans'][first_next]['adopted'])
        first_plan = state['plans'][first_next]
        state = self.command('archive-next', {'day': day}, plan_id)
        self.assertEqual(first_plan, state['plans'][first_next])
        self.assertEqual(3, len(state['plans']))
        restored = Workbench(self.path.parent / 'roundtrip.sqlite3')
        restored.restore(0, self.workspace.backup())
        self.assertEqual(state['plans'], restored.snapshot()['plans'])
        self.command('edit-cell', {'cellId': 'c1', 'text': '甲'}, plan_id)
        before = self.workspace.backup()
        with self.assertRaises(WorkspaceError):
            self.command('archive-next', {'day': day}, plan_id)
        self.assertEqual(before, self.workspace.backup())

    def test_pending_input_survives_backup_and_attendance_edit_but_bad_references_are_rejected(self):
        plan_id, _ = self.complete_plan()
        day = self.workspace.snapshot()['plans'][plan_id]['day']
        day['cells'][0]['workStart'] = None; day['cells'][0]['workEnd'] = None
        self.command('save-input', {'day': day}, plan_id)
        self.command('edit-cell', {'cellId': 'c1', 'text': '甲/乙'}, plan_id)
        self.command('save-input', {'day': day}, plan_id)
        before = self.workspace.backup()
        restored = Workbench(self.path.parent / 'pending.sqlite3')
        state = restored.restore(0, before)
        self.assertEqual('甲/乙', state['plans'][plan_id]['cellDrafts']['c1'])
        self.assertFalse(state['plans'][plan_id]['valid'])
        for pending in ({'unknown-cell': '甲'}, {'c1': 'unknown-person'}, {'c1': ''}, []):
            broken = deepcopy(before); broken['state']['plans'][plan_id]['cellDrafts'] = pending
            with self.assertRaises(WorkspaceError):
                self.workspace.restore(before['state']['revision'], broken)
            self.assertEqual(before, self.workspace.backup())

    def test_prepared_reschedule_keeps_previous_plan_and_failed_generation_writes_nothing(self):
        plan_id, _ = self.complete_plan()
        self.command('solve', {}, plan_id)
        source = self.workspace.snapshot()['plans'][plan_id]
        day = deepcopy(source['day'])
        day['memberAvailability'][2]['participating'] = False
        before = self.workspace.backup()
        bad = deepcopy(day)
        for a in bad['memberAvailability']: a['participating'] = False
        with self.assertRaises(WorkspaceError):
            self.command('prepare-solve', {'day': bad}, plan_id)
        self.assertEqual(before, self.workspace.backup())
        state = self.command('prepare-solve', {'day': day}, plan_id)
        self.assertNotEqual(plan_id, state['selectedPlanId'])
        self.assertEqual(source, state['plans'][plan_id])
        self.assertTrue(state['plans'][state['selectedPlanId']]['valid'])
        with self.assertRaises(WorkspaceError):
            self.workspace.execute(before['state']['revision'], 'clear-draft', {}, plan_id)

    def test_post_edit_missing_lock_is_rejected_by_adoption(self):
        plan_id, people = self.complete_plan()
        day = self.workspace.snapshot()['plans'][plan_id]['day']
        day['cells'][0]['fixedMemberNames'] = ['甲']
        self.command('save-input', {'day': day}, plan_id)
        self.command('solve', {}, plan_id)
        formal = self.command('adopt', {}, plan_id)['plans'][plan_id]['adopted']
        p = self.command('edit-cell', {'cellId': 'c1', 'text': '乙/丙'}, plan_id)['plans'][plan_id]
        self.assertTrue(any(i['code'] == 'lock_missing' for i in p['issues']))
        with self.assertRaises(WorkspaceError): self.command('adopt', {}, plan_id)
        self.assertEqual(formal, self.workspace.snapshot()['plans'][plan_id]['adopted'])
        self.assertTrue(self.workspace.export(plan_id).startswith(b'PK'))

    def test_archive_failure_keeps_source_and_disk_failure_is_atomic(self):
        from unittest.mock import patch
        plan_id, _ = self.complete_plan()
        self.command('solve', {}, plan_id)
        day = deepcopy(self.workspace.snapshot()['plans'][plan_id]['day'])
        day['serviceDate'] = '2026-08-04'
        before = self.workspace.backup()
        with patch.object(self.workspace.store, 'compare_and_save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.command('archive-next', {'day': day}, plan_id)
        self.assertEqual(before, self.workspace.backup())
        day['memberAvailability'] = []
        with self.assertRaises(WorkspaceError): self.command('archive-next', {'day': day}, plan_id)
        self.assertEqual(before, self.workspace.backup())

    def test_multisegment_edit_requires_explicit_slots_and_does_not_guess_rotation(self):
        import json
        trial = json.loads(Path('templates/登机口完整试用配置-20260802.json').read_text(encoding='utf-8'))
        state = self.workspace.restore(0, trial)
        plan_id = state['selectedPlanId']; p = state['plans'][plan_id]
        role = '大件行李拦截'
        column, segments = next((column, segments) for column, segments in p['schedule']['matrix'][role].items() if len(segments)>1)
        cell_id = next(c['id'] for c in p['day']['cells'] if c['roleName']==role and c['flightNo']==column)
        name = p['schedule']['members'][0]['name']
        before = self.workspace.backup()
        with self.assertRaises(WorkspaceError): self.command('edit-cell', {'cellId': cell_id, 'text': name}, plan_id)
        self.assertEqual(before,self.workspace.backup())
        state = self.command('edit-cell', {'cellId': cell_id, 'taskId': segments[0]['task_id'], 'text': name}, plan_id)
        current = state['plans'][plan_id]
        unaffected = lambda values: [a for a in values if a['taskId']!=segments[0]['task_id']]
        self.assertEqual(unaffected(p['draft']),unaffected(current['draft']))



if __name__ == '__main__':
    unittest.main()
