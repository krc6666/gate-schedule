import unittest
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from gate_schedule.web import create_app


ROOT = Path(__file__).resolve().parents[1]
SCENARIO_PATH = ROOT / "examples" / "phase-0-scenario.json"
ASSIGNMENTS_PATH = ROOT / "examples" / "phase-1-assignments.json"


class ManualScheduleApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.client = TestClient(
            create_app(SCENARIO_PATH, ASSIGNMENTS_PATH, Path(self.directory.name) / "schedule.sqlite3")
        )

    def tearDown(self):
        self.client.close()
        self.directory.cleanup()

    def test_rejects_rotation_candidate_outside_pool_without_changing_saved_schedule(self):
        original = self.client.get("/api/schedule").json()
        changed = [
            {"taskId": item["taskId"], "memberId": "receiver" if item["taskId"] == "cx-baggage-2" else item["memberId"]}
            for item in original["assignments"]
        ]

        response = self.client.post("/api/manual-assignments", json={"assignments": changed})
        saved = self.client.get("/api/schedule").json()

        self.assertEqual(422, response.status_code)
        self.assertFalse(response.json()["valid"])
        self.assertIn("rotation_candidate", {item["code"] for item in response.json()["issues"]})
        self.assertEqual(original["assignments"], saved["assignments"])

    def test_ke_receive_can_be_saved_and_receiver_path_has_two_non_overlapping_tasks(self):
        schedule = self.client.get("/api/schedule").json()

        response = self.client.post("/api/manual-assignments", json={"assignments": schedule["assignments"]})

        self.assertEqual(200, response.status_code)
        receiver_path = response.json()["memberPaths"]["receiver"]
        self.assertEqual(["cx-receive", "ke-receive"], [item["taskId"] for item in receiver_path])


if __name__ == "__main__":
    unittest.main()
