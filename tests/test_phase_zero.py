import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from gate_schedule.domain import Assignment, ExternalSupportRequirement, Flight, Member, Rotation, Scenario, Task
from gate_schedule.projections import member_paths, task_matrix
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule


def member(member_id, roles, available_from=8 * 60):
    return Member(
        id=member_id,
        name=member_id,
        eligible_role_ids=frozenset(roles),
        available_from=available_from,
    )


def task(
    task_id,
    flight_id,
    role_id,
    start,
    end,
    *,
    kind="mobile",
    locked=(),
):
    return Task(
        id=task_id,
        flight_id=flight_id,
        role_id=role_id,
        start=start,
        end=end,
        required_count=1,
        task_kind=kind,
        locked_member_ids=tuple(locked),
    )


def feasible_scenario():
    return Scenario(
        service_date="2026-08-02",
        flights=(
            Flight("CX937", "CX937", "2026-08-02", 8 * 60, 10 * 60 + 30),
            Flight("KE0166", "KE0166", "2026-08-02", 10 * 60, 10 * 60 + 30),
        ),
        members=(
            member("supervisor", {"supervisor"}),
            member("receiver", {"receive"}),
            member("baggage", {"baggage", "support"}),
            member("baggage-alternate", {"baggage"}),
            member("main", {"main"}),
            member("verifier", {"verify"}),
            member("support", {"support"}),
            member("id-checker", {"id-check"}),
            member("late", {"receive"}, available_from=9 * 60),
        ),
        tasks=(
            task("cx-supervisor", "CX937", "supervisor", 8 * 60, 10 * 60, kind="fixed", locked=("supervisor",)),
            task("cx-receive", "CX937", "receive", 8 * 60, 8 * 60 + 30),
            task("cx-baggage", "CX937", "baggage", 8 * 60 + 15, 8 * 60 + 45),
            task("cx-main", "CX937", "main", 8 * 60 + 30, 9 * 60 + 30, kind="fixed", locked=("main",)),
            task("cx-verify", "CX937", "verify", 8 * 60 + 30, 9 * 60 + 30, kind="fixed", locked=("verifier",)),
            task("cx-baggage-1", "CX937", "baggage", 9 * 60, 9 * 60 + 20, kind="rotation"),
            task("cx-baggage-2", "CX937", "baggage", 9 * 60 + 20, 9 * 60 + 40, kind="rotation"),
            task("cx-baggage-3", "CX937", "baggage", 9 * 60 + 40, 10 * 60, kind="rotation"),
            Task("cx-id-check", "CX937", "id-check", 9 * 60, 10 * 60, 1, "mobile"),
            # Test fixture only: the real 0910 support duration remains owner-confirmed.
            task("support-0910", None, "support", 9 * 60 + 10, 9 * 60 + 40, kind="support", locked=("support",)),
            task("ke-receive", "KE0166", "receive", 10 * 60, 10 * 60 + 30, locked=("receiver",)),
        ),
        rotations=(
            Rotation("cx-baggage-1", "cx-baggage", 9 * 60, 9 * 60 + 20, 1, ("baggage",)),
            Rotation("cx-baggage-2", "cx-baggage", 9 * 60 + 20, 9 * 60 + 40, 1, ("baggage",)),
            Rotation("cx-baggage-3", "cx-baggage", 9 * 60 + 40, 10 * 60, 1, ("baggage",)),
        ),
        external_support_requirements=(ExternalSupportRequirement("cx-id-check", 1, "值机支援"),),
    )


class PhaseZeroTests(unittest.TestCase):
    def test_solves_two_flights_and_projects_matrix_and_member_paths(self):
        result = solve(feasible_scenario(), time_limit_seconds=2)

        self.assertIn(result.status, {"OPTIMAL", "FEASIBLE"})
        self.assertEqual([], validate_schedule(feasible_scenario(), result.assignments))
        self.assertIn("CX937", task_matrix(feasible_scenario(), result.assignments)["supervisor"])
        self.assertIn("receiver", member_paths(feasible_scenario(), result.assignments))

    def test_allows_one_member_to_complete_non_overlapping_tasks_in_sequence(self):
        scenario = feasible_scenario()
        result = solve(scenario)

        receiver_tasks = [item.task_id for item in result.assignments if item.member_id == "receiver"]
        self.assertEqual(["cx-receive", "ke-receive"], receiver_tasks)

    def test_keeps_fixed_post_exactly_locked(self):
        result = solve(feasible_scenario())

        self.assertIn(Assignment("cx-verify", "verifier"), result.assignments)
        self.assertIn(Assignment("cx-main", "main"), result.assignments)

    def test_rejects_overlapping_assignments_and_late_arrival(self):
        scenario = feasible_scenario()
        invalid = (
            Assignment("cx-receive", "late"),
            Assignment("support-0910", "baggage"),
            Assignment("cx-baggage-2", "baggage"),
        )

        codes = {issue.code for issue in validate_schedule(scenario, invalid)}
        self.assertIn("member_unavailable", codes)
        self.assertIn("overlap", codes)

    def test_rotation_segments_are_continuous_and_fully_covered(self):
        result = solve(feasible_scenario())

        self.assertEqual([], validate_schedule(feasible_scenario(), result.assignments))
        rotation_assignments = [item for item in result.assignments if item.task_id.startswith("cx-baggage-")]
        self.assertEqual(3, len(rotation_assignments))

    def test_rejects_rotation_task_without_explicit_rotation_definition(self):
        scenario = feasible_scenario()
        missing_rotation = Scenario(
            service_date=scenario.service_date,
            flights=scenario.flights,
            members=scenario.members,
            tasks=scenario.tasks,
            rotations=(),
            external_support_requirements=scenario.external_support_requirements,
        )

        result = solve(missing_rotation)

        self.assertEqual("MODEL_INVALID", result.status)
        self.assertTrue(any("轮换" in message for message in result.diagnostics))

    def test_support_occupation_conflict_is_rejected(self):
        scenario = feasible_scenario()
        invalid = (
            Assignment("support-0910", "baggage"),
            Assignment("cx-baggage-2", "baggage"),
        )

        self.assertIn("overlap", {issue.code for issue in validate_schedule(scenario, invalid)})

    def test_external_support_is_not_an_internal_assignment_or_workload(self):
        result = solve(feasible_scenario())

        id_check = [item for item in result.assignments if item.task_id == "cx-id-check"]
        self.assertEqual([Assignment("cx-id-check", "id-checker")], id_check)
        self.assertEqual(1, result.external_support_by_task["cx-id-check"])
        self.assertNotIn("值机支援", member_paths(feasible_scenario(), result.assignments))

    def test_reports_infeasible_coverage_with_a_readable_gap(self):
        scenario = feasible_scenario()
        impossible = Scenario(
            service_date=scenario.service_date,
            flights=scenario.flights,
            members=scenario.members,
            tasks=scenario.tasks + (task("impossible", "KE0166", "unknown-role", 11 * 60, 12 * 60),),
            rotations=scenario.rotations,
            external_support_requirements=scenario.external_support_requirements,
        )

        result = solve(impossible)

        self.assertEqual("INFEASIBLE", result.status)
        self.assertTrue(any("impossible" in message for message in result.diagnostics))

    def test_independent_validator_rejects_post_solution_tampering(self):
        scenario = feasible_scenario()
        result = solve(scenario)
        tampered = result.assignments + (Assignment("cx-baggage-2", "support"),)

        codes = {issue.code for issue in validate_schedule(scenario, tampered)}
        self.assertIn("overlap", codes)
        self.assertIn("coverage_exceeded", codes)

    def test_independent_validator_rejects_rotation_candidate_pool_tampering(self):
        scenario = feasible_scenario()
        result = solve(scenario)
        tampered = tuple(
            Assignment(item.task_id, "baggage-alternate") if item.task_id == "cx-baggage-2" else item
            for item in result.assignments
        )

        self.assertIn("rotation_candidate", {issue.code for issue in validate_schedule(scenario, tampered)})

    def test_distinguishes_timeout_and_model_invalid(self):
        self.assertEqual("TIMED_OUT", solve(feasible_scenario(), time_limit_seconds=0).status)
        invalid = Scenario(
            service_date="2026-08-02",
            flights=(),
            members=(),
            tasks=(Task("bad", None, "role", 10, 10, 1, "mobile"),),
        )
        self.assertEqual("MODEL_INVALID", solve(invalid).status)

    def test_cli_reads_json_and_keeps_all_rotation_segments_in_matrix(self):
        payload = {
            "serviceDate": "2026-08-02",
            "flights": [{"id": "F1", "flightNo": "CX937", "windowStart": "08:00", "windowEnd": "10:00"}],
            "members": [{"id": "m1", "name": "员工甲", "eligibleRoleIds": ["baggage"], "availableFrom": "08:00"}],
            "tasks": [
                {"id": "r1", "flightId": "F1", "roleId": "baggage", "start": "08:00", "end": "08:20", "requiredCount": 1, "taskKind": "rotation"},
                {"id": "r2", "flightId": "F1", "roleId": "baggage", "start": "08:20", "end": "08:40", "requiredCount": 1, "taskKind": "rotation"},
            ],
            "rotations": [
                {"taskId": "r1", "groupId": "bag", "segmentStart": "08:00", "segmentEnd": "08:20", "requiredCount": 1, "candidateMemberIds": ["m1"]},
                {"taskId": "r2", "groupId": "bag", "segmentStart": "08:20", "segmentEnd": "08:40", "requiredCount": 1, "candidateMemberIds": ["m1"]},
            ],
            "externalSupportRequirements": [
                {"taskId": "r1", "count": 1, "sourceLabel": "值机支援"}
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scenario.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, "-m", "gate_schedule", str(path)],
                check=False,
                capture_output=True,
                encoding="utf-8",
            )

        self.assertEqual(0, completed.returncode, completed.stderr)
        output = json.loads(completed.stdout)
        self.assertEqual("OPTIMAL", output["status"])
        self.assertEqual(2, len(output["taskMatrix"]["baggage"]["CX937"]))

        self.assertEqual("值机支援", output["externalSupportRequirements"][0]["source_label"])


if __name__ == "__main__":
    unittest.main()
