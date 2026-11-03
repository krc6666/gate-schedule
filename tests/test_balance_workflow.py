from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from gate_schedule.workbench import Workbench, WorkspaceError
from gate_schedule.excel_io import import_complete_configuration


def small_configuration(gap=0):
    flights = [{"id": f"f{i}", "flightNo": f"F{i}", "windowStart": start, "windowEnd": end}
               for i, (start, end) in enumerate((("10:00", "10:30"), ("11:00", "11:30")))]
    day = {
        "serviceDate": "2026-08-02", "title": "整表平衡工作流", "releasePolicy": "task", "attendanceMode": "duty",
        "flights": flights, "roleNames": ["接机1"], "roster": ["甲", "乙"],
        "remarks": [], "remarksReviewed": True,
        "roleConfigs": [{"roleName": "接机1", "requiredCount": 1, "workMode": "arrival", "concurrent": False}],
        "memberConfigs": [{"memberName": name, "eligibleRoleNames": ["接机1"]} for name in ("甲", "乙")],
        "memberAvailability": [{"memberName": name, "participating": True, "availableFrom": None, "availableUntil": None}
                               for name in ("甲", "乙")],
        "cells": [{"id": f"c{i}", "flightId": flight["id"], "flightNo": flight["flightNo"], "roleName": "接机1",
                   "rawValue": "", "internalNames": [], "externalLabels": [], "semantics": "empty"}
                  for i, flight in enumerate(flights)],
    }
    return {"day": day, "rotationMinutes": 60, "rotationEnabled": True, "source": "平衡验收夹具",
            "balanceSettings": {"flightCountGap": gap, "priorityEnabled": False,
                                "priorityRoleNames": [], "priorityAdjustments": []}}


class BalanceWorkflowTests(unittest.TestCase):
    def setUp(self):
        directory = Path(__file__).resolve().parents[1] / ".tmp"
        directory.mkdir(exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=directory, prefix="balance-workflow-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "workspace.sqlite3"
        self.workbench = Workbench(self.path)

    def command(self, action, payload=None, plan_id=None):
        return self.workbench.execute(self.workbench.snapshot()["revision"], action, payload or {}, plan_id)

    def setup_plan(self, gap=0):
        state = self.command("import-complete-configuration", small_configuration(gap))
        return state["selectedPlanId"]

    def fill(self, plan_id, first="甲", second="乙"):
        self.command("edit-cell", {"cellId": "c0", "text": first}, plan_id)
        return self.command("edit-cell", {"cellId": "c1", "text": second}, plan_id)

    def update_gap(self, gap):
        settings = deepcopy(self.workbench.snapshot()["balanceSettings"])
        settings["flightCountGap"] = gap
        return self.command("update-balance-settings", {"balanceSettings": settings})

    def test_automatic_scarcity_survives_excel_empty_database_restart_and_backup(self):
        config = small_configuration(None)
        day = config["day"]
        day["roleNames"].append("普通岗")
        day["roleConfigs"].append({"roleName": "普通岗", "requiredCount": 1, "workMode": "arrival", "concurrent": False})
        for member in day["memberConfigs"]:
            member["eligibleRoleNames"].append("普通岗")
        day["roster"].append("丙")
        day["memberConfigs"].append({"memberName": "丙", "eligibleRoleNames": ["普通岗"]})
        day["memberAvailability"].append({"memberName": "丙", "participating": True, "availableFrom": None, "availableUntil": None})
        day["cells"].extend({**deepcopy(cell), "id": cell["id"] + "-common", "roleName": "普通岗"}
                            for cell in list(day["cells"]))
        state = self.command("import-complete-configuration", config)
        pid = state["selectedPlanId"]
        for role in state["configuration"]["roleConfigs"]:
            if role["roleName"] not in day["roleNames"]:
                self.command("save-role", {"roleName": role["roleName"], "requiredCount": 0})
        state = self.command("solve", {}, pid)
        pid = state["selectedPlanId"]
        self.command("adopt", {}, pid)
        before = self.workbench.backup()
        incoming = import_complete_configuration(self.workbench.export_complete_configuration(pid))
        other_path = Path(self.directory.name) / "excel-empty.sqlite3"
        target = Workbench(other_path)
        state = target.execute(0, "import-complete-configuration", incoming)
        target = Workbench(other_path)
        state = target.execute(state["revision"], "solve", {}, state["selectedPlanId"])
        plan = state["plans"][state["selectedPlanId"]]
        self.assertTrue(plan["valid"])
        self.assertFalse(state["balanceSettings"]["priorityEnabled"], "自动稀缺轮换不依赖手选重点开关")
        rare_tasks = {task["id"] for task in plan["schedule"]["tasks"] if task["roleId"] == "接机1"}
        self.assertEqual(2, len({a["memberId"] for a in plan["draft"] if a["taskId"] in rare_tasks}))
        restored = Workbench(Path(self.directory.name) / "backup-empty.sqlite3")
        restored.restore(0, before)
        self.assertEqual(before["state"]["plans"], restored.backup()["state"]["plans"])
        self.assertEqual(before, self.workbench.backup(), "迁移及重算不能改原正式和历史")

    def test_zero_gap_does_not_block_first_manual_choice_but_reports_whole_day(self):
        plan_id = self.setup_plan(0)
        before = self.workbench.backup()
        candidates = {c["name"]: c for c in self.workbench.candidates(plan_id, "imported-c0")}
        self.assertTrue(candidates["甲"]["available"])
        self.assertTrue(candidates["乙"]["available"])
        self.assertEqual(before, self.workbench.backup())
        state = self.command("edit-cell", {"cellId": "c0", "text": "甲"}, plan_id)
        plan = state["plans"][plan_id]
        self.assertFalse(plan["valid"])
        self.assertIn("flight_count_spread", {i["code"] for i in plan["issues"]})
        self.assertEqual([0, 1], sorted(m["count"] for m in plan["schedule"]["memberFlights"]))
        next_candidates = {c["name"]: c for c in self.workbench.candidates(plan_id, "imported-c1")}
        self.assertTrue(next_candidates["甲"]["available"], "整表差值不应伪装成本岗不合格")
        self.assertTrue(next_candidates["乙"]["available"])
        state = self.command("edit-cell", {"cellId": "c1", "text": "乙"}, plan_id)
        self.assertTrue(state["plans"][plan_id]["valid"])
        self.assertNotIn("flight_count_spread", {i["code"] for i in state["plans"][plan_id]["issues"]})

    def test_breaking_valid_draft_preserves_warnings_in_archive_and_export(self):
        plan_id = self.setup_plan(0)
        self.assertTrue(self.fill(plan_id)["plans"][plan_id]["valid"])
        state = self.command("edit-cell", {"cellId": "c1", "text": "甲"}, plan_id)
        plan = state["plans"][plan_id]
        self.assertEqual({"flight_count_spread"}, {i["code"] for i in plan["issues"]})
        issue = plan['issues'][0]
        self.assertEqual([], issue['relatedTaskIds'], '整表航班差值不在每个岗位格标红或显示感叹号')
        self.assertIsNone(issue['taskId'])
        self.assertEqual('当天参与航班最多2班、最少0班，差2，超过上限0；请整体调整人员安排', issue['message'])
        self.assertFalse(plan["valid"])
        next_day = deepcopy(plan["day"])
        next_day["serviceDate"] = "2026-08-04"
        before = self.workbench.backup()
        for action, payload in (("adopt", {}), ("archive-only", {}), ("archive-next", {"day": next_day})):
            state = self.command(action, payload, plan_id)
            self.assertEqual('flight_count_spread', state['plans'][plan_id]['adopted']['issues'][0]['code'])
        self.assertTrue(self.workbench.export(plan_id).startswith(b'PK'))
        after = self.workbench.backup()
        restored = Workbench(self.path)
        self.assertEqual(after, restored.backup())

    def test_rule_change_rerun_and_cancel_keep_original_plan_and_formal_rule(self):
        plan_id = self.setup_plan(None)
        self.fill(plan_id, "甲", "甲")
        original = self.command("adopt", {}, plan_id)["plans"][plan_id]
        original_backup = deepcopy(self.workbench.backup()["state"]["plans"][plan_id])
        self.assertIsNone(original["adopted"]["scenario"]["flightCountGap"])
        self.update_gap(0)
        state = self.workbench.snapshot()
        self.assertEqual(original_backup, self.workbench.backup()["state"]["plans"][plan_id])
        self.assertTrue(state["plans"][plan_id]["valid"], "新开关不应追改旧计划")
        self.assertTrue(self.workbench.export(plan_id).startswith(b"PK"))
        for action in ("solve", "prepare-solve"):
            with self.subTest(action=action):
                source = deepcopy(self.workbench.backup()["state"]["plans"][plan_id])
                payload = {"day": deepcopy(state["plans"][plan_id]["day"])} if action == "prepare-solve" else {}
                current = self.command(action, payload, plan_id)
                new_id = current["selectedPlanId"]
                self.assertNotEqual(plan_id, new_id)
                new_plan = current["plans"][new_id]
                self.assertEqual(0, new_plan["balanceSettings"]["flightCountGap"])
                self.assertTrue(new_plan["valid"])
                self.assertEqual([1, 1], sorted(row["count"] for row in new_plan["schedule"]["memberFlights"]))
                self.assertEqual(plan_id, new_plan["preview"]["returnPlanId"])
                self.assertEqual(source, self.workbench.backup()["state"]["plans"][plan_id])
                current = self.command("cancel-preview", {}, new_id)
                self.assertEqual(plan_id, current["selectedPlanId"])
                self.assertEqual(source, self.workbench.backup()["state"]["plans"][plan_id])
        self.assertEqual(original["adopted"], Workbench(self.path).snapshot()["plans"][plan_id]["adopted"])

    def test_new_formal_snapshot_keeps_own_rule_after_global_switch_and_draft_changes(self):
        plan_id = self.setup_plan(0)
        self.fill(plan_id)
        state = self.command("adopt", {}, plan_id)
        formal = deepcopy(state["plans"][plan_id]["adopted"])
        self.assertEqual(0, formal["scenario"]["flightCountGap"])
        self.update_gap(None)
        state = self.command("edit-cell", {"cellId": "c1", "text": "甲"}, plan_id)
        self.assertFalse(state["plans"][plan_id]["valid"], "本日草稿仍按已选择的规则快照校验")
        self.assertEqual(formal, state["plans"][plan_id]["adopted"])
        self.assertTrue(self.workbench.export(plan_id).startswith(b"PK"), "历史导出仍用原合法正式快照")
        before = self.workbench.backup()
        saved = self.command("adopt", {}, plan_id)
        self.assertTrue(saved['plans'][plan_id]['adopted']['issues'])
        restored = Workbench(Path(self.directory.name) / "restored.sqlite3")
        restored.restore(0, before)
        self.assertEqual(formal, restored.snapshot()["plans"][plan_id]["adopted"])


if __name__ == "__main__":
    unittest.main()
