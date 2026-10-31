import unittest
from dataclasses import replace
from datetime import time
from io import BytesIO
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from gate_schedule.excel_io import export_imported_day, import_day, update_imported_day, validate_imported_day
from gate_schedule.projections import flight_handoff_suggestions
from gate_schedule.web import create_app


def workbook_bytes(title, headers, rows):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "保障明细"
    sheet.append([title])
    sheet.append(["岗位", *headers])
    for row in rows:
        sheet.append(row)
    result = BytesIO()
    workbook.save(result)
    return result.getvalue()


def day_20260802_bytes():
    return workbook_bytes(
        "服务08.02",
        ["CX937 / 1010-1130", "KE0166 / 1100-1215"],
        [
            ["督导", "叶欢", "余雪莲"],
            ["接机1", "余雯雯/黄潇蒙", "邓颖"],
            ["接机2", "余雪妮", "余雪莲"],
            ["大件行李拦截", "杜乐乐/李梦/李杨", ""],
            ["登机人证核对/队伍秩序维护", "李梦/黄潇蒙", "余雯雯/黄潇蒙"],
            ["0910支援: 余雪莲、刘楠", "", ""],
            ["14:00到岗:余雯雯 16:00到岗:刘楠 黄潇蒙", "", ""],
        ],
    )


def day_20260814_bytes():
    return workbook_bytes(
        "服务08.14",
        ["CX937 / 1100-1225", "KE166 / 1100-1215"],
        [
            ["督导", "史成贺", "李杨"],
            ["接机1", "刘楠/黄潇蒙", "邓颖/李杨"],
            ["大件行李拦截", "李梦/陈丹丹", ""],
            ["登机人证核对/队伍秩序维护", "刘楠/值机支援", "陈丹丹/值机支援"],
            ["0910支援 ：邓颖、杜乐乐", "", ""],
        ],
    )


def day_20260802_with_work_times_bytes():
    workbook = load_workbook(BytesIO(day_20260802_bytes()))
    sheet = workbook.create_sheet("岗位时间")
    sheet.append(["航班号", "岗位", "开始时间", "结束时间"])
    sheet.append(["CX937", "接机1", time(10, 20), time(10, 35)])
    sheet.append(["KE0166", "登机人证核对/队伍秩序维护", time(11, 40), time(12, 10)])
    workbook.active = workbook.index(sheet)
    result = BytesIO()
    workbook.save(result)
    return result.getvalue()


def cell(day, role_name, flight_no):
    return next(item for item in day.cells if item.role_name == role_name and item.flight_no == flight_no)


class ExcelIoTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.client = TestClient(create_app(database_path=Path(self.directory.name) / "schedule.sqlite3"))

    def tearDown(self):
        self.client.close()
        self.directory.cleanup()

    def test_20260802_preserves_confirmed_slash_rules_and_unconfirmed_cells(self):
        day = import_day(day_20260802_bytes(), "2026-08-02")

        self.assertEqual(["CX937", "KE0166"], [flight.flight_no for flight in day.flights])
        self.assertEqual("simultaneous", cell(day, "接机1", "CX937").semantics)
        self.assertEqual("rotation", cell(day, "大件行李拦截", "CX937").semantics)
        self.assertEqual(("杜乐乐", "李梦", "李杨"), cell(day, "大件行李拦截", "CX937").internal_names)
        self.assertEqual("ambiguous", cell(day, "登机人证核对/队伍秩序维护", "CX937").semantics)
        self.assertEqual("support", day.remarks[0].kind)
        self.assertIsNone(day.remarks[0].time)
        self.assertEqual(("14:00", "16:00"), tuple(remark.time for remark in day.remarks[1:]))
        self.assertEqual(("刘楠", "黄潇蒙"), day.remarks[2].people)
        self.assertIn("ambiguous_slash", {issue.code for issue in validate_imported_day(day)})
        self.assertIsNone(cell(day, "接机1", "CX937").work_start)
        self.assertIsNone(cell(day, "接机1", "CX937").work_end)

    def test_work_times_import_export_and_reimport_without_changing_the_main_sheet(self):
        imported = import_day(day_20260802_with_work_times_bytes(), "2026-08-02")

        arrival = cell(imported, "接机1", "CX937")
        self.assertEqual("10:20", arrival.work_start)
        self.assertEqual("10:35", arrival.work_end)

        exported = export_imported_day(imported)
        workbook = load_workbook(BytesIO(exported), data_only=True)
        self.assertEqual("保障明细", workbook.active.title)
        self.assertIn("岗位时间", workbook.sheetnames)
        restored = import_day(exported, "2026-08-02")
        self.assertEqual("10:20", cell(restored, "接机1", "CX937").work_start)
        self.assertEqual("10:35", cell(restored, "接机1", "CX937").work_end)

    def test_work_time_must_have_both_ends_and_end_after_start(self):
        day = import_day(day_20260802_bytes(), "2026-08-02")
        target = cell(day, "接机1", "CX937")

        incomplete = update_imported_day(day, [{"id": target.id, "workStart": "10:20", "workEnd": ""}])
        invalid = update_imported_day(day, [{"id": target.id, "workStart": "25:00", "workEnd": "26:00"}])
        reversed_window = update_imported_day(day, [{"id": target.id, "workStart": "10:35", "workEnd": "10:20"}])

        self.assertIn("incomplete_work_time", {issue.code for issue in validate_imported_day(incomplete)})
        self.assertIn("invalid_work_time", {issue.code for issue in validate_imported_day(invalid)})
        self.assertIn("work_time_order", {issue.code for issue in validate_imported_day(reversed_window)})

    def test_confirmation_rejects_a_work_time_with_only_one_end(self):
        preview = self.client.post("/api/import-excel?serviceDate=2026-08-14", content=day_20260814_bytes()).json()
        target = next(
            item for item in preview["cells"] if item["roleName"] == "接机1" and item["flightNo"] == "CX937"
        )

        response = self.client.post(
            "/api/imported-day/confirm",
            json={"cells": [{"id": target["id"], "rawValue": target["rawValue"], "workStart": "10:20", "workEnd": ""}]},
        )

        self.assertEqual(422, response.status_code)
        self.assertIn("incomplete_work_time", {issue["code"] for issue in response.json()["issues"]})
        self.assertEqual(404, self.client.get("/api/imported-day").status_code)

    def test_saved_work_times_survive_restart(self):
        preview = self.client.post(
            "/api/import-excel?serviceDate=2026-08-02",
            content=day_20260802_with_work_times_bytes(),
        ).json()
        confirmations = [
            {"id": item["id"], "rawValue": item["rawValue"], "semantics": "simultaneous"}
            for item in preview["cells"]
            if item["semantics"] == "ambiguous"
        ]

        confirmed = self.client.post("/api/imported-day/confirm", json={"cells": confirmations})
        self.assertEqual(200, confirmed.status_code)
        restarted = TestClient(create_app(database_path=Path(self.directory.name) / "schedule.sqlite3"))
        try:
            restored = restarted.get("/api/imported-day").json()
        finally:
            restarted.close()

        restored_arrival = next(
            item for item in restored["cells"] if item["roleName"] == "接机1" and item["flightNo"] == "CX937"
        )
        self.assertEqual("10:20", restored_arrival["workStart"])
        self.assertEqual("10:35", restored_arrival["workEnd"])

    def test_20260814_keeps_value_support_out_of_internal_names_and_round_trips_excel(self):
        day = import_day(day_20260814_bytes(), "2026-08-14")
        imported_cell = cell(day, "登机人证核对/队伍秩序维护", "CX937")

        self.assertEqual("external", imported_cell.semantics)
        self.assertEqual(("刘楠",), imported_cell.internal_names)
        self.assertEqual(("值机支援",), imported_cell.external_labels)
        self.assertNotIn("值机支援", imported_cell.internal_names)
        exported = import_day(export_imported_day(day), "2026-08-14")
        exported_cell = cell(exported, "登机人证核对/队伍秩序维护", "CX937")
        self.assertEqual("刘楠/值机支援", exported_cell.raw_value)

    def test_handoff_suggestions_start_after_source_flight_and_skip_people_already_on_target_boarding(self):
        day = import_day(day_20260802_bytes(), "2026-08-02")

        suggestions = flight_handoff_suggestions(day)

        self.assertEqual(1, len(suggestions))
        suggestion = suggestions[0]
        self.assertEqual("CX937", suggestion.source_flight_no)
        self.assertEqual("KE0166", suggestion.target_flight_no)
        self.assertEqual("11:30", suggestion.suggested_from)
        self.assertEqual("12:15", suggestion.suggested_until)
        self.assertEqual(("余雪妮",), suggestion.candidate_names)
        self.assertEqual(("登机人证核对/队伍秩序维护",), suggestion.boarding_roles)

    def test_handoff_suggestions_require_overlapping_flights_and_remaining_target_window(self):
        day = import_day(day_20260802_bytes(), "2026-08-02")
        source, target = day.flights
        no_overlap = replace(
            day,
            flights=(
                replace(source, window_start="08:00", window_end="10:00"),
                replace(target, window_start="10:00", window_end="11:00"),
            ),
        )
        target_ends_at_release = replace(
            day,
            flights=(
                replace(source, window_start="08:00", window_end="10:00"),
                replace(target, window_start="09:30", window_end="10:00"),
            ),
        )

        self.assertEqual((), flight_handoff_suggestions(no_overlap))
        self.assertEqual((), flight_handoff_suggestions(target_ends_at_release))

    def test_import_preview_exposes_handoff_suggestions(self):
        response = self.client.post("/api/import-excel?serviceDate=2026-08-02", content=day_20260802_bytes())

        self.assertEqual(200, response.status_code)
        preview = response.json()
        suggestion = preview["flightHandoffSuggestions"][0]
        self.assertEqual("CX937", suggestion["sourceFlightNo"])
        self.assertEqual("KE0166", suggestion["targetFlightNo"])
        self.assertEqual("11:30", suggestion["suggestedFrom"])
        self.assertEqual(["余雪妮"], suggestion["candidateNames"])

        target_boarding = next(
            item for item in preview["cells"]
            if item["roleName"].startswith("登机") and item["flightNo"] == "KE0166"
        )
        changed = self.client.post(
            "/api/imported-day/validate",
            json={
                "cells": [
                    {
                        "id": target_boarding["id"],
                        "rawValue": "余雯雯/黄潇蒙/余雪妮",
                        "semantics": "simultaneous",
                    }
                ]
            },
        )

        self.assertEqual(200, changed.status_code)
        self.assertEqual([], changed.json()["flightHandoffSuggestions"])

    def test_saved_import_recomputes_handoff_suggestions_after_restart(self):
        preview = self.client.post("/api/import-excel?serviceDate=2026-08-02", content=day_20260802_bytes()).json()
        confirmations = [
            {"id": item["id"], "rawValue": item["rawValue"], "semantics": "simultaneous"}
            for item in preview["cells"]
            if item["semantics"] == "ambiguous"
        ]

        confirmed = self.client.post("/api/imported-day/confirm", json={"cells": confirmations})
        self.assertEqual(200, confirmed.status_code)
        expected = confirmed.json()["flightHandoffSuggestions"]
        restarted = TestClient(create_app(database_path=Path(self.directory.name) / "schedule.sqlite3"))
        try:
            restored = restarted.get("/api/imported-day")
        finally:
            restarted.close()

        self.assertEqual(200, restored.status_code)
        self.assertEqual(expected, restored.json()["flightHandoffSuggestions"])

    def test_confirmation_is_required_before_the_imported_day_can_be_saved(self):
        client = self.client

        preview = client.post("/api/import-excel?serviceDate=2026-08-02", content=day_20260802_bytes())
        rejected = client.post("/api/imported-day/confirm", json={"cells": []})

        self.assertEqual(200, preview.status_code)
        self.assertEqual(422, rejected.status_code)
        self.assertIn("ambiguous_slash", {issue["code"] for issue in rejected.json()["issues"]})
        self.assertEqual(404, client.get("/api/imported-day").status_code)

        confirmations = [
            {"id": item["id"], "rawValue": item["rawValue"], "semantics": "simultaneous"}
            for item in preview.json()["cells"]
            if item["semantics"] == "ambiguous"
        ]
        confirmed = client.post(
            "/api/imported-day/confirm",
            json={"cells": confirmations},
        )

        self.assertEqual(200, confirmed.status_code)
        self.assertEqual(200, client.get("/api/imported-day").status_code)
        exported = client.get("/api/export-excel")
        self.assertEqual(200, exported.status_code)
        self.assertEqual("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", exported.headers["content-type"])

    def test_cell_changes_are_reparsed_before_save(self):
        day = import_day(day_20260814_bytes(), "2026-08-14")
        support_cell = cell(day, "登机人证核对/队伍秩序维护", "CX937")

        changed = update_imported_day(
            day,
            [{"id": support_cell.id, "rawValue": "刘楠/陈丹丹", "semantics": "simultaneous"}],
        )

        changed_cell = cell(changed, "登机人证核对/队伍秩序维护", "CX937")
        self.assertEqual(("刘楠", "陈丹丹"), changed_cell.internal_names)
        self.assertEqual((), changed_cell.external_labels)
        self.assertEqual("simultaneous", changed_cell.semantics)

    def test_invalid_confirmation_does_not_replace_the_saved_imported_day(self):
        client = self.client
        preview = client.post("/api/import-excel?serviceDate=2026-08-02", content=day_20260802_bytes()).json()
        confirmations = [
            {"id": item["id"], "rawValue": item["rawValue"], "semantics": "simultaneous"}
            for item in preview["cells"]
            if item["semantics"] == "ambiguous"
        ]
        self.assertEqual(200, client.post("/api/imported-day/confirm", json={"cells": confirmations}).status_code)
        saved = client.get("/api/imported-day").json()
        first_cell = saved["cells"][0]

        rejected = client.post(
            "/api/imported-day/confirm",
            json={"cells": [{"id": first_cell["id"], "rawValue": "甲/乙", "semantics": "ambiguous"}]},
        )

        self.assertEqual(422, rejected.status_code)
        self.assertIn("ambiguous_slash", {issue["code"] for issue in rejected.json()["issues"]})
        self.assertEqual(first_cell["rawValue"], client.get("/api/imported-day").json()["cells"][0]["rawValue"])


if __name__ == "__main__":
    unittest.main()
