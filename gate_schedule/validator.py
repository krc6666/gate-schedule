from __future__ import annotations

from collections import defaultdict

from .domain import Assignment, Scenario, ValidationIssue, has_role_qualification, task_pair_conflict, member_flight_participation, task_flight_ids, flight_balance_members
from .domain import MEMBER_STATUSES, member_available, HALF_REST_MODES, half_rest_period_violation


def validate_scenario(scenario: Scenario) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if type(scenario.balance_exempt_half_rest) is not bool or any(m.half_rest not in HALF_REST_MODES for m in scenario.members):
        issues.append(ValidationIssue("invalid_half_rest", "半休模式或航班数豁免设置无效"))
    if any(type(v) is not bool for v in (scenario.team_leader_last, scenario.balance_exempt_team_leaders)) or any(type(m.team_leader) is not bool for m in scenario.members):
        issues.append(ValidationIssue("invalid_team_leader", "分队长标记与规则开关必须是布尔值"))
    member_ids = [member.id for member in scenario.members]
    if any(m.status not in MEMBER_STATUSES for m in scenario.members):
        issues.append(ValidationIssue("invalid_personnel_status", "人员状态无效"))
    task_ids = [task.id for task in scenario.tasks]
    flight_ids = [flight.id for flight in scenario.flights]
    if len(member_ids) != len(set(member_ids)):
        issues.append(ValidationIssue("duplicate_member", "人员编号重复"))
    if len(task_ids) != len(set(task_ids)):
        issues.append(ValidationIssue("duplicate_task", "任务编号重复"))
    if len(flight_ids) != len(set(flight_ids)):
        issues.append(ValidationIssue("duplicate_flight", "航班编号重复"))
    if type(scenario.minimum_transition_minutes) is not int or scenario.minimum_transition_minutes < 0:
        issues.append(ValidationIssue("invalid_transition_minutes", "跨航班走动预留必须是非负整数分钟"))
    if scenario.flight_count_gap is not None and (type(scenario.flight_count_gap) is not int or scenario.flight_count_gap < 0):
        issues.append(ValidationIssue("invalid_flight_count_gap", "当天航班数最大差距必须为空或非负整数"))
    priority_roles = scenario.priority_role_ids
    if (not isinstance(priority_roles, (tuple, list))
            or any(not isinstance(role, str) or not role or role != role.strip() for role in priority_roles)
            or len(priority_roles) != len(set(priority_roles))):
        issues.append(ValidationIssue("invalid_priority_roles", "重点岗位必须为不重复的有效岗位名称"))
    known_members = set(member_ids)
    known_flights = set(flight_ids)
    if scenario.release_policy not in ("flight", "task"):
        issues.append(ValidationIssue("invalid_release_policy", "支援释放规则无效"))
    seen_handoffs = set()
    for pair in scenario.phase_handoffs:
        if len(pair) != 2 or any(not isinstance(p, str) or p not in known_flights for p in pair) or pair[0] == pair[1] or pair in seen_handoffs:
            issues.append(ValidationIssue("invalid_phase_handoff", "航班衔接引用未知航班、自身或重复关系"))
        else:
            seen_handoffs.add(pair)
    flights = {flight.id: flight for flight in scenario.flights}
    known_tasks = set(task_ids)
    rotations_by_task: dict[str, list] = defaultdict(list)
    for rotation in scenario.rotations:
        rotations_by_task[rotation.task_id].append(rotation)
    for task in scenario.tasks:
        if task.timing_mode not in ("exact", "phase") or type(task.concurrent) is not bool or type(task.exclusive_same_flight) is not bool or (task.timing_mode == "phase" and (task.flight_id is None or scenario.release_policy != "task")):
            issues.append(ValidationIssue("invalid_task_mode", f"任务 {task.id} 的环节或兼任性质无效", task.id))
        envelope = flights.get(task.flight_id)
        if task.timing_mode == "phase" and envelope and (task.start, task.end) != (envelope.window_start, envelope.window_end):
            issues.append(ValidationIssue("invalid_phase_envelope", f"任务 {task.id} 的环节范围必须保留完整航班窗口，不能伪造实际起止", task.id))
        if task.end <= task.start:
            issues.append(ValidationIssue("invalid_time", f"任务 {task.id} 的结束时间必须晚于开始时间", task.id))
        if task.required_count < 1:
            issues.append(ValidationIssue("invalid_required_count", f"任务 {task.id} 的内部人数必须至少为 1", task.id))
        if task.service_stage is not None and task.service_stage not in ("arrival", "boarding"):
            issues.append(ValidationIssue("invalid_task_stage", f"任务 {task.id} 的工作类型无效", task.id))
        if task.service_stage is not None and task.flight_id is None:
            issues.append(ValidationIssue("task_stage_requires_flight", f"任务 {task.id} 标记了接机或登机，但没有关联航班", task.id))
        if task.flight_id is not None and task.flight_id not in known_flights:
            issues.append(ValidationIssue("unknown_flight", f"任务 {task.id} 引用了未知航班 {task.flight_id}", task.id))
        if any(identity not in known_flights for identity in task.covered_flight_ids) or len(task.covered_flight_ids) != len(set(task.covered_flight_ids)):
            issues.append(ValidationIssue("invalid_covered_flights", f"任务 {task.id} 的共享覆盖航班无效", task.id))
        for member_id in task.locked_member_ids:
            if member_id not in known_members:
                issues.append(ValidationIssue("unknown_locked_member", f"任务 {task.id} 锁定了未知人员 {member_id}", task.id, member_id))
        if len(task.locked_member_ids) > task.required_count:
            issues.append(ValidationIssue("too_many_locks", f"任务 {task.id} 的锁定人数超过内部需求", task.id))
        rotation_count = len(rotations_by_task.get(task.id, ()))
        if task.task_kind == "rotation" and rotation_count != 1:
            issues.append(ValidationIssue("rotation_definition", f"轮换任务 {task.id} 必须有且只有一个明确轮换定义", task.id))
        if task.task_kind != "rotation" and rotation_count:
            issues.append(ValidationIssue("unexpected_rotation", f"非轮换任务 {task.id} 不应附带轮换定义", task.id))
    for rotation in scenario.rotations:
        task = next((item for item in scenario.tasks if item.id == rotation.task_id), None)
        if task is None or task.task_kind != "rotation":
            issues.append(ValidationIssue("invalid_rotation_task", f"轮换引用了非轮换任务 {rotation.task_id}", rotation.task_id))
            continue
        if (rotation.segment_start, rotation.segment_end, rotation.required_count) != (task.start, task.end, task.required_count):
            issues.append(ValidationIssue("rotation_mismatch", f"轮换 {rotation.task_id} 与任务时段或人数不一致", rotation.task_id))
        for member_id in rotation.candidate_member_ids:
            if member_id not in known_members:
                issues.append(ValidationIssue("unknown_rotation_member", f"轮换 {rotation.task_id} 引用了未知人员 {member_id}", rotation.task_id, member_id))
    for requirement in scenario.external_support_requirements:
        if requirement.task_id not in known_tasks:
            issues.append(ValidationIssue("unknown_external_task", f"外援缺口引用了未知任务 {requirement.task_id}", requirement.task_id))
        if requirement.count < 1 or not requirement.source_label:
            issues.append(ValidationIssue("invalid_external_requirement", f"任务 {requirement.task_id} 的外援缺口无效", requirement.task_id))
    return issues


def validate_schedule(scenario: Scenario, assignments: tuple[Assignment, ...]) -> list[ValidationIssue]:
    issues = validate_scenario(scenario)
    tasks = {task.id: task for task in scenario.tasks}
    members = {member.id: member for member in scenario.members}
    flights = {flight.id: flight for flight in scenario.flights}
    rotations_by_task = {rotation.task_id: rotation for rotation in scenario.rotations}
    assignments_by_task: dict[str, list[str]] = defaultdict(list)
    assignments_by_member: dict[str, list[str]] = defaultdict(list)

    for assignment in assignments:
        task = tasks.get(assignment.task_id)
        member = members.get(assignment.member_id)
        if task is None:
            issues.append(ValidationIssue("unknown_task", f"分配引用了未知任务 {assignment.task_id}", assignment.task_id, assignment.member_id))
            continue
        if member is None:
            issues.append(ValidationIssue("unknown_member", f"任务 {task.id} 分配给未知人员 {assignment.member_id}", task.id, assignment.member_id))
            continue
        assignments_by_task[task.id].append(member.id)
        if not member_available(member):
            issues.append(ValidationIssue("personnel_unavailable", f"人员 {member.id} 当前状态为{member.status}，不能安排任务", task.id, member.id))
        half_rest_issue = half_rest_period_violation(member, task)
        if half_rest_issue:
            issues.append(ValidationIssue("half_rest_period", half_rest_issue, task.id, member.id))
        assignments_by_member[member.id].append(task.id)
        if assignments_by_task[task.id].count(member.id) > 1:
            issues.append(ValidationIssue("duplicate_assignment", f"人员 {member.id} 被重复分配到任务 {task.id}", task.id, member.id))
        if not has_role_qualification(member, task.role_id):
            issues.append(ValidationIssue("ineligible", f"人员 {member.id} 不具备岗位 {task.role_id} 资格", task.id, member.id))
        rotation = rotations_by_task.get(task.id)
        if rotation is not None and member.id not in rotation.candidate_member_ids:
            issues.append(ValidationIssue("rotation_candidate", f"人员 {member.id} 不在轮换任务 {task.id} 的候选池内", task.id, member.id))
        if member.available_from > task.start or (member.available_until is not None and member.available_until < task.end):
            issues.append(ValidationIssue("member_unavailable", f"人员 {member.id} 不在任务 {task.id} 的可用时间内", task.id, member.id))

    for task in scenario.tasks:
        assigned = assignments_by_task[task.id]
        if len(assigned) < task.required_count:
            issues.append(ValidationIssue("coverage_shortfall", f"任务 {task.id} 缺少 {task.required_count - len(assigned)} 名内部人员", task.id))
        if len(assigned) > task.required_count:
            issues.append(ValidationIssue("coverage_exceeded", f"任务 {task.id} 超出内部人数需求", task.id))
        for member_id in task.locked_member_ids:
            if member_id not in assigned:
                issues.append(ValidationIssue("lock_missing", f"任务 {task.id} 未保持锁定人员 {member_id}", task.id, member_id))

    for member_id, task_ids in assignments_by_member.items():
        for index, first_id in enumerate(task_ids):
            first = tasks[first_id]
            for second_id in task_ids[index + 1 :]:
                second = tasks[second_id]
                conflict = task_pair_conflict(scenario, first, second)
                if conflict:
                    code, reason = conflict
                    issues.append(ValidationIssue(code, f"人员 {member_id}：{reason}", first_id, member_id, (first_id, second_id)))

    rotation_groups: dict[str, list] = defaultdict(list)
    for rotation in scenario.rotations:
        rotation_groups[rotation.group_id].append(rotation)
    for group_id, segments in rotation_groups.items():
        ordered = sorted(segments, key=lambda item: item.segment_start)
        for first, second in zip(ordered, ordered[1:]):
            if first.segment_end != second.segment_start:
                issues.append(ValidationIssue("rotation_gap", f"轮换组 {group_id} 的 {first.task_id} 与 {second.task_id} 未连续覆盖", first.task_id))
    if type(scenario.flight_count_gap) is int and scenario.flight_count_gap >= 0:
        participation = member_flight_participation(scenario, assignments)
        counts = [len(participation[m.id]) for m in flight_balance_members(scenario)]
        maximum, minimum = (max(counts), min(counts)) if counts else (0, 0)
        if maximum - minimum > scenario.flight_count_gap:
            related = tuple(dict.fromkeys(a.task_id for a in assignments
                                          if a.task_id in tasks and task_flight_ids(tasks[a.task_id])))
            issues.append(ValidationIssue("flight_count_spread",
                f"当天参与航班最多{maximum}班、最少{minimum}班，差{maximum - minimum}，超过上限{scenario.flight_count_gap}；请整体调整人员安排",
                related_task_ids=related))
    return issues


