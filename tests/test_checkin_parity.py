import unittest
from dataclasses import replace
from threading import Event

from tests import test_workspace as fixtures
from gate_schedule.solver import solve
from gate_schedule.domain import Assignment, draft_blocking_issues
from gate_schedule.validator import validate_schedule
from gate_schedule.workbench import WorkspaceError


class ParityTests(unittest.TestCase):
    def test_schedule_columns_follow_service_time_without_reordering_saved_input(self):
        from copy import deepcopy
        from gate_schedule.workbench import schedule_data
        plan_id, people = self.complete_plan()
        day = self.workspace.snapshot()["plans"][plan_id]["day"]
        day["flights"] = [
            {"id": "next", "flightNo": "TW616", "windowStart": "次日00:10", "windowEnd": "次日01:10"},
            {"id": "late", "flightNo": "AK151", "windowStart": "23:05", "windowEnd": "次日00:05"},
            {"id": "early", "flightNo": "CX937", "windowStart": "10:10", "windowEnd": "11:30"},
            {"id": "same", "flightNo": "KE166", "windowStart": "10:10", "windowEnd": "11:20"},
        ]
        template = deepcopy(day["cells"][0])
        day["cells"] = [{**template, "id": f["id"], "flightId": f["id"], "flightNo": f["flightNo"], "workStart": f["windowStart"], "workEnd": f["windowEnd"], "requiredCountOverride": 1} for f in day["flights"]]
        for attendance in day["memberAvailability"]:
            attendance.update(availableFrom="10:00", availableUntil="次日02:00")
        self.command("save-input", {"day": day}, plan_id)
        plan = self.command("edit-cell", {"cellId": "early", "text": "甲"}, plan_id)["plans"][plan_id]
        before = self.workspace.backup()
        expected = ["CX937", "KE166", "AK151", "TW616"]
        self.assertEqual(expected, [f["flightNo"] for f in plan["schedule"]["flights"]])
        self.assertEqual(expected, plan["flightColumns"])
        scenario, _ = self.workspace._scenario(before["state"]["plans"][plan_id])
        projected = schedule_data(scenario, (Assignment("imported-early", people[0]["id"]),))
        self.assertEqual(expected, [f["flightNo"] for f in projected["flights"]])
        self.assertEqual(["甲"], projected["matrix"]["接机1"]["CX937"][0]["members"])
        self.assertEqual(["TW616", "AK151", "CX937", "KE166"], [f["flightNo"] for f in self.workspace.snapshot()["plans"][plan_id]["day"]["flights"]])
        self.assertEqual(before, self.workspace.backup(), "显示排序不能写回日输入、草稿或历史")
        for cell_id, name in (("same", "乙"), ("late", "甲"), ("next", "甲")):
            self.command("edit-cell", {"cellId": cell_id, "text": name}, plan_id)
        formal = self.command("adopt", {}, plan_id)["plans"][plan_id]
        self.assertEqual(expected, [f["flightNo"] for f in formal["formalSchedule"]["flights"]])
        # Missing role details still show the correct flight headers.
        for cell in day["cells"]:
            cell.update(workStart=None, workEnd=None)
        plan = self.command("save-input", {"day": day}, plan_id)["plans"][plan_id]
        self.assertFalse(plan["schedule"]["tasks"])
        self.assertEqual(expected, plan["flightColumns"])
        from unittest.mock import patch
        with patch.object(self.workspace, "_scenario", return_value=(None, [])):
            self.assertEqual(expected, self.workspace.snapshot()["plans"][plan_id]["flightColumns"])

    def test_progress_reports_full_pipeline_and_finishes_only_after_commit(self):
        from gate_schedule.schedule_runs import ScheduleRuns
        from unittest.mock import patch
        plan_id,_=self.complete_plan()
        before=self.workspace.backup()
        entered,release=Event(),Event()
        original=self.workspace._solve
        def blocked(*args,**kwargs):
            args[-1].stage('计算人员安排')
            entered.set();release.wait(5)
            return original(*args,**kwargs)
        runs=ScheduleRuns(self.workspace)
        with patch.object(self.workspace,'_solve',blocked):
            run=runs.start(before['state']['revision'],'solve',{},plan_id)
            try:
                self.assertTrue(entered.wait(2))
                progress=runs.get(run['id'])
                self.assertGreater(len(progress['steps']),4)
                self.assertTrue(any(s['status']=='pending' for s in progress['steps']))
                self.assertLess(progress['percent'],100)
                self.assertEqual(before,self.workspace.backup())
            finally:release.set();runs.join(run['id'],10)
        done=runs.get(run['id'])
        self.assertEqual('completed',done['status'])
        self.assertEqual(100,done['percent'])
        self.assertTrue(all(s['status']=='completed' for s in done['steps']))

    setUp = fixtures.WorkspaceTests.setUp
    tearDown = fixtures.WorkspaceTests.tearDown
    command = fixtures.WorkspaceTests.command
    complete_plan = fixtures.WorkspaceTests.complete_plan

    def test_http_progress_remains_responsive_and_stop_preserves_workspace(self):
        from fastapi.testclient import TestClient
        from gate_schedule.server import create_server_app
        from gate_schedule.workbench import Workbench
        from unittest.mock import patch
        from time import monotonic, sleep
        plan_id, _ = self.complete_plan()
        before = self.workspace.backup()
        entered, release = Event(), Event()
        original = Workbench._solve
        def blocked(workspace, *args, **kwargs):
            entered.set()
            release.wait(3)
            return original(workspace, *args, **kwargs)
        with TestClient(create_server_app(database_path=self.path)) as client, patch.object(Workbench, "_solve", blocked):
            response = client.post("/api/workspace/runs", json={"expectedRevision": before["state"]["revision"], "action": "solve", "planId": plan_id})
            self.assertEqual(200, response.status_code)
            run_id = response.json()["id"]
            self.assertTrue(entered.wait(1))
            self.assertEqual(200, client.get("/api/workspace").status_code)
            self.assertEqual("running", client.get(f"/api/workspace/runs/{run_id}").json()["status"])
            self.assertEqual(409, client.post(f"/api/workspace/runs/{run_id}/stop", json={"keep": True}).status_code)
            self.assertEqual(200, client.post(f"/api/workspace/runs/{run_id}/stop", json={"keep": False}).status_code)
            release.set()
            deadline = monotonic() + 3
            while monotonic() < deadline:
                status = client.get(f"/api/workspace/runs/{run_id}").json()
                if status["status"] != "running": break
                sleep(.01)
            self.assertEqual("cancelled", status["status"])
        self.assertEqual(before, self.workspace.backup())

    def test_archive_uses_previous_formal_role_and_preserves_prior_assignments(self):
        from copy import deepcopy
        plan_id, _ = self.complete_plan()
        plan = self.command("solve", {}, plan_id)["plans"][plan_id]
        previous_ids = {a["memberId"] for a in plan["draft"]}
        day = deepcopy(plan["day"]); day["serviceDate"] = "2026-08-04"
        state = self.command("archive-next", {"day": day, "rotationMinutes": 90, "rotationEnabled": False}, plan_id)
        current = state["plans"][state["selectedPlanId"]]
        self.assertEqual("2026-08-02", current["historyReferenceDate"])
        self.assertEqual(90, current["rotationMinutes"])
        self.assertFalse(current["rotationEnabled"])
        self.assertEqual(1, len(previous_ids & {a["memberId"] for a in current["draft"]}))
        self.assertEqual(plan["draft"], state["plans"][plan_id]["adopted"]["assignments"])

    def test_template_configuration_persists_and_roundtrips_without_touching_history(self):
        from gate_schedule.excel_io import import_complete_configuration
        from gate_schedule.workbench import Workbench
        plan_id, _ = self.complete_plan()
        before = self.workspace.backup()["state"]["plans"]
        for number in ("AK151", "KE166"):
            self.command("save-flight-template", {"flightNo": number, "windowStart": "10:00", "windowEnd": "11:00", "roles": [{"roleName": "接机1", "requiredCount": 1}]})
        self.command("copy-flight-roles", {"sourceNo": "AK151", "targetNos": ["KE166"]})
        self.assertEqual(before, self.workspace.backup()["state"]["plans"])
        exported = self.workspace.export_complete_configuration(plan_id)
        data = import_complete_configuration(exported)
        library = self.workspace.snapshot()["flightLibrary"]
        self.assertEqual(library, data["flightLibrary"])
        other = Workbench(self.path.parent / "templates.sqlite3")
        other.execute(0, "import-complete-configuration", data)
        self.assertEqual(library, other.snapshot()["flightLibrary"])
        state = self.command("new-plan", {"serviceDate": "2026-08-04", "flightNos": ["KE166"]})
        day = state["plans"][state["selectedPlanId"]]["day"]
        self.assertEqual("KE166", day["flights"][0]["flightNo"])
        self.assertEqual(1, next(c for c in day["cells"] if c["roleName"] == "接机1")["requiredCountOverride"])
        self.assertEqual([], day["cells"][0]["fixedMemberNames"])
        before = self.workspace.backup()
        with self.assertRaises(ValueError):
            self.command("save-flight-template", {"flightNo": "BAD", "windowStart": "23:00", "windowEnd": "01:00"})
        self.assertEqual(before, self.workspace.backup())

    def test_background_keep_and_concurrent_write_protection(self):
        from gate_schedule.schedule_runs import ScheduleRuns
        from unittest.mock import patch
        plan_id, _ = self.complete_plan()
        for stale in (False, True):
            entered, release = Event(), Event()
            original = self.workspace._solve
            def blocked(*args, **kwargs):
                result = original(*args, **kwargs)
                entered.set()
                release.wait(5)
                return result
            runs = ScheduleRuns(self.workspace)
            with patch.object(self.workspace, "_solve", blocked):
                run = runs.start(self.workspace.snapshot()["revision"], "solve", {}, plan_id)
                self.assertTrue(entered.wait(3))
                self.assertTrue(runs.get(run["id"])["canKeep"])
                if stale:
                    self.command("save-person", {"name": "新增", "active": True})
                runs.stop(run["id"], True)
                release.set()
                runs.join(run["id"], 10)
            self.assertEqual("failed" if stale else "completed", runs.get(run["id"])["status"])
            if stale:
                self.assertIn("更新", runs.get(run["id"])["message"])
            else:
                self.assertTrue(self.workspace.snapshot()["plans"][plan_id]["valid"])
    def test_shortfall_keeps_safe_draft_but_never_formal(self):
        plan_id, _ = self.complete_plan()
        day = self.workspace.snapshot()["plans"][plan_id]["day"]
        day["roleConfigs"][0]["requiredCount"] = 4
        self.command("save-input", {"day": day}, plan_id)
        state = self.command("solve", {}, plan_id)
        plan = state["plans"][plan_id]
        self.assertEqual(3, len(plan["draft"]))
        self.assertFalse(plan["valid"])
        self.assertEqual({"coverage_shortfall"}, {i["code"] for i in plan["issues"]})
        before = self.workspace.backup()
        saved = self.command("adopt", {}, plan_id)
        self.assertEqual({'coverage_shortfall'}, {i['code'] for i in saved['plans'][plan_id]['adopted']['issues']})
        scenario, _ = self.workspace._scenario(plan)
        self.assertFalse(draft_blocking_issues(validate_schedule(scenario, tuple(Assignment(a["taskId"], a["memberId"]) for a in plan["draft"]))))
        broken = replace(scenario, members=tuple(replace(m, eligible_role_ids=()) for m in scenario.members))
        self.assertTrue(draft_blocking_issues(validate_schedule(broken, (Assignment(scenario.tasks[0].id, scenario.members[0].id),))))

    def test_previous_role_preference_does_not_create_shortfall(self):
        plan_id, _ = self.complete_plan()
        scenario, _ = self.workspace._scenario(self.workspace.snapshot()["plans"][plan_id])
        result = solve(scenario)
        previous = {(a.member_id, scenario.tasks[0].role_id) for a in result.assignments}
        rotated = solve(scenario, allow_partial=True, previous_roles=previous)
        self.assertFalse(validate_schedule(scenario, rotated.assignments))
        self.assertLess(len({a.member_id for a in result.assignments} & {a.member_id for a in rotated.assignments}), 2)

    def test_background_stop_without_result_does_not_write(self):
        from gate_schedule.schedule_runs import ScheduleRuns
        plan_id, _ = self.complete_plan()
        before = self.workspace.backup()
        entered, release = Event(), Event()
        from unittest.mock import patch
        original = self.workspace._solve
        def blocked(*args, **kwargs):
            entered.set()
            release.wait(5)
            return original(*args, **kwargs)
        runs = ScheduleRuns(self.workspace)
        with patch.object(self.workspace, "_solve", blocked):
            job = runs.start(before["state"]["revision"], "solve", {}, plan_id)
            self.assertTrue(entered.wait(2))
            runs.stop(job["id"], False)
            release.set()
            runs.join(job["id"], 10)
        self.assertEqual("cancelled", runs.get(job["id"])["status"])
        self.assertLess(runs.get(job["id"])["percent"], 100)
        self.assertTrue(any(step["status"] == "stopped" for step in runs.get(job["id"])["steps"]))
        self.assertEqual(before, self.workspace.backup())


if __name__ == "__main__":
    unittest.main()
