"""Baggage turn names select rules, while physical qualifications stay shared."""
from dataclasses import replace
from copy import deepcopy
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from openpyxl import load_workbook

from gate_schedule.day_document import imported_day_from_dict
from gate_schedule.domain import Assignment, flow_target_rank, flow_rule_roles, task_rule_roles, validate_handoff_preferences, validate_work_rules
from gate_schedule.excel_io import import_complete_configuration
from gate_schedule.imported_schedule import imported_day_to_scenario
from gate_schedule.manual_editing import candidate_options
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule
from gate_schedule.workbench import Workbench
from tests.test_baggage_window import baggage_day


def preference(source, targets, scope='*', enabled=True):
    return dict(flightNo=scope,arrivalRole=source,boardingRoles=targets,enabled=enabled,ordered=True)


def turn_day():
    day=baggage_day([dict(id='A',flightNo='CX937',windowStart='10:10',windowEnd='11:30',departureTime='12:45')])
    roles=['入境申报','登机1','登机2']
    day['roleNames']+=roles
    day['roleConfigs'] += [dict(roleName=r,requiredCount=1,workMode='boarding',concurrent=True) for r in roles]
    for m in day['memberConfigs']:m['eligibleRoleNames']=list(day['roleNames'])
    day['cells'] += [dict(id=r,flightId='A',flightNo='CX937',roleName=r,rawValue='',internalNames=[],externalLabels=[],semantics='empty',fixedMemberNames=[]) for r in roles]
    day['flowPreferences']=[preference('大件1',['入境申报']),preference('大件2',['登机1']),preference('大件3',['登机2'])]
    day['workRules']['separationGroups']=[dict(id='three',name='申报登机分人',flightNo='*',roles=roles,enabled=True)]
    return day


def turn_scene():
    return imported_day_to_scenario(imported_day_from_dict(turn_day()))


class BaggageFlowRoleTests(unittest.TestCase):
    def test_single_turn_defaults_to_one_and_more_turns_extend_choices(self):
        day=turn_day();day['flights'][0]['departureTime']='10:15'
        sc,issues=imported_day_to_scenario(imported_day_from_dict(day));self.assertEqual([],issues)
        self.assertEqual(1,len(sc.rotations));task=next(t for t in sc.tasks if t.id==sc.rotations[0].task_id)
        self.assertEqual(('大件1','大件行李拦截'),task_rule_roles(sc,task))
        self.assertEqual(('大件1','大件行李拦截'),task_rule_roles(replace(sc,rotations=()),task))
        day['flights'][0]['departureTime']='14:00'
        sc,issues=imported_day_to_scenario(imported_day_from_dict(day));self.assertEqual([],issues)
        self.assertIn('大件4',flow_rule_roles(day['roleNames'],scenarios=[sc]))
        self.assertIn('大件9',flow_rule_roles(day['roleNames'],preferences=[preference('大件9',['入境申报'])]))
        self.assertNotIn('大件1',flow_rule_roles(['入境申报']))

    def test_three_turns_match_three_routes_with_original_baggage_qualifications(self):
        sc,issues=turn_scene();self.assertEqual([],issues)
        result=solve(sc);self.assertEqual([],validate_schedule(sc,result.assignments))
        owners={a.task_id:a.member_id for a in result.assignments}
        for rotation,role in zip(sc.rotations,['入境申报','登机1','登机2']):
            task=next(t for t in sc.tasks if t.role_id==role)
            self.assertEqual(owners[rotation.task_id],owners[task.id])
        self.assertTrue(all('大件1' not in m.eligible_role_ids for m in sc.members))

    def test_aliases_are_valid_rule_choices_but_unknown_roles_still_fail(self):
        catalog=['大件行李拦截','接机1']
        validate_handoff_preferences([preference('大件1',['接机1'])],catalog)
        validate_handoff_preferences([preference('接机1',['大件2'])],catalog)
        for invalid in ['大件0','大件01','大件一','大件1x','不存在']:
            with self.subTest(role=invalid),self.assertRaises(ValueError):
                validate_handoff_preferences([preference(invalid,['接机1'])],catalog)
        with self.assertRaises(ValueError):validate_handoff_preferences([preference('大件1',['接机1'])],['接机1'])

    def test_alias_group_and_forbidden_handoff_block_manual_and_solver(self):
        sc,issues=turn_scene();self.assertEqual([],issues)
        rules={**sc.work_rules,'separationGroups':[dict(id='one',name='第一轮与申报分人',flightNo='*',roles=['大件1','入境申报'],enabled=True)],
               'forbiddenHandoffs':[dict(flightNo='*',sourceRole='大件2',targets=[dict(role='登机1',flightScope='same-flight')],enabled=True)]}
        validate_work_rules(rules,['大件行李拦截','入境申报','登机1','登机2'])
        sc=replace(sc,work_rules=rules,members=(*sc.members,*(replace(sc.members[0],id=n,name=n) for n in ('丁','戊','己'))))
        result=solve(sc);self.assertEqual([],validate_schedule(sc,result.assignments))
        for turn,role,code in [(0,'入境申报','role_separation'),(1,'登机1','forbidden_handoff')]:
            bag=sc.rotations[turn].task_id;target=next(t.id for t in sc.tasks if t.role_id==role)
            owner=next(a.member_id for a in result.assignments if a.task_id==bag)
            broken=tuple(Assignment(a.task_id,owner) if a.task_id==target else a for a in result.assignments)
            self.assertIn(code,{i.code for i in validate_schedule(sc,broken)})
            option=next(c for c in candidate_options(sc,(Assignment(bag,owner),),target) if c['id']==owner)
            self.assertFalse(option['available'])

    def test_specific_turn_replaces_generic_and_disabled_rule_does_not_fall_back(self):
        sc,_=turn_scene();bags=[next(t for t in sc.tasks if t.id==r.task_id) for r in sc.rotations]
        declaration=next(t for t in sc.tasks if t.role_id=='入境申报')
        boarding=next(t for t in sc.tasks if t.role_id=='登机1')
        sc=replace(sc,flow_preferences=(preference('大件行李拦截',['入境申报']),preference('大件1',['登机1'],'CX*')))
        self.assertEqual(0,flow_target_rank(sc,bags[0],boarding));self.assertIsNone(flow_target_rank(sc,bags[0],declaration))
        self.assertEqual(0,flow_target_rank(sc,bags[1],declaration))
        off=replace(sc,flow_preferences=(*sc.flow_preferences,preference('大件1',[],'CX937',False)))
        self.assertIsNone(flow_target_rank(off,bags[0],boarding));self.assertIsNone(flow_target_rank(off,bags[0],declaration))
        # Target selectors use the same turn identity.
        reverse=replace(sc,flow_preferences=(preference('入境申报',['大件2']),))
        self.assertEqual(0,flow_target_rank(reverse,declaration,bags[1]))
        self.assertIsNone(flow_target_rank(reverse,declaration,bags[0]))

    def test_alias_save_export_empty_import_restart_actual_use_and_atomic_invalid_excel(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()/'.tmp') as folder:
            day=turn_day();rules=deepcopy(day['workRules'])
            rules['forbiddenHandoffs']=[dict(flightNo='*',sourceRole='大件1',targets=[dict(role='登机1',flightScope='same-flight')],enabled=True)]
            rules['separationGroups'].append(dict(id='second',name='第二轮与申报分人',flightNo='*',roles=['大件2','入境申报'],enabled=True))
            wb=Workbench(Path(folder)/'source.db')
            state=wb.execute(0,'import-complete-configuration',dict(day=day,workRules=rules,handoffPreferences=day['flowPreferences'],rotationMinutes=15,rotationEnabled=False))
            for role in state['configuration']['roleConfigs']:
                if role['roleName'] not in day['roleNames']:state=wb.execute(state['revision'],'save-role',{**role,'requiredCount':0})
            pid=state['selectedPlanId'];state=wb.execute(state['revision'],'solve',{},pid)
            self.assertTrue(state['plans'][pid]['valid'],state['plans'][pid]['issues'])
            self.assertTrue({'大件1','大件2','大件3'}<=set(state['flowRuleRoles']))
            self.assertTrue(all('大件1' not in p['eligibleRoleNames'] for p in state['people']))
            content=wb.export_complete_configuration();payload=import_complete_configuration(content)
            self.assertEqual(rules,payload['workRules']);self.assertEqual(day['flowPreferences'],payload['handoffPreferences'])
            target=Workbench(Path(folder)/'empty.db');target.execute(0,'import-complete-configuration',payload)
            target=Workbench(Path(folder)/'empty.db');state=target.snapshot();self.assertEqual(rules,state['workRules'])
            state=target.execute(state['revision'],'solve',{},state['selectedPlanId'])
            plan=state['plans'][state['selectedPlanId']];self.assertTrue(plan['valid'],plan['issues'])
            self.assertEqual(3,plan['baggageCoverage']['requiredPeople'])
            for r in ['大件1','大件2','大件3']:self.assertIn(r,[t['timingLabel'].split(' · ')[0] for t in plan['schedule']['tasks']])
            before=target.backup();book=load_workbook(BytesIO(content));book['大件保障规则']['E2']=0
            out=BytesIO();book.save(out);book.close()
            with self.assertRaises(ValueError):target.execute(state['revision'],'import-complete-configuration',import_complete_configuration(out.getvalue()))
            self.assertEqual(before,target.backup())


if __name__=='__main__':unittest.main()
