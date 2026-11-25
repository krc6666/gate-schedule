from copy import deepcopy
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from openpyxl import load_workbook

from gate_schedule.day_document import imported_day_from_dict
from gate_schedule.domain import Assignment, task_pair_conflict
from gate_schedule.excel_io import export_complete_configuration, import_complete_configuration, export_imported_day, import_day
from gate_schedule.flight_configuration import fill_day, with_role_membership
from gate_schedule.imported_schedule import imported_day_to_scenario
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule
from gate_schedule.manual_editing import candidate_options
from gate_schedule.workbench import Workbench
from tests.test_balance_configuration import configuration


def template_state():
    roles = [
        {"roleName": "接机2", "requiredCount": 1, "workModeOverride": None, "workStart": None,
         "workEnd": None, "exclusiveSameFlight": False},
        {"roleName": "登机主验", "requiredCount": 1, "workModeOverride": None, "workStart": None,
         "workEnd": None, "exclusiveSameFlight": False},
    ]
    return {
        "configuration": {"roleConfigs": [
            {"roleName": "接机2", "requiredCount": 1, "workMode": "arrival", "concurrent": False,
             "exclusiveSameFlight": False},
            {"roleName": "登机主验", "requiredCount": 1, "workMode": "boarding", "concurrent": False,
             "exclusiveSameFlight": False},
        ]},
        "flightLibrary": [
            {"flightNo": "CX937", "windowStart": "10:00", "windowEnd": "11:00",
             "roleNames": ["接机2", "登机主验"], "roles": [
                 deepcopy(roles[0]), {**deepcopy(roles[1]), "exclusiveSameFlight": True}]},
            {"flightNo": "KE0166", "windowStart": "12:00", "windowEnd": "13:00",
             "roleNames": ["接机2", "登机主验"], "roles": deepcopy(roles)},
        ],
    }


def complete_payload(*, dedicated_cx=True):
    data = configuration()
    day = data["day"]
    data["balanceSettings"].update(flightCountGap=None, priorityEnabled=False, priorityRoleNames=[], priorityAdjustments=[])
    day["roleNames"] = ["接机2", "登机主验"]
    day["roleConfigs"].append({"roleName": "接机2", "requiredCount": 1, "workMode": "arrival",
                               "concurrent": False, "exclusiveSameFlight": False})
    for person in day["memberConfigs"]:
        person["eligibleRoleNames"].append("接机2")
    for number, flight, cell in zip(("CX937", "KE0166"), day["flights"], day["cells"]):
        flight["flightNo"] = number
        cell["flightNo"] = number
        cell["roleName"] = "登机主验"
        day["cells"].append({"id": f"arrival-{flight['id']}", "flightId": flight["id"],
                             "flightNo": flight["flightNo"], "roleName": "接机2", "rawValue": "",
                             "internalNames": [], "externalLabels": [], "semantics": "empty"})
    templates = template_state()["flightLibrary"]
    if not dedicated_cx:
        templates[0]["roles"][1]["exclusiveSameFlight"] = False
    data["flightLibrary"] = templates
    data["handoffPreferences"] = [{"flightNo": "*", "arrivalRole": "接机2", "boardingRoles": ["登机主验"],
                                     "enabled": True, "ordered": True, "targetFlightScopes": {"登机主验": "same-flight"}}]
    day["flowPreferences"] = deepcopy(data["handoffPreferences"])
    return data


def prepared_scenario(state=None):
    state = deepcopy(state or template_state())
    day = {
        "serviceDate": "2026-08-24", "title": "按航班专岗测试", "releasePolicy": "task",
        "attendanceMode": "duty", "transitionMinutes": 0, "remarksReviewed": True,
        "remarks": [], "extraOccupancies": [], "phaseHandoffs": [], "flights": [], "cells": [],
        "roleNames": ["接机2", "登机主验"], "roster": ["甲", "乙"],
        "roleConfigs": deepcopy(state["configuration"]["roleConfigs"]),
        "memberConfigs": [
            {"memberName": "甲", "eligibleRoleNames": ["接机2", "登机主验"]},
            {"memberName": "乙", "eligibleRoleNames": ["接机2", "登机主验"]},
        ],
        "memberAvailability": [
            {"memberName": name, "participating": True, "availableFrom": None, "availableUntil": None}
            for name in ("甲", "乙")
        ],
        "flowPreferences": [{"flightNo": "*", "arrivalRole": "接机2", "boardingRoles": ["登机主验"],
                             "enabled": True, "ordered": True, "targetFlightScopes": {"登机主验": "same-flight"}}],
    }
    fill_day(state, day, ["CX937", "KE0166"])
    return imported_day_to_scenario(imported_day_from_dict(day))[0]


class FlightSpecificDedicationTests(unittest.TestCase):
    def test_per_flight_template_setting_becomes_task_hard_constraint_and_other_flight_stays_flowable(self):
        scenario = prepared_scenario()
        tasks = {(scenario.flights[[f.id for f in scenario.flights].index(t.flight_id)].flight_no, t.role_id): t
                 for t in scenario.tasks}
        cx_arrival, cx_boarding = tasks["CX937", "接机2"], tasks["CX937", "登机主验"]
        ke_arrival, ke_boarding = tasks["KE0166", "接机2"], tasks["KE0166", "登机主验"]
        self.assertFalse(cx_arrival.exclusive_same_flight)
        self.assertTrue(cx_boarding.exclusive_same_flight)
        self.assertFalse(ke_boarding.exclusive_same_flight)
        self.assertEqual("exclusive_same_flight", task_pair_conflict(scenario, cx_arrival, cx_boarding)[0])
        self.assertIsNone(task_pair_conflict(scenario, ke_arrival, ke_boarding))

        result = solve(scenario, allow_partial=True)
        assignments = {(a.task_id, a.member_id) for a in result.assignments}
        self.assertEqual(4, len(result.assignments), result.diagnostics)
        self.assertNotEqual(
            next(member for task, member in assignments if task == cx_arrival.id),
            next(member for task, member in assignments if task == cx_boarding.id),
        )
        self.assertEqual(
            next(member for task, member in assignments if task == ke_arrival.id),
            next(member for task, member in assignments if task == ke_boarding.id),
        )
        self.assertFalse(validate_schedule(scenario, result.assignments))
        choice = next(option for option in candidate_options(scenario, (Assignment(cx_arrival.id, "甲"),), cx_boarding.id)
                      if option["id"] == "甲")
        self.assertFalse(choice["available"])
        broken = tuple(Assignment(t.id, "甲") for t in scenario.tasks)
        self.assertIn("exclusive_same_flight", {i.code for i in validate_schedule(scenario, broken)})

    def test_complete_excel_roundtrip_preserves_per_flight_values(self):
        data = complete_payload()
        content = export_complete_configuration(data)
        book = load_workbook(BytesIO(content), data_only=True)
        self.assertEqual("hidden", book["岗位性质"].sheet_state)
        self.assertIn("同航班专人专岗", [cell.value for cell in book["模板岗位"][1]])
        dedicated_col = [cell.value for cell in book["模板岗位"][1]].index("同航班专人专岗")
        rows = {(row[0], row[1]): row[dedicated_col] for row in book["模板岗位"].iter_rows(min_row=2, values_only=True)}
        self.assertEqual("是", rows[("CX937", "登机主验")])
        self.assertEqual("否", rows[("KE0166", "登机主验")])
        parsed = import_complete_configuration(content)
        values = {flight["flightNo"]: next(r["exclusiveSameFlight"] for r in flight["roles"] if r["roleName"] == "登机主验")
                  for flight in parsed["flightLibrary"]}
        self.assertEqual({"CX937": True, "KE0166": False}, values)

    def test_daily_excel_roundtrip_preserves_explicit_on_and_off_per_flight(self):
        data = complete_payload()['day']
        for cell in data['cells']:
            cell['exclusiveSameFlightOverride'] = cell['flightNo'] == 'CX937' and cell['roleName'] == '登机主验'
        day = imported_day_from_dict(data)
        content = export_imported_day(day)
        restored = import_day(content, day.service_date)
        values = {(cell.flight_no, cell.role_name): cell.exclusive_same_flight_override for cell in restored.cells}
        self.assertEqual({('CX937','登机主验'):True, ('CX937','接机2'):False,
                          ('KE0166','登机主验'):False, ('KE0166','接机2'):False}, values)

    def test_old_daily_excel_keeps_missing_overrides_and_its_own_task_facts(self):
        day = imported_day_from_dict(complete_payload()['day'])
        restored = import_day(export_imported_day(day), day.service_date)
        self.assertTrue(all(cell.exclusive_same_flight_override is None for cell in restored.cells))
        before = imported_day_to_scenario(day)[0]
        after = imported_day_to_scenario(restored)[0]
        def facts(scenario):
            flights = {flight.id: flight.flight_no for flight in scenario.flights}
            return {(flights[task.flight_id], task.role_id):
                    (task.start, task.end, task.required_count, task.timing_mode, task.concurrent, task.exclusive_same_flight)
                    for task in scenario.tasks}
        self.assertEqual(facts(before), facts(after))

    def test_old_flight_template_without_per_flight_column_migrates_role_defaults_once(self):
        data = complete_payload(dedicated_cx=False)
        data["flightLibrary"][0]["roles"][1].pop("exclusiveSameFlight")
        data["flightLibrary"][1]["roles"][1].pop("exclusiveSameFlight")
        data["day"]["roleConfigs"] = [{"roleName": name, "requiredCount": 1,
                                         "workMode": "arrival" if name == "接机2" else "boarding",
                                         "concurrent": False, "exclusiveSameFlight": name == "登机主验"}
                                        for name in data["day"]["roleNames"]]
        parsed = {"day": data["day"], "flightLibrary": data["flightLibrary"]}
        migrated = {'configuration': {'roleConfigs': parsed['day']['roleConfigs']}, 'flightLibrary': parsed['flightLibrary']}
        with_role_membership(migrated)
        values = {flight["flightNo"]: next(r["exclusiveSameFlight"] for r in flight["roles"] if r["roleName"] == "登机主验")
                  for flight in migrated["flightLibrary"]}
        self.assertEqual({"CX937": True, "KE0166": True}, values)

    def test_template_edit_and_backup_isolate_flights_and_survive_reload(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd() / ".tmp", prefix="flight-dedicated-") as directory:
            workspace = Workbench(Path(directory) / "source.sqlite3")
            payload = complete_payload()
            state = workspace.execute(0, "import-complete-configuration", payload)
            cx = deepcopy(next(f for f in state["flightLibrary"] if f["flightNo"] == "CX937"))
            cx["roles"] = [{**r, "exclusiveSameFlight": False} if r["roleName"] == "登机主验" else r for r in cx["roles"]]
            state = workspace.execute(state["revision"], "save-flight-template", cx)
            values = {f["flightNo"]: next(r["exclusiveSameFlight"] for r in f["roles"] if r["roleName"] == "登机主验")
                      for f in state["flightLibrary"]}
            self.assertEqual({"CX937": False, "KE0166": False}, values)
            restored = Workbench(Path(directory) / "restored.sqlite3")
            restored.restore(0, workspace.backup())
            values = {f["flightNo"]: next(r["exclusiveSameFlight"] for r in f["roles"] if r["roleName"] == "登机主验")
                      for f in restored.snapshot()["flightLibrary"]}
            self.assertEqual({"CX937": False, "KE0166": False}, values)

    def test_export_empty_device_restart_new_day_and_actual_solve_keep_dedicated_exception(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd() / ".tmp", prefix="flight-dedicated-migration-") as directory:
            folder = Path(directory)
            source = Workbench(folder / "source.sqlite3")
            source.execute(0, "import-complete-configuration", complete_payload())
            content = source.export_complete_configuration()
            decoded = import_complete_configuration(content)
            device = Workbench(folder / "imported.sqlite3")
            device.execute(0, "import-complete-configuration", decoded)
            device = Workbench(folder / "imported.sqlite3")
            state = device.snapshot()
            state = device.execute(state["revision"], "new-plan", {"serviceDate": "2026-09-03", "flightNos": ["CX937", "KE0166"]})
            pid = state["selectedPlanId"]
            day = deepcopy(state["plans"][pid]["day"])
            for cell in day["cells"]:
                if cell["roleName"] not in {"接机2", "登机主验"}:
                    cell["requiredCountOverride"] = 0
            state = device.execute(state["revision"], "save-input", {"day": day}, pid)
            state = device.execute(state["revision"], "solve", {}, pid)
            plan = state["plans"][pid]
            self.assertTrue(plan["valid"], plan["issues"])
            flights = {flight["id"]: flight["flightNo"] for flight in plan["day"]["flights"]}
            tasks = {(flights[t["flightId"]], t["roleId"]): t for t in plan["schedule"]["tasks"]}
            assigned = {a["taskId"]: a["memberId"] for a in plan["draft"]}
            self.assertTrue(tasks["CX937", "登机主验"]["exclusiveSameFlight"])
            self.assertFalse(tasks["KE0166", "登机主验"]["exclusiveSameFlight"])
            self.assertNotEqual(assigned[tasks["CX937", "接机2"]["id"]], assigned[tasks["CX937", "登机主验"]["id"]])
            self.assertEqual(assigned[tasks["KE0166", "接机2"]["id"]], assigned[tasks["KE0166", "登机主验"]["id"]])

    def test_missing_old_template_flag_preserves_existing_flight_values_and_invalid_flags_are_atomic(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd() / '.tmp', prefix='flight-dedication-old-') as directory:
            workspace = Workbench(Path(directory) / 'state.sqlite3')
            payload = complete_payload()
            state = workspace.execute(0, 'import-complete-configuration', payload)
            old = deepcopy(payload)
            for flight in old['flightLibrary']:
                for role in flight['roles']:
                    role.pop('exclusiveSameFlight', None)
            state = workspace.execute(state['revision'], 'import-complete-configuration', old)
            values = {f['flightNo']: next(r['exclusiveSameFlight'] for r in f['roles'] if r['roleName'] == '登机主验')
                      for f in state['flightLibrary']}
            self.assertEqual({'CX937': True, 'KE0166': False}, values)
            before = workspace.backup()
            for invalid in ('yes', 1, None, []):
                template = deepcopy(state['flightLibrary'][0])
                template['roles'][0]['exclusiveSameFlight'] = invalid
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    workspace.execute(state['revision'], 'save-flight-template', template)
                self.assertEqual(before, workspace.backup())

    def test_shared_baggage_restricts_only_the_flight_whose_baggage_is_dedicated(self):
        from dataclasses import replace
        from gate_schedule.domain import Flight, Member, Scenario, Task
        from gate_schedule.io import scenario_from_dict, scenario_to_dict
        s = Scenario("2026-08-24", (Flight("cx", "CX937", "2026-08-24", 600, 700),
                                     Flight("ke", "KE0166", "2026-08-24", 610, 720)),
                     (Member("甲", "甲", frozenset({"大件行李拦截", "接机2"}), 0),),
                     (Task("bag", "cx", "大件行李拦截", 600, 720, 1, "mobile", covered_flight_ids=("cx", "ke"),
                           exclusive_same_flight=True, exclusive_flight_ids=("cx",)),
                      Task("cx-arrival", "cx", "接机2", 600, 700, 1, "mobile", timing_mode="phase", service_stage="arrival"),
                      Task("ke-arrival", "ke", "接机2", 610, 720, 1, "mobile", timing_mode="phase", service_stage="arrival")),
                     release_policy="task", flow_preferences=({"flightNo": "*", "arrivalRole": "大件行李拦截",
                        "boardingRoles": ["接机2"], "enabled": True, "targetFlightScopes": {"接机2": "same-flight"}},))
        self.assertEqual(s, scenario_from_dict(scenario_to_dict(s)))
        self.assertEqual("exclusive_same_flight", task_pair_conflict(s, s.tasks[0], s.tasks[1])[0])
        self.assertIsNone(task_pair_conflict(s, s.tasks[0], s.tasks[2]))
        invalid = replace(s, tasks=(replace(s.tasks[0], exclusive_flight_ids=("unknown",)), *s.tasks[1:]))
        self.assertIn("invalid_exclusive_flights", {issue.code for issue in validate_schedule(invalid, ())})


if __name__ == "__main__":
    unittest.main()
