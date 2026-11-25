"""同航班接机分人不阻止明确配置的晚结束重叠航班接续。"""
from dataclasses import replace
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from gate_schedule.domain import (Assignment, Flight, Member, Scenario, Task,
    task_pair_conflict, validate_handoff_preferences, flow_overlap_permitted, preferred_mobile_handoffs)
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule


def arrival_scene():
    roles=('接机1','接机2')
    preferences=tuple(dict(flightNo='*',arrivalRole=source,boardingRoles=list(roles),
        targetFlightScopes=dict.fromkeys(roles,'later-overlap'),enabled=True,ordered=True) for source in roles)
    return Scenario('2026-08-24',
        (Flight('A','CX937','2026-08-24',610,690),Flight('B','KE0166','2026-08-24',660,735)),
        tuple(Member(m,m,frozenset(roles),0) for m in ('甲','乙')),
        tuple(Task(f'{flight}-{role}',flight,role,start,end,1,'mobile',timing_mode='phase',service_stage='arrival')
            for flight,start,end in [('A',610,690),('B',660,735)] for role in roles),
        release_policy='task',flow_preferences=preferences,
        work_rules={'pairs':[dict(flightNo='*',firstRole='接机1',secondRole='接机2',scope='same-flight',mode='separate')],
            'baggage':[],'forbiddenHandoffs':[dict(flightNo='*',sourceRole='接机1',
                targets=[dict(role='接机2',flightScope='same-flight')],enabled=True)]})


class ArrivalCrossFlightTests(unittest.TestCase):
    def test_same_role_later_overlap_rule_is_accepted_and_used(self):
        s=arrival_scene()
        validate_handoff_preferences(list(s.flow_preferences))
        result=solve(s)
        self.assertEqual('OPTIMAL',result.status)
        self.assertEqual(4,len(result.assignments))
        self.assertFalse(validate_schedule(s,result.assignments))
        self.assertIn(('A-接机1','B-接机1'),{(a.id,b.id) for a,b in preferred_mobile_handoffs(s)})
        for member in s.members:
            self.assertEqual(2,sum(a.member_id==member.id for a in result.assignments))

    def test_same_flight_separation_and_prohibition_remain_hard(self):
        s=arrival_scene()
        # A合法人员分配经后置改动破坏同航班分人，必须独立检出。
        legal=(Assignment('A-接机1','甲'),Assignment('A-接机2','乙'),
               Assignment('B-接机1','甲'),Assignment('B-接机2','乙'))
        self.assertFalse(validate_schedule(s,legal))
        broken=tuple(replace(a,member_id='甲') if a.task_id=='A-接机2' else a for a in legal)
        self.assertIn('forbidden_handoff',{i.code for i in validate_schedule(s,broken)})
        self.assertIsNone(task_pair_conflict(s,s.tasks[0],s.tasks[3]))
        group=replace(s,work_rules={'pairs':[],'baggage':[], 'separationGroups':[
            dict(id='arrival',name='接机分人',flightNo='*',roles=['接机1','接机2'],enabled=True)]})
        self.assertFalse(validate_schedule(group,legal))
        self.assertIn('role_separation',{i.code for i in validate_schedule(group,broken)})

    def test_same_role_requires_explicit_cross_flight_scope(self):
        row=arrival_scene().flow_preferences[0]
        for scope in (None,'same-flight'):
            bad={**row,'targetFlightScopes':{} if scope is None else {'接机1':scope,'接机2':'later-overlap'}}
            with self.subTest(scope=scope),self.assertRaises(ValueError):
                validate_handoff_preferences([bad])

    def test_other_cross_flight_prohibition_and_qualification_still_win(self):
        s=arrival_scene()
        blocked=replace(s,work_rules={**s.work_rules,'forbiddenHandoffs':[dict(flightNo='*',sourceRole='接机1',
            targets=[dict(role='接机1',flightScope='later-overlap')],enabled=True)]})
        self.assertEqual('forbidden_handoff',task_pair_conflict(blocked,s.tasks[0],s.tasks[2])[0])
        self.assertFalse(flow_overlap_permitted(s,s.tasks[2],s.tasks[0]))
        no_route=replace(s,flow_preferences=())
        self.assertEqual('overlap',task_pair_conflict(no_route,s.tasks[0],s.tasks[2])[0])
        unqualified=replace(s,members=(replace(s.members[0],eligible_role_ids=frozenset({'接机1'})),s.members[1]))
        broken=(Assignment('A-接机1','甲'),Assignment('B-接机2','甲'))
        self.assertIn('ineligible',{i.code for i in validate_schedule(unqualified,broken)})


def arrival_configuration():
    from tests.test_coverage_priorities import shortage_handoff_configuration
    data=shortage_handoff_configuration()
    day=data['day'];day['roleNames']=['接机1','接机2']
    day['roleConfigs']=[dict(roleName=r,requiredCount=1,workMode='arrival',concurrent=True) for r in day['roleNames']]
    day['roster']=['甲','乙']
    day['memberConfigs']=[dict(memberName=n,eligibleRoleNames=day['roleNames']) for n in day['roster']]
    day['memberAvailability']=[dict(memberName=n,participating=True) for n in day['roster']]
    data['peopleIds']={'甲':'stable-a','乙':'stable-b'}
    base=day['cells'][0]
    day['cells']=[dict(base,id=f['id']+r,flightId=f['id'],flightNo=f['flightNo'],roleName=r) for f in day['flights'] for r in day['roleNames']]
    data['balanceSettings']['coveragePriorities']=[]
    s=arrival_scene()
    data['handoffPreferences']=deepcopy(list(s.flow_preferences));day['flowPreferences']=deepcopy(data['handoffPreferences'])
    data['workRules']=deepcopy(s.work_rules);day['workRules']=deepcopy(s.work_rules)
    return data


class ArrivalCrossFlightMigrationTests(unittest.TestCase):
    def test_excel_empty_database_restart_and_backup_actual_solve(self):
        from gate_schedule.workbench import Workbench
        from gate_schedule.excel_io import import_complete_configuration
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp',prefix='arrival-cross-') as d:
            d=Path(d);w=Workbench(d/'source.db');data=arrival_configuration()
            state=w.execute(0,'import-complete-configuration',data)
            for role in state['configuration']['roleConfigs']:
                if role['roleName'] not in data['day']['roleNames']:
                    state=w.execute(state['revision'],'save-role',{**role,'requiredCount':0})
            for label in ['source','excel','backup']:
                if label=='excel':
                    decoded=import_complete_configuration(w.export_complete_configuration())
                    self.assertEqual(data['handoffPreferences'],decoded['handoffPreferences'])
                    w=Workbench(d/'excel.db');w.execute(0,'import-complete-configuration',decoded);w=Workbench(d/'excel.db')
                elif label=='backup':
                    saved=w.backup();w=Workbench(d/'backup.db');w.restore(0,saved);w=Workbench(d/'backup.db')
                state=w.snapshot();state=w.execute(state['revision'],'solve',{},state['selectedPlanId'])
                p=state['plans'][state['selectedPlanId']]
                self.assertTrue(p['valid'],(label,p['issues']))
                self.assertEqual(4,len(p['draft']),label)
                self.assertEqual(data['handoffPreferences'],state['handoffPreferences'])
