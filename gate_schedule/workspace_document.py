"""Saved workspace facts, plan reconstruction and independent document validation."""
from copy import deepcopy
from dataclasses import replace
from datetime import date
import re
from .domain import Assignment, GATE_ROLE_NAMES, BaggageRotationSettings, ImportedRoleConfig, phase_role_config
from .excel_io import imported_day_from_dict, validate_imported_day
from .imported_schedule import imported_day_to_scenario
from .io import scenario_from_dict
from .validator import validate_schedule
from .manual_editing import issue_data, name_suggestions
from .domain import validate_weekly_flights
from .domain import validate_handoff_preferences, default_handoff_preferences, validate_display_preferences, general_handoff_preferences
from .flight_configuration import seed_library, validate_library, with_role_membership
from .domain import default_balance_settings, validate_balance_settings
from .duty_roster import default_roster
from .domain import validate_duty_roster
from .domain import default_team_leader_settings, validate_team_leader_settings, default_half_rest_settings, validate_half_rest_settings
from .domain import MEMBER_STATUSES, personnel_status, personnel_states, validate_personnel_details
from .domain import default_relaxed_shift_settings, validate_relaxed_shift_settings


class WorkspaceError(ValueError):
    pass


def read_assignments(values):
    if not isinstance(values, list):
        raise WorkspaceError("分配必须是列表")
    return tuple(Assignment(item["taskId"], item["memberId"]) for item in values)


def _validate_day_structure(day):
    date.fromisoformat(day.service_date)
    if len(day.flights) != len({f.id for f in day.flights}) or len(day.flights) != len({f.flight_no for f in day.flights}) or any(not f.flight_no.strip() for f in day.flights):
        raise WorkspaceError("航班编号或航班号重复/为空")
    if len(day.cells) != len({c.id for c in day.cells}) or len(day.role_names) != len(set(day.role_names)) or len(day.roster) != len(set(day.roster)):
        raise WorkspaceError("任务格、岗位或当天名单重复")


def role_configuration(role):
    config = phase_role_config(ImportedRoleConfig(role["roleName"], role["requiredCount"]))
    return {**role, "workMode": role.get("workMode", config.work_mode), "concurrent": role.get("concurrent", config.concurrent)}


def with_configuration(state):
    """Add optional v2 configuration without inferring demand or changing saved plans."""
    if isinstance(state, dict) and state.get("stateVersion") == 2 and "configuration" not in state:
        roles = list(GATE_ROLE_NAMES)
        for person in state.get("people", []):
            roles.extend(person["eligibleRoleNames"])
        for plan in state.get("plans", {}).values():
            roles.extend(plan["day"]["roleNames"])
        state["configuration"] = {"roleConfigs": [{"roleName": r, "requiredCount": None} for r in dict.fromkeys(roles)], "rotationMinutes": 60, "rotationEnabled": True, "transitionMinutes": 0}
    return state


def _with_preferences(state):
    if "handoffPreferences" not in state:
        state["handoffPreferences"] = default_handoff_preferences([f["flightNo"] for f in state["flightLibrary"]], [r["roleName"] for r in state["configuration"]["roleConfigs"]])
    validate_handoff_preferences(state["handoffPreferences"], [r["roleName"] for r in state["configuration"]["roleConfigs"]], {f["flightNo"] for f in state["flightLibrary"]} | {f["flightNo"] for p in state["plans"].values() for f in p["day"]["flights"]})
    state["handoffPreferences"] = general_handoff_preferences(state["handoffPreferences"])
    state.setdefault("displayPreferences", {"layout": "flights", "zoom": 100})
    state.setdefault("balanceSettings", default_balance_settings())
    # Owner chose to reselect the new flight/role scope; legacy fields stay
    # as migration evidence and never select new priority combinations.
    state['balanceSettings'].setdefault('priorityFlightRoles', [])
    state.setdefault("teamLeaderSettings", default_team_leader_settings())
    state.setdefault("halfRestSettings", default_half_rest_settings())
    state.setdefault("relaxedShiftSettings", default_relaxed_shift_settings())
    state['relaxedShiftSettings'] = validate_relaxed_shift_settings(state['relaxedShiftSettings'])
    state.setdefault("dutyRoster", default_roster(state))


def plan_scenario(plan):
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
            if plan.get("balanceSettings", default_balance_settings())["priorityEnabled"] and 'priorityFlightRoles' not in plan.get('balanceSettings', {}) else (),
        priority_flight_roles=tuple((p['flightNo'], p['roleName']) for p in plan.get('balanceSettings', {}).get('priorityFlightRoles', []))
            if plan.get('balanceSettings', {}).get('priorityEnabled', False) else (),
        coverage_priorities=tuple((p['roleName'], p['priority']) for p in plan.get('balanceSettings', {}).get('coveragePriorities', [])),
        team_leader_last=plan.get("teamLeaderSettings", default_team_leader_settings())["lastUsed"],
        balance_exempt_team_leaders=plan.get("teamLeaderSettings", default_team_leader_settings())["exemptFlightCount"],
        balance_exempt_half_rest=plan.get("halfRestSettings", default_half_rest_settings())["exemptFlightCount"],
    )
    return scenario, issues


def apply_personnel_policy(plan, state):
    plan["teamLeaderSettings"] = deepcopy(state["teamLeaderSettings"])
    plan["halfRestSettings"] = deepcopy(state["halfRestSettings"])
    plan["teamLeaderIds"] = [p["id"] for p in state["people"] if p.get("teamLeader", False)]
    plan["personnelStates"] = personnel_states(state["people"])


def pending_issues(plan):
    cells = {c["id"]: c for c in plan["day"]["cells"]}
    return [{"code": "pending_cell", "message": f'{cells[key]["flightNo"]} {cells[key]["roleName"]} 已输入“{text}”，请确认人数、工作方式和当天人员并确认安排', "taskId": key, "memberId": None} for key, text in plan.get("cellDrafts", {}).items()]


def archive_review(plan):
    scenario, issues = plan_scenario(plan)
    issues += pending_issues(plan)
    values = read_assignments(plan["draft"])
    if scenario:
        issues += issue_data(scenario, validate_schedule(scenario, values))
    if not scenario or not scenario.tasks:
        issues.append({'code':'no_tasks', 'message':'当前没有已落实的岗位任务，原输入和待完善姓名已保留', 'taskId':None, 'memberId':None, 'relatedTaskIds':[]})
    return scenario, issues


def typed_names(plan, text):
    if not isinstance(text, str) or len(text) > 2000:
        raise WorkspaceError("姓名内容无效或过长")
    names = [n.strip() for n in re.split(r"[/／、,，;；\n]+", text) if n.strip()]
    unknown = set(names) - set(plan["day"]["roster"])
    if unknown:
        suggestions = name_suggestions(text, plan["day"]["roster"])
        raise WorkspaceError("未找到当天人员：" + "、".join(sorted(unknown)) + ("。可选择：" + "、".join(suggestions) if suggestions else "。请先配置人员") + "；原安排保留")
    return names


def sync_roster(plan, people, refresh):
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


def validate_document(state, *, validate_undo=True):
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
    with_configuration(state)
    seed_library(state)
    validate_library(state["flightLibrary"])
    with_role_membership(state)
    _with_preferences(state)
    validate_handoff_preferences(state["handoffPreferences"], [r["roleName"] for r in state["configuration"]["roleConfigs"]], {f["flightNo"] for f in state["flightLibrary"]} | {f["flightNo"] for p in state["plans"].values() for f in p["day"]["flights"]})
    validate_display_preferences(state["displayPreferences"])
    validate_weekly_flights(state.get("weeklyFlightPresets", {}))
    validate_balance_settings(state["balanceSettings"], [r["roleName"] for r in state["configuration"]["roleConfigs"]], {p["id"] for p in state["people"]})
    validate_team_leader_settings(state["teamLeaderSettings"])
    validate_half_rest_settings(state["halfRestSettings"])
    validate_relaxed_shift_settings(state["relaxedShiftSettings"])
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
        validate_half_rest_settings(plan.get("halfRestSettings", default_half_rest_settings()))
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
            validate_document(shadow, validate_undo=False)
        if plan["id"] != plan_id or type(plan["inputRevision"]) is not int:
            raise WorkspaceError("计划身份或版本无效")
        day = imported_day_from_dict(plan["day"])
        if "balanceSettings" in plan:
            validate_balance_settings(plan["balanceSettings"], plan["balanceSettings"]["priorityRoleNames"]
                + [r['roleName'] for r in plan['balanceSettings'].get('priorityFlightRoles', [])]
                + [r['roleName'] for r in plan['balanceSettings'].get('coveragePriorities', [])]
                + [r["roleName"] for r in state["configuration"]["roleConfigs"]])
        BaggageRotationSettings(plan["rotationEnabled"], plan["rotationMinutes"])
        _validate_day_structure(day)
        pending = plan.get("cellDrafts", {})
        if not isinstance(pending, dict) or set(pending) - {c.id for c in day.cells}:
            raise WorkspaceError("待完善姓名格引用无效")
        for text in pending.values():
            if not typed_names(plan, text):
                raise WorkspaceError("待完善姓名格不能为空")
        scenario, _ = plan_scenario(plan)
        draft = read_assignments(plan["draft"])
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
            previous = read_assignments(preview["previousDraft"])
            if not scenario or any(a.task_id not in {t.id for t in scenario.tasks} or a.member_id not in {m.id for m in scenario.members} for a in previous):
                raise WorkspaceError("预览中的原草稿引用未知任务或人员")
        adopted = plan["adopted"]
        if adopted:
            if 'dutyMemberId' in adopted and adopted['dutyMemberId'] is not None and adopted['dutyMemberId'] not in ids:
                raise WorkspaceError('正式结果值班身份无效')
            if "savedRevision" in adopted and (type(adopted["savedRevision"]) is not int or adopted["savedRevision"] < 0):
                raise WorkspaceError("正式结果归档版本无效")
            warning_archive = 'archiveVersion' in adopted
            if warning_archive and (type(adopted['archiveVersion']) is not int or adopted['archiveVersion'] != 1 or not isinstance(adopted.get('issues'), list)):
                raise WorkspaceError('归档问题快照版本或问题清单无效')
            formal = scenario_from_dict(adopted["scenario"]) if adopted.get('scenario') else None
            assignments = read_assignments(adopted['assignments'])
            if assignments and (not formal or any(a.task_id not in {t.id for t in formal.tasks} or a.member_id not in {m.id for m in formal.members} for a in assignments)):
                raise WorkspaceError('归档引用未知任务或人员')
            issues = validate_schedule(formal, assignments) if formal else []
            if not warning_archive and (issues or formal is None):
                raise WorkspaceError("正式结果无效：" + "；".join(i.message for i in issues))
            if warning_archive or not adopted.get("legacyOriginal"):
                reference = {"day": adopted["day"], "memberIds": adopted["memberIds"], "rotationMinutes": adopted["rotationMinutes"], "rotationEnabled": adopted["rotationEnabled"]}
                _validate_day_structure(imported_day_from_dict(reference['day']))
                BaggageRotationSettings(reference['rotationEnabled'], reference['rotationMinutes'])
                if "balanceSettings" in adopted:
                    reference["balanceSettings"] = adopted["balanceSettings"]
                for key in ("teamLeaderSettings", "teamLeaderIds", "personnelStates", "halfRestSettings"):
                    if key in adopted:
                        reference[key] = adopted[key]
                if warning_archive:
                    reference['draft'] = adopted['assignments']
                    reference['cellDrafts'] = adopted.get('cellDrafts', {})
                    if not isinstance(reference['cellDrafts'], dict) or set(reference['cellDrafts']) - {c['id'] for c in reference['day']['cells']}:
                        raise WorkspaceError('归档待完善姓名格引用无效')
                    for text in reference['cellDrafts'].values():
                        if not typed_names(reference, text):
                            raise WorkspaceError('归档待完善姓名格不能为空')
                    rebuilt, problems = archive_review(reference)
                    if problems != adopted['issues']:
                        raise WorkspaceError('归档问题清单与原安排不一致')
                else:
                    rebuilt, problems = plan_scenario(reference)
                if (problems and not warning_archive) or rebuilt != formal:
                    raise WorkspaceError("正式结果与采用输入快照不一致")


def normalize_document(document):
    """Read optional saved fields without changing any plan or inferring demand."""
    state = document if document is not None else {"stateVersion": 2, "timeVersion": 2, "revision": 0, "people": [], "plans": {}, "selectedPlanId": None, "migrationReport": []}
    with_configuration(state)
    seed_library(state)
    with_role_membership(state)
    _with_preferences(state)
    return state
