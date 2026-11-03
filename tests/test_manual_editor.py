from copy import deepcopy
import unittest
import test_workspace as fixtures
from gate_schedule.workbench import Workbench, WorkspaceError


class ManualEditorTests(unittest.TestCase):
    setUp = fixtures.WorkspaceTests.setUp
    tearDown = fixtures.WorkspaceTests.tearDown
    command = fixtures.WorkspaceTests.command
    complete_plan = fixtures.WorkspaceTests.complete_plan

    def filled(self):
        pid, people = self.complete_plan()
        self.command('edit-cell', {'cellId':'c1','text':'甲/乙'},pid)
        return pid,people

    def test_slot_candidates_preserve_partner_and_are_read_only(self):
        pid,people=self.filled(); before=self.workspace.backup()
        candidates=self.workspace.candidates(pid,'imported-c1',slot=0)
        by_name={c['name']:c for c in candidates}
        self.assertFalse(by_name['乙']['available'])
        self.assertIn('已在本岗','；'.join(by_name['乙']['reasons']))
        self.assertTrue(by_name['丙']['available'])
        self.assertEqual(before,self.workspace.backup())

    def test_edit_slot_preview_undo_and_formal_survive_restart(self):
        pid,people=self.filled()
        formal=self.command('adopt',{},pid)['plans'][pid]['adopted']
        before=self.workspace.backup()
        payload={'taskId':'imported-c1','slot':0,'memberId':people[2]['id']}
        preview=self.workspace.preview_edit(pid,payload)
        self.assertTrue(preview['valid'])
        self.assertEqual(before,self.workspace.backup())
        p=self.command('edit-slot',payload,pid)['plans'][pid]
        self.assertEqual({people[1]['id'],people[2]['id']},{a['memberId'] for a in p['draft']})
        self.assertTrue(p['undoAvailable'])
        self.assertFalse(p['editFeedback']['hasWarnings'])
        restored=Workbench(self.path.parent/'roundtrip.sqlite3')
        restored.restore(0,self.workspace.backup())
        s=restored.snapshot();p=restored.execute(s['revision'],'undo-edit',{},pid)['plans'][pid]
        self.assertEqual(before['state']['plans'][pid]['draft'],p['draft'])
        self.assertEqual(formal,p['adopted'])
        self.assertFalse(p['undoAvailable'])

    def test_conflict_preview_feedback_links_both_tasks_and_adoption_refuses(self):
        pid,people=self.filled()
        day=deepcopy(self.workspace.snapshot()['plans'][pid]['day'])
        day['flights'].append({'id':'f2','flightNo':'OTHER','windowStart':'23:05','windowEnd':'次日00:05'})
        day['cells'].append({**day['cells'][0],'id':'c2','flightId':'f2','flightNo':'OTHER','requiredCountOverride':1})
        self.command('save-input',{'day':day},pid)
        self.command('edit-cell',{'cellId':'c1','text':'甲/乙'},pid)
        before=self.workspace.backup()
        payload={'taskId':'imported-c2','slot':0,'memberId':people[0]['id']}
        preview=self.workspace.preview_edit(pid,payload)
        conflict=next(i for i in preview['issues'] if i['code']=='overlap')
        self.assertEqual({'imported-c1','imported-c2'},set(conflict['relatedTaskIds']))
        self.assertIn('甲',conflict['message']);self.assertIn('AK151',conflict['message']);self.assertIn('OTHER',conflict['message'])
        self.assertNotIn(people[0]['id'],conflict['message'])
        self.assertEqual(before,self.workspace.backup())
        p=self.command('edit-slot',payload,pid)['plans'][pid]
        self.assertTrue(p['editFeedback']['hasWarnings'])
        self.assertFalse(p['valid'])
        p=self.command('undo-edit',{},pid)['plans'][pid]
        self.assertEqual(before['state']['plans'][pid]['draft'],p['draft'])
        self.command('edit-slot',payload,pid)
        saved=self.command('adopt',{},pid)['plans'][pid]
        self.assertIn('overlap',{i['code'] for i in saved['adopted']['issues']})

    def test_nonparticipating_and_unqualified_candidates_explain_why(self):
        pid,people=self.filled()
        day=deepcopy(self.workspace.snapshot()['plans'][pid]['day'])
        day['memberAvailability'][2]['participating']=False
        day['memberConfigs'][1]['eligibleRoleNames']=[]
        self.command('save-input',{'day':day},pid)
        candidates={c['name']:c for c in self.workspace.candidates(pid,'imported-c1',slot=0)}
        self.assertFalse(candidates['丙']['available']);self.assertIn('参加','；'.join(candidates['丙']['reasons']))
        self.assertFalse(candidates['乙']['available']);self.assertIn('资格','；'.join(candidates['乙']['reasons']))

    def test_unknown_name_has_suggestions_and_keeps_input_uncommitted(self):
        pid,_=self.filled(); before=self.workspace.backup()
        result=self.workspace.preview_edit(pid,{'cellId':'c1','text':'甲/丙丙'})
        self.assertFalse(result['valid']);self.assertIn('丙',result['suggestions'])
        self.assertEqual(before,self.workspace.backup())

    def test_manual_warning_candidate_retains_name_restart_backup_and_formal(self):
        pid, people = self.filled()
        formal = self.command('adopt', {}, pid)['plans'][pid]['adopted']
        day = deepcopy(self.workspace.snapshot()['plans'][pid]['day'])
        day['memberConfigs'][2]['eligibleRoleNames'] = []
        day['flights'].append({'id':'f2','flightNo':'OTHER','windowStart':'23:05','windowEnd':'次日00:05'})
        day['cells'].append({**day['cells'][0], 'id':'c2','flightId':'f2','flightNo':'OTHER','requiredCountOverride':1})
        self.command('save-input', {'day':day}, pid)
        self.command('edit-cell', {'cellId':'c1','text':'甲/乙'}, pid)
        candidates = {c['name']:c for c in self.workspace.candidates(pid, 'imported-c1', 0)}
        self.assertFalse(candidates['丙']['available'])
        self.assertTrue(candidates['丙']['manualAllowed'], '资格问题只阻止自动安排，允许手工草稿')
        self.assertFalse(candidates['乙']['manualAllowed'], '不能把同一搭档重复加入同岗')
        self.command('edit-slot', {'taskId':'imported-c1','slot':0,'memberId':people[2]['id']}, pid)
        self.command('edit-cell', {'cellId':'c2','text':'丙'}, pid)
        p = Workbench(self.path).snapshot()['plans'][pid]
        self.assertEqual(2, sum(a['memberId']==people[2]['id'] for a in p['draft']))
        self.assertTrue({'ineligible','overlap'} <= {i['code'] for i in p['issues']})
        self.assertIn('丙', str(p['schedule']['matrix']))
        self.assertEqual(formal, p['adopted'])
        other = Workbench(self.path.parent/'warning-restored.sqlite3')
        other.restore(0, self.workspace.backup())
        self.assertEqual(p['draft'], other.snapshot()['plans'][pid]['draft'])
        for action in ['adopt','archive-only']:
            result=self.command(action, {}, pid)
            self.assertTrue({'ineligible','overlap'} <= {i['code'] for i in result['plans'][pid]['adopted']['issues']})

    def test_undo_temporary_cell_restores_pending_text_and_rejects_bad_or_stale_restore(self):
        pid,_=self.filled()
        day=deepcopy(self.workspace.snapshot()['plans'][pid]['day'])
        day['cells'].append({**day['cells'][0],'id':'temp','requiredCountOverride':0})
        # A separate role is required to keep the flight/role matrix unique.
        day['roleNames'].append('特服');day['roleConfigs'].append({'roleName':'特服','requiredCount':0})
        day['cells'][-1]['roleName']='特服'
        self.command('save-input',{'day':day},pid)
        self.command('edit-cell',{'cellId':'temp','text':'丙'},pid)
        before=self.workspace.backup()['state']['plans'][pid]
        self.command('configure-cell',{'cellId':'temp','requiredCount':1,'workStart':'20:00','workEnd':'21:00'},pid)
        p=self.command('undo-edit',{},pid)['plans'][pid]
        self.assertEqual(before['day'],p['day']);self.assertEqual(before['cellDrafts'],p['cellDrafts'])
        self.command('edit-cell',{'cellId':'c1','text':'甲/乙'},pid)
        broken=self.workspace.backup();broken['state']['plans'][pid]['undoEdit']['before']['draft']=[{'taskId':'no-task','memberId':'no-person'}]
        saved=self.workspace.backup()
        with self.assertRaises(WorkspaceError):self.workspace.restore(saved['state']['revision'],broken)
        self.assertEqual(saved,self.workspace.backup())
        self.command('select-plan',{},pid)
        with self.assertRaisesRegex(WorkspaceError,'已改变'):self.command('undo-edit',{},pid)
