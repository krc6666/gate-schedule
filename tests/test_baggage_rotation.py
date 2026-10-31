import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import Workbook

from gate_schedule.domain import BaggageRotationSettings, ImportedFlight
from gate_schedule.projections import baggage_rotation_segments
from gate_schedule.web import create_app


def baggage_flights():
    return (
        ImportedFlight("f1", "CX100", "08:00", "09:00"),
        ImportedFlight("f2", "CX200", "08:30", "10:30"),
        ImportedFlight("f3", "CX300", "11:00", "12:00"),
    )


def workbook_bytes():
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["服务日"])
    sheet.append(["岗位", "CX100 / 0800-0900", "CX200 / 0830-1030", "CX300 / 1100-1200"])
    sheet.append(["大件行李拦截", "甲/乙", "丙", "丁"])
    result = BytesIO()
    workbook.save(result)
    return result.getvalue()


def invalid_window_workbook_bytes():
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["服务日"])
    sheet.append(["岗位", "CX999 / 2560-2700"])
    sheet.append(["大件行李拦截", "甲"])
    result = BytesIO()
    workbook.save(result)
    return result.getvalue()


class BaggageRotationProjectionTests(unittest.TestCase):
    def test_overlapping_flights_merge_and_gaps_remain_unstaffed(self):
        segments = baggage_rotation_segments(baggage_flights(), BaggageRotationSettings())

        self.assertEqual(
            [("08:00", "09:00"), ("09:00", "10:00"), ("10:00", "10:30"), ("11:00", "12:00")],
            [(item.start, item.end) for item in segments],
        )

    def test_rotation_interval_is_adjustable_and_last_segment_can_be_shorter(self):
        segments = baggage_rotation_segments(
            (ImportedFlight("f1", "CX100", "08:00", "10:00"),),
            BaggageRotationSettings(interval_minutes=75),
        )

        self.assertEqual([("08:00", "09:15"), ("09:15", "10:00")], [(item.start, item.end) for item in segments])

    def test_flights_without_a_gap_share_one_continuous_coverage_window(self):
        flights = (
            ImportedFlight("f1", "CX100", "08:00", "09:00"),
            ImportedFlight("f2", "CX200", "09:00", "10:00"),
        )
        segments = baggage_rotation_segments(flights, BaggageRotationSettings(interval_minutes=90))

        self.assertEqual([("08:00", "09:30"), ("09:30", "10:00")], [(item.start, item.end) for item in segments])

    def test_disabled_or_empty_coverage_has_no_segments(self):
        disabled = BaggageRotationSettings(enabled=False)

        self.assertEqual((), baggage_rotation_segments(baggage_flights(), disabled))
        self.assertEqual((), baggage_rotation_segments((), BaggageRotationSettings()))

    def test_rotation_interval_must_fit_in_a_day(self):
        with self.assertRaises(ValueError):
            BaggageRotationSettings(interval_minutes=0)

        with self.assertRaises(ValueError):
            BaggageRotationSettings(interval_minutes=1441)


class BaggageRotationApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.directory.name) / "schedule.sqlite3"
        self.client = TestClient(create_app(database_path=self.database_path))

    def tearDown(self):
        self.client.close()
        self.directory.cleanup()

    def test_import_preview_uses_saved_interval_and_persists_it_across_restart(self):
        self.assertEqual(200, self.client.post("/api/import-excel?serviceDate=2026-08-02", content=workbook_bytes()).status_code)
        settings = self.client.post("/api/baggage-rotation-settings", json={"enabled": True, "intervalMinutes": 90})

        self.assertEqual(200, settings.status_code)
        self.assertEqual(
            [("08:00", "09:30"), ("09:30", "10:30"), ("11:00", "12:00")],
            [(item["start"], item["end"]) for item in settings.json()["segments"]],
        )

        restored = TestClient(create_app(database_path=self.database_path))
        try:
            persisted = restored.get("/api/baggage-rotation-settings")
            self.assertEqual(200, persisted.status_code)
            self.assertEqual(90, persisted.json()["intervalMinutes"])
        finally:
            restored.close()

    def test_rotation_preview_excludes_flights_with_baggage_count_zero(self):
        imported = self.client.post("/api/import-excel?serviceDate=2026-08-02", content=workbook_bytes()).json()
        baggage_cells = [cell for cell in imported["cells"] if cell["roleName"] == "大件行李拦截"]
        updated = self.client.post("/api/imported-day/confirm", json={
            "roleConfigs": [{"roleName": "大件行李拦截", "requiredCount": 1}],
            "cells": [
                {"id": cell["id"], "rawValue": cell["rawValue"], "requiredCountOverride": 0}
                for cell in baggage_cells[1:]
            ],
        })
        self.assertEqual(200, updated.status_code)

        segments = self.client.get("/api/baggage-rotation-settings").json()["segments"]

        self.assertEqual([("08:00", "09:00")], [(item["start"], item["end"]) for item in segments])

    def test_import_rejects_flight_windows_that_cannot_produce_coverage(self):
        response = self.client.post(
            "/api/import-excel?serviceDate=2026-08-02",
            content=invalid_window_workbook_bytes(),
        )

        self.assertEqual(422, response.status_code)
        self.assertIn("起止时间无效", response.json()["detail"])

    def test_disabling_suggestions_does_not_change_imported_day(self):
        self.client.post("/api/import-excel?serviceDate=2026-08-02", content=workbook_bytes())
        self.assertEqual(200, self.client.post("/api/imported-day/confirm", json={"cells": []}).status_code)
        imported = self.client.get("/api/imported-day").json()

        response = self.client.post("/api/baggage-rotation-settings", json={"enabled": False, "intervalMinutes": 60})

        self.assertEqual(200, response.status_code)
        self.assertEqual([], response.json()["segments"])
        self.assertEqual(imported["cells"], self.client.get("/api/imported-day").json()["cells"])

    def test_invalid_settings_and_backup_do_not_replace_valid_settings(self):
        self.assertEqual(200, self.client.post("/api/baggage-rotation-settings", json={"enabled": True, "intervalMinutes": 45}).status_code)

        rejected = self.client.post("/api/baggage-rotation-settings", json={"enabled": True, "intervalMinutes": 0})
        self.assertEqual(422, rejected.status_code)
        self.assertEqual(45, self.client.get("/api/baggage-rotation-settings").json()["intervalMinutes"])

        backup = self.client.get("/api/backup").json()
        backup["state"]["baggageRotationSettings"] = {"enabled": True, "intervalMinutes": 0}
        rejected_backup = self.client.post("/api/restore-backup", content=json.dumps(backup), headers={"Content-Type": "application/json"})

        self.assertEqual(422, rejected_backup.status_code)
        self.assertEqual(45, self.client.get("/api/baggage-rotation-settings").json()["intervalMinutes"])

    def test_complete_backup_restores_rotation_settings(self):
        self.client.post("/api/baggage-rotation-settings", json={"enabled": False, "intervalMinutes": 75})
        backup = self.client.get("/api/backup").json()
        restore_path = Path(self.directory.name) / "restored.sqlite3"
        restored = TestClient(create_app(database_path=restore_path))
        try:
            response = restored.post("/api/restore-backup", json=backup)
            self.assertEqual(200, response.status_code)
            self.assertEqual({"enabled": False, "intervalMinutes": 75}, {
                "enabled": restored.get("/api/baggage-rotation-settings").json()["enabled"],
                "intervalMinutes": restored.get("/api/baggage-rotation-settings").json()["intervalMinutes"],
            })
        finally:
            restored.close()

    def test_backup_without_rotation_settings_uses_the_default(self):
        self.client.post("/api/baggage-rotation-settings", json={"enabled": True, "intervalMinutes": 45})
        backup = self.client.get("/api/backup").json()
        backup["state"].pop("baggageRotationSettings")

        response = self.client.post("/api/restore-backup", json=backup)

        self.assertEqual(200, response.status_code)
        settings = self.client.get("/api/baggage-rotation-settings").json()
        self.assertTrue(settings["enabled"])
        self.assertEqual(60, settings["intervalMinutes"])

    def test_backup_with_invalid_flight_window_is_rejected(self):
        backup = self.client.get("/api/backup").json()
        backup["state"]["importedDay"] = {
            "serviceDate": "2026-08-02",
            "title": "服务日",
            "flights": [{"id": "f1", "flightNo": "CX999", "windowStart": "25:60", "windowEnd": "27:00"}],
            "roleNames": [],
            "cells": [],
            "remarks": [],
        }

        response = self.client.post("/api/restore-backup", json=backup)

        self.assertEqual(422, response.status_code)
        self.assertEqual(404, self.client.get("/api/imported-day").status_code)


if __name__ == "__main__":
    unittest.main()
