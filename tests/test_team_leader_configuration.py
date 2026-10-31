from copy import deepcopy
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from openpyxl import load_workbook

from gate_schedule.excel_io import import_complete_configuration, export_complete_configuration
from gate_schedule.workbench import Workbench
from test_balance_workflow import small_configuration


class TeamLeaderConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp', prefix='leader-')
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)/'state.sqlite3'
        self.workbench = Workbench(self.path)
        self.command('import-complete-configuration', small_configuration(1))
        for role in self.workbench.snapshot()['configuration']['roleConfigs']:
            if role['roleName'] != '接机1':
                self.command('save-role', {'roleName':role['roleName'], 'requiredCount':0})

    def command(self, action, payload=None, pid=None, workspace=None):
        workspace = workspace or self.workbench
        return workspace.execute(workspace.snapshot()['revision'], action, payload or {}, pid)

    def mark(self, value=True):
        person = next(p for p in self.workbench.snapshot()['people'] if p['name']=='甲')
        return self.command('save-person', {'id':person['id'], 'name':person['name'], 'teamLeader':value})

    def test_excel_empty_device_restart_actual_solve_and_backup(self):
        before = deepcopy(self.workbench.snapshot()['plans'])
        state = self.mark()
        self.assertEqual(before, state['plans'])
        content = self.workbench.export_complete_configuration(state['selectedPlanId'])
        book = load_workbook(BytesIO(content))
        self.assertEqual('分队长', book['人员状态'].cell(1,3).value)
        self.assertEqual('是', book['分队长设置'].cell(2,2).value)
        book.close()
        imported = import_complete_configuration(content)
        self.assertEqual({'甲':True,'乙':False}, imported['peopleTeamLeaders'])
        path = self.path.with_name('other.sqlite3'); other = Workbench(path)
        self.command('import-complete-configuration', imported, workspace=other)
        other = Workbench(path); state = other.snapshot(); pid=state['selectedPlanId']
        state = self.command('solve', {}, pid, other); plan=state['plans'][pid]
        self.assertTrue(plan['valid'], plan['issues'])
        leader = next(p['id'] for p in state['people'] if p['name']=='甲')
        self.assertNotIn(leader, {a['memberId'] for a in plan['draft']})
        state = self.command('adopt', {}, pid, other)
        formal = deepcopy(state['plans'][pid]['adopted'])
        self.assertTrue(next(m for m in formal['scenario']['members'] if m['id']==leader)['teamLeader'])
        self.assertTrue(formal['scenario']['teamLeaderSettings']['exemptFlightCount'])
        # Later edits do not retroactively change an adopted scenario.
        self.command('save-person', {'id':leader, 'name':'甲', 'teamLeader':False}, workspace=other)
        self.assertEqual(formal, other.snapshot()['plans'][pid]['adopted'])
        restored = Workbench(self.path.with_name('restore.sqlite3'))
        restored.restore(0, other.backup())
        self.assertEqual(formal, restored.snapshot()['plans'][pid]['adopted'])
        self.assertEqual(before, self.workbench.snapshot()['plans'])

    def test_legacy_preserves_flags_explicit_no_cancels_and_invalid_atomic(self):
        state = self.mark()
        person = next(p for p in state['people'] if p['name']=='甲')
        state = self.command('save-person', {'id':person['id'], 'name':'甲'})
        self.assertTrue(next(p for p in state['people'] if p['name']=='甲')['teamLeader'])
        legacy = import_complete_configuration(export_complete_configuration(small_configuration(1)))
        self.assertNotIn('peopleTeamLeaders', legacy)
        self.command('import-complete-configuration', legacy)
        self.assertTrue(next(p for p in self.workbench.snapshot()['people'] if p['name']=='甲')['teamLeader'])
        incoming = deepcopy(legacy); incoming['peopleTeamLeaders']={'甲':False,'乙':False}
        self.command('import-complete-configuration', incoming)
        self.assertFalse(next(p for p in self.workbench.snapshot()['people'] if p['name']=='甲')['teamLeader'])
        before = self.workbench.backup()
        for invalid in [{'甲':'是','乙':False}, {'甲':True}, {'甲':True,'乙':False,'未知':True}]:
            with self.assertRaises(ValueError):
                self.command('import-complete-configuration', {**incoming,'peopleTeamLeaders':invalid})
            self.assertEqual(before, self.workbench.backup())

    def test_rule_unified_save_snapshot_and_old_excel_without_new_columns(self):
        state = self.mark(); pid=state['selectedPlanId']
        payload={'configuration':state['configuration'], 'balanceSettings':state['balanceSettings'],
                 'preferences':state['handoffPreferences'], 'teamLeaderSettings':{'lastUsed':False,'exemptFlightCount':False}}
        self.command('save-rules', payload)
        state=self.command('solve', {}, pid); new_id=state['selectedPlanId']
        self.assertNotEqual(pid,new_id)
        self.assertTrue(state['plans'][new_id]['valid'])
        people={p['id']:p['name'] for p in state['people']}
        self.assertEqual({'甲','乙'}, {people[a['memberId']] for a in state['plans'][new_id]['draft']})
        book=load_workbook(BytesIO(self.workbench.export_complete_configuration(new_id)))
        book.remove(book['分队长设置']); book['人员状态'].delete_cols(3,3)
        stream=BytesIO(); book.save(stream); book.close()
        incoming=import_complete_configuration(stream.getvalue())
        self.assertNotIn('teamLeaderSettings',incoming);self.assertNotIn('peopleTeamLeaders',incoming)
        self.command('import-complete-configuration',incoming)
        self.assertEqual(payload['teamLeaderSettings'],self.workbench.snapshot()['teamLeaderSettings'])
        self.assertTrue(next(p for p in self.workbench.snapshot()['people'] if p['name']=='甲')['teamLeader'])
        before=self.workbench.backup(); payload['teamLeaderSettings']['lastUsed']='是'
        with self.assertRaises(ValueError):self.command('save-rules',payload)
        self.assertEqual(before,self.workbench.backup())
