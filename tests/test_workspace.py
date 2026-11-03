import tempfile
import unittest
from pathlib import Path

from gate_schedule.workbench import Workbench, WorkspaceError


class WorkspaceTests(unittest.TestCase):
    def test_position_first_configuration_is_atomic_and_reused_without_mutating_existing_day(self):
        ids = []
        for name in ("甲", "乙", "丙"):
            state = self.command("save-person", {"name": name, "eligibleRoleNames": ["登机主验"]})
            ids.append(next(p["id"] for p in state["people"] if p["name"] == name))
        state = self.command("save-role", {"roleName": "接机1", "requiredCount": 2, "qualifiedMemberIds": ids[:2]})
        self.assertEqual(["登机主验", "接机1"], next(p for p in state["people"] if p["id"] == ids[0])["eligibleRoleNames"])
        state = self.command("new-plan", {"serviceDate": "2026-08-02"})
        plan_id = state["selectedPlanId"]
        original = state["plans"][plan_id]
        self.assertEqual(2, next(r["requiredCount"] for r in original["day"]["roleConfigs"] if r["roleName"] == "接机1"))
        self.command("save-role", {"roleName": "接机1", "requiredCount": 1, "qualifiedMemberIds": [ids[2]]})
        self.assertEqual(original, self.workspace.snapshot()["plans"][plan_id])
        state = self.command("apply-config", {}, plan_id)
        self.assertEqual(1, next(r["requiredCount"] for r in state["plans"][plan_id]["day"]["roleConfigs"] if r["roleName"] == "接机1"))
        before = self.workspace.backup()
        with self.assertRaises(WorkspaceError):
            self.command("save-role", {"roleName": "接机1", "qualifiedMemberIds": [ids[0], "unknown"]})
        self.assertEqual(before, self.workspace.backup())
        restored = Workbench(Path(self.directory.name) / "restored-config.sqlite3")
        restored.restore(0, self.workspace.backup())
        self.assertEqual(self.workspace.snapshot()["configuration"], restored.snapshot()["configuration"])
        self.assertEqual(self.workspace.snapshot()["people"], restored.snapshot()["people"])

    def test_older_v2_backup_gets_empty_demand_defaults_and_person_name_edit_preserves_roles(self):
        self.command("save-person", {"name": "甲", "eligibleRoleNames": ["自定义岗"]})
        old = self.workspace.backup()
        old["state"].pop("configuration", None)
        state = self.workspace.restore(old["state"]["revision"], old)
        self.assertIn("自定义岗", [r["roleName"] for r in state["configuration"]["roleConfigs"]])
        person = state["people"][0]
        state = self.command("save-person", {"id": person["id"], "name": "甲改名", "active": True})
        self.assertEqual(["自定义岗"], state["people"][0]["eligibleRoleNames"])

    def test_configuration_rejects_invalid_selection_and_restore_atomically(self):
        self.command("save-person", {"name": "甲", "eligibleRoleNames": ["接机1"]})
        person_id = self.workspace.snapshot()["people"][0]["id"]
        before = self.workspace.backup()
        for payload in [
            {"roleName": "接机1", "requiredCount": -1},
            {"roleName": "接机1", "requiredCount": 1.5},
            {"roleName": "接机1", "qualifiedMemberIds": [person_id, person_id]},
            {"roleName": "新增岗", "requiredCount": 2, "qualifiedMemberIds": ["unknown"]},
        ]:
            with self.subTest(payload=payload), self.assertRaises(WorkspaceError):
                self.command("save-role", payload)
            self.assertEqual(before, self.workspace.backup())
        from copy import deepcopy
        broken = deepcopy(before)
        broken["state"]["configuration"]["roleConfigs"][0]["requiredCount"] = -1
        with self.assertRaises(WorkspaceError):
            self.workspace.restore(before["state"]["revision"], broken)
        self.assertEqual(before, self.workspace.backup())

    def test_loading_configuration_clears_preview_but_preserves_adopted_snapshot(self):
        plan_id, people = self.complete_plan()
        self.command("solve", {}, plan_id)
        adopted = self.command("adopt", {}, plan_id)["plans"][plan_id]["adopted"]
        self.command("solve", {}, plan_id)
        self.command("save-role", {"roleName": "接机1", "requiredCount": 1, "qualifiedMemberIds": [people[0]["id"]]})
        self.command("save-config", {"rotationEnabled": False, "rotationMinutes": 90, "transitionMinutes": 10})
        plan = self.command("apply-config", {}, plan_id)["plans"][plan_id]
        self.assertEqual(adopted, plan["adopted"])
        self.assertEqual([], plan["draft"])
        self.assertIsNone(plan["preview"])
        self.assertFalse(plan["rotationEnabled"])
        self.assertEqual(90, plan["rotationMinutes"])
        self.assertEqual(10, plan["day"]["transitionMinutes"])
        self.assertTrue(self.workspace.export(plan_id).startswith(b"PK"))

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "workspace.sqlite3"
        self.workspace = Workbench(self.path)

    def tearDown(self):
        self.directory.cleanup()

    def command(self, action, payload=None, plan_id=None):
        return self.workspace.execute(self.workspace.snapshot()["revision"], action, payload or {}, plan_id)

    def complete_plan(self):
        for name in ("甲", "乙", "丙"):
            self.command("save-person", {"name": name, "eligibleRoleNames": ["接机1"], "active": True})
        state = self.command("new-plan", {"serviceDate": "2026-08-02", "roleNames": ["接机1"]})
        plan_id = state["selectedPlanId"]
        day = state["plans"][plan_id]["day"]
        day["flights"] = [{"id": "f1", "flightNo": "AK151", "windowStart": "23:05", "windowEnd": "次日00:05"}]
        day["roleConfigs"] = [{"roleName": "接机1", "requiredCount": 2}]
        day["cells"] = [{"id": "c1", "flightId": "f1", "flightNo": "AK151", "roleName": "接机1", "rawValue": "", "internalNames": [], "externalLabels": [], "semantics": "empty", "workStart": "23:05", "workEnd": "次日00:05", "fixedMemberNames": [], "requiredCountOverride": None}]
        day["memberAvailability"] = [{"memberName": person["name"], "participating": True, "availableFrom": "22:00", "availableUntil": "次日02:00"} for person in state["people"]]
        self.command("save-input", {"day": day}, plan_id)
        return plan_id, self.workspace.snapshot()["people"]

    def test_new_person_blank_plan_partial_draft_survives_restart_but_cannot_be_adopted(self):
        plan_id, people = self.complete_plan()
        plan = self.workspace.snapshot()["plans"][plan_id]
        task_id = plan["schedule"]["tasks"][0]["id"]
        self.command("assign", {"assignments": [{"taskId": task_id, "memberId": people[0]["id"]}]}, plan_id)
        restored = Workbench(self.path).snapshot()["plans"][plan_id]
        self.assertEqual(1, len(restored["draft"]))
        self.assertFalse(restored["valid"])
        saved=self.command("adopt", {}, plan_id)['plans'][plan_id]
        self.assertTrue(saved['adopted']['issues'])
        self.assertEqual(restored['draft'],saved['adopted']['assignments'])

    def test_preview_is_not_adoption_and_latest_manual_result_is_formal_after_restore(self):
        plan_id, people = self.complete_plan()
        state = self.command("solve", {}, plan_id)
        plan = state["plans"][plan_id]
        self.assertTrue(plan["valid"], plan["issues"])
        self.assertIsNone(plan["adopted"])
        task_id = plan["schedule"]["tasks"][0]["id"]
        assigned = [{"taskId": task_id, "memberId": person["id"]} for person in people[1:]]
        self.command("assign", {"assignments": assigned}, plan_id)
        state = self.command("adopt", {}, plan_id)
        self.assertEqual(assigned, state["plans"][plan_id]["adopted"]["assignments"])
        other = Workbench(Path(self.directory.name) / "restored.sqlite3")
        other.restore(other.snapshot()["revision"], self.workspace.backup())
        self.assertEqual(assigned, other.snapshot()["plans"][plan_id]["adopted"]["assignments"])

    def test_stale_write_and_failed_disk_write_do_not_replace_formal_state(self):
        stale = self.workspace.snapshot()["revision"]
        self.command("save-person", {"name": "甲", "eligibleRoleNames": [], "active": True})
        with self.assertRaises(WorkspaceError):
            self.workspace.execute(stale, "save-person", {"name": "乙", "eligibleRoleNames": [], "active": True})
        before = self.workspace.snapshot()
        from unittest.mock import patch
        with patch.object(self.workspace.store, "compare_and_save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.command("save-person", {"name": "丙", "eligibleRoleNames": [], "active": True})
        self.assertEqual(before, self.workspace.snapshot())

    def test_new_input_cannot_mutate_formal_snapshot_and_unknown_backup_is_refused(self):
        plan_id, _ = self.complete_plan()
        self.command("solve", {}, plan_id)
        state = self.command("adopt", {}, plan_id)
        formal = state["plans"][plan_id]["adopted"]
        day = state["plans"][plan_id]["day"]
        day["roleConfigs"][0]["requiredCount"] = 3
        state = self.command("save-input", {"day": day}, plan_id)
        self.assertEqual(formal, state["plans"][plan_id]["adopted"])
        self.assertFalse(state["plans"][plan_id]["valid"])
        with self.assertRaises(WorkspaceError):
            self.workspace.restore(state["revision"], {"formatVersion": 999, "state": {}})
        self.assertEqual(state, self.workspace.snapshot())

    def test_refresh_renames_stable_person_once_and_disabled_person_stops_participating(self):
        plan_id, people = self.complete_plan()
        self.command("solve", {}, plan_id)
        formal = self.command("adopt", {}, plan_id)["plans"][plan_id]["adopted"]
        self.command("save-person", {**people[0], "name": "甲新名"})
        self.command("save-person", {**people[1], "active": False})
        plan = self.command("refresh-people", {}, plan_id)["plans"][plan_id]
        self.assertNotIn("甲", plan["day"]["roster"])
        self.assertEqual(people[0]["id"], plan["memberIds"]["甲新名"])
        self.assertFalse(next(a for a in plan["day"]["memberAvailability"] if a["memberName"] == "乙")["participating"])
        self.assertEqual(formal, plan["adopted"])

    def test_excel_import_preserves_explicit_qualifications_and_new_roster(self):
        from gate_schedule.excel_io import export_imported_day, imported_day_from_dict, import_day
        plan_id, _ = self.complete_plan()
        day = self.workspace.snapshot()["plans"][plan_id]["day"]
        content = export_imported_day(imported_day_from_dict(day))
        self.assertEqual(tuple(day["roster"]), import_day(content, day["serviceDate"]).roster)
        imported = self.command("import-plan", {"serviceDate": day["serviceDate"], "content": content})
        restored = imported["plans"][imported["selectedPlanId"]]["day"]
        self.assertEqual(day["memberConfigs"], restored["memberConfigs"])

    def test_shared_baggage_matrix_ignores_zero_demand_flight_without_duplicate_occupancy(self):
        from gate_schedule.excel_io import imported_day_from_dict
        from gate_schedule.imported_schedule import imported_day_to_scenario
        from gate_schedule.projections import task_matrix, member_paths
        from gate_schedule.solver import solve
        plan_id, _ = self.complete_plan()
        day = self.workspace.snapshot()["plans"][plan_id]["day"]
        day["roleNames"] = ["大件行李拦截"]
        day["roleConfigs"] = [{"roleName": "大件行李拦截", "requiredCount": 1}]
        day["memberConfigs"] = [{"memberName": n, "eligibleRoleNames": day["roleNames"]} for n in day["roster"]]
        base = day["flights"][0]
        day["flights"] = [{**base, "id": f"f{i}", "flightNo": f"AK15{i}"} for i in range(3)]
        day["cells"] = [{**day["cells"][0], "id": f"c{i}", "flightId": f"f{i}", "flightNo": f"AK15{i}", "roleName": "大件行李拦截", "requiredCountOverride": 0 if i == 2 else None} for i in range(3)]
        scenario, issues = imported_day_to_scenario(imported_day_from_dict(day), 60, True)
        self.assertEqual([], issues)
        result = solve(scenario)
        matrix = task_matrix(scenario, result.assignments)["大件行李拦截"]
        self.assertEqual({"AK150", "AK151"}, set(matrix))
        self.assertEqual(1, len(scenario.tasks))
        self.assertEqual(1, sum(map(len, member_paths(scenario, result.assignments).values())))

    def test_manual_choices_and_automatic_assignments_use_explicit_role_qualification(self):
        plan_id, people = self.complete_plan()
        day = self.workspace.snapshot()["plans"][plan_id]["day"]
        day["memberConfigs"][0]["eligibleRoleNames"] = []
        state = self.command("save-input", {"day": day}, plan_id)
        task = state["plans"][plan_id]["schedule"]["tasks"][0]
        self.assertEqual({p["id"] for p in people[1:]}, set(task["qualifiedMemberIds"]))
        state = self.command("solve", {}, plan_id)
        self.assertEqual({p["id"] for p in people[1:]}, {a["memberId"] for a in state["plans"][plan_id]["draft"]})
        forged = [{"taskId": task["id"], "memberId": p["id"]} for p in people[:2]]
        state = self.command("assign", {"assignments": forged}, plan_id)
        self.assertIn("ineligible", {i["code"] for i in state["plans"][plan_id]["issues"]})
        saved=self.command("adopt", {}, plan_id)['plans'][plan_id]
        self.assertIn('ineligible',{i['code'] for i in saved['adopted']['issues']})

    def test_missing_flight_role_cell_is_not_silently_accepted_as_zero_demand(self):
        plan_id, _ = self.complete_plan()
        day = self.workspace.snapshot()["plans"][plan_id]["day"]
        day["flights"].append({"id": "f2", "flightNo": "TR121", "windowStart": "23:30", "windowEnd": "次日00:55"})
        state = self.command("save-input", {"day": day}, plan_id)
        self.assertIn("missing_imported_cell", {i["code"] for i in state["plans"][plan_id]["issues"]})
        with self.assertRaises(WorkspaceError):
            self.command("solve", {}, plan_id)

    def test_actual_sqlite_write_failure_and_concurrent_writers_keep_one_consistent_state(self):
        from contextlib import contextmanager
        from concurrent.futures import ThreadPoolExecutor
        import sqlite3
        from threading import Barrier
        from unittest.mock import patch
        before = self.workspace.snapshot()
        original_connection = self.workspace.store._connection

        @contextmanager
        def readonly_connection():
            with original_connection() as connection:
                connection.execute("PRAGMA query_only=ON")
                yield connection

        with patch.object(self.workspace.store, "_connection", readonly_connection):
            with self.assertRaises(sqlite3.OperationalError):
                self.command("save-person", {"name": "未写入", "eligibleRoleNames": []})
        self.assertEqual(before, self.workspace.snapshot())
        barrier = Barrier(2)

        def writer(name):
            workbench = Workbench(self.path)
            original_save = workbench.store.compare_and_save

            def synchronized_save(*args):
                barrier.wait(timeout=5)
                return original_save(*args)

            with patch.object(workbench.store, "compare_and_save", synchronized_save):
                try:
                    workbench.execute(0, "save-person", {"name": name, "eligibleRoleNames": []})
                    return "saved"
                except WorkspaceError:
                    return "stale"

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(writer, ("甲", "乙")))
        self.assertCountEqual(["saved", "stale"], results)
        state = self.workspace.snapshot()
        self.assertEqual(1, state["revision"])
        self.assertEqual(1, len(state["people"]))


if __name__ == "__main__":
    unittest.main()
