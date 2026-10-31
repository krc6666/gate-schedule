from copy import deepcopy
from pathlib import Path
from io import BytesIO
import tempfile
import unittest
from openpyxl import load_workbook
from gate_schedule.workbench import Workbench
from gate_schedule.excel_io import import_complete_configuration
from test_balance_workflow import small_configuration


class DutyRosterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp', prefix='duty-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'state.sqlite3'
        self.w = Workbench(self.path)
        self.command('import-complete-configuration', small_configuration(1))
        self.command('save-person', {'name':'丙'})
        self.command('save-person', {'name':'丁'})
        for role in self.w.snapshot()['configuration']['roleConfigs']:
            if role['roleName'] != '接机1':
                self.command('save-role', {'roleName':role['roleName'], 'requiredCount':0})

    def command(self, action, payload, w=None):
        w = w or self.w
        return w.execute(w.snapshot()['revision'], action, payload)

    def configure(self):
        state=self.w.snapshot(); ids=[p['id'] for p in state['people']]
        settings={**state['dutyRoster'], 'anchorDate':'2026-08-02', 'dutyMemberIds':ids, 'standbyMemberIds':ids}
        self.command('save-duty-settings', settings)
        return ids

    def test_month_rotation_balance_distinct_and_next_day(self):
        ids=self.configure(); report=self.w.duty_statistics('2026-08')
        self.assertEqual(15, len(report['rows']))
        self.assertEqual('2026-08-03', report['rows'][0]['standbyDate'])
        for row in report['rows']:
            self.assertEqual(3,len(set([row['dutyMemberId'], *row['standbyMemberIds']])))
            self.assertNotIn(None,[row['dutyMemberId'],*row['standbyMemberIds']])
        duty=[r['dutyCount'] for r in report['people']];standby=[r['standbyCount'] for r in report['people']]
        self.assertLessEqual(max(duty)-min(duty),1)
        self.assertLessEqual(max(standby)-min(standby),1)
        self.assertGreaterEqual(min(standby),2)
        self.assertEqual('2026-09-01',self.w.duty_statistics('2026-09')['rows'][0]['date'])
        self.assertEqual(report,Workbench(self.path).duty_statistics('2026-08'))

    def test_manual_swap_reset_and_atomic_invalid(self):
        ids=self.configure(); row=self.w.duty_statistics('2026-08')['rows'][0]
        self.command('update-duty-roster',{'date':row['date'],'slot':'duty','memberId':row['standbyMemberIds'][0]})
        adjusted=self.w.duty_statistics('2026-08')['rows'][0]
        self.assertEqual(row['dutyMemberId'],adjusted['standbyMemberIds'][0]);self.assertTrue(adjusted['adjusted'])
        before=self.w.backup()
        with self.assertRaises(ValueError):self.command('update-duty-roster',{'date':row['date'],'slot':'duty','memberId':'missing'})
        self.assertEqual(before,self.w.backup())
        self.command('reset-duty-roster',{'date':row['date']})
        self.assertEqual(row,self.w.duty_statistics('2026-08')['rows'][0])

    def test_leave_leader_deleted_not_auto_and_manual_corruption_flagged(self):
        ids=self.configure();row=self.w.duty_statistics('2026-08')['rows'][0]
        self.command('update-duty-roster',{'date':row['date'],'slot':'duty','memberId':row['dutyMemberId']})
        person=next(p for p in self.w.snapshot()['people'] if p['id']==row['dutyMemberId'])
        self.command('save-person',{**person,'status':'休假'})
        report=self.w.duty_statistics('2026-08')
        self.assertTrue(report['rows'][0]['issues'])
        for r in report['rows'][1:]:self.assertNotIn(person['id'],[r['dutyMemberId'],*r['standbyMemberIds']])
        from gate_schedule.duty_roster import validate_roster_row
        corrupted={**row,'standbyMemberIds':[row['dutyMemberId'],None]}
        self.assertTrue(validate_roster_row(self.w._load(),corrupted))

    def test_configuration_roundtrip_legacy_and_backup(self):
        self.configure();row=self.w.duty_statistics('2026-08')['rows'][0]
        self.command('update-duty-roster',{'date':row['date'],'slot':'duty','memberId':row['standbyMemberIds'][0]})
        before=self.w.snapshot();pid=before['selectedPlanId'];content=self.w.export_complete_configuration(pid)
        data=import_complete_configuration(content);self.assertIn('dutyRoster',data)
        other=Workbench(self.path.with_name('other.sqlite3'))
        self.command('import-complete-configuration',data,other);other=Workbench(self.path.with_name('other.sqlite3'))
        self.assertEqual(before['dutyRoster'],other.snapshot()['dutyRoster'])
        self.assertEqual(self.w.duty_statistics('2026-08')['rows'],other.duty_statistics('2026-08')['rows'])
        state=other.snapshot();pid=state['selectedPlanId']
        state=other.execute(state['revision'],'solve',{},pid)
        self.assertTrue(state['plans'][pid]['valid'],state['plans'][pid]['issues'])
        other.execute(state['revision'],'adopt',{},pid)
        self.assertEqual(before['dutyRoster'],Workbench(self.path.with_name('other.sqlite3')).snapshot()['dutyRoster'])
        book=load_workbook(BytesIO(content))
        for name in ['轮值设置','轮值资质','值班备勤表']:del book[name]
        stream=BytesIO();book.save(stream);book.close()
        self.command('import-complete-configuration',import_complete_configuration(stream.getvalue()))
        self.assertEqual(before['dutyRoster'],self.w.snapshot()['dutyRoster'])
        restored=Workbench(self.path.with_name('restore.sqlite3'));restored.restore(0,self.w.backup())
        self.assertEqual(self.w.snapshot()['dutyRoster'],restored.snapshot()['dutyRoster'])

    def test_excel_roster_import_preview_atomic_and_shortage(self):
        self.configure();content=self.w.export_duty_roster('2026-08')
        preview=self.w.preview_duty_roster(content)
        self.assertEqual(15,len(preview['rows']))
        self.command('import-duty-roster',preview)
        self.assertTrue(all(r['adjusted'] for r in self.w.duty_statistics('2026-08')['rows']))
        self.command('rebalance-duty-month',{'month':'2026-08'})
        self.assertFalse(any(r['adjusted'] for r in self.w.duty_statistics('2026-08')['rows']))
        s=deepcopy(self.w.snapshot()['dutyRoster']);s['standbyMemberIds']=s['standbyMemberIds'][:1]
        self.command('save-duty-settings',s)
        self.assertTrue(any(r['issues'] for r in self.w.duty_statistics('2026-08')['rows']))

    def test_two_existing_unavailable_positions_can_be_repaired_one_at_a_time(self):
        self.configure();row=self.w.duty_statistics('2026-08')['rows'][0]
        self.command('update-duty-roster',{'date':row['date'],'slot':'duty','memberId':row['dutyMemberId']})
        for identity in [row['dutyMemberId'],row['standbyMemberIds'][0]]:
            person=next(p for p in self.w.snapshot()['people'] if p['id']==identity)
            self.command('save-person',{**person,'status':'病假'})
        self.command('update-duty-roster',{'date':row['date'],'slot':'duty','memberId':None})
        self.command('update-duty-roster',{'date':row['date'],'slot':'standby-0','memberId':None})
        repaired=self.w.duty_statistics('2026-08')['rows'][0]
        self.assertIsNone(repaired['dutyMemberId']);self.assertIsNone(repaired['standbyMemberIds'][0])
        self.assertTrue(all(i.endswith('待安排') for i in repaired['issues']))

    def test_import_maps_existing_identity_and_missing_fields_do_not_guess_qualifications(self):
        self.assertEqual([],self.w.snapshot()['dutyRoster']['dutyMemberIds'])
        self.configure();data=import_complete_configuration(self.w.export_complete_configuration(self.w.snapshot()['selectedPlanId']))
        other=Workbench(self.path.with_name('mapping.sqlite3'))
        self.command('save-person',{'name':'甲'},other)
        identity=other.snapshot()['people'][0]['id']
        self.command('import-complete-configuration',data,other)
        self.assertIn(identity,other.snapshot()['dutyRoster']['dutyMemberIds'])
        old=next(p['id'] for p in self.w.snapshot()['people'] if p['name']=='甲')
        self.assertNotIn(old,other.snapshot()['dutyRoster']['dutyMemberIds'])
