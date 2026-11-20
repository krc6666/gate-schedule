from dataclasses import replace
import unittest

from gate_schedule.domain import Assignment, Flight, Member, Scenario, Task
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule


def scene():
    roles=frozenset({'申报','秩序','查验','接机'})
    prefs=({'flightNo':'*','arrivalRole':'申报','boardingRoles':['秩序'],
            'targetFlightScopes':{'秩序':'later-overlap'},'enabled':True,'ordered':True},)
    return Scenario('2026-08-26',
        (Flight('A','CX937','2026-08-26',610,690),Flight('B','KE0166','2026-08-26',660,735)),
        (Member('m','甲',roles,0),),
        (Task('source','A','申报',610,690,1,'mobile',timing_mode='phase',service_stage='arrival',exclusive_same_flight=True),
         Task('target','B','秩序',660,735,1,'mobile',timing_mode='phase',service_stage='boarding'),
         Task('missing','B','接机',660,735,1,'mobile',timing_mode='phase',service_stage='arrival')),
        release_policy='task',flow_preferences=prefs,
        coverage_priorities=(('秩序','leave-empty'),('查验','leave-empty')))


class ContinuationStaffingTests(unittest.TestCase):
    def test_configured_part_time_does_not_consume_another_person_in_shortage(self):
        s=scene();result=solve(s,allow_partial=True)
        self.assertEqual('OPTIMAL',result.status)
        self.assertEqual({Assignment('source','m'),Assignment('target','m')},set(result.assignments))
        self.assertEqual({('coverage_shortfall','missing')},{(i.code,i.task_id) for i in validate_schedule(s,result.assignments)})

    def test_no_source_no_route_disabled_unqualified_and_dedicated_remain_boundaries(self):
        original=scene()
        cases=[replace(original,flow_preferences=()),
               replace(original,flow_preferences=({**original.flow_preferences[0],'enabled':False},)),
               replace(original,members=(replace(original.members[0],eligible_role_ids=frozenset({'秩序','接机'})),)),
               replace(original,members=(replace(original.members[0],eligible_role_ids=frozenset({'申报','接机'})),)),
               replace(original,tasks=tuple(replace(t,flight_id='A',start=610,end=690) if t.id=='target' else t for t in original.tasks),
                   flow_preferences=({**original.flow_preferences[0],'targetFlightScopes':{'秩序':'same-flight'}},))]
        for s in cases:
            with self.subTest(s=s):
                result=solve(s,allow_partial=True)
                self.assertNotIn(Assignment('target','m'),result.assignments)
        valid=solve(original,allow_partial=True).assignments
        broken=replace(original,tasks=tuple(replace(t,flight_id='A') if t.id=='target' else t for t in original.tasks))
        self.assertIn('exclusive_same_flight',{i.code for i in validate_schedule(broken,valid)})

    def test_chain_can_continue_but_low_only_cycle_cannot_create_staff(self):
        original=scene()
        chain=replace(original,tasks=original.tasks+(Task('third','B','查验',660,735,1,'mobile',timing_mode='phase',service_stage='boarding'),),
            flow_preferences=({**original.flow_preferences[0],'boardingRoles':['秩序','查验'],
                'targetFlightScopes':{'秩序':'later-overlap','查验':'later-overlap'}}, {'flightNo':'*','arrivalRole':'秩序','boardingRoles':['查验'],
                'targetFlightScopes':{'查验':'same-flight'},'enabled':True,'ordered':True},))
        values=solve(chain,allow_partial=True).assignments
        self.assertEqual({'source','target','third'},{a.task_id for a in values})
        no_anchor=replace(chain,tasks=tuple(t for t in chain.tasks if t.id!='source'),
            flow_preferences=chain.flow_preferences+({'flightNo':'*','arrivalRole':'查验','boardingRoles':['秩序'],
                'targetFlightScopes':{'秩序':'same-flight'},'enabled':True,'ordered':True},),
            members=(replace(chain.members[0],eligible_role_ids=frozenset({'秩序','查验'})),))
        self.assertFalse(solve(no_anchor,allow_partial=True).assignments)

    def test_forbidden_handoff_remains_hard_even_for_a_free_part_time(self):
        original=scene()
        blocked=replace(original,work_rules={'pairs':[],'baggage':[],
            'forbiddenHandoffs':[{'flightNo':'*','sourceRole':'申报','targets':[{'role':'秩序','flightScope':'later-overlap'}],'enabled':True}]})
        # Exercise the real configured form, including the final validator.
        values=solve(blocked,allow_partial=True).assignments
        self.assertNotIn(Assignment('target','m'),values)
        broken=values+(Assignment('target','m'),)
        self.assertTrue(any(i.code!='coverage_shortfall' for i in validate_schedule(blocked,broken)))

    def test_two_configured_compatible_targets_do_not_invent_a_one_job_limit(self):
        original=scene()
        s=replace(original,tasks=original.tasks+(Task('third','B','查验',660,735,1,'mobile',timing_mode='phase',service_stage='boarding',concurrent=True),),
            flow_preferences=({**original.flow_preferences[0],'boardingRoles':['秩序','查验'],
                'targetFlightScopes':{'秩序':'later-overlap','查验':'later-overlap'}},))
        legal=(Assignment('source','m'),Assignment('target','m'),Assignment('third','m'))
        self.assertEqual({'coverage_shortfall'},{i.code for i in validate_schedule(s,legal)})
        self.assertEqual(set(legal),set(solve(s,allow_partial=True).assignments))

    def test_an_extra_person_at_low_role_is_still_withheld_and_flight_order_is_kept(self):
        s=scene()
        s=replace(s,members=s.members+(Member('other','乙',frozenset({'秩序'}),0),),
                  tasks=tuple(replace(t,required_count=2) if t.id=='target' else t for t in s.tasks))
        values=solve(s,allow_partial=True).assignments
        self.assertEqual({Assignment('source','m'),Assignment('target','m')},set(values))
        # A legal source in an earlier independent window does not turn an
        # otherwise independently staffed role in this shortage into a free job.
        disjoint=replace(s,flights=(replace(s.flights[0],window_end=650),s.flights[1]),
            tasks=tuple(replace(t,end=650) if t.id=='source' else t for t in s.tasks),
            members=tuple(replace(m,eligible_role_ids=m.eligible_role_ids-{'接机'}) for m in s.members),
            flow_preferences=({**s.flow_preferences[0],'targetFlightScopes':{}},))
        self.assertFalse(any(a.task_id=='target' for a in solve(disjoint,allow_partial=True).assignments))
