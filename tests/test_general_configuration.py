from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from io import BytesIO
import tempfile
import unittest
from openpyxl import load_workbook

from gate_schedule.domain import Assignment
from gate_schedule.excel_io import import_complete_configuration
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule
from gate_schedule.workbench import Workbench
from tests.test_complete_excel import example
from tests.test_handoff_preferences import scenario


class GeneralConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp', prefix='general-')
        self.addCleanup(self.directory.cleanup)
        self.path=Path(self.directory.name)/'state.sqlite3'
        self.wb=Workbench(self.path)
        state=self.wb.execute(0,'import-complete-configuration',example())
        self.pid=state['selectedPlanId']

    def command(self,action,payload):
        return self.wb.execute(self.wb.snapshot()['revision'],action,payload)

    def test_legacy_paths_merge_to_one_shared_unordered_setting_without_changing_plans(self):
        old=self.wb.backup()
        old['state']['handoffPreferences']=[
            {'flightNo':'TW616','arrivalRole':'接机1','boardingRoles':['登机核验'],'enabled':True,'ordered':True},
            {'flightNo':'CX937','arrivalRole':'接机1','boardingRoles':['登机主验','登机核验'],'enabled':True},
        ]
        self.wb.restore(old['state']['revision'],old)
        state=Workbench(self.path).snapshot()
        self.assertEqual([{'flightNo':'*','arrivalRole':'接机1','boardingRoles':['登机核验','登机主验'],'enabled':True,'ordered':False}],state['handoffPreferences'])
        self.assertEqual(old['state']['plans'],self.wb.backup()['state']['plans'])

    def test_global_preference_applies_to_new_flight_and_skips_missing_target_legally(self):
        prefs=[{'flightNo':'*','arrivalRole':'接机2','boardingRoles':['不存在的本航班岗','登机核验','登机主验'],'enabled':True,'ordered':True}]
        base=scenario()
        for number in ['TW616','NEW123']:
            sc=replace(base,flights=(replace(base.flights[0],flight_no=number),))
            result=solve(sc,handoff_preferences=prefs)
            self.assertFalse(validate_schedule(sc,result.assignments))
            self.assertIn(Assignment('3','乙'),result.assignments)

    def test_independent_flight_roles_roundtrip_restart_and_actual_new_plan(self):
        before=deepcopy(self.wb.backup()['state']['plans'])
        snapshot=self.wb.snapshot()
        first=deepcopy(snapshot['flightLibrary'][0]);second=deepcopy(snapshot['flightLibrary'][1])
        first['roleNames']=['接机1']
        self.command('save-flight-template',first)
        state=self.wb.snapshot()
        self.assertEqual(second,next(f for f in state['flightLibrary'] if f['flightNo']==second['flightNo']))
        self.assertEqual(before,self.wb.backup()['state']['plans'])
        self.command('add-template-role',{'flightNo':first['flightNo'],'roleName':'自定义服务岗'})
        self.command('save-role',{'roleName':'自定义服务岗','requiredCount':1,'workMode':'boarding','concurrent':False,'qualifiedMemberIds':[p['id'] for p in self.wb.snapshot()['people']]})
        self.command('save-role',{'roleName':'接机1','requiredCount':2,'workMode':'arrival','concurrent':False})
        prefs=[{'flightNo':'*','arrivalRole':'接机1','boardingRoles':['自定义服务岗'],'enabled':True,'ordered':True}]
        self.command('save-handoff-preferences',{'preferences':prefs})
        data=import_complete_configuration(self.wb.export_complete_configuration(self.pid))
        book=load_workbook(BytesIO(self.wb.export_complete_configuration(self.pid)))
        self.assertEqual('适用范围',book['岗位接续偏好'].cell(1,1).value)
        self.assertEqual('通用',book['岗位接续偏好'].cell(2,1).value)
        self.assertEqual('本航班岗位',book['航班模板'].cell(1,4).value)
        book.close()
        target=Path(self.directory.name)/'device.sqlite3';other=Workbench(target)
        other.execute(0,'import-complete-configuration',data);other=Workbench(target)
        state=other.snapshot()
        self.assertEqual(prefs,state['handoffPreferences'])
        template=next(f for f in state['flightLibrary'] if f['flightNo']==first['flightNo'])
        self.assertEqual(['接机1','自定义服务岗'],template['roleNames'])
        state=other.execute(state['revision'],'new-plan',{'serviceDate':'2026-09-01','flightNos':[first['flightNo']]})
        day=state['plans'][state['selectedPlanId']]['day']
        self.assertTrue(all(c['requiredCountOverride']==0 for c in day['cells'] if c['roleName'] not in template['roleNames']))
        self.assertIsNone(next(c for c in day['cells'] if c['roleName']=='自定义服务岗')['requiredCountOverride'])
        day['memberAvailability']=[{'memberName':n,'participating':True,'availableFrom':None,'availableUntil':None} for n in day['roster']]
        pid=state['selectedPlanId']
        state=other.execute(state['revision'],'save-input',{'day':day},pid)
        state=other.execute(state['revision'],'solve',{},pid)
        plan=state['plans'][pid]
        self.assertTrue(plan['valid'],plan['issues'])
        self.assertEqual(set(template['roleNames']),{t['roleId'] for t in plan['schedule']['tasks']})
        source_people={a['memberId'] for a in plan['draft'] if next(t for t in plan['schedule']['tasks'] if t['id']==a['taskId'])['roleId']=='接机1'}
        target_people={a['memberId'] for a in plan['draft'] if next(t for t in plan['schedule']['tasks'] if t['id']==a['taskId'])['roleId']=='自定义服务岗'}
        self.assertTrue(source_people & target_people,'迁移后的通用偏好实际用于新航班任务')
        restored=Workbench(Path(self.directory.name)/'restore.sqlite3');restored.restore(0,other.backup())
        self.assertEqual(template['roleNames'],next(f for f in restored.snapshot()['flightLibrary'] if f['flightNo']==first['flightNo'])['roleNames'])

    def test_invalid_membership_does_not_partially_write(self):
        before=self.wb.backup();f=deepcopy(self.wb.snapshot()['flightLibrary'][0])
        for names in [['不存在的岗位'],['接机1','接机1'],'接机1']:
            with self.subTest(names=names),self.assertRaises(ValueError):self.command('save-flight-template',dict(f,roleNames=names))
            self.assertEqual(before,self.wb.backup())

    def test_legacy_excel_merges_paths_and_missing_membership_preserves_old_behavior(self):
        book=load_workbook(BytesIO(self.wb.export_complete_configuration(self.pid)))
        sheet=book['岗位接续偏好'];sheet.delete_rows(1,sheet.max_row)
        sheet.append(['航班号','接机岗位','优先登机岗位','启用','按顺序优先'])
        sheet.append(['TW616','接机2','登机核验','是','是'])
        sheet.append(['CX937','接机2','登机主验','是','是'])
        book['航班模板'].delete_cols(4)
        out=BytesIO();book.save(out);book.close()
        data=import_complete_configuration(out.getvalue())
        self.assertEqual([{'flightNo':'*','arrivalRole':'接机2','boardingRoles':['登机核验','登机主验'],'enabled':True,'ordered':False}],data['handoffPreferences'])
        self.command('import-complete-configuration',data)
        names=[r['roleName'] for r in self.wb.snapshot()['configuration']['roleConfigs']]
        self.assertTrue(all(f['roleNames']==names for f in self.wb.snapshot()['flightLibrary']))

    def test_empty_membership_and_explicit_disabled_preferences_roundtrip(self):
        template=deepcopy(self.wb.snapshot()['flightLibrary'][0]);template['roleNames']=[]
        self.command('save-flight-template',template)
        prefs=[{'flightNo':'*','arrivalRole':'接机1','boardingRoles':['登机核验'],'enabled':False,'ordered':True}]
        self.command('save-handoff-preferences',{'preferences':prefs})
        data=import_complete_configuration(self.wb.export_complete_configuration(self.pid))
        self.assertEqual([],next(f['roleNames'] for f in data['flightLibrary'] if f['flightNo']==template['flightNo']))
        self.assertEqual(prefs,data['handoffPreferences'])

    def test_old_excel_missing_membership_preserves_existing_manual_selection(self):
        template=deepcopy(self.wb.snapshot()['flightLibrary'][0]);template['roleNames']=['接机1']
        self.command('save-flight-template',template)
        book=load_workbook(BytesIO(self.wb.export_complete_configuration(self.pid)));book['航班模板'].delete_cols(4)
        out=BytesIO();book.save(out);book.close()
        self.command('import-complete-configuration',import_complete_configuration(out.getvalue()))
        self.assertEqual(['接机1'],next(f['roleNames'] for f in self.wb.snapshot()['flightLibrary'] if f['flightNo']==template['flightNo']))
