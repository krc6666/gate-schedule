import copy
import tempfile
import unittest
from dataclasses import replace
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from gate_schedule.domain import ImportedRoleConfig
from gate_schedule.excel_io import export_imported_day, import_day, update_imported_day, validate_imported_day
from gate_schedule.web import create_app
from tests.test_excel_io import day_20260814_bytes


CONFIGS = [
    {"roleName": "接机1", "requiredCount": 2},
    {"roleName": "大件行李拦截", "requiredCount": 1},
    {"roleName": "督导", "requiredCount": 0},
    {"roleName": "登机人证核对/队伍秩序维护", "requiredCount": None},
]


def configured_workbook(configs=CONFIGS, first=False):
    workbook = load_workbook(BytesIO(day_20260814_bytes()))
    sheet = workbook.create_sheet("岗位配置", 0 if first else len(workbook.worksheets))
    sheet.append(["岗位", "同时需要人数"])
    for config in configs:
        sheet.append([config["roleName"], config["requiredCount"]])
    result = BytesIO()
    workbook.save(result)
    return result.getvalue()


class RoleConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "schedule.sqlite3"
        self.client = TestClient(create_app(database_path=self.database))

    def tearDown(self):
        self.client.close()
        self.directory.cleanup()

    def save_configs(self):
        self.client.post("/api/import-excel?serviceDate=2026-08-14", content=day_20260814_bytes())
        result = self.client.post("/api/imported-day/confirm", json={"cells": [], "roleConfigs": CONFIGS})
        self.assertEqual(200, result.status_code, result.text)
        return result.json()

    def test_configured_counts_preserve_names_and_survive_restart_and_backup(self):
        saved = self.save_configs()
        self.assertEqual(CONFIGS, saved["roleConfigs"])
        baggage = next(cell for cell in saved["cells"] if cell["roleName"] == "大件行李拦截" and cell["internalNames"])
        self.assertEqual(2, len(baggage["internalNames"]))
        self.assertEqual("rotation", baggage["semantics"])
        restarted = TestClient(create_app(database_path=self.database))
        restored = TestClient(create_app(database_path=Path(self.directory.name) / "restored.sqlite3"))
        try:
            self.assertEqual(CONFIGS, restarted.get("/api/imported-day").json()["roleConfigs"])
            backup = self.client.get("/api/backup").json()
            self.assertEqual(200, restored.post("/api/restore-backup", json=backup).status_code)
            self.assertEqual(CONFIGS, restored.get("/api/imported-day").json()["roleConfigs"])
        finally:
            restarted.close()
            restored.close()

    def test_clearing_a_count_preserves_zero_and_other_edits(self):
        self.save_configs()
        configs = copy.deepcopy(CONFIGS)
        configs[0]["requiredCount"] = None
        response = self.client.post("/api/imported-day/confirm", json={"cells": [], "roleConfigs": configs})
        self.assertEqual(200, response.status_code)
        self.assertEqual(configs, response.json()["roleConfigs"])
        response = self.client.post("/api/imported-day/confirm", json={"cells": []})
        self.assertEqual(configs, response.json()["roleConfigs"])

    def test_saved_day_can_be_edited_after_restart_without_reimporting(self):
        self.save_configs()
        restarted = TestClient(create_app(database_path=self.database))
        try:
            self.assertEqual(200, restarted.get("/api/imported-day").status_code)
            configs = copy.deepcopy(CONFIGS)
            configs[0]["requiredCount"] = 3
            response = restarted.post("/api/imported-day/confirm", json={"cells": [], "roleConfigs": configs})
            self.assertEqual(200, response.status_code)
            self.assertEqual(configs, response.json()["roleConfigs"])
        finally:
            restarted.close()

    def test_new_workbook_inherits_saved_config_but_file_config_takes_precedence(self):
        self.save_configs()
        preview = self.client.post("/api/import-excel?serviceDate=2026-08-15", content=day_20260814_bytes())
        self.assertEqual(CONFIGS, preview.json()["roleConfigs"])
        configs = [{"roleName": "接机1", "requiredCount": 3}]
        preview = self.client.post("/api/import-excel?serviceDate=2026-08-16", content=configured_workbook(configs))
        by_name = {item["roleName"]: item["requiredCount"] for item in preview.json()["roleConfigs"]}
        self.assertEqual(3, by_name["接机1"])
        self.assertIsNone(by_name["督导"])
        self.assertEqual(CONFIGS, self.client.get("/api/imported-day").json()["roleConfigs"])

    def test_opening_saved_day_replaces_unsaved_preview_before_editing(self):
        self.assertEqual(404, self.client.post("/api/imported-day/open").status_code)
        saved = self.save_configs()
        preview = self.client.post("/api/import-excel?serviceDate=2026-08-15", content=day_20260814_bytes())
        self.assertEqual("2026-08-15", preview.json()["serviceDate"])
        opened = self.client.post("/api/imported-day/open")
        self.assertEqual(200, opened.status_code)
        self.assertEqual(saved, opened.json())
        configs = copy.deepcopy(CONFIGS)
        configs[0]["requiredCount"] = 3
        edited = self.client.post("/api/imported-day/confirm", json={"cells": [], "roleConfigs": configs})
        self.assertEqual(200, edited.status_code)
        self.assertEqual("2026-08-14", edited.json()["serviceDate"])
        self.assertEqual(configs, edited.json()["roleConfigs"])
        self.assertEqual(edited.json(), self.client.get("/api/imported-day").json())

    def test_excel_round_trip_preserves_null_and_zero_with_config_sheet_first(self):
        imported = import_day(configured_workbook(first=True), "2026-08-14")
        self.assertEqual(CONFIGS, [{"roleName": item.role_name, "requiredCount": item.required_count} for item in imported.role_configs])
        exported = export_imported_day(imported)
        self.assertEqual("保障明细", load_workbook(BytesIO(exported)).active.title)
        restored = import_day(exported, "2026-08-14")
        self.assertEqual(imported.role_configs, restored.role_configs)
        self.assertEqual(imported.cells, restored.cells)

    def test_invalid_config_and_tampered_backup_leave_saved_day_unchanged(self):
        saved = self.save_configs()
        invalid_configs = [
            [{"roleName": "接机1", "requiredCount": value}] for value in (-1, 1.5, True, "2")
        ] + [
            [{"roleName": "未知岗位", "requiredCount": 2}],
            [{"roleName": "接机1", "requiredCount": 2}] * 2,
        ]
        for configs in invalid_configs:
            with self.subTest(configs=configs):
                rejected = self.client.post("/api/imported-day/confirm", json={"cells": [], "roleConfigs": configs})
                self.assertEqual(422, rejected.status_code)
                self.assertEqual(saved, self.client.get("/api/imported-day").json())
        backup = self.client.get("/api/backup").json()
        for value in (-1, 2.5, True, "2"):
            with self.subTest(backup=value):
                corrupted = copy.deepcopy(backup)
                corrupted["state"]["importedDay"]["roleConfigs"][0]["requiredCount"] = value
                self.assertEqual(422, self.client.post("/api/restore-backup", json=corrupted).status_code)
                self.assertEqual(saved, self.client.get("/api/imported-day").json())

    def test_final_validation_rejects_negative_duplicate_and_unknown_config(self):
        day = update_imported_day(import_day(day_20260814_bytes(), "2026-08-14"), [], CONFIGS)
        self.assertEqual([], validate_imported_day(day))
        for config, code in (
            (ImportedRoleConfig("接机1", -1), "invalid_required_count"),
            (ImportedRoleConfig("接机1", 1), "duplicate_role_config"),
            (ImportedRoleConfig("未知岗位", 1), "unknown_role_config"),
        ):
            tampered = replace(day, role_configs=day.role_configs + (config,))
            self.assertIn(code, {issue.code for issue in validate_imported_day(tampered)})

    def test_old_workbook_and_backup_leave_counts_unconfigured(self):
        preview = self.client.post("/api/import-excel?serviceDate=2026-08-14", content=day_20260814_bytes()).json()
        self.assertEqual([], preview["roleConfigs"])
        self.save_configs()
        backup = self.client.get("/api/backup").json()
        backup["state"]["importedDay"].pop("roleConfigs")
        self.assertEqual(200, self.client.post("/api/restore-backup", json=backup).status_code)
        self.assertEqual([], self.client.get("/api/imported-day").json()["roleConfigs"])


if __name__ == "__main__":
    unittest.main()
