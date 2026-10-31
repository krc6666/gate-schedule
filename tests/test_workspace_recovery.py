from copy import deepcopy
from dataclasses import replace
from io import BytesIO
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from gate_schedule.domain import Assignment, parse_minutes
from gate_schedule.io import normalize_saved_scenario, scenario_from_dict
from gate_schedule.server import create_server_app
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule
from gate_schedule.workbench import Workbench, WorkspaceError
from tests.test_flight_handoff import handoff_payload
from tests.test_phase_zero import feasible_scenario


class RecoveryTests(unittest.TestCase):
    def test_unrelated_old_import_is_not_attached_to_same_date_formal_result(self):
        from gate_schedule.io import scenario_to_dict
        from gate_schedule.excel_io import import_day, imported_day_to_dict
        from tests.test_excel_io import day_20260814_bytes
        scenario = feasible_scenario()
        day = imported_day_to_dict(import_day(day_20260814_bytes(), scenario.service_date))
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workbench(Path(directory) / "test.sqlite3")
            state = workspace.restore(0, {"formatVersion": 1, "state": {"scenario": scenario_to_dict(scenario), "assignments": [{"taskId": a.task_id, "memberId": a.member_id} for a in solve(scenario).assignments], "importedDay": day}})
            self.assertEqual(2, len(state["plans"]))
            formal = state["plans"][state["selectedPlanId"]]
            self.assertEqual([], formal["day"]["flights"])
            self.assertEqual(len(scenario.tasks), len(formal["formalSchedule"]["tasks"]))
            draft = next(p for p in state["plans"].values() if not p["adopted"])
            self.assertEqual(day["flights"], draft["day"]["flights"])

    def test_legacy_unsplit_mapping_preserves_people_intervals_and_is_idempotent(self):
        raw = handoff_payload()
        raw["members"][1]["availableUntil"] = "11:00"
        assignments = [{"taskId": "a-arrival-task", "memberId": "a-arrival"}, {"taskId": "b-boarding-task", "memberId": "b-boarder"}]
        normalized, migrated, mapping = normalize_saved_scenario(raw, assignments)
        self.assertEqual(["b-boarding-task::segment-1", "b-boarding-task::segment-2"], mapping["b-boarding-task"])
        self.assertEqual(3, len(migrated))
        self.assertEqual([], validate_schedule(scenario_from_dict(normalized), tuple(Assignment(a["taskId"], a["memberId"]) for a in migrated)))
        again, repeated, _ = normalize_saved_scenario(normalized, migrated)
        self.assertEqual(normalized, again)
        self.assertEqual(migrated, repeated)
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workbench(Path(directory) / "test.sqlite3")
            state = workspace.restore(0, {"formatVersion": 1, "state": {"scenario": raw, "assignments": assignments}})
            plan = state["plans"][state["selectedPlanId"]]
            self.assertEqual(migrated, plan["adopted"]["assignments"])
            first = workspace.backup()
            workspace.restore(state["revision"], first)
            self.assertEqual(first["state"]["plans"], workspace.backup()["state"]["plans"])
            self.assertEqual("正式保障班表", load_workbook(BytesIO(workspace.export(plan["id"]))).worksheets[0].title)
            invalid = deepcopy(assignments)
            invalid.append(assignments[0])
            before = workspace.backup()
            with self.assertRaises(WorkspaceError):
                workspace.restore(workspace.snapshot()["revision"], {"formatVersion": 1, "state": {"scenario": raw, "assignments": invalid}})
            self.assertEqual(before, workspace.backup())
            raw["tasks"][0]["lockedMemberIds"] = ["b-boarder"]
            with self.assertRaises(WorkspaceError):
                workspace.restore(workspace.snapshot()["revision"], {"formatVersion": 1, "state": {"scenario": raw, "assignments": assignments}})
            self.assertEqual(before, workspace.backup())

    def test_api_conflicts_disk_failure_and_corrupt_database_recovery_preserve_original(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.sqlite3"
            with TestClient(create_server_app(database_path=path)) as client:
                payload = {"expectedRevision": 0, "action": "save-person", "payload": {"name": "甲", "eligibleRoleNames": []}}
                self.assertEqual(200, client.post("/api/workspace/command", json=payload).status_code)
                before = client.get("/api/workspace").json()
                for broken in ([], {"formatVersion": 2, "state": []}):
                    self.assertEqual(422, client.post(f"/api/workspace/restore?expectedRevision={before['revision']}", json=broken).status_code)
                self.assertEqual(422, client.post(f"/api/workspace/import?serviceDate=2026-08-02&expectedRevision={before['revision']}", content=b"not an xlsx").status_code)
                self.assertEqual(409, client.post("/api/workspace/command", json=payload).status_code)
                payload["expectedRevision"] = before["revision"]
                payload["payload"]["name"] = "乙"
                with patch("gate_schedule.storage.LocalStateStore.compare_and_save", side_effect=sqlite3.OperationalError("disk full")):
                    self.assertEqual(503, client.post("/api/workspace/command", json=payload).status_code)
                    self.assertEqual(503, client.post(f"/api/workspace/restore?expectedRevision={before['revision']}", json=client.get("/api/workspace/backup").json()).status_code)
                self.assertEqual(before, client.get("/api/workspace").json())
            corrupt = Path(directory) / "corrupt.sqlite3"
            original = b"not a database: retain original"
            corrupt.write_bytes(original)
            with TestClient(create_server_app(database_path=corrupt)) as client:
                self.assertEqual(200, client.get("/").status_code)
                self.assertEqual(503, client.get("/api/workspace").status_code)
                self.assertTrue(client.get("/api/workspace").json()["recoveryRequired"])
                self.assertEqual(original, client.get("/api/workspace/raw-database").content)
                self.assertEqual(503, client.post("/api/workspace/import?serviceDate=2026-08-02&expectedRevision=0", content=b"bad").status_code)
            self.assertEqual(original, corrupt.read_bytes())
            empty_state = Path(directory) / "empty-state.sqlite3"
            workspace = Workbench(empty_state)
            workspace.store.compare_and_save(workspace.KEY, 0, {})
            with TestClient(create_server_app(database_path=empty_state)) as client:
                self.assertEqual(503, client.get("/api/workspace").status_code, "损坏的空状态不能静默变成全新工作台")

    def test_cross_midnight_release_walk_and_post_edit_constraints_are_independent(self):
        raw = handoff_payload()
        # Translate 09:00 to 23:30, preserving the known feasible handoff across midnight.
        offset = 870
        for flight in raw["flights"]:
            for key in ("windowStart", "windowEnd"):
                flight[key] = parse_minutes(flight[key]) + offset
        for member in raw["members"]:
            for key in ("availableFrom", "availableUntil"):
                if key in member:
                    member[key] = parse_minutes(member[key]) + offset
        for task in raw["tasks"]:
            for key in ("start", "end"):
                task[key] = parse_minutes(task[key]) + offset
        scenario = scenario_from_dict(raw)
        legal = solve(scenario).assignments
        self.assertEqual([], validate_schedule(scenario, legal))
        boarding = sorted((t for t in scenario.tasks if t.service_stage == "boarding"), key=lambda t: t.start)
        bad = tuple(Assignment(a.task_id, "a-arrival") if a.task_id == boarding[0].id else a for a in legal)
        self.assertIn("cross_flight_before_release", {i.code for i in validate_schedule(scenario, bad)})
        assigned = next(a for a in legal if a.task_id == boarding[-1].id)
        member = next(m for m in scenario.members if m.id == assigned.member_id)
        mutations = [
            ("ineligible", replace(scenario, members=tuple(replace(m, eligible_role_ids=frozenset()) if m.id == member.id else m for m in scenario.members))),
            ("member_unavailable", replace(scenario, members=tuple(replace(m, available_until=boarding[-1].end-1) if m.id == member.id else m for m in scenario.members))),
            ("insufficient_transition", replace(scenario, minimum_transition_minutes=31)),
            ("lock_missing", replace(scenario, tasks=tuple(replace(t, locked_member_ids=("b-boarder",)) if t.id == boarding[-1].id else t for t in scenario.tasks))),
            ("overlap", replace(scenario, tasks=tuple(replace(t, end=boarding[-1].start+1) if t.service_stage == "arrival" else t for t in scenario.tasks))),
        ]
        for code, changed in mutations:
            with self.subTest(code=code):
                self.assertIn(code, {i.code for i in validate_schedule(changed, legal)})
        self.assertIn("coverage_shortfall", {i.code for i in validate_schedule(scenario, legal[:-1])})

    def test_next_day_rotation_pool_and_shared_projection_reference_tampering_are_rejected(self):
        original = feasible_scenario()
        scenario = replace(original,
            flights=tuple(replace(f, window_start=f.window_start+900, window_end=f.window_end+900) for f in original.flights),
            members=tuple(replace(m, available_from=m.available_from+900) for m in original.members),
            tasks=tuple(replace(t, start=t.start+900, end=t.end+900) for t in original.tasks),
            rotations=tuple(replace(r, segment_start=r.segment_start+900, segment_end=r.segment_end+900) for r in original.rotations))
        legal = solve(scenario).assignments
        self.assertEqual([], validate_schedule(scenario, legal))
        bad = tuple(Assignment(a.task_id, "baggage-alternate") if a.task_id == "cx-baggage-2" else a for a in legal)
        self.assertIn("rotation_candidate", {i.code for i in validate_schedule(scenario, bad)})
        changed = replace(scenario, tasks=tuple(replace(t, covered_flight_ids=("unknown",)) if t.id == "cx-baggage-2" else t for t in scenario.tasks))
        self.assertIn("invalid_covered_flights", {i.code for i in validate_schedule(changed, legal)})


if __name__ == "__main__":
    unittest.main()
