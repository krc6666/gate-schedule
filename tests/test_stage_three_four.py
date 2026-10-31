import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from gate_schedule.web import create_app
from tests.test_excel_io import day_20260814_bytes


ROOT = Path(__file__).resolve().parents[1]
SCENARIO_PATH = ROOT / "examples" / "phase-0-scenario.json"
ASSIGNMENTS_PATH = ROOT / "examples" / "phase-1-assignments.json"


class StageThreeFourTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.directory.name) / "schedule.sqlite3"
        self.client = TestClient(create_app(SCENARIO_PATH, ASSIGNMENTS_PATH, self.database_path))

    def tearDown(self):
        self.client.close()
        self.directory.cleanup()

    def test_automatic_solution_is_independently_validated_and_restored_from_sqlite(self):
        original = self.client.get("/api/schedule").json()
        response = self.client.post("/api/solve")

        self.assertEqual(200, response.status_code)
        self.assertIn(response.json()["status"], {"OPTIMAL", "FEASIBLE"})
        self.assertEqual([], response.json()["validationIssues"])
        self.assertEqual(original, self.client.get("/api/schedule").json())
        confirmed = self.client.post("/api/solve/confirm", json={"previewId": response.json()["previewId"]})
        self.assertEqual(200, confirmed.status_code)
        self.assertEqual("automatic", response.json()["assignmentSource"])

        restored = TestClient(create_app(SCENARIO_PATH, ASSIGNMENTS_PATH, self.database_path)).get("/api/schedule")

        self.assertEqual(200, restored.status_code)
        self.assertEqual("automatic", restored.json()["assignmentSource"])
        self.assertEqual(response.json()["assignments"], restored.json()["assignments"])

    def test_complete_backup_restores_state_and_invalid_backup_does_not_replace_it(self):
        preview = self.client.post("/api/solve").json()
        self.assertEqual(200, self.client.post("/api/solve/confirm", json={"previewId": preview["previewId"]}).status_code)
        original = self.client.get("/api/schedule").json()
        backup = self.client.get("/api/backup")

        self.assertEqual(200, backup.status_code)
        payload = backup.json()
        self.assertEqual(1, payload["formatVersion"])
        self.assertIn("scenario", payload["state"])
        self.assertIn("assignments", payload["state"])

        rejected = self.client.post("/api/restore-backup", json={"formatVersion": 1, "state": {"scenario": {}}})
        self.assertEqual(422, rejected.status_code)
        self.assertEqual(original["assignments"], self.client.get("/api/schedule").json()["assignments"])

        restore_directory = tempfile.TemporaryDirectory()
        try:
            restore_client = TestClient(
                create_app(SCENARIO_PATH, ASSIGNMENTS_PATH, Path(restore_directory.name) / "restored.sqlite3")
            )
            restored = restore_client.post("/api/restore-backup", content=json.dumps(payload), headers={"Content-Type": "application/json"})
            self.assertEqual(200, restored.status_code)
            self.assertEqual("automatic", restored.json()["assignmentSource"])
            self.assertEqual(original["assignments"], restored.json()["assignments"])
        finally:
            restore_client.close()
            restore_directory.cleanup()

    def test_complete_backup_also_restores_the_confirmed_excel_day(self):
        preview = self.client.post(
            "/api/import-excel?serviceDate=2026-08-14",
            content=day_20260814_bytes(),
        )
        self.assertEqual(200, preview.status_code)
        self.assertTrue(preview.json()["valid"])
        self.assertEqual(200, self.client.post("/api/imported-day/confirm", json={"cells": []}).status_code)
        backup = self.client.get("/api/backup").json()

        restore_directory = tempfile.TemporaryDirectory()
        restore_client = TestClient(
            create_app(SCENARIO_PATH, ASSIGNMENTS_PATH, Path(restore_directory.name) / "restored.sqlite3")
        )
        try:
            self.assertEqual(
                200,
                restore_client.post(
                    "/api/restore-backup",
                    content=json.dumps(backup),
                    headers={"Content-Type": "application/json"},
                ).status_code,
            )
            restored_day = restore_client.get("/api/imported-day")
            self.assertEqual(200, restored_day.status_code)
            self.assertEqual("2026-08-14", restored_day.json()["serviceDate"])
            self.assertTrue(restored_day.json()["valid"])
        finally:
            restore_client.close()
            restore_directory.cleanup()

    def test_manual_save_is_recovered_after_rebuilding_the_app(self):
        original = self.client.get("/api/schedule").json()

        saved = self.client.post("/api/manual-assignments", json={"assignments": original["assignments"]})
        restored = TestClient(create_app(SCENARIO_PATH, ASSIGNMENTS_PATH, self.database_path)).get("/api/schedule")

        self.assertEqual(200, saved.status_code)
        self.assertEqual("manual", saved.json()["assignmentSource"])
        self.assertEqual("manual", restored.json()["assignmentSource"])
        self.assertEqual(original["assignments"], restored.json()["assignments"])


if __name__ == "__main__":
    unittest.main()
