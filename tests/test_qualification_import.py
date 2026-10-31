from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from gate_schedule.excel_io import export_qualifications, import_qualifications
from gate_schedule.historical_qualifications import analyze_history
from gate_schedule.server import create_server_app
from gate_schedule.workbench import Workbench, WorkspaceError
from tests import test_workspace


def proposal():
    return {"people": ["甲", "乙"], "roles": [{"roleName": "接机1", "memberNames": ["甲", "乙"]}], "source": "手动配置"}


class QualificationImportTests(unittest.TestCase):
    def test_workbook_roundtrip_and_invalid_marks_names_and_formulas(self):
        content = export_qualifications(proposal())
        self.assertEqual(proposal(), import_qualifications(content))
        for coordinate, value in [("B1", ""), ("C1", "甲"), ("B2", "是吧"), ("B2", "=1"), ("A2", "")]:
            with self.subTest(coordinate=coordinate, value=value):
                workbook = load_workbook(BytesIO(content))
                workbook["岗位资质"][coordinate] = value
                stream = BytesIO()
                workbook.save(stream)
                with self.assertRaises(ValueError):
                    import_qualifications(stream.getvalue())
        with self.assertRaises(ValueError):
            import_qualifications(b"broken xlsx")

    def test_preview_is_read_only_apply_merges_by_exact_name_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workbench(Path(directory) / "test.sqlite3")
            state = workspace.execute(0, "save-person", {"name": "甲", "eligibleRoleNames": ["督导"], "active": False})
            before = workspace.backup()
            preview = workspace.preview_qualifications(proposal())
            self.assertEqual(1, preview["newPeopleCount"])
            self.assertEqual(2, preview["newQualificationCount"])
            self.assertEqual(["甲"], preview["inactivePeople"])
            self.assertEqual(before, workspace.backup())
            applied = workspace.execute(state["revision"], "import-qualifications", proposal())
            self.assertEqual({"督导", "接机1"}, set(applied["people"][0]["eligibleRoleNames"]))
            self.assertFalse(applied["people"][0]["active"])
            self.assertEqual(state["people"][0]["id"], applied["people"][0]["id"])
            again = workspace.preview_qualifications(proposal())
            self.assertEqual(0, again["newPeopleCount"])
            self.assertEqual(0, again["newQualificationCount"])
            repeated = workspace.execute(applied["revision"], "import-qualifications", proposal())
            self.assertEqual(applied["people"], repeated["people"])
            bad = proposal()
            bad["roles"].append({"roleName": "新岗", "memberNames": ["不存在"]})
            before = workspace.backup()
            with self.assertRaises(WorkspaceError):
                workspace.execute(repeated["revision"], "import-qualifications", bad)
            self.assertEqual(before, workspace.backup())
            with self.assertRaises(WorkspaceError):
                workspace.execute(state["revision"], "import-qualifications", proposal())

    def test_import_leaves_adopted_plans_drafts_input_and_default_counts_unchanged(self):
        helper = test_workspace.WorkspaceTests()
        helper.setUp()
        self.addCleanup(helper.tearDown)
        plan_id, _ = helper.complete_plan()
        helper.command("solve", {}, plan_id)
        helper.command("adopt", {}, plan_id)
        before = helper.workspace.snapshot()
        data = proposal()
        data["people"].append("新人员")
        data["roles"].append({"roleName": "新岗位", "memberNames": ["新人员"]})
        after = helper.command("import-qualifications", data)
        self.assertEqual(before["plans"], after["plans"])
        self.assertEqual(before["configuration"]["roleConfigs"], after["configuration"]["roleConfigs"][:-1])
        self.assertIsNone(after["configuration"]["roleConfigs"][-1]["requiredCount"])
        self.assertTrue(helper.workspace.export(plan_id).startswith(b"PK"))

    def test_actual_api_preview_cancel_apply_and_export(self):
        with tempfile.TemporaryDirectory() as directory:
            client = TestClient(create_server_app(database_path=Path(directory) / "test.sqlite3"))
            initial = client.get("/api/workspace").json()
            content = export_qualifications(proposal())
            preview = client.post("/api/workspace/qualifications/preview", content=content)
            self.assertEqual(200, preview.status_code, preview.text)
            self.assertEqual(initial, client.get("/api/workspace").json())
            response = client.post("/api/workspace/command", json={"expectedRevision": initial["revision"], "action": "import-qualifications", "payload": preview.json()["payload"]})
            self.assertEqual(200, response.status_code, response.text)
            exported = client.get("/api/workspace/qualifications/template")
            self.assertEqual(200, exported.status_code)
            parsed = import_qualifications(exported.content)
            self.assertEqual(proposal()["people"], parsed["people"])
            self.assertEqual(proposal()["roles"][0], next(r for r in parsed["roles"] if r["roleName"] == "接机1"))
            self.assertEqual(422, client.post("/api/workspace/qualifications/preview", content=b"bad").status_code)

    def test_history_counts_per_flight_excludes_support_and_keeps_identity_questions(self):
        text = """## 20260802
| 岗位 | CX937 / 1010-1130 | KE166 / 1100-1215 |
| --- | --- | --- |
| 接机1 | 李杨/李杨/甲 | 李扬 |
| 登机人证核对/队伍秩序维护 | 甲/值机支援 | 支援 |
| 登机查验 / （VIP/F/ / 优先通道） | 甲 | / |
| 0910支援: 备注人员 | | |
"""
        result = analyze_history(text)
        self.assertEqual(["李杨", "甲"], result["payload"]["people"])
        self.assertNotIn("支援", result["payload"]["people"])
        self.assertNotIn("备注人员", result["payload"]["people"])
        self.assertEqual(["李扬"], result["pendingNames"])
        evidence = next(e for e in result["evidence"] if e["memberName"] == "李杨")
        self.assertEqual(1, evidence["flightCount"])
        self.assertEqual("李杨/李杨/甲", evidence["records"][0]["rawValue"])
        self.assertIn("登机查验（VIP/F/优先通道）", [r["roleName"] for r in result["payload"]["roles"]])
        merged = analyze_history(text, name_aliases={"李扬": "李杨"})
        self.assertEqual([], merged["pendingNames"])
        self.assertEqual(2, next(e for e in merged["evidence"] if e["memberName"] == "李杨")["flightCount"])


if __name__ == "__main__":
    unittest.main()
