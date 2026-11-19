"""Configurable same-flight separation, independent of continuation ranking."""
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from openpyxl import load_workbook

from gate_schedule.domain import Assignment, Flight, Member, Scenario, Task, validate_work_rules
from gate_schedule.manual_editing import candidate_options
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule
from gate_schedule.workbench import Workbench
from gate_schedule.workspace_document import plan_scenario
from gate_schedule.excel_io import import_complete_configuration
from tests.test_flow_scheduling import preference
from tests.test_relative_scheduling import relative_day


def group(key='arrival', roles=None, scope='*', enabled=True, name='接机组'):
    return dict(id=key,name=name,flightNo=scope,roles=roles if roles is not None else ['接机1','接机2'],enabled=enabled)


def scene(groups=None):
    roles=['接机1','接机2','登机1','登机2']
    return Scenario('2057-08-02',(Flight('A','CX937','2057-08-02',480,660),),
        tuple(Member(n,n,frozenset(roles),0) for n in ('甲','乙')),
        tuple(Task(r,'A',r,480,660,1,'mobile',timing_mode='phase',concurrent=True) for r in roles),
        release_policy='task',flow_preferences=tuple(preference(roles[i+1:],source=r) for i,r in enumerate(roles[:-1])),
        work_rules=dict(pairs=[],baggage=[],separationGroups=groups if groups is not None else [group(),group('boarding',['登机1','登机2'],name='登机组')]))


class SeparationGroupTests(unittest.TestCase):
    def test_declaration_same_flight_forbidden_but_explicit_later_overlap_allowed(self):
        roles=['入境申报','登机1','登机2']
        sc=Scenario('2057-08-02',(Flight('A','A', '2057-08-02',480,660),Flight('B','B','2057-08-02',540,720)),
            (Member('甲','甲',frozenset(roles),0),Member('乙','乙',frozenset(roles),0)),
            (Task('declare','A','入境申报',480,660,1,'mobile',timing_mode='phase'),
             Task('same','A','登机1',480,660,1,'mobile',timing_mode='phase'),
             Task('later','B','登机1',540,720,1,'mobile',timing_mode='phase')),
            release_policy='task',flow_preferences=(dict(**preference(['登机1','登机2'],source='入境申报'),
                targetFlightScopes={'登机1':'later-overlap','登机2':'later-overlap'}),),
            work_rules=dict(pairs=[],baggage=[],separationGroups=[group(roles=roles)]))
        legal=(Assignment('declare','甲'),Assignment('later','甲'),Assignment('same','乙'))
        self.assertEqual([],validate_schedule(sc,legal))
        self.assertIn('role_separation',{i.code for i in validate_schedule(sc,(*legal[:2],Assignment('same','甲')))})
        option=next(c for c in candidate_options(sc,legal[:1],'later') if c['id']=='甲')
        self.assertTrue(option['available']);self.assertEqual('continuation',option['status'])
        from gate_schedule.domain import preferred_mobile_handoffs
        self.assertIn(('declare','later'),[(a.id,b.id) for a,b in preferred_mobile_handoffs(sc)])
        small=replace(sc,members=sc.members[:1],tasks=(sc.tasks[0],sc.tasks[2]))
        result=solve(small)
        self.assertEqual([],validate_schedule(small,result.assignments))
        legacy=replace(sc,flow_preferences=(preference(['登机1'],source='入境申报'),))
        self.assertIn('overlap',{i.code for i in validate_schedule(legacy,legal)})
        wrong_direction=replace(sc,flights=(replace(sc.flights[0],window_end=780),sc.flights[1]))
        self.assertIn('overlap',{i.code for i in validate_schedule(wrong_direction,legal)})
        equal_end=replace(sc,flights=(replace(sc.flights[0],window_end=720),sc.flights[1]))
        self.assertIn('overlap',{i.code for i in validate_schedule(equal_end,legal)})
        special=replace(sc,flow_preferences=(*sc.flow_preferences,dict(**preference(['登机1'],scope='A',source='入境申报',enabled=False),targetFlightScopes={'登机1':'later-overlap'})))
        self.assertIn('overlap',{i.code for i in validate_schedule(special,legal)})
        for changed in (
            replace(sc,minimum_transition_minutes=5),
            replace(sc,tasks=tuple(replace(t,timing_mode='exact') for t in sc.tasks)),
            replace(sc,flow_preferences=(dict(**preference(['登机1'],source='入境申报'),targetFlightScopes={'登机1':'same-flight'}),)),
        ):
            with self.subTest(changed=changed):
                self.assertTrue(validate_schedule(changed,legal))
        dedicated=replace(sc,dedicated_flight_protection=True,tasks=(replace(sc.tasks[0],exclusive_same_flight=True),*sc.tasks[1:]))
        self.assertEqual([],validate_schedule(dedicated,legal),'专岗不禁止明确的跨航班接续')

    def test_shortage_leaves_group_gap_instead_of_reusing_person(self):
        sc=replace(scene(),members=scene().members[:1])
        result=solve(sc,allow_partial=True)
        self.assertEqual({'coverage_shortfall'},{i.code for i in validate_schedule(sc,result.assignments)})
        self.assertEqual(2,len(result.assignments))
        self.assertEqual(2,len({a.task_id for a in result.assignments}))

    def test_group_hard_rule_and_cross_group_continuation_manual_reverse(self):
        sc=scene();result=solve(sc)
        self.assertIn(result.status,{'FEASIBLE','OPTIMAL'})
        self.assertEqual([],validate_schedule(sc,result.assignments))
        people={a.task_id:a.member_id for a in result.assignments}
        self.assertNotEqual(people['接机1'],people['接机2'])
        self.assertNotEqual(people['登机1'],people['登机2'])
        self.assertEqual(2,len(set(people.values())))
        legal=(Assignment('接机1','甲'),Assignment('登机1','甲'),Assignment('接机2','乙'),Assignment('登机2','乙'))
        self.assertEqual([],validate_schedule(sc,legal))
        broken=tuple(a for a in legal if a.task_id!='接机2')+(Assignment('接机2','甲'),)
        self.assertIn('role_separation',{i.code for i in validate_schedule(sc,broken)})
        option=next(c for c in candidate_options(sc,(Assignment('接机1','甲'),),'接机2') if c['id']=='甲')
        self.assertFalse(option['available'])
        self.assertIn('接机组',' '.join(option['reasons']))

    def test_specific_longest_series_disabled_and_delete_inheritance(self):
        rows=[group(),group(roles=['接机1','登机1'],scope='CX*'),group(roles=[],scope='CX9*',enabled=False),group(scope='CX937')]
        values=(Assignment('接机1','甲'),Assignment('接机2','甲'))
        self.assertIn('role_separation',{i.code for i in validate_schedule(scene(rows),values)})
        self.assertNotIn('role_separation',{i.code for i in validate_schedule(scene(rows[:-1]),values)})
        changed=scene(rows[:2])
        self.assertNotIn('role_separation',{i.code for i in validate_schedule(changed,values)})
        self.assertIn('role_separation',{i.code for i in validate_schedule(changed,(Assignment('接机1','甲'),Assignment('登机1','甲')))})
        self.assertIn('role_separation',{i.code for i in validate_schedule(scene(rows[:1]),values)})

    def test_same_flight_only_and_shared_coverage(self):
        sc=scene()
        later=replace(sc,flights=(*sc.flights,Flight('B','CX938',sc.service_date,720,780)),
            tasks=(sc.tasks[0],replace(sc.tasks[1],flight_id='B',start=720,end=780)))
        self.assertEqual([],validate_schedule(later,(Assignment('接机1','甲'),Assignment('接机2','甲'))))
        shared=replace(sc,tasks=(replace(sc.tasks[0],covered_flight_ids=('A','B')),replace(sc.tasks[1],flight_id='B')),
            flights=(*sc.flights,Flight('B','CX938',sc.service_date,480,660)))
        self.assertIn('role_separation',{i.code for i in validate_schedule(shared,(Assignment('接机1','甲'),Assignment('接机2','甲')))})

    def test_invalid_group_input_and_legacy_contract(self):
        self.assertEqual(dict(pairs=[],baggage=[]),validate_work_rules(dict(pairs=[],baggage=[])))
        self.assertEqual([],validate_work_rules(dict(pairs=[],baggage=[],separationGroups=[]))['separationGroups'])
        bad=[group(roles=['接机1']),group(roles=['接机1','接机1']),group(scope='CX**'),group(roles=['未知','接机1'])]
        for row in bad:
            with self.subTest(row=row), self.assertRaises(ValueError):
                validate_work_rules(dict(pairs=[],baggage=[],separationGroups=[row]),['接机1','接机2'])
        with self.assertRaises(ValueError):validate_work_rules(dict(pairs=[],baggage=[],separationGroups=[group(),group()]))
        from gate_schedule.domain import validate_handoff_preferences
        for scopes in ({'未知':'later-overlap'},{'登机1':'invalid'},[],{'登机1':{}}):
            with self.subTest(scopes=scopes),self.assertRaises(ValueError):
                validate_handoff_preferences([dict(**preference(['登机1']),targetFlightScopes=scopes)])

    def test_legacy_pairs_conversion_preserves_enabled_and_disabled_exceptions(self):
        from gate_schedule.domain import grouped_work_rules
        old=dict(pairs=[dict(flightNo=f,firstRole='接机1',secondRole='接机2',scope='same-flight',mode=m)
            for f,m in [('*','separate'),('CX*','sequence'),('CX937','separate')]],baggage=[])
        converted=grouped_work_rules(old)
        self.assertEqual(3,len(converted['separationGroups']))
        self.assertEqual(3,len(converted['pairs']))
        self.assertTrue(all(r['mode']=='sequence' for r in converted['pairs']))
        self.assertEqual(1,len({r['id'] for r in converted['separationGroups']}))
        for flight in ('OTHER','CX931','CX937'):
            a=replace(scene(),work_rules=old,flights=(replace(scene().flights[0],flight_no=flight),))
            b=replace(a,work_rules=converted)
            values=(Assignment('接机1','甲'),Assignment('接机2','甲'))
            self.assertEqual({i.code for i in validate_schedule(a,values)},{i.code for i in validate_schedule(b,values)})
            # Converting the last separation pair must not enable implicit
            # concurrency for unrelated roles merely because pairs became empty.
            from itertools import combinations
            from gate_schedule.domain import task_pair_conflict
            a=replace(a,flow_preferences=());b=replace(b,flow_preferences=())
            for first,second in combinations(a.tasks,2):
                self.assertEqual(bool(task_pair_conflict(a,first,second)),bool(task_pair_conflict(b,first,second)),(flight,first.id,second.id))
        single=dict(pairs=old['pairs'][:1],baggage=[])
        a=replace(scene(),work_rules=single,flow_preferences=());b=replace(a,work_rules=grouped_work_rules(single))
        for first,second in combinations(a.tasks,2):
            self.assertEqual(bool(task_pair_conflict(a,first,second)),bool(task_pair_conflict(b,first,second)),(first.id,second.id))

    def test_export_empty_import_restart_use_and_old_file_keeps_groups(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()/'.tmp') as folder:
            wb=Workbench(Path(folder)/'source.db');config=dict(day=relative_day(),rotationEnabled=True,rotationMinutes=60)
            state=wb.execute(0,'import-complete-configuration',config);pid=state['selectedPlanId']
            for role in state['configuration']['roleConfigs']:
                if role['roleName'] not in config['day']['roleNames']:
                    state=wb.execute(state['revision'],'save-role',{**role,'requiredCount':0})
            rules=dict(pairs=[],baggage=[],separationGroups=[group(roles=['登机核验','接机1']),group(roles=[],scope='CX*',enabled=False)])
            # Ensure a second role is present as a maintained business setting.
            state=wb.execute(state['revision'],'save-role',dict(roleName='接机1',requiredCount=0))
            prefs=[dict(**preference(['接机1'],source='登机核验'),targetFlightScopes={'接机1':'later-overlap'})]
            state=wb.execute(state['revision'],'save-rules',dict(configuration=state['configuration'],
                balanceSettings=state['balanceSettings'],preferences=prefs,workRules=rules))
            content=wb.export_complete_configuration()
            book=load_workbook(BytesIO(content));self.assertEqual('visible',book['岗位分人组'].sheet_state)
            self.assertEqual('visible',book['接续航班范围'].sheet_state)
            book.remove(book['岗位分人组']);book.remove(book['接续航班范围']);out=BytesIO();book.save(out);book.close()
            old=import_complete_configuration(out.getvalue())
            state=wb.execute(state['revision'],'import-complete-configuration',old)
            self.assertEqual(rules['separationGroups'],state['workRules']['separationGroups'])
            self.assertEqual(prefs,state['handoffPreferences'])
            target=Workbench(Path(folder)/'empty.db')
            state=target.execute(0,'import-complete-configuration',import_complete_configuration(content))
            target=Workbench(Path(folder)/'empty.db');state=target.snapshot()
            self.assertEqual(rules,state['workRules'])
            self.assertEqual(prefs,state['handoffPreferences'])
            state=target.execute(state['revision'],'solve',{},state['selectedPlanId'])
            sc,issues=plan_scenario(state['plans'][state['selectedPlanId']]);self.assertEqual([],issues)
            self.assertEqual(rules,sc.work_rules)
            self.assertEqual(tuple(prefs),sc.flow_preferences)
            self.assertTrue(state['plans'][state['selectedPlanId']]['valid'])
            # A present, empty group table intentionally clears maintained groups.
            book=load_workbook(BytesIO(content));book['岗位分人组'].delete_rows(2,book['岗位分人组'].max_row)
            book['接续航班范围'].delete_rows(2,book['接续航班范围'].max_row)
            out=BytesIO();book.save(out);book.close()
            state=target.execute(state['revision'],'import-complete-configuration',import_complete_configuration(out.getvalue()))
            self.assertEqual([],state['workRules']['separationGroups'])
            self.assertEqual({},state['handoffPreferences'][0]['targetFlightScopes'])

    def test_invalid_save_atomic_and_complete_backup_preserves_group_identity(self):
        from gate_schedule.group_workspaces import GroupWorkspaces
        from tests.test_work_relations import setup
        with tempfile.TemporaryDirectory(dir=Path.cwd()/'.tmp') as folder:
            groups=GroupWorkspaces(Path(folder)/'groups.db');wb=groups.workbench('A');state=setup(wb)
            roles=['登机1','登机2']
            rules=dict(pairs=[],baggage=[],separationGroups=[group(roles=roles),group(roles=[],scope='CX*',enabled=False)])
            payload=dict(configuration=state['configuration'],balanceSettings=state['balanceSettings'],
                preferences=state['handoffPreferences'],workRules=rules)
            state=wb.execute(state['revision'],'save-rules',payload)
            before=groups.backup();payload['workRules']=dict(pairs=[],baggage=[],separationGroups=[group(roles=['未知','登机1'])])
            with self.assertRaises(ValueError):wb.execute(state['revision'],'save-rules',payload)
            self.assertEqual(before,groups.backup())
            restored=GroupWorkspaces(Path(folder)/'restored.db');preview=restored.preview_restore('A',before)
            restored.restore('A',preview['expectedRevision'],before,preview['expectedGroupsRevision'])
            reopened=GroupWorkspaces(Path(folder)/'restored.db')
            self.assertEqual(rules,reopened.workbench('A').snapshot()['workRules'])
            self.assertEqual(before['groups']['A']['plans'],reopened.backup()['groups']['A']['plans'])


if __name__=='__main__':unittest.main()
