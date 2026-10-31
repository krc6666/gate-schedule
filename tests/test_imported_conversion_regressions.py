import tempfile
import unittest
from dataclasses import replace
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import Workbook

from gate_schedule.domain import Assignment, ImportedMemberAvailability, ImportedRemark
from gate_schedule.excel_io import import_day
from gate_schedule.imported_schedule import imported_day_to_scenario
from gate_schedule.io import scenario_from_dict, scenario_to_dict
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule
from gate_schedule.web import create_app


def workbook_bytes(headers, rows, configuration):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "保障明细"
    sheet.append(["转换回归"])
    sheet.append(["岗位", *headers])
    for row in rows:
        sheet.append(row)
    for title, entries in configuration.items():
        sheet = workbook.create_sheet(title)
        for entry in entries:
            sheet.append(entry)
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def handoff_workbook_bytes():
    return workbook_bytes(
        ["A / 0800-1000", "B / 0930-1100"],
        [["接机1", "甲", ""], ["登机主验", "", "乙"]],
        {
            "岗位时间": [["航班号", "岗位", "开始时间", "结束时间"], ["A", "接机1", "08:00", "09:00"], ["B", "登机主验", "09:30", "11:00"]],
            "岗位配置": [["岗位", "同时需要人数"], ["接机1", 1], ["登机主验", 1]],
            "岗位人数例外": [["航班号", "岗位", "同时需要人数"], ["A", "登机主验", 0], ["B", "接机1", 0]],
            "人员配置": [["人员", "可做岗位"], ["甲", "接机1\n登机主验"], ["乙", "登机主验"]],
            "当日出勤": [["人员", "参加排班", "到岗时间", "离岗时间"], ["甲", "是", "08:00", "11:00"], ["乙", "是", "08:00", "11:00"]],
        },
    )


def idle_candidate_workbook_bytes():
    return workbook_bytes(
        ["CX937 / 1010-1130"], [["接机1", "甲"], ["特服", "乙"]],
        {
            "岗位时间": [["航班号", "岗位", "开始时间", "结束时间"], ["CX937", "接机1", "10:10", "10:40"]],
            "岗位配置": [["岗位", "同时需要人数"], ["接机1", 1], ["特服", 0]],
            "人员配置": [["人员", "可做岗位"], ["甲", "接机1"], ["乙", "接机1"]],
            "当日出勤": [["人员", "参加排班", "到岗时间", "离岗时间"], ["甲", "否", None, None], ["乙", "是", "10:00", "12:00"]],
        },
    )


class ImportedConversionRegressionTests(unittest.TestCase):
    def test_imported_solution_keeps_task_identity_after_restart_and_backup_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "schedule.sqlite3"
            with TestClient(create_app(database_path=database)) as client:
                response = client.post("/api/import-excel?serviceDate=2026-08-02", content=handoff_workbook_bytes())
                self.assertEqual(200, response.status_code, response.text)
                preview = response.json()
                response = client.post("/api/imported-day/solve")
                self.assertEqual(200, response.status_code, response.text)
                solved = response.json()
                self.assertTrue(solved["accepted"], solved)
                confirmed = client.post("/api/imported-day/confirm-schedule", json={
                    "day": {key: preview[key] for key in ("cells", "roleConfigs", "memberConfigs", "memberAvailability", "extraOccupancies", "remarksReviewed", "transitionMinutes")},
                    "assignments": solved["assignments"],
                })
                self.assertEqual(200, confirmed.status_code, confirmed.text)
                saved = client.get("/api/schedule").json()
                backup = client.get("/api/backup").json()
            with TestClient(create_app(database_path=database)) as restarted:
                self.assertEqual(saved, restarted.get("/api/schedule").json())
            restored_database = Path(directory) / "restored.sqlite3"
            with TestClient(create_app(database_path=restored_database)) as restored:
                response = restored.post("/api/restore-backup", json=backup)
                self.assertEqual(200, response.status_code, response.text)
                self.assertEqual(saved, restored.get("/api/schedule").json())
            with TestClient(create_app(database_path=restored_database)) as restarted:
                self.assertEqual(saved, restarted.get("/api/schedule").json())

    def test_imported_handoff_uses_release_boundary_and_rejects_early_manual_support(self):
        day = import_day(handoff_workbook_bytes(), "2026-08-02")
        day = replace(day, member_availability=tuple(
            replace(entry, available_until="10:00") if entry.member_name == "乙" else entry
            for entry in day.member_availability
        ))
        scenario, issues = imported_day_to_scenario(day)
        self.assertEqual([], issues)
        result = solve(scenario)
        self.assertEqual("OPTIMAL", result.status)
        self.assertEqual([], validate_schedule(scenario, result.assignments))
        boarding = sorted((task for task in scenario.tasks if task.service_stage == "boarding"), key=lambda task: task.start)
        self.assertEqual([(570, 600), (600, 660)], [(task.start, task.end) for task in boarding])
        assigned = {assignment.task_id: assignment.member_id for assignment in result.assignments}
        self.assertEqual(["乙", "甲"], [assigned[task.id] for task in boarding])
        tampered = tuple(
            Assignment(assignment.task_id, "甲") if assignment.task_id == boarding[0].id else assignment
            for assignment in result.assignments
        )
        self.assertIn("cross_flight_before_release", {issue.code for issue in validate_schedule(scenario, tampered)})
        self.assertEqual(scenario, scenario_from_dict(scenario_to_dict(scenario)))

    def test_zero_demand_reference_cell_does_not_exclude_a_qualified_present_person(self):
        day = import_day(idle_candidate_workbook_bytes(), "2026-08-02")
        scenario, issues = imported_day_to_scenario(day)
        self.assertEqual([], issues)
        self.assertEqual(["乙"], [member.name for member in scenario.members])
        result = solve(scenario)
        self.assertEqual("OPTIMAL", result.status)
        self.assertEqual((Assignment("imported-cell-3-2", "乙"),), result.assignments)
        self.assertEqual([], validate_schedule(scenario, result.assignments))
        invalid_scenarios = {
            "ineligible": replace(scenario, members=(replace(scenario.members[0], eligible_role_ids=frozenset()),)),
            "member_unavailable": replace(scenario, members=(replace(scenario.members[0], available_from=11 * 60),)),
            "coverage_shortfall": replace(scenario, tasks=(replace(scenario.tasks[0], required_count=2),)),
        }
        for expected_code, invalid in invalid_scenarios.items():
            with self.subTest(expected_code=expected_code):
                self.assertIn(expected_code, {issue.code for issue in validate_schedule(invalid, result.assignments)})

    def test_remark_only_person_uses_explicit_qualification_and_attendance(self):
        day = import_day(idle_candidate_workbook_bytes(), "2026-08-02")
        day = replace(day,
            cells=(day.cells[0], replace(day.cells[1], raw_value="", internal_names=(), semantics="empty")),
            remarks=(ImportedRemark("arrival", "10:00到岗：乙", ("乙",), "10:00"),),
        )
        scenario, issues = imported_day_to_scenario(day)
        self.assertEqual([], issues)
        result = solve(scenario)
        self.assertEqual("OPTIMAL", result.status)
        self.assertEqual((Assignment("imported-cell-3-2", "乙"),), result.assignments)
        self.assertEqual([], validate_schedule(scenario, result.assignments))

    def test_unconfigured_zero_demand_reference_names_do_not_block_present_people(self):
        day = import_day(idle_candidate_workbook_bytes(), "2026-08-02")
        day = replace(day, cells=(day.cells[0], replace(
            day.cells[1], raw_value="乙/丙", internal_names=("乙", "丙"), semantics="simultaneous",
        )))
        for attendance in ((), (ImportedMemberAvailability("丙", False),)):
            with self.subTest(attendance=attendance):
                candidate = replace(day, member_availability=(*day.member_availability, *attendance))
                scenario, issues = imported_day_to_scenario(candidate)
                self.assertEqual([], issues)
                self.assertEqual(["乙"], [member.name for member in scenario.members])
                result = solve(scenario)
                self.assertEqual("OPTIMAL", result.status)
                self.assertEqual([], validate_schedule(scenario, result.assignments))


if __name__ == "__main__":
    unittest.main()
