from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from gate_schedule.excel_io import export_complete_configuration, import_complete_configuration
from gate_schedule.server import create_server_app
from gate_schedule.workbench import Workbench, WorkspaceError

ROOT = Path(__file__).resolve().parents[1]


def example():
    data = json.loads((ROOT / "templates/登机口完整试用配置-20260802.json").read_text(encoding="utf-8"))["state"]
    plan = data["plans"][data["selectedPlanId"]]
    return {"day": plan["day"], "rotationEnabled": plan["rotationEnabled"], "rotationMinutes": plan["rotationMinutes"], "source": data["trialInfo"]["label"]}


def edit(content, sheet, cell, value):
    book = load_workbook(BytesIO(content))
    book[sheet][cell] = value
    result = BytesIO()
    book.save(result)
    return result.getvalue()


class CompleteExcelTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=ROOT / ".tmp", prefix="complete-excel-")
        self.addCleanup(self.directory.cleanup)
        self.workspace = Workbench(Path(self.directory.name) / "state.sqlite3")
        self.content = export_complete_configuration(example())

    def test_one_visible_workbook_preserves_inputs_and_edited_zero(self):
        book = load_workbook(BytesIO(self.content))
        self.assertIn("岗位资质", book.sheetnames)
        self.assertNotIn("人员配置", book.sheetnames)
        self.assertNotIn("保障明细", book.sheetnames)
        self.assertTrue(all(sheet.sheet_state == "visible" for sheet in book))
        self.assertEqual(15, book["岗位资质"].max_column - 1)
        imported = import_complete_configuration(self.content)
        self.assertEqual(115, sum(len(c["eligibleRoleNames"]) for c in imported["day"]["memberConfigs"]))
        self.assertEqual(7, len(imported["day"]["flights"]))
        self.assertEqual(70, len(imported["day"]["cells"]))
        self.assertEqual(example()["day"]["memberAvailability"], imported["day"]["memberAvailability"])
        self.assertEqual("次日00:10", imported["day"]["flights"][-1]["windowStart"])
        changed = import_complete_configuration(edit(self.content, "岗位资质", "B2", 0))
        name = book["岗位资质"]["B1"].value
        role = book["岗位资质"]["A2"].value
        self.assertNotIn(role, next(c["eligibleRoleNames"] for c in changed["day"]["memberConfigs"] if c["memberName"] == name))
        # Optional settings and actual times are editable, not hidden payloads.
        modified = edit(edit(self.content, "自动排班设置", "B4", "否"), "岗位时间", "C2", "10:15")
        parsed = import_complete_configuration(modified)
        self.assertFalse(parsed["rotationEnabled"])
        self.assertEqual("10:15", parsed["day"]["cells"][0]["workStart"])

    def test_atomic_preview_revoke_preserve_formal_and_roundtrip(self):
        config = import_complete_configuration(self.content)
        preview = self.workspace.preview_complete_configuration(config)
        self.assertEqual(0, self.workspace.snapshot()["revision"])
        state = self.workspace.execute(0, "import-complete-configuration", preview["payload"])
        plan_id = state["selectedPlanId"]
        state = self.workspace.execute(state["revision"], "solve", {}, plan_id)
        self.assertTrue(state["plans"][plan_id]["valid"])
        state = self.workspace.execute(state["revision"], "adopt", {}, plan_id)
        formal = deepcopy(state["plans"][plan_id])
        ids = {p["name"]: p["id"] for p in state["people"]}
        exported = self.workspace.export_complete_configuration(plan_id)
        book = load_workbook(BytesIO(exported))
        target = next(c for row in book["岗位资质"].iter_rows(min_row=2, min_col=2) for c in row if c.value == 1)
        person = book["岗位资质"].cell(1, target.column).value
        role = book["岗位资质"].cell(target.row, 1).value
        modified = import_complete_configuration(edit(exported, "岗位资质", target.coordinate, 0))
        before = self.workspace.snapshot()
        preview = self.workspace.preview_complete_configuration(modified)
        self.assertEqual(1, preview["removedQualificationCount"])
        self.assertEqual(before, self.workspace.snapshot(), "预览/取消不能写库")
        new = self.workspace.execute(state["revision"], "import-complete-configuration", preview["payload"])
        self.assertEqual(formal, new["plans"][plan_id], "原草稿/正式快照均保留")
        self.assertNotEqual(plan_id, new["selectedPlanId"])
        self.assertEqual(ids, {p["name"]: p["id"] for p in new["people"]})
        self.assertNotIn(role, next(p["eligibleRoleNames"] for p in new["people"] if p["name"] == person))
        self.assertNotIn(role, next(p["eligibleRoleNames"] for p in new["plans"][new["selectedPlanId"]]["day"]["memberConfigs"] if p["memberName"] == person))
        self.assertTrue(self.workspace.export(plan_id).startswith(b"PK"))
        restored = Workbench(Path(self.directory.name) / "restored.sqlite3")
        restored.restore(0, self.workspace.backup())
        expected = deepcopy(new)
        expected["revision"] = 1
        expected["priorityStatistics"]["revision"] = 1
        self.assertEqual(expected, restored.snapshot())
        with self.assertRaises(WorkspaceError):
            self.workspace.execute(state["revision"], "import-complete-configuration", config)
        self.assertEqual(new, self.workspace.snapshot())

    def test_invalid_workbook_and_inactive_person_never_partially_write(self):
        for sheet, cell, value in [("岗位资质", "B2", "=1"), ("岗位资质", "B2", 3), ("使用说明", "B2", "未来版本"), ("航班", "A3", "CX937"), ("航班", "C2", "09:00"), ("自动排班设置", "B5", 0), ("当日出勤", "A2", "未列人员")]:
            with self.subTest(sheet=sheet, cell=cell, value=value), self.assertRaises(ValueError):
                import_complete_configuration(edit(self.content, sheet, cell, value))
        config = import_complete_configuration(self.content)
        name = config["day"]["roster"][0]
        state = self.workspace.execute(0, "save-person", {"name": name, "active": False})
        with self.assertRaisesRegex(WorkspaceError, "停用"):
            self.workspace.execute(state["revision"], "import-complete-configuration", config)
        self.assertEqual(state, self.workspace.snapshot())

    def test_api_preview_import_export_and_bad_upload(self):
        client = TestClient(create_server_app(database_path=Path(self.directory.name) / "api.sqlite3"))
        preview = client.post("/api/workspace/configuration/preview", content=self.content)
        self.assertEqual(200, preview.status_code, preview.text)
        self.assertEqual([], client.get("/api/workspace").json()["people"])
        state = client.post("/api/workspace/command", json={"expectedRevision": 0, "action": "import-complete-configuration", "payload": preview.json()["payload"]}).json()
        export = client.get(f"/api/workspace/configuration/export/{state['selectedPlanId']}")
        self.assertEqual(200, export.status_code)
        self.assertEqual(15, len(import_complete_configuration(export.content)["day"]["roster"]))
        response = client.post("/api/workspace/configuration/preview", content=b"bad xlsx")
        self.assertEqual(422, response.status_code)
        self.assertEqual(state, client.get("/api/workspace").json())

    def test_add_people_and_roles_keep_unlisted_data_and_atomic_disk_failure(self):
        state = self.workspace.execute(0, "save-person", {"name": "表外人员", "eligibleRoleNames": ["表外岗位"]})
        listed_name = example()["day"]["roster"][0]
        state = self.workspace.execute(state["revision"], "save-person", {"name": listed_name, "eligibleRoleNames": ["表外岗位"]})
        book = load_workbook(BytesIO(self.content))
        matrix = book["岗位资质"]
        new_column = matrix.max_column + 1
        matrix.cell(1, new_column, "新增人员")
        for row in range(2, matrix.max_row + 1):
            matrix.cell(row, new_column, 1 if matrix.cell(row, 1).value == "接机1" else 0)
        matrix.append(["临时岗位", *([0] * (new_column - 1))])
        book["岗位配置"].append(["临时岗位", 0])
        book["当日出勤"].append(["新增人员", "否", "", ""])
        content = BytesIO()
        book.save(content)
        config = import_complete_configuration(content.getvalue())
        with patch.object(self.workspace.store, "compare_and_save", side_effect=OSError("模拟磁盘失败")):
            with self.assertRaises(OSError):
                self.workspace.execute(state["revision"], "import-complete-configuration", config)
        self.assertEqual(state, self.workspace.snapshot(), "不能先写入部分人员/资质")
        state = self.workspace.execute(state["revision"], "import-complete-configuration", config)
        plan = state["plans"][state["selectedPlanId"]]
        self.assertEqual(16, len(plan["day"]["roster"]))
        self.assertEqual(77, len(plan["day"]["cells"]))
        self.assertNotIn("表外人员", plan["day"]["roster"])
        self.assertIn("表外岗位", next(p["eligibleRoleNames"] for p in state["people"] if p["name"] == listed_name))
        self.assertEqual(["接机1"], next(p["eligibleRoleNames"] for p in state["people"] if p["name"] == "新增人员"))
        self.assertEqual(0, next(r["requiredCount"] for r in state["configuration"]["roleConfigs"] if r["roleName"] == "临时岗位"))

    def test_missing_actual_times_remain_incomplete_and_cannot_solve(self):
        content = edit(edit(self.content, "岗位时间", "C2", None), "当日出勤", "C2", None)
        config = import_complete_configuration(content)
        state = self.workspace.execute(0, "import-complete-configuration", config)
        plan_id = state["selectedPlanId"]
        self.assertFalse(state["plans"][plan_id]["valid"])
        with self.assertRaises(WorkspaceError):
            self.workspace.execute(state["revision"], "solve", {}, plan_id)
        with self.assertRaises(WorkspaceError):
            self.workspace.execute(state["revision"], "adopt", {}, plan_id)
        self.assertEqual(state, self.workspace.snapshot())


if __name__ == "__main__":
    unittest.main()
