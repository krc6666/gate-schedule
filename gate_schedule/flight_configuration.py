"""Persistent flight templates; personnel and past assignments never live here."""
from copy import deepcopy
from uuid import uuid4
from .domain import ImportedRoleConfig, WORK_MODES, validate_weekly_flights, parse_minutes


def validate_library(library):
    if not isinstance(library, list):
        raise ValueError("航班模板必须为列表")
    validate_weekly_flights({"0": [{k: f[k] for k in ("flightNo", "windowStart", "windowEnd")} for f in library]})
    for flight in library:
        if set(flight) - {"flightNo", "windowStart", "windowEnd", "roles", "roleNames"}:
            raise ValueError("航班模板不能包含旧人员、固定安排或出勤")
        if "roleNames" in flight:
            names = flight["roleNames"]
            if not isinstance(names, list) or any(not isinstance(n, str) or not n.strip() or n != n.strip() for n in names) or len(names) != len(set(names)):
                raise ValueError("本航班岗位必须为不重复的岗位名称列表")
        roles = flight.get("roles", [])
        if not isinstance(roles, list) or len(roles) != len({r["roleName"] for r in roles}):
            raise ValueError("模板岗位重复或无效")
        for role in roles:
            if set(role) - {"roleName", "requiredCount", "workModeOverride", "workStart", "workEnd"}:
                raise ValueError("模板岗位字段无效")
            if not isinstance(role["roleName"], str) or not role["roleName"].strip():
                raise ValueError("模板岗位名称不能为空")
            count = role.get("requiredCount")
            if count is not None and (type(count) is not int or count < 0):
                raise ValueError("模板人数必须为空、0或正整数")
            if role.get("workModeOverride") is not None and role["workModeOverride"] not in WORK_MODES:
                raise ValueError("模板岗位方式无效")
            for key in ("workStart", "workEnd"):
                if role.get(key):
                    parse_minutes(role[key])
            if role.get("workStart") and role.get("workEnd") and parse_minutes(role["workEnd"]) <= parse_minutes(role["workStart"]):
                raise ValueError("模板特殊时段无效")
            for key in ("requiredCount", "workModeOverride", "workStart", "workEnd"):
                role.setdefault(key, None)
    return library


def with_role_membership(state):
    """Materialize legacy inheritance once; later catalog additions are opt-in."""
    names = [r["roleName"] for r in state["configuration"]["roleConfigs"]]
    for flight in state["flightLibrary"]:
        flight.setdefault("roleNames", list(names))
        if set(flight["roleNames"]) - set(names):
            raise ValueError("本航班岗位引用了不存在的通用岗位")


def seed_library(state):
    if "flightLibrary" in state:
        return
    catalog = {}
    for plan in sorted(state["plans"].values(), key=lambda p: p["day"]["serviceDate"]):
        day = plan["day"]
        defaults = {r["roleName"]: r for r in day["roleConfigs"]}
        for flight in day["flights"]:
            roles = []
            for cell in day["cells"]:
                if cell["flightId"] != flight["id"]:
                    continue
                roles.append({"roleName": cell["roleName"], "requiredCount": cell.get("requiredCountOverride") if cell.get("requiredCountOverride") is not None else defaults.get(cell["roleName"], {}).get("requiredCount"),
                              "workModeOverride": cell.get("workModeOverride"), "workStart": cell.get("workStart"), "workEnd": cell.get("workEnd")})
            catalog[flight["flightNo"]] = {k: flight[k] for k in ("flightNo", "windowStart", "windowEnd")}
            catalog[flight["flightNo"]]["roles"] = roles
    for flights in state.get("weeklyFlightPresets", {}).values():
        for flight in flights:
            catalog.setdefault(flight["flightNo"], deepcopy(flight))
    state["flightLibrary"] = list(catalog.values())


def save_template(state, payload):
    seed_library(state)
    number = payload["flightNo"].strip()
    existing = next((f for f in state["flightLibrary"] if f["flightNo"] == number), {})
    template = {**deepcopy(existing), **deepcopy(payload), "flightNo": number}
    library = [f for f in state["flightLibrary"] if f["flightNo"] != number] + [template]
    state["flightLibrary"] = validate_library(library)
    with_role_membership(state)


def remember_day(state, day):
    """Import is an explicit configuration update; ordinary saved drafts never update defaults."""
    shadow = {"plans": {"incoming": {"day": day}}, "weeklyFlightPresets": {}}
    seed_library(shadow)
    for flight in shadow["flightLibrary"]:
        save_template(state, flight)


def fill_day(state, day, flight_numbers):
    if not isinstance(flight_numbers, list) or len(flight_numbers) != len(set(flight_numbers)):
        raise ValueError("航班选择无效")
    catalog = {f["flightNo"]: f for f in state["flightLibrary"]}
    if set(flight_numbers) - set(catalog):
        raise ValueError("所选航班模板不存在")
    for number in flight_numbers:
        template = catalog[number]
        flight = {"id": str(uuid4()), **{k: template[k] for k in ("flightNo", "windowStart", "windowEnd")}}
        day["flights"].append(flight)
        roles = {r["roleName"]: r for r in template.get("roles", [])}
        for role_name in day["roleNames"]:
            rule = roles.get(role_name, {})
            day["cells"].append({"id": str(uuid4()), "flightId": flight["id"], "flightNo": number, "roleName": role_name,
                                 "rawValue": "", "internalNames": [], "externalLabels": [], "semantics": "empty", "fixedMemberNames": [],
                                 "requiredCountOverride": rule.get("requiredCount") if role_name in template["roleNames"] else 0, "workModeOverride": rule.get("workModeOverride") if role_name in template["roleNames"] else None, "workStart": rule.get("workStart") if role_name in template["roleNames"] else None, "workEnd": rule.get("workEnd") if role_name in template["roleNames"] else None})
