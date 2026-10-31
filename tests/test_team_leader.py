from dataclasses import replace
import unittest

from gate_schedule.domain import Assignment, Member
from gate_schedule.io import scenario_from_dict, scenario_to_dict
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule
from test_candidate_availability import task, scenario


class TeamLeaderTests(unittest.TestCase):
    def example(self):
        value = scenario(task('a', '接机1', stage='arrival'), task('b', '登机', stage='boarding'))
        return replace(value, members=tuple(replace(m, team_leader=m.id == '甲') for m in value.members))

    def test_ordinary_staff_first_even_when_leader_has_handoff_preference(self):
        value = self.example()
        result = solve(value)
        self.assertEqual('OPTIMAL', result.status)
        self.assertTrue(result.assignments)
        self.assertNotIn('甲', {a.member_id for a in result.assignments})
        self.assertEqual([], validate_schedule(value, result.assignments))

    def test_shortage_uses_qualified_leader_before_leaving_vacancy(self):
        value = self.example()
        value = replace(value, tasks=(replace(value.tasks[0], required_count=len(value.members)),))
        result = solve(value, allow_partial=True)
        self.assertEqual([], validate_schedule(value, result.assignments))
        self.assertIn('甲', {a.member_id for a in result.assignments})
        # A leader flag never grants a missing qualification.
        value = replace(value, members=tuple(replace(m, eligible_role_ids=frozenset()) if m.team_leader else m for m in value.members))
        partial = solve(value, allow_partial=True)
        self.assertNotIn('甲', {a.member_id for a in partial.assignments})
        self.assertEqual({'coverage_shortfall'}, {i.code for i in validate_schedule(value, partial.assignments)})

    def test_leader_priority_precedes_monthly_rotation_and_can_be_disabled(self):
        value = self.example()
        value = replace(value, tasks=(value.tasks[0],), priority_role_ids=('接机1',))
        result = solve(value, priority_counts={('甲','接机1'):0,('乙','接机1'):100})
        self.assertEqual((Assignment('a','乙'),), result.assignments)
        off = solve(replace(value, team_leader_last=False), priority_counts={('甲','接机1'):0,('乙','接机1'):100})
        self.assertEqual((Assignment('a','甲'),), off.assignments)

    def test_fixed_leader_respected_and_manual_leader_is_legal(self):
        value = self.example()
        value = replace(value, tasks=(replace(value.tasks[0], locked_member_ids=('甲',)),))
        result = solve(value)
        self.assertEqual((Assignment('a', '甲'),), result.assignments)
        self.assertEqual([], validate_schedule(value, result.assignments))

    def test_hard_gap_preserved_and_exemption_is_explicit(self):
        value = self.example()
        value = replace(value, tasks=(value.tasks[0],), flight_count_gap=0, balance_exempt_team_leaders=False)
        self.assertEqual('INFEASIBLE', solve(value).status)
        exempt = replace(value, balance_exempt_team_leaders=True, members=value.members[:2])
        result = solve(exempt)
        self.assertEqual([], validate_schedule(exempt, result.assignments))
        self.assertNotIn('甲', {a.member_id for a in result.assignments})
        corrupted = replace(exempt, balance_exempt_team_leaders=False)
        self.assertIn('flight_count_spread', {i.code for i in validate_schedule(corrupted, result.assignments)})

    def test_snapshot_roundtrip_and_invalid_flags(self):
        value = replace(self.example(), balance_exempt_team_leaders=True)
        self.assertEqual(value, scenario_from_dict(scenario_to_dict(value)))
        for invalid in [replace(value, team_leader_last='是'), replace(value, balance_exempt_team_leaders=1),
                        replace(value, members=(replace(value.members[0], team_leader='是'),))]:
            self.assertEqual('MODEL_INVALID', solve(invalid).status)

    def test_exemption_does_not_relax_ordinary_staff_gap_or_leader_conflicts(self):
        value = self.example()
        value = replace(value, members=(*value.members, Member('丙','丙',value.members[1].eligible_role_ids,540,1560)),
                        tasks=(replace(value.tasks[0], locked_member_ids=('乙',)),), flight_count_gap=0)
        self.assertEqual('INFEASIBLE', solve(value).status)
        value = self.example()
        value = replace(value, tasks=(replace(value.tasks[0], locked_member_ids=('甲',)),
                                    replace(value.tasks[0], id='overlap', locked_member_ids=('甲',))))
        self.assertEqual('INFEASIBLE', solve(value).status)
        self.assertTrue(validate_schedule(value, (Assignment('a','甲'),Assignment('overlap','甲'))))
