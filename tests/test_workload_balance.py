from dataclasses import replace
import unittest

from gate_schedule.domain import (
    Assignment, Flight, Member, Scenario, Task, draft_blocking_issues,
    default_balance_settings, member_flight_participation, member_role_flight_counts,
    task_flight_ids, validate_balance_settings, scarce_role_groups,
)
from gate_schedule.solver import solve
from gate_schedule.validator import validate_scenario, validate_schedule


def workload_scenario(count=3, *, people=2, role="重点岗", gap=None, priority=()):
    flights = tuple(Flight(f"f{i}", f"F{i}", "2026-08-02", 600 + 60 * i, 630 + 60 * i)
                    for i in range(count))
    tasks = tuple(Task(f"t{i}", f.id, role, f.window_start, f.window_end, 1, "fixed")
                  for i, f in enumerate(flights))
    members = tuple(Member(f"m{i}", f"人员{i}", frozenset({role}), 0, 1440) for i in range(people))
    return Scenario("2026-08-02", flights, members, tasks, release_policy="task",
                    flight_count_gap=gap, priority_role_ids=priority)


class ParticipationFactTests(unittest.TestCase):
    def test_shared_segments_same_flight_roles_and_occupancy_are_counted_once(self):
        value = workload_scenario(2, people=3)
        first, second = value.tasks
        shared = replace(first, id="shared", covered_flight_ids=("f0", "f1"), role_id="大件")
        shared_tail = replace(shared, id="shared-tail", start=615)
        ordinary = replace(second, id="ordinary", flight_id="f0", role_id="其他岗")
        occupancy = replace(first, id="occupancy", flight_id=None, role_id="0910")
        value = replace(value, tasks=(shared, shared_tail, ordinary, occupancy))
        assignments = tuple(Assignment(task.id, "m0") for task in value.tasks)
        self.assertEqual(("f0", "f1"), task_flight_ids(shared))
        self.assertEqual(("f0",), task_flight_ids(ordinary))
        self.assertEqual((), task_flight_ids(occupancy))
        self.assertEqual({"m0": frozenset({"f0", "f1"}), "m1": frozenset(), "m2": frozenset()},
                         member_flight_participation(value, assignments))
        counts = member_role_flight_counts(value, assignments)
        self.assertEqual(2, counts["m0", "大件"])
        self.assertEqual(1, counts["m0", "其他岗"])
        self.assertEqual(0, counts["m0", "0910"])
        self.assertEqual(0, counts["m2", "大件"])
        self.assertEqual({(m.id, "当天没有的重点岗"): 0 for m in value.members},
                         member_role_flight_counts(value, assignments, ("当天没有的重点岗",)))

    def test_counting_ignores_unknown_assignments_without_hiding_validator_errors(self):
        value = workload_scenario(1)
        assignments = (Assignment("unknown", "m0"), Assignment("t0", "unknown"))
        self.assertEqual({m.id: frozenset() for m in value.members},
                         member_flight_participation(value, assignments))
        self.assertEqual({"unknown_task", "unknown_member", "coverage_shortfall"},
                         {i.code for i in validate_schedule(value, assignments)})


class BalanceSettingsTests(unittest.TestCase):
    def test_defaults_are_independent_and_complete(self):
        value = default_balance_settings()
        self.assertEqual({"flightCountGap": None, "priorityEnabled": False,
                          "priorityRoleNames": [], "priorityAdjustments": []}, value)
        value["priorityRoleNames"].append("重点岗")
        self.assertEqual([], default_balance_settings()["priorityRoleNames"])

    def test_valid_settings_keep_negative_adjustments_and_return_copy(self):
        value = {"flightCountGap": 0, "priorityEnabled": True, "priorityRoleNames": ["重点岗"],
                 "priorityAdjustments": [{"month": "2026-08", "memberId": "m0", "roleName": "重点岗", "delta": -3}]}
        checked = validate_balance_settings(value, {"重点岗"}, {"m0"})
        self.assertEqual(value, checked)
        self.assertIsNot(value, checked)
        self.assertIsNot(value["priorityAdjustments"][0], checked["priorityAdjustments"][0])
        self.assertEqual(value, validate_balance_settings(value, {"重点岗"}))

    def test_bad_settings_are_rejected_by_one_owner(self):
        from copy import deepcopy
        valid = {"flightCountGap": 1, "priorityEnabled": True, "priorityRoleNames": ["重点岗"],
                 "priorityAdjustments": [{"month": "2026-08", "memberId": "m0", "roleName": "重点岗", "delta": 1}]}
        invalid = []
        for key, value in (("flightCountGap", True), ("flightCountGap", -1), ("priorityEnabled", 1),
                           ("priorityRoleNames", ["未知"]), ("priorityRoleNames", ["重点岗", "重点岗"]),
                           ("priorityAdjustments", {})):
            entry = deepcopy(valid); entry[key] = value; invalid.append(entry)
        for key, value in (("month", "2026-13"), ("month", "2026-8"), ("month", "0000-01"),
                           ("memberId", ""), ("memberId", "unknown"), ("roleName", "未知"),
                           ("delta", True), ("delta", 1.5)):
            entry = deepcopy(valid); entry["priorityAdjustments"][0][key] = value; invalid.append(entry)
        entry = deepcopy(valid); entry["priorityAdjustments"] *= 2; invalid.append(entry)
        entry = deepcopy(valid); del entry["priorityEnabled"]; invalid.append(entry)
        entry = deepcopy(valid); entry["unknown"] = 1; invalid.append(entry)
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_balance_settings(value, {"重点岗"}, {"m0"})


class FlightCountGapTests(unittest.TestCase):
    def assert_safe(self, scenario, result, *, partial=False):
        self.assertIn(result.status, {"OPTIMAL", "FEASIBLE"})
        issues = validate_schedule(scenario, result.assignments)
        self.assertEqual([], draft_blocking_issues(issues) if partial else issues)
        counts = [len(ids) for ids in member_flight_participation(scenario, result.assignments).values()]
        if counts and scenario.flight_count_gap is not None:
            self.assertLessEqual(max(counts) - min(counts), scenario.flight_count_gap)

    def test_solver_respects_zero_and_one_limits_with_complete_coverage(self):
        for count, gap in ((2, 0), (3, 1), (4, 0), (4, 2)):
            with self.subTest(count=count, gap=gap):
                value = workload_scenario(count, gap=gap)
                result = solve(value)
                self.assert_safe(value, result)
                self.assertEqual(count, len(result.assignments))

    def test_manual_change_breaking_gap_is_rejected_after_legal_result(self):
        value = workload_scenario(3, gap=1)
        legal = solve(value)
        self.assert_safe(value, legal)
        changed = tuple(Assignment(a.task_id, "m0") for a in legal.assignments)
        problems = validate_schedule(value, changed)
        issue = next(i for i in problems if i.code == "flight_count_spread")
        self.assertIn("3", issue.message)
        self.assertIn("0", issue.message)
        self.assertIn("1", issue.message)
        self.assertIn(issue, draft_blocking_issues(problems))
        self.assertEqual([], validate_schedule(replace(value, flight_count_gap=None), changed))

    def test_zero_work_participant_is_in_minimum_and_partial_never_relaxes_gap(self):
        value = workload_scenario(3, gap=1)
        value = replace(value, members=(value.members[0], replace(value.members[1], eligible_role_ids=frozenset())))
        result = solve(value, allow_partial=True)
        self.assert_safe(value, result, partial=True)
        self.assertEqual(1, len(result.assignments))
        self.assertEqual(2, sum(i.code == "coverage_shortfall" for i in validate_schedule(value, result.assignments)))
        self.assertEqual("INFEASIBLE", solve(value).status)

    def test_zero_gap_can_require_empty_partial_and_must_not_be_reported_complete(self):
        value = workload_scenario(1, gap=0)
        self.assertEqual("INFEASIBLE", solve(value).status)
        result = solve(value, allow_partial=True)
        self.assert_safe(value, result, partial=True)
        self.assertEqual((), result.assignments)
        self.assertEqual({"coverage_shortfall"}, {i.code for i in validate_schedule(value, result.assignments)})

    def test_fixed_work_can_make_even_partial_infeasible(self):
        value = workload_scenario(2, gap=1)
        value = replace(value, tasks=tuple(replace(task, locked_member_ids=("m0",)) for task in value.tasks))
        result = solve(value, allow_partial=True)
        self.assertEqual("INFEASIBLE", result.status)
        self.assertIn("航班数", "；".join(result.diagnostics))

    def test_shared_task_counts_all_covered_flights_in_solver_and_validator(self):
        value = workload_scenario(2, gap=1)
        shared = replace(value.tasks[0], id="shared", covered_flight_ids=("f0", "f1"))
        value = replace(value, tasks=(shared,))
        self.assertEqual("INFEASIBLE", solve(value).status)
        result = solve(value, allow_partial=True)
        self.assert_safe(value, result, partial=True)
        self.assertEqual((), result.assignments)
        self.assertIn("flight_count_spread", {i.code for i in validate_schedule(value, (Assignment("shared", "m0"),))})

    def test_same_flight_multiple_tasks_do_not_consume_multiple_flight_counts(self):
        value = workload_scenario(1, gap=1)
        first = replace(value.tasks[0], end=610, locked_member_ids=("m0",))
        second = replace(first, id="second", start=610, end=620)
        value = replace(value, tasks=(first, second))
        result = solve(value)
        self.assert_safe(value, result)
        self.assertEqual(2, len(result.assignments))

    def test_empty_members_and_disabled_rule_preserve_old_behavior(self):
        value = workload_scenario(0, people=0, gap=0)
        self.assert_safe(value, solve(value))
        value = workload_scenario(3)
        assignments = tuple(Assignment(task.id, "m0") for task in value.tasks)
        self.assertEqual([], validate_schedule(value, assignments))

    def test_invalid_new_rule_values_are_model_invalid(self):
        base = workload_scenario(1)
        for gap in (-1, True, 1.5, "1"):
            with self.subTest(gap=gap):
                value = replace(base, flight_count_gap=gap)
                self.assertIn("invalid_flight_count_gap", {i.code for i in validate_scenario(value)})
                self.assertEqual("MODEL_INVALID", solve(value).status)
        for roles in (("重点岗", "重点岗"), ("",), (" 岗位",), (1,)):
            with self.subTest(roles=roles):
                value = replace(base, priority_role_ids=roles)
                self.assertIn("invalid_priority_roles", {i.code for i in validate_scenario(value)})
                self.assertEqual("MODEL_INVALID", solve(value).status)
        self.assertEqual([], validate_scenario(replace(base, priority_role_ids=("当天没有的岗位",))))


class PriorityRoleRotationTests(unittest.TestCase):
    def assert_safe(self, value, result):
        self.assertIn(result.status, {"OPTIMAL", "FEASIBLE"})
        self.assertEqual([], validate_schedule(value, result.assignments))

    def scarcity_scenario(self):
        value = workload_scenario(2, priority=("普通岗",), role="稀缺岗")
        rare, common = "稀缺岗", "普通岗"
        members = tuple(replace(m, eligible_role_ids=frozenset({rare, common})) for m in value.members)
        members += (Member("m2", "早退人员", frozenset({common}), 0, 650),)
        first = replace(value.tasks[0], end=610, locked_member_ids=("m0",))
        earlier_common = replace(first, id="common0", role_id=common, start=610, end=620)
        later_common = replace(value.tasks[1], id="common1", role_id=common)
        return replace(value, members=members, tasks=(first, earlier_common, value.tasks[1], later_common))

    def test_automatic_scarce_role_spread_precedes_selected_common_role(self):
        value = self.scarcity_scenario()
        result = solve(value)
        self.assert_safe(value, result)
        counts = member_role_flight_counts(value, result.assignments)
        self.assertEqual([1, 1], [counts.get((m, "稀缺岗"), 0) for m in ("m0", "m1")])
        self.assertEqual("m0", next(a.member_id for a in result.assignments if a.task_id == "common1"))

    def test_automatic_scarce_month_counts_precede_common_rotation_without_selection(self):
        value = self.scarcity_scenario()
        value = replace(value, tasks=value.tasks[2:], priority_role_ids=())
        result = solve(value, priority_counts={
            ("m0", "稀缺岗"): 0, ("m1", "稀缺岗"): 3,
            ("m0", "普通岗"): 0, ("m1", "普通岗"): 20},
            previous_roles={("m1", "稀缺岗"), ("m1", "普通岗")})
        self.assert_safe(value, result)
        self.assertEqual("m0", next(a.member_id for a in result.assignments if a.task_id == "t1"))

    def test_automatic_scarce_rotation_never_overrides_coverage_or_fixed_people(self):
        value = self.scarcity_scenario()
        tasks = tuple(replace(t, locked_member_ids=("m0",)) if t.id == "t1" else t for t in value.tasks)
        value = replace(value, tasks=tasks)
        result = solve(value, allow_partial=True)
        self.assert_safe(value, result)
        self.assertEqual(len(value.tasks), len(result.assignments))
        self.assertEqual("m0", next(a.member_id for a in result.assignments if a.task_id == "t1"))

    def test_scarcity_groups_ignore_leave_zero_demand_and_extra_occupancy(self):
        value = self.scarcity_scenario()
        self.assertEqual((("稀缺岗",),), scarce_role_groups(value))
        absent = replace(value, members=(*value.members[:2], replace(value.members[2], status="休假")))
        self.assertEqual((), scarce_role_groups(absent), "请假人员不应使普通岗显得更不稀缺")
        extra = replace(value.tasks[0], id="extra", flight_id=None, role_id="额外占用")
        unused = replace(value.tasks[0], id="unused", role_id="无需求岗", required_count=0)
        changed = replace(value, tasks=(*value.tasks, extra, unused))
        self.assertEqual(scarce_role_groups(value), scarce_role_groups(changed))
        self.assertEqual((), scarce_role_groups(replace(value, tasks=())))

    def test_equal_scarcity_shares_one_tier_and_more_scarce_roles_come_first(self):
        value = self.scarcity_scenario()
        members = tuple(replace(m, eligible_role_ids=m.eligible_role_ids | {"另一稀缺岗"}) if m.id != "m2" else m
                        for m in value.members)
        members = (replace(members[0], eligible_role_ids=members[0].eligible_role_ids | {"唯一资质岗"}), *members[1:])
        tasks = (*value.tasks, replace(value.tasks[0], id="other", role_id="另一稀缺岗"),
                 replace(value.tasks[0], id="only", role_id="唯一资质岗"))
        self.assertEqual((("唯一资质岗",), tuple(sorted(("稀缺岗", "另一稀缺岗")))),
                         scarce_role_groups(replace(value, tasks=tasks, members=members)))

    def test_automatic_scarce_rotation_uses_previous_archive_when_month_counts_tie(self):
        value = self.scarcity_scenario()
        value = replace(value, tasks=value.tasks[2:], priority_role_ids=())
        result = solve(value, previous_roles={("m0", "稀缺岗"), ("m0", "普通岗")})
        self.assert_safe(value, result)
        self.assertEqual("m1", next(a.member_id for a in result.assignments if a.task_id == "t1"))

    def test_selected_role_spreads_work_before_month_counts(self):
        value = workload_scenario(4, priority=("重点岗",))
        result = solve(value, priority_counts={("m0", "重点岗"): 100, ("m1", "重点岗"): 0})
        self.assert_safe(value, result)
        counts = member_role_flight_counts(value, result.assignments)
        self.assertEqual([2, 2], sorted(counts[m.id, "重点岗"] for m in value.members))

    def test_month_counts_prefer_less_used_member_before_previous_role(self):
        value = workload_scenario(1, priority=("重点岗",))
        result = solve(value, priority_counts={("m0", "重点岗"): 0, ("m1", "重点岗"): 3},
                       previous_roles={("m0", "重点岗")})
        self.assert_safe(value, result)
        self.assertEqual("m0", result.assignments[0].member_id)

    def test_equal_counts_prefer_different_member_from_previous_archive(self):
        value = workload_scenario(1, priority=("重点岗",))
        result = solve(value, priority_counts={("m0", "重点岗"): 2, ("m1", "重点岗"): 2},
                       previous_roles={("m0", "重点岗")})
        self.assert_safe(value, result)
        self.assertEqual("m1", result.assignments[0].member_id)

    def test_priority_month_rotation_precedes_existing_same_flight_handoff(self):
        value = workload_scenario(1, priority=("重点岗",))
        arrival = replace(value.tasks[0], id="arrival", role_id="接机", end=610,
                          service_stage="arrival", locked_member_ids=("m0",))
        boarding = replace(value.tasks[0], start=610, service_stage="boarding")
        value = replace(value, tasks=(arrival, boarding),
                        members=tuple(replace(m, eligible_role_ids=frozenset({"接机", "重点岗"})) for m in value.members))
        result = solve(value, priority_counts={("m0", "重点岗"): 3, ("m1", "重点岗"): 0})
        self.assert_safe(value, result)
        self.assertEqual("m1", next(a.member_id for a in result.assignments if a.task_id == "t0"))

    def test_many_segments_do_not_overweight_one_flight_when_spreading_role(self):
        value = workload_scenario(3, priority=("重点岗",))
        segments = tuple(replace(value.tasks[0], id=f"segment{i}", start=600 + 5 * i, end=605 + 5 * i)
                         for i in range(4))
        value = replace(value, tasks=(*segments, *value.tasks[1:]))
        result = solve(value, priority_counts={("m0", "重点岗"): 0, ("m1", "重点岗"): 1})
        self.assert_safe(value, result)
        counts = member_role_flight_counts(value, result.assignments)
        self.assertEqual([1, 2], sorted(counts[m.id, "重点岗"] for m in value.members))
        self.assertEqual(1, len({a.member_id for a in result.assignments if a.task_id.startswith("segment")}))

    def test_priority_does_not_create_vacancy_grant_qualification_or_bypass_lock(self):
        value = workload_scenario(2, priority=("重点岗",))
        value = replace(value, members=(value.members[0], replace(value.members[1], eligible_role_ids=frozenset())))
        result = solve(value, allow_partial=True, previous_roles={("m0", "重点岗")},
                       priority_counts={("m0", "重点岗"): 100})
        self.assert_safe(value, result)
        self.assertEqual({"m0"}, {a.member_id for a in result.assignments})
        locked = replace(value, members=(value.members[0], replace(value.members[1], eligible_role_ids=frozenset({"重点岗"}))),
                         tasks=tuple(replace(t, locked_member_ids=("m0",)) for t in value.tasks))
        result = solve(locked, priority_counts={("m0", "重点岗"): 100})
        self.assert_safe(locked, result)
        self.assertEqual({"m0"}, {a.member_id for a in result.assignments})

    def test_unselected_or_absent_roles_do_not_change_existing_history_preference(self):
        for selected in ((), ("当天没有的重点岗",)):
            value = workload_scenario(1, priority=selected)
            result = solve(value, previous_roles={("m0", "重点岗")},
                           priority_counts={("m1", "重点岗"): 100})
            self.assert_safe(value, result)
            self.assertEqual("m1", result.assignments[0].member_id)

    def test_priority_history_counts_require_nonnegative_integers(self):
        value = workload_scenario(1, priority=("重点岗",))
        for counts in ({("m0", "重点岗"): -1}, {("m0", "重点岗"): True},
                       {("m0", "重点岗"): 0.5}, {"m0": 1}):
            with self.subTest(counts=counts):
                with self.assertRaises(ValueError):
                    solve(value, priority_counts=counts)

    def test_month_counts_can_be_large_without_overflowing_objective(self):
        value = workload_scenario(1, priority=("重点岗",))
        result = solve(value, priority_counts={("m0", "重点岗"): 10 ** 100, ("m1", "重点岗"): 10 ** 100 + 1})
        self.assert_safe(value, result)
        self.assertEqual("m0", result.assignments[0].member_id)

    def test_realistic_multi_role_model_does_not_overflow_priority_weights(self):
        value = workload_scenario(7, people=15)
        roles = ("接机1", "接机2", "登机主验", "登机核验", "登机秩序", "督导")
        priority = roles[2:]
        tasks = tuple(Task(f"{flight.id}-{role}", flight.id, role, flight.window_start, flight.window_end,
                           1, "fixed", service_stage="arrival" if role.startswith("接机") else "boarding" if role.startswith("登机") else None,
                           timing_mode="phase", concurrent=role == "督导")
                      for flight in value.flights for role in roles)
        value = replace(value, tasks=tasks, priority_role_ids=priority,
                        members=tuple(replace(m, eligible_role_ids=frozenset(roles)) for m in value.members))
        previous = {(m.id, role) for m in value.members for role in roles}
        monthly = {(m.id, role): index for index, m in enumerate(value.members) for role in priority}
        result = solve(value, time_limit_seconds=0.1, allow_partial=True,
                       previous_roles=previous, priority_counts=monthly)
        self.assertNotEqual("MODEL_INVALID", result.status, result.diagnostics)


if __name__ == "__main__":
    unittest.main()
