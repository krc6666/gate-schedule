from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from gate_schedule.excel_io import import_complete_configuration
from gate_schedule.workbench import Workbench, WorkspaceError
from tests.test_complete_excel import example


ROOT = Path(__file__).resolve().parents[1]


class AddTemplateRoleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=ROOT / ".tmp", prefix="add-template-role-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "state.sqlite3"
        self.workspace = Workbench(self.path)
        state = self.workspace.execute(0, "import-complete-configuration", example())
        self.plan_id = state["selectedPlanId"]
        self.first_no = state["flightLibrary"][0]["flightNo"]
        self.second_no = state["flightLibrary"][1]["flightNo"]

    def command(self, action, payload):
        return self.workspace.execute(self.workspace.snapshot()["revision"], action, payload)

    def template(self, number=None, state=None):
        state = state or self.workspace.snapshot()
        return deepcopy(next(f for f in state["flightLibrary"] if f["flightNo"] == (number or self.first_no)))

    def role(self, name, state=None):
        state = state or self.workspace.snapshot()
        return next(r for r in state["configuration"]["roleConfigs"] if r["roleName"] == name)

    def configure_supervisor(self):
        people = self.workspace.snapshot()["people"]
        selected = [p["id"] for p in people[:2]]
        self.command("save-role", {"roleName": "督导", "requiredCount": 1,
                                  "workMode": "continuous", "concurrent": True,
                                  "qualifiedMemberIds": selected})
        self.command("save-person", {"name": "停用督导测试人员", "active": False,
                                    "eligibleRoleNames": ["督导"]})
        flight = self.template()
        flight["roles"] = [r for r in flight["roles"] if r["roleName"] != "督导"]
        flight["roles"].append({"roleName": "督导", "requiredCount": 2})
        self.command("save-flight-template", flight)
        return selected

    def test_existing_category_adds_a_distinct_row_with_copied_configuration(self):
        self.configure_supervisor()
        before = self.workspace.backup()["state"]
        source = deepcopy(self.role("督导"))
        template_before = self.template()
        second_before = self.template(self.second_no)

        state = self.command("add-template-role", {"flightNo": self.first_no, "roleName": "督导"})

        flight = self.template(state=state)
        self.assertEqual(template_before["roleNames"] + ["督导2"], flight["roleNames"])
        self.assertEqual({**source, "roleName": "督导2"}, self.role("督导2", state))
        self.assertEqual(2, next(r["requiredCount"] for r in flight["roles"] if r["roleName"] == "督导2"))
        self.assertEqual(second_before, self.template(self.second_no, state))
        self.assertEqual(before["plans"], self.workspace.backup()["state"]["plans"])
        for person in state["people"]:
            self.assertEqual("督导" in person["eligibleRoleNames"], "督导2" in person["eligibleRoleNames"])
        disabled = next(p for p in state["people"] if p["name"] == "停用督导测试人员")
        self.assertFalse(disabled["active"])
        self.assertIn("督导2", disabled["eligibleRoleNames"])

        flight["roles"] = [{**r, "requiredCount": 3} if r["roleName"] == "督导2" else r for r in flight["roles"]]
        self.command("save-flight-template", flight)
        self.assertEqual(2, next(r["requiredCount"] for r in self.template()["roles"] if r["roleName"] == "督导"))
        self.assertEqual(1, self.role("督导")["requiredCount"])

    def test_source_category_can_add_by_blank_or_custom_name(self):
        self.configure_supervisor()
        state = self.command("add-template-role", {"flightNo": self.first_no,
                                                   "sourceRoleName": "督导", "roleName": ""})
        self.assertIn("督导2", self.template(state=state)["roleNames"])
        state = self.command("add-template-role", {"flightNo": self.first_no,
                                                   "sourceRoleName": "督导", "roleName": "临时督导"})
        self.assertEqual({**self.role("督导", state), "roleName": "临时督导"}, self.role("临时督导", state))
        self.assertIn("临时督导", self.template(state=state)["roleNames"])
        for person in state["people"]:
            self.assertEqual("督导" in person["eligibleRoleNames"], "临时督导" in person["eligibleRoleNames"])

    def test_suffixes_are_unique_across_the_catalog(self):
        self.configure_supervisor()
        self.command("add-template-role", {"flightNo": self.second_no, "roleName": "督导2"})
        existing = deepcopy(self.role("督导2"))
        qualifications = {p["id"]: "督导2" in p["eligibleRoleNames"] for p in self.workspace.snapshot()["people"]}

        state = self.command("add-template-role", {"flightNo": self.first_no, "roleName": "督导"})
        self.assertIn("督导3", self.template(state=state)["roleNames"])
        self.assertNotIn("督导2", self.template(state=state)["roleNames"])
        self.assertEqual(existing, self.role("督导2", state))
        self.assertEqual(qualifications, {p["id"]: "督导2" in p["eligibleRoleNames"] for p in state["people"]})
        state = self.command("add-template-role", {"flightNo": self.first_no,
                                                   "sourceRoleName": "督导", "roleName": ""})
        self.assertIn("督导4", self.template(state=state)["roleNames"])

    def test_numbered_source_names_continue_natural_numbering(self):
        state = self.command("add-template-role", {"flightNo": self.first_no, "roleName": "接机1"})
        self.assertIn("接机3", self.template(state=state)["roleNames"])
        self.assertNotIn("接机12", self.template(state=state)["roleNames"])
        self.command("add-template-role", {"flightNo": self.first_no, "roleName": "督导"})
        state = self.command("add-template-role", {"flightNo": self.first_no, "roleName": "督导2"})
        self.assertIn("督导3", self.template(state=state)["roleNames"])
        self.assertNotIn("督导22", self.template(state=state)["roleNames"])

    def test_baggage_category_creates_independent_continuous_role_without_special_times(self):
        self.command("save-role", {"roleName": "大件行李拦截", "requiredCount": 1,
                                  "workMode": "timed", "concurrent": False})
        flight = self.template()
        override = next(r for r in flight["roles"] if r["roleName"] == "大件行李拦截")
        override.update(workModeOverride="timed", workStart="10:10", workEnd="11:30")
        self.command("save-flight-template", flight)
        state = self.command("add-template-role", {"flightNo": self.first_no,
                                                   "sourceRoleName": "大件行李拦截", "roleName": ""})
        name = "大件行李拦截2"
        self.assertEqual("continuous", self.role(name, state)["workMode"])
        self.assertFalse(self.role(name, state)["concurrent"])
        new_override = next(r for r in self.template(state=state)["roles"] if r["roleName"] == name)
        self.assertEqual(override["requiredCount"], new_override["requiredCount"])
        self.assertIsNone(new_override["workModeOverride"])
        self.assertIsNone(new_override["workStart"])
        self.assertIsNone(new_override["workEnd"])
        for person in state["people"]:
            self.assertEqual("大件行李拦截" in person["eligibleRoleNames"], name in person["eligibleRoleNames"])

    def test_readding_an_existing_catalog_role_does_not_regrant_qualifications(self):
        self.configure_supervisor()
        flight = self.template()
        flight["roleNames"].remove("督导")
        self.command("save-flight-template", flight)
        before = self.workspace.backup()["state"]

        state = self.command("add-template-role", {"flightNo": self.first_no,
                                                   "sourceRoleName": "接机1", "roleName": "督导"})

        self.assertIn("督导", self.template(state=state)["roleNames"])
        self.assertNotIn("督导2", self.template(state=state)["roleNames"])
        self.assertEqual(before["configuration"], state["configuration"])
        self.assertEqual(before["people"], state["people"])
        self.assertEqual(before["plans"], self.workspace.backup()["state"]["plans"])

    def test_new_custom_name_without_source_stays_unconfigured_and_unqualified(self):
        state = self.command("add-template-role", {"flightNo": self.first_no, "roleName": "自定义新增岗位"})
        role = self.role("自定义新增岗位", state)
        self.assertIsNone(role["requiredCount"])
        self.assertFalse(role["concurrent"])
        self.assertTrue(all("自定义新增岗位" not in p["eligibleRoleNames"] for p in state["people"]))
        self.assertIn("自定义新增岗位", self.template(state=state)["roleNames"])
        self.assertNotIn("自定义新增岗位", self.template(self.second_no, state)["roleNames"])

    def test_unknown_source_rejects_the_complete_transaction(self):
        before = self.workspace.backup()
        with self.assertRaises(WorkspaceError):
            self.command("add-template-role", {"flightNo": self.first_no,
                                               "sourceRoleName": "不存在的分类", "roleName": "新名称"})
        self.assertEqual(before, self.workspace.backup())

    def test_invalid_addition_inputs_reject_without_partial_writes(self):
        before = self.workspace.backup()
        for payload in (
            {"flightNo": self.first_no, "roleName": ""},
            {"flightNo": self.first_no, "roleName": None},
            {"flightNo": self.first_no, "roleName": 2},
            {"flightNo": self.first_no, "roleName": "新岗位", "sourceRoleName": None},
            {"flightNo": self.first_no, "roleName": "新岗位", "sourceRoleName": ["督导"]},
            {"flightNo": "不存在的航班", "roleName": "新岗位"},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(WorkspaceError):
                    self.command("add-template-role", payload)
                self.assertEqual(before, self.workspace.backup())

    def test_excel_empty_device_restart_and_actual_solve_preserve_new_row_behavior(self):
        selected = self.configure_supervisor()
        self.command("save-role", {"roleName": "接机1", "requiredCount": 2,
                                  "workMode": "arrival", "concurrent": False,
                                  "qualifiedMemberIds": selected})
        flight = self.template()
        flight["roleNames"] = ["督导", "接机1"]
        flight["roles"] = [r for r in flight["roles"] if r["roleName"] == "督导"]
        self.command("save-flight-template", flight)
        old_plans = deepcopy(self.workspace.backup()["state"]["plans"])
        other_flight = self.template(self.second_no)

        self.command("add-template-role", {"flightNo": self.first_no,
                                          "sourceRoleName": "督导", "roleName": ""})
        flight = self.template()
        flight["roles"] = [{**r, "requiredCount": 1} if r["roleName"] == "督导2" else r for r in flight["roles"]]
        self.command("save-flight-template", flight)
        self.assertEqual(old_plans, self.workspace.backup()["state"]["plans"])
        self.assertEqual(other_flight, self.template(self.second_no))

        payload = import_complete_configuration(self.workspace.export_complete_configuration(self.plan_id))
        device_path = Path(self.directory.name) / "second-device.sqlite3"
        device = Workbench(device_path)
        device.execute(0, "import-complete-configuration", payload)
        device = Workbench(device_path)
        state = device.snapshot()
        self.assertEqual(self.template(), self.template(state=state))
        self.assertEqual({"exclusiveSameFlight": False, **self.role("督导2")},
                         {"exclusiveSameFlight": False, **self.role("督导2", state)})
        disabled = next(p for p in state["people"] if p["name"] == "停用督导测试人员")
        self.assertFalse(disabled["active"])
        self.assertIn("督导2", disabled["eligibleRoleNames"])

        state = device.execute(state["revision"], "new-plan", {"serviceDate": "2026-09-03", "flightNos": [self.first_no]})
        plan_id = state["selectedPlanId"]
        day = deepcopy(state["plans"][plan_id]["day"])
        qualified_names = {p["name"] for p in state["people"] if p["active"] and "督导2" in p["eligibleRoleNames"]}
        day["memberAvailability"] = [
            {"memberName": name, "participating": name in qualified_names,
             "availableFrom": None, "availableUntil": None}
            for name in day["roster"]
        ]
        state = device.execute(state["revision"], "save-input", {"day": day}, plan_id)
        state = device.execute(state["revision"], "solve", {}, plan_id)
        plan = state["plans"][plan_id]
        self.assertTrue(plan["valid"], plan["issues"])
        tasks = {t["roleId"]: t for t in plan["schedule"]["tasks"]}
        self.assertEqual({"督导", "督导2", "接机1"}, set(tasks))
        self.assertEqual(2, tasks["督导"]["requiredCount"])
        self.assertEqual(1, tasks["督导2"]["requiredCount"])
        self.assertEqual(2, tasks["接机1"]["requiredCount"])
        self.assertEqual(5, len(plan["draft"]))
        assignments = {role: {a["memberId"] for a in plan["draft"] if a["taskId"] == task["id"]}
                       for role, task in tasks.items()}
        self.assertTrue(assignments["督导2"] <= assignments["接机1"], "新增督导行保留兼任性质，能与接机同时安排")
        self.assertEqual(old_plans, self.workspace.backup()["state"]["plans"])


if __name__ == "__main__":
    unittest.main()
