from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from gate_schedule.domain import Assignment, Flight, Member, Rotation, Scenario, Task
from gate_schedule.manual_editing import candidate_options, replace_slot
from gate_schedule.validator import validate_schedule
from gate_schedule.workbench import Workbench
from test_work_phases import phase_day


def task(identity, role, *, flight="A", start=610, end=690, stage=None,
         concurrent=False, timing="phase", count=1, kind="fixed", locked=()):
    return Task(identity, flight, role, start, end, count, kind,
                locked_member_ids=locked, service_stage=stage,
                timing_mode=timing, concurrent=concurrent)


def scenario(*tasks, **changes):
    roles = frozenset(t.role_id for t in tasks)
    value = Scenario(
        "2026-08-02",
        (Flight("A", "CX937", "2026-08-02", 610, 690),
         Flight("B", "KE0166", "2026-08-02", 660, 735)),
        (Member("甲", "甲", roles, 540, 1560),
         Member("乙", "乙", roles, 540, 1560)),
        tuple(tasks), release_policy="task")
    return replace(value, **changes)


class CandidateAvailabilityTests(unittest.TestCase):
    def option(self, value, assignments, task_id, member="甲", slot=0):
        before = tuple(assignments)
        option = next(c for c in candidate_options(value, assignments, task_id, slot)
                      if c["id"] == member)
        self.assertEqual(before, tuple(assignments), "查询候选不得修改原安排")
        return option

    def assert_legal(self, value, assignments, task_id, option, slot=0):
        trial = replace_slot(value, assignments, task_id, slot, option["id"])
        self.assertEqual([], [i for i in validate_schedule(value, trial)
                              if i.code != "coverage_shortfall"])

    def test_same_flight_reception_can_continue_but_is_not_unoccupied(self):
        value = scenario(task("arrival", "接机1", stage="arrival"),
                         task("boarding", "登机主验", stage="boarding"))
        draft = (Assignment("arrival", "甲"),)
        option = self.option(value, draft, "boarding")
        self.assertTrue(option["available"])
        self.assertEqual(("continuation", "可接续"), (option["status"], option["label"]))
        self.assert_legal(value, draft, "boarding", option)
        spare = self.option(value, draft, "boarding", "乙")
        self.assertEqual(("available", "可安排"), (spare["status"], spare["label"]))

    def test_other_ordinary_boarding_work_overrides_reception_continuation(self):
        value = scenario(task("arrival", "接机1", stage="arrival"),
                         task("main", "登机主验", stage="boarding"),
                         task("check", "登机核验", stage="boarding"))
        draft = (Assignment("arrival", "甲"), Assignment("main", "甲"))
        self.assert_legal(value, draft, "check", self.option(value, draft, "check", "乙"))
        option = self.option(value, draft, "check")
        self.assertFalse(option["available"])
        self.assertEqual(("blocked", "不可安排"), (option["status"], option["label"]))
        self.assertIn("overlap", {i["code"] for i in option["issues"]})
        conflict = next(i for i in option["issues"] if i["code"] == "overlap")
        self.assertEqual({"main", "check"}, set(conflict["relatedTaskIds"]))
        self.assertIn("CX937", conflict["message"])

    def test_supervisor_concurrency_follows_configured_switch(self):
        supervisor = task("supervisor", "督导", concurrent=True)
        boarding = task("boarding", "登机主验", stage="boarding")
        value = scenario(supervisor, boarding)
        draft = (Assignment("supervisor", "甲"),)
        option = self.option(value, draft, "boarding")
        self.assertEqual((True, "concurrent", "可兼任"),
                         (option["available"], option["status"], option["label"]))
        self.assert_legal(value, draft, "boarding", option)
        blocked = self.option(replace(value, tasks=(replace(supervisor, concurrent=False), boarding)),
                              draft, "boarding")
        self.assertFalse(blocked["available"])
        self.assertEqual("blocked", blocked["status"])

    def test_cross_flight_handoff_requires_confirmation_and_proven_walking_time(self):
        value = scenario(task("arrival", "接机1", stage="arrival"),
                         task("boarding", "登机主验", flight="B", start=660, end=735,
                              stage="boarding"))
        draft = (Assignment("arrival", "甲"),)
        unconfirmed = self.option(value, draft, "boarding")
        self.assertEqual("blocked", unconfirmed["status"])
        self.assertIn("phase_handoff_unconfirmed", {i["code"] for i in unconfirmed["issues"]})
        reversed_handoff = self.option(replace(value, phase_handoffs=(("B", "A"),)), draft, "boarding")
        self.assertEqual((False, "blocked"), (reversed_handoff["available"], reversed_handoff["status"]))
        self.assertIn("phase_handoff_unconfirmed", {i["code"] for i in reversed_handoff["issues"]})
        confirmed = replace(value, phase_handoffs=(("A", "B"),))
        option = self.option(confirmed, draft, "boarding")
        self.assertEqual((True, "continuation"), (option["available"], option["status"]))
        self.assert_legal(confirmed, draft, "boarding", option)
        walking = self.option(replace(confirmed, minimum_transition_minutes=5), draft, "boarding")
        self.assertFalse(walking["available"])
        self.assertEqual("blocked", walking["status"])
        self.assertIn("phase_transition_unknown", {i["code"] for i in walking["issues"]})

    def test_actual_time_handoff_checks_gap_between_personal_tasks(self):
        value = scenario(task("arrival", "接机1", start=610, end=630, stage="arrival", timing="exact"),
                         task("boarding", "登机主验", flight="B", start=660, end=690,
                              stage="boarding", timing="exact"), minimum_transition_minutes=30)
        draft = (Assignment("arrival", "甲"),)
        option = self.option(value, draft, "boarding")
        self.assertEqual((True, "continuation"), (option["available"], option["status"]))
        self.assert_legal(value, draft, "boarding", option)
        blocked = self.option(replace(value, minimum_transition_minutes=31), draft, "boarding")
        self.assertFalse(blocked["available"])
        self.assertIn("insufficient_transition", {i["code"] for i in blocked["issues"]})

    def test_later_reception_is_not_labelled_continuation_for_earlier_boarding(self):
        value = scenario(task("arrival", "接机1", flight="B", start=1080, end=1140,
                              stage="arrival", timing="exact"),
                         task("boarding", "登机主验", start=610, end=630,
                              stage="boarding", timing="exact"))
        value = replace(value, flights=(value.flights[0], replace(value.flights[1], window_start=1080, window_end=1140)))
        draft = (Assignment("arrival", "甲"),)
        option = self.option(value, draft, "boarding")
        self.assertTrue(option["available"], "前后不重叠的合法安排仍然可选")
        self.assertEqual(("available", "可安排"), (option["status"], option["label"]))
        self.assert_legal(value, draft, "boarding", option)

    def test_explicit_0910_occupancy_blocks_handoff_during_its_actual_interval(self):
        value = scenario(task("arrival", "接机1", end=630, stage="arrival", timing="exact"),
                         task("boarding", "登机主验", start=660, stage="boarding", timing="exact"),
                         task("support", "额外占用：0910", flight=None, start=660, end=670,
                              timing="exact", locked=("甲",)))
        draft = (Assignment("arrival", "甲"), Assignment("support", "甲"))
        option = self.option(value, draft, "boarding")
        self.assertEqual((False, "blocked"), (option["available"], option["status"]))
        self.assertIn("0910", "；".join(option["reasons"]))
        self.assertIn("overlap", {i["code"] for i in option["issues"]})
        self.assert_legal(value, draft, "boarding", self.option(value, draft, "boarding", "乙"))

    def test_attendance_and_qualification_are_checked_before_positive_labels(self):
        value = scenario(task("arrival", "接机1", stage="arrival"),
                         task("boarding", "登机主验", stage="boarding"))
        draft = (Assignment("arrival", "甲"),)
        for member, code in ((replace(value.members[0], available_from=660), "member_unavailable"),
                             (replace(value.members[0], available_until=660), "member_unavailable"),
                             (replace(value.members[0], eligible_role_ids=frozenset({"接机1"})), "ineligible")):
            with self.subTest(code=code, member=member):
                option = self.option(replace(value, members=(member, value.members[1])), draft, "boarding")
                self.assertEqual((False, "blocked"), (option["available"], option["status"]))
                self.assertIn(code, {i["code"] for i in option["issues"]})

    def test_fixed_member_and_rotation_candidate_pool_remain_authoritative(self):
        value = scenario(task("fixed", "登机主验", locked=("甲",)))
        valid = self.option(value, (), "fixed")
        self.assert_legal(value, (), "fixed", valid)
        fixed = self.option(value, (), "fixed", "乙")
        self.assertEqual((False, "blocked"), (fixed["available"], fixed["status"]))
        self.assertIn("lock_missing", {i["code"] for i in fixed["issues"]})
        rotation_task = task("rotation", "大件", kind="rotation", timing="exact")
        rotated = scenario(rotation_task, rotations=(Rotation("rotation", "group", 610, 690, 1, ("甲",)),))
        self.assert_legal(rotated, (), "rotation", self.option(rotated, (), "rotation"))
        outside = self.option(rotated, (), "rotation", "乙")
        self.assertEqual((False, "blocked"), (outside["available"], outside["status"]))
        self.assertIn("rotation_candidate", {i["code"] for i in outside["issues"]})

    def test_assigned_label_preserves_slot_and_partner_legality(self):
        value = scenario(task("pair", "接机1", count=2, stage="arrival"))
        draft = (Assignment("pair", "甲"), Assignment("pair", "乙"))
        current = self.option(value, draft, "pair")
        partner = self.option(value, draft, "pair", "乙")
        self.assertEqual((True, "assigned", "已在本岗"),
                         (current["available"], current["status"], current["label"]))
        self.assertEqual((False, "assigned", "已在本岗"),
                         (partner["available"], partner["status"], partner["label"]))
        self.assertIn("不能重复选择", "；".join(partner["reasons"]))

    def test_unrelated_existing_conflict_keeps_existing_filter_contract(self):
        value = scenario(task("one", "主验"), task("two", "核验"),
                         task("target", "督导", flight="B", start=720, end=735, timing="exact"))
        draft = (Assignment("one", "甲"), Assignment("two", "甲"))
        unrelated = self.option(value, draft, "target", "乙")
        self.assertTrue(unrelated["available"])
        self.assertEqual("available", unrelated["status"])
        involved = self.option(value, draft, "target")
        self.assertFalse(involved["available"])
        self.assertEqual("blocked", involved["status"])


class WorkbenchCandidateAvailabilityTests(unittest.TestCase):
    def setUp(self):
        temporary_root = Path(__file__).resolve().parents[1] / ".tmp"
        temporary_root.mkdir(exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(prefix="candidate-availability-", dir=temporary_root)
        self.path = Path(self.directory.name) / "state.sqlite3"
        self.workbench = Workbench(self.path)

    def tearDown(self):
        self.directory.cleanup()

    def command(self, action, payload, plan_id=None):
        return self.workbench.execute(self.workbench.snapshot()["revision"], action, payload, plan_id)

    def test_real_entry_shares_status_revision_and_absence_without_writing(self):
        day = phase_day()
        day["memberAvailability"][1]["participating"] = False
        state = self.command("import-complete-configuration", {
            "day": day, "rotationMinutes": 60, "rotationEnabled": True, "source": "候选测试"})
        plan_id = state["selectedPlanId"]
        state = self.command("edit-cell", {"cellId": "接机1", "text": "甲"}, plan_id)
        before = self.workbench.backup()
        options = {c["name"]: c for c in self.workbench.candidates(plan_id, "imported-登机主验")}
        self.assertEqual((True, "continuation"), (options["甲"]["available"], options["甲"]["status"]))
        self.assertEqual((False, "blocked", "不可安排"),
                         (options["乙"]["available"], options["乙"]["status"], options["乙"]["label"]))
        self.assertIn("未参加", "；".join(options["乙"]["reasons"]))
        self.assertTrue(all(c["revision"] == state["revision"] for c in options.values()))
        self.assertEqual(before, self.workbench.backup())
        state = self.command("edit-cell", {"cellId": "登机主验", "text": "甲"}, plan_id)
        options = {c["name"]: c for c in self.workbench.candidates(plan_id, "imported-登机核验")}
        self.assertEqual((False, "blocked"), (options["甲"]["available"], options["甲"]["status"]))
        self.assertTrue(all(c["revision"] == state["revision"] for c in options.values()))
        restarted = Workbench(self.path)
        self.assertEqual(options, {c["name"]: c for c in restarted.candidates(plan_id, "imported-登机核验")})


if __name__ == "__main__":
    unittest.main()
