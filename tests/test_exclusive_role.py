from copy import deepcopy
from dataclasses import replace
import unittest

from gate_schedule.domain import Assignment, task_pair_conflict
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule
from gate_schedule.manual_editing import candidate_options
from gate_schedule.io import scenario_from_dict, scenario_to_dict
from test_candidate_availability import task, scenario


class ExclusiveRoleTests(unittest.TestCase):
    def example(self):
        arrival = task('arrival', '接机1', stage='arrival')
        dedicated = replace(task('main', '自定义主任', stage='boarding'), exclusive_same_flight=True)
        supervisor = task('supervisor', '督导', concurrent=True)
        return scenario(arrival, dedicated, supervisor)

    def test_solver_separates_and_validator_rejects_later_corruption(self):
        value = self.example()
        result = solve(value)
        self.assertIn(result.status, {'OPTIMAL', 'FEASIBLE'})
        self.assertEqual([], validate_schedule(value, result.assignments))
        owner = next(a.member_id for a in result.assignments if a.task_id == 'main')
        self.assertTrue(all(a.member_id != owner for a in result.assignments if a.task_id != 'main'))
        for target in ['arrival', 'supervisor']:
            broken = tuple(Assignment(a.task_id, owner) if a.task_id == target else a for a in result.assignments)
            self.assertIn('exclusive_same_flight', {i.code for i in validate_schedule(value, broken)})

    def test_both_directions_candidates_block_even_with_concurrency(self):
        value = self.example()
        for existing, target in [('main', 'arrival'), ('main', 'supervisor'), ('arrival', 'main'), ('supervisor', 'main')]:
            options = candidate_options(value, (Assignment(existing, '甲'),), target)
            person = next(item for item in options if item['id'] == '甲')
            self.assertFalse(person['available'])
            self.assertIn('exclusive_same_flight', {i['code'] for i in person['issues']})
            self.assertIn('专岗专人', ' '.join(person['reasons']))

    def test_nonoverlapping_same_flight_still_dedicated_but_other_flight_can_work(self):
        value = self.example()
        first = replace(value.tasks[1], timing_mode='exact', start=600, end=620)
        later = replace(value.tasks[0], timing_mode='exact', start=650, end=670)
        self.assertEqual('exclusive_same_flight', task_pair_conflict(value, first, later)[0])
        self.assertIsNone(task_pair_conflict(value, first, replace(later, flight_id='B')))

    def test_explicit_later_overlap_flow_can_cross_a_dedicated_role(self):
        value = self.example()
        source = replace(value.tasks[1], flight_id='A', task_kind='mobile', timing_mode='phase', start=610, end=690, locked_member_ids=('甲',))
        target = replace(value.tasks[0], id='later', flight_id='B', task_kind='mobile', timing_mode='phase', start=660, end=735)
        flights = value.flights
        flow = dict(flightNo='*', arrivalRole=source.role_id, boardingRoles=[target.role_id],
                    targetFlightScopes={target.role_id:'later-overlap'}, enabled=True, ordered=True)
        scene = replace(value, flights=flights, tasks=(source,target),
                        flow_preferences=(flow,), dedicated_flight_protection=True)
        self.assertIsNone(task_pair_conflict(scene, source, target))
        result = solve(scene)
        self.assertIn(result.status, {'OPTIMAL','FEASIBLE'})
        self.assertIn(Assignment(target.id, '甲'), result.assignments)
        self.assertEqual([], validate_schedule(scene, result.assignments))
        choice = next(c for c in candidate_options(scene, (Assignment(source.id,'甲'),), target.id) if c['id']=='甲')
        self.assertEqual((True,'continuation'), (choice['available'],choice['status']))
        # A dedicated target also restricts only its own flight. Both flags can
        # coexist along a legal cross-flight route.
        both = replace(scene,tasks=(source,replace(target,exclusive_same_flight=True)))
        self.assertIsNone(task_pair_conflict(both,*both.tasks))
        legal=(Assignment(source.id,'甲'),Assignment(target.id,'甲'))
        self.assertEqual([],validate_schedule(both,legal))
        # Moving that same target back into CX937 must still be rejected, even
        # if a saved preference asks for the same-flight continuation.
        same=replace(scene,tasks=(source,replace(target,flight_id='A')),
                     flow_preferences=({**flow,'targetFlightScopes':{target.role_id:'same-flight'}},))
        self.assertIn('exclusive_same_flight',{i.code for i in validate_schedule(same,legal)})
        no_route=replace(scene,flow_preferences=())
        self.assertEqual('overlap',task_pair_conflict(no_route,*no_route.tasks)[0])

    def test_dedicated_cross_flight_part_time_does_not_take_another_person(self):
        value=self.example()
        source=replace(value.tasks[1],task_kind='mobile',locked_member_ids=('甲',))
        target=replace(value.tasks[0],id='later',flight_id='B',task_kind='mobile',start=660,end=735)
        missing=replace(target,id='missing',role_id='无人资质岗')
        flow=dict(flightNo='*',arrivalRole=source.role_id,boardingRoles=[target.role_id],
                  targetFlightScopes={target.role_id:'later-overlap'},enabled=True,ordered=True)
        scene=replace(value,tasks=(source,target,missing),flow_preferences=(flow,),
                      coverage_priorities=((target.role_id,'leave-empty'),))
        result=solve(scene,allow_partial=True)
        self.assertIn(result.status,{'OPTIMAL','FEASIBLE'})
        self.assertIn(Assignment(target.id,'甲'),result.assignments)
        self.assertEqual({'coverage_shortfall'},{i.code for i in validate_schedule(scene,result.assignments)})
        # With no competing shortage the valid dedicated continuation is used.
        enough=replace(scene,tasks=(source,target))
        result=solve(enough)
        self.assertEqual([],validate_schedule(enough,result.assignments))
        self.assertIn(Assignment(target.id,'甲'),result.assignments)

    def test_shared_coverage_counts_and_same_role_segments_are_allowed(self):
        value = self.example()
        dedicated = value.tasks[1]
        shared = replace(task('baggage', '大件行李拦截', flight='B', timing='exact', kind='rotation'), covered_flight_ids=('A','B'))
        self.assertEqual('exclusive_same_flight', task_pair_conflict(value, dedicated, shared)[0])
        next_segment = replace(dedicated, id='next', timing_mode='exact', start=700, end=710)
        self.assertIsNone(task_pair_conflict(value, replace(dedicated, timing_mode='exact'), next_segment))

    def test_off_restores_original_behavior_and_json_preserves_on(self):
        value = self.example()
        raw = scenario_to_dict(value)
        restored = scenario_from_dict(raw)
        self.assertTrue(restored.tasks[1].exclusive_same_flight)
        self.assertEqual(raw, scenario_to_dict(restored))
        off = replace(value.tasks[1], exclusive_same_flight=False)
        self.assertIsNone(task_pair_conflict(value, off, value.tasks[0]))
        self.assertIsNone(task_pair_conflict(value, off, value.tasks[2]))
        legacy = deepcopy(raw)
        for item in legacy['tasks']: item.pop('exclusiveSameFlight', None)
        self.assertFalse(any(t.exclusive_same_flight for t in scenario_from_dict(legacy).tasks))

    def test_short_staff_cannot_bypass_dedicated_rule(self):
        value = self.example()
        value = replace(value, members=value.members[:1])
        self.assertEqual('INFEASIBLE', solve(value).status)
        partial = solve(value, allow_partial=True)
        self.assertIn(partial.status, {'OPTIMAL', 'FEASIBLE'})
        self.assertNotIn('exclusive_same_flight', {i.code for i in validate_schedule(value, partial.assignments)})
        self.assertLess(len(partial.assignments), len(value.tasks))

    def test_invalid_flag_is_rejected_by_independent_validation(self):
        value = self.example()
        invalid = replace(value, tasks=(replace(value.tasks[1], exclusive_same_flight='是'),))
        self.assertTrue(validate_schedule(invalid, ()))
