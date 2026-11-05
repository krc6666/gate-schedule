from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date
from pathlib import Path
import re
from uuid import uuid4

from .domain import Personnel, format_minutes, has_role_qualification, BaggageRotationSettings, ImportedRoleConfig, WORK_MODES, phase_role_config, phase_handoff_options, task_timing_label, prepare_regular_day
from .excel_io import imported_day_from_dict, imported_day_to_dict, import_day, export_formal_schedule
from .io import scenario_from_dict, scenario_to_dict, normalize_saved_scenario
from .projections import task_matrix, member_paths, flights_by_start
from .solver import solve
from .storage import LocalStateStore
from .validator import validate_schedule
from .qualification_config import validate_qualification_config
from .manual_editing import replace_slot, slot_issues, issue_data, name_suggestions, candidate_options
from .domain import validate_weekly_flights
from .domain import ValidationIssue
from .domain import draft_blocking_issues
from .domain import validate_handoff_preferences, default_handoff_preferences, validate_display_preferences, general_handoff_preferences
from .flight_configuration import save_template, fill_day
from .domain import default_balance_settings, validate_balance_settings, member_flight_participation
from .priority_statistics import priority_statistics, priority_counts_before, formal_history, next_formal_revision, role_statistics
from .duty_roster import duty_statistics, update_slot, export_roster, preview_roster, validate_roster_row
from .domain import validate_duty_roster
from .domain import default_team_leader_settings, validate_team_leader_settings, default_half_rest_settings, validate_half_rest_settings, HALF_REST_LABELS
from .domain import PERSONNEL_STATUSES, personnel_states, validate_personnel_details
from .domain import validate_relaxed_shift_settings
from .relaxed_shift_statistics import relaxed_shift_statistics
from .workspace_document import (
    WorkspaceError, normalize_document, with_configuration, validate_document,
    plan_scenario, apply_personnel_policy, pending_issues, archive_review,
    typed_names, sync_roster, read_assignments, role_configuration,
)
from .configuration_transfer import prepare_import, prepare_export
from .domain import COVERAGE_PRIORITIES, task_coverage_priority


def _assignment_data(values):
    return [{"taskId": item.task_id, "memberId": item.member_id} for item in values]


def _issues(values):
    return issue_data(None, values)


def _prepare_day(raw):
    explicit = {r["roleName"] for r in raw.get("roleConfigs", []) if "workMode" in r}
    natures = tuple(r["roleName"] for r in raw.get("roleConfigs", []) if "concurrent" in r)
    day = prepare_regular_day(imported_day_from_dict(raw), tuple(r for r in raw["roleNames"] if r not in explicit), natures)
    return imported_day_to_dict(day)


def _input_summary(issues):
    messages = list(dict.fromkeys(i["message"] for i in issues))
    return "；".join(messages[:4]) + (f"；另有{len(messages)-4}项，请在当天资料中查看" if len(messages) > 4 else "")


def _solver_summary(scenario, diagnostics):
    return _input_summary(issue_data(scenario, [ValidationIssue("solver", text) for text in diagnostics])) or "当前人员与岗位要求无法同时满足，请检查当天人员、岗位人数和规则"


def schedule_data(scenario, assignments):
    if scenario is None:
        return None
    participation = member_flight_participation(scenario, assignments)
    flights = flights_by_start(scenario.flights)
    return {
        "serviceDate": scenario.service_date,
        "flights": [{"id": f.id, "flightNo": f.flight_no, "windowStart": format_minutes(f.window_start), "windowEnd": format_minutes(f.window_end)} for f in flights_by_start(scenario.flights)],
        "tasks": [{"id": t.id, "flightId": t.flight_id, "roleId": t.role_id, "start": format_minutes(t.start), "end": format_minutes(t.end), "timingMode": t.timing_mode, "timingLabel": task_timing_label(t), "concurrent": t.concurrent, "exclusiveSameFlight": t.exclusive_same_flight, "requiredCount": t.required_count, "lockedMemberIds": list(t.locked_member_ids), "qualifiedMemberIds": [m.id for m in scenario.members if has_role_qualification(m, t.role_id)]} for t in scenario.tasks],
        "members": [{"id": m.id, "name": m.name, "status": m.status, "halfRest": m.half_rest} for m in scenario.members],
        "assignments": _assignment_data(assignments),
        "matrix": task_matrix(scenario, assignments),
        "memberPaths": member_paths(scenario, assignments),
        "memberFlights": [{"memberId": m.id, "memberName": m.name,
            "flightIds": [f.id for f in flights if f.id in participation[m.id]],
            "flightNos": [f.flight_no for f in flights if f.id in participation[m.id]],
            "count": len(participation[m.id])} for m in scenario.members],
    }


class Workbench:
    """One persisted workspace: inputs, editable drafts and immutable adopted snapshots."""

    KEY = "workspace-v2"

    def __init__(self, database_path: Path, *, store=None):
        self.store = store if store is not None else LocalStateStore(database_path)
        self._default_group = store is None

    def _load(self):
        if self._default_group and isinstance(self.store, LocalStateStore) and self.store.read_document('group-workspaces-v1') is not None:
            from .group_workspaces import GroupWorkspaces, _GroupStore
            self.store = _GroupStore(GroupWorkspaces(self.store.database_path), 'A')
        return normalize_document(self.store.read_document(self.KEY))

    def snapshot(self):
        state = deepcopy(self._load())
        state["workModes"] = WORK_MODES
        state['coveragePolicy'] = {'choices': COVERAGE_PRIORITIES}
        state["priorityStatistics"] = priority_statistics(state, current=self._statistics_draft(state))
        state["people"] = [p for p in state["people"] if not p.get("deleted", False)]
        state["personnelStatuses"] = list(PERSONNEL_STATUSES)
        state["weeklyFlightPresets"] = deepcopy(state.get("weeklyFlightPresets", {}))
        state["flightTemplates"] = [{k: f[k] for k in ("flightNo", "windowStart", "windowEnd")} for f in state["flightLibrary"]]
        state["newRoleDefaults"] = {r["roleName"]: role_configuration(r) for r in state["configuration"]["roleConfigs"]}
        state["suggestedHandoffPreferences"] = general_handoff_preferences(default_handoff_preferences([f["flightNo"] for f in state["flightLibrary"]], [r["roleName"] for r in state["configuration"]["roleConfigs"]]))
        for plan in state["plans"].values():
            plan["preparationDay"] = _prepare_day(plan["day"])
            flights = imported_day_from_dict(plan["day"]).flights
            try:
                flights = flights_by_start(flights)
            except (ValueError, TypeError):
                pass  # Keep the entered order when a flight time still needs correction.
            plan["flightColumns"] = [f.flight_no for f in flights]
            plan["needsPreparation"] = plan["day"].get("attendanceMode") != "duty"
            plan["handoffOptions"] = phase_handoff_options(imported_day_from_dict(plan["day"]))
            scenario, issues = plan_scenario(plan)
            plan["preparationNotice"] = f'{plan["day"]["serviceDate"]} 尚未选择当天人员。请选择谁来上班，再生成排班。' if plan["day"]["flights"] and not any(a.get("participating") for a in plan["day"].get("memberAvailability", [])) else None
            legacy_prefix = "原班表已归档；新日期暂时无合法安排，已保留未完成草稿："
            if plan.get("message", "").startswith(legacy_prefix):
                plan["message"] = "上一班已归档；新一天草稿已保存。" + (plan["preparationNotice"] or _solver_summary(scenario, plan["message"][len(legacy_prefix):].split("；")))
            assignments = read_assignments(plan["draft"])
            if scenario:
                issues += issue_data(scenario, validate_schedule(scenario, assignments))
                plan["schedule"] = schedule_data(scenario, assignments)
            else:
                plan["schedule"] = None
            issues += pending_issues(plan)
            plan["issues"] = issues
            plan["valid"] = bool(scenario and scenario.tasks) and not issues
            plan["undoAvailable"] = bool(plan.get("undoEdit") and plan["undoEdit"]["atRevision"] == state["revision"])
            changed = plan.get("lastEdit", {})
            edit_issues = [i for i in issues if set(i.get("relatedTaskIds", [])) & set(changed.get("taskIds", [])) or i["taskId"] in changed.get("cellIds", [])]
            plan["editFeedback"] = {"hasWarnings": bool(edit_issues), "issues": edit_issues,
                "message": ("已保存为待处理草稿：" + _input_summary(edit_issues)) if edit_issues else plan.get("message", "草稿已保存")}
            formal = plan.get("adopted")
            plan["formalSchedule"] = schedule_data(scenario_from_dict(formal["scenario"]) if formal.get('scenario') else None, read_assignments(formal["assignments"])) if formal else None
            plan['formalIssues'] = deepcopy(formal.get('issues', [])) if formal else []
            plan['formalCellDrafts'] = deepcopy(formal.get('cellDrafts', {})) if formal else {}
            plan['formalDay'] = deepcopy(formal['day']) if formal else None
            if formal and plan['formalSchedule'] is None:
                day = imported_day_from_dict(formal['day'])
                plan['formalSchedule'] = {'serviceDate':day.service_date, 'flights':[{'id':f.id,'flightNo':f.flight_no,'windowStart':f.window_start,'windowEnd':f.window_end} for f in day.flights],
                    'tasks':[], 'members':[], 'assignments':[], 'matrix':{}, 'memberPaths':{}, 'memberFlights':[]}
        selected = state['plans'].get(state.get('selectedPlanId'))
        if selected:
            day = selected['day']['serviceDate']
            state['relaxedShiftSummary'] = relaxed_shift_statistics(state, day[:7],
                current=self._statistics_draft(state), service_date=day)['current']
            if not selected['draft'] and not selected.get('lastEdit'):
                state['relaxedShiftSummary'] = {'date': day, 'earlyDepartures': [], 'afternoonRest': []}
            state['formalRelaxedShiftSummary'] = relaxed_shift_statistics(state, day[:7],
                current=selected.get('adopted'), service_date=day)['current'] if selected.get('adopted') else None
        return state

    def _commit(self, state, expected):
        state["revision"] = expected + 1
        validate_document(state)
        try:
            self.store.compare_and_save(self.KEY, expected, state)
        except ValueError as error:
            raise WorkspaceError(str(error)) from error
        return self.snapshot()

    def _save_balance(self, state, payload):
        if 'priorityFlightRoles' not in payload["balanceSettings"]:
            raise WorkspaceError("页面版本已更新，请按 Ctrl+F5 刷新，选择航班后重新勾选重点岗位；本次未保存，原配置和班表保留")
        incoming = deepcopy(payload['balanceSettings'])
        if 'coveragePriorities' in state['balanceSettings']:
            incoming.setdefault('coveragePriorities', deepcopy(state['balanceSettings']['coveragePriorities']))
        settings = validate_balance_settings(incoming, [r["roleName"] for r in state["configuration"]["roleConfigs"]], {p["id"] for p in state["people"]})
        if settings["priorityAdjustments"] != state["balanceSettings"]["priorityAdjustments"]:
            raise WorkspaceError("累计次数已改变，请刷新后重试；次数请在统计表用加减按钮调整")
        state["balanceSettings"] = settings


    @staticmethod
    def _personnel_policy_changed(plan, state):
        return (plan.get("halfRestSettings", default_half_rest_settings()) != state["halfRestSettings"]
                or plan.get("teamLeaderSettings", default_team_leader_settings()) != state["teamLeaderSettings"]
                or set(plan.get("teamLeaderIds", [])) != {p["id"] for p in state["people"] if p.get("teamLeader", False)}
                or plan.get("personnelStates", {}) != personnel_states(state["people"]))

    def _save_preferences(self, state, payload):
        validate_handoff_preferences(payload["preferences"], [r["roleName"] for r in state["configuration"]["roleConfigs"]], {f["flightNo"] for f in state["flightLibrary"]} | {f["flightNo"] for p in state["plans"].values() for f in p["day"]["flights"]})
        state["handoffPreferences"] = general_handoff_preferences(payload["preferences"])

    def _save_base_settings(self, state, payload):
        settings = BaggageRotationSettings(payload["rotationEnabled"], payload["rotationMinutes"])
        transition = payload["transitionMinutes"]
        if type(transition) is not int or not 0 <= transition <= 180:
            raise WorkspaceError("走动预留必须为0到180分钟")
        state["configuration"].update(rotationEnabled=settings.enabled, rotationMinutes=settings.interval_minutes, transitionMinutes=transition)

    def _save_day_input(self, state, plan, payload):
        day = deepcopy(payload["day"])
        if day["serviceDate"] != plan["day"]["serviceDate"]:
            raise WorkspaceError("修改日期请新建计划，避免覆盖原工作")
        plan["day"] = day
        interval = payload.get("rotationMinutes", plan["rotationMinutes"])
        enabled = payload.get("rotationEnabled", plan["rotationEnabled"])
        BaggageRotationSettings(enabled, interval)
        plan["rotationMinutes"], plan["rotationEnabled"] = interval, enabled
        sync_roster(plan, state["people"], refresh=False)
        # Parsing is structural; incomplete business input is retained and shown as draft issues.
        imported_day_from_dict(plan["day"])
        plan["inputRevision"] += 1
        plan["draft"] = []
        plan["preview"] = None
        if "cellDrafts" in plan:
            plan["cellDrafts"] = {key: text for key, text in plan["cellDrafts"].items() if key in {c["id"] for c in day["cells"]}}
        plan["message"] = "准备资料已保存；输入改变，旧预览已失效。正式班表保留"

    def execute(self, expected, action, payload, plan_id=None, *, control=None):
        state = deepcopy(self._load())
        if type(expected) is not int or state["revision"] != expected:
            raise WorkspaceError("数据版本已更新，请重新读取；当前输入未被覆盖")
        plan = state["plans"].get(plan_id) if plan_id else None
        manual_actions = {"assign", "edit-cell", "edit-slot", "configure-cell", "move-assignment", "clear-draft"}
        before_edit = {k: deepcopy(plan.get(k, {} if k == "cellDrafts" else None)) for k in ("day", "inputRevision", "draft", "preview", "cellDrafts")} if plan and action in manual_actions else None
        if action == "import-qualifications":
            self._merge_qualifications(state, payload)
        elif action == "import-complete-configuration":
            state = prepare_import(state, payload).state
        elif action == "save-rules":
            # Validate on the same private state; one CAS commit or no write at all.
            self._save_base_settings(state, payload["configuration"])
            self._save_balance(state, payload)
            if "relaxedShiftSettings" in payload:
                state["relaxedShiftSettings"] = validate_relaxed_shift_settings(payload["relaxedShiftSettings"])
            if "halfRestSettings" in payload:
                state["halfRestSettings"] = validate_half_rest_settings(payload["halfRestSettings"])
            if "teamLeaderSettings" in payload:
                state["teamLeaderSettings"] = validate_team_leader_settings(payload["teamLeaderSettings"])
            self._save_preferences(state, payload)
            if "input" in payload:
                if plan is None:
                    raise WorkspaceError("请先打开当天计划，原规则未改变")
                self._save_day_input(state, plan, payload["input"])
        elif action == "update-balance-settings":
            self._save_balance(state, payload)
        elif action == "adjust-priority-count":
            self._adjust_priority_count(state, payload)
        elif action == "save-duty-settings":
            incoming = validate_duty_roster(payload, {p['id'] for p in state['people']})
            if incoming['overrides'] != state['dutyRoster']['overrides']:
                raise WorkspaceError('人工安排请在轮值表逐日修改或恢复，原设置未改变')
            state['dutyRoster'] = incoming
        elif action == "update-duty-roster":
            update_slot(state, payload)
        elif action == "reset-duty-roster":
            day = payload['date']
            date.fromisoformat(day)
            state['dutyRoster']['overrides'] = [r for r in state['dutyRoster']['overrides'] if r['date'] != day]
        elif action == "rebalance-duty-month":
            month = payload['month']
            duty_statistics(state, month)
            state['dutyRoster']['overrides'] = [r for r in state['dutyRoster']['overrides'] if r['date'][:7] != month]
        elif action == "import-duty-roster":
            rows = payload['rows']
            if payload.get('expectedRevision') != expected:
                raise WorkspaceError('预览后数据已改变，请重新导入轮值表')
            candidate = deepcopy(state['dutyRoster'])
            dates = {r['date'] for r in rows}
            candidate['overrides'] = [r for r in candidate['overrides'] if r['date'] not in dates] + rows
            validate_duty_roster(candidate, {p['id'] for p in state['people']})
            for row in rows:
                issues = [m for m in validate_roster_row(state, row) if not m.endswith('待安排')]
                if issues: raise WorkspaceError('；'.join(issues))
            state['dutyRoster'] = candidate
        elif action == "save-flight-template":
            save_template(state, payload)
        elif action == "save-handoff-preferences":
            self._save_preferences(state, payload)
        elif action == "add-template-role":
            flight = next((f for f in state["flightLibrary"] if f["flightNo"] == payload.get("flightNo")), None)
            name = payload.get("roleName", "")
            source_name = payload.get("sourceRoleName", "")
            catalog = {r["roleName"]: r for r in state["configuration"]["roleConfigs"]}
            if flight is None or not isinstance(name, str) or not isinstance(source_name, str):
                raise WorkspaceError("请选择航班和岗位分类，或填写岗位名称")
            name, source_name = name.strip(), source_name.strip()
            if source_name and source_name not in catalog:
                raise WorkspaceError("岗位分类不存在，请重新选择")
            name = name or source_name
            if not name:
                raise WorkspaceError("请选择岗位分类或填写岗位名称")
            source_name = source_name or (name if name in catalog else "")
            if name in flight["roleNames"]:
                numbered = re.fullmatch(r"(.+?)([0-9]+)", name)
                base, suffix = (numbered[1], int(numbered[2]) + 1) if numbered else (name, 2)
                while f"{base}{suffix}" in catalog:
                    suffix += 1
                name = f"{base}{suffix}"
            if name not in catalog:
                role = {**role_configuration(catalog[source_name]), "roleName": name} if source_name else role_configuration({"roleName": name, "requiredCount": None})
                if source_name == "大件行李拦截":
                    role["workMode"] = "continuous"
                state["configuration"]["roleConfigs"].append(role)
                if source_name:
                    for person in state["people"]:
                        if source_name in person["eligibleRoleNames"]:
                            person["eligibleRoleNames"].append(name)
                    override = next((r for r in flight.get("roles", []) if r["roleName"] == source_name), None)
                    if override:
                        inherited = {**deepcopy(override), "roleName": name}
                        if source_name == "大件行李拦截":
                            inherited.update(workModeOverride=None, workStart=None, workEnd=None)
                        flight.setdefault("roles", []).append(inherited)
            flight["roleNames"].append(name)
        elif action == "save-display-preferences":
            state["displayPreferences"] = deepcopy(validate_display_preferences(payload))
        elif action == "save-roles":
            names = payload["roleNames"]
            count = payload.get("requiredCount")
            if not isinstance(names, list) or not names or any(not isinstance(n, str) or not n.strip() for n in names) or len(names) != len(set(names)) or (count is not None and (type(count) is not int or count < 0)):
                raise WorkspaceError("请填写不重复的岗位名称，人数为空或非负整数")
            if set(names) & {r["roleName"] for r in state["configuration"]["roleConfigs"]}:
                raise WorkspaceError("包含已有岗位，请修改现有岗位，原配置未改变")
            state["configuration"]["roleConfigs"].extend(role_configuration({"roleName": n.strip(), "requiredCount": count}) for n in names)
        elif action == "copy-flight-roles":
            source = next((f for f in state["flightLibrary"] if f["flightNo"] == payload["sourceNo"]), None)
            if source is None:
                raise WorkspaceError("来源航班模板不存在")
            targets = payload["targetNos"]
            if not isinstance(targets, list) or not targets or set(targets) - {f["flightNo"] for f in state["flightLibrary"]}:
                raise WorkspaceError("请选择有效的目标航班")
            for flight in state["flightLibrary"]:
                if flight["flightNo"] in targets:
                    flight["roles"] = deepcopy(source.get("roles", []))
                    flight["roleNames"] = deepcopy(source["roleNames"])
        elif action == "save-person":
            name = str(payload.get("name", "")).strip()
            if not name:
                raise WorkspaceError("请填写人员姓名")
            person_id = payload.get("id") or str(uuid4())
            if any(p["name"] == name and p["id"] != person_id and not p.get("deleted", False) for p in state["people"]):
                raise WorkspaceError("已有同名人员，请修改现有项目或填写可区分的姓名")
            existing = next((p for p in state["people"] if p["id"] == person_id), None)
            if existing and existing.get("deleted", False):
                raise WorkspaceError("该人员已删除，请刷新人员名单；需要重新新增时使用新增人员")
            roles = payload.get("eligibleRoleNames", existing["eligibleRoleNames"] if existing else [])
            if not isinstance(roles, list) or any(not isinstance(r, str) or not r.strip() for r in roles) or type(payload.get("active", True)) is not bool:
                raise WorkspaceError("人员岗位或启用状态无效")
            leader = payload.get("teamLeader", existing.get("teamLeader", False) if existing else False)
            if type(leader) is not bool:
                raise WorkspaceError("分队长开关必须是布尔值")
            status = payload.get("status", existing.get("status", "正常") if existing else "正常")
            remark = payload.get("remark", existing.get("remark", "") if existing else "")
            validate_personnel_details(status, remark)
            person = Personnel(person_id, name, tuple(dict.fromkeys(roles)), payload.get("active", existing["active"] if existing else True), leader, status, remark)
            saved = {"id": person.id, "name": person.name, "eligibleRoleNames": list(person.eligible_role_names), "active": person.active, "teamLeader": person.team_leader, "status": person.status, "remark": person.remark}
            if existing is not None:
                existing.update(saved)
            else:
                state["people"].append(saved)
        elif action == "delete-person":
            person = next((p for p in state["people"] if p["id"] == payload.get("id") and not p.get("deleted", False)), None)
            if person is None:
                raise WorkspaceError("人员不存在或已删除，原数据未改变")
            person.update(deleted=True, active=False, eligibleRoleNames=[], teamLeader=False)
            for key in ('dutyMemberIds', 'standbyMemberIds'):
                state['dutyRoster'][key] = [i for i in state['dutyRoster'][key] if i != person['id']]
            state["balanceSettings"]["priorityAdjustments"] = [a for a in state["balanceSettings"]["priorityAdjustments"] if a["memberId"] != person["id"]]
        elif action == "save-role":
            name = str(payload.get("roleName", "")).strip()
            if not name:
                raise WorkspaceError("请填写岗位名称")
            config = state["configuration"]
            role = next((r for r in config["roleConfigs"] if r["roleName"] == name), None)
            if role is None:
                role = {"roleName": name, "requiredCount": None}
                config["roleConfigs"].append(role)
            if "requiredCount" in payload:
                count = payload["requiredCount"]
                if count is not None and (type(count) is not int or count < 0):
                    raise WorkspaceError("岗位人数必须为空、0或正整数")
                role["requiredCount"] = count
            for key in ("workMode", "concurrent", "exclusiveSameFlight"):
                if key in payload:
                    role[key] = payload[key]
            ImportedRoleConfig(name, role["requiredCount"], role.get("workMode", "timed"), role.get("concurrent", False), role.get("exclusiveSameFlight", False))
            if "qualifiedMemberIds" in payload:
                selected = payload["qualifiedMemberIds"]
                if not isinstance(selected, list) or any(not isinstance(i, str) for i in selected) or len(selected) != len(set(selected)) or set(selected) - {p["id"] for p in state["people"]}:
                    raise WorkspaceError("资质人员选择无效，原配置未改变")
                for person in state["people"]:
                    values = [r for r in person["eligibleRoleNames"] if r != name]
                    if person["id"] in selected:
                        values.append(name)
                    person["eligibleRoleNames"] = values
        elif action == "save-config":
            self._save_base_settings(state, payload)
        elif action == "save-weekly-flights":
            presets = deepcopy(state.get("weeklyFlightPresets", {}))
            weekday = payload["weekday"]
            if not isinstance(weekday, str) or weekday not in set("0123456"):
                raise WorkspaceError("请选择有效星期")
            if payload.get("remove"):
                presets.pop(weekday, None)
            else:
                presets[weekday] = deepcopy(payload["flights"])
            state["weeklyFlightPresets"] = validate_weekly_flights(presets)
        elif action in ("new-plan", "import-plan"):
            service_date = payload["serviceDate"]
            date.fromisoformat(service_date)
            plan_id = str(uuid4())
            if action == "import-plan":
                day = imported_day_to_dict(import_day(payload["content"], service_date))
            else:
                config = state["configuration"]
                roles = payload.get("roleNames", [r["roleName"] for r in config["roleConfigs"]])
                day = {"serviceDate": service_date, "title": "当天保障计划", "roleNames": roles, "flights": [], "cells": [], "remarks": [], "roster": [], "roleConfigs": [role_configuration(r) for r in config["roleConfigs"] if r["roleName"] in roles], "memberConfigs": [], "memberAvailability": [], "extraOccupancies": [], "remarksReviewed": True, "transitionMinutes": config["transitionMinutes"], "releasePolicy": "task", "phaseHandoffs": []}
            if action == "new-plan":
                day["attendanceMode"] = "duty"
                if "flightNos" in payload:
                    fill_day(state, day, payload["flightNos"])
            for name in imported_day_to_dict(imported_day_from_dict(day))["memberNames"]:
                if not any(p["name"] == name for p in state["people"]):
                    state["people"].append({"id": str(uuid4()), "name": name, "eligibleRoleNames": [], "active": True})
            plan = {"id": plan_id, "day": day, "inputRevision": 0, "draft": [], "adopted": None, "rotationMinutes": 60, "rotationEnabled": True, "memberIds": {}, "preview": None, "message": "已创建未完成草稿"}
            plan["balanceSettings"] = deepcopy(state["balanceSettings"])
            apply_personnel_policy(plan, state)
            if action == "new-plan":
                plan.update(rotationMinutes=state["configuration"]["rotationMinutes"], rotationEnabled=state["configuration"]["rotationEnabled"])
            sync_roster(plan, state["people"], refresh=action == "new-plan")
            state["plans"][plan_id] = plan
            state["selectedPlanId"] = plan_id
        elif action == "select-plan":
            if plan is None:
                raise WorkspaceError("计划不存在")
            state["selectedPlanId"] = plan_id
        elif plan is None:
            raise WorkspaceError("请先创建或打开当天计划")
        elif action == "phase-plan":
            new_id = str(uuid4())
            day = imported_day_from_dict(plan["day"])
            configs = {r.role_name: r for r in day.role_configs}
            day = replace(day, release_policy="task", attendance_mode="duty", phase_handoffs=(), role_configs=tuple(phase_role_config(configs.get(r, ImportedRoleConfig(r))) for r in day.role_names))
            # Keep the old plan and formal snapshot byte-for-byte; the new draft is explicit.
            fresh = {**deepcopy(plan), "id": new_id, "day": imported_day_to_dict(day), "draft": [], "preview": None, "adopted": None, "inputRevision": 0, "message": "已另建环节方案，原方案保留。接机/登机无需逐格时间，督导可兼任；其他岗位暂按全程保障，请在配置调整性质与环节。跨航班衔接在规则中确认。"}
            fresh["balanceSettings"] = deepcopy(state["balanceSettings"])
            apply_personnel_policy(fresh, state)
            fresh.pop("cellDrafts", None)
            state["plans"][new_id] = fresh
            state["selectedPlanId"] = new_id
        elif action == "save-input":
            self._save_day_input(state, plan, payload)
        elif action == "refresh-people":
            sync_roster(plan, state["people"], refresh=True)
            apply_personnel_policy(plan, state)
            plan["inputRevision"] += 1
            plan["draft"], plan["preview"] = [], None
        elif action == "apply-config":
            config = state["configuration"]
            day = plan["day"]
            for role in config["roleConfigs"]:
                if role["roleName"] not in day["roleNames"]:
                    day["roleNames"].append(role["roleName"])
                    for flight in day["flights"]:
                        day["cells"].append({"id": str(uuid4()), "flightId": flight["id"], "flightNo": flight["flightNo"], "roleName": role["roleName"], "rawValue": "", "internalNames": [], "externalLabels": [], "semantics": "empty", "workStart": None, "workEnd": None, "fixedMemberNames": [], "requiredCountOverride": None})
            current = {r["roleName"]: r for r in day["roleConfigs"]}
            current.update({r["roleName"]: role_configuration(r) if day.get("releasePolicy") == "task" else deepcopy(r) for r in config["roleConfigs"]})
            day["roleConfigs"] = list(current.values())
            day["transitionMinutes"] = config["transitionMinutes"]
            plan.update(rotationMinutes=config["rotationMinutes"], rotationEnabled=config["rotationEnabled"])
            plan["balanceSettings"] = deepcopy(state["balanceSettings"])
            apply_personnel_policy(plan, state)
            sync_roster(plan, state["people"], refresh=True)
            plan["inputRevision"] += 1
            plan["draft"], plan["preview"] = [], None
            plan["message"] = "已载入基础岗位、资质及规则；当前草稿重置，正式结果保留"
        elif action == "assign":
            values = read_assignments(payload["assignments"])
            scenario, _ = plan_scenario(plan)
            if scenario is None:
                raise WorkspaceError("请先补齐当天岗位和人员资料")
            tasks = {t.id for t in scenario.tasks}
            members = {m.id for m in scenario.members}
            if any(a.task_id not in tasks or a.member_id not in members for a in values):
                raise WorkspaceError("分配引用了不存在的任务或人员")
            plan["draft"] = _assignment_data(values)
            plan["message"] = "草稿已保存；可以连同校验问题采用归档和导出"
        elif action == "edit-cell":
            self._edit_cell(plan, payload)
        elif action == "edit-slot":
            scenario, _ = plan_scenario(plan)
            if scenario is None:
                raise WorkspaceError("请先确认当天岗位与人员")
            try:
                values = replace_slot(scenario, read_assignments(plan["draft"]), payload["taskId"], payload["slot"], payload.get("memberId", ""))
            except ValueError as error:
                raise WorkspaceError(str(error)) from error
            plan["draft"] = _assignment_data(values)
            plan["message"] = "人员已更新并保存草稿"
        elif action == "undo-edit":
            undo = plan.get("undoEdit")
            if not undo or undo["atRevision"] != expected:
                raise WorkspaceError("班表已改变，不能撤销较早的修改")
            plan.update(deepcopy(undo["before"]))
            plan.pop("undoEdit", None)
            plan.pop("lastEdit", None)
            plan["message"] = "已撤销本次修改，原正式班表保留"
        elif action == "configure-cell":
            self._configure_cell(plan, payload)
        elif action == "move-assignment":
            self._move_assignment(plan, payload)
        elif action == "clear-draft":
            plan["draft"], plan["preview"] = [], None
            plan.pop("cellDrafts", None)
            plan["message"] = "当前草稿已清空，原正式班表保留"
        elif action == "solve":
            source_id = plan["id"]
            if plan.get("balanceSettings", default_balance_settings()) != state["balanceSettings"] or self._personnel_policy_changed(plan, state):
                plan = {**deepcopy(plan), "id": str(uuid4()), "balanceSettings": deepcopy(state["balanceSettings"]), "adopted": None, "preview": None}
                apply_personnel_policy(plan, state)
                state["plans"][plan["id"]] = plan
                state["selectedPlanId"] = plan["id"]
            self._solve(plan, state, control)
            if plan["id"] != source_id:
                plan["preview"]["returnPlanId"] = source_id
        elif action == "prepare-solve":
            day = _prepare_day(payload["day"])
            if day["serviceDate"] != plan["day"]["serviceDate"]:
                raise WorkspaceError("重新排班不能更改日期，请使用归档并排后天")
            source_id = plan["id"]
            if day != imported_day_to_dict(imported_day_from_dict(plan["day"])) or plan.get("balanceSettings", default_balance_settings()) != state["balanceSettings"] or self._personnel_policy_changed(plan, state):
                new_id = str(uuid4())
                plan = {**deepcopy(plan), "id": new_id, "day": day, "inputRevision": 0, "draft": [], "preview": None, "adopted": None}
                plan["balanceSettings"] = deepcopy(state["balanceSettings"])
                apply_personnel_policy(plan, state)
                if "cellDrafts" in plan:
                    plan["cellDrafts"] = {key: text for key, text in plan["cellDrafts"].items() if key in {c["id"] for c in day["cells"]}}
                sync_roster(plan, state["people"], refresh=False)
                state["plans"][new_id] = plan
                state["selectedPlanId"] = new_id
            self._solve(plan, state, control)
            if plan["id"] != source_id:
                plan["preview"]["returnPlanId"] = source_id
        elif action == "cancel-preview":
            if plan["preview"]:
                return_id = plan["preview"].get("returnPlanId")
                plan["draft"] = plan["preview"]["previousDraft"]
                if return_id in state["plans"]:
                    state["selectedPlanId"] = return_id
            plan["preview"] = None
            plan["message"] = "已取消预览，原草稿与正式班表保留"
        elif action in ("adopt", "archive-only"):
            self._adopt(plan, next_formal_revision(state), state)
            plan["message"] = "当前班表已归档，可以继续查看或编辑草稿" if action == "archive-only" else "正式班表已采用并保存，可以导出"
        elif action == "archive-next":
            day = _prepare_day(payload["day"])
            if date.fromisoformat(day["serviceDate"]) <= date.fromisoformat(plan["day"]["serviceDate"]):
                raise WorkspaceError("下一班日期必须晚于当前班表日期")
            # Validate structure before adopting; all changes commit together.
            imported_day_from_dict(day)
            self._adopt(plan, next_formal_revision(state), state)
            next_id = str(uuid4())
            next_plan = {"id": next_id, "day": day, "inputRevision": 0, "draft": [], "adopted": None, "rotationMinutes": payload.get("rotationMinutes", plan["rotationMinutes"]), "rotationEnabled": payload.get("rotationEnabled", plan["rotationEnabled"]), "memberIds": deepcopy(plan["memberIds"]), "preview": None, "message": "已归档原班表并创建下一班草稿，请确认航班、岗位设置和当天出勤"}
            next_plan["balanceSettings"] = deepcopy(state["balanceSettings"])
            apply_personnel_policy(next_plan, state)
            BaggageRotationSettings(next_plan["rotationEnabled"], next_plan["rotationMinutes"])
            sync_roster(next_plan, state["people"], refresh=False)
            try:
                self._solve(next_plan, state, control)
            except WorkspaceError as error:
                if control and control.stopped.is_set():
                    raise
                next_plan['message'] = '后天暂未排出安排，已保留待处理草稿：' + str(error)
            next_plan["message"] = f'已归档 {plan["day"]["serviceDate"]}，并生成 {day["serviceDate"]} 草稿。' + next_plan["message"]
            state["plans"][next_id] = next_plan
            state["selectedPlanId"] = next_id
        else:
            raise WorkspaceError("不支持的操作")
        if before_edit is not None:
            previous = before_edit["draft"]
            affected = {a["taskId"] for a in previous + plan["draft"] if a not in previous or a not in plan["draft"]}
            affected.update(payload[k] for k in ("taskId", "targetTaskId", "sourceTaskId") if payload.get(k))
            if payload.get("cellId"):
                cell = self._cell(plan, payload["cellId"])
                current, _ = plan_scenario(plan)
                if current:
                    affected.update(t.id for t in current.tasks if t.role_id == cell["roleName"] and (t.flight_id == cell["flightId"] or cell["flightId"] in t.covered_flight_ids))
            plan["undoEdit"] = {"atRevision": expected + 1, "before": before_edit}
            plan["lastEdit"] = {"taskIds": sorted(affected), "cellIds": [payload["cellId"]] if payload.get("cellId") else []}
        if control:
            control.stage("保存草稿与核对数据版本")
            return control.finish(lambda: self._commit(state, expected))
        return self._commit(state, expected)


    def _solve(self, plan, state=None, control=None):
        if not any(a.get("participating") for a in plan["day"].get("memberAvailability", [])):
            raise WorkspaceError("请选择当天参加人员")
        scenario, issues = plan_scenario(plan)
        issues += pending_issues(plan)
        if issues or scenario is None or not scenario.tasks:
            raise WorkspaceError(_input_summary(issues) or "请选择航班并配置岗位人数和资质")
        previous_roles = set()
        previous_date = None
        if state:
            history = [f for f in formal_history(state, before=plan["day"]["serviceDate"]) if f.get('scenario')]
            if history:
                previous = history[-1]
                previous_date = previous["scenario"]["serviceDate"]
                roles = {t["id"]: t["roleId"] for t in previous["scenario"]["tasks"]}
                previous_roles = {(a["memberId"], roles[a["taskId"]]) for a in previous["assignments"]}
                from .domain import member_flight_role_participation
                old_scenario = scenario_from_dict(previous['scenario'])
                flight_nos = {f.id: f.flight_no for f in old_scenario.flights}
                previous_roles.update((m, flight_nos[f], r) for m, f, r in
                    member_flight_role_participation(old_scenario, read_assignments(previous['assignments'])))
        counts = {**priority_counts_before(state, scenario.service_date),
            **priority_counts_before(state, scenario.service_date, by_flight=True)} if state else {}
        result = solve(scenario, allow_partial=True, previous_roles=previous_roles, priority_counts=counts, handoff_preferences=state.get("handoffPreferences", []) if state else [], control=control)
        values = control.latest if control and control.stopped.is_set() and control.keep else result.assignments
        if control:
            control.stage("独立复查资格、冲突与缺口")
        final_issues = validate_schedule(scenario, values)
        if (result.status not in ("OPTIMAL", "FEASIBLE") and not (control and control.keep and values is not None)) or draft_blocking_issues(final_issues):
            raise WorkspaceError("暂时排不开：" + _solver_summary(scenario, result.diagnostics))
        if plan["preview"] is None:
            plan["preview"] = {"previousDraft": deepcopy(plan["draft"]), "inputRevision": plan["inputRevision"]}
        plan["draft"] = _assignment_data(values)
        plan["historyReferenceDate"] = previous_date
        plan["message"] = ("已生成未完成草稿：" + _input_summary(issue_data(scenario, final_issues)) + "。可保留问题归档或导出。") if final_issues else "排班已生成并保存，可以调整或继续归档排后天。"
        protected = {t.id for t in scenario.tasks if task_coverage_priority(scenario, t) == 'protect'}
        shortfalls = [i for i in final_issues if i.code == 'coverage_shortfall' and i.task_id in protected]
        if shortfalls:
            plan['message'] += '优先保岗仍有缺口：' + _input_summary(issue_data(scenario, shortfalls)) + '；当前人员与规则下尚未补齐，请增加可用人员或检查限制。'
        if final_issues and scenario.flight_count_gap is not None:
            plan["message"] += f"当前航班数最大差距{scenario.flight_count_gap}仍严格执行；可补齐人员或在规则页调整后重新排班。"
        if previous_date:
            plan["message"] += f"轮岗参考 {previous_date} 已归档班表。"


    def _adopt(self, plan, saved_revision=None, state=None):
        scenario, issues = archive_review(plan)
        values = read_assignments(plan['draft'])
        previous = plan.get("adopted")
        if previous and previous.get('scenario') == (scenario_to_dict(scenario) if scenario else None) and previous["assignments"] == _assignment_data(values) and previous.get("day") == plan["day"] and previous["inputRevision"] == plan["inputRevision"] and previous.get('cellDrafts', {}) == plan.get('cellDrafts', {}) and previous.get('issues', []) == issues:
            plan["preview"] = None
            return
        plan["adopted"] = {"archiveVersion":1, "issues":deepcopy(issues), "cellDrafts":deepcopy(plan.get('cellDrafts', {})), "inputRevision": plan["inputRevision"], "scenario": scenario_to_dict(scenario) if scenario else None, "assignments": _assignment_data(values), "day": deepcopy(plan["day"]), "memberIds": deepcopy(plan["memberIds"]), "rotationMinutes": plan["rotationMinutes"], "rotationEnabled": plan["rotationEnabled"]}
        if state is not None:
            plan['adopted']['dutyMemberId'] = next((r['dutyMemberId'] for r in
                duty_statistics(state, plan['day']['serviceDate'][:7])['rows'] if r['date'] == plan['day']['serviceDate']), None)
        if "balanceSettings" in plan:
            plan["adopted"]["balanceSettings"] = deepcopy(plan["balanceSettings"])
        for key in ("teamLeaderSettings", "teamLeaderIds", "personnelStates", "halfRestSettings"):
            if key in plan:
                plan["adopted"][key] = deepcopy(plan[key])
        if saved_revision is not None:
            plan["adopted"]["savedRevision"] = saved_revision
        plan["preview"] = None

    @staticmethod
    def _cell(plan, cell_id):
        cell = next((c for c in plan["day"]["cells"] if c["id"] == cell_id), None)
        if cell is None:
            raise WorkspaceError("岗位格不存在，请重新读取")
        return cell


    def _edit_cell(self, plan, payload):
        cell = self._cell(plan, payload["cellId"])
        names = typed_names(plan, payload["text"])
        scenario, _ = plan_scenario(plan)
        segments = task_matrix(scenario, ()).get(cell["roleName"], {}).get(cell["flightNo"], []) if scenario else []
        requested_task = payload.get("taskId")
        if requested_task:
            segments = [s for s in segments if s["task_id"] == requested_task]
            if not segments:
                raise WorkspaceError("选择的分段不属于此格，请重新选择")
        tasks = {t.id: t for t in scenario.tasks} if scenario else {}
        member_ids = {m.name: m.id for m in scenario.members} if scenario else {}
        if not segments or any(name not in member_ids for name in names):
            if names:
                plan.setdefault("cellDrafts", {})[cell["id"]] = payload["text"].strip()
            else:
                plan.get("cellDrafts", {}).pop(cell["id"], None)
            plan["message"] = "姓名已保存为待完善草稿，请确认该格岗位人数、工作方式和当天人员"
            return
        total = sum(tasks[s["task_id"]].required_count for s in segments)
        if len(segments) > 1 and names and len(names) != total:
            raise WorkspaceError(f"该格有{len(segments)}个实际分段，共{total}个人员位置，请在详情中逐段输入，避免把轮换当作同时搭档")
        affected = {s["task_id"] for s in segments}
        values = [a for a in plan["draft"] if a["taskId"] not in affected]
        offset = 0
        for segment in segments:
            task = tasks[segment["task_id"]]
            group = names if len(segments) == 1 else names[offset:offset + task.required_count]
            if len(group) != len(set(group)):
                raise WorkspaceError("同一任务不能重复填写同一个人")
            values.extend({"taskId": task.id, "memberId": member_ids[name]} for name in group)
            offset += task.required_count
        plan["draft"] = values
        plan.get("cellDrafts", {}).pop(cell["id"], None)
        plan["message"] = "姓名已保存为草稿；资质、人数和时间冲突已重新校验"

    def _configure_cell(self, plan, payload):
        cell = self._cell(plan, payload["cellId"])
        count = payload["requiredCount"]
        if type(count) is not int or count < 0:
            raise WorkspaceError("岗位人数必须为0或正整数")
        old, _ = plan_scenario(plan)
        cell.update(requiredCountOverride=count, workStart=payload.get("workStart") or None, workEnd=payload.get("workEnd") or None)
        if "workModeOverride" in payload:
            cell["workModeOverride"] = payload["workModeOverride"] or None
        imported_day_from_dict(plan["day"])
        new, _ = plan_scenario(plan)
        # Preserve only task identities whose entire facts are unchanged.
        old_tasks = {t.id: t for t in old.tasks} if old else {}
        retained = {t.id for t in new.tasks if old_tasks.get(t.id) == t} if new else set()
        plan["draft"] = [a for a in plan["draft"] if a["taskId"] in retained]
        plan["inputRevision"] += 1
        plan["preview"] = None
        text = payload.get("text", plan.get("cellDrafts", {}).get(cell["id"]))
        if text is not None:
            self._edit_cell(plan, {"cellId": cell["id"], "text": text})
        plan["message"] = "该格岗位资料已保存；其他事实未变的安排保留，正式快照不变"

    def _move_assignment(self, plan, payload):
        scenario, _ = plan_scenario(plan)
        if not scenario:
            raise WorkspaceError("请先补齐当天资料")
        tasks = {t.id: t for t in scenario.tasks}
        member = payload["memberId"]
        source, target = payload.get("sourceTaskId"), payload["targetTaskId"]
        target_member = payload.get("targetMemberId")
        if target not in tasks or (source and source not in tasks) or member not in {m.id for m in scenario.members}:
            raise WorkspaceError("拖动引用了失效任务或人员")
        if source == target:
            raise WorkspaceError("来源与目标为同一实际任务，无需移动")
        values = deepcopy(plan["draft"])
        src = {"taskId": source, "memberId": member}
        dst = {"taskId": target, "memberId": target_member}
        if source and src not in values:
            raise WorkspaceError("来源人员已改变，请重新拖动")
        if target_member and dst not in values:
            raise WorkspaceError("目标人员已改变，请重新拖动")
        if {"taskId": target, "memberId": member} in values:
            raise WorkspaceError("目标任务已安排此人")
        if not target_member and sum(a["taskId"] == target for a in values) >= tasks[target].required_count:
            raise WorkspaceError("目标岗位已满，请拖到具体姓名上交换或在详情选择替换人员")
        if source:
            values.remove(src)
        if target_member:
            values.remove(dst)
            if source:
                if {"taskId": source, "memberId": target_member} in values:
                    raise WorkspaceError("交换后会让同一人重复进入来源任务")
                values.append({"taskId": source, "memberId": target_member})
        values.append({"taskId": target, "memberId": member})
        plan["draft"] = values
        plan["message"] = "拖动已保存为草稿；有冲突时须修正后才能采用"


    @staticmethod
    def _merge_qualifications(state, payload):
        try:
            data = validate_qualification_config(payload)
        except ValueError as error:
            raise WorkspaceError(str(error)) from error
        people = {p["name"]: p for p in state["people"] if not p.get("deleted", False)}
        inactive = [name for name in data["people"] if name in people and not people[name]["active"]]
        new_people = 0
        for name in data["people"]:
            if name not in people:
                person = {"id": str(uuid4()), "name": name, "eligibleRoleNames": [], "active": True}
                state["people"].append(person)
                people[name] = person
                new_people += 1
        catalog = state["configuration"]["roleConfigs"]
        known_roles = {r["roleName"] for r in catalog}
        added = 0
        for role in data["roles"]:
            name = role["roleName"]
            if name not in known_roles:
                catalog.append({"roleName": name, "requiredCount": None})
                known_roles.add(name)
            for member in role["memberNames"]:
                values = people[member]["eligibleRoleNames"]
                if name not in values:
                    values.append(name)
                    added += 1
        return {"payload": data, "newPeopleCount": new_people, "existingPeopleCount": len(data["people"]) - new_people, "newQualificationCount": added, "inactivePeople": inactive}

    def preview_qualifications(self, payload):
        state = deepcopy(self._load())
        return {**self._merge_qualifications(state, payload), "expectedRevision": state["revision"]}


    def preview_complete_configuration(self, payload):
        return prepare_import(self._load(), payload).preview


    def export_complete_configuration(self, plan_id=None, service_date=None):
        return prepare_export(self._load(), plan_id, service_date).excel()

    def qualification_configuration(self):
        state = self._load()
        state["people"] = [p for p in state["people"] if not p.get("deleted", False)]
        return {"people": [p["name"] for p in state["people"]], "roles": [{"roleName": r["roleName"], "memberNames": [p["name"] for p in state["people"] if r["roleName"] in p["eligibleRoleNames"]]} for r in state["configuration"]["roleConfigs"]], "source": "手动配置"}

    def backup(self):
        return {"formatVersion": 2, "state": deepcopy(self._load())}

    def priority_statistics(self, month=None):
        state = self._load()
        return priority_statistics(state, month, current=self._statistics_draft(state))

    def role_statistics(self, month=None):
        state = self._load()
        return role_statistics(state, month, current=self._statistics_draft(state))

    def relaxed_shift_statistics(self, month=None):
        state = self._load()
        selected = state['plans'].get(state.get('selectedPlanId'))
        return relaxed_shift_statistics(state, month, current=self._statistics_draft(state),
            service_date=selected['day']['serviceDate'] if selected else None)

    def _statistics_draft(self, state):
        """Use the selected saved schedule, including an explicitly cleared draft."""
        plan = state['plans'].get(state.get('selectedPlanId'))
        if not plan or not (plan['draft'] or plan.get('lastEdit')):
            return None
        scenario, _ = plan_scenario(plan)
        if scenario is None:
            return None
        return {'scenario': scenario_to_dict(scenario), 'assignments': plan['draft']}

    def duty_statistics(self, month=None):
        return duty_statistics(self._load(), month)

    def export_duty_roster(self, month):
        return export_roster(self._load(), month)

    def preview_duty_roster(self, content):
        return preview_roster(self._load(), content)

    def _adjust_priority_count(self, state, payload):
        delta = payload.get("delta")
        if type(delta) is not int or delta not in (-1, 1):
            raise WorkspaceError("每次请增加或减少1次")
        month, member, role = payload.get("month"), payload.get("memberId"), payload.get("roleName")
        flight = payload.get('flightNo')
        report = priority_statistics(state, month, current=self._statistics_draft(state))
        group = next((r for r in report["roles"] if r["roleName"] == role and r.get('flightNo') == flight), None)
        row = next((m for m in group["members"] if m["memberId"] == member), None) if group else None
        if row is None:
            raise WorkspaceError("请选择统计表中的人员与重点岗位")
        if delta < 0 and row["effectiveCount"] == 0:
            raise WorkspaceError("累计次数已经是0，不能继续减少")
        adjustments = state["balanceSettings"]["priorityAdjustments"]
        existing = next((a for a in adjustments if (a["month"], a["memberId"], a.get('flightNo'), a["roleName"]) == (month, member, flight, role)), None)
        correction = row["effectiveCount"] + delta - row["historyCount"]
        if existing:
            existing["delta"] = correction
            if existing["delta"] == 0:
                adjustments.remove(existing)
        else:
            adjustments.append({"month": month, "memberId": member, "flightNo": flight, "roleName": role, "delta": correction})

    def _restore_candidate(self, backup):
        if not isinstance(backup, dict) or not isinstance(backup.get("state"), dict):
            raise WorkspaceError("备份必须包含完整的工作台对象；当前数据未改变")
        if backup.get("formatVersion") == 1:
            candidate = self._migrate_legacy(backup["state"])
        elif backup.get("formatVersion") == 2:
            candidate = with_configuration(deepcopy(backup["state"]))
        else:
            raise WorkspaceError("备份版本不支持；当前数据未改变")
        if candidate.get("stateVersion") != 2 or candidate.get("timeVersion") != 2:
            raise WorkspaceError("数据或时间版本不支持")
        validate_document(candidate)
        return candidate

    def preview_restore(self, backup):
        candidate = self._restore_candidate(backup)
        return {'expectedRevision': self._load()['revision'], 'historyPlanCount': len(candidate['plans']),
                'peopleCount': len(candidate['people'])}

    def restore(self, expected, backup):
        if self._load()['revision'] != expected:
            raise WorkspaceError('当前数据已改变，请先刷新页面')
        candidate = self._restore_candidate(backup)
        original_revision = candidate["revision"]
        for plan in candidate["plans"].values():
            undo = plan.get("undoEdit")
            if undo:
                if undo["atRevision"] == original_revision:
                    undo["atRevision"] = expected + 1
                else:
                    plan.pop("undoEdit", None)
        return self._commit(candidate, expected)


    def _migrate_legacy(self, old):
        raw, assignments, mapping = normalize_saved_scenario(old["scenario"], old["assignments"])
        scenario = scenario_from_dict(raw)
        issues = validate_schedule(scenario, read_assignments(assignments))
        if issues:
            raise WorkspaceError("旧结果无法合法迁移：" + "；".join(i.message for i in issues))
        imported = old.get("importedDay")
        settings = old.get("baggageRotationSettings") or {}
        interval, enabled = settings.get("intervalMinutes", 60), settings.get("enabled", True)
        linked = False
        if imported is not None and scenario.model_version == "imported-day-v1":
            rebuilt, problems = plan_scenario({"day": imported, "rotationMinutes": interval, "rotationEnabled": enabled, "memberIds": {}})
            linked = not problems and rebuilt == scenario
        day = imported if linked else {"serviceDate": scenario.service_date, "title": "旧版独立正式快照", "flights": [], "roleNames": [], "cells": [], "remarks": [], "roster": [], "remarksReviewed": True}
        day = imported_day_to_dict(imported_day_from_dict(day))
        plan_id = str(uuid4())
        plan = {"id": plan_id, "day": deepcopy(day), "inputRevision": 0, "draft": deepcopy(assignments) if linked else [], "rotationMinutes": interval, "rotationEnabled": enabled, "memberIds": {}, "preview": None, "message": "旧结果保持原人员与覆盖迁移；原文件未更改。可查看正式结果或导出", "adopted": {"scenario": raw, "assignments": assignments, "inputRevision": 0, "day": deepcopy(day), "legacyOriginal": True}}
        plans = {plan_id: plan}
        if imported is not None and not linked:
            draft_id = str(uuid4())
            plans[draft_id] = {"id": draft_id, "day": imported_day_to_dict(imported_day_from_dict(imported)), "inputRevision": 0, "draft": [], "rotationMinutes": interval, "rotationEnabled": enabled, "memberIds": {}, "preview": None, "message": "旧日表另存草稿；无法证明与旧正式结果对应，未按日期强行配对", "adopted": None}
        return {"stateVersion": 2, "timeVersion": 2, "revision": 0, "selectedPlanId": plan_id, "people": [], "plans": plans, "migrationReport": [{"taskMapping": mapping, "inputLinked": linked, "note": "保持原人员与时段，未重新求解；无法对应的旧日表单独保留"}]}

    def export(self, plan_id):
        plan = self._load()["plans"].get(plan_id)
        if not plan or not plan["adopted"]:
            raise WorkspaceError("尚无归档班表，请先采用保存；可以保留问题归档和导出")
        formal = plan["adopted"]
        validate_document(self._load())
        scenario = scenario_from_dict(formal["scenario"]) if formal.get('scenario') else None
        assignments = read_assignments(formal["assignments"])
        if formal.get('archiveVersion') == 1:
            from .excel_io import export_archived_schedule
            return export_archived_schedule(formal['day'], scenario, assignments, formal['issues'], formal.get('cellDrafts', {}))
        return export_formal_schedule(scenario, assignments)

    def candidates(self, plan_id, task_id, slot=0):
        state = self._load()
        plan = state["plans"][plan_id]
        scenario, issues = plan_scenario(plan)
        if issues or scenario is None:
            return []
        return self._candidate_roster(state, plan, scenario, task_id, slot)

    @staticmethod
    def _candidate_roster(state, plan, scenario, task_id, slot):
        draft = read_assignments(plan["draft"])
        result = []
        options = {option["name"]: option for option in candidate_options(scenario, draft, task_id, slot)}
        for name in plan["day"].get("roster", []):
            option = options.get(name)
            if option is None:
                result.append({"id": plan["memberIds"].get(name, name), "name": name, "available": False, "reasons": ["今天未参加排班，或当天出勤资料尚未确认"], "issues": [], "status": "blocked", "label": "不可安排", "revision": state["revision"]})
                continue
            result.append({**option, "revision": state["revision"]})
        return sorted(result, key=lambda i: not i["available"])

    def cell_candidates(self, plan_id, cell_id, slot=0):
        """Check an unconfigured cell on a copy; querying never creates demand."""
        state = self._load()
        plan = deepcopy(state["plans"][plan_id])
        cell = self._cell(plan, cell_id)

        def unchecked(reasons):
            return [{"id": plan["memberIds"].get(name, name), "name": name,
                     "available": False, "status": "unverified", "label": "待完善岗位资料",
                     "reasons": reasons, "issues": [], "revision": state["revision"],
                     "requiresConfiguration": True} for name in plan["day"]["roster"]]

        scenario, issues = plan_scenario(plan)
        segments = task_matrix(scenario, ()).get(cell["roleName"], {}).get(cell["flightNo"], []) if scenario else []
        if len(segments) > 1:
            return unchecked(["请在岗位详情中选择具体轮换段，再检查人选"])
        provisional = not segments
        if provisional:
            if slot != 0:
                return unchecked(["请先确认临时岗位人数，再逐个位置检查人选"])
            if cell["roleName"] == "大件行李拦截":
                return unchecked(["请先确认大件岗位需求及轮换段，再逐段检查人选"])
            existing_tasks = {task.id: task for task in scenario.tasks} if scenario else {}
            cell["requiredCountOverride"] = 1
            scenario, issues = plan_scenario(plan)
            new_tasks = {task.id: task for task in scenario.tasks} if scenario else {}
            if scenario and any(new_tasks.get(key) != task for key, task in existing_tasks.items()):
                return unchecked(["临时岗位会改变原方案的任务分段，请先确认岗位需求，再检查人选"])
            segments = task_matrix(scenario, ()).get(cell["roleName"], {}).get(cell["flightNo"], []) if scenario else []
        if issues or scenario is None or len(segments) != 1:
            return unchecked(list(dict.fromkeys(i["message"] for i in issues)) or ["请先确认岗位工作方式、时间和当天人员，再检查人选"])
        task_id = segments[0]["task_id"]
        result = self._candidate_roster(state, plan, scenario, task_id, slot)
        for item in result:
            if provisional:
                item["requiresConfiguration"] = True
                if item["available"]:
                    item["label"] += "（待确认临时需求）"
        return result

    def preview_edit(self, plan_id, payload):
        if not isinstance(payload, dict):
            raise WorkspaceError("编辑内容必须为对象，原安排保留")
        state = self._load()
        if payload.get("expectedRevision", state["revision"]) != state["revision"]:
            raise WorkspaceError("班表已改变，请重新选择人员")
        plan = deepcopy(state["plans"][plan_id])
        scenario, _ = plan_scenario(plan)
        try:
            if "cellId" in payload:
                self._edit_cell(plan, payload)
            else:
                if scenario is None:
                    raise WorkspaceError("请先完成当天航班与人员准备，原安排保留")
                plan["draft"] = _assignment_data(replace_slot(scenario, read_assignments(plan["draft"]), payload["taskId"], payload["slot"], payload.get("memberId", "")))
        except (ValueError, TypeError) as error:
            return {"valid": False, "revision": state["revision"], "issues": [{"code":"edit", "message":str(error), "taskId":payload.get("taskId"), "memberId":None, "relatedTaskIds":[]}], "suggestions":name_suggestions(payload.get("text", ""), plan["day"].get("roster", []))}
        scenario, issues = plan_scenario(plan)
        if scenario:
            issues += issue_data(scenario, validate_schedule(scenario, read_assignments(plan["draft"])))
        issues += pending_issues(plan)
        return {"valid": bool(scenario and scenario.tasks) and not issues, "revision":state["revision"], "issues":issues, "suggestions":[]}
