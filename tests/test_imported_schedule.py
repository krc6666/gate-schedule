import unittest
from dataclasses import replace
import tempfile
from pathlib import Path
from io import BytesIO

from openpyxl import Workbook
from openpyxl import load_workbook

from fastapi.testclient import TestClient

from gate_schedule.domain import Assignment, Flight, ImportedCell, ImportedDay, ImportedFlight, ImportedMemberAvailability, ImportedMemberConfig, ImportedOccupancy, ImportedRoleConfig, Member, Scenario, Task
from gate_schedule.excel_io import import_day
from gate_schedule.imported_schedule import imported_day_to_scenario
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule
from gate_schedule.web import create_app
from tests.test_excel_io import day_20260814_bytes


class ImportedScheduleTests(unittest.TestCase):
    def test_api_solves_a_complete_imported_day_and_honors_fixed_name(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "保障明细"
        sheet.append(["服务日"])
        sheet.append(["岗位", "CX937 / 1010-1130"])
        sheet.append(["接机1", "刘楠/黄潇蒙"])
        times = workbook.create_sheet("岗位时间")
        times.append(["航班号", "岗位", "开始时间", "结束时间"])
        times.append(["CX937", "接机1", "10:10", "10:40"])
        roles = workbook.create_sheet("岗位配置")
        roles.append(["岗位", "同时需要人数"])
        roles.append(["接机1", 1])
        members = workbook.create_sheet("人员配置")
        members.append(["人员", "可做岗位"])
        members.append(["刘楠", "接机1"])
        members.append(["黄潇蒙", "接机1"])
        attendance = workbook.create_sheet("当日出勤")
        attendance.append(["人员", "参加排班", "到岗时间", "离岗时间"])
        attendance.append(["刘楠", "是", "10:00", "12:00"])
        attendance.append(["黄潇蒙", "是", "10:00", "12:00"])
        fixed = workbook.create_sheet("固定人员")
        fixed.append(["航班号", "岗位", "固定人员"])
        fixed.append(["CX937", "接机1", "刘楠"])
        stream = BytesIO()
        workbook.save(stream)
        with tempfile.TemporaryDirectory() as directory:
            client = TestClient(create_app(database_path=Path(directory) / "schedule.sqlite3"))
            try:
                self.assertEqual(200, client.post("/api/import-excel?serviceDate=2026-08-14", content=stream.getvalue()).status_code)
                response = client.post("/api/imported-day/solve")
                self.assertEqual(200, response.status_code, response.text)
                self.assertTrue(response.json()["accepted"], response.json())
                self.assertEqual("刘楠", response.json()["assignments"][0]["memberId"])
                preview = client.post("/api/import-excel?serviceDate=2026-08-14", content=stream.getvalue()).json()
                bad = client.post("/api/imported-day/confirm-schedule", json={
                    "day": {key: preview[key] for key in ("cells", "roleConfigs", "memberConfigs", "memberAvailability", "extraOccupancies", "remarksReviewed", "transitionMinutes")},
                    "assignments": [{"taskId": "imported-cell-3-2", "memberId": "黄潇蒙"}],
                })
                self.assertEqual(422, bad.status_code)
                valid = client.post("/api/imported-day/confirm-schedule", json={
                    "day": {key: preview[key] for key in ("cells", "roleConfigs", "memberConfigs", "memberAvailability", "extraOccupancies", "remarksReviewed", "transitionMinutes")},
                    "assignments": response.json()["assignments"],
                })
                self.assertTrue(valid.json()["valid"], valid.text)
                self.assertEqual("automatic", client.get("/api/schedule").json()["assignmentSource"])
                restarted = TestClient(create_app(database_path=Path(directory) / "schedule.sqlite3"))
                try:
                    self.assertEqual("automatic", restarted.get("/api/schedule").json()["assignmentSource"])
                    exported = load_workbook(BytesIO(restarted.get("/api/export-excel").content))
                    self.assertIn("自动排班结果", exported.sheetnames)
                finally:
                    restarted.close()
            finally:
                client.close()

    def test_manual_change_after_imported_solution_is_exported_after_restart(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "保障明细"
        sheet.append(["服务日"])
        sheet.append(["岗位", "CX937 / 1010-1130"])
        sheet.append(["接机1", "刘楠/黄潇蒙"])
        for title, rows in (
            ("岗位时间", (("航班号", "岗位", "开始时间", "结束时间"), ("CX937", "接机1", "10:10", "10:40"))),
            ("岗位配置", (("岗位", "同时需要人数"), ("接机1", 1))),
            ("人员配置", (("人员", "可做岗位"), ("刘楠", "接机1"), ("黄潇蒙", "接机1"))),
            ("当日出勤", (("人员", "参加排班", "到岗时间", "离岗时间"), ("刘楠", "是", "10:00", "12:00"), ("黄潇蒙", "是", "10:00", "12:00"))),
        ):
            configuration = workbook.create_sheet(title)
            for row in rows:
                configuration.append(row)
        stream = BytesIO()
        workbook.save(stream)
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "schedule.sqlite3"
            with TestClient(create_app(database_path=database)) as client:
                preview = client.post("/api/import-excel?serviceDate=2026-08-14", content=stream.getvalue()).json()
                solved = client.post("/api/imported-day/solve").json()
                self.assertTrue(solved["accepted"], solved)
                confirmed = client.post("/api/imported-day/confirm-schedule", json={
                    "day": {key: preview[key] for key in ("cells", "roleConfigs", "memberConfigs", "memberAvailability", "extraOccupancies", "remarksReviewed", "transitionMinutes")},
                    "assignments": solved["assignments"],
                })
                self.assertEqual(200, confirmed.status_code, confirmed.text)
                assignment = solved["assignments"][0]
                replacement = next(name for name in ("刘楠", "黄潇蒙") if name != assignment["memberId"])
                changed_assignments = [{"taskId": assignment["taskId"], "memberId": replacement}]
                saved = client.post("/api/manual-assignments", json={"assignments": changed_assignments})
                self.assertEqual(200, saved.status_code, saved.text)
            with TestClient(create_app(database_path=database)) as restarted:
                restored = restarted.get("/api/schedule").json()
                self.assertEqual("manual", restored["assignmentSource"])
                self.assertEqual(changed_assignments, restored["assignments"])
                exported = restarted.get("/api/export-excel")
                self.assertEqual(200, exported.status_code)
                result = load_workbook(BytesIO(exported.content))
                self.assertIn("自动排班结果", result.sheetnames)
                self.assertEqual(replacement, result["自动排班结果"].cell(2, 6).value)

    def test_export_does_not_attach_unrelated_same_date_demo_assignments(self):
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(create_app(database_path=Path(directory) / "schedule.sqlite3")) as client:
                current = client.get("/api/schedule").json()
                imported = client.post(
                    f"/api/import-excel?serviceDate={current['serviceDate']}", content=day_20260814_bytes()
                )
                self.assertEqual(200, imported.status_code, imported.text)
                confirmed = client.post("/api/imported-day/confirm", json={"cells": []})
                self.assertEqual(200, confirmed.status_code, confirmed.text)
                for source in ("manual", "automatic"):
                    with self.subTest(source=source):
                        if source == "manual":
                            saved = client.post("/api/manual-assignments", json={"assignments": current["assignments"]})
                        else:
                            preview = client.post("/api/solve").json()
                            saved = client.post("/api/solve/confirm", json={"previewId": preview["previewId"]})
                        self.assertEqual(200, saved.status_code, saved.text)
                        self.assertEqual(source, saved.json()["assignmentSource"])
                        exported = client.get("/api/export-excel")
                        self.assertEqual(200, exported.status_code)
                        result = load_workbook(BytesIO(exported.content))
                        self.assertNotIn("自动排班结果", result.sheetnames)
                        self.assertEqual("CX937 / 1100-1225", result.worksheets[0].cell(2, 2).value)

    def test_api_refuses_to_solve_when_day_facts_are_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            client = TestClient(create_app(database_path=Path(directory) / "schedule.sqlite3"))
            try:
                client.post("/api/import-excel?serviceDate=2026-08-14", content=day_20260814_bytes())
                response = client.post("/api/imported-day/solve")
                self.assertEqual(422, response.status_code)
                self.assertFalse(response.json()["valid"])
                self.assertTrue(response.json()["issues"])
            finally:
                client.close()

    def test_missing_explicit_inputs_are_reported_without_guessing(self):
        day = import_day(day_20260814_bytes(), "2026-08-14")
        scenario, issues = imported_day_to_scenario(day)
        self.assertEqual((), scenario.tasks)
        codes = {issue.code for issue in issues}
        self.assertIn("missing_work_time", codes)
        self.assertIn("missing_required_count", codes)

    def test_explicit_inputs_create_tasks_members_and_fixed_locks(self):
        day = import_day(day_20260814_bytes(), "2026-08-14")
        cells = []
        for cell in day.cells:
            if cell.role_name == "接机1" and cell.flight_no == "CX937":
                cells.append({"id": cell.id, "rawValue": cell.raw_value, "workStart": "10:10", "workEnd": "10:40", "fixedMemberNames": [cell.internal_names[0]]})
            else:
                cells.append({"id": cell.id, "rawValue": cell.raw_value, "workStart": "", "workEnd": ""})
        from gate_schedule.excel_io import update_imported_day

        day = update_imported_day(
            day,
            cells,
            role_configs=[{"roleName": "接机1", "requiredCount": 1}],
            member_configs=[
                {"memberName": name, "eligibleRoleNames": ["接机1"]}
                for name in ("刘楠", "黄潇蒙", "邓颖", "李杨")
            ],
            member_availability=[
                {"memberName": "刘楠", "participating": True, "availableFrom": "10:00", "availableUntil": "12:00"},
                *[
                    {"memberName": name, "participating": True, "availableFrom": "10:00", "availableUntil": "12:00"}
                    for name in ("黄潇蒙", "邓颖", "李杨")
                ],
            ],
        )
        scenario, issues = imported_day_to_scenario(day)
        self.assertNotIn("missing_member_config", {issue.code for issue in issues if issue.task_id == "imported-cell-3-2"})
        self.assertNotIn("missing_member_availability", {issue.code for issue in issues if issue.task_id == "imported-cell-3-2"})
        self.assertEqual(1, len(scenario.tasks))
        self.assertEqual(("刘楠",), scenario.tasks[0].locked_member_ids)
        self.assertEqual({"刘楠", "黄潇蒙", "邓颖", "李杨"}, {member.id for member in scenario.members})

    def test_baggage_windows_merge_and_split_into_adjustable_rotation_segments(self):
        day = ImportedDay(
            "2026-08-14", "服务08.14",
            (ImportedFlight("f1", "CX937", "08:00", "10:00"), ImportedFlight("f2", "KE166", "09:00", "11:00")),
            ("大件行李拦截",),
            (
                ImportedCell("c1", "大件行李拦截", "f1", "CX937", "甲/乙", ("甲", "乙"), (), "rotation", "08:30", "10:00"),
                ImportedCell("c2", "大件行李拦截", "f2", "KE166", "乙/丙", ("乙", "丙"), (), "rotation", "09:00", "10:30"),
            ), (),
            (ImportedRoleConfig("大件行李拦截", 1),),
            tuple(ImportedMemberConfig(name, ("大件行李拦截",)) for name in ("甲", "乙", "丙")),
            tuple(ImportedMemberAvailability(name, True, "08:00", "12:00") for name in ("甲", "乙", "丙")),
        )
        scenario, issues = imported_day_to_scenario(day, 60, True)
        self.assertEqual([], issues)
        self.assertEqual([(480, 540), (540, 600), (600, 660)], [(task.start, task.end) for task in scenario.tasks])
        self.assertEqual(1, len({rotation.group_id for rotation in scenario.rotations}))
        result = solve(scenario)
        self.assertIn(result.status, {"OPTIMAL", "FEASIBLE"})
        names = {assignment.task_id: assignment.member_id for assignment in result.assignments}
        self.assertNotEqual(names["imported-baggage-1-1"], names["imported-baggage-1-2"])
        self.assertNotEqual(names["imported-baggage-1-2"], names["imported-baggage-1-3"])
        self.assertEqual([], validate_schedule(scenario, result.assignments))

    def test_baggage_task_does_not_require_role_work_time(self):
        day = ImportedDay(
            "2026-08-14", "test",
            (ImportedFlight("f1", "CX937", "08:00", "09:00"),),
            ("大件行李拦截",),
            (ImportedCell("c1", "大件行李拦截", "f1", "CX937", "甲/乙", ("甲", "乙"), (), "rotation", None, None),),
            (),
            (ImportedRoleConfig("大件行李拦截", 1),),
            (ImportedMemberConfig("甲", ("大件行李拦截",)), ImportedMemberConfig("乙", ("大件行李拦截",))),
            (ImportedMemberAvailability("甲", True, "08:00", "12:00"), ImportedMemberAvailability("乙", True, "08:00", "12:00")),
        )
        scenario, issues = imported_day_to_scenario(day)
        self.assertEqual([], issues)
        self.assertEqual([(480, 540)], [(task.start, task.end) for task in scenario.tasks])

    def test_baggage_fixed_person_covers_source_flight_window_and_rotation_clock_stays_anchored(self):
        day = ImportedDay(
            "2026-08-14", "test",
            (ImportedFlight("f1", "CX937", "08:00", "08:30"), ImportedFlight("f2", "KE166", "08:30", "11:00")),
            ("大件行李拦截",),
            (
                ImportedCell("c1", "大件行李拦截", "f1", "CX937", "甲", ("甲",), (), "single", "08:00", "08:30", fixed_member_names=("甲",)),
                ImportedCell("c2", "大件行李拦截", "f2", "KE166", "甲/乙", ("甲", "乙"), (), "rotation", "08:30", "11:00"),
            ), (),
            (ImportedRoleConfig("大件行李拦截", 1),),
            (ImportedMemberConfig("甲", ("大件行李拦截",)), ImportedMemberConfig("乙", ("大件行李拦截",))),
            (ImportedMemberAvailability("甲", True, "08:00", "12:00"), ImportedMemberAvailability("乙", True, "08:00", "12:00")),
        )
        scenario, issues = imported_day_to_scenario(day, 60, True)
        self.assertEqual([], issues)
        self.assertEqual([(480, 510), (510, 540), (540, 600), (600, 660)], [(task.start, task.end) for task in scenario.tasks])
        self.assertEqual([("甲",), (), (), ()], [task.locked_member_ids for task in scenario.tasks])
        self.assertEqual([0, 0, 1, 2], [rotation.rotation_index for rotation in scenario.rotations])
        result = solve(scenario)
        self.assertIn(result.status, {"OPTIMAL", "FEASIBLE"})
        names_by_id = {member.id: member.name for member in scenario.members}
        names = {item.task_id: names_by_id[item.member_id] for item in result.assignments}
        self.assertEqual(names["imported-baggage-1-1"], names["imported-baggage-1-2"])
        self.assertNotEqual(names["imported-baggage-1-2"], names["imported-baggage-1-3"])

    def test_explicit_occupancy_and_cross_flight_walk_buffer_are_hard_constraints(self):
        day = ImportedDay(
            "2026-08-14", "test", (ImportedFlight("f1", "A", "10:00", "11:00"), ImportedFlight("f2", "B", "11:00", "12:00")),
            ("接机1",),
            (ImportedCell("c1", "接机1", "f1", "A", "甲", ("甲",), (), "single", "10:00", "10:30"),
             ImportedCell("c2", "接机1", "f2", "B", "甲", ("甲",), (), "single", "10:40", "11:00")), (),
            (ImportedRoleConfig("接机1", 1),), (ImportedMemberConfig("甲", ("接机1",)),),
            (ImportedMemberAvailability("甲", True, "10:00", "12:00"),),
            (ImportedOccupancy("occ1", "登机准备", ("甲",), "10:15", "10:25"),), True, 15,
        )
        scenario, issues = imported_day_to_scenario(day)
        self.assertEqual([], issues)
        result = solve(scenario)
        self.assertEqual("INFEASIBLE", result.status)
        manual = (Assignment("imported-c1", "甲"), Assignment("imported-c2", "甲"), Assignment("occupancy-occ1-甲", "甲"))
        codes = {issue.code for issue in validate_schedule(scenario, manual)}
        self.assertIn("overlap", codes)
        self.assertIn("insufficient_transition", codes)

    def test_cross_flight_walk_buffer_rejects_a_previously_legal_result(self):
        day = ImportedDay(
            "2026-08-14", "test", (ImportedFlight("f1", "A", "10:00", "11:00"), ImportedFlight("f2", "B", "11:00", "12:00")),
            ("接机1",),
            (ImportedCell("c1", "接机1", "f1", "A", "甲", ("甲",), (), "single", "10:00", "10:30"),
             ImportedCell("c2", "接机1", "f2", "B", "甲", ("甲",), (), "single", "10:40", "11:00")), (),
            (ImportedRoleConfig("接机1", 1),), (ImportedMemberConfig("甲", ("接机1",)),),
            (ImportedMemberAvailability("甲", True, "10:00", "12:00"),), (), True, 0,
        )
        legal, issues = imported_day_to_scenario(day)
        self.assertEqual([], issues)
        result = solve(legal)
        self.assertEqual("OPTIMAL", result.status)
        self.assertEqual([], validate_schedule(legal, result.assignments))
        with_walk = replace(legal, minimum_transition_minutes=15)
        self.assertIn("insufficient_transition", {issue.code for issue in validate_schedule(with_walk, result.assignments)})


if __name__ == "__main__":
    unittest.main()
