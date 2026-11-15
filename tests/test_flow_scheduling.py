"""Owner-confirmed process routing, without invented task minutes."""
from copy import deepcopy
from io import BytesIO
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from openpyxl import load_workbook

from gate_schedule.domain import Assignment, Flight, Member, Rotation, Scenario, Task, preferred_mobile_handoffs
from gate_schedule.excel_io import import_complete_configuration
from gate_schedule.day_document import imported_day_from_dict
from gate_schedule.imported_schedule import imported_day_to_scenario
from gate_schedule.io import scenario_from_dict, scenario_to_dict
from gate_schedule.manual_editing import candidate_options
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule
from gate_schedule.workbench import Workbench
from gate_schedule.workspace_document import plan_scenario
from tests.test_relative_scheduling import relative_day


def preference(targets=None, scope='*', source='大件行李拦截', enabled=True):
    return dict(flightNo=scope, arrivalRole=source, boardingRoles=targets or ['接机1','接机2'], enabled=enabled, ordered=True)


def flow_scene():
    tasks = (Task('bag1','A','大件行李拦截',480,540,1,'rotation',locked_member_ids=('甲',)),
             Task('bag2','A','大件行李拦截',540,600,1,'rotation',locked_member_ids=('丙',)),
             Task('r1','A','接机1',480,660,1,'mobile',timing_mode='phase'),
             Task('r2','A','接机2',480,660,1,'mobile',timing_mode='phase'))
    roles = frozenset(t.role_id for t in tasks)
    people = (Member('甲','甲',roles,0),Member('乙','乙',frozenset({'接机1','接机2'}),0),
              Member('丙','丙',frozenset({'大件行李拦截'}),0))
    return Scenario('2057-08-02',(Flight('A','CX937','2057-08-02',480,660),),people,tasks,
        release_policy='task',flow_preferences=(preference(),),
        rotations=(Rotation('bag1','pool',480,540,1,('甲','丙'),0),Rotation('bag2','pool',540,600,1,('甲','丙'),1)),
        work_rules={'pairs':[dict(flightNo='*',firstRole='接机1',secondRole='接机2',scope='same-flight',mode='separate')],'baggage':[]})


class FlowSchedulingTests(unittest.TestCase):
    def test_cross_flight_continuation_only_goes_forward_and_cycles_stay_feasible(self):
        scenario=flow_scene()
        scene=replace(scenario, flights=(*scenario.flights,Flight('B','B','2057-08-02',720,780)),
            tasks=(*scenario.tasks,Task('late','B','接机1',720,780,1,'mobile',timing_mode='phase')),
            flow_preferences=(preference(['接机2'],source='接机1'),))
        self.assertNotIn(('late','r2'),[(a.id,b.id) for a,b in preferred_mobile_handoffs(scene)])
        chain=replace(scenario,flow_preferences=(preference(['接机1']),preference(['大件行李拦截'],source='接机1')))
        result=solve(chain)
        self.assertIn(result.status,{'OPTIMAL','FEASIBLE'})
        self.assertEqual([],validate_schedule(chain,result.assignments))

    def test_first_choice_then_filled_or_unqualified_fallback(self):
        scenario = flow_scene()
        result = solve(scenario)
        self.assertEqual([],validate_schedule(scenario,result.assignments))
        self.assertIn(Assignment('r1','甲'),result.assignments)
        fixed = replace(scenario,tasks=tuple(replace(t,locked_member_ids=('乙',)) if t.id=='r1' else t for t in scenario.tasks))
        result = solve(fixed)
        self.assertEqual([],validate_schedule(fixed,result.assignments))
        self.assertIn(Assignment('r2','甲'),result.assignments)
        restricted = replace(scenario,members=tuple(replace(m,eligible_role_ids=m.eligible_role_ids-{'接机1'}) if m.id=='甲' else m for m in scenario.members))
        self.assertIn(Assignment('r2','甲'),solve(restricted).assignments)

    def test_flow_candidate_is_explained_as_continuation_and_ignores_legacy_input(self):
        scenario = flow_scene()
        choices = candidate_options(scenario, (Assignment('bag1','甲'),), 'r1')
        choice = next(item for item in choices if item['id'] == '甲')
        self.assertEqual(('continuation', '可接续'), (choice['status'], choice['label']))
        # New snapshots own their preference contract. An unrelated legacy
        # payload cannot invalidate or alter a new flow solve.
        result = solve(scenario, handoff_preferences=({'invalid': True},))
        self.assertIn(result.status, {'OPTIMAL', 'FEASIBLE'})

    def test_series_override_disable_and_legacy_snapshot(self):
        scenario=flow_scene()
        special=replace(scenario,flow_preferences=(preference(),preference(['接机2','接机1'],'CX*')))
        self.assertIn(Assignment('r2','甲'),solve(special).assignments)
        disabled=replace(scenario,flow_preferences=(preference(),preference(['接机2'],'CX*',enabled=False)))
        choices=candidate_options(disabled,(Assignment('bag1','甲'),),'r1')
        self.assertFalse(next(c['available'] for c in choices if c['id']=='甲'))
        saved=scenario_to_dict(scenario)
        self.assertEqual(scenario,scenario_from_dict(saved))
        saved.pop('flowPreferences')
        self.assertIsNone(scenario_from_dict(saved).flow_preferences)

    def test_cross_flight_window_and_hard_separation_manual_reverse(self):
        scenario=flow_scene(); result=solve(scenario)
        self.assertEqual([],validate_schedule(scenario,result.assignments))
        broken=tuple(a for a in result.assignments if a.task_id!='r2')+(Assignment('r2','甲'),)
        self.assertIn('role_separation',{i.code for i in validate_schedule(scenario,broken)})
        extra=Task('other','B','入境申报',660,680,1,'mobile')
        cross=replace(scenario,flights=(*scenario.flights,Flight('B','B','2057-08-02',600,720)),
            tasks=(*scenario.tasks,extra),members=tuple(replace(m,eligible_role_ids=m.eligible_role_ids|{'入境申报'}) for m in scenario.members))
        broken=(*result.assignments,Assignment('other','甲'))
        self.assertIn('overlap',{i.code for i in validate_schedule(cross,broken)})
        self.assertFalse(next(c['available'] for c in candidate_options(cross,result.assignments,'other') if c['id']=='甲'))
        dedicated=replace(scenario,tasks=tuple(replace(t,exclusive_same_flight=True) if t.id=='r1' else t for t in scenario.tasks))
        self.assertIn('exclusive_same_flight',{i.code for i in validate_schedule(dedicated,result.assignments)})
        separated=replace(scenario,
            tasks=(replace(scenario.tasks[0],end=660),scenario.tasks[2]),
            rotations=(replace(scenario.rotations[0],segment_end=660),),
            work_rules={'pairs':[*scenario.work_rules['pairs'],dict(flightNo='*',firstRole='大件行李拦截',secondRole='接机1',scope='after-baggage',mode='separate')],'baggage':[]})
        self.assertIn('role_separation',{i.code for i in validate_schedule(separated,(Assignment('bag1','甲'),Assignment('r1','甲')))})
        self.assertFalse(next(c['available'] for c in candidate_options(separated,(Assignment('bag1','甲'),),'r1') if c['id']=='甲'))
        result=solve(separated)
        self.assertEqual([],validate_schedule(separated,result.assignments))
        self.assertNotIn(Assignment('r1','甲'),result.assignments)

    def test_rotation_overlap_remains_hard_and_all_blocked_leaves_red_gap(self):
        scenario=flow_scene();result=solve(scenario)
        broken=tuple(a for a in result.assignments if a.task_id!='bag2')+(Assignment('bag2','甲'),)
        overlap=replace(scenario,tasks=tuple(replace(t,start=530) if t.id=='bag2' else t for t in scenario.tasks),
            rotations=(scenario.rotations[0],replace(scenario.rotations[1],segment_start=530)))
        self.assertIn('overlap',{i.code for i in validate_schedule(overlap,broken)})
        blocked=replace(scenario,members=tuple(replace(m,eligible_role_ids=frozenset({'大件行李拦截'})) for m in scenario.members))
        partial=solve(blocked,allow_partial=True)
        self.assertIn(partial.status,{'OPTIMAL','FEASIBLE'})
        self.assertEqual(2,sum(i.code=='coverage_shortfall' for i in validate_schedule(blocked,partial.assignments)))

    def test_generation_ignores_obsolete_task_minutes_and_uses_flight_envelope(self):
        data=relative_day();data['flowPreferences']=[]
        data['flights'][0].update(arrivalTime='前日23:30',departureTime='00:20')
        data['cells'][0].update(workModeOverride='timed',workStart='00:00',workEnd='00:10')
        scenario,issues=imported_day_to_scenario(imported_day_from_dict(data))
        self.assertEqual([],issues)
        self.assertEqual((-30,20,'phase'),(scenario.tasks[0].start,scenario.tasks[0].end,scenario.tasks[0].timing_mode))
        self.assertEqual((-30,20),(scenario.flights[0].window_start,scenario.flights[0].window_end))
        data['roleConfigs'][0].update(requiredCount=None,workMode='timed')
        data['cells'][0].update(requiredCountOverride=None,workStart=None,workEnd=None)
        _,issues=imported_day_to_scenario(imported_day_from_dict(data))
        self.assertIn('missing_required_count',{i.code for i in issues})
        self.assertNotIn('missing_work_time',{i.code for i in issues})

    def test_configuration_export_empty_restart_actual_use_and_formal_preserved(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()/'.tmp',prefix='flow-') as folder:
            wb=Workbench(Path(folder)/'source.db')
            day=relative_day()
            state=wb.execute(0,'import-complete-configuration',dict(day=day,rotationEnabled=True,rotationMinutes=60))
            pid=state['selectedPlanId']
            state=wb.execute(state['revision'],'solve',{},pid)
            self.assertIn('flowPreferences',state['plans'][pid]['day'])
            scenario,issues=plan_scenario(state['plans'][pid]);self.assertEqual([],issues)
            self.assertEqual('phase',scenario.tasks[0].timing_mode)
            state=wb.execute(state['revision'],'adopt',{},pid)
            formal=deepcopy(state['plans'][pid]['adopted'])
            prefs=[preference(['登机核验'],source='自定义来源')]
            state=wb.execute(state['revision'],'save-role',dict(roleName='自定义来源',requiredCount=0))
            state=wb.execute(state['revision'],'save-handoff-preferences',dict(preferences=prefs))
            for role in state['configuration']['roleConfigs']:
                if role['roleName'] not in day['roleNames']:
                    state=wb.execute(state['revision'],'save-role',{**role,'requiredCount':0})
            exported=wb.export_complete_configuration(pid)
            book=load_workbook(BytesIO(exported))
            self.assertEqual('hidden',book['相对岗位时间'].sheet_state)
            self.assertEqual('visible',book['岗位接续偏好'].sheet_state)
            book.close()
            path=Path(folder)/'target.db';target=Workbench(path)
            state=target.execute(0,'import-complete-configuration',import_complete_configuration(exported))
            target=Workbench(path);state=target.snapshot()
            self.assertEqual(prefs,state['handoffPreferences'])
            self.assertEqual(formal,target.backup()['state']['plans'][pid]['adopted'])
            state=target.execute(state['revision'],'solve',{},state['selectedPlanId'])
            self.assertTrue(state['plans'][state['selectedPlanId']]['valid'])


if __name__=='__main__':unittest.main()
