from __future__ import annotations


def validate_duty_roster(value, member_ids=None):
    """Portable monthly roster inputs. Leave/status is checked on resolved rows."""
    from datetime import date
    from copy import deepcopy
    fields = {"enabled", "anchorDate", "dutyMemberIds", "standbyMemberIds", "overrides"}
    if not isinstance(value, dict) or set(value) != fields or type(value["enabled"]) is not bool:
        raise ValueError("值班备勤设置字段不完整")
    def iso(text):
        if not isinstance(text, str) or date.fromisoformat(text).isoformat() != text:
            raise ValueError("轮值日期必须为YYYY-MM-DD")
        return date.fromisoformat(text)
    anchor = iso(value["anchorDate"])
    for key in ("dutyMemberIds", "standbyMemberIds"):
        ids = value[key]
        if not isinstance(ids, list) or any(not isinstance(i, str) or not i for i in ids) or len(ids) != len(set(ids)) or member_ids is not None and set(ids)-set(member_ids):
            raise ValueError("轮值资质名单包含重复或未知人员")
    rows = value["overrides"]
    if not isinstance(rows, list):
        raise ValueError("人工轮值必须为列表")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"date", "dutyMemberId", "standbyMemberIds"}:
            raise ValueError("人工轮值字段无效")
        day = iso(row["date"])
        if row["date"] in seen or (day-anchor).days % 2:
            raise ValueError("轮值日期重复或不属于基准工作班，请先恢复相关人工安排")
        seen.add(row["date"])
        standby = row["standbyMemberIds"]
        if not isinstance(standby, list) or len(standby) != 2:
            raise ValueError("每班必须保留两个次日备勤位置")
        ids = [row["dutyMemberId"], *standby]
        if any(i is not None and (not isinstance(i, str) or not i) for i in ids):
            raise ValueError("轮值人员ID无效")
        occupied = [i for i in ids if i is not None]
        if len(occupied) != len(set(occupied)) or member_ids is not None and set(occupied)-set(member_ids):
            raise ValueError("值班与两名备勤不能重复或引用未知人员")
    return deepcopy(value)


def validate_weekly_flights(value):
    """Optional weekday templates: Sunday=0. No personnel or duty facts carried."""
    if not isinstance(value, dict) or set(value) - set("0123456"):
        raise ValueError("每周航班的星期必须为0（星期日）至6（星期六）")
    for weekday, flights in value.items():
        if not isinstance(flights, list):
            raise ValueError("每周航班必须为列表")
        seen = set()
        for flight in flights:
            if not isinstance(flight, dict) or set(flight) != {"flightNo", "windowStart", "windowEnd"}:
                raise ValueError("常用航班只能包含航班号和明确保障起止，不可夹带人员安排")
            number = flight["flightNo"]
            if not isinstance(number, str) or not number.strip() or number != number.strip() or number in seen:
                raise ValueError("常用航班号为空或重复")
            seen.add(number)
            if not all(isinstance(flight[k], str) for k in ("windowStart", "windowEnd")) or parse_minutes(flight["windowEnd"]) <= parse_minutes(flight["windowStart"]):
                raise ValueError("常用航班保障时间无效，次日请明确填写")
    return value

from dataclasses import dataclass, field
from typing import Literal
import re


def parse_minutes(value: int | str) -> int:
    """Minutes from the service date; date offsets are explicit, not clock guesses."""
    if type(value) is int:
        if value < 0:
            raise ValueError("时间不能早于服务日")
        return value
    if not isinstance(value, str):
        raise TypeError("时间需要钟点或分钟数")
    match = re.fullmatch(r"(?:(次日)|第(\d+)日)?\s*(\d{2}):(\d{2})", value.strip())
    if not match:
        raise ValueError(f"时间格式无效：{value}；使用23:05或次日00:05")
    tomorrow, days, hours, minutes = match.groups()
    if int(hours) > 23 or int(minutes) > 59:
        raise ValueError(f"钟点无效：{value}")
    return (1 if tomorrow else int(days or 0)) * 1440 + int(hours) * 60 + int(minutes)


def format_minutes(value: int) -> str:
    value = parse_minutes(value)
    days, clock = divmod(value, 1440)
    prefix = "" if days == 0 else "次日" if days == 1 else f"第{days}日"
    return f"{prefix}{clock // 60:02d}:{clock % 60:02d}"


def default_relaxed_shift_settings():
    return {'earlyDepartureCutoffTime': '12:00', 'afternoonRestStartTime': '12:00',
            'afternoonRestEndTime': '18:00', 'lateShiftEndTime': '23:00', 'lateFlightNos': []}


def validate_relaxed_shift_settings(value):
    from copy import deepcopy
    if not isinstance(value, dict) or set(value) != set(default_relaxed_shift_settings()):
        raise ValueError('轻松班次设置字段不完整')
    for key in set(value) - {'lateFlightNos'}:
        if not isinstance(value[key], str) or not re.fullmatch(r'\d{2}:\d{2}', value[key]):
            raise ValueError('轻松班次时间须为当日HH:MM')
        parse_minutes(value[key])
    if parse_minutes(value['afternoonRestStartTime']) >= parse_minutes(value['afternoonRestEndTime']):
        raise ValueError('下午无航班统计结束必须晚于开始')
    numbers = value['lateFlightNos']
    if not isinstance(numbers, list) or any(not isinstance(n, str) or not n.strip() or n != n.strip() for n in numbers) or len(numbers) != len(set(numbers)):
        raise ValueError('末班航班号不能为空或重复')
    return deepcopy(value)


def relaxed_shift_facts(scenario, assignments, settings, duty_member_id=None):
    """One fact per regular person/day from assigned flight tasks, never attendance alone."""
    validate_relaxed_shift_settings(settings)
    tasks = {t.id: t for t in scenario.tasks}
    flights = {f.id: f for f in scenario.flights}
    occupied = {}
    for assignment in assignments:
        task = tasks.get(assignment.task_id)
        if task and task.required_count > 0 and task_flight_ids(task):
            occupied.setdefault(assignment.member_id, {})[task.id] = task
    cutoff = parse_minutes(settings['earlyDepartureCutoffTime'])
    afternoon_start = parse_minutes(settings['afternoonRestStartTime'])
    afternoon_end = parse_minutes(settings['afternoonRestEndTime'])
    late_end = parse_minutes(settings['lateShiftEndTime'])
    selected_late = set(settings['lateFlightNos'])
    result = []
    for member in scenario.members:
        work = list(occupied.get(member.id, {}).values())
        if not work or member.team_leader or member.status != '正常':
            continue
        worked_flights = {identity for t in work for identity in task_flight_ids(t)}
        late = sorted({flights[i].flight_no for i in worked_flights if flights[i].flight_no in selected_late})
        last = max(work, key=lambda t: (t.end, t.start, t.id))
        last_numbers = sorted({flights[i].flight_no for i in task_flight_ids(last)})
        result.append({'memberId': member.id, 'memberName': member.name,
            'lastFlightNo': ' / '.join(last_numbers), 'lastEndTime': format_minutes(last.end),
            'earlyDeparture': member.id != duty_member_id and last.end < cutoff and not late
                and all(flights[i].window_end <= late_end for i in worked_flights),
            'afternoonRest': all(t.end <= afternoon_start or t.start >= afternoon_end for t in work),
            'lateFlightNos': late})
    return result


TaskKind = Literal["fixed", "mobile", "rotation", "support"]
TaskServiceStage = Literal["arrival", "boarding"]
ImportedCellSemantics = Literal["empty", "single", "simultaneous", "rotation", "external", "ambiguous"]

# Common rows from the owner's scheduling facts; demand and qualifications stay explicit.
GATE_ROLE_NAMES = ("督导", "入境申报", "接机1", "接机2", "大件行李拦截", "登机主验", "登机核验", "登机查验（VIP/F/优先通道）", "登机人证核对/队伍秩序维护", "特服")


@dataclass(frozen=True)
class Flight:
    id: str
    flight_no: str
    service_date: str
    window_start: int
    window_end: int
    location_id: str | None = None
    category: str | None = None


@dataclass(frozen=True)
class Member:
    id: str
    name: str
    eligible_role_ids: frozenset[str]
    available_from: int
    available_until: int | None = None
    team_leader: bool = False
    status: str = "正常"
    half_rest: str | None = None


def has_role_qualification(member: Member, role_id: str) -> bool:
    return role_id in member.eligible_role_ids


@dataclass(frozen=True)
class Task:
    id: str
    flight_id: str | None
    role_id: str
    start: int
    end: int
    required_count: int
    task_kind: TaskKind
    location_id: str | None = None
    locked_member_ids: tuple[str, ...] = ()
    service_stage: TaskServiceStage | None = None
    covered_flight_ids: tuple[str, ...] = ()
    timing_mode: str = "exact"
    concurrent: bool = False
    exclusive_same_flight: bool = False


@dataclass(frozen=True)
class Rotation:
    task_id: str
    group_id: str
    segment_start: int
    segment_end: int
    required_count: int
    candidate_member_ids: tuple[str, ...]
    rotation_index: int | None = None


@dataclass(frozen=True)
class ExternalSupportRequirement:
    task_id: str
    count: int
    source_label: str
    resolved_member_name: str | None = None


@dataclass(frozen=True)
class ImportedFlight:
    id: str
    flight_no: str
    window_start: str
    window_end: str
    raw_header: str = ""


@dataclass(frozen=True)
class BaggageRotationSettings:
    enabled: bool = True
    interval_minutes: int = 60

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("大件岗轮换开关必须是布尔值")
        if type(self.interval_minutes) is not int or not 1 <= self.interval_minutes <= 24 * 60:
            raise ValueError("大件岗轮换时长必须在 1 至 1440 分钟之间")


@dataclass(frozen=True)
class BaggageRotationSegment:
    start: str
    end: str


@dataclass(frozen=True)
class FlightHandoffSuggestion:
    source_flight_no: str
    target_flight_no: str
    suggested_from: str
    suggested_until: str
    candidate_names: tuple[str, ...]
    boarding_roles: tuple[str, ...]


@dataclass(frozen=True)
class ImportedCell:
    id: str
    role_name: str
    flight_id: str
    flight_no: str
    raw_value: str
    internal_names: tuple[str, ...]
    external_labels: tuple[str, ...]
    semantics: ImportedCellSemantics
    work_start: str | None = None
    work_end: str | None = None
    required_count_override: int | None = None
    fixed_member_names: tuple[str, ...] = ()
    work_mode_override: str | None = None

    def __post_init__(self):
        if self.work_mode_override is not None and self.work_mode_override not in WORK_MODES:
            raise ValueError(f"{self.flight_no} / {self.role_name} 的工作方式例外无效")


@dataclass(frozen=True)
class ImportedRemark:
    kind: Literal["support", "arrival", "other"]
    raw_text: str
    people: tuple[str, ...] = ()
    time: str | None = None


@dataclass(frozen=True)
class ImportedRoleConfig:
    role_name: str
    required_count: int | None = None
    work_mode: str = "timed"
    concurrent: bool = False
    exclusive_same_flight: bool = False

    def __post_init__(self):
        if self.work_mode not in WORK_MODES or type(self.concurrent) is not bool or type(self.exclusive_same_flight) is not bool:
            raise ValueError(f"岗位 {self.role_name} 的工作环节、兼任或专岗专人开关无效")


@dataclass(frozen=True)
class ImportedMemberConfig:
    member_name: str
    eligible_role_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImportedMemberAvailability:
    member_name: str
    participating: bool = False
    available_from: str | None = None
    available_until: str | None = None
    half_rest: str | None = None

    def __post_init__(self):
        if self.half_rest not in HALF_REST_MODES:
            raise ValueError("半休时段必须为不半休、上午半休或下午半休")


@dataclass(frozen=True)
class ImportedOccupancy:
    id: str
    label: str
    member_names: tuple[str, ...]
    start: str
    end: str


@dataclass(frozen=True)
class ImportedDay:
    service_date: str
    title: str
    flights: tuple[ImportedFlight, ...]
    role_names: tuple[str, ...]
    cells: tuple[ImportedCell, ...]
    remarks: tuple[ImportedRemark, ...]
    role_configs: tuple[ImportedRoleConfig, ...] = ()
    member_configs: tuple[ImportedMemberConfig, ...] = ()
    member_availability: tuple[ImportedMemberAvailability, ...] = ()
    extra_occupancies: tuple[ImportedOccupancy, ...] = ()
    remarks_reviewed: bool = False
    transition_minutes: int = 0
    roster: tuple[str, ...] = ()
    release_policy: str = "flight"
    phase_handoffs: tuple[tuple[str, str], ...] = ()
    attendance_mode: str = "bounded"

    def __post_init__(self):
        if self.attendance_mode not in {"bounded", "duty"}:
            raise ValueError("当天出勤方式无效")


def imported_member_names(day: ImportedDay) -> tuple[str, ...]:
    names = [name for cell in day.cells for name in cell.internal_names]
    names.extend(name for remark in day.remarks for name in remark.people)
    names.extend(day.roster)
    return tuple(dict.fromkeys(names))


def imported_required_count(day: ImportedDay, cell: ImportedCell) -> int | None:
    if cell.required_count_override is not None:
        return cell.required_count_override
    return next((config.required_count for config in day.role_configs if config.role_name == cell.role_name), None)


def imported_work_mode(day: ImportedDay, cell: ImportedCell) -> str:
    return cell.work_mode_override or next((r.work_mode for r in day.role_configs if r.role_name == cell.role_name), "timed")


@dataclass(frozen=True)
class Scenario:
    service_date: str
    flights: tuple[Flight, ...]
    members: tuple[Member, ...]
    tasks: tuple[Task, ...]
    rotations: tuple[Rotation, ...] = ()
    external_support_requirements: tuple[ExternalSupportRequirement, ...] = ()
    model_version: str = "phase-0"
    minimum_transition_minutes: int = 0
    release_policy: str = "flight"
    phase_handoffs: tuple[tuple[str, str], ...] = ()
    flight_count_gap: int | None = None
    priority_role_ids: tuple[str, ...] = ()
    team_leader_last: bool = True
    balance_exempt_team_leaders: bool = True
    balance_exempt_half_rest: bool = True


@dataclass(frozen=True, order=True)
class Assignment:
    task_id: str
    member_id: str


def task_flight_ids(task: Task) -> tuple[str, ...]:
    """Actual flight identities served by one task; shared coverage takes precedence."""
    return tuple(dict.fromkeys(task.covered_flight_ids)) if task.covered_flight_ids else ((task.flight_id,) if task.flight_id is not None else ())


def member_flight_participation(scenario: Scenario, assignments) -> dict[str, frozenset[str]]:
    """All participating members, including zero-flight staff, with deduplicated flights."""
    result = {member.id: set() for member in scenario.members}
    tasks = {task.id: task for task in scenario.tasks}
    known_flights = {flight.id for flight in scenario.flights}
    for assignment in assignments:
        task = tasks.get(assignment.task_id)
        if task is not None and assignment.member_id in result:
            result[assignment.member_id].update(known_flights.intersection(task_flight_ids(task)))
    return {identity: frozenset(flights) for identity, flights in result.items()}


def member_role_flight_counts(scenario: Scenario, assignments, role_ids=None) -> dict[tuple[str, str], int]:
    """Count each member/role/flight once, regardless of compatible duties or segments."""
    roles = tuple(dict.fromkeys(role_ids if role_ids is not None else (task.role_id for task in scenario.tasks)))
    result = {(member.id, role): set() for member in scenario.members for role in roles}
    tasks = {task.id: task for task in scenario.tasks}
    known_flights = {flight.id for flight in scenario.flights}
    for assignment in assignments:
        task = tasks.get(assignment.task_id)
        key = (assignment.member_id, task.role_id) if task is not None else None
        if key in result:
            result[key].update(known_flights.intersection(task_flight_ids(task)))
    return {key: len(flights) for key, flights in result.items()}


def default_balance_settings() -> dict:
    return {"flightCountGap": None, "priorityEnabled": False,
            "priorityRoleNames": [], "priorityAdjustments": []}


def default_team_leader_settings() -> dict:
    return {"lastUsed": True, "exemptFlightCount": True}


def validate_team_leader_settings(value) -> dict:
    if not isinstance(value, dict) or set(value) != set(default_team_leader_settings()) or any(type(v) is not bool for v in value.values()):
        raise ValueError("分队长设置需完整填写最后使用、航班数豁免两个布尔开关")
    return dict(value)


def flight_balance_members(scenario: Scenario) -> tuple[Member, ...]:
    """One owner for the hard flight-count comparison population."""
    return tuple(m for m in scenario.members if member_available(m)
                 and not (scenario.balance_exempt_team_leaders and m.team_leader)
                 and not (scenario.balance_exempt_half_rest and m.half_rest))


HALF_REST_MODES = (None, "early-finish", "late-start")
HALF_REST_LABELS = {None: "不半休", "early-finish": "下午半休", "late-start": "上午半休"}


def default_half_rest_settings():
    return {"exemptFlightCount": True}


def validate_half_rest_settings(value):
    if not isinstance(value, dict) or set(value) != {"exemptFlightCount"} or type(value['exemptFlightCount']) is not bool:
        raise ValueError("半休设置需填写航班数豁免布尔开关")
    return dict(value)


def half_rest_period_violation(member: Member, task: Task) -> str | None:
    """Check operational morning by task start, matching check-in's 06:00–12:00 rule."""
    morning = 360 <= task.start % 1440 < 720
    if member.half_rest == "late-start" and morning:
        return "上午半休人员不得安排06:00至12:00前开始的岗位"
    if member.half_rest == "early-finish" and not morning:
        return "下午半休人员只能安排06:00至12:00前开始的岗位"
    return None


PERSONNEL_STATUSES = ("正常", "病假", "休假")
MEMBER_STATUSES = (*PERSONNEL_STATUSES, "停用", "已删除")


def validate_personnel_details(status, remark):
    if status not in PERSONNEL_STATUSES or not isinstance(remark, str):
        raise ValueError("人员状态必须为正常、病假或休假，备注必须为文本")


def personnel_status(person) -> str:
    """Effective availability, including legacy enablement and retained deletion identity."""
    return "已删除" if person.get("deleted", False) else "停用" if not person["active"] else person.get("status", "正常")


def personnel_states(people) -> dict[str, str]:
    return {p["id"]: personnel_status(p) for p in people if personnel_status(p) != "正常"}


def member_available(member: Member) -> bool:
    return member.status == "正常"


def scarce_role_groups(scenario: Scenario) -> tuple[tuple[str, ...], ...]:
    """Relatively scarce demanded roles, fewest attending qualified people first."""
    roles = {task.role_id for task in scenario.tasks if task.required_count > 0 and task_flight_ids(task)}
    counts = {role: sum(member_available(member) and has_role_qualification(member, role)
                        for member in scenario.members) for role in roles}
    broadest = max(counts.values(), default=0)
    return tuple(tuple(sorted(role for role in roles if counts[role] == count))
                 for count in sorted(set(counts.values())) if count < broadest)


def validate_balance_settings(value, role_names, member_ids=None) -> dict:
    """One configuration contract for editable balance rules and monthly corrections."""
    from copy import deepcopy
    from datetime import date
    import re

    if not isinstance(value, dict) or set(value) != set(default_balance_settings()):
        raise ValueError("重点轮换与航班数设置字段不完整或未知")
    gap = value["flightCountGap"]
    if gap is not None and (type(gap) is not int or gap < 0):
        raise ValueError("当天航班数最大差距必须为空或非负整数")
    if type(value["priorityEnabled"]) is not bool:
        raise ValueError("重点岗位轮换开关必须是布尔值")
    roles = value["priorityRoleNames"]
    catalog = set(role_names)
    if (not isinstance(roles, list)
            or any(not isinstance(role, str) or not role or role != role.strip() or role not in catalog for role in roles)
            or len(roles) != len(set(roles))):
        raise ValueError("重点岗位名单必须为不重复的现有岗位")
    adjustments = value["priorityAdjustments"]
    if not isinstance(adjustments, list):
        raise ValueError("重点岗位次数修正必须为列表")
    known_members = set(member_ids) if member_ids is not None else None
    seen = set()
    for adjustment in adjustments:
        if not isinstance(adjustment, dict) or set(adjustment) != {"month", "memberId", "roleName", "delta"}:
            raise ValueError("重点岗位次数修正字段不完整或未知")
        month, member, role, delta = (adjustment[key] for key in ("month", "memberId", "roleName", "delta"))
        if not isinstance(month, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}", month) is None:
            raise ValueError("重点岗位次数修正月份必须为有效的YYYY-MM")
        try:
            date.fromisoformat(month + "-01")
        except ValueError as error:
            raise ValueError("重点岗位次数修正月份无效") from error
        if not isinstance(member, str) or not member or member != member.strip() or known_members is not None and member not in known_members:
            raise ValueError("重点岗位次数修正人员无效")
        if not isinstance(role, str) or role not in catalog:
            raise ValueError("重点岗位次数修正引用了不存在的岗位")
        if type(delta) is not int:
            raise ValueError("重点岗位次数修正必须是整数")
        key = (month, member, role)
        if key in seen:
            raise ValueError("同月同人同岗的次数修正不能重复")
        seen.add(key)
    return deepcopy(value)


@dataclass(frozen=True)
class Personnel:
    id: str
    name: str
    eligible_role_names: tuple[str, ...] = ()
    active: bool = True
    team_leader: bool = False
    status: str = "正常"
    remark: str = ""


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    task_id: str | None = None
    member_id: str | None = None
    related_task_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SolveResult:
    status: Literal["OPTIMAL", "FEASIBLE", "INFEASIBLE", "TIMED_OUT", "MODEL_INVALID"]
    assignments: tuple[Assignment, ...] = ()
    external_support_by_task: dict[str, int] = field(default_factory=dict)
    diagnostics: tuple[str, ...] = ()


def draft_blocking_issues(issues):
    """Only an unfilled headcount is permitted in an automatic unfinished draft."""
    return [issue for issue in issues if issue.code != "coverage_shortfall"]


WORK_MODES = {"timed": "特殊实际时段", "arrival": "接机环节", "boarding": "登机环节", "continuous": "全程保障"}


def validate_handoff_preferences(preferences, role_names=None, flight_numbers=None):
    """Soft same-flight paths; this grants neither qualification nor concurrency."""
    if not isinstance(preferences, list):
        raise ValueError("岗位接续偏好必须为列表")
    seen = set()
    for item in preferences:
        required = {"flightNo", "arrivalRole", "boardingRoles", "enabled"}
        if not isinstance(item, dict) or not required <= set(item) or set(item) - required - {"ordered"}:
            raise ValueError("岗位接续偏好字段不完整或未知")
        if any(not isinstance(item[k], str) or not item[k].strip() or item[k] != item[k].strip() for k in ("flightNo", "arrivalRole")):
            raise ValueError("接续偏好的航班号和接机岗位不能为空或含首尾空格")
        targets = item["boardingRoles"]
        if not isinstance(targets, list) or not targets or any(not isinstance(r, str) or not r.strip() or r != r.strip() for r in targets) or len(targets) != len(set(targets)) or item["arrivalRole"] in targets:
            raise ValueError("请选择不重复的优先登机岗位，不能与接机岗位相同")
        if type(item["enabled"]) is not bool:
            raise ValueError("接续偏好启用开关必须为布尔值")
        if "ordered" in item and type(item["ordered"]) is not bool:
            raise ValueError("按顺序优先开关必须为布尔值")
        if role_names is not None and {item["arrivalRole"], *targets} - set(role_names):
            raise ValueError("接续偏好引用了不存在的岗位，请核对配置中的岗位名称")
        if flight_numbers is not None and item["flightNo"] != "*" and item["flightNo"] not in flight_numbers:
            raise ValueError("接续偏好引用了不存在的航班，请核对航班模板")
        key = (item["flightNo"], item["arrivalRole"])
        if key in seen:
            raise ValueError("同航班同接机岗位的偏好不能重复")
        seen.add(key)
    return preferences


def preferred_handoff(preferences, flight_no, arrival_role, boarding_role):
    return any(p["enabled"] and p["flightNo"] in ("*", flight_no) and p["arrivalRole"] == arrival_role and boarding_role in p["boardingRoles"] for p in preferences)


def handoff_rank(preferences, flight_no, arrival_role, boarding_role):
    """Lower ranks win after preferred coverage; legacy sets have no ordering."""
    for item in preferences:
        if item["enabled"] and item.get("ordered", False) and item["flightNo"] in ("*", flight_no) and item["arrivalRole"] == arrival_role and boarding_role in item["boardingRoles"]:
            return item["boardingRoles"].index(boarding_role)
    return 0


def general_handoff_preferences(preferences):
    """Owner-confirmed legacy merge; explicit shared settings retain their order."""
    from copy import deepcopy
    validate_handoff_preferences(preferences)
    groups = {}
    for item in preferences:
        groups.setdefault(item["arrivalRole"], []).append(item)
    result = []
    for source, items in groups.items():
        shared = next((p for p in items if p["flightNo"] == "*"), None)
        if shared is not None:
            result.append(deepcopy(shared))
            continue
        active = [p for p in items if p["enabled"]]
        targets = list(dict.fromkeys(r for p in (active or items) for r in p["boardingRoles"]))
        result.append({"flightNo": "*", "arrivalRole": source, "boardingRoles": targets, "enabled": bool(active), "ordered": False})
    return result


def default_handoff_preferences(flight_numbers, role_names):
    """Owner-authorized historical suggestions, editable and never hard constraints."""
    order = "登机人证核对/队伍秩序维护"
    vip = "登机查验（VIP/F/优先通道）"
    main, check = "登机主验", "登机核验"
    paths = {
        "CX937": ((order, vip), (check, vip)), "CX931": ((order, vip), (check, vip)),
        "MF8683": ((main, check), (main, check)), "TW616": ((main, check), (main, check)),
        "AK151": ((order,), (main, check)), "TR121": ((order,), (main,)),
        "FD573": ((order, check), (main, check)), "AE218": ((order, check), (main, check)),
        "KE0166": ((main, check, order), (main, check)), "KE166": ((main, check, order), (main, check)),
    }
    result = []
    for number in flight_numbers:
        for source, targets in zip(("接机1", "接机2"), paths.get(number, ())):
            targets = [r for r in targets if r in role_names]
            if source in role_names and targets:
                result.append({"flightNo": number, "arrivalRole": source, "boardingRoles": targets, "enabled": True})
    return result


def validate_display_preferences(value):
    if not isinstance(value, dict) or set(value) != {"layout", "zoom"} or value["layout"] not in {"flights", "matrix"} or type(value["zoom"]) is not int or not 70 <= value["zoom"] <= 140:
        raise ValueError("表格设置需为按航班/原保障表，大小为70至140")
    return value


def phase_role_config(config: ImportedRoleConfig) -> ImportedRoleConfig:
    """Owner-confirmed starting choices for an explicitly created phase plan.

    Unclassified roles conservatively occupy the whole envelope until edited.
    History never authorizes compatibility.
    """
    from dataclasses import replace
    if config.role_name.startswith("接机"):
        return replace(config, work_mode="arrival")
    if config.role_name.startswith("登机"):
        return replace(config, work_mode="boarding")
    if config.role_name == "督导":
        return replace(config, work_mode="continuous", concurrent=True)
    return replace(config, work_mode="continuous") if config.work_mode == "timed" else config


def prepare_regular_day(day: ImportedDay, implicit_roles: tuple[str, ...], configured_natures: tuple[str, ...] = ()) -> ImportedDay:
    """Prepare routine scheduling; explicit special-time choices are never replaced."""
    from dataclasses import replace
    configs = {r.role_name: r for r in day.role_configs}
    prepared = []
    for role in day.role_names:
        original = configs.get(role, ImportedRoleConfig(role))
        config = phase_role_config(original) if role in implicit_roles else original
        if role in configured_natures:
            config = replace(config, concurrent=original.concurrent)
        prepared.append(config)
    return replace(day, release_policy="task", attendance_mode="duty", role_configs=tuple(prepared))


def attendance_bounds(day: ImportedDay, entry: ImportedMemberAvailability):
    """Selected duty staff have no special time bound until a complete pair is supplied."""
    start, end = entry.available_from, entry.available_until
    if start in (None, "") and end in (None, ""):
        if day.attendance_mode == "duty" or not entry.participating:
            return (0, None), None
    if not start or not end:
        return None, ValidationIssue("incomplete_availability", f"人员 {entry.member_name} 请填写完整的到岗和离岗时间，或清空两项取消特殊限制")
    try:
        bounds = parse_minutes(start), parse_minutes(end)
    except (TypeError, ValueError):
        return None, ValidationIssue("invalid_availability", f"人员 {entry.member_name} 的到岗或离岗时间无效")
    if bounds[1] <= bounds[0]:
        return None, ValidationIssue("availability_order", f"人员 {entry.member_name} 的离岗时间必须晚于到岗时间")
    return bounds, None


def phase_handoff_options(day: ImportedDay) -> list[dict]:
    options = []
    for source in day.flights:
        for target in day.flights:
            if source.id == target.id:
                continue
            try:
                ss, se = parse_minutes(source.window_start), parse_minutes(source.window_end)
                ts, te = parse_minutes(target.window_start), parse_minutes(target.window_end)
            except (ValueError, TypeError):
                continue
            if (ss <= ts and ss < te and ts < se) or (source.id, target.id) in day.phase_handoffs:
                options.append({"sourceId": source.id, "targetId": target.id, "sourceNo": source.flight_no, "targetNo": target.flight_no})
    return options


def task_timing_label(task: Task) -> str:
    if task.timing_mode == "phase":
        return {"arrival": "接机环节", "boarding": "登机环节"}.get(task.service_stage, "全程保障") + (" · 可兼任" if task.concurrent else "")
    return f"{format_minutes(task.start)}—{format_minutes(task.end)}" + (" · 可兼任" if task.concurrent else "")


def task_pair_conflict(scenario: Scenario, first: Task, second: Task) -> tuple[str, str] | None:
    """One owner for time/phase/concurrency facts; callers never infer minutes.

    A phase interval is an envelope, not a fabricated actual working interval.
    Same-flight concurrent duties do not occupy an additional ordinary position.
    Pairwise checking keeps that permission from spreading to two ordinary duties.
    """
    shared_flights = set(task_flight_ids(first)).intersection(task_flight_ids(second))
    if shared_flights and first.role_id != second.role_id and (first.exclusive_same_flight or second.exclusive_same_flight):
        dedicated = first if first.exclusive_same_flight else second
        numbers = "、".join(f.flight_no for f in scenario.flights if f.id in shared_flights)
        return "exclusive_same_flight", f"{numbers} 的 {dedicated.role_id} 已开启同航班专岗专人，不能由同一人兼做该航班其他岗位"
    overlaps = first.start < second.end and second.start < first.end
    same_flight = first.flight_id is not None and first.flight_id == second.flight_id
    if same_flight and (first.concurrent or second.concurrent) and not ({first.task_kind, second.task_kind} & {"rotation", "support"}):
        return None
    arrival, boarding = (first, second) if first.service_stage == "arrival" and second.service_stage == "boarding" else (second, first) if second.service_stage == "arrival" and first.service_stage == "boarding" else (None, None)
    if first.timing_mode == second.timing_mode == "phase" and arrival is not None:
        if same_flight:
            return None
        if overlaps and (arrival.flight_id, boarding.flight_id) in scenario.phase_handoffs:
            if scenario.minimum_transition_minutes:
                return "phase_transition_unknown", "跨航班衔接已确认，但尚无实际交接时刻来核对走动分钟；请为这两个岗位填写特殊实际时段"
            return None
        if overlaps:
            return "phase_handoff_unconfirmed", "重叠航班的接机后登机衔接尚未确认；请在规则中确认人员确实能接上，或填写特殊实际时段"
    if overlaps:
        suffix = "（环节范围尚未证明可以衔接；可补特殊实际时段）" if "phase" in (first.timing_mode, second.timing_mode) else ""
        return "overlap", f"任务 {first.id} 与 {second.id} 冲突{suffix}"
    if not same_flight and first.flight_id and second.flight_id and scenario.minimum_transition_minutes:
        gap = min(abs(first.start - second.end), abs(second.start - first.end))
        if gap < scenario.minimum_transition_minutes:
            return "insufficient_transition", f"两航班之间仅有 {gap} 分钟，少于走动预留 {scenario.minimum_transition_minutes} 分钟"
    # Retain old adopted snapshots' explicit historical contract. New plans use task release.
    if scenario.release_policy == "flight" and arrival is not None and not same_flight:
        source = next((f for f in scenario.flights if f.id == arrival.flight_id), None)
        if source and boarding.start < source.window_end:
            return "cross_flight_before_release", f"旧方案要求等接机航班 {source.flight_no} 整体结束才能支援；可另建环节方案使用本人任务完成规则"
    return None
