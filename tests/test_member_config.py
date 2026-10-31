import copy
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from gate_schedule.excel_io import export_imported_day, import_day
from gate_schedule.web import create_app
from tests.test_excel_io import day_20260814_bytes


CONFIGS = [
    {"memberName": "刘楠", "eligibleRoleNames": ["接机1", "登机人证核对/队伍秩序维护"]},
    {"memberName": "李梦", "eligibleRoleNames": []},
]


class MemberConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "schedule.sqlite3"
        self.client = TestClient(create_app(database_path=self.database))

    def tearDown(self):
        self.client.close()
        self.directory.cleanup()

    def save_config(self):
        preview = self.client.post("/api/import-excel?serviceDate=2026-08-14", content=day_20260814_bytes())
        self.assertEqual(200, preview.status_code)
        response = self.client.post("/api/imported-day/confirm", json={"cells": [], "memberConfigs": CONFIGS})
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(CONFIGS, response.json()["memberConfigs"])
        return response.json()

    def test_names_are_available_for_checkboxes_without_inferring_permissions(self):
        preview = self.client.post("/api/import-excel?serviceDate=2026-08-14", content=day_20260814_bytes()).json()
        self.assertIn("刘楠", preview["memberNames"])
        self.assertIn("邓颖", preview["memberNames"])
        self.assertNotIn("值机支援", preview["memberNames"])
        self.assertEqual([], preview["memberConfigs"])

    def test_confirm_restart_and_backup_preserve_permissions_and_empty_selection(self):
        saved = self.save_config()
        restarted = TestClient(create_app(database_path=self.database))
        restored = TestClient(create_app(database_path=Path(self.directory.name) / "restored.sqlite3"))
        try:
            self.assertEqual(saved, restarted.get("/api/imported-day").json())
            backup = self.client.get("/api/backup").json()
            self.assertEqual(200, restored.post("/api/restore-backup", json=backup).status_code)
            self.assertEqual(saved, restored.get("/api/imported-day").json())
            updated = [{"memberName": "刘楠", "eligibleRoleNames": []}]
            response = restarted.post("/api/imported-day/confirm", json={"cells": [], "memberConfigs": updated})
            self.assertEqual(updated, response.json()["memberConfigs"])
            response = restarted.post("/api/imported-day/confirm", json={"cells": []})
            self.assertEqual(updated, response.json()["memberConfigs"])
        finally:
            restarted.close()
            restored.close()

    def test_invalid_permissions_and_tampered_backup_do_not_replace_saved_day(self):
        saved = self.save_config()
        bad_configs = [
            [{"memberName": "刘楠", "eligibleRoleNames": ["不存在的岗位"]}],
            [{"memberName": "值机支援", "eligibleRoleNames": ["接机1"]}],
            [{"memberName": "刘楠", "eligibleRoleNames": ["接机1", "接机1"]}],
            [CONFIGS[0], CONFIGS[0]],
        ]
        for configs in bad_configs:
            with self.subTest(configs=configs):
                response = self.client.post("/api/imported-day/confirm", json={"cells": [], "memberConfigs": configs})
                self.assertEqual(422, response.status_code)
                self.assertEqual(saved, self.client.get("/api/imported-day").json())
                backup = self.client.get("/api/backup").json()
                backup["state"]["importedDay"]["memberConfigs"] = configs
                self.assertEqual(422, self.client.post("/api/restore-backup", json=backup).status_code)
                self.assertEqual(saved, self.client.get("/api/imported-day").json())

    def test_excel_round_trip_preserves_permissions_when_config_sheet_is_first(self):
        self.save_config()
        workbook = load_workbook(BytesIO(self.client.get("/api/export-excel").content))
        self.assertIn("人员配置", workbook.sheetnames)
        self.assertEqual("保障明细", workbook.active.title)
        workbook.move_sheet("人员配置", offset=-workbook.sheetnames.index("人员配置"))
        stream = BytesIO()
        workbook.save(stream)
        day = import_day(stream.getvalue(), "2026-08-14")
        restored = import_day(export_imported_day(day), "2026-08-14")
        self.assertEqual(day.member_configs, restored.member_configs)
        self.assertEqual(CONFIGS, [
            {"memberName": item.member_name, "eligibleRoleNames": list(item.eligible_role_names)}
            for item in restored.member_configs
        ])

    def test_next_day_reuses_confirmed_permissions_and_file_can_clear_them(self):
        self.save_config()
        response = self.client.post("/api/import-excel?serviceDate=2026-08-15", content=day_20260814_bytes())
        self.assertEqual(CONFIGS, response.json()["memberConfigs"])
        workbook = load_workbook(BytesIO(day_20260814_bytes()))
        sheet = workbook.create_sheet("人员配置")
        sheet.append(["人员", "可做岗位"])
        sheet.append(["刘楠", ""])
        stream = BytesIO()
        workbook.save(stream)
        response = self.client.post("/api/import-excel?serviceDate=2026-08-15", content=stream.getvalue())
        self.assertEqual([{"memberName": "刘楠", "eligibleRoleNames": []}], response.json()["memberConfigs"])

    def test_old_backup_has_no_implied_permissions(self):
        self.save_config()
        backup = copy.deepcopy(self.client.get("/api/backup").json())
        backup["state"]["importedDay"].pop("memberConfigs")
        self.assertEqual(200, self.client.post("/api/restore-backup", json=backup).status_code)
        self.assertEqual([], self.client.get("/api/imported-day").json()["memberConfigs"])


if __name__ == "__main__":
    unittest.main()
