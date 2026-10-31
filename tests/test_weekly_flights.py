from copy import deepcopy
from io import BytesIO
import unittest
from openpyxl import load_workbook
import test_workspace as fixtures
from gate_schedule.workbench import Workbench
from gate_schedule.excel_io import import_complete_configuration, export_complete_configuration


class WeeklyFlightTests(unittest.TestCase):
    setUp = fixtures.WorkspaceTests.setUp
    tearDown = fixtures.WorkspaceTests.tearDown
    command = fixtures.WorkspaceTests.command
    complete_plan = fixtures.WorkspaceTests.complete_plan

    def test_preset_roundtrip_preserves_day_and_formal(self):
        pid, _ = self.complete_plan()
        before=self.workspace.backup()['state']['plans'][pid]
        flights=[{'flightNo':'NEXT','windowStart':'23:05','windowEnd':'次日00:05'}]
        self.command('save-weekly-flights',{'weekday':'0','flights':flights})
        self.assertEqual(before,self.workspace.backup()['state']['plans'][pid])
        data=import_complete_configuration(self.workspace.export_complete_configuration(pid))
        self.assertEqual({'0':flights},data['weeklyFlightPresets'])
        self.assertEqual(before,self.workspace.backup()['state']['plans'][pid])
        preview=self.workspace.preview_complete_configuration(data)
        self.assertEqual(before,self.workspace.backup()['state']['plans'][pid])
        self.assertEqual({'0':flights},preview['payload']['weeklyFlightPresets'])
        restored=Workbench(self.path.parent/'weekly.sqlite3')
        restored.restore(0,self.workspace.backup())
        self.assertEqual({'0':flights},restored.snapshot()['weeklyFlightPresets'])
        self.command('save-weekly-flights',{'weekday':'0','remove':True})
        self.assertEqual({},self.workspace.snapshot()['weeklyFlightPresets'])

    def test_bad_weekday_time_duplicate_or_hidden_personnel_is_atomic(self):
        self.complete_plan(); before=self.workspace.backup()
        valid={'flightNo':'X','windowStart':'23:05','windowEnd':'次日00:05'}
        for payload in ({'weekday':'7','flights':[]}, {'weekday':'1','flights':[valid,valid]}, {'weekday':'1','flights':[{**valid,'memberId':'甲'}]}, {'weekday':'1','flights':[{**valid,'windowEnd':'00:05'}]}):
            with self.assertRaises((ValueError,TypeError)):self.command('save-weekly-flights',payload)
            self.assertEqual(before,self.workspace.backup())
        backup=deepcopy(before); backup['state']['weeklyFlightPresets']={'1':[{**valid,'windowEnd':'00:05'}]}
        with self.assertRaises(ValueError):self.workspace.restore(before['state']['revision'],backup)
        self.assertEqual(before,self.workspace.backup())

    def test_missing_weekly_sheet_preserves_existing_and_empty_day_roundtrips(self):
        pid,_=self.complete_plan()
        self.command('save-weekly-flights',{'weekday':'2','flights':[]})
        book=load_workbook(BytesIO(self.workspace.export_complete_configuration(pid)))
        self.assertIn('每周航班',book.sheetnames)
        stream=BytesIO();book.save(stream)
        self.assertEqual({'2':[]},import_complete_configuration(stream.getvalue())['weeklyFlightPresets'])
        del book['每周航班'];stream=BytesIO();book.save(stream);book.close()
        payload=import_complete_configuration(stream.getvalue())
        self.assertNotIn('weeklyFlightPresets',payload)
        self.command('import-complete-configuration',payload)
        self.assertEqual({'2':[]},self.workspace.snapshot()['weeklyFlightPresets'])
