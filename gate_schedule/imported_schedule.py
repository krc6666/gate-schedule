from __future__ import annotations

from .domain import Flight, ImportedDay, Member, Rotation, Scenario, Task, ValidationIssue, imported_member_names, imported_required_count, imported_work_mode, parse_minutes, attendance_bounds
from .io import scenario_from_dict, scenario_to_dict
from .validator import validate_scenario


def _minutes(value: str) -> int:
    return parse_minutes(value)


def imported_day_to_scenario(day: ImportedDay, baggage_interval_minutes: int = 60, baggage_rotation_enabled: bool = True) -> tuple[Scenario, list[ValidationIssue]]:
    issues: list[ValidationIssue] = []
    if any(remark.kind == "support" for remark in day.remarks) and not day.remarks_reviewed:
        issues.append(ValidationIssue("support_remark_unreviewed", "请先核对 0910 支援备注，并把需要占用的时间填入额外占用"))
    flights = tuple(Flight(flight.id, flight.flight_no, day.service_date, _minutes(flight.window_start), _minutes(flight.window_end)) for flight in day.flights)
    flights_by_id = {flight.id: flight for flight in day.flights}
    flights_by_no = {flight.flight_no: flight for flight in flights}
    config_by_member = {item.member_name: item for item in day.member_configs}
    availability_by_member = {item.member_name: item for item in day.member_availability}
    required_member_names = {
        name for cell in day.cells
        if imported_required_count(day, cell) not in (None, 0)
        for name in cell.internal_names
    }
    required_member_names.update(name for occupancy in day.extra_occupancies for name in occupancy.member_names)
    member_names = imported_member_names(day)
    member_names = tuple(dict.fromkeys((*member_names, *(name for occupancy in day.extra_occupancies for name in occupancy.member_names))))
    members: list[Member] = []
    for name in member_names:
        config = config_by_member.get(name)
        availability = availability_by_member.get(name)
        if name not in required_member_names and (availability is None or not availability.participating):
            continue
        if config is None:
            issues.append(ValidationIssue("missing_member_config", f"人员 {name} 尚未设置可做岗位"))
            continue
        if availability is None:
            issues.append(ValidationIssue("missing_member_availability", f"人员 {name} 尚未填写当天出勤"))
            continue
        if not availability.participating:
            continue
        bounds, issue = attendance_bounds(day, availability)
        if issue:
            issues.append(issue)
            continue
        extra_roles = {f"额外占用：{item.label}" for item in day.extra_occupancies if name in item.member_names}
        members.append(Member(name, name, frozenset((*config.eligible_role_names, *extra_roles)), *bounds, half_rest=availability.half_rest))
    tasks: list[Task] = []
    rotations: list[Rotation] = []
    baggage_cells = []
    role_configs = {r.role_name: r for r in day.role_configs}
    for cell in day.cells:
        count = imported_required_count(day, cell)
        if count == 0:
            continue
        if count is None:
            issues.append(ValidationIssue("missing_required_count", f"{cell.role_name} / {cell.flight_no} 尚未设置同时需要人数", cell.id))
            if cell.role_name != "大件行李拦截" and imported_work_mode(day, cell) == "timed" and (not cell.work_start or not cell.work_end):
                issues.append(ValidationIssue("missing_work_time", f"{cell.role_name} / {cell.flight_no} 尚未填写岗位起止时间", cell.id))
            continue
        if cell.role_name == "大件行李拦截":
            flight = flights_by_id.get(cell.flight_id)
            if flight is None:
                issues.append(ValidationIssue("unknown_flight", f"大件行李拦截引用了未知航班：{cell.flight_no}", cell.id))
                continue
            baggage_cells.append((cell, count, _minutes(flight.window_start), _minutes(flight.window_end)))
            continue
        config = role_configs.get(cell.role_name)
        mode = imported_work_mode(day, cell)
        if mode == "timed" and (not cell.work_start or not cell.work_end):
            issues.append(ValidationIssue("missing_work_time", f"{cell.role_name} / {cell.flight_no} 尚未填写岗位起止时间", cell.id))
            continue
        if cell.flight_no not in flights_by_no:
            issues.append(ValidationIssue("unknown_flight", f"岗位单元格引用了未知航班：{cell.flight_no}", cell.id))
            continue
        stage = ("arrival" if cell.role_name.startswith("接机") else "boarding" if cell.role_name.startswith("登机") else None) if mode == "timed" else mode if mode in {"arrival", "boarding"} else None
        flight = flights_by_no[cell.flight_no]
        tasks.append(Task(
            id=f"imported-{cell.id}",
            flight_id=flights_by_no[cell.flight_no].id,
            role_id=cell.role_name,
            start=_minutes(cell.work_start) if mode == "timed" else flight.window_start,
            end=_minutes(cell.work_end) if mode == "timed" else flight.window_end,
            required_count=count,
            task_kind="mobile",
            locked_member_ids=cell.fixed_member_names,
            service_stage=stage,
            timing_mode="exact" if mode == "timed" else "phase",
            concurrent=config.concurrent if config else False,
            exclusive_same_flight=config.exclusive_same_flight if config else False,
        ))
    if baggage_cells:
        merged: list[list[int]] = []
        for _, _, start, end in sorted(baggage_cells, key=lambda item: item[2]):
            if end <= start:
                issues.append(ValidationIssue("invalid_work_time", "大件行李拦截的结束时间必须晚于开始时间"))
                continue
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        eligible_baggage = tuple(member.id for member in members if "大件行李拦截" in member.eligible_role_ids)
        for group_index, (window_start, window_end) in enumerate(merged, start=1):
            group_cells = [item for item in baggage_cells if item[2] < window_end and window_start < item[3]]
            boundaries = sorted({value for item in group_cells for value in (item[2], item[3]) if window_start < value < window_end})
            atomic_profiles = []
            for start, end in zip([window_start, *boundaries], [*boundaries, window_end]):
                active = [item for item in group_cells if item[2] <= start and item[3] >= end]
                if not active:
                    issues.append(ValidationIssue("baggage_coverage_gap", "大件行李拦截的合并时段出现空档"))
                    continue
                required_count = max(item[1] for item in active)
                if required_count != 1:
                    issues.append(ValidationIssue("invalid_baggage_count", "大件行李拦截当前确认每段只需 1 人"))
                    continue
                fixed_names = tuple(dict.fromkeys(name for item in active for name in item[0].fixed_member_names))
                if len(fixed_names) > required_count:
                    issues.append(ValidationIssue("too_many_fixed_members", "大件行李拦截某一轮换时段的固定人数超过同时需求"))
                profile = (start, end, required_count, fixed_names, active[0][0])
                if atomic_profiles and atomic_profiles[-1][2:4] == profile[2:4]:
                    previous = atomic_profiles[-1]
                    atomic_profiles[-1] = (previous[0], end, *previous[2:])
                else:
                    atomic_profiles.append(profile)

            segment_index = 0
            for profile_start, profile_end, required_count, fixed_names, source_cell in atomic_profiles:
                segment_start = profile_start
                while segment_start < profile_end:
                    if baggage_rotation_enabled:
                        rotation_index = (segment_start - window_start) // baggage_interval_minutes
                        rotation_end = window_start + (rotation_index + 1) * baggage_interval_minutes
                    else:
                        rotation_index = 0
                        rotation_end = profile_end
                    segment_end = min(rotation_end, profile_end)
                    task_id = f"imported-baggage-{group_index}-{segment_index + 1}"
                    tasks.append(Task(
                        id=task_id,
                        flight_id=flights_by_no[source_cell.flight_no].id,
                        role_id="大件行李拦截",
                        start=segment_start,
                        end=segment_end,
                        required_count=required_count,
                        task_kind="rotation" if baggage_rotation_enabled else "mobile",
                        locked_member_ids=fixed_names,
                        exclusive_same_flight=(role_configs["大件行李拦截"].exclusive_same_flight if "大件行李拦截" in role_configs else False),
                        covered_flight_ids=tuple(dict.fromkeys(item[0].flight_id for item in group_cells if item[2] < segment_end and segment_start < item[3])),
                    ))
                    if baggage_rotation_enabled:
                        rotations.append(Rotation(task_id, f"baggage-{group_index}", segment_start, segment_end, required_count, eligible_baggage, rotation_index))
                    segment_start = segment_end
                    segment_index += 1
    for occupancy in day.extra_occupancies:
        try:
            start, end = _minutes(occupancy.start), _minutes(occupancy.end)
        except ValueError:
            continue
        for name in occupancy.member_names:
            tasks.append(Task(
                id=f"occupancy-{occupancy.id}-{name}",
                flight_id=None,
                role_id=f"额外占用：{occupancy.label}",
                start=start,
                end=end,
                required_count=1,
                task_kind="fixed",
                locked_member_ids=(name,),
            ))
    scenario = Scenario(
        day.service_date, flights, tuple(members), tuple(tasks), rotations=tuple(rotations), model_version="imported-day-v1",
        minimum_transition_minutes=day.transition_minutes,
        release_policy=day.release_policy, phase_handoffs=day.phase_handoffs,
    )
    # 与 JSON 读取和恢复共用衔接拆段，确保求解和保存使用相同任务身份。
    scenario = scenario_from_dict(scenario_to_dict(scenario))
    issues.extend(validate_scenario(scenario))
    return scenario, issues
