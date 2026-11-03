from copy import deepcopy
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from openpyxl import load_workbook
from gate_schedule.excel_io import import_complete_configuration
from gate_schedule.workbench import Workbench, WorkspaceError
from tests.test_balance_workflow import small_configuration


class WarningArchiveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path.cwd()/'.tmp', prefix='warning-archive-')
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)/'state.sqlite3'
        self.w = Workbench(self.path)

    def command(self, action, payload=None, pid=None):
        s = self.w.snapshot()
        return self.w.execute(s['revision'], action, payload or {}, pid or s['selectedPlanId'])

    def prepare(self, gap=None, unqualified=False):
        data = small_configuration(gap)
        if unqualified:
            data['day']['memberConfigs'][1]['eligibleRoleNames'] = []
        return self.command('import-complete-configuration', data)['selectedPlanId']

    def test_ineligible_and_vacant_archive_export_history_restart_and_excel_migration(self):
        pid = self.prepare(unqualified=True)
        self.command('edit-cell', {'cellId':'c0','text':'乙'})
        before = deepcopy(self.w.snapshot()['plans'][pid])
        state = self.command('archive-only')
        formal = state['plans'][pid]['adopted']
        self.assertEqual(before['draft'], formal['assignments'])
        self.assertTrue({'ineligible','coverage_shortfall'} <= {i['code'] for i in formal['issues']})
        self.assertFalse(state['plans'][pid]['valid'])
        content = self.w.export(pid)
        book = load_workbook(BytesIO(content))
        self.addCleanup(book.close)
        self.assertIn('排班问题', book.sheetnames)
        self.assertIn('乙', str([[c.value for c in row] for row in book.worksheets[0]]))
        self.assertIn('待安排', str([[c.value for c in row] for row in book.worksheets[0]]))
        self.assertIn('ineligible', str([[c.value for c in row] for row in book['排班问题']]))
        self.command('edit-cell', {'cellId':'c0','text':'甲'})
        self.assertEqual(formal, Workbench(self.path).snapshot()['plans'][pid]['adopted'])
        incoming = import_complete_configuration(self.w.export_complete_configuration(pid))
        target = Workbench(Path(self.directory.name)/'target.sqlite3')
        target.execute(0, 'import-complete-configuration', incoming)
        self.assertEqual(formal, Workbench(target.store.database_path).snapshot()['plans'][pid]['adopted'])
        # Existing names on another device may have different stable identities.
        other = Workbench(Path(self.directory.name)/'existing-people.sqlite3')
        for name in ['甲','乙']:
            other.execute(other.snapshot()['revision'],'save-person',{'name':name,'active':True,'eligibleRoleNames':[]})
        other.execute(other.snapshot()['revision'],'import-complete-configuration',incoming)
        saved = other.snapshot()['plans'][pid]
        self.assertEqual({i['code'] for i in formal['issues']},{i['code'] for i in saved['formalIssues']})
        self.assertEqual('乙', next(m['name'] for m in saved['formalSchedule']['members'] if m['id']==saved['adopted']['assignments'][0]['memberId']))

    def test_difference_warning_can_be_archived_repeatedly_and_next_infeasible_still_advances(self):
        pid = self.prepare(gap=0)
        for c in ['c0','c1']: self.command('edit-cell', {'cellId':c,'text':'甲'})
        source = deepcopy(self.w.snapshot()['plans'][pid])
        day = deepcopy(source['day']);day['serviceDate']='2026-08-04'
        for a in day['memberAvailability']: a['participating']=False
        state = self.command('archive-next', {'day':day})
        self.assertEqual(source['draft'],state['plans'][pid]['adopted']['assignments'])
        self.assertIn('flight_count_spread', {i['code'] for i in state['plans'][pid]['adopted']['issues']})
        nid = state['selectedPlanId']
        self.assertNotEqual(pid,nid)
        self.assertEqual([],state['plans'][nid]['draft'])
        day = deepcopy(state['plans'][nid]['day']);day['serviceDate']='2026-08-06'
        state = self.command('archive-next', {'day':day})
        self.assertIsNotNone(state['plans'][nid]['adopted'])
        self.assertEqual('2026-08-06',state['plans'][state['selectedPlanId']]['day']['serviceDate'])

    def test_pending_cell_text_without_tasks_preserved_in_archive_and_export(self):
        pid = self.prepare()
        day = deepcopy(self.w.snapshot()['plans'][pid]['day'])
        day['roleConfigs'][0]['requiredCount']=None
        self.command('save-input', {'day':day})
        self.command('edit-cell', {'cellId':'c0','text':'甲'})
        state = self.command('adopt')
        formal = state['plans'][pid]['adopted']
        self.assertEqual('甲',formal['cellDrafts']['c0'])
        self.assertIn('pending_cell',{i['code'] for i in formal['issues']})
        self.assertEqual('甲',state['plans'][pid]['formalCellDrafts']['c0'])
        book = load_workbook(BytesIO(self.w.export(pid)))
        self.addCleanup(book.close)
        self.assertIn('甲',str([[c.value for c in row] for row in book.worksheets[0]]))

    def test_recorded_warning_cannot_hide_corrupt_references_or_tampered_issue_list(self):
        pid = self.prepare(unqualified=True)
        self.command('edit-cell',{'cellId':'c0','text':'乙'})
        self.command('adopt')
        original = self.w.backup()
        for mode in ['issues','member','scenario','marker','duplicate-cell']:
            bad = deepcopy(original)
            formal=bad['state']['plans'][pid]['adopted']
            if mode=='issues': formal['issues']=[]
            elif mode=='member': formal['assignments'][0]['memberId']='missing'
            elif mode=='scenario': formal['scenario']['tasks'][0]['requiredCount']=20
            elif mode=='duplicate-cell': formal['day']['cells'].append(deepcopy(formal['day']['cells'][0]))
            else: formal.pop('archiveVersion')
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.w.restore(self.w.snapshot()['revision'],bad)
            self.assertEqual(original,self.w.backup())

    def test_unconvertible_input_can_archive_and_migrate_without_inventing_tasks(self):
        pid = self.prepare()
        day = deepcopy(self.w.snapshot()['plans'][pid]['day'])
        day['flights'][0]['windowStart']='待确认时间'
        self.command('save-input',{'day':day})
        self.command('edit-cell',{'cellId':'c0','text':'甲'})
        state=self.command('archive-only')
        self.assertIsNone(state['plans'][pid]['adopted']['scenario'])
        self.assertEqual('甲',state['plans'][pid]['formalCellDrafts']['c0'])
        self.assertTrue(self.w.export(pid).startswith(b'PK'))
        target=Workbench(Path(self.directory.name)/'bad-input-target.sqlite3')
        target.restore(0,self.w.backup())
        self.assertEqual([], target.snapshot()['plans'][pid]['draft'])
        self.assertEqual(state['plans'][pid]['adopted'], target.snapshot()['plans'][pid]['adopted'])
        self.assertTrue(target.role_statistics('2026-08'))
        formal = deepcopy(state['plans'][pid]['adopted'])
        day['flights'][0]['windowStart'] = '10:00'
        self.command('save-input', {'day':day})
        incoming = import_complete_configuration(self.w.export_complete_configuration(pid))
        migrated = Workbench(Path(self.directory.name)/'unconvertible-history.sqlite3')
        migrated.execute(0, 'import-complete-configuration', incoming)
        self.assertEqual(formal, Workbench(migrated.store.database_path).snapshot()['plans'][pid]['adopted'])

    def test_archive_next_cancel_and_system_failure_keep_the_original_document(self):
        from unittest.mock import patch
        from gate_schedule.schedule_runs import RunControl, RunCancelled
        pid = self.prepare(unqualified=True)
        self.command('edit-cell', {'cellId':'c0','text':'乙'})
        original = self.w.backup()
        day = deepcopy(original['state']['plans'][pid]['day'])
        day['serviceDate'] = '2026-08-04'
        control = RunControl()

        def cancel(*args):
            control.stopped.set()
            raise RunCancelled('stopped')

        with patch.object(self.w, '_solve', side_effect=cancel), self.assertRaises(RunCancelled):
            self.w.execute(original['state']['revision'], 'archive-next', {'day':day}, pid, control=control)
        self.assertEqual(original, self.w.backup())
        with patch.object(self.w, '_solve', side_effect=RuntimeError('system failure')), self.assertRaises(RuntimeError):
            self.command('archive-next', {'day':day})
        self.assertEqual(original, self.w.backup())


if __name__ == '__main__': unittest.main()
