import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from gate_schedule.domain import Assignment
from gate_schedule.io import scenario_from_dict
from gate_schedule.solver import solve
from gate_schedule.validator import validate_scenario, validate_schedule
from gate_schedule.web import create_app


def handoff_payload():
    return {
        "serviceDate": "2026-10-28",
        "flights": [
            {"id": "A", "flightNo": "A", "windowStart": "08:00", "windowEnd": "10:00"},
            {"id": "B", "flightNo": "B", "windowStart": "09:00", "windowEnd": "11:00"},
        ],
        "members": [
            {
                "id": "a-arrival",
                "name": "A接机",
                "eligibleRoleIds": ["arrival", "boarding"],
                "availableFrom": "09:00",
            },
            {
                "id": "b-boarder",
                "name": "B登机",
                "eligibleRoleIds": ["boarding"],
                "availableFrom": "09:30",
                "availableUntil": "10:00",
            },
        ],
        "tasks": [
            {
                "id": "a-arrival-task",
                "flightId": "A",
                "roleId": "arrival",
                "start": "09:00",
                "end": "09:30",
                "requiredCount": 1,
                "taskKind": "mobile",
                "serviceStage": "arrival",
            },
            {
                "id": "b-boarding-task",
                "flightId": "B",
                "roleId": "boarding",
                "start": "09:30",
                "end": "11:00",
                "requiredCount": 1,
                "taskKind": "mobile",
                "serviceStage": "boarding",
            },
        ],
    }


class FlightHandoffTests(unittest.TestCase):
    def test_splits_boarding_task_at_earlier_arrival_flight_end(self):
        scenario = scenario_from_dict(handoff_payload())

        boarding_tasks = sorted(
            (task for task in scenario.tasks if task.service_stage == "boarding"),
            key=lambda task: task.start,
        )

        self.assertEqual([(9 * 60 + 30, 10 * 60), (10 * 60, 11 * 60)], [(task.start, task.end) for task in boarding_tasks])
        self.assertEqual(2, len({task.id for task in boarding_tasks}))

    def test_splits_rotation_boarding_and_keeps_rotation_segments_valid(self):
        payload = handoff_payload()
        payload["tasks"][1]["taskKind"] = "rotation"
        payload["rotations"] = [
            {
                "taskId": "b-boarding-task",
                "groupId": "b-boarding",
                "segmentStart": "09:30",
                "segmentEnd": "11:00",
                "requiredCount": 1,
                "candidateMemberIds": ["a-arrival", "b-boarder"],
            }
        ]

        scenario = scenario_from_dict(payload)

        self.assertEqual([], validate_scenario(scenario))
        self.assertEqual(
            [(9 * 60 + 30, 10 * 60), (10 * 60, 11 * 60)],
            [(rotation.segment_start, rotation.segment_end) for rotation in scenario.rotations],
        )

    def test_rejects_invalid_service_stage_value(self):
        payload = handoff_payload()
        payload["tasks"][0]["serviceStage"] = ["arrival"]

        scenario = scenario_from_dict(payload)

        self.assertIn("invalid_task_stage", {issue.code for issue in validate_scenario(scenario)})

    def test_solver_uses_arrival_staff_after_source_flight_ends(self):
        scenario = scenario_from_dict(handoff_payload())

        result = solve(scenario)

        self.assertIn(result.status, {"OPTIMAL", "FEASIBLE"})
        boarding_tasks = sorted(
            (task for task in scenario.tasks if task.service_stage == "boarding"),
            key=lambda task: task.start,
        )
        assignments = {item.task_id: item.member_id for item in result.assignments}
        self.assertEqual("b-boarder", assignments[boarding_tasks[0].id])
        self.assertEqual("a-arrival", assignments[boarding_tasks[1].id])
        self.assertEqual([], validate_schedule(scenario, result.assignments))

    def test_validator_rejects_cross_flight_assignment_before_source_flight_end(self):
        scenario = scenario_from_dict(handoff_payload())
        legal_result = solve(scenario)
        self.assertIn(legal_result.status, {"OPTIMAL", "FEASIBLE"})
        self.assertEqual([], validate_schedule(scenario, legal_result.assignments))
        boarding_tasks = sorted(
            (task for task in scenario.tasks if task.service_stage == "boarding"),
            key=lambda task: task.start,
        )
        assignments = tuple(
            Assignment(item.task_id, "a-arrival") if item.task_id == boarding_tasks[0].id else item
            for item in legal_result.assignments
        )

        issues = validate_schedule(scenario, assignments)

        self.assertIn("cross_flight_before_release", {issue.code for issue in issues})

    def test_solver_prefers_same_flight_arrival_staff_for_boarding(self):
        scenario = scenario_from_dict(
            {
                "serviceDate": "2026-10-28",
                "flights": [{"id": "A", "flightNo": "A", "windowStart": "08:00", "windowEnd": "11:00"}],
                "members": [
                    {"id": "same-flight", "name": "同航班接机", "eligibleRoleIds": ["arrival", "boarding"], "availableFrom": "08:00"},
                    {"id": "other", "name": "其他登机人员", "eligibleRoleIds": ["boarding"], "availableFrom": "08:00"},
                ],
                "tasks": [
                    {
                        "id": "arrival",
                        "flightId": "A",
                        "roleId": "arrival",
                        "start": "08:00",
                        "end": "08:30",
                        "requiredCount": 1,
                        "taskKind": "mobile",
                        "serviceStage": "arrival",
                    },
                    {
                        "id": "boarding",
                        "flightId": "A",
                        "roleId": "boarding",
                        "start": "08:30",
                        "end": "09:00",
                        "requiredCount": 1,
                        "taskKind": "mobile",
                        "serviceStage": "boarding",
                    },
                ],
            }
        )

        result = solve(scenario)

        self.assertEqual("same-flight", next(item.member_id for item in result.assignments if item.task_id == "boarding"))

    def test_api_keeps_generated_boarding_segments_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scenario_path = root / "scenario.json"
            assignments_path = root / "assignments.json"
            database_path = root / "schedule.sqlite3"
            scenario_path.write_text(json.dumps(handoff_payload(), ensure_ascii=False), encoding="utf-8")
            assignments_path.write_text(
                json.dumps(
                    {
                        "assignments": [
                            {"taskId": "a-arrival-task", "memberId": "a-arrival"},
                            {"taskId": "b-boarding-task::segment-1", "memberId": "b-boarder"},
                            {"taskId": "b-boarding-task::segment-2", "memberId": "a-arrival"},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with TestClient(create_app(scenario_path, assignments_path, database_path)) as client:
                response = client.post("/api/solve")
                self.assertTrue(response.json()["accepted"])
                self.assertEqual(200, client.post("/api/solve/confirm", json={"previewId": response.json()["previewId"]}).status_code)
                self.assertEqual(2, len([task for task in response.json()["tasks"] if task["serviceStage"] == "boarding"]))

            with TestClient(create_app(scenario_path, assignments_path, database_path)) as restarted_client:
                schedule = restarted_client.get("/api/schedule").json()
                self.assertEqual("automatic", schedule["assignmentSource"])
                self.assertEqual(2, len([task for task in schedule["tasks"] if task["serviceStage"] == "boarding"]))
                self.assertEqual(
                    [],
                    validate_schedule(
                        scenario_from_dict(handoff_payload()),
                        tuple(Assignment(item["taskId"], item["memberId"]) for item in schedule["assignments"]),
                    ),
                )


if __name__ == "__main__":
    unittest.main()
