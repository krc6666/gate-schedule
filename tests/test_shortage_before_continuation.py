"""缺人留空高于兼职，不能以同人或暂时补不了缺口为例外。"""
from dataclasses import replace
import unittest

from gate_schedule.domain import Assignment, Flight, Member, Scenario, Task
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule


def shortage_scene():
    return Scenario('2026-08-24',
        (Flight('A', 'CX937', '2026-08-24', 610, 690),
         Flight('B', 'KE0166', '2026-08-24', 660, 735)),
        (Member('m', '甲', frozenset({'接机1', '秩序'}), 0),),
        (Task('source', 'A', '接机1', 610, 690, 1, 'mobile',
              timing_mode='phase', service_stage='arrival'),
         Task('low', 'A', '秩序', 610, 690, 1, 'mobile',
              timing_mode='phase', service_stage='boarding'),
         Task('missing', 'B', '接机1', 660, 735, 1, 'mobile',
              timing_mode='phase', service_stage='arrival')),
        release_policy='task', coverage_priorities=(('接机1', 'protect'), ('秩序', 'leave-empty')),
        flow_preferences=({'flightNo': '*', 'arrivalRole': '接机1',
            'boardingRoles': ['秩序'], 'enabled': True, 'ordered': True},))


class ShortageBeforeContinuationTests(unittest.TestCase):
    def test_worker_can_cover_later_arrival_before_earlier_continuation(self):
        s = shortage_scene()
        s = replace(s, tasks=tuple(replace(t, role_id='接机2') if t.id=='missing' else t for t in s.tasks),
            members=(replace(s.members[0], eligible_role_ids=s.members[0].eligible_role_ids | {'接机2'}),),
            flow_preferences=({**s.flow_preferences[0], 'boardingRoles':['秩序','接机2'],
                'targetFlightScopes':{'秩序':'same-flight','接机2':'later-overlap'}},))
        result = solve(s, allow_partial=True)
        self.assertEqual({Assignment('source','m'), Assignment('missing','m')}, set(result.assignments))
        self.assertEqual({('coverage_shortfall','low')},
            {(i.code,i.task_id) for i in validate_schedule(s,result.assignments)})

    def test_shortage_withholds_even_a_legal_same_person_continuation(self):
        s = shortage_scene()
        result = solve(s, allow_partial=True)
        self.assertEqual('OPTIMAL', result.status)
        self.assertEqual((Assignment('source', 'm'),), result.assignments)
        self.assertEqual({('coverage_shortfall', 'low'), ('coverage_shortfall', 'missing')},
            {(i.code, i.task_id) for i in validate_schedule(s, result.assignments)})
        # 此人仍不能兼做两个重叠接机；留空不授予新的跨班许可。
        broken = result.assignments + (Assignment('missing', 'm'),)
        self.assertIn('overlap', {i.code for i in validate_schedule(s, broken)})

    def test_enough_staff_keeps_continuation_and_fills_both_arrivals(self):
        s = shortage_scene()
        s = replace(s, members=s.members + (replace(s.members[0], id='n', name='乙'),))
        result = solve(s, allow_partial=True)
        self.assertEqual({'source', 'low', 'missing'}, {a.task_id for a in result.assignments})
        self.assertFalse(validate_schedule(s, result.assignments))

    def test_independent_window_is_not_emptied_by_earlier_shortage(self):
        s = shortage_scene()
        s = replace(s, flights=s.flights + (Flight('C', 'FD573', s.service_date, 735, 800),),
            tasks=s.tasks + (Task('separate', 'C', '秩序', 735, 800, 1, 'fixed'),))
        self.assertEqual({'source', 'separate'},
            {a.task_id for a in solve(s, allow_partial=True).assignments})

    def test_explicit_fixed_assignment_still_has_priority_over_soft_withholding(self):
        s = shortage_scene()
        s = replace(s, tasks=tuple(replace(t, locked_member_ids=('m',)) if t.id == 'low' else t for t in s.tasks))
        self.assertIn(Assignment('low', 'm'), solve(s, allow_partial=True).assignments)
