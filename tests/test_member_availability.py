import copy
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from gate_schedule.web import create_app
from tests.test_excel_io import day_20260814_bytes


ATTENDANCE = [
    {"memberName": "刘楠", "participating": True, "availableFrom": "10:00", "availableUntil": "18:00"},
    {"memberName": "李梦", "participating": False, "availableFrom": None, "availableUntil": None},
]


class MemberAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "schedule.sqlite3"
        self.client = TestClient(create_app(database_path=self.database))
        self.client.post("/api/import-excel?serviceDate=2026-08-14", content=day_20260814_bytes())

    def tearDown(self):
        self.client.close()
        self.directory.cleanup()

    def save_attendance(self):
        response = self.client.post("/api/imported-day/confirm", json={"cells": [], "memberAvailability": ATTENDANCE})
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(ATTENDANCE, response.json()["memberAvailability"])
        return response.json()

    def test_daily_attendance_survives_restart_backup_and_excel_round_trip(self):
        saved = self.save_attendance()
        restarted = TestClient(create_app(database_path=self.database))
        restored = TestClient(create_app(database_path=Path(self.directory.name) / "restored.sqlite3"))
        try:
            self.assertEqual(saved, restarted.get("/api/imported-day").json())
            self.assertEqual(200, restored.post("/api/restore-backup", json=self.client.get("/api/backup").json()).status_code)
            self.assertEqual(ATTENDANCE, restored.get("/api/imported-day").json()["memberAvailability"])
            exported = self.client.get("/api/export-excel").content
            self.assertIn("当日出勤", load_workbook(BytesIO(exported)).sheetnames)
            preview = restored.post("/api/import-excel?serviceDate=2026-08-14", content=exported)
            self.assertEqual(ATTENDANCE, preview.json()["memberAvailability"])
        finally:
            restarted.close()
            restored.close()

    def test_new_day_reuses_roles_but_never_assumes_previous_days_attendance(self):
        self.client.post("/api/imported-day/confirm", json={
            "cells": [], "memberAvailability": ATTENDANCE,
            "memberConfigs": [{"memberName": "刘楠", "eligibleRoleNames": ["接机1"]}],
        })
        preview = self.client.post("/api/import-excel?serviceDate=2026-08-15", content=day_20260814_bytes()).json()
        self.assertEqual([], preview["memberAvailability"])
        self.assertEqual([{"memberName": "刘楠", "eligibleRoleNames": ["接机1"]}], preview["memberConfigs"])
        self.assertEqual(ATTENDANCE, self.client.get("/api/imported-day").json()["memberAvailability"])

    def test_selected_person_requires_valid_complete_daily_time(self):
        saved = self.save_attendance()
        for start, end in ((None, None), ("10:00", None), ("25:00", "26:00"), ("18:00", "10:00"), ("10:00", "10:00")):
            with self.subTest(start=start, end=end):
                data = copy.deepcopy(ATTENDANCE)
                data[0]["availableFrom"], data[0]["availableUntil"] = start, end
                response = self.client.post("/api/imported-day/confirm", json={"cells": [], "memberAvailability": data})
                self.assertEqual(422, response.status_code)
                self.assertEqual(saved, self.client.get("/api/imported-day").json())

    def test_tampered_attendance_backup_and_duplicate_external_person_are_rejected(self):
        saved = self.save_attendance()
        invalid = [
            [ATTENDANCE[0], ATTENDANCE[0]],
            [{**ATTENDANCE[0], "memberName": "值机支援"}],
            [{**ATTENDANCE[0], "participating": "yes"}],
            [{**ATTENDANCE[0], "availableUntil": None}],
        ]
        for entries in invalid:
            with self.subTest(entries=entries):
                response = self.client.post("/api/imported-day/confirm", json={"cells": [], "memberAvailability": entries})
                self.assertEqual(422, response.status_code)
                backup = self.client.get("/api/backup").json()
                backup["state"]["importedDay"]["memberAvailability"] = entries
                self.assertEqual(422, self.client.post("/api/restore-backup", json=backup).status_code)
                self.assertEqual(saved, self.client.get("/api/imported-day").json())

    def test_unchecked_person_and_old_backup_dont_gain_implied_availability(self):
        self.save_attendance()
        response = self.client.post("/api/imported-day/confirm", json={"cells": [], "memberAvailability": [ATTENDANCE[1]]})
        self.assertTrue(response.json()["valid"])
        self.assertEqual([ATTENDANCE[1]], response.json()["memberAvailability"])
        backup = self.client.get("/api/backup").json()
        backup["state"]["importedDay"].pop("memberAvailability")
        self.assertEqual(200, self.client.post("/api/restore-backup", json=backup).status_code)
        self.assertEqual([], self.client.get("/api/imported-day").json()["memberAvailability"])


if __name__ == "__main__":
    unittest.main()
