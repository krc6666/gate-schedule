from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date
from pathlib import Path
import re
from uuid import uuid4

from .domain import Assignment, Personnel, format_minutes, has_role_qualification, GATE_ROLE_NAMES, BaggageRotationSettings, ImportedRoleConfig, WORK_MODES, phase_role_config, phase_handoff_options, task_timing_label, prepare_regular_day
from .excel_io import imported_day_from_dict, imported_day_to_dict, import_day, validate_imported_day, export_formal_schedule, validate_complete_configuration, export_complete_configuration
from .imported_schedule import imported_day_to_scenario
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
from .flight_configuration import seed_library, save_template, validate_library, remember_day, fill_day, with_role_membership
from .excel_io import configuration_day
from .domain import default_balance_settings, validate_balance_settings, member_flight_participation
from .priority_statistics import priority_statistics, priority_counts_before, formal_history, next_formal_revision
from .duty_roster import default_roster, duty_statistics, update_slot, export_roster, preview_roster, validate_roster_row
from .domain import validate_duty_roster
from .domain import default_team_leader_settings, validate_team_leader_settings
from .domain import PERSONNEL_STATUSES, MEMBER_STATUSES, personnel_status, personnel_states, validate_personnel_details
from .domain import imported_required_count


class WorkspaceError(ValueError):
    pass


def _assignments(values):
    if not isinstance(values, list):
        raise WorkspaceError("分配必须是列表")
    return tuple(Assignment(item["taskId"], item["memberId"]) for item in values)


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


def _new_role_config(role):
    config = phase_role_config(ImportedRoleConfig(role["roleName"], role["requiredCount"]))
    return {**role, "workMode": role.get("workMode", config.work_mode), "concurrent": role.get("concurrent", config.concurrent)}


def schedule_data(scenario, assignments):
    participation = member_flight_participation(scenario, assignments)
    flights = flights_by_start(scenario.flights)
    return {
        "serviceDate": scenario.service_date,
        "flights": [{"id": f.id, "flightNo": f.flight_no, "windowStart": format_minutes(f.window_start), "windowEnd": format_minutes(f.window_end)} for f in flights_by_start(scenario.flights)],
        "tasks": [{"id": t.id, "flightId": t.flight_id, "roleId": t.role_id, "start": format_minutes(t.start), "end": format_minutes(t.end), "timingMode": t.timing_mode, "timingLabel": task_timing_label(t), "concurrent": t.concurrent, "exclusiveSameFlight": t.exclusive_same_flight, "requiredCount": t.required_count, "lockedMemberIds": list(t.locked_member_ids), "qualifiedMemberIds": [m.id for m in scenario.members if has_role_qualification(m, t.role_id)]} for t in scenario.tasks],
        "members": [{"id": m.id, "name": m.name, "status": m.status} for m in scenario.members],
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

    def __init__(self, database_path: Path):
        self.store = LocalStateStore(database_path)

    def _load(self):
        document = self.store.read_document(self.KEY)
        state = document if document is not None else {"stateVersion": 2, "timeVersion": 2, "revision": 0, "people": [], "plans": {}, "selectedPlanId": None, "migrationReport": []}
        self._with_configuration(state)
        seed_library(state)
        with_role_membership(state)
        self._with_preferences(state)
        return state

    @staticmethod
    def _with_preferences(state):
        if "handoffPreferences" not in state:
            state["handoffPreferences"] = default_handoff_preferences([f["flightNo"] for f in state["flightLibrary"]], [r["roleName"] for r in state["configuration"]["roleConfigs"]])
        validate_handoff_preferences(state["handoffPreferences"], [r["roleName"] for r in state["configuration"]["roleConfigs"]], {f["flightNo"] for f in state["flightLibrary"]} | {f["flightNo"] for p in state["plans"].values() for f in p["day"]["flights"]})
        state["handoffPreferences"] = general_handoff_preferences(state["handoffPreferences"])
        state.setdefault("displayPreferences", {"layout": "flights", "zoom": 100})
        state.setdefault("balanceSettings", default_balance_settings())
        state.setdefault("teamLeaderSettings", default_team_leader_settings())
        state.setdefault("dutyRoster", default_roster(state))

    @staticmethod
    def _with_configuration(state):
        """Add optional v2 configuration without inferring demand or changing saved plans."""
        if isinstance(state, dict) and state.get("stateVersion") == 2 and "configuration" not in state:
            roles = list(GATE_ROLE_NAMES)
            for person in state.get("people", []):
                roles.extend(person["eligibleRoleNames"])
            for plan in state.get("plans", {}).values():
                roles.extend(plan["day"]["roleNames"])
            state["configuration"] = {"roleConfigs": [{"roleName": r, "requiredCount": None} for r in dict.fromkeys(roles)], "rotationMinutes": 60, "rotationEnabled": True, "transitionMinutes": 0}
        return state

    def _scenario(self, plan):
        day = imported_day_from_dict(plan["day"])
        issues = validate_imported_day(day)
        try:
            scenario, conversion = imported_day_to_scenario(day, plan["rotationMinutes"], plan["rotationEnabled"])
        except (ValueError, TypeError, KeyError) as error:
            return None, [{"code": "input", "message": str(error), "taskId": None, "memberId": None}]
        issues = issue_data(scenario, issues + conversion)
        identities = plan.get("memberIds", {})
        scenario = replace(scenario,
            members=tuple(replace(m, id=identities.get(m.name, m.id), team_leader=identities.get(m.name, m.id) in plan.get("teamLeaderIds", []), status=plan.get("personnelStates", {}).get(identities.get(m.name, m.id), "正常")) for m in scenario.members),
            tasks=tuple(replace(t, locked_member_ids=tuple(identities.get(n, n) for n in t.locked_member_ids)) for t in scenario.tasks),
            rotations=tuple(replace(r, candidate_member_ids=tuple(identities.get(n, n) for n in r.candidate_member_ids)) for r in scenario.rotations),
            flight_count_gap=plan.get("balanceSettings", default_balance_settings())["flightCountGap"],
            priority_role_ids=tuple(plan.get("balanceSettings", default_balance_settings())["priorityRoleNames"])
                if plan.get("balanceSettings", default_balance_settings())["priorityEnabled"] else (),
            team_leader_last=plan.get("teamLeaderSettings", default_team_leader_settings())["lastUsed"],
            balance_exempt_team_leaders=plan.get("teamLeaderSettings", default_team_leader_settings())["exemptFlightCount"],
        )
        return scenario, issues

    def snapshot(self):
        state = deepcopy(self._load())
        state["workModes"] = WORK_MODES
        state["priorityStatistics"] = priority_statistics(state)
        state["people"] = [p for p in state["people"] if not p.get("deleted", False)]
        state["personnelStatuses"] = list(PERSONNEL_STATUSES)
        state["weeklyFlightPresets"] = deepcopy(state.get("weeklyFlightPresets", {}))
        state["flightTemplates"] = [{k: f[k] for k in ("flightNo", "windowStart", "windowEnd")} for f in state["flightLibrary"]]
        state["newRoleDefaults"] = {r["roleName"]: _new_role_config(r) for r in state["configuration"]["roleConfigs"]}
        state["suggestedHandoffPreferences"] = general_handoff_preferences(default_handoff_preferences([f["flightNo"] for f in state["flightLibrary"]], [r["roleName"] for r in state["configuration"]["roleConfigs"]]))
        for plan in state["plans"].values():
            plan["preparationDay"] = _prepare_day(plan["day"])
            plan["flightColumns"] = [f.flight_no for f in flights_by_start(imported_day_from_dict(plan["day"]).flights)]
            plan["needsPreparation"] = plan["day"].get("attendanceMode") != "duty"
            plan["handoffOptions"] = phase_handoff_options(imported_day_from_dict(plan["day"]))
            scenario, issues = self._scenario(plan)
            plan["preparationNotice"] = f'{plan["day"]["serviceDate"]} 尚未选择当天人员。请选择谁来上班，再生成排班。' if plan["day"]["flights"] and not any(a.get("participating") for a in plan["day"].get("memberAvailability", [])) else None
            legacy_prefix = "原班表已归档；新日期暂时无合法安排，已保留未完成草稿："
            if plan.get("message", "").startswith(legacy_prefix):
                plan["message"] = "上一班已归档；新一天草稿已保存。" + (plan["preparationNotice"] or _solver_summary(scenario, plan["message"][len(legacy_prefix):].split("；")))
            assignments = _assignments(plan["draft"])
            if scenario:
                issues += issue_data(scenario, validate_schedule(scenario, assignments))
                plan["schedule"] = schedule_data(scenario, assignments)
            else:
                plan["schedule"] = None
            issues += self._pending_issues(plan)
            plan["issues"] = issues
            plan["valid"] = bool(scenario and scenario.tasks) and not issues
            plan["undoAvailable"] = bool(plan.get("undoEdit") and plan["undoEdit"]["atRevision"] == state["revision"])
            changed = plan.get("lastEdit", {})
            edit_issues = [i for i in issues if set(i.get("relatedTaskIds", [])) & set(changed.get("taskIds", [])) or i["taskId"] in changed.get("cellIds", [])]
            plan["editFeedback"] = {"hasWarnings": bool(edit_issues), "issues": edit_issues,
                "message": ("已保存为待处理草稿：" + _input_summary(edit_issues)) if edit_issues else plan.get("message", "草稿已保存")}
            formal = plan.get("adopted")
            plan["formalSchedule"] = schedule_data(scenario_from_dict(formal["scenario"]), _assignments(formal["assignments"])) if formal else None
        return state

    def _commit(self, state, expected):
        state["revision"] = expected + 1
        self._validate_document(state)
        try:
            self.store.compare_and_save(self.KEY, expected, state)
        except ValueError as error:
            raise WorkspaceError(str(error)) from error
        return self.snapshot()

    def _save_balance(self, state, payload):
        settings = validate_balance_settings(payload["balanceSettings"], [r["roleName"] for r in state["configuration"]["roleConfigs"]], {p["id"] for p in state["people"]})
        if settings["priorityAdjustments"] != state["balanceSettings"]["priorityAdjustments"]:
            raise WorkspaceError("累计次数已改变，请刷新后重试；次数请在统计表用加减按钮调整")
        state["balanceSettings"] = settings

    @staticmethod
    def _personnel_policy(plan, state):
        plan["teamLeaderSettings"] = deepcopy(state["teamLeaderSettings"])
        plan["teamLeaderIds"] = [p["id"] for p in state["people"] if p.get("teamLeader", False)]
        plan["personnelStates"] = personnel_states(state["people"])

    @staticmethod
    def _personnel_policy_changed(plan, state):
        return (plan.get("teamLeaderSettings", default_team_leader_settings()) != state["teamLeaderSettings"]
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
        self._roster(plan, state["people"], refresh=False)
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
            self._apply_complete_configuration(state, payload)
        elif action == "save-rules":
            # Validate on the same private state; one CAS commit or no write at all.
            self._save_base_settings(state, payload["configuration"])
            self._save_balance(state, payload)
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
                role = {**_new_role_config(catalog[source_name]), "roleName": name} if source_name else _new_role_config({"roleName": name, "requiredCount": None})
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
            state["configuration"]["roleConfigs"].extend(_new_role_config({"roleName": n.strip(), "requiredCount": count}) for n in names)
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
                day = {"serviceDate": service_date, "title": "当天保障计划", "roleNames": roles, "flights": [], "cells": [], "remarks": [], "roster": [], "roleConfigs": [_new_role_config(r) for r in config["roleConfigs"] if r["roleName"] in roles], "memberConfigs": [], "memberAvailability": [], "extraOccupancies": [], "remarksReviewed": True, "transitionMinutes": config["transitionMinutes"], "releasePolicy": "task", "phaseHandoffs": []}
            if action == "new-plan":
                day["attendanceMode"] = "duty"
                if "flightNos" in payload:
                    fill_day(state, day, payload["flightNos"])
            for name in imported_day_to_dict(imported_day_from_dict(day))["memberNames"]:
                if not any(p["name"] == name for p in state["people"]):
                    state["people"].append({"id": str(uuid4()), "name": name, "eligibleRoleNames": [], "active": True})
            plan = {"id": plan_id, "day": day, "inputRevision": 0, "draft": [], "adopted": None, "rotationMinutes": 60, "rotationEnabled": True, "memberIds": {}, "preview": None, "message": "已创建未完成草稿"}
            plan["balanceSettings"] = deepcopy(state["balanceSettings"])
            self._personnel_policy(plan, state)
            if action == "new-plan":
                plan.update(rotationMinutes=state["configuration"]["rotationMinutes"], rotationEnabled=state["configuration"]["rotationEnabled"])
            self._roster(plan, state["people"], refresh=action == "new-plan")
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
            self._personnel_policy(fresh, state)
            fresh.pop("cellDrafts", None)
            state["plans"][new_id] = fresh
            state["selectedPlanId"] = new_id
        elif action == "save-input":
            self._save_day_input(state, plan, payload)
        elif action == "refresh-people":
            self._roster(plan, state["people"], refresh=True)
            self._personnel_policy(plan, state)
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
            current.update({r["roleName"]: _new_role_config(r) if day.get("releasePolicy") == "task" else deepcopy(r) for r in config["roleConfigs"]})
            day["roleConfigs"] = list(current.values())
            day["transitionMinutes"] = config["transitionMinutes"]
            plan.update(rotationMinutes=config["rotationMinutes"], rotationEnabled=config["rotationEnabled"])
            plan["balanceSettings"] = deepcopy(state["balanceSettings"])
            self._personnel_policy(plan, state)
            self._roster(plan, state["people"], refresh=True)
            plan["inputRevision"] += 1
            plan["draft"], plan["preview"] = [], None
            plan["message"] = "已载入基础岗位、资质及规则；当前草稿重置，正式结果保留"
        elif action == "assign":
            values = _assignments(payload["assignments"])
            scenario, _ = self._scenario(plan)
            if scenario is None:
                raise WorkspaceError("请先补齐当天岗位和人员资料")
            tasks = {t.id for t in scenario.tasks}
            members = {m.id for m in scenario.members}
            if any(a.task_id not in tasks or a.member_id not in members for a in values):
                raise WorkspaceError("分配引用了不存在的任务或人员")
            plan["draft"] = _assignment_data(values)
            plan["message"] = "草稿已保存；仍须通过校验并采用才是正式班表"
        elif action == "edit-cell":
            self._edit_cell(plan, payload)
        elif action == "edit-slot":
            scenario, _ = self._scenario(plan)
            if scenario is None:
                raise WorkspaceError("请先确认当天岗位与人员")
            try:
                values = replace_slot(scenario, _assignments(plan["draft"]), payload["taskId"], payload["slot"], payload.get("memberId", ""))
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
                self._personnel_policy(plan, state)
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
                self._personnel_policy(plan, state)
                if "cellDrafts" in plan:
                    plan["cellDrafts"] = {key: text for key, text in plan["cellDrafts"].items() if key in {c["id"] for c in day["cells"]}}
                self._roster(plan, state["people"], refresh=False)
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
            self._adopt(plan, next_formal_revision(state))
            plan["message"] = "当前班表已归档，可以继续查看或编辑草稿" if action == "archive-only" else "正式班表已采用并保存，可以导出"
        elif action == "archive-next":
            day = _prepare_day(payload["day"])
            if date.fromisoformat(day["serviceDate"]) <= date.fromisoformat(plan["day"]["serviceDate"]):
                raise WorkspaceError("下一班日期必须晚于当前班表日期")
            # Validate structure before adopting; all changes commit together.
            imported_day_from_dict(day)
            self._adopt(plan, next_formal_revision(state))
            next_id = str(uuid4())
            next_plan = {"id": next_id, "day": day, "inputRevision": 0, "draft": [], "adopted": None, "rotationMinutes": payload.get("rotationMinutes", plan["rotationMinutes"]), "rotationEnabled": payload.get("rotationEnabled", plan["rotationEnabled"]), "memberIds": deepcopy(plan["memberIds"]), "preview": None, "message": "已归档原班表并创建下一班草稿，请确认航班、岗位设置和当天出勤"}
            next_plan["balanceSettings"] = deepcopy(state["balanceSettings"])
            self._personnel_policy(next_plan, state)
            BaggageRotationSettings(next_plan["rotationEnabled"], next_plan["rotationMinutes"])
            self._roster(next_plan, state["people"], refresh=False)
            try:
                self._solve(next_plan, state, control)
            except WorkspaceError as error:
                raise WorkspaceError("后天排班未生成，原班表和日期保留。" + str(error)) from error
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
                current, _ = self._scenario(plan)
                if current:
                    affected.update(t.id for t in current.tasks if t.role_id == cell["roleName"] and (t.flight_id == cell["flightId"] or cell["flightId"] in t.covered_flight_ids))
            plan["undoEdit"] = {"atRevision": expected + 1, "before": before_edit}
            plan["lastEdit"] = {"taskIds": sorted(affected), "cellIds": [payload["cellId"]] if payload.get("cellId") else []}
        if control:
            control.stage("保存草稿与核对数据版本")
            return control.finish(lambda: self._commit(state, expected))
        return self._commit(state, expected)

    @staticmethod
    def _pending_issues(plan):
        cells = {c["id"]: c for c in plan["day"]["cells"]}
        return [{"code": "pending_cell", "message": f'{cells[key]["flightNo"]} {cells[key]["roleName"]} 已输入“{text}”，请确认人数、工作方式和当天人员并确认安排', "taskId": key, "memberId": None} for key, text in plan.get("cellDrafts", {}).items()]

    def _solve(self, plan, state=None, control=None):
        if not any(a.get("participating") for a in plan["day"].get("memberAvailability", [])):
            raise WorkspaceError("请选择当天参加人员")
        scenario, issues = self._scenario(plan)
        issues += self._pending_issues(plan)
        if issues or scenario is None or not scenario.tasks:
            raise WorkspaceError(_input_summary(issues) or "请选择航班并配置岗位人数和资质")
        previous_roles = set()
        previous_date = None
        if state:
            history = formal_history(state, before=plan["day"]["serviceDate"])
            if history:
                previous = history[-1]
                previous_date = previous["scenario"]["serviceDate"]
                roles = {t["id"]: t["roleId"] for t in previous["scenario"]["tasks"]}
                previous_roles = {(a["memberId"], roles[a["taskId"]]) for a in previous["assignments"]}
        result = solve(scenario, allow_partial=True, previous_roles=previous_roles, priority_counts=priority_counts_before(state, scenario.service_date) if state else {}, handoff_preferences=state.get("handoffPreferences", []) if state else [], control=control)
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
        plan["message"] = ("已生成未完成草稿：" + _input_summary(issue_data(scenario, final_issues)) + "。请补齐后再归档。") if final_issues else "排班已生成并保存，可以调整或继续归档排后天。"
        if final_issues and scenario.flight_count_gap is not None:
            plan["message"] += f"当前航班数最大差距{scenario.flight_count_gap}仍严格执行；可补齐人员或在规则页调整后重新排班。"
        if previous_date:
            plan["message"] += f"轮岗参考 {previous_date} 已归档班表。"

    def _adopt(self, plan, saved_revision=None):
        scenario, issues = self._scenario(plan)
        issues += self._pending_issues(plan)
        values = _assignments(plan["draft"])
        if scenario:
            issues += _issues(validate_schedule(scenario, values))
        if issues or scenario is None or not scenario.tasks:
            raise WorkspaceError("不能采用：" + ("；".join(i["message"] for i in issues) or "没有可安排任务"))
        previous = plan.get("adopted")
        if previous and scenario_from_dict(previous["scenario"]) == scenario and previous["assignments"] == _assignment_data(values) and previous.get("day") == plan["day"] and previous["inputRevision"] == plan["inputRevision"]:
            plan["preview"] = None
            return
        plan["adopted"] = {"inputRevision": plan["inputRevision"], "scenario": scenario_to_dict(scenario), "assignments": _assignment_data(values), "day": deepcopy(plan["day"]), "memberIds": deepcopy(plan["memberIds"]), "rotationMinutes": plan["rotationMinutes"], "rotationEnabled": plan["rotationEnabled"]}
        if "balanceSettings" in plan:
            plan["adopted"]["balanceSettings"] = deepcopy(plan["balanceSettings"])
        for key in ("teamLeaderSettings", "teamLeaderIds", "personnelStates"):
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

    @staticmethod
    def _typed_names(plan, text):
        if not isinstance(text, str) or len(text) > 2000:
            raise WorkspaceError("姓名内容无效或过长")
        names = [n.strip() for n in re.split(r"[/／、,，;；\n]+", text) if n.strip()]
        unknown = set(names) - set(plan["day"]["roster"])
        if unknown:
            suggestions = name_suggestions(text, plan["day"]["roster"])
            raise WorkspaceError("未找到当天人员：" + "、".join(sorted(unknown)) + ("。可选择：" + "、".join(suggestions) if suggestions else "。请先配置人员") + "；原安排保留")
        return names

    def _edit_cell(self, plan, payload):
        cell = self._cell(plan, payload["cellId"])
        names = self._typed_names(plan, payload["text"])
        scenario, _ = self._scenario(plan)
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
        old, _ = self._scenario(plan)
        cell.update(requiredCountOverride=count, workStart=payload.get("workStart") or None, workEnd=payload.get("workEnd") or None)
        if "workModeOverride" in payload:
            cell["workModeOverride"] = payload["workModeOverride"] or None
        imported_day_from_dict(plan["day"])
        new, _ = self._scenario(plan)
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
        scenario, _ = self._scenario(plan)
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

    def _roster(self, plan, people, refresh):
        day = plan["day"]
        active = [p for p in people if p["active"] and not p.get("deleted", False)]
        if refresh:
            by_id = {p["id"]: p for p in people}
            renames = {name: by_id[identity]["name"] for name, identity in plan.get("memberIds", {}).items() if identity in by_id}
            renamed = lambda name: renames.get(name, name)
            day["roster"] = list(dict.fromkeys(renamed(n) for n in day.get("roster", [])))
            for key in ("memberConfigs", "memberAvailability"):
                for entry in day.get(key, []):
                    entry["memberName"] = renamed(entry["memberName"])
            for entry in day.get("memberAvailability", []):
                if any(p["id"] == plan.get("memberIds", {}).get(entry["memberName"]) and personnel_status(p) != "正常" for p in people):
                    entry["participating"] = False
            for cell in day["cells"]:
                for key in ("internalNames", "fixedMemberNames"):
                    cell[key] = [renamed(n) for n in cell.get(key, [])]
            for entry in day.get("extraOccupancies", []):
                entry["memberNames"] = [renamed(n) for n in entry["memberNames"]]
            for entry in day.get("remarks", []):
                entry["people"] = [renamed(n) for n in entry.get("people", [])]
            day["roster"] = list(dict.fromkeys([*day.get("roster", []), *(p["name"] for p in active)]))
            configs = {c["memberName"]: c for c in day.get("memberConfigs", [])}
            for p in active:
                configs[p["name"]] = {"memberName": p["name"], "eligibleRoleNames": [r for r in p["eligibleRoleNames"] if r in day["roleNames"]]}
            day["memberConfigs"] = list(configs.values())
        plan["memberIds"] = {**{name: identity for name, identity in plan.get("memberIds", {}).items() if name in day.get("roster", [])}, **{p["name"]: p["id"] for p in people}}

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

    def _apply_complete_configuration(self, state, payload):
        data = validate_complete_configuration(payload)
        day = deepcopy(data["day"])
        existing_flags = {r["roleName"]: r.get("exclusiveSameFlight", False) for r in state["configuration"]["roleConfigs"]}
        for role in day["roleConfigs"]:
            if "exclusiveSameFlight" not in role and existing_flags.get(role["roleName"], False):
                role["exclusiveSameFlight"] = True
        # The imported plan and maintained defaults must use the same retained flags.
        data["day"] = deepcopy(day)
        by_name = {p["name"]: p for p in state["people"] if not p.get("deleted", False)}
        inactive = [p["name"] for p in by_name.values() if not p["active"] and not data.get("peopleActive", {}).get(p["name"], False) and any(a["memberName"] == p["name"] and a["participating"] for a in day["memberAvailability"])]
        if inactive:
            raise WorkspaceError("这些人员已停用，不能通过Excel自动启用或参加排班：" + "、".join(inactive))
        added, removed, new_people = 0, 0, 0
        roles = set(day["roleNames"])
        for member in day["memberConfigs"]:
            name = member["memberName"]
            if name not in by_name:
                identity = data.get("peopleIds", {}).get(name, str(uuid4()))
                if any(p["id"] == identity for p in state["people"]):
                    raise WorkspaceError("导入人员ID已属于另一位人员，原数据未改变")
                person = {"id": identity, "name": name, "eligibleRoleNames": [], "active": True}
                by_name[name] = person
                state["people"].append(person)
                new_people += 1
            person = by_name[name]
            current = set(person["eligibleRoleNames"]) & roles
            incoming = set(member["eligibleRoleNames"])
            added += len(incoming - current)
            removed += len(current - incoming)
            person["eligibleRoleNames"] = [r for r in person["eligibleRoleNames"] if r not in roles] + list(member["eligibleRoleNames"])
        config = state["configuration"]
        incoming_roles = {r["roleName"]: {**r, "workMode": r.get("workMode", "timed"), "concurrent": r.get("concurrent", False)} for r in day["roleConfigs"]}
        for role in day["roleNames"]:
            incoming_roles.setdefault(role, {"roleName": role, "requiredCount": None})
        existing = {r["roleName"]: r for r in config["roleConfigs"]}
        existing.update(incoming_roles)
        config["roleConfigs"] = list(existing.values())
        config.update(rotationEnabled=data["rotationEnabled"], rotationMinutes=data["rotationMinutes"], transitionMinutes=day["transitionMinutes"])
        for key in ("handoffPreferences", "displayPreferences"):
            if key in data:
                state[key] = deepcopy(data[key])
        if "balanceSettings" in data:
            settings = deepcopy(data["balanceSettings"])
            identities = {identity: by_name[name]["id"] for name, identity in data.get("peopleIds", {}).items()}
            for adjustment in settings["priorityAdjustments"]:
                adjustment["memberId"] = identities.get(adjustment["memberId"], adjustment["memberId"])
            state["balanceSettings"] = validate_balance_settings(settings, [r["roleName"] for r in config["roleConfigs"]], {p["id"] for p in state["people"]})
        if "peopleActive" in data:
            for person in state["people"]:
                if not person.get("deleted", False) and person["name"] in data["peopleActive"]:
                    person["active"] = data["peopleActive"][person["name"]]
        if "peopleTeamLeaders" in data:
            for person in state["people"]:
                if not person.get("deleted", False) and person["name"] in data["peopleTeamLeaders"]:
                    person["teamLeader"] = data["peopleTeamLeaders"][person["name"]]
        for key, field in (("peopleStatus", "status"), ("peopleRemarks", "remark")):
            if key in data:
                for name, value in data[key].items():
                    by_name[name][field] = value
        if "teamLeaderSettings" in data:
            state["teamLeaderSettings"] = deepcopy(data["teamLeaderSettings"])
        if "dutyRoster" in data:
            incoming = deepcopy(data['dutyRoster'])
            identity_map = {identity: by_name[name]['id'] for name, identity in data.get('peopleIds', {}).items()}
            for key in ('dutyMemberIds', 'standbyMemberIds'):
                incoming[key] = [identity_map.get(i, i) for i in incoming[key]]
            for row in incoming['overrides']:
                row['dutyMemberId'] = identity_map.get(row['dutyMemberId'], row['dutyMemberId'])
                row['standbyMemberIds'] = [identity_map.get(i,i) for i in row['standbyMemberIds']]
            state['dutyRoster'] = validate_duty_roster(incoming, {p['id'] for p in state['people']})
        if "weeklyFlightPresets" in data:
            state["weeklyFlightPresets"] = deepcopy(data["weeklyFlightPresets"])
        if "flightLibrary" in data:
            previous_templates = {f["flightNo"]: f for f in state["flightLibrary"]}
            state["flightLibrary"] = deepcopy(data["flightLibrary"])
            for flight in state["flightLibrary"]:
                if "roleNames" not in flight and flight["flightNo"] in previous_templates:
                    flight["roleNames"] = deepcopy(previous_templates[flight["flightNo"]]["roleNames"])
        else:
            remember_day(state, day)
        with_role_membership(state)
        day = configuration_day(data)
        daily_settings = data.get("dayOverrides", {}).get("settings", {})
        plan_id = str(uuid4())
        plan = {"id": plan_id, "day": day, "inputRevision": 0, "draft": [], "adopted": None, "rotationMinutes": data["rotationMinutes"], "rotationEnabled": data["rotationEnabled"], "memberIds": {}, "preview": None, "configurationSource": data["source"], "message": "完整Excel配置已载入；已新增当天计划，旧计划和正式结果保留。可直接点自动排班。"}
        plan["balanceSettings"] = deepcopy(state["balanceSettings"])
        self._personnel_policy(plan, state)
        self._roster(plan, state["people"], refresh=False)
        plan["rotationEnabled"] = daily_settings.get("rotationEnabled", plan["rotationEnabled"])
        plan["rotationMinutes"] = daily_settings.get("rotationMinutes", plan["rotationMinutes"])
        state["plans"][plan_id] = plan
        state["selectedPlanId"] = plan_id
        return {"payload": data, "newPeopleCount": new_people, "newQualificationCount": added, "removedQualificationCount": removed, "peopleCount": len(day["roster"]), "flightCount": len(day["flights"]), "serviceDate": day["serviceDate"], "handoffPreferenceCount": len(state["handoffPreferences"])}

    def preview_complete_configuration(self, payload):
        state = deepcopy(self._load())
        result = self._apply_complete_configuration(state, payload)
        self._validate_document(state)
        return {**result, "expectedRevision": state["revision"]}

    def export_complete_configuration(self, plan_id):
        state = self._load()
        state["people"] = [p for p in state["people"] if not p.get("deleted", False)]
        current_ids = {p["id"] for p in state["people"]}
        for key in ('dutyMemberIds', 'standbyMemberIds'):
            state['dutyRoster'][key] = [i for i in state['dutyRoster'][key] if i in current_ids]
        for row in state['dutyRoster']['overrides']:
            if row['dutyMemberId'] not in current_ids: row['dutyMemberId'] = None
            row['standbyMemberIds'] = [i if i in current_ids else None for i in row['standbyMemberIds']]
        state["balanceSettings"]["priorityAdjustments"] = [a for a in state["balanceSettings"]["priorityAdjustments"] if a["memberId"] in current_ids]
        if plan_id not in state["plans"]:
            raise WorkspaceError("请先打开要导出的当天计划")
        plan = state["plans"][plan_id]
        # Export latest maintained configuration, even if today's draft was not rerun.
        export_plan = deepcopy(plan)
        self._roster(export_plan, state["people"], refresh=True)
        day = export_plan["day"]
        saved_day = deepcopy(day)
        # refresh above only normalizes stable identities; daily qualifications remain explicit.
        renames = {name: next((p["name"] for p in state["people"] if p["id"] == identity), name) for name, identity in plan.get("memberIds", {}).items()}
        saved_day["memberConfigs"] = [{**deepcopy(m), "memberName": renames.get(m["memberName"], m["memberName"])} for m in plan["day"]["memberConfigs"]]
        config = state["configuration"]
        day["roleNames"] = list(dict.fromkeys([*day["roleNames"], *(r["roleName"] for r in config["roleConfigs"])]))
        roles = {r["roleName"]: r for r in day["roleConfigs"]}
        roles.update({r["roleName"]: _new_role_config(r) for r in config["roleConfigs"]})
        day["roleConfigs"] = list(roles.values())
        day["roster"] = [p["name"] for p in state["people"]]
        day["memberConfigs"] = [{"memberName": p["name"], "eligibleRoleNames": [r for r in p["eligibleRoleNames"] if r in day["roleNames"]]} for p in state["people"]]
        available = {a["memberName"]: a for a in day["memberAvailability"]}
        day["memberAvailability"] = [{**available.get(p["name"], {"memberName": p["name"], "participating": False, "availableFrom": None, "availableUntil": None}), **({"participating": False} if personnel_status(p) != "正常" else {})} for p in state["people"]]
        # Configuration represents today's maintained roster; retired identities
        # remain only in the original plans and full backups, never active locks.
        live_names = set(day["roster"])
        previous_cells = {c.id: c for c in imported_day_from_dict(day).cells}
        source_day = imported_day_from_dict(day)
        for cell in day["cells"]:
            if set(cell["internalNames"]) - live_names:
                demand = imported_required_count(source_day, previous_cells[cell["id"]])
                if cell.get("requiredCountOverride") is None and demand is not None:
                    cell["requiredCountOverride"] = demand
                cell["internalNames"] = [n for n in cell["internalNames"] if n in live_names]
                cell["rawValue"] = "/".join([*cell["internalNames"], *cell["externalLabels"]])
                if len(cell["internalNames"]) < 2:
                    cell["semantics"] = "external" if cell["externalLabels"] else "single" if cell["internalNames"] else "empty"
            cell["fixedMemberNames"] = [n for n in cell.get("fixedMemberNames", []) if n in live_names]
        for entry in day["extraOccupancies"]:
            entry["memberNames"] = [n for n in entry["memberNames"] if n in live_names]
        day["extraOccupancies"] = [entry for entry in day["extraOccupancies"] if entry["memberNames"]]
        for entry in day["remarks"]:
            entry["people"] = [n for n in entry.get("people", []) if n in live_names]
        for flight in day["flights"]:
            for role in day["roleNames"]:
                if not any(c["flightId"] == flight["id"] and c["roleName"] == role for c in day["cells"]):
                    day["cells"].append({"id": str(uuid4()), "flightId": flight["id"], "flightNo": flight["flightNo"], "roleName": role, "rawValue": "", "internalNames": [], "externalLabels": [], "semantics": "empty", "fixedMemberNames": [], "requiredCountOverride": None, "workStart": None, "workEnd": None})
        day["transitionMinutes"] = config["transitionMinutes"]
        saved_roles = {r["roleName"]: _new_role_config(r) for r in saved_day["roleConfigs"]}
        for name, role in saved_roles.items():
            if next((r.get("exclusiveSameFlight", False) for r in day["roleConfigs"] if r["roleName"] == name), False) and "exclusiveSameFlight" not in role:
                role["exclusiveSameFlight"] = False
        latest_roles = {r["roleName"]: r for r in day["roleConfigs"]}
        latest_members = {m["memberName"]: set(m["eligibleRoleNames"]) for m in day["memberConfigs"]}
        qualifications = [{"memberName": m["memberName"], "roleName": role, "qualified": role in m["eligibleRoleNames"]}
                          for m in saved_day["memberConfigs"] if m["memberName"] in latest_members for role in plan["day"]["roleNames"]
                          if (role in m["eligibleRoleNames"]) != (role in latest_members[m["memberName"]])]
        daily_settings = {"rotationEnabled": plan["rotationEnabled"], "rotationMinutes": plan["rotationMinutes"], "transitionMinutes": saved_day["transitionMinutes"]}
        base_settings = {k: config[k] for k in daily_settings}
        overrides = {"roleConfigs": [r for name, r in saved_roles.items() if r != latest_roles[name]], "qualifications": qualifications, "settings": daily_settings if daily_settings != base_settings else {}}
        return export_complete_configuration({"day": day, "dayOverrides": overrides, "peopleStatus": {p["name"]: p.get("status", "正常") for p in state["people"]}, "peopleRemarks": {p["name"]: p.get("remark", "") for p in state["people"]}, "peopleActive": {p["name"]: p["active"] for p in state["people"]}, "peopleTeamLeaders": {p["name"]: p.get("teamLeader", False) for p in state["people"]}, "teamLeaderSettings": state["teamLeaderSettings"], "dutyRoster": state["dutyRoster"], "peopleIds": {p["name"]: p["id"] for p in state["people"]}, "balanceSettings": state["balanceSettings"], "handoffPreferences": state["handoffPreferences"], "displayPreferences": state["displayPreferences"], "flightLibrary": state["flightLibrary"], "weeklyFlightPresets": state.get("weeklyFlightPresets", {}), "rotationEnabled": config["rotationEnabled"], "rotationMinutes": config["rotationMinutes"], "source": plan.get("configurationSource", "当前最新长期配置，含当天航班与例外")})

    def qualification_configuration(self):
        state = self._load()
        state["people"] = [p for p in state["people"] if not p.get("deleted", False)]
        return {"people": [p["name"] for p in state["people"]], "roles": [{"roleName": r["roleName"], "memberNames": [p["name"] for p in state["people"] if r["roleName"] in p["eligibleRoleNames"]]} for r in state["configuration"]["roleConfigs"]], "source": "手动配置"}

    def backup(self):
        return {"formatVersion": 2, "state": deepcopy(self._load())}

    def priority_statistics(self, month=None):
        return priority_statistics(self._load(), month)

    def duty_statistics(self, month=None):
        return duty_statistics(self._load(), month)

    def export_duty_roster(self, month):
        return export_roster(self._load(), month)

    def preview_duty_roster(self, content):
        return preview_roster(self._load(), content)

    @staticmethod
    def _adjust_priority_count(state, payload):
        delta = payload.get("delta")
        if type(delta) is not int or delta not in (-1, 1):
            raise WorkspaceError("每次请增加或减少1次")
        month, member, role = payload.get("month"), payload.get("memberId"), payload.get("roleName")
        report = priority_statistics(state, month)
        group = next((r for r in report["roles"] if r["roleName"] == role), None)
        row = next((m for m in group["members"] if m["memberId"] == member), None) if group else None
        if row is None:
            raise WorkspaceError("请选择统计表中的人员与重点岗位")
        if delta < 0 and row["effectiveCount"] == 0:
            raise WorkspaceError("累计次数已经是0，不能继续减少")
        adjustments = state["balanceSettings"]["priorityAdjustments"]
        existing = next((a for a in adjustments if (a["month"], a["memberId"], a["roleName"]) == (month, member, role)), None)
        correction = row["effectiveCount"] + delta - row["historyCount"]
        if existing:
            existing["delta"] = correction
            if existing["delta"] == 0:
                adjustments.remove(existing)
        else:
            adjustments.append({"month": month, "memberId": member, "roleName": role, "delta": correction})

    def restore(self, expected, backup):
        if not isinstance(backup, dict) or not isinstance(backup.get("state"), dict):
            raise WorkspaceError("备份必须包含完整的工作台对象；当前数据未改变")
        if backup.get("formatVersion") == 1:
            candidate = self._migrate_legacy(backup["state"])
        elif backup.get("formatVersion") == 2:
            candidate = self._with_configuration(deepcopy(backup["state"]))
        else:
            raise WorkspaceError("备份版本不支持；当前数据未改变")
        if candidate.get("stateVersion") != 2 or candidate.get("timeVersion") != 2:
            raise WorkspaceError("数据或时间版本不支持")
        if self._load()["revision"] != expected:
            raise WorkspaceError("当前数据已改变，请先重新读取")
        self._validate_document(candidate)
        original_revision = candidate["revision"]
        for plan in candidate["plans"].values():
            undo = plan.get("undoEdit")
            if undo:
                if undo["atRevision"] == original_revision:
                    undo["atRevision"] = expected + 1
                else:
                    plan.pop("undoEdit", None)
        return self._commit(candidate, expected)

    def _validate_document(self, state, *, validate_undo=True):
        if not isinstance(state, dict):
            raise WorkspaceError("工作台数据必须为对象")
        if state.get("stateVersion") != 2 or state.get("timeVersion") != 2:
            raise WorkspaceError("数据或时间版本不支持")
        if not isinstance(state["people"], list) or not isinstance(state["plans"], dict):
            raise WorkspaceError("人员或计划结构无效")
        if "trialInfo" in state:
            notice = state["trialInfo"]
            if not isinstance(notice, dict) or any(not isinstance(notice.get(key), str) or not notice[key].strip() for key in ("label", "source")) or not isinstance(notice.get("assumptions"), list) or any(not isinstance(item, str) or not item.strip() for item in notice["assumptions"]):
                raise WorkspaceError("试用数据说明无效")
        self._with_configuration(state)
        seed_library(state)
        validate_library(state["flightLibrary"])
        with_role_membership(state)
        self._with_preferences(state)
        validate_handoff_preferences(state["handoffPreferences"], [r["roleName"] for r in state["configuration"]["roleConfigs"]], {f["flightNo"] for f in state["flightLibrary"]} | {f["flightNo"] for p in state["plans"].values() for f in p["day"]["flights"]})
        validate_display_preferences(state["displayPreferences"])
        validate_weekly_flights(state.get("weeklyFlightPresets", {}))
        validate_balance_settings(state["balanceSettings"], [r["roleName"] for r in state["configuration"]["roleConfigs"]], {p["id"] for p in state["people"]})
        validate_team_leader_settings(state["teamLeaderSettings"])
        validate_duty_roster(state['dutyRoster'], {p['id'] for p in state['people']})
        config = state["configuration"]
        BaggageRotationSettings(config["rotationEnabled"], config["rotationMinutes"])
        if type(config["transitionMinutes"]) is not int or not 0 <= config["transitionMinutes"] <= 180:
            raise WorkspaceError("基础走动预留无效")
        roles = config["roleConfigs"]
        if not isinstance(roles, list) or len(roles) != len({r["roleName"] for r in roles}):
            raise WorkspaceError("基础岗位重复或无效")
        for role in roles:
            ImportedRoleConfig(role["roleName"], role["requiredCount"], role.get("workMode", "timed"), role.get("concurrent", False), role.get("exclusiveSameFlight", False))
            count = role["requiredCount"]
            if not isinstance(role["roleName"], str) or not role["roleName"].strip() or (count is not None and (type(count) is not int or count < 0)):
                raise WorkspaceError("基础岗位名称或人数无效")
        if type(state["revision"]) is not int or state["revision"] < 0 or (state["selectedPlanId"] is not None and state["selectedPlanId"] not in state["plans"]):
            raise WorkspaceError("工作台版本或选中计划无效")
        ids = [p["id"] for p in state["people"]]
        names = [p["name"] for p in state["people"] if not p.get("deleted", False)]
        if len(ids) != len(set(ids)) or len(names) != len(set(names)):
            raise WorkspaceError("人员身份重复")
        for person in state["people"]:
            validate_personnel_details(person.get("status", "正常"), person.get("remark", ""))
            if type(person.get("deleted", False)) is not bool:
                raise WorkspaceError("人员删除标记无效")
            if type(person.get("teamLeader", False)) is not bool:
                raise WorkspaceError("分队长开关必须是布尔值")
            if not isinstance(person["id"], str) or not person["id"] or not isinstance(person["name"], str) or not person["name"].strip() or type(person["active"]) is not bool or not isinstance(person["eligibleRoleNames"], list) or any(not isinstance(r, str) or not r.strip() for r in person["eligibleRoleNames"]):
                raise WorkspaceError("人员结构无效")
        for plan_id, plan in state["plans"].items():
            statuses = plan.get("personnelStates", {})
            if not isinstance(statuses, dict) or any(not isinstance(k, str) or not k or v not in MEMBER_STATUSES for k, v in statuses.items()):
                raise WorkspaceError("当天人员状态快照无效")
            validate_team_leader_settings(plan.get("teamLeaderSettings", default_team_leader_settings()))
            leaders = plan.get("teamLeaderIds", [])
            if not isinstance(leaders, list) or any(not isinstance(v, str) or not v for v in leaders) or len(set(leaders)) != len(leaders):
                raise WorkspaceError("当天分队长身份列表无效")
            undo = plan.get("undoEdit")
            if "undoEdit" in plan and not isinstance(undo, dict):
                raise WorkspaceError("撤销记录无效，原数据保留")
            changed = plan.get("lastEdit", {})
            if not isinstance(changed, dict) or any(not isinstance(changed.get(k, []), list) or any(not isinstance(v, str) for v in changed.get(k, [])) for k in ("taskIds", "cellIds")):
                raise WorkspaceError("编辑反馈记录无效，原数据保留")
            if undo and validate_undo:
                if not isinstance(undo, dict) or type(undo.get("atRevision")) is not int or not 0 <= undo["atRevision"] <= state["revision"] or not isinstance(undo.get("before"), dict) or set(undo["before"]) != {"day", "inputRevision", "draft", "preview", "cellDrafts"}:
                    raise WorkspaceError("撤销记录无效，原数据保留")
                shadow = deepcopy(state)
                for value in shadow["plans"].values():
                    value.pop("undoEdit", None)
                shadow["plans"][plan_id].update(deepcopy(undo["before"]))
                self._validate_document(shadow, validate_undo=False)
            if plan["id"] != plan_id or type(plan["inputRevision"]) is not int:
                raise WorkspaceError("计划身份或版本无效")
            day = imported_day_from_dict(plan["day"])
            if "balanceSettings" in plan:
                validate_balance_settings(plan["balanceSettings"], plan["balanceSettings"]["priorityRoleNames"] + [r["roleName"] for r in state["configuration"]["roleConfigs"]])
            BaggageRotationSettings(plan["rotationEnabled"], plan["rotationMinutes"])
            date.fromisoformat(day.service_date)
            if len(day.flights) != len({f.id for f in day.flights}) or len(day.flights) != len({f.flight_no for f in day.flights}) or any(not f.flight_no.strip() for f in day.flights):
                raise WorkspaceError("航班编号或航班号重复/为空")
            if len(day.cells) != len({c.id for c in day.cells}) or len(day.role_names) != len(set(day.role_names)) or len(day.roster) != len(set(day.roster)):
                raise WorkspaceError("任务格、岗位或当天名单重复")
            pending = plan.get("cellDrafts", {})
            if not isinstance(pending, dict) or set(pending) - {c.id for c in day.cells}:
                raise WorkspaceError("待完善姓名格引用无效")
            for text in pending.values():
                if not self._typed_names(plan, text):
                    raise WorkspaceError("待完善姓名格不能为空")
            scenario, _ = self._scenario(plan)
            draft = _assignments(plan["draft"])
            if draft and scenario is None:
                raise WorkspaceError("草稿没有有效任务资料")
            if scenario:
                if any(a.task_id not in {t.id for t in scenario.tasks} or a.member_id not in {m.id for m in scenario.members} for a in draft):
                    raise WorkspaceError("草稿引用未知任务或人员")
            if plan.get("preview"):
                preview = plan["preview"]
                return_id = preview.get("returnPlanId")
                if return_id is not None and (not isinstance(return_id, str) or return_id == plan_id or return_id not in state["plans"] or state["plans"][return_id]["day"]["serviceDate"] != day.service_date):
                    raise WorkspaceError("预览返回方案无效")
                if preview["inputRevision"] != plan["inputRevision"]:
                    raise WorkspaceError("预览与当前输入版本不一致")
                previous = _assignments(preview["previousDraft"])
                if not scenario or any(a.task_id not in {t.id for t in scenario.tasks} or a.member_id not in {m.id for m in scenario.members} for a in previous):
                    raise WorkspaceError("预览中的原草稿引用未知任务或人员")
            adopted = plan["adopted"]
            if adopted:
                if "savedRevision" in adopted and (type(adopted["savedRevision"]) is not int or adopted["savedRevision"] < 0):
                    raise WorkspaceError("正式结果归档版本无效")
                formal = scenario_from_dict(adopted["scenario"])
                issues = validate_schedule(formal, _assignments(adopted["assignments"]))
                if issues:
                    raise WorkspaceError("正式结果无效：" + "；".join(i.message for i in issues))
                if not adopted.get("legacyOriginal"):
                    reference = {"day": adopted["day"], "memberIds": adopted["memberIds"], "rotationMinutes": adopted["rotationMinutes"], "rotationEnabled": adopted["rotationEnabled"]}
                    if "balanceSettings" in adopted:
                        reference["balanceSettings"] = adopted["balanceSettings"]
                    for key in ("teamLeaderSettings", "teamLeaderIds", "personnelStates"):
                        if key in adopted:
                            reference[key] = adopted[key]
                    rebuilt, problems = self._scenario(reference)
                    if problems or rebuilt != formal:
                        raise WorkspaceError("正式结果与采用输入快照不一致")

    def _migrate_legacy(self, old):
        raw, assignments, mapping = normalize_saved_scenario(old["scenario"], old["assignments"])
        scenario = scenario_from_dict(raw)
        issues = validate_schedule(scenario, _assignments(assignments))
        if issues:
            raise WorkspaceError("旧结果无法合法迁移：" + "；".join(i.message for i in issues))
        imported = old.get("importedDay")
        settings = old.get("baggageRotationSettings") or {}
        interval, enabled = settings.get("intervalMinutes", 60), settings.get("enabled", True)
        linked = False
        if imported is not None and scenario.model_version == "imported-day-v1":
            rebuilt, problems = self._scenario({"day": imported, "rotationMinutes": interval, "rotationEnabled": enabled, "memberIds": {}})
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
            raise WorkspaceError("尚无正式班表，请先校验并采用；未完成草稿不能正式导出")
        formal = plan["adopted"]
        scenario = scenario_from_dict(formal["scenario"])
        assignments = _assignments(formal["assignments"])
        if validate_schedule(scenario, assignments):
            raise WorkspaceError("正式结果未通过独立校验")
        return export_formal_schedule(scenario, assignments)

    def candidates(self, plan_id, task_id, slot=0):
        state = self._load()
        plan = state["plans"][plan_id]
        scenario, issues = self._scenario(plan)
        if issues or scenario is None:
            return []
        return self._candidate_roster(state, plan, scenario, task_id, slot)

    @staticmethod
    def _candidate_roster(state, plan, scenario, task_id, slot):
        draft = _assignments(plan["draft"])
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

        scenario, issues = self._scenario(plan)
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
            scenario, issues = self._scenario(plan)
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
        scenario, _ = self._scenario(plan)
        try:
            if "cellId" in payload:
                self._edit_cell(plan, payload)
            else:
                if scenario is None:
                    raise WorkspaceError("请先完成当天航班与人员准备，原安排保留")
                plan["draft"] = _assignment_data(replace_slot(scenario, _assignments(plan["draft"]), payload["taskId"], payload["slot"], payload.get("memberId", "")))
        except (ValueError, TypeError) as error:
            return {"valid": False, "revision": state["revision"], "issues": [{"code":"edit", "message":str(error), "taskId":payload.get("taskId"), "memberId":None, "relatedTaskIds":[]}], "suggestions":name_suggestions(payload.get("text", ""), plan["day"].get("roster", []))}
        scenario, issues = self._scenario(plan)
        if scenario:
            issues += issue_data(scenario, validate_schedule(scenario, _assignments(plan["draft"])))
        issues += self._pending_issues(plan)
        return {"valid": bool(scenario and scenario.tasks) and not issues, "revision":state["revision"], "issues":issues, "suggestions":[]}
