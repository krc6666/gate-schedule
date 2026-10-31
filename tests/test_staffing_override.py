import copy
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from gate_schedule.web import create_app
from tests.test_excel_io import day_20260814_bytes


class StaffingOverrideTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "schedule.sqlite3"
        self.client = TestClient(create_app(database_path=self.database))
        preview = self.client.post("/api/import-excel?serviceDate=2026-08-14", content=day_20260814_bytes()).json()
        self.cells = [cell for cell in preview["cells"] if cell["roleName"] == "接机1"]
        self.client.post("/api/imported-day/confirm", json={"cells": [], "roleConfigs": [{"roleName": "接机1", "requiredCount": 2}]})

    def tearDown(self):
        self.client.close()
        self.directory.cleanup()

    def edit(self, value):
        cell = self.cells[1]
        return self.client.post("/api/imported-day/confirm", json={"cells": [{
            "id": cell["id"], "rawValue": cell["rawValue"], "requiredCountOverride": value,
        }]})

    def test_override_zero_then_clear_returns_to_role_default(self):
        saved = self.edit(0)
        self.assertEqual(200, saved.status_code)
        cells = [cell for cell in saved.json()["cells"] if cell["roleName"] == "接机1"]
        self.assertEqual([2, 0], [cell["effectiveRequiredCount"] for cell in cells])
        self.assertEqual(self.cells[1]["internalNames"], cells[1]["internalNames"])
        cleared = self.edit(None)
        cells = [cell for cell in cleared.json()["cells"] if cell["roleName"] == "接机1"]
        self.assertEqual([2, 2], [cell["effectiveRequiredCount"] for cell in cells])

    def test_override_survives_restart_backup_excel_and_does_not_inherit_to_new_day(self):
        self.assertEqual(200, self.edit(3).status_code)
        restarted = TestClient(create_app(database_path=self.database))
        restored = TestClient(create_app(database_path=Path(self.directory.name) / "restored.sqlite3"))
        try:
            saved = restarted.get("/api/imported-day").json()
            self.assertEqual(3, next(cell for cell in saved["cells"] if cell["id"] == self.cells[1]["id"])["effectiveRequiredCount"])
            self.assertEqual(200, restored.post("/api/restore-backup", json=self.client.get("/api/backup").json()).status_code)
            self.assertEqual(saved, restored.get("/api/imported-day").json())
            exported = self.client.get("/api/export-excel").content
            self.assertIn("岗位人数例外", load_workbook(BytesIO(exported)).sheetnames)
            preview = restored.post("/api/import-excel?serviceDate=2026-08-14", content=exported).json()
            self.assertEqual(saved["cells"], preview["cells"])
            preview = self.client.post("/api/import-excel?serviceDate=2026-08-15", content=day_20260814_bytes()).json()
            self.assertEqual([2, 2], [cell["effectiveRequiredCount"] for cell in preview["cells"] if cell["roleName"] == "接机1"])
        finally:
            restarted.close()
            restored.close()

    def test_bad_override_or_tampered_backup_never_replaces_valid_day(self):
        self.assertEqual(200, self.edit(1).status_code)
        saved = self.client.get("/api/imported-day").json()
        for value in (-1, True, 1.5, "3"):
            with self.subTest(value=value):
                self.assertEqual(422, self.edit(value).status_code)
                backup = copy.deepcopy(self.client.get("/api/backup").json())
                cell = next(cell for cell in backup["state"]["importedDay"]["cells"] if cell["id"] == self.cells[1]["id"])
                cell["requiredCountOverride"] = value
                self.assertEqual(422, self.client.post("/api/restore-backup", json=backup).status_code)
                self.assertEqual(saved, self.client.get("/api/imported-day").json())


if __name__ == "__main__":
    unittest.main()
