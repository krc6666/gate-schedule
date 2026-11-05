"""Prepare configuration transfers in memory; workbook encoding is an output adapter."""
from copy import deepcopy
from datetime import date
from uuid import uuid4
from .domain import format_minutes
from .excel_io import imported_day_from_dict, validate_complete_configuration, export_complete_configuration
from .io import scenario_from_dict
from .flight_configuration import remember_day, with_role_membership
from .excel_io import configuration_day
from .domain import validate_balance_settings
from .domain import validate_duty_roster
from .domain import personnel_status
from .domain import imported_required_count
from .configuration_history import merge_history
from .domain import task_flight_ids
from dataclasses import dataclass
from .workspace_document import (
    WorkspaceError, normalize_document, with_configuration, validate_document,
    plan_scenario, apply_personnel_policy, sync_roster, role_configuration,
)


@dataclass(frozen=True)
class PreparedImport:
    state: dict
    preview: dict


@dataclass(frozen=True)
class PreparedExport:
    payload: dict

    def excel(self):
        return export_complete_configuration(self.payload, history_schedules=_history_views(self.payload['history']['state']))


def prepare_import(state, payload):
    """Build and validate a complete candidate; neither argument is modified."""
    state = normalize_document(deepcopy(state))
    expected = state['revision']
    preview = _apply_configuration(state, payload)
    state['revision'] = expected + 1
    validate_document(state)
    return PreparedImport(state, {**preview, 'expectedRevision': expected})


def _apply_configuration(state, payload):
    data = validate_complete_configuration(payload)
    original_people = {p['id'] for p in state['people']}
    history_summary = {key: 0 for key in ('historyDayCount', 'historyPlanCount', 'newHistoryPlanCount', 'retainedHistoryPlanCount')}
    if 'history' in data:
        history = with_configuration(deepcopy(data['history']['state']))
        validate_document(history)
        history_summary = merge_history(state, history)
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
    added, removed, new_people = 0, 0, len({p['id'] for p in state['people']} - original_people)
    roles = set(day["roleNames"])
    for member in day["memberConfigs"]:
        name = member["memberName"]
        if name not in by_name and name in data.get('peopleIds', {}):
            person = next((p for p in state['people'] if p['id'] == data['peopleIds'][name] and not p.get('deleted', False)), None)
            if person is not None:
                by_name.pop(person['name'], None)
                person['name'] = name
                by_name[name] = person
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
        settings.setdefault('priorityFlightRoles', deepcopy(state['balanceSettings'].get('priorityFlightRoles', [])))
        if 'coveragePriorities' in state['balanceSettings']:
            settings.setdefault('coveragePriorities', deepcopy(state['balanceSettings']['coveragePriorities']))
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
    if "relaxedShiftSettings" in data:
        state["relaxedShiftSettings"] = deepcopy(data["relaxedShiftSettings"])
    if "halfRestSettings" in data:
        state["halfRestSettings"] = deepcopy(data["halfRestSettings"])
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
    apply_personnel_policy(plan, state)
    sync_roster(plan, state["people"], refresh=False)
    plan["rotationEnabled"] = daily_settings.get("rotationEnabled", plan["rotationEnabled"])
    plan["rotationMinutes"] = daily_settings.get("rotationMinutes", plan["rotationMinutes"])
    state["plans"][plan_id] = plan
    state["selectedPlanId"] = plan_id
    return {"payload": data, **history_summary, "newPeopleCount": new_people, "newQualificationCount": added, "removedQualificationCount": removed, "peopleCount": len(day["roster"]), "flightCount": len(day["flights"]), "serviceDate": day["serviceDate"], "handoffPreferenceCount": len(state["handoffPreferences"])}


def _history_views(state):
    result = {}
    for identity, plan in state['plans'].items():
        versions = {}
        scenario, _ = plan_scenario(plan)
        if scenario:
            versions['草稿'] = (scenario, plan['draft'])
        if plan.get('adopted') and plan['adopted'].get('scenario'):
            formal = plan['adopted']
            versions['正式'] = (scenario_from_dict(formal['scenario']), formal['assignments'])
        result[identity] = {}
        for version, (scenario, assignments) in versions.items():
            flights = {f.id: f.flight_no for f in scenario.flights}
            result[identity][version] = {'serviceDate': scenario.service_date,
                'members': [{'id': m.id, 'name': m.name} for m in scenario.members],
                'tasks': [{'id': t.id, 'roleId': t.role_id, 'flightNos': [flights[f] for f in task_flight_ids(t)],
                           'start': format_minutes(t.start), 'end': format_minutes(t.end)} for t in scenario.tasks],
                'assignments': assignments}
    return result


def prepare_export(state, plan_id=None, service_date=None):
    state = normalize_document(deepcopy(state))
    history = {"formatVersion": 2, "state": deepcopy(state)}
    state["people"] = [p for p in state["people"] if not p.get("deleted", False)]
    current_ids = {p["id"] for p in state["people"]}
    for key in ('dutyMemberIds', 'standbyMemberIds'):
        state['dutyRoster'][key] = [i for i in state['dutyRoster'][key] if i in current_ids]
    for row in state['dutyRoster']['overrides']:
        if row['dutyMemberId'] not in current_ids: row['dutyMemberId'] = None
        row['standbyMemberIds'] = [i if i in current_ids else None for i in row['standbyMemberIds']]
    state["balanceSettings"]["priorityAdjustments"] = [a for a in state["balanceSettings"]["priorityAdjustments"] if a["memberId"] in current_ids]
    plan_id = plan_id or state['selectedPlanId']
    if plan_id is not None and plan_id not in state['plans']:
        raise WorkspaceError('当天计划不存在，请刷新页面')
    if plan_id is None:
        day = {'serviceDate': service_date or date.today().isoformat(), 'title': '登机口配置',
            'roleNames': [r['roleName'] for r in state['configuration']['roleConfigs']],
            'flights': [], 'cells': [], 'remarks': [], 'roster': [], 'memberConfigs': [],
            'memberAvailability': [], 'roleConfigs': deepcopy(state['configuration']['roleConfigs']),
            'extraOccupancies': [], 'remarksReviewed': True, 'releasePolicy': 'task', 'attendanceMode': 'duty',
            'transitionMinutes': state['configuration']['transitionMinutes']}
        plan = {'day': day, 'memberIds': {}, 'rotationEnabled': state['configuration']['rotationEnabled'],
                'rotationMinutes': state['configuration']['rotationMinutes']}
    else:
        plan = state['plans'][plan_id]
    # Export latest maintained configuration, even if today's draft was not rerun.
    export_plan = deepcopy(plan)
    sync_roster(export_plan, state["people"], refresh=True)
    day = export_plan["day"]
    saved_day = deepcopy(day)
    # refresh above only normalizes stable identities; daily qualifications remain explicit.
    renames = {name: next((p["name"] for p in state["people"] if p["id"] == identity), name) for name, identity in plan.get("memberIds", {}).items()}
    saved_day["memberConfigs"] = [{**deepcopy(m), "memberName": renames.get(m["memberName"], m["memberName"])} for m in plan["day"]["memberConfigs"]]
    config = state["configuration"]
    day["roleNames"] = list(dict.fromkeys([*day["roleNames"], *(r["roleName"] for r in config["roleConfigs"])]))
    roles = {r["roleName"]: r for r in day["roleConfigs"]}
    roles.update({r["roleName"]: role_configuration(r) for r in config["roleConfigs"]})
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
    saved_roles = {r["roleName"]: role_configuration(r) for r in saved_day["roleConfigs"]}
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
    payload = {"history": history, "day": day, "dayOverrides": overrides, "peopleStatus": {p["name"]: p.get("status", "正常") for p in state["people"]}, "peopleRemarks": {p["name"]: p.get("remark", "") for p in state["people"]}, "peopleActive": {p["name"]: p["active"] for p in state["people"]}, "peopleTeamLeaders": {p["name"]: p.get("teamLeader", False) for p in state["people"]}, "teamLeaderSettings": state["teamLeaderSettings"], "halfRestSettings": state["halfRestSettings"], "relaxedShiftSettings": state["relaxedShiftSettings"], "dutyRoster": state["dutyRoster"], "peopleIds": {p["name"]: p["id"] for p in state["people"]}, "balanceSettings": state["balanceSettings"], "handoffPreferences": state["handoffPreferences"], "displayPreferences": state["displayPreferences"], "flightLibrary": state["flightLibrary"], "weeklyFlightPresets": state.get("weeklyFlightPresets", {}), "rotationEnabled": config["rotationEnabled"], "rotationMinutes": config["rotationMinutes"], "source": plan.get("configurationSource", "当前最新长期配置，含当天航班与例外")}
    # A complete export explicitly writes disabled switches. Old imported files
    # may omit them, so materialize the domain values only on this export path.
    flags = {r.role_name: r.exclusive_same_flight for r in imported_day_from_dict(payload['day']).role_configs}
    for role in payload['day']['roleConfigs']:
        role['exclusiveSameFlight'] = flags[role['roleName']]
    return PreparedExport(validate_complete_configuration(payload))
