import tempfile
import unittest
from dataclasses import replace
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from gate_schedule.excel_io import export_imported_day, import_day, update_imported_day, validate_imported_day
from gate_schedule.web import create_app
from tests.test_excel_io import day_20260814_bytes


class FixedMemberTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "schedule.sqlite3"
        self.client = TestClient(create_app(database_path=self.database))

    def tearDown(self):
        self.client.close()
        self.directory.cleanup()

    def save_day(self):
        day = self.client.post("/api/import-excel?serviceDate=2026-08-14", content=day_20260814_bytes()).json()
        cell = next(item for item in day["cells"] if item["roleName"] == "接机1" and item["internalNames"])
        return cell

    def test_fixed_names_are_explicit_and_survive_save_restart_and_backup(self):
        cell = self.save_day()
        fixed = [{"id": cell["id"], "rawValue": cell["rawValue"], "fixedMemberNames": [cell["internalNames"][0]]}]
        response = self.client.post("/api/imported-day/confirm", json={"cells": fixed})
        self.assertEqual(200, response.status_code, response.text)
        saved = response.json()
        saved_cell = next(item for item in saved["cells"] if item["id"] == cell["id"])
        self.assertEqual([cell["internalNames"][0]], saved_cell["fixedMemberNames"])
        restarted = TestClient(create_app(database_path=self.database))
        try:
            self.assertEqual(saved, restarted.get("/api/imported-day").json())
            backup = self.client.get("/api/backup").json()
            restored = TestClient(create_app(database_path=Path(self.directory.name) / "restored.sqlite3"))
            try:
                self.assertEqual(200, restored.post("/api/restore-backup", json=backup).status_code)
                self.assertEqual(saved, restored.get("/api/imported-day").json())
            finally:
                restored.close()
        finally:
            restarted.close()

    def test_fixed_names_round_trip_excel_and_old_workbook_is_empty(self):
        day = import_day(day_20260814_bytes(), "2026-08-14")
        cell = next(item for item in day.cells if item.role_name == "接机1" and item.internal_names)
        fixed_day = update_imported_day(day, [{"id": cell.id, "rawValue": cell.raw_value, "fixedMemberNames": [cell.internal_names[0]]}])
        exported = export_imported_day(fixed_day)
        workbook = load_workbook(BytesIO(exported))
        self.assertIn("固定人员", workbook.sheetnames)
        restored = import_day(exported, "2026-08-14")
        restored_cell = next(item for item in restored.cells if item.id == cell.id)
        self.assertEqual((cell.internal_names[0],), restored_cell.fixed_member_names)
        self.assertEqual((), cell.fixed_member_names)

    def test_fixed_name_must_be_in_cell_and_duplicates_are_rejected(self):
        day = self.client.post("/api/import-excel?serviceDate=2026-08-14", content=day_20260814_bytes()).json()
        cell = next(item for item in day["cells"] if item["roleName"] == "接机1" and item["internalNames"])
        other = next(name for item in day["cells"] for name in item["internalNames"] if name not in cell["internalNames"])
        for names, code in ((["未知人员"], "unknown_fixed_member"), ([cell["internalNames"][0]] * 2, "duplicate_fixed_member"), (["值机支援"], "unknown_fixed_member"), ([other], "fixed_member_not_in_cell")):
            response = self.client.post("/api/imported-day/confirm", json={"cells": [{"id": cell["id"], "rawValue": cell["rawValue"], "fixedMemberNames": names}]})
            self.assertEqual(422, response.status_code)
            self.assertIn(code, {issue["code"] for issue in response.json()["issues"]})
        legal = import_day(day_20260814_bytes(), "2026-08-14")
        self.assertEqual([], validate_imported_day(legal))
        tampered = replace(legal.cells[0], fixed_member_names=("未知人员",))
        self.assertIn("unknown_fixed_member", {issue.code for issue in validate_imported_day(replace(legal, cells=(tampered,) + legal.cells[1:]))})

    def test_extra_occupancy_and_transition_settings_round_trip_excel(self):
        from gate_schedule.domain import ImportedOccupancy

        day = import_day(day_20260814_bytes(), "2026-08-14")
        name = next(name for cell in day.cells for name in cell.internal_names)
        configured = replace(
            day,
            extra_occupancies=(ImportedOccupancy("o1", "登机准备", (name, "刘楠"), "10:00", "10:20"),),
            remarks_reviewed=False,
            transition_minutes=12,
        )
        exported = export_imported_day(configured)
        restored = import_day(exported, "2026-08-14")
        self.assertEqual(configured.extra_occupancies, restored.extra_occupancies)
        self.assertEqual(12, restored.transition_minutes)
        self.assertFalse(restored.remarks_reviewed)


if __name__ == "__main__":
    unittest.main()
