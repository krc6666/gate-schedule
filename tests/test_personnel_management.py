from copy import deepcopy
from dataclasses import replace
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from openpyxl import load_workbook

from gate_schedule.domain import Assignment
from gate_schedule.io import scenario_from_dict
from gate_schedule.manual_editing import candidate_options
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule
from gate_schedule.workbench import Workbench
from gate_schedule.excel_io import import_complete_configuration
from test_balance_workflow import small_configuration
from test_candidate_availability import scenario, task


class PersonnelManagementTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp',prefix='personnel-')
        self.addCleanup(self.directory.cleanup)
        self.path=Path(self.directory.name)/'state.sqlite3';self.workspace=Workbench(self.path)
        self.command('import-complete-configuration',small_configuration(1))
        for role in self.workspace.snapshot()['configuration']['roleConfigs']:
            if role['roleName']!='接机1':self.command('save-role',{'roleName':role['roleName'],'requiredCount':0})

    def command(self, action, payload=None, pid=None, workspace=None):
        w=workspace or self.workspace
        return w.execute(w.snapshot()['revision'],action,payload or {},pid)

    def person(self, name='甲'):
        return next(p for p in self.workspace.snapshot()['people'] if p['name']==name)

    def test_leave_solver_candidates_and_manual_corruption_rejected(self):
        value=scenario(task('a','接机1',stage='arrival'))
        value=replace(value,members=(replace(value.members[0],status='休假'),value.members[1]),flight_count_gap=0)
        result=solve(value);self.assertEqual((Assignment('a','乙'),),result.assignments)
        self.assertEqual([],validate_schedule(value,result.assignments))
        self.assertFalse(next(p for p in candidate_options(value,(), 'a') if p['id']=='甲')['available'])
        self.assertIn('personnel_unavailable',{i.code for i in validate_schedule(value,(Assignment('a','甲'),))})
        self.assertEqual('MODEL_INVALID',solve(replace(value,members=(replace(value.members[0],status='未知'),))).status)

    def test_status_changes_apply_on_rerun_and_keep_frozen_history(self):
        state=self.workspace.snapshot();pid=state['selectedPlanId']
        state=self.command('solve',{},pid);state=self.command('adopt',{},pid)
        old=deepcopy(state['plans']);person=self.person()
        state=self.command('save-person',{**person,'status':'休假','remark':'年休假'})
        self.assertEqual('休假',self.person()['status']);self.assertEqual(old,state['plans'])
        state=self.command('solve',{},pid);newid=state['selectedPlanId'];self.assertNotEqual(pid,newid)
        self.assertTrue(state['plans'][newid]['valid'],state['plans'][newid]['issues'])
        self.assertNotIn(person['id'],{a['memberId'] for a in state['plans'][newid]['draft']})
        state=self.command('adopt',{},newid)
        self.assertEqual(old[pid],state['plans'][pid])
        self.command('save-person',{'id':person['id'],'name':'甲','status':'正常'})
        self.assertEqual('年休假',self.person()['remark'])

    def test_delete_hides_from_config_but_preserves_formals_and_history_statistics(self):
        state=self.workspace.snapshot();pid=state['selectedPlanId']
        settings=deepcopy(state['balanceSettings']);settings['priorityRoleNames']=[]
        settings['priorityFlightRoles']=[{'flightNo':'F0','roleName':'接机1'},{'flightNo':'F1','roleName':'接机1'}]
        self.command('update-balance-settings',{'balanceSettings':settings})
        state=self.command('solve',{},pid);pid=state['selectedPlanId'];state=self.command('adopt',{},pid)
        before=deepcopy(state['plans']);person=self.person()
        def actual_counts(snapshot):
            return {group['flightNo']: member['historyCount']
                    for group in snapshot['priorityStatistics']['roles'] for member in group['members']
                    if member['memberId']==person['id'] and member['historyCount']}
        counts_before=actual_counts(state)
        self.assertTrue(counts_before)
        state=self.command('delete-person',{'id':person['id']})
        self.assertNotIn(person['id'],{p['id'] for p in state['people']})
        self.assertEqual(before,state['plans'])
        self.assertEqual(counts_before,actual_counts(state))
        self.command('adjust-priority-count',{'month':'2026-08','memberId':person['id'],'flightNo':next(iter(counts_before)),'roleName':'接机1','delta':1})
        # Retired history may still be corrected; current configuration contains
        # only current people, while full backups retain historical corrections.
        exported=import_complete_configuration(self.workspace.export_complete_configuration(pid))
        self.assertNotIn('甲',exported['day']['roster'])
        self.assertFalse(any(a['memberId']==person['id'] for a in exported['balanceSettings']['priorityAdjustments']))
        self.assertTrue(any(a['memberId']==person['id'] for a in self.workspace.backup()['state']['balanceSettings']['priorityAdjustments']))
        self.assertNotIn('甲',self.workspace.qualification_configuration()['people'])
        state=self.command('solve',{},pid);newid=state['selectedPlanId']
        self.assertTrue(state['plans'][newid]['valid']);self.assertNotIn(person['id'],{a['memberId'] for a in state['plans'][newid]['draft']})
        restore=Workbench(self.path.with_name('restore.sqlite3'));restore.restore(0,self.workspace.backup())
        self.assertNotIn(person['id'],{p['id'] for p in restore.snapshot()['people']})
        self.assertEqual(before[pid],restore.snapshot()['plans'][pid])

    def test_excel_status_remark_empty_device_restart_effect_and_legacy_preserve(self):
        person=self.person();self.command('save-person',{**person,'status':'病假','remark':'复诊'})
        pid=self.workspace.snapshot()['selectedPlanId'];content=self.workspace.export_complete_configuration(pid)
        book=load_workbook(BytesIO(content));self.assertEqual('状态',book['人员状态'].cell(1,4).value)
        incoming=import_complete_configuration(content)
        self.assertEqual('病假',incoming['peopleStatus']['甲']);self.assertEqual('复诊',incoming['peopleRemarks']['甲'])
        path=self.path.with_name('other.sqlite3');other=Workbench(path)
        self.command('import-complete-configuration',incoming,workspace=other);other=Workbench(path)
        state=other.snapshot();newid=state['selectedPlanId'];state=self.command('solve',{},newid,other)
        self.assertTrue(state['plans'][newid]['valid'])
        names={m['id']:m['name'] for m in state['plans'][newid]['schedule']['members']}
        self.assertEqual({'乙'},{names[a['memberId']] for a in state['plans'][newid]['draft']})
        book['人员状态'].delete_cols(4,2);stream=BytesIO();book.save(stream);book.close()
        legacy=import_complete_configuration(stream.getvalue());self.assertNotIn('peopleStatus',legacy)
        self.command('import-complete-configuration',legacy)
        self.assertEqual('病假',self.person()['status']);self.assertEqual('复诊',self.person()['remark'])
        incoming['peopleStatus']['甲']='正常';incoming['peopleRemarks']['甲']=''
        self.command('import-complete-configuration',incoming);self.assertEqual('正常',self.person()['status']);self.assertEqual('',self.person()['remark'])

    def test_invalid_status_or_delete_does_not_write(self):
        before=self.workspace.backup();person=self.person()
        for action,payload in [('save-person',{**person,'status':'随便'}),('save-person',{**person,'remark':False}),('delete-person',{'id':'missing'})]:
            with self.assertRaises(ValueError):self.command(action,payload)
            self.assertEqual(before,self.workspace.backup())

    def test_deleted_fixed_names_do_not_reappear_in_export_or_new_same_name_identity(self):
        state=self.workspace.snapshot();pid=state['selectedPlanId'];day=deepcopy(state['plans'][pid]['day'])
        day['cells'][0].update(rawValue='甲', internalNames=['甲'], fixedMemberNames=['甲'], semantics='single')
        day['extraOccupancies']=[{'id':'occ','label':'其他工作','memberNames':['甲'],'start':'09:00','end':'09:30'}]
        self.command('save-input',{'day':day},pid)
        person=self.person();self.command('delete-person',{'id':person['id']})
        incoming=import_complete_configuration(self.workspace.export_complete_configuration(pid))
        self.assertNotIn('甲',incoming['day']['memberNames'])
        self.assertFalse(any(c.get('fixedMemberNames') for c in incoming['day']['cells']))
        self.assertEqual([],incoming['day']['extraOccupancies'])
        self.command('save-person',{'name':'甲'})
        self.assertNotEqual(person['id'],self.person()['id'])
