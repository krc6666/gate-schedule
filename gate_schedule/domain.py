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
        return value
    if not isinstance(value, str):
        raise TypeError("时间需要钟点或分钟数")
    match = re.fullmatch(r"(?:(前日)|前第(\d+)日|(次日)|第(\d+)日)?\s*(\d{2}):(\d{2})", value.strip())
    if not match:
        raise ValueError(f"时间格式无效：{value}；使用23:05、前日23:40或次日00:05")
    previous, previous_days, tomorrow, days, hours, minutes = match.groups()
    if int(hours) > 23 or int(minutes) > 59:
        raise ValueError(f"钟点无效：{value}")
    day_offset = -(1 if previous else int(previous_days or 0)) if previous or previous_days else (1 if tomorrow else int(days or 0))
    return day_offset * 1440 + int(hours) * 60 + int(minutes)


def format_minutes(value: int) -> str:
    value = parse_minutes(value)
    days, clock = divmod(value, 1440)
    prefix = "" if days == 0 else "次日" if days == 1 else f"第{days}日" if days > 1 else "前日" if days == -1 else f"前第{-days}日"
    return f"{prefix}{clock // 60:02d}:{clock % 60:02d}"


def default_relaxed_shift_settings():
    return {'earlyDepartureCutoffTime': '12:00', 'afternoonRestStartTime': '12:00',
            'afternoonRestEndTime': '18:00', 'lateShiftEndTime': '23:00', 'lateFlightNos': [],
            'earlyDepartureBalanceEnabled': True, 'earlyDepartureMaxDifference': 1,
            'afternoonRestBalanceEnabled': True, 'afternoonRestMaxDifference': 1}


def validate_relaxed_shift_settings(value, base=None):
    from copy import deepcopy
    defaults = default_relaxed_shift_settings()
    balance_keys = {k for k in defaults if k.endswith(('Enabled', 'Difference'))}
    if not isinstance(value, dict) or set(value) - set(defaults) or set(defaults) - balance_keys - set(value):
        raise ValueError('轻松班次设置字段不完整')
    value = {**defaults, **(base or {}), **value}
    for key in balance_keys:
        if key.endswith('Enabled'):
            if type(value[key]) is not bool:
                raise ValueError('轻松班次均衡开关须为是或否')
        elif type(value[key]) is not int or not 0 <= value[key] <= 31:
            raise ValueError('轻松班次目标差距须为0至31的整数')
    for key in set(value) - {'lateFlightNos'} - balance_keys:
        if not isinstance(value[key], str) or not re.fullmatch(r'\d{2}:\d{2}', value[key]):
            raise ValueError('轻松班次时间须为当日HH:MM')
        parse_minutes(value[key])
    if parse_minutes(value['afternoonRestStartTime']) >= parse_minutes(value['afternoonRestEndTime']):
        raise ValueError('下午无航班统计结束必须晚于开始')
    numbers = value['lateFlightNos']
    if not isinstance(numbers, list) or any(not isinstance(n, str) or not n.strip() or n != n.strip() for n in numbers) or len(numbers) != len(set(numbers)):
        raise ValueError('末班航班号不能为空或重复')
    return deepcopy(value)


def relaxed_shift_task_groups(scenario, settings):
    """Shared classification for display facts and the optimization compiler."""
    settings = validate_relaxed_shift_settings(settings)
    flights = {f.id: f for f in scenario.flights}
    work = tuple(t for t in scenario.tasks if t.required_count > 0 and task_flight_ids(t))
    cutoff = parse_minutes(settings['earlyDepartureCutoffTime'])
    start, end = (parse_minutes(settings[k]) for k in ('afternoonRestStartTime', 'afternoonRestEndTime'))
    late_end = parse_minutes(settings['lateShiftEndTime'])
    late = set(settings['lateFlightNos'])
    return work, {
        'earlyDeparture': frozenset(t.id for t in work if t.end >= cutoff or any(
            flights[i].window_end > late_end or flights[i].flight_no in late for i in task_flight_ids(t))),
        'afternoonRest': frozenset(t.id for t in work if t.start < end and t.end > start),
    }


def relaxed_shift_facts(scenario, assignments, settings, duty_member_id=None):
    """One fact per regular person/day from assigned flight tasks, never attendance alone."""
    settings = validate_relaxed_shift_settings(settings)
    work_tasks, blockers = relaxed_shift_task_groups(scenario, settings)
    tasks = {t.id: t for t in work_tasks}
    flights = {f.id: f for f in scenario.flights}
    occupied = {}
    for assignment in assignments:
        task = tasks.get(assignment.task_id)
        if task and task.required_count > 0 and task_flight_ids(task):
            occupied.setdefault(assignment.member_id, {})[task.id] = task
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
            'earlyDeparture': member.id != duty_member_id and not any(t.id in blockers['earlyDeparture'] for t in work),
            'afternoonRest': not any(t.id in blockers['afternoonRest'] for t in work),
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
    arrival_time: int | None = None
    departure_time: int | None = None


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
    arrival_time: str | None = None
    departure_time: str | None = None


def validate_relative_timing(value):
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {'anchor', 'offsetMinutes', 'durationMinutes'}
            or value['anchor'] not in {'arrival', 'departure'}
            or type(value['offsetMinutes']) is not int
            or type(value['durationMinutes']) is not int or not 1 <= value['durationMinutes'] <= 1440):
        raise ValueError('相对岗位时间须明确起降基准、整数偏移和1至1440分钟持续时长')
    return dict(value)


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
    relative_timing: dict | None = None

    def __post_init__(self):
        if self.work_mode not in WORK_MODES or type(self.concurrent) is not bool or type(self.exclusive_same_flight) is not bool:
            raise ValueError(f"岗位 {self.role_name} 的工作环节、兼任或专岗专人开关无效")
        validate_relative_timing(self.relative_timing)


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
    work_rules: dict | None = None
    work_rules_override: bool = False
    task_handoffs: tuple[str, ...] = ()
    dedicated_flight_protection: bool = True
    flow_preferences: tuple[dict, ...] | None = None

    def __post_init__(self):
        if self.attendance_mode not in {"bounded", "duty"}:
            raise ValueError("当天出勤方式无效")
        if type(self.work_rules_override) is not bool or type(self.dedicated_flight_protection) is not bool or (self.work_rules_override and self.work_rules is None) or any(not isinstance(k,str) for k in self.task_handoffs):
            raise ValueError('当天岗位搭配覆盖或衔接确认无效')
        if self.work_rules is not None:
            validate_work_rules(self.work_rules)
        if self.flow_preferences is not None:
            validate_handoff_preferences(list(self.flow_preferences))


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


def imported_task_timing(day: ImportedDay, cell: ImportedCell):
    """Resolve explicit actual times, relative rules, then the phase envelope."""
    mode = imported_work_mode(day, cell)
    config = next((r for r in day.role_configs if r.role_name == cell.role_name), None)
    flight = next((f for f in day.flights if f.id == cell.flight_id), None)
    if flight is None:
        return None, ValidationIssue('unknown_flight', f'{cell.flight_no} 引用未知航班', cell.id)
    window = (day.work_rules or {}).get('baggageWindow')
    if cell.role_name == '大件行李拦截' and window is not None:
        start = parse_minutes(flight.window_end) - window['startBeforeMinutes']
        end = parse_minutes(flight.departure_time or flight.window_end) - window['endBeforeMinutes']
        if end <= start:
            return None, ValidationIssue('invalid_baggage_window', f'{cell.flight_no} 大件截载必须晚于开检，请核对模板结束、起飞和提前分钟', cell.id)
        return (start, end, 'exact'), None
    if day.flow_preferences is not None:
        start, end = flow_flight_window(flight)
        return (start, end, 'phase'), None
    relative = config.relative_timing if config else None
    if cell.role_name == '大件行李拦截' and relative is None:
        # Old baggage cells can retain obsolete ordinary-cell times. Shared
        # coverage has always used flight windows unless a new rule opts in.
        return (parse_minutes(flight.window_start), parse_minutes(flight.window_end), 'phase'), None
    explicit = mode == 'timed' and (cell.work_start is not None or cell.work_end is not None)
    if explicit:
        if not cell.work_start or not cell.work_end:
            return None, ValidationIssue('missing_work_time', f'{cell.role_name} / {cell.flight_no} 请填写完整岗位起止', cell.id)
        start, end = parse_minutes(cell.work_start), parse_minutes(cell.work_end)
        return (start, end, 'exact'), None
    if relative is not None:
        base = flight.departure_time if relative['anchor'] == 'departure' else flight.arrival_time
        if base is None:
            return None, ValidationIssue('missing_relative_anchor', f'{cell.role_name} / {cell.flight_no} 缺少相对时间起降基准', cell.id)
        start = parse_minutes(base) + relative['offsetMinutes']
        return (start, start + relative['durationMinutes'], 'exact'), None
    if mode == 'timed' and cell.role_name != '大件行李拦截':
        return None, ValidationIssue('missing_work_time', f'{cell.role_name} / {cell.flight_no} 尚未填写岗位起止时间', cell.id)
    return (parse_minutes(flight.window_start), parse_minutes(flight.window_end), 'phase'), None


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
    work_rules: dict | None = None
    task_handoffs: tuple[str, ...] = ()
    flow_preferences: tuple[dict, ...] | None = None
    flight_count_gap: int | None = None
    priority_role_ids: tuple[str, ...] = ()
    team_leader_last: bool = True
    balance_exempt_team_leaders: bool = True
    balance_exempt_half_rest: bool = True
    priority_flight_roles: tuple[tuple[str, str], ...] = ()
    coverage_priorities: tuple[tuple[str, str], ...] = ()
    dedicated_flight_protection: bool = True
    coverage_role_order: tuple[str, ...] = ()


@dataclass(frozen=True, order=True)
class Assignment:
    task_id: str
    member_id: str


def task_flight_ids(task: Task) -> tuple[str, ...]:
    """Actual flight identities served by one task; shared coverage takes precedence."""
    return tuple(dict.fromkeys(task.covered_flight_ids)) if task.covered_flight_ids else ((task.flight_id,) if task.flight_id is not None else ())


COVERAGE_PRIORITIES = {'protect': '优先保岗', 'normal': '正常安排', 'leave-empty': '缺人时优先留空'}


def validate_coverage_priorities(items, role_names=None):
    if not isinstance(items, list):
        raise ValueError('保岗优先级必须为列表')
    seen = set()
    for item in items:
        if (not isinstance(item, dict) or set(item) != {'roleName', 'priority'}
                or any(not isinstance(v, str) or not v or v != v.strip() for v in item.values())
                or item['priority'] not in COVERAGE_PRIORITIES):
            raise ValueError('保岗优先级需明确岗位及有效档位')
        role = item['roleName']
        if role in seen or role_names is not None and role not in role_names:
            raise ValueError('保岗优先级重复或引用未知岗位')
        seen.add(role)


def task_coverage_priority(scenario: Scenario, task: Task) -> str:
    """One role policy applies across flights, shared tasks and rotation segments."""
    return dict(scenario.coverage_priorities).get(task.role_id, 'normal')


def validate_coverage_role_order(items, role_names=None):
    if (not isinstance(items, list)
            or any(not isinstance(role, str) or not role or role != role.strip() for role in items)
            or len(items) != len(set(items))
            or role_names is not None and not set(items) <= set(role_names)):
        raise ValueError('同档保岗顺序必须是不重复的有效岗位列表')


def coverage_order_weights(scenario: Scenario) -> dict[str, int]:
    """Bounded, additive preference within each tier; never a staffing constraint.

    Omitted roles tie with the last listed role. Empty/one-role tiers have no
    preference. Callers must retain the existing coverage objectives first.
    """
    priorities = dict(scenario.coverage_priorities)
    weights = {}
    for tier in COVERAGE_PRIORITIES:
        roles = [role for role in scenario.coverage_role_order if priorities.get(role, 'normal') == tier]
        weights.update((role, len(roles) - index - 1) for index, role in enumerate(roles))
    return weights


def coverage_signature(scenario: Scenario, assignments) -> tuple[int, ...]:
    """Staffed counts of the existing coverage goals, including shared tasks once."""
    counts = {}
    for assignment in assignments:
        counts[assignment.task_id] = counts.get(assignment.task_id, 0) + 1
    return tuple(sum(counts.get(t.id, 0) for t in group) for group in coverage_task_groups(scenario))


def coverage_task_groups(scenario: Scenario) -> tuple[tuple[Task, ...], ...]:
    """Dedicated coverage first, then flight end, then maintained role priority.

    Shared rotation segments represent one position even across flight columns.
    Flight envelopes determine urgency; a task's precise end cannot reorder its flight.
    """
    ends = {f.id: f.window_end for f in scenario.flights}
    by_end = {}
    for task in scenario.tasks:
        end = min((ends[f] for f in task_flight_ids(task)), default=task.end)
        by_end.setdefault(end, []).append(task)
    dedicated = tuple(t for t in scenario.tasks if t.exclusive_same_flight)
    groups = [dedicated] if dedicated else []
    for end in sorted(by_end):
        for priority in COVERAGE_PRIORITIES:
            group = tuple(t for t in by_end[end] if task_coverage_priority(scenario, t) == priority)
            if group and group != dedicated:
                groups.append(group)
    return tuple(groups)


def single_switch_role_settings(role):
    """Normalize newly maintained input, never archived role/task snapshots."""
    exclusive = role.get('exclusiveSameFlight', False)
    if type(exclusive) is not bool or ('concurrent' in role and type(role['concurrent']) is not bool):
        raise ValueError('专岗专人开关必须为布尔值')
    return {**role, 'concurrent': not exclusive}


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
    for member_id, flight_id, role in member_flight_role_participation(scenario, assignments):
        key = member_id, role
        if key in result:
            result[key].add(flight_id)
    return {key: len(flights) for key, flights in result.items()}


def member_flight_role_participation(scenario: Scenario, assignments) -> frozenset[tuple[str, str, str]]:
    """Actual member/flight-id/role facts; split and shared tasks count once."""
    tasks = {task.id: task for task in scenario.tasks}
    members = {member.id for member in scenario.members}
    flights = {flight.id for flight in scenario.flights}
    return frozenset((a.member_id, flight_id, tasks[a.task_id].role_id)
        for a in assignments if a.member_id in members and a.task_id in tasks
        for flight_id in task_flight_ids(tasks[a.task_id]) if flight_id in flights)


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


def validate_priority_flight_roles(items, role_names=None):
    if not isinstance(items, list):
        raise ValueError('航班重点岗位必须为列表')
    seen = set()
    for item in items:
        if (not isinstance(item, dict) or set(item) != {'flightNo', 'roleName'}
                or any(not isinstance(v, str) or not v or v != v.strip() for v in item.values())):
            raise ValueError('重点岗位必须明确填写航班号和岗位')
        pair = item['flightNo'], item['roleName']
        if pair in seen or role_names is not None and pair[1] not in role_names:
            raise ValueError('航班重点岗位重复或引用未知岗位')
        seen.add(pair)


def validate_balance_settings(value, role_names, member_ids=None) -> dict:
    """One configuration contract for editable balance rules and monthly corrections."""
    from copy import deepcopy
    from datetime import date
    import re

    required = set(default_balance_settings())
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - {'priorityFlightRoles', 'coveragePriorities', 'coverageRoleOrder'}:
        raise ValueError("重点轮换与航班数设置字段不完整或未知")
    gap = value["flightCountGap"]
    if gap is not None and (type(gap) is not int or gap < 0):
        raise ValueError("当天航班数最大差距必须为空或非负整数")
    if type(value["priorityEnabled"]) is not bool:
        raise ValueError("重点岗位轮换开关必须是布尔值")
    roles = value["priorityRoleNames"]
    catalog = set(role_names)
    if 'coveragePriorities' in value:
        validate_coverage_priorities(value['coveragePriorities'], catalog)
    if 'coverageRoleOrder' in value:
        validate_coverage_role_order(value['coverageRoleOrder'], catalog)
    if 'priorityFlightRoles' in value:
        validate_priority_flight_roles(value['priorityFlightRoles'], catalog)
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
        if not isinstance(adjustment, dict) or set(adjustment) not in ({"month", "memberId", "roleName", "delta"}, {"month", "memberId", "roleName", "delta", "flightNo"}):
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
        flight = adjustment.get('flightNo')
        if 'flightNo' in adjustment and (not isinstance(flight, str) or not flight or flight != flight.strip()):
            raise ValueError('重点次数修正航班号无效')
        key = (month, member, flight, role)
        if key in seen:
            raise ValueError("同月同人同航班同岗的次数修正不能重复")
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

    @property
    def severity(self):
        return 'warning' if self.code == 'coverage_order_unmet' else 'error'


@dataclass(frozen=True)
class SolveResult:
    status: Literal["OPTIMAL", "FEASIBLE", "INFEASIBLE", "TIMED_OUT", "MODEL_INVALID"]
    assignments: tuple[Assignment, ...] = ()
    external_support_by_task: dict[str, int] = field(default_factory=dict)
    diagnostics: tuple[str, ...] = ()


def draft_blocking_issues(issues):
    """Shortfalls and soft preference notices never waive a hard violation."""
    return [issue for issue in issues if issue.code != "coverage_shortfall" and issue.severity != 'warning']


WORK_MODES = {"timed": "特殊实际时段", "arrival": "接机环节", "boarding": "登机环节", "continuous": "全程保障"}


def validate_flight_scope(value):
    """Existing exact numbers, global *, or one explicit trailing prefix star."""
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError('适用航班不能为空或含首尾空格')
    if '*' in value and value != '*' and (value.count('*') != 1 or not value.endswith('*') or not value[:-1].isascii() or not value[:-1].isalnum()):
        raise ValueError('航班系列请填写如CX*，只支持字母数字前缀和一个末尾星号')
    return value


def applicable_flight_rule(rows, flight_no):
    """Choose one row: exact > longest prefix > global, including disabled rows.

    Callers first filter by their own role/pair/rotation key. No merging here.
    """
    def priority(row):
        scope = row['flightNo']
        if scope == '*':
            return 0
        if flight_no and scope == flight_no:
            return len(flight_no) + 1
        if flight_no and scope.endswith('*') and flight_no.startswith(scope[:-1]):
            return len(scope) - 1
        return -1
    return max((r for r in rows if priority(r) >= 0), key=priority, default=None)


def validate_handoff_preferences(preferences, role_names=None, flight_numbers=None):
    """Stored source/ordered-target rules; qualification remains independent."""
    if not isinstance(preferences, list):
        raise ValueError("岗位接续偏好必须为列表")
    seen = set()
    for item in preferences:
        required = {"flightNo", "arrivalRole", "boardingRoles", "enabled"}
        if not isinstance(item, dict) or not required <= set(item) or set(item) - required - {"ordered", "targetFlightScopes"}:
            raise ValueError("岗位接续偏好字段不完整或未知")
        if any(not isinstance(item[k], str) or not item[k].strip() or item[k] != item[k].strip() for k in ("flightNo", "arrivalRole")):
            raise ValueError("接续优先级的航班号和来源岗位不能为空或含首尾空格")
        targets = item["boardingRoles"]
        if not isinstance(targets, list) or (not targets and item['enabled'] is not False) or any(not isinstance(r, str) or not r.strip() or r != r.strip() for r in targets) or len(targets) != len(set(targets)) or item["arrivalRole"] in targets:
            raise ValueError("请选择不重复的目标岗位，不能与来源岗位相同")
        if type(item["enabled"]) is not bool:
            raise ValueError("接续偏好启用开关必须为布尔值")
        if "ordered" in item and type(item["ordered"]) is not bool:
            raise ValueError("按顺序优先开关必须为布尔值")
        scopes = item.get('targetFlightScopes', {})
        if not isinstance(scopes,dict) or set(scopes)-set(targets) or any(not isinstance(v,str) or v not in {'same-flight','later-overlap'} for v in scopes.values()):
            raise ValueError('接续航班范围无效，须对应已选目标岗位')
        if role_names is not None and unknown_flow_rule_roles({item["arrivalRole"], *targets}, role_names):
            raise ValueError("接续偏好引用了不存在的岗位，请核对配置中的岗位名称")
        validate_flight_scope(item['flightNo'])
        if flight_numbers is not None and '*' not in item["flightNo"] and item["flightNo"] not in flight_numbers:
            raise ValueError("接续偏好引用了不存在的航班，请核对航班模板")
        key = (item["flightNo"], item["arrivalRole"])
        if key in seen:
            raise ValueError("同航班同接机岗位的偏好不能重复")
        seen.add(key)
    return preferences


def applicable_handoff_preference(preferences, flight_no, arrival_role):
    """An explicit flight/source exception replaces the shared path, even off."""
    return applicable_flight_rule((p for p in preferences if p['arrivalRole'] == arrival_role), flight_no)


def preferred_handoff(preferences, flight_no, arrival_role, boarding_role):
    item = applicable_handoff_preference(preferences, flight_no, arrival_role)
    return bool(item and item['enabled'] and boarding_role in item['boardingRoles'])


def handoff_rank(preferences, flight_no, arrival_role, boarding_role):
    """Lower ranks win after preferred coverage; legacy sets have no ordering."""
    item = applicable_handoff_preference(preferences, flight_no, arrival_role)
    if item and item['enabled'] and item.get('ordered', False) and boarding_role in item['boardingRoles']:
        return item['boardingRoles'].index(boarding_role)
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


def compile_attendance_bounds(day: ImportedDay):
    """Resolve this day's entries, scanning its default start only when first needed.

    Keep the resolver local to one conversion or validation. It must never be
    reused for a different day or after changing that day's rule inputs.
    """
    default_start = None

    def resolve(entry: ImportedMemberAvailability):
        nonlocal default_start
        start, end = entry.available_from, entry.available_until
        if start in (None, "") and end in (None, ""):
            if day.attendance_mode == "duty" or not entry.participating:
                if default_start is None:
                    starts = [0]
                    for cell in day.cells:
                        if imported_required_count(day, cell) in (None, 0):
                            continue
                        try:
                            timing, issue = imported_task_timing(day, cell)
                            if not issue:
                                starts.append(timing[0])
                        except (ValueError, TypeError):
                            pass  # Invalid task inputs are reported by their own validator.
                    for occupancy in day.extra_occupancies:
                        try:
                            starts.append(parse_minutes(occupancy.start))
                        except (ValueError, TypeError):
                            pass
                    default_start = min(starts)
                return (default_start, None), None
        if not start or not end:
            return None, ValidationIssue("incomplete_availability", f"人员 {entry.member_name} 请填写完整的到岗和离岗时间，或清空两项取消特殊限制")
        try:
            bounds = parse_minutes(start), parse_minutes(end)
        except (TypeError, ValueError):
            return None, ValidationIssue("invalid_availability", f"人员 {entry.member_name} 的到岗或离岗时间无效")
        if bounds[1] <= bounds[0]:
            return None, ValidationIssue("availability_order", f"人员 {entry.member_name} 的离岗时间必须晚于到岗时间")
        return bounds, None

    return resolve


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


def task_timing_label(task: Task, scenario: Scenario | None = None) -> str:
    nature = " · 专人专岗" if task.exclusive_same_flight else " · 可兼任" if task.concurrent else ""
    if task.timing_mode == "phase":
        return {"arrival": "接机环节", "boarding": "登机环节"}.get(task.service_stage, "全程保障") + nature
    turn = f'{task_rule_roles(scenario,task)[0]} · ' if scenario and task.role_id=='大件行李拦截' else ''
    return turn + f"{format_minutes(task.start)}—{format_minutes(task.end)}" + nature


def empty_work_rules():
    return {'pairs': [], 'baggage': []}


def default_baggage_window():
    return dict(startBeforeMinutes=180,endBeforeMinutes=60,rotationMinutes=60,remainderToleranceMinutes=15)


def unchanged_task_ids(before: Scenario | None, after: Scenario | None) -> set[str]:
    """A reused identifier alone never proves that an assignment still fits."""
    previous = {task.id: task for task in before.tasks} if before else {}
    return {task.id for task in after.tasks if previous.get(task.id) == task} if after else set()


def baggage_turn_bounds(start: int, end: int, interval: int, tolerance: int | None = None) -> tuple[tuple[int, int], ...]:
    """An explicit tolerance decides whether a remainder needs another person."""
    if end <= start or interval <= 0:
        raise ValueError('大件保障窗口与轮值时长必须为正')
    full, remainder = divmod(end - start, interval)
    count = max(1, full + int(tolerance is not None and remainder > tolerance))
    return tuple((start + index * interval,
                  end if index == count - 1 else start + (index + 1) * interval)
                 for index in range(count))


def baggage_coverage_summary(scenario):
    """Project actual generated turns, without recomputing their timing in UI."""
    tasks={t.id:t for t in scenario.tasks}
    flights={f.id:f.flight_no for f in scenario.flights}
    groups={}
    for rotation in scenario.rotations:
        if rotation.task_id in tasks and tasks[rotation.task_id].role_id=='大件行李拦截':
            groups.setdefault(rotation.group_id,[]).append(rotation)
    result=[]
    for rotations in groups.values():
        turns={}
        covered=[]
        for rotation in sorted(rotations,key=lambda r:r.segment_start):
            index=rotation.rotation_index if rotation.rotation_index is not None else rotation.task_id
            bounds=turns.setdefault(index,[rotation.segment_start,rotation.segment_end,task_rule_roles(scenario,tasks[rotation.task_id])[0]])
            bounds[0]=min(bounds[0],rotation.segment_start);bounds[1]=max(bounds[1],rotation.segment_end)
            covered.extend(task_flight_ids(tasks[rotation.task_id]))
        result.append(dict(start=format_minutes(min(r.segment_start for r in rotations)),
            end=format_minutes(max(r.segment_end for r in rotations)),requiredPeople=len(turns),
            flights=[flights[f] for f in dict.fromkeys(covered) if f in flights],
            turns=[dict(start=format_minutes(a),end=format_minutes(b),role=role) for a,b,role in turns.values()]))
    return dict(requiredPeople=sum(g['requiredPeople'] for g in result),groups=result)


def baggage_rotation_conflict(scenario,first,second):
    """One distinct person per turn of a merged pool; fragments stay one turn."""
    if (scenario.work_rules or {}).get('baggageWindow') is None:
        return None
    if first.role_id!='大件行李拦截' or second.role_id!='大件行李拦截':
        return None
    rotations={r.task_id:r for r in scenario.rotations}
    a,b=rotations.get(first.id),rotations.get(second.id)
    if (a and b and a.group_id==b.group_id
            and (a.rotation_index if a.rotation_index is not None else a.task_id)
                != (b.rotation_index if b.rotation_index is not None else b.task_id)):
        return 'baggage_rotation_person', '同一段大件合并保障每轮必须安排不同人员，一人只能承担一轮'
    return None


def validate_baggage_window(value):
    """Opt-in baggage timing; absence never changes an archived contract."""
    if value is None:
        return None
    required = {'startBeforeMinutes','endBeforeMinutes','rotationMinutes'}
    if (not isinstance(value, dict) or not required <= set(value) or set(value) - required - {'remainderToleranceMinutes'}
            or any(type(n) is not int for n in value.values())
            or not 0 <= value['endBeforeMinutes'] < value['startBeforeMinutes'] <= 1440
            or not 1 <= value['rotationMinutes'] <= 1440):
        raise ValueError('大件保障规则需0至1440分钟提前量，开检提前大于截载提前，轮值1至1440分钟')
    if 'remainderToleranceMinutes' in value and not 0 <= value['remainderToleranceMinutes'] <= 1440:
        raise ValueError('大件尾段宽限须为0至1440分钟的整数')
    return value


def validate_work_rules(value, role_names=None):
    if not isinstance(value, dict) or not {'pairs', 'baggage'} <= set(value) or set(value) - {'pairs','baggage','separationGroups','forbiddenHandoffs','baggageWindow'} or any(not isinstance(value[k], list) for k in value if k != 'baggageWindow'):
        raise ValueError('岗位搭配设置须包含岗位关系和大件轮次去向')
    validate_baggage_window(value.get('baggageWindow'))
    seen_forbidden = set()
    for row in value.get('forbiddenHandoffs', []):
        if not isinstance(row,dict) or set(row) != {'flightNo','sourceRole','targets','enabled'}:
            raise ValueError('禁止衔接字段不完整或未知')
        if any(not isinstance(row[k],str) or not row[k].strip() or row[k]!=row[k].strip() for k in ('flightNo','sourceRole')) or type(row['enabled']) is not bool:
            raise ValueError('禁止衔接的来源航班、岗位或开关无效')
        validate_flight_scope(row['flightNo'])
        if not isinstance(row['targets'],list) or (row['enabled'] and not row['targets']):
            raise ValueError('启用的禁止衔接至少需要一个目标')
        targets=set()
        for target in row['targets']:
            if (not isinstance(target,dict) or set(target)!={'role','flightScope'}
                    or not isinstance(target['role'],str) or not target['role'].strip() or target['role']!=target['role'].strip()
                    or target['role']==row['sourceRole'] or not isinstance(target['flightScope'],str)
                    or target['flightScope'] not in {'same-flight','later-overlap'}):
                raise ValueError('禁止衔接目标岗位或航班范围无效')
            key=(target['role'],target['flightScope'])
            if key in targets:
                raise ValueError('禁止衔接同一目标岗位及航班范围重复')
            targets.add(key)
        if role_names is not None and unknown_flow_rule_roles({row['sourceRole'],*(t['role'] for t in row['targets'])}, role_names):
            raise ValueError('禁止衔接引用未知岗位')
        key=(row['flightNo'],row['sourceRole'])
        if key in seen_forbidden:
            raise ValueError('同来源航班、同来源岗位的禁止衔接设置重复')
        seen_forbidden.add(key)
    seen_groups = set()
    for row in value.get('separationGroups', []):
        if not isinstance(row, dict) or set(row) != {'id','name','flightNo','roles','enabled'}:
            raise ValueError('岗位分人组字段不完整或未知')
        if any(not isinstance(row[k],str) or not row[k].strip() or row[k] != row[k].strip() for k in ('id','name','flightNo')):
            raise ValueError('分人组名称、身份和适用航班不能为空或含首尾空格')
        validate_flight_scope(row['flightNo'])
        roles = row['roles']
        if type(row['enabled']) is not bool or not isinstance(roles,list) or any(not isinstance(r,str) or not r.strip() or r!=r.strip() for r in roles) or len(roles)!=len(set(roles)):
            raise ValueError('分人组开关或岗位列表无效，岗位不能重复')
        if row['enabled'] and len(roles) < 2:
            raise ValueError('启用的分人组至少需要两个岗位')
        if role_names is not None and unknown_flow_rule_roles(roles, role_names):
            raise ValueError('分人组引用未知岗位')
        key = (row['id'],row['flightNo'])
        if key in seen_groups:
            raise ValueError('同一分人组在同一航班范围的设置重复')
        seen_groups.add(key)
    seen = set()
    for row in value['pairs']:
        if not isinstance(row, dict) or set(row) != {'flightNo', 'firstRole', 'secondRole', 'scope', 'mode'}:
            raise ValueError('岗位搭配字段不完整或未知')
        if any(not isinstance(row[k], str) or not row[k].strip() or row[k] != row[k].strip() for k in row):
            raise ValueError('岗位搭配名称不能为空或含首尾空格')
        validate_flight_scope(row['flightNo'])
        if row['scope'] not in {'same-flight', 'later-flight', 'after-baggage'} or row['mode'] not in {'separate', 'sequence', 'concurrent'}:
            raise ValueError('岗位搭配范围或方式无效')
        if row['firstRole'] == row['secondRole'] or (row['scope'] != 'same-flight' and row['mode'] == 'concurrent') or (row['scope'] == 'after-baggage' and row['firstRole'] != '大件行李拦截'):
            raise ValueError('不能设置同岗搭配或跨航班同时兼做')
        roles = (row['firstRole'], row['secondRole'])
        if role_names is not None and set(roles) - set(role_names):
            raise ValueError('岗位搭配引用未知岗位')
        key = (row['flightNo'], row['scope'], *(sorted(roles) if row['scope'] == 'same-flight' else roles))
        if key in seen:
            raise ValueError('同一作用范围的岗位搭配重复')
        seen.add(key)
    seen = set()
    for row in value['baggage']:
        if not isinstance(row, dict) or set(row) != {'flightNo', 'position', 'targetRoles', 'enabled'}:
            raise ValueError('大件轮次去向字段不完整或未知')
        if not isinstance(row['flightNo'], str) or not row['flightNo'].strip() or row['flightNo'] != row['flightNo'].strip() or type(row['position']) is not int or row['position'] < 1 or type(row['enabled']) is not bool:
            raise ValueError('大件去向航班、轮次或开关无效')
        validate_flight_scope(row['flightNo'])
        roles = row['targetRoles']
        if not isinstance(roles, list) or any(not isinstance(r,str) or not r.strip() or r != r.strip() or r == '大件行李拦截' for r in roles) or len(roles) != len(set(roles)) or (row['enabled'] and not roles):
            raise ValueError('启用的大件去向须选择不重复的后续岗位')
        if role_names is not None and set(roles) - set(role_names):
            raise ValueError('大件去向引用未知岗位')
        key = (row['flightNo'],row['position'])
        if key in seen:
            raise ValueError('大件同航班同轮次去向重复')
        seen.add(key)
    return value


def forbidden_handoff_conflict(scenario, first, second):
    """A prohibition forbids sharing a person, even through another flow path.

    Scope belongs to the source flight. Check every covered flight of shared
    tasks, and select its override before filtering the target list.
    """
    rows=(scenario.work_rules or {}).get('forbiddenHandoffs', [])
    if not rows:
        return None
    flights={f.id:f for f in scenario.flights}
    for source,target in ((first,second),(second,first)):
        for identity in task_flight_ids(source):
            flight=flights.get(identity)
            if flight is None:
                continue
            row=flow_source_rule(rows, 'sourceRole', task_rule_roles(scenario,source)[0], flight.flight_no)
            if not row or not row['enabled']:
                continue
            for entry in row['targets']:
                if entry['role'] not in task_rule_roles(scenario,target):
                    continue
                same=entry['flightScope']=='same-flight' and identity in task_flight_ids(target)
                later=entry['flightScope']=='later-overlap' and any(
                    other in flights and other!=identity and flight.window_end<flights[other].window_end
                    and flight.window_start<flights[other].window_end and flights[other].window_start<flight.window_end
                    for other in task_flight_ids(target))
                if same or later:
                    scope='本航班' if same else '重叠且晚结束的其他航班'
                    return 'forbidden_handoff',f'{flight.flight_no} 已设置禁止衔接：{source.role_id} 与{scope}的 {target.role_id} 不能由同一人承担'
    return None


def separation_group_conflict(scenario, first, second):
    """Choose scope per stable group identity before checking its membership."""
    shared = set(task_flight_ids(first)) & set(task_flight_ids(second))
    if not shared:
        return None
    rows = (scenario.work_rules or {}).get('separationGroups', [])
    for flight in scenario.flights:
        if flight.id not in shared:
            continue
        for identity in dict.fromkeys(r['id'] for r in rows):
            row = applicable_flight_rule((r for r in rows if r['id']==identity), flight.flight_no)
            if (row and row['enabled']
                    and any(a != b and a in row['roles'] and b in row['roles']
                            for a in task_rule_roles(scenario,first) for b in task_rule_roles(scenario,second))):
                return 'role_separation', f"{flight.flight_no} 的分人组“{row['name']}”要求 {first.role_id} 与 {second.role_id} 由不同人员承担"
    return None


def grouped_work_rules(value):
    """Explicit editor conversion, never applied to saved formal snapshots.

    Pair exceptions that permit sharing become disabled group exceptions.
    Converted pairs retain sequence permission under the hard group: an empty
    pairs list enables legacy implicit concurrency for unrelated duties, so
    removing the last pair would silently broaden the existing contract.
    """
    from copy import deepcopy
    import json
    validate_work_rules(value)
    result = deepcopy(value)
    pairs = result['pairs']
    keys = {tuple(sorted((r['firstRole'],r['secondRole']))) for r in pairs if r['scope']=='same-flight' and r['mode']=='separate'}
    if not keys:
        return result
    groups = result.setdefault('separationGroups', [])
    for roles in sorted(keys):
        base = 'pair:' + json.dumps(roles,ensure_ascii=False,separators=(',',':'))
        identity = base
        suffix = 2
        while any(r['id']==identity for r in groups):
            identity=f'{base}:{suffix}';suffix+=1
        for row in pairs:
            if row['scope']=='same-flight' and tuple(sorted((row['firstRole'],row['secondRole'])))==roles:
                groups.append(dict(id=identity,name=' / '.join(roles),flightNo=row['flightNo'],roles=list(roles),enabled=row['mode']=='separate'))
    result['pairs']=[{**r,'mode':'sequence'} if r['scope']=='same-flight' and r['mode']=='separate' else r for r in pairs]
    return result


def suggested_work_rules(role_names):
    # Explicit owner-confirmed preset; never inferred from historical names.
    boarding = ['登机查验（VIP/F/优先通道）', '登机人证核对/队伍秩序维护']
    pairs = [('接机1','接机2','same-flight','separate'), (*boarding,'same-flight','separate')]
    pairs += [('入境申报', r, scope, mode) for r in boarding for scope,mode in [('same-flight','separate'),('later-flight','sequence')]]
    pairs += [('大件行李拦截',r,'after-baggage','sequence') for r in ['入境申报',*boarding]]
    result = empty_work_rules()
    result['pairs'] = [dict(flightNo='*',firstRole=a,secondRole=b,scope=s,mode=m) for a,b,s,m in pairs if a in role_names and b in role_names]
    # Rotation numbers and one-hour examples are not fixed business routing.
    # Optional preferences are maintained explicitly by the owner.
    return result


def work_pair_rule(scenario, source, target, scope):
    rows = (scenario.work_rules or {}).get('pairs', [])
    if not rows:
        return None
    flights = {f.id: f for f in scenario.flights}
    owner_id = target.flight_id if scope == 'after-baggage' else source.flight_id
    number = flights[owner_id].flight_no if owner_id in flights else None
    matches = [r for r in rows if r['scope'] == scope
        and ((r['firstRole'],r['secondRole']) == (source.role_id,target.role_id)
             or scope == 'same-flight' and (r['firstRole'],r['secondRole']) == (target.role_id,source.role_id))]
    return applicable_flight_rule(matches, number)


def baggage_position(scenario, task):
    rotation = next((r for r in scenario.rotations if r.task_id == task.id),None)
    return rotation.rotation_index + 1 if rotation and rotation.rotation_index is not None else None


def is_baggage_turn_role(role):
    import re
    return isinstance(role,str) and re.fullmatch(r'大件[1-9][0-9]*',role) is not None


def unknown_flow_rule_roles(roles, role_names):
    catalog=set(role_names)
    return {role for role in roles if role not in catalog
            and not ('大件行李拦截' in catalog and is_baggage_turn_role(role))}


def task_rule_roles(scenario, task):
    """Most specific rule identity first; qualifications keep the physical role."""
    if task.role_id != '大件行李拦截':
        return (task.role_id,)
    return (f'大件{baggage_position(scenario,task) or 1}', task.role_id)


def flow_source_rule(rows, field, role, flight_no):
    names=(role,'大件行李拦截') if is_baggage_turn_role(role) else (role,)
    for name in names:
        row=applicable_flight_rule((r for r in rows if r[field]==name),flight_no)
        if row is not None:
            return row
    return None


def flow_rule_roles(role_names, preferences=(), rule_sets=(), scenarios=(), *, role_configs=()):
    """Editable flow choices exclude dedicated roles, including physical turn aliases.

    Stored rule references remain untouched; historical tasks never override the
    current group's maintained dedicated switch for future editing choices.
    """
    dedicated = {r['roleName'] for r in role_configs if r.get('exclusiveSameFlight', False)}
    result=[role for role in dict.fromkeys(role_names)
            if role not in dedicated and not ('大件行李拦截' in dedicated and is_baggage_turn_role(role))]
    if '大件行李拦截' not in result:
        return result
    extra={f'大件{i}' for i in range(1,4)}
    for row in preferences:
        extra.update((row['arrivalRole'],*row['boardingRoles']))
    for rules in rule_sets:
        for row in (rules or {}).get('separationGroups',[]):extra.update(row['roles'])
        for row in (rules or {}).get('forbiddenHandoffs',[]):extra.update((row['sourceRole'],*(t['role'] for t in row['targets'])))
    for scenario in scenarios:
        if scenario:
            for task in scenario.tasks:
                if task.role_id=='大件行李拦截':extra.add(task_rule_roles(scenario,task)[0])
    return list(dict.fromkeys((*result,*sorted((r for r in extra if is_baggage_turn_role(r)),key=lambda r:(len(r[2:]),r[2:])))))


def baggage_targets(scenario, source, target):
    position = baggage_position(scenario,source)
    number = next((f.flight_no for f in scenario.flights if f.id == target.flight_id),None)
    rows = [r for r in (scenario.work_rules or empty_work_rules())['baggage'] if r['position'] == position]
    row = applicable_flight_rule(rows, number)
    return row['targetRoles'] if row and row['enabled'] else []


def flow_flight_window(flight):
    """Whole-flight envelope, never an inferred duration or task start."""
    arrival = getattr(flight, 'arrival_time', None)
    departure = getattr(flight, 'departure_time', None)
    return (parse_minutes(arrival if arrival is not None else flight.window_start),
            parse_minutes(departure if departure is not None else flight.window_end))


def flow_preference(scenario, source):
    number = next((f.flight_no for f in scenario.flights if f.id == source.flight_id), None)
    return flow_source_rule(scenario.flow_preferences or (), 'arrivalRole', task_rule_roles(scenario,source)[0], number)


def flow_target_rank(scenario, source, target):
    row = flow_preference(scenario, source)
    if row and row['enabled']:
        for index,role in enumerate(row['boardingRoles']):
            if role in task_rule_roles(scenario,target) and flow_target_scope_allows(scenario, source, target, row,role):
                return index
    return None


def flow_target_scope_allows(scenario, source, target, row, role=None):
    """Explicit target scope filters paths; missing scope retains old behavior."""
    scope = row.get('targetFlightScopes', {}).get(role or target.role_id, 'standard')
    shared = set(task_flight_ids(source)) & set(task_flight_ids(target))
    if scope == 'same-flight':
        return bool(shared)
    if scope == 'later-overlap':
        return not shared and _later_flight_target(scenario,source,target)
    flights = {f.id:f for f in scenario.flights}
    return bool(shared) or not any(flights[a].window_start < flights[b].window_end and flights[b].window_start < flights[a].window_end
        for a in task_flight_ids(source) for b in task_flight_ids(target) if a in flights and b in flights)


def flow_later_overlap_permitted(scenario, source, target):
    """Only an explicit direct ordinary-process route may bridge overlap.

    Real rotation/support blocks, shared pools and dedicated duties never
    acquire this permission from a transitive path or a default setting.
    """
    if (set(task_flight_ids(source)) & set(task_flight_ids(target))
            or len(task_flight_ids(source)) != 1 or len(task_flight_ids(target)) != 1
            or source.task_kind != 'mobile' or target.task_kind != 'mobile'
            or source.timing_mode != 'phase' or target.timing_mode != 'phase'
            or source.exclusive_same_flight or target.exclusive_same_flight):
        return False
    row = flow_preference(scenario,source)
    return bool(row and row['enabled'] and target.role_id in row['boardingRoles']
        and row.get('targetFlightScopes',{}).get(target.role_id) == 'later-overlap'
        and _later_flight_target(scenario,source,target))


def flow_roles_connected(scenario, source, target):
    """Transitive process permission; pairwise hard separation still wins.

    A route A -> B -> C permits A/C coexistence. It does not permit B/D just
    because both appear as alternatives after A. Cycles terminate here; the
    solver separately forbids choosing cyclic route edges for one person.
    """
    number = next((f.flight_no for f in scenario.flights if f.id == source.flight_id), None)
    pending, visited = [task_rule_roles(scenario,source)[0]], set()
    while pending:
        role = pending.pop()
        if role in visited:
            continue
        visited.add(role)
        row = flow_source_rule(scenario.flow_preferences or (), 'arrivalRole', role, number)
        if row and row['enabled']:
            targets = [r for r in row['boardingRoles'] if flow_target_scope_allows(scenario,source,target,row,r)]
            if set(task_rule_roles(scenario,target)) & set(targets):
                return True
            pending.extend(targets)
    return False


def task_handoff_key(source, target, service_date):
    import json
    return json.dumps([[t.id,t.flight_id,t.role_id,t.start,t.end,t.timing_mode,t.task_kind,list(t.covered_flight_ids),t.required_count,service_date] for t in (source,target)],ensure_ascii=False,separators=(',',':'))


def _later_flight_target(scenario, source, target):
    flights = {f.id:f for f in scenario.flights}
    return (source.flight_id in flights and target.flight_id in flights
        and source.flight_id != target.flight_id
        and flights[source.flight_id].window_end < flights[target.flight_id].window_end
        and source.start < target.end and target.start < source.end)


def task_handoff_possible(scenario, source, target):
    if target.timing_mode != 'phase' or not target.flight_id or source.id == target.id or source.role_id == target.role_id:
        return False
    shared = set(task_flight_ids(source)) & set(task_flight_ids(target))
    if shared and (source.exclusive_same_flight or target.exclusive_same_flight):
        return False
    if source.role_id == '大件行李拦截':
        rule = work_pair_rule(scenario,source,target,'after-baggage')
        if not rule or rule['mode'] != 'sequence' or not source.start < source.end < target.end:
            return False
        # A rotation may have fragments due to fixed/coverage changes; release
        # only after the final fragment of that actual rotation position.
        rotation = next((r for r in scenario.rotations if r.task_id == source.id),None)
        return bool(rotation and not any(r.group_id == rotation.group_id and r.rotation_index == rotation.rotation_index and r.segment_end > source.end for r in scenario.rotations))
    rule = work_pair_rule(scenario,source,target,'later-flight')
    return bool(rule and rule['mode'] == 'sequence' and _later_flight_target(scenario,source,target))


def task_handoff_options(scenario):
    if scenario.flow_preferences is not None:
        return []
    if not (scenario.work_rules or {}).get('pairs'):
        return []
    flights = {f.id:f.flight_no for f in scenario.flights}
    return [dict(key=task_handoff_key(a,b,scenario.service_date),sourceTaskId=a.id,targetTaskId=b.id,
        sourceLabel=(f'大件第{baggage_position(scenario,a)}轮（{format_minutes(a.start)}—{format_minutes(a.end)}）结束' if a.role_id == '大件行李拦截' else f'{flights.get(a.flight_id,"")} {a.role_id}完成'),
        targetLabel=f'{flights.get(b.flight_id,"")} {b.role_id}',position=baggage_position(scenario,a))
        for a in scenario.tasks for b in scenario.tasks if task_handoff_possible(scenario,a,b)]


def confirmed_task_handoff(scenario, source, target):
    if not scenario.task_handoffs:
        return False
    if task_handoff_possible(scenario,source,target) and task_handoff_key(source,target,scenario.service_date) in scenario.task_handoffs:
        return True
    if source.role_id != '大件行李拦截':
        return False
    rotation = next((r for r in scenario.rotations if r.task_id == source.id),None)
    if rotation is None:
        return False
    # Coverage/fixed changes may split a single rotation. Its final-segment
    # confirmation releases every earlier fragment of that same rotation only.
    final_ids = {r.task_id for r in scenario.rotations if r.group_id == rotation.group_id
        and r.rotation_index == rotation.rotation_index and r.segment_end > source.end}
    return any(t.id in final_ids and task_handoff_possible(scenario,t,target)
        and task_handoff_key(t,target,scenario.service_date) in scenario.task_handoffs for t in scenario.tasks)


def work_rule_issues(scenario):
    try:
        if scenario.flow_preferences is not None:
            validate_handoff_preferences(list(scenario.flow_preferences))
        if scenario.work_rules is not None:
            validate_work_rules(scenario.work_rules)
        if len(scenario.task_handoffs) != len(set(scenario.task_handoffs)) or any(not isinstance(k,str) for k in scenario.task_handoffs):
            raise ValueError('当天衔接确认重复或无效')
        if scenario.task_handoffs:
            allowed = {o['key'] for o in task_handoff_options(scenario)}
            if set(scenario.task_handoffs) - allowed:
                raise ValueError('航班、岗位或大件轮次已改变，原接岗确认失效，请在规则页清除失效确认并重新确认')
    except (ValueError,TypeError,KeyError):
        return [ValidationIssue('invalid_work_rules','岗位搭配或当天接岗确认失效，请在规则页核对并清除失效确认')]
    return []


def task_pair_conflict(scenario: Scenario, first: Task, second: Task) -> tuple[str, str] | None:
    """One owner for time/phase/concurrency facts; callers never infer minutes.

    A phase interval is an envelope, not a fabricated actual working interval.
    Same-flight concurrent duties do not occupy an additional ordinary position.
    Pairwise checking keeps that permission from spreading to two ordinary duties.
    """
    shared_flights = set(task_flight_ids(first)).intersection(task_flight_ids(second))
    rotation_conflict=baggage_rotation_conflict(scenario,first,second)
    if rotation_conflict:
        return rotation_conflict
    forbidden = forbidden_handoff_conflict(scenario,first,second)
    if forbidden:
        return forbidden
    group_conflict = separation_group_conflict(scenario, first, second)
    if group_conflict:
        return group_conflict
    if shared_flights and first.role_id != second.role_id and (first.exclusive_same_flight or second.exclusive_same_flight):
        dedicated = first if first.exclusive_same_flight else second
        numbers = "、".join(f.flight_no for f in scenario.flights if f.id in shared_flights)
        return "exclusive_same_flight", f"{numbers} 的 {dedicated.role_id} 已开启同航班专岗专人，不能由同一人兼做该航班其他岗位"
    if scenario.dedicated_flight_protection and (first.exclusive_same_flight or second.exclusive_same_flight):
        flights = {f.id: f for f in scenario.flights}
        for dedicated, other in ((first, second), (second, first)):
            if not dedicated.exclusive_same_flight:
                continue
            for flight_id in task_flight_ids(dedicated):
                protected_flight = flights.get(flight_id)
                if (protected_flight and set(task_flight_ids(other)) - set(task_flight_ids(dedicated))
                        and other.start < protected_flight.window_end and protected_flight.window_start < other.end):
                    return 'dedicated_flight_protection', f'{protected_flight.flight_no}仍在保障期间，专岗人员不能支援重叠航班'
    rule = work_pair_rule(scenario,first,second,'same-flight') if shared_flights else None
    if rule and rule['mode'] == 'separate':
        return 'role_separation', f'{first.role_id} 与 {second.role_id} 已设置必须分人，不能安排同一人员'
    if scenario.flow_preferences is not None:
        return _flow_task_pair_conflict(scenario, first, second, shared_flights, rule)
    return _legacy_task_pair_conflict(scenario, first, second, shared_flights, rule)


def _ordinary_rule_pair_permitted(first, second, rule):
    if rule and not ({first.task_kind,second.task_kind} & {'rotation','support'}):
        if rule['mode'] == 'concurrent':
            return True
        if first.timing_mode == second.timing_mode == 'phase':
            return True
    return False


def _ordinary_phase_pair_permitted(scenario, first, second, same_flight):
    if not (scenario.work_rules or {}).get('pairs') and same_flight and (first.concurrent or second.concurrent) and not ({first.task_kind, second.task_kind} & {"rotation", "support"}):
        return True
    arrival, boarding = (first, second) if first.service_stage == "arrival" and second.service_stage == "boarding" else (second, first) if second.service_stage == "arrival" and first.service_stage == "boarding" else (None, None)
    return first.timing_mode == second.timing_mode == 'phase' and arrival is not None and same_flight


def _flow_task_pair_conflict(scenario, first, second, shared_flights, rule):
    flights = {f.id: f for f in scenario.flights}
    for source, target in ((first, second), (second, first)):
        later_rule = work_pair_rule(scenario, source, target, 'later-flight') if not shared_flights else None
        if later_rule and later_rule['mode'] == 'separate' and _later_flight_target(scenario, source, target):
            return 'role_separation', '与其他航班后续岗位已设置必须分人'
        if source.role_id == '大件行李拦截':
            baggage_rule = work_pair_rule(scenario, source, target, 'after-baggage')
            if baggage_rule and baggage_rule['mode'] == 'separate':
                return 'role_separation', '大件轮换与后续岗位已设置必须分人'
    if not shared_flights and first.flight_id and second.flight_id:
        if flow_later_overlap_permitted(scenario,first,second) or flow_later_overlap_permitted(scenario,second,first):
            if scenario.minimum_transition_minutes:
                return 'phase_transition_unknown', '已允许支援晚结束航班，但没有实际交接时刻可核对走动预留，请现场核对'
            return None
        for a in task_flight_ids(first):
            for b in task_flight_ids(second):
                if a in flights and b in flights:
                    left, right = flights[a], flights[b]
                    if left.window_start < right.window_end and right.window_start < left.window_end:
                        return 'overlap', '两航班整体保障窗口重叠冲突，不能自动跨班顺延；请核对现场安排后手动微调'
                    gap = max(left.window_start, right.window_start) - min(left.window_end, right.window_end)
                    if gap < scenario.minimum_transition_minutes:
                        return 'insufficient_transition', '两航班整体保障窗口之间的走动预留不足'
    # Only ordinary process tasks may use an envelope as a relationship.
    # Two actual rotation/fixed/support blocks still collide by minutes.
    if (shared_flights and first.role_id != second.role_id
            and 'phase' in (first.timing_mode, second.timing_mode)
            and first.task_kind in {'mobile', 'rotation'} and second.task_kind in {'mobile', 'rotation'}
            and (flow_roles_connected(scenario, first, second) or flow_roles_connected(scenario, second, first))):
        return None
    same_flight = first.flight_id is not None and first.flight_id == second.flight_id
    if (_ordinary_rule_pair_permitted(first, second, rule)
            or _ordinary_phase_pair_permitted(scenario, first, second, same_flight)):
        return None
    return _task_interval_conflict(scenario, first, second)


def _legacy_task_pair_conflict(scenario, first, second, shared_flights, rule):
    overlaps = first.start < second.end and second.start < first.end
    same_flight = first.flight_id is not None and first.flight_id == second.flight_id
    if _ordinary_rule_pair_permitted(first, second, rule):
        return None
    for source,target in ((first,second),(second,first)):
        later = work_pair_rule(scenario,source,target,'later-flight') if not shared_flights else None
        if later and later['mode'] == 'separate' and _later_flight_target(scenario,source,target):
            return 'role_separation',f'{source.role_id} 与其他航班的 {target.role_id} 已设置必须分人'
        baggage = work_pair_rule(scenario,source,target,'after-baggage') if source.role_id == '大件行李拦截' else None
        if baggage and baggage['mode'] == 'separate' and baggage_position(scenario,source) is not None and source.end < target.end:
            return 'role_separation',f'大件轮换与后续的 {target.role_id} 已设置必须分人'
        if confirmed_task_handoff(scenario,source,target):
            if scenario.minimum_transition_minutes and source.flight_id != target.flight_id:
                return 'phase_transition_unknown','接岗已确认，但尚无实际交接时刻核对走动分钟，请填写特殊实际时段'
            return None
    if _ordinary_phase_pair_permitted(scenario, first, second, same_flight):
        return None
    arrival, boarding = (first, second) if first.service_stage == "arrival" and second.service_stage == "boarding" else (second, first) if second.service_stage == "arrival" and first.service_stage == "boarding" else (None, None)
    if first.timing_mode == second.timing_mode == "phase" and arrival is not None:
        if overlaps and (arrival.flight_id, boarding.flight_id) in scenario.phase_handoffs:
            if scenario.minimum_transition_minutes:
                return "phase_transition_unknown", "跨航班衔接已确认，但尚无实际交接时刻来核对走动分钟；请为这两个岗位填写特殊实际时段"
            return None
        if overlaps:
            return "phase_handoff_unconfirmed", "重叠航班的接机后登机衔接尚未确认；请在规则中确认人员确实能接上，或填写特殊实际时段"
    return _task_interval_conflict(scenario, first, second)


def _task_interval_conflict(scenario, first, second):
    overlaps = first.start < second.end and second.start < first.end
    same_flight = first.flight_id is not None and first.flight_id == second.flight_id
    arrival, boarding = (first, second) if first.service_stage == "arrival" and second.service_stage == "boarding" else (second, first) if second.service_stage == "arrival" and first.service_stage == "boarding" else (None, None)
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


def preferred_mobile_handoffs(scenario):
    """Ordered, legal task continuations eligible for the general flow preference."""
    result = []
    for source in scenario.tasks:
        if source.required_count < 1:
            continue
        for target in scenario.tasks:
            if source.id == target.id or source.role_id == target.role_id:
                continue
            if not source.flight_id or not target.flight_id or source.task_kind not in {'mobile', 'rotation'} or target.task_kind not in {'mobile', 'rotation'}:
                continue
            if scenario.flow_preferences is not None:
                if not flow_continuation_direction(scenario, source, target):
                    continue
                if flow_target_rank(scenario, source, target) is not None and task_pair_conflict(scenario, source, target) is None:
                    result.append((source, target))
                continue
            if source.timing_mode == target.timing_mode == 'exact':
                if source.end > target.start:
                    continue
            elif not (source.service_stage == 'arrival' and target.service_stage == 'boarding'
                      or confirmed_task_handoff(scenario, source, target)):
                continue
            if task_pair_conflict(scenario, source, target) is None:
                result.append((source, target))
    return tuple(result)


def flow_continuation_direction(scenario, source, target):
    """Task direction only; permission and hard conflicts are checked separately."""
    shared = set(task_flight_ids(source)) & set(task_flight_ids(target))
    flights = {f.id: f for f in scenario.flights}
    if not shared and any(flights[a].window_end > flights[b].window_start
            for a in task_flight_ids(source) for b in task_flight_ids(target)
            if a in flights and b in flights):
        return flow_later_overlap_permitted(scenario,source,target)
    return not (source.timing_mode == target.timing_mode == 'exact' and source.end > target.start)


def task_candidate_relationship(scenario, existing, target):
    """Explain a legal candidate using the same facts as flow matching.

    This describes a relationship, never a free time interval. The caller
    still runs the independent full schedule validation for the actual slot.
    """
    allowed = [task for task in existing if task_pair_conflict(scenario, task, target) is None]
    if scenario.flow_preferences is not None:
        if any(task.id != target.id and task.role_id != target.role_id
                and task.task_kind in {'mobile', 'rotation'} and target.task_kind in {'mobile', 'rotation'}
                and flow_continuation_direction(scenario, task, target)
                and flow_roles_connected(scenario, task, target) for task in allowed):
            return 'continuation'
    else:
        if any(confirmed_task_handoff(scenario, task, target) for task in allowed):
            return 'continuation'
        if target.service_stage == 'boarding' and any(
                task.service_stage == 'arrival' and (
                    task.flight_id is not None and task.flight_id == target.flight_id
                    or task.end <= target.start
                    or task.timing_mode == target.timing_mode == 'phase'
                    and task.start < target.end and target.start < task.end
                    and (task.flight_id, target.flight_id) in scenario.phase_handoffs)
                for task in allowed):
            return 'continuation'
    if any(target.flight_id is not None and task.flight_id == target.flight_id
            and task.start < target.end and target.start < task.end
            and (task.concurrent or target.concurrent) for task in allowed):
        return 'concurrent'
    return 'available'


def validate_imported_day(day: ImportedDay) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    all_internal_names = {name for item in day.cells for name in item.internal_names}
    known_names = set(imported_member_names(day))
    if type(day.transition_minutes) is not int or day.transition_minutes < 0 or day.transition_minutes > 180:
        issues.append(ValidationIssue("invalid_transition_minutes", "跨航班走动预留必须在 0 到 180 分钟之间"))
    if type(day.remarks_reviewed) is not bool:
        issues.append(ValidationIssue("invalid_remarks_reviewed", "支援备注核对状态无效"))
    if len(day.role_names) != len(set(day.role_names)):
        issues.append(ValidationIssue("duplicate_imported_role", "日表岗位名称重复"))
    flights_by_id = {flight.id: flight for flight in day.flights}
    seen_cells: set[tuple[str, str]] = set()
    for flight in day.flights:
        try:
            start = parse_minutes(flight.window_start)
            end = parse_minutes(flight.window_end)
            for base in (flight.arrival_time, flight.departure_time):
                if base is not None:
                    parse_minutes(base)
        except (ValueError, TypeError):
            issues.append(ValidationIssue("invalid_flight_window", f"航班 {flight.flight_no} 的起止时间无效", flight.id))
            continue
        if end <= start:
            issues.append(ValidationIssue("invalid_flight_window", f"航班 {flight.flight_no} 的结束时间必须晚于开始时间", flight.id))
    for cell in day.cells:
        flight = flights_by_id.get(cell.flight_id)
        if flight is None or flight.flight_no != cell.flight_no:
            issues.append(ValidationIssue("unknown_cell_flight", f"{cell.role_name} / {cell.flight_no} 引用了不存在的航班", cell.id))
        if cell.role_name not in day.role_names:
            issues.append(ValidationIssue("unknown_cell_role", f"日表单元格引用了不存在的岗位：{cell.role_name}", cell.id))
        cell_key = (cell.flight_id, cell.role_name)
        if cell_key in seen_cells:
            issues.append(ValidationIssue("duplicate_imported_cell", f"{cell.role_name} / {cell.flight_no} 重复", cell.id))
        seen_cells.add(cell_key)
        if cell.required_count_override is not None and (type(cell.required_count_override) is not int or cell.required_count_override < 0):
            issues.append(ValidationIssue("invalid_count_override", f"{cell.role_name} / {cell.flight_no} 的例外人数必须是 0 或正整数", cell.id))
        if cell.work_mode_override is not None and cell.work_mode_override not in WORK_MODES:
            issues.append(ValidationIssue("invalid_work_mode", f"{cell.role_name} 工作方式例外无效", cell.id))
        if day.flow_preferences is not None or imported_work_mode(day, cell) != "timed":
            pass
        elif (cell.work_start is None) != (cell.work_end is None):
            issues.append(ValidationIssue("incomplete_work_time", f"{cell.role_name} / {cell.flight_no} 的开始和结束时间需要一起填写", cell.id))
        elif cell.work_start is not None and cell.work_end is not None:
            try:
                start_time = parse_minutes(cell.work_start)
                end_time = parse_minutes(cell.work_end)
            except (TypeError, ValueError):
                issues.append(ValidationIssue("invalid_work_time", f"{cell.role_name} / {cell.flight_no} 的岗位时间格式无效", cell.id))
            else:
                if end_time <= start_time:
                    issues.append(ValidationIssue("work_time_order", f"{cell.role_name} / {cell.flight_no} 的结束时间必须晚于开始时间", cell.id))
        if cell.semantics == "ambiguous":
            issues.append(ValidationIssue("ambiguous_slash", f"{cell.role_name} / {cell.flight_no} 的斜杠语义待确认", cell.id))
        if cell.semantics in {"simultaneous", "rotation"} and len(cell.internal_names) < 2:
            issues.append(ValidationIssue("insufficient_people", f"{cell.role_name} / {cell.flight_no} 需要至少两名内部人员", cell.id))
        if cell.semantics == "external" and not cell.external_labels:
            issues.append(ValidationIssue("external_missing", f"{cell.role_name} / {cell.flight_no} 缺少外援标签", cell.id))
        if len(cell.internal_names) != len(set(cell.internal_names)):
            issues.append(ValidationIssue("duplicate_imported_person", f"{cell.role_name} / {cell.flight_no} 有重复人员", cell.id))
        if len(cell.fixed_member_names) != len(set(cell.fixed_member_names)):
            issues.append(ValidationIssue("duplicate_fixed_member", f"{cell.role_name} / {cell.flight_no} 的固定人员重复", cell.id))
        if cell.fixed_member_names and imported_required_count(day, cell) == 0:
            issues.append(ValidationIssue("fixed_member_on_disabled_role", f"{cell.role_name} / {cell.flight_no} 已设为不需要人员，不能再固定人员", cell.id))
        for name in cell.fixed_member_names:
            if name not in all_internal_names and name not in day.roster:
                issues.append(ValidationIssue("unknown_fixed_member", f"固定人员不在日表名单中：{name}", cell.id))
            elif name not in cell.internal_names and name not in day.roster:
                issues.append(ValidationIssue("fixed_member_not_in_cell", f"{cell.role_name} / {cell.flight_no} 的固定人员必须来自本单元格原名单", cell.id))
    for flight in day.flights:
        for role in day.role_names:
            if (flight.id, role) not in seen_cells:
                issues.append(ValidationIssue("missing_imported_cell", f"{role} / {flight.flight_no} 缺少岗位格，请补齐需求资料"))
    seen_roles: set[str] = set()
    for config in day.role_configs:
        if config.role_name not in day.role_names:
            issues.append(ValidationIssue("unknown_role_config", f"岗位配置中的岗位不存在：{config.role_name}"))
        if config.role_name in seen_roles:
            issues.append(ValidationIssue("duplicate_role_config", f"岗位配置重复：{config.role_name}"))
        if config.required_count is not None and (type(config.required_count) is not int or config.required_count < 0):
            issues.append(ValidationIssue("invalid_required_count", f"岗位 {config.role_name} 的同时需要人数必须是 0 或正整数"))
        seen_roles.add(config.role_name)
    seen_occupancies: set[str] = set()
    for occupancy in day.extra_occupancies:
        if not occupancy.id or occupancy.id in seen_occupancies:
            issues.append(ValidationIssue("duplicate_occupancy", "额外占用编号为空或重复"))
        if not occupancy.label.strip():
            issues.append(ValidationIssue("empty_occupancy_label", "额外占用需要填写事项"))
        if not occupancy.member_names or len(occupancy.member_names) != len(set(occupancy.member_names)):
            issues.append(ValidationIssue("invalid_occupancy_members", "额外占用需要选择不重复的人员"))
        for name in occupancy.member_names:
            if name not in known_names:
                issues.append(ValidationIssue("unknown_occupancy_member", f"额外占用中的人员不在日表名单中：{name}"))
        try:
            start = parse_minutes(occupancy.start)
            end = parse_minutes(occupancy.end)
        except (TypeError, ValueError):
            issues.append(ValidationIssue("invalid_occupancy_time", f"额外占用 {occupancy.label} 的开始或结束时间无效"))
        else:
            if end <= start:
                issues.append(ValidationIssue("occupancy_time_order", f"额外占用 {occupancy.label} 的结束时间必须晚于开始时间"))
        seen_occupancies.add(occupancy.id)
    seen_members: set[str] = set()
    for config in day.member_configs:
        if config.member_name not in known_names:
            issues.append(ValidationIssue("unknown_member_config", f"人员配置中的人员不在日表名单中：{config.member_name}"))
        if config.member_name in seen_members:
            issues.append(ValidationIssue("duplicate_member_config", f"人员配置重复：{config.member_name}"))
        if len(config.eligible_role_names) != len(set(config.eligible_role_names)):
            issues.append(ValidationIssue("duplicate_member_role", f"人员 {config.member_name} 的可做岗位重复"))
        for role_name in config.eligible_role_names:
            if role_name not in day.role_names:
                issues.append(ValidationIssue("unknown_member_role", f"人员 {config.member_name} 的可做岗位不存在：{role_name}"))
        seen_members.add(config.member_name)
    resolve_attendance = compile_attendance_bounds(day)
    seen_attendance: set[str] = set()
    for entry in day.member_availability:
        if entry.member_name not in known_names:
            issues.append(ValidationIssue("unknown_attendance_member", f"出勤人员不在日表名单中：{entry.member_name}"))
        if entry.member_name in seen_attendance:
            issues.append(ValidationIssue("duplicate_attendance_member", f"出勤人员重复：{entry.member_name}"))
        if type(entry.participating) is not bool:
            issues.append(ValidationIssue("invalid_participation", f"人员 {entry.member_name} 的参加排班选项无效"))
        _, issue = resolve_attendance(entry)
        if issue:
            issues.append(issue)
        seen_attendance.add(entry.member_name)
    return issues
