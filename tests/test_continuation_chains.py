"""Compatibility of retired group settings and archived snapshots."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from gate_schedule.domain import Assignment, Flight, Member, Rotation, Scenario, Task, validate_handoff_preferences
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule
from gate_schedule.excel_io import import_complete_configuration
from gate_schedule.workbench import Workbench


def chain_scene(count=4):
    roles=('大件行李拦截','接机1','秩序维护','登机查验')[:count]
    tasks=(Task('bag','A',roles[0],480,540,1,'rotation',locked_member_ids=('m',)),)+tuple(
        Task(str(i),'A',r,480,660,1,'mobile',timing_mode='phase',service_stage='arrival' if i==1 else 'boarding')
        for i,r in enumerate(roles[1:],1))
    return Scenario('2026-08-24',(Flight('A','CX937','2026-08-24',480,660),),
        (Member('m','甲',frozenset(roles),0),),tasks,
        rotations=(Rotation('bag','pool',480,540,1,('m',),0),),release_policy='task',
        work_rules={'pairs':[],'baggage':[]},
        flow_preferences=({'flightNo':'*','arrivalRole':'大件1','boardingRoles':list(roles[1:]),
            'enabled':True,'ordered':True,'mode':'chain','targetFlightScopes':dict.fromkeys(roles[1:],'same-flight')},))


class ContinuationChainTests(unittest.TestCase):
    def test_one_rule_covers_two_three_and_four_jobs_with_one_person(self):
        for n in (2,3,4):
            s=chain_scene(n)
            with self.subTest(n=n):
                result=solve(s,allow_partial=True)
                self.assertEqual('OPTIMAL',result.status,result.diagnostics)
                self.assertEqual({Assignment(t.id,'m') for t in s.tasks},set(result.assignments))
                self.assertFalse(validate_schedule(s,result.assignments))

    def test_chain_is_soft_and_fixed_other_worker_produces_an_explanation(self):
        s=chain_scene(3)
        s=replace(s,members=s.members+(replace(s.members[0],id='n',name='乙'),),
            tasks=tuple(replace(t,locked_member_ids=('n',)) if t.id=='2' else t for t in s.tasks))
        result=solve(s,allow_partial=True)
        self.assertEqual(3,len(result.assignments))
        self.assertIn(Assignment('2','n'),result.assignments)
        warnings=[i for i in validate_schedule(s,result.assignments) if i.code=='continuation_chain_unmet']
        self.assertTrue(warnings)
        self.assertTrue(all(i.severity=='warning' for i in warnings))

    def test_legacy_group_can_cover_all_roles_when_others_are_available(self):
        s=chain_scene(4)
        s=replace(s,members=s.members+(replace(s.members[0],id='n',name='乙'),))
        result=solve(s,allow_partial=True)
        self.assertEqual({Assignment(t.id,'m') for t in s.tasks},set(result.assignments))
        self.assertFalse(validate_schedule(s,result.assignments))

    def test_real_occupied_blocks_do_not_get_an_envelope_permission(self):
        s=chain_scene(3)
        s=replace(s,tasks=tuple(replace(t,task_kind='fixed',timing_mode='exact',start=500,end=550)
                               if t.id=='1' else t for t in s.tasks))
        broken=tuple(Assignment(t.id,'m') for t in s.tasks)
        self.assertIn('overlap',{i.code for i in validate_schedule(s,broken)})
        result=solve(s,allow_partial=True)
        self.assertNotIn('overlap',{i.code for i in validate_schedule(s,result.assignments)})
        self.assertLess(len(result.assignments),3)

    def test_qualification_prohibition_and_separation_cannot_be_bypassed(self):
        s=chain_scene(4);legal=tuple(Assignment(t.id,'m') for t in s.tasks)
        cases=[replace(s,members=(replace(s.members[0],eligible_role_ids=s.members[0].eligible_role_ids-{'登机查验'}),)),
            replace(s,work_rules={'pairs':[],'baggage':[],'separationGroups':[
                dict(id='g',name='登机分人',flightNo='*',roles=['秩序维护','登机查验'],enabled=True)]}),
            replace(s,work_rules={'pairs':[],'baggage':[],'forbiddenHandoffs':[
                dict(flightNo='*',sourceRole='大件1',targets=[dict(role='登机查验',flightScope='same-flight')],enabled=True)]})]
        for bad,code in zip(cases,('ineligible','role_separation','forbidden_handoff')):
            with self.subTest(code=code):
                self.assertIn(code,{i.code for i in validate_schedule(bad,legal)})
                partial=solve(bad,allow_partial=True)
                self.assertNotIn(code,{i.code for i in validate_schedule(bad,partial.assignments)})
                self.assertLess(len(partial.assignments),4)

    def test_leave_empty_policy_still_precedes_a_complete_chain(self):
        s=chain_scene(3)
        s=replace(s,tasks=s.tasks+(Task('missing','A','无法安排',480,660,1,'fixed'),),
            coverage_priorities=(('秩序维护','leave-empty'),))
        result=solve(s,allow_partial=True)
        self.assertNotIn(Assignment('2','m'),result.assignments)
        self.assertTrue(any(i.code=='continuation_chain_unmet' for i in validate_schedule(s,result.assignments)))

    def test_missing_mode_remains_priority_and_unknown_mode_is_rejected(self):
        row=chain_scene().flow_preferences[0]
        legacy={k:v for k,v in row.items() if k!='mode'}
        validate_handoff_preferences([legacy])
        with self.assertRaises(ValueError):validate_handoff_preferences([{**row,'mode':'anything'}])
        for mode in (None,[],{},True):
            with self.subTest(mode=mode),self.assertRaises(ValueError):
                validate_handoff_preferences([{**row,'mode':mode}])
        old=replace(chain_scene(),flow_preferences=(legacy,))
        result=solve(old,allow_partial=True)
        self.assertFalse(any(i.code=='continuation_chain_unmet' for i in validate_schedule(old,result.assignments)))

    def test_missing_target_is_reported_without_creating_fake_demand(self):
        s=chain_scene(3);s=replace(s,tasks=s.tasks[:-1])
        result=solve(s,allow_partial=True)
        self.assertEqual(2,len(result.assignments))
        self.assertTrue(any(i.code=='continuation_chain_unmet' for i in validate_schedule(s,result.assignments)))

    def test_same_source_independent_groups_and_priority_coexist(self):
        s=chain_scene(4);row=s.flow_preferences[0]
        rows=({**row,'id':'first'},
              {**row,'id':'second','boardingRoles':['接机1','登机查验']},
              {k:v for k,v in row.items() if k not in {'mode','id'}})
        # Only scopes for this group's own steps are accepted.
        rows=tuple({**r,'targetFlightScopes':{k:v for k,v in r['targetFlightScopes'].items() if k in r['boardingRoles']}} for r in rows)
        validate_handoff_preferences(list(rows))
        s=replace(s,flow_preferences=rows)
        result=solve(s,allow_partial=True)
        self.assertEqual(4,len(result.assignments));self.assertFalse(validate_schedule(s,result.assignments))
        with self.assertRaises(ValueError):validate_handoff_preferences([rows[0],rows[0]])
        with self.assertRaises(ValueError):validate_handoff_preferences([{**rows[2],'id':'priority-cannot-have-group-id'}])

    def test_disabled_chain_exception_does_not_disable_other_group(self):
        s=chain_scene(3);row=s.flow_preferences[0]
        rows=({**row,'id':'one'}, {**row,'id':'one','flightNo':'CX937','enabled':False},
              {**row,'id':'two'})
        s=replace(s,flow_preferences=rows)
        from gate_schedule.domain import flow_chain_instances
        self.assertEqual({2},{index for index,_,_ in flow_chain_instances(s)})
        result=solve(s)
        self.assertEqual(3,len(result.assignments));self.assertFalse(validate_schedule(s,result.assignments))

    def test_cross_flight_scope_is_per_step_and_does_not_bypass_direct_pair_checks(self):
        from gate_schedule.domain import task_pair_conflict,preferred_mobile_handoffs
        s=chain_scene(3)
        # Two ordinary phase jobs can bridge explicitly configured overlap;
        # a baggage actual block never acquires that permission.
        s=replace(s,tasks=s.tasks[1:],rotations=(),
            flights=s.flights+(Flight('B','KE0166',s.service_date,600,720),),
            flow_preferences=({'id':'cross','mode':'chain','flightNo':'*','arrivalRole':'接机1',
                'boardingRoles':['秩序维护'],'enabled':True,'targetFlightScopes':{'秩序维护':'later-overlap'}},))
        s=replace(s,tasks=(s.tasks[0],replace(s.tasks[1],flight_id='B',start=600,end=720)))
        result=solve(s)
        self.assertEqual(2,len(result.assignments));self.assertFalse(validate_schedule(s,result.assignments))
        self.assertIn(('1','2'),{(a.id,b.id) for a,b in preferred_mobile_handoffs(s)})
        no_route=replace(s,flow_preferences=())
        self.assertEqual('overlap',task_pair_conflict(no_route,*s.tasks)[0])
        blocked=replace(s,work_rules={'pairs':[],'baggage':[],'forbiddenHandoffs':[
            dict(flightNo='*',sourceRole='接机1',targets=[dict(role='秩序维护',flightScope='later-overlap')],enabled=True)]})
        self.assertIn('forbidden_handoff',{i.code for i in validate_schedule(blocked,result.assignments)})


def chain_configuration():
    from tests.test_balance_configuration import configuration
    p=configuration();day=p['day'];day['flights']=day['flights'][:1]
    roles=['接机1','秩序维护','登机查验','服务引导']
    day['roleNames']=roles;day['roleConfigs']=[dict(roleName=r,requiredCount=1,workMode='arrival' if i==0 else 'boarding',concurrent=False) for i,r in enumerate(roles)]
    day['roster']=['甲'];day['memberConfigs']=[dict(memberName='甲',eligibleRoleNames=roles)]
    day['memberAvailability']=[dict(memberName='甲',participating=True)]
    base=day['cells'][0]
    day['cells']=[dict(base,id='chain-'+r,roleName=r,internalNames=[],fixedMemberNames=[]) for r in roles]
    p['peopleIds']={'甲':'stable-a'}
    p['balanceSettings'].update(flightCountGap=None,priorityEnabled=False,priorityRoleNames=[],priorityAdjustments=[],priorityFlightRoles=[])
    p['handoffPreferences']=[dict(id='chain-a',flightNo='*',arrivalRole=roles[0],boardingRoles=roles[1:],enabled=True,ordered=True,mode='chain',targetFlightScopes=dict.fromkeys(roles[1:],'same-flight')),
        dict(id='chain-b',flightNo='*',arrivalRole=roles[0],boardingRoles=roles[1:3],enabled=True,ordered=True,mode='chain',targetFlightScopes=dict.fromkeys(roles[1:3],'same-flight')),
        dict(flightNo='*',arrivalRole=roles[0],boardingRoles=[roles[1]],enabled=True,ordered=True,targetFlightScopes={roles[1]:'same-flight'})]
    day['flowPreferences']=deepcopy(p['handoffPreferences']);p['workRules']={'pairs':[],'baggage':[]};day['workRules']=deepcopy(p['workRules'])
    return p


def baggage_chain_configuration():
    data=chain_configuration();day=data['day']
    day['flights'][0].update(windowStart='10:00',windowEnd='12:00')
    day['roleNames']=['大件行李拦截','接机1','秩序维护','登机查验']
    day['roleConfigs']=[dict(roleName=r,requiredCount=1,workMode='continuous' if i==0 else 'arrival' if i==1 else 'boarding',concurrent=False) for i,r in enumerate(day['roleNames'])]
    day['roster']=['甲','乙'];day['memberConfigs']=[dict(memberName='甲',eligibleRoleNames=day['roleNames']),dict(memberName='乙',eligibleRoleNames=['大件行李拦截'])]
    day['memberAvailability']=[dict(memberName=n,participating=True) for n in day['roster']]
    data['peopleIds']={'甲':'stable-a','乙':'stable-b'}
    base=day['cells'][0];day['cells']=[dict(base,id='bagchain-'+r,roleName=r) for r in day['roleNames']]
    data['workRules']={'pairs':[],'baggage':[],'baggageWindow':{'startBeforeMinutes':180,'endBeforeMinutes':60,'rotationMinutes':60,'remainderToleranceMinutes':15,'timeBasis':'flight-window'}}
    day['workRules']=deepcopy(data['workRules']);data['rotationEnabled']=True;data['rotationMinutes']=60
    data['handoffPreferences']=[dict(flightNo='*',arrivalRole='接机1',boardingRoles=['秩序维护'],enabled=True,ordered=True,targetFlightScopes={'秩序维护':'same-flight'})]
    day['flowPreferences']=deepcopy(data['handoffPreferences'])
    return data


class ContinuationChainMigrationTests(unittest.TestCase):
    def test_unmet_group_warning_does_not_block_legal_manual_adoption_or_restore(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp',prefix='chain-warning-') as d:
            d=Path(d);w=Workbench(d/'source.db');data=chain_configuration()
            s=w.execute(0,'import-complete-configuration',data)
            for r in s['configuration']['roleConfigs']:
                if r['roleName'] not in data['day']['roleNames']:
                    s=w.execute(s['revision'],'save-role',{**r,'requiredCount':0})
            pid=s['selectedPlanId'];s=w.execute(s['revision'],'solve',{},pid)
            s=w.execute(s['revision'],'save-person',{'name':'乙','eligibleRoleNames':data['day']['roleNames']})
            # Refreshing attendance explicitly makes the new person available.
            day=deepcopy(s['plans'][pid]['day']);day['roster'].append('乙')
            day['memberConfigs'].append(dict(memberName='乙',eligibleRoleNames=day['roleNames']))
            day['memberAvailability'].append(dict(memberName='乙',participating=True))
            s=w.execute(s['revision'],'save-input',{'day':day},pid)
            people={p['name']:p['id'] for p in s['people']}
            tasks=s['plans'][pid]['schedule']['tasks']
            values=[{'taskId':t['id'],'memberId':people['甲' if t['roleId']=='接机1' else '乙']} for t in tasks]
            s=w.execute(s['revision'],'assign',{'assignments':values},pid)
            self.assertTrue(any(i['code']=='continuation_chain_unmet' for i in s['plans'][pid]['issues']))
            self.assertTrue(all(i.get('severity')=='warning' for i in s['plans'][pid]['issues']))
            s=w.execute(s['revision'],'adopt',{},pid)
            formal=deepcopy(s['plans'][pid]['adopted'])
            other=Workbench(d/'other.db');other.restore(0,w.backup());other=Workbench(d/'other.db')
            self.assertEqual(formal,other.snapshot()['plans'][pid]['adopted'])

    def test_bad_excel_mode_or_reused_group_identity_is_rejected_atomically(self):
        from io import BytesIO
        from openpyxl import load_workbook
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp',prefix='chain-bad-excel-') as d:
            w=Workbench(Path(d)/'state.db');w.execute(0,'import-complete-configuration',chain_configuration())
            content=w.export_complete_configuration();before=w.backup()
            for case in ('mode','identity','scope-identity'):
                book=load_workbook(BytesIO(content))
                if case=='mode':book['岗位接续偏好'].cell(2,6).value='未知方式'
                elif case=='identity':book['岗位接续偏好'].cell(3,7).value=book['岗位接续偏好'].cell(2,7).value
                else:book['接续航班范围'].cell(2,6).value='unknown-group'
                buffer=BytesIO();book.save(buffer);book.close()
                with self.subTest(case=case),self.assertRaises(ValueError):import_complete_configuration(buffer.getvalue())
                self.assertEqual(before,w.backup())

    def test_old_candidate_sheet_keeps_groups_but_explicit_empty_clears(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp',prefix='chain-legacy-') as d:
            w=Workbench(Path(d)/'state.db');data=chain_configuration()
            s=w.execute(0,'import-complete-configuration',data)
            original=deepcopy(s['handoffPreferences'])
            legacy=deepcopy(data);legacy['handoffPreferences']=[r for r in legacy['handoffPreferences'] if r.get('mode')!='chain']
            preview=w.preview_complete_configuration(legacy)
            self.assertEqual(2,preview['retainedChainCount'])
            s=w.execute(s['revision'],'import-complete-configuration',legacy)
            self.assertEqual({r['id'] for r in original if r.get('mode')=='chain'},
                {r['id'] for r in s['handoffPreferences'] if r.get('mode')=='chain'})
            explicit=deepcopy(legacy);explicit['handoffPreferences']=[{**legacy['handoffPreferences'][0],'mode':'priority'}]
            s=w.execute(s['revision'],'import-complete-configuration',explicit)
            self.assertFalse(any(r.get('mode')=='chain' for r in s['handoffPreferences']))
            s=w.execute(s['revision'],'import-complete-configuration',data)
            clear=deepcopy(legacy);clear['handoffPreferences']=[]
            s=w.execute(s['revision'],'import-complete-configuration',clear)
            self.assertEqual([],s['handoffPreferences'])

    def test_excel_empty_import_restart_backup_and_actual_use(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp',prefix='chain-migration-') as d:
            d=Path(d);data=chain_configuration();w=Workbench(d/'source.db')
            s=w.execute(0,'import-complete-configuration',data)
            for r in s['configuration']['roleConfigs']:
                if r['roleName'] not in data['day']['roleNames']:
                    s=w.execute(s['revision'],'save-role',{**r,'requiredCount':0})
            for stage in ('source','excel','backup'):
                if stage=='excel':
                    decoded=import_complete_configuration(w.export_complete_configuration())
                    self.assertEqual(data['handoffPreferences'],decoded['handoffPreferences'])
                    w=Workbench(d/'excel.db');w.execute(0,'import-complete-configuration',decoded);w=Workbench(d/'excel.db')
                elif stage=='backup':
                    backup=w.backup();w=Workbench(d/'backup.db');w.restore(0,backup);w=Workbench(d/'backup.db')
                s=w.snapshot();s=w.execute(s['revision'],'solve',{},s['selectedPlanId']);p=s['plans'][s['selectedPlanId']]
                self.assertEqual(4,len(p['draft']),(stage,p['issues']))
                self.assertTrue(p['valid'],(stage,p['issues']))
                self.assertEqual(data['handoffPreferences'],s['handoffPreferences'])
