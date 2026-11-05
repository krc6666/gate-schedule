from __future__ import annotations

from time import monotonic

from .cp_backend import cp_model

from .domain import Assignment, Scenario, SolveResult, has_role_qualification, task_pair_conflict, validate_handoff_preferences, preferred_handoff, handoff_rank, task_flight_ids, flight_balance_members
from .validator import validate_scenario
from .domain import member_available, scarce_role_groups, half_rest_period_violation


def _eligible(scenario: Scenario, member, task) -> bool:
    rotation = next((item for item in scenario.rotations if item.task_id == task.id), None)
    return (
        member_available(member) and has_role_qualification(member, task.role_id)
        and half_rest_period_violation(member, task) is None
        and member.available_from <= task.start
        and (member.available_until is None or member.available_until >= task.end)
        and (rotation is None or member.id in rotation.candidate_member_ids)
    )


def _infeasibility_diagnostics(scenario: Scenario) -> tuple[str, ...]:
    messages = []
    for task in scenario.tasks:
        candidates = [member for member in scenario.members if _eligible(scenario, member, task)]
        if len(candidates) < task.required_count:
            messages.append(f"任务 {task.id} 可用的合格内部人员不足：需要 {task.required_count}，仅有 {len(candidates)}")
    if scenario.flight_count_gap is not None:
        exemption = "，分队长不参与比较" if scenario.balance_exempt_team_leaders else ""
        messages.append(f"当天参与航班数最大差距上限为{scenario.flight_count_gap}，参加比较人员（含0班）均须满足{exemption}；请核对差距设置、人员资格和固定安排")
    return tuple(messages) or ("任务的时间、资格、锁定和人数约束组合后无可行解",)


def _flight_participation_variables(model, scenario, variables, *, role_id=None):
    """OR assignment variables into the domain's member/flight participation facts."""
    result = {}
    for member in scenario.members:
        for flight in scenario.flights:
            relevant = [variables[member.id, task.id] for task in scenario.tasks
                        if (member.id, task.id) in variables and flight.id in task_flight_ids(task)
                        and (role_id is None or task.role_id == role_id)]
            if not relevant:
                continue
            if len(relevant) == 1:
                result[member.id, flight.id] = relevant[0]
            else:
                present = model.new_bool_var(f"participates_{role_id}_{member.id}_{flight.id}")
                model.add_max_equality(present, relevant)
                result[member.id, flight.id] = present
    return result


def _priority_costs(model, scenario, variables, previous_roles, priority_counts, *, role_ids=None):
    repeats, monthly, previous = [], [], []
    repeat_bound = monthly_bound = previous_bound = 0
    groups = [(None, role) for role in (scenario.priority_role_ids if role_ids is None else role_ids)]
    if role_ids is None:
        groups.extend(scenario.priority_flight_roles)
    for flight_no, role in groups:
        participation = _flight_participation_variables(model, scenario, variables, role_id=role)
        if flight_no is not None:
            flight_ids = {f.id for f in scenario.flights if f.flight_no == flight_no}
            participation = {key: v for key, v in participation.items() if key[1] in flight_ids}
        def count_key(member):
            return (member, flight_no, role) if flight_no is not None else (member, role)
        eligible_members = {member_id for member_id, _ in participation}
        # Only ordering and ties matter: arbitrary manual corrections must not
        # turn large real counts into overflowing CP-SAT objective coefficients.
        levels = sorted({priority_counts.get(count_key(member), 0) for member in eligible_members})
        ranks = {count: rank for rank, count in enumerate(levels)}
        for member in scenario.members:
            entries = [variable for (member_id, _), variable in participation.items() if member_id == member.id]
            if not entries:
                continue
            if len(entries) > 1:
                count = model.new_int_var(0, len(entries), f"priority_count_{member.id}_{role}")
                model.add(count == sum(entries))
                bound = len(entries) * (len(entries) - 1) // 2
                repeated = model.new_int_var(0, bound, f"priority_repeat_{member.id}_{role}")
                model.add_element(count, [n * (n - 1) // 2 for n in range(len(entries) + 1)], repeated)
                repeats.append(repeated)
                repeat_bound += bound
            rank = ranks[priority_counts.get(count_key(member.id), 0)]
            if rank:
                monthly.extend(rank * variable for variable in entries)
                monthly_bound += rank * len(entries)
            if count_key(member.id) in previous_roles:
                previous.extend(entries)
                previous_bound += len(entries)
    return (sum(repeats), repeat_bound), (sum(monthly), monthly_bound), (sum(previous), previous_bound)


def solve(scenario: Scenario, time_limit_seconds: float = 5, *, allow_partial=False, previous_roles=frozenset(), handoff_preferences=(), priority_counts=None, control=None) -> SolveResult:
    validate_handoff_preferences(list(handoff_preferences))
    priority_counts = {} if priority_counts is None else priority_counts
    if not isinstance(priority_counts, dict) or any(
            not isinstance(key, tuple) or len(key) not in (2, 3)
            or any(not isinstance(part, str) or not part for part in key)
            or type(count) is not int or count < 0
            for key, count in priority_counts.items()):
        raise ValueError("重点岗位历史次数必须为人员与岗位对应的非负整数")
    if control:
        control.stage("检查输入与资格")
    model_issues = validate_scenario(scenario)
    if model_issues:
        return SolveResult("MODEL_INVALID", diagnostics=tuple(issue.message for issue in model_issues))

    model = cp_model.CpModel()
    if control:
        control.stage("建立岗位与人员候选")
    variables: dict[tuple[str, str], cp_model.IntVar] = {}
    for task in scenario.tasks:
        for member in scenario.members:
            if _eligible(scenario, member, task):
                variables[member.id, task.id] = model.new_bool_var(f"assign_{member.id}_{task.id}")

    vacancies = []
    for task in scenario.tasks:
        candidates = [variable for (member_id, task_id), variable in variables.items() if task_id == task.id]
        if allow_partial:
            missing = model.new_int_var(0, task.required_count, f"missing_{task.id}")
            vacancies.append(missing)
            model.add(sum(candidates) + missing == task.required_count)
        else:
            model.add(sum(candidates) == task.required_count)
        for member_id in task.locked_member_ids:
            variable = variables.get((member_id, task.id))
            if variable is None:
                return SolveResult("INFEASIBLE", diagnostics=(f"任务 {task.id} 的锁定人员 {member_id} 不满足资格或到岗时间",))
            model.add(variable == 1)

    if control:
        control.stage("检查冲突、固定与轮换")
    for member in scenario.members:
        member_tasks = [task for task in scenario.tasks if (member.id, task.id) in variables]
        for index, first in enumerate(member_tasks):
            for second in member_tasks[index + 1 :]:
                if task_pair_conflict(scenario, first, second):
                    model.add(variables[member.id, first.id] + variables[member.id, second.id] <= 1)

    balance_members = flight_balance_members(scenario)
    if scenario.flight_count_gap is not None and balance_members:
        participation = _flight_participation_variables(model, scenario, variables)
        counts = []
        for member in balance_members:
            count = model.new_int_var(0, len(scenario.flights), f"flight_count_{member.id}")
            model.add(count == sum(variable for (identity, _), variable in participation.items() if identity == member.id))
            counts.append(count)
        maximum = model.new_int_var(0, len(scenario.flights), "maximum_flight_count")
        minimum = model.new_int_var(0, len(scenario.flights), "minimum_flight_count")
        model.add_max_equality(maximum, counts)
        model.add_min_equality(minimum, counts)
        model.add(maximum - minimum <= scenario.flight_count_gap)

    flights = {flight.id: flight for flight in scenario.flights}
    same_flight_handoffs = []
    preferred_handoffs = []
    ranked_handoffs = []
    cross_flight_handoffs = []
    rotation_repeats = []
    rotation_changes = []
    pair_index = 0
    for member in scenario.members:
        arrival_tasks = [task for task in scenario.tasks if task.service_stage == "arrival"]
        boarding_tasks = [task for task in scenario.tasks if task.service_stage == "boarding"]
        for arrival in arrival_tasks:
            arrival_assignment = variables.get((member.id, arrival.id))
            source_flight = flights.get(arrival.flight_id)
            if arrival_assignment is None or source_flight is None:
                continue
            for boarding in boarding_tasks:
                boarding_assignment = variables.get((member.id, boarding.id))
                if boarding_assignment is None:
                    continue
                if task_pair_conflict(scenario, arrival, boarding):
                    continue
                if arrival.flight_id == boarding.flight_id:
                    if arrival.timing_mode == boarding.timing_mode == "exact" and arrival.end > boarding.start:
                        continue
                    pair = model.new_bool_var(f"same_flight_handoff_{pair_index}")
                    same_flight_handoffs.append(pair)
                    if preferred_handoff(handoff_preferences, source_flight.flight_no, arrival.role_id, boarding.role_id):
                        preferred_handoffs.append(pair)
                        rank = handoff_rank(handoff_preferences, source_flight.flight_no, arrival.role_id, boarding.role_id)
                        if rank:
                            ranked_handoffs.append((rank, pair))
                else:
                    pair = model.new_bool_var(f"cross_flight_handoff_{pair_index}")
                    cross_flight_handoffs.append(pair)
                pair_index += 1
                model.add(pair <= arrival_assignment)
                model.add(pair <= boarding_assignment)
                model.add(pair >= arrival_assignment + boarding_assignment - 1)

    rotations_by_group: dict[str, list] = {}
    for rotation in scenario.rotations:
        rotations_by_group.setdefault(rotation.group_id, []).append(rotation)
    for group_rotations in rotations_by_group.values():
        ordered = sorted(group_rotations, key=lambda item: item.segment_start)
        for first, second in zip(ordered, ordered[1:]):
            if first.segment_end != second.segment_start:
                continue
            for member in scenario.members:
                first_assignment = variables.get((member.id, first.task_id))
                second_assignment = variables.get((member.id, second.task_id))
                if first_assignment is None or second_assignment is None:
                    continue
                together = model.new_bool_var(f"rotation_pair_{member.id}_{first.task_id}_{second.task_id}")
                model.add(together <= first_assignment)
                model.add(together <= second_assignment)
                model.add(together >= first_assignment + second_assignment - 1)
                if first.rotation_index is not None and first.rotation_index == second.rotation_index:
                    rotation_changes.append(1 - together)
                else:
                    rotation_repeats.append(together)

    stability_weight = 1
    rotation_weight = len(rotation_changes) * stability_weight + 1
    cross_flight_weight = len(rotation_repeats) * rotation_weight + len(rotation_changes) * stability_weight + 1
    rank_weight = len(cross_flight_handoffs) * cross_flight_weight + len(rotation_repeats) * rotation_weight + len(rotation_changes) * stability_weight + 1
    rank_bound = sum(rank for rank, _ in ranked_handoffs)
    path_weight = (rank_bound + 1) * rank_weight
    same_flight_weight = len(preferred_handoffs) * path_weight + path_weight
    roles_by_task = {task.id: task.role_id for task in scenario.tasks}
    history_repeats = [variable for (member_id, task_id), variable in variables.items()
                       if roles_by_task[task_id] not in scenario.priority_role_ids
                       and (member_id, roles_by_task[task_id]) in previous_roles]
    history_weight = 1
    preference_scale = len(history_repeats) + 1
    preference_bound = (len(cross_flight_handoffs) * cross_flight_weight + len(rotation_repeats) * rotation_weight
                        + len(rotation_changes) + len(same_flight_handoffs) * same_flight_weight + len(preferred_handoffs) * path_weight + rank_bound * rank_weight) * preference_scale + len(history_repeats)
    base_objective = (
        history_weight * sum(history_repeats)
        + preference_scale * (
        cross_flight_weight * sum(cross_flight_handoffs)
        + rotation_weight * sum(rotation_repeats)
        + stability_weight * sum(rotation_changes)
        + rank_weight * sum(rank * pair for rank, pair in ranked_handoffs)
        - same_flight_weight * sum(same_flight_handoffs) - path_weight * sum(preferred_handoffs))
    )
    objective = base_objective
    # Coverage, leader use, automatic scarcity tiers, selected priority roles,
    # then existing handoff/rotation goals. Equal scarcity shares one cost tier.
    priority_costs = _priority_costs(model, scenario, variables, previous_roles, priority_counts)
    scarce_costs = tuple(cost for roles in scarce_role_groups(scenario)
                         for cost in _priority_costs(model, scenario, variables, previous_roles,
                                                     priority_counts, role_ids=roles))
    half_rest_costs = []
    participation, early_end, late_end, late_count = [], [], [], []
    end_bound = max((t.end for t in scenario.tasks), default=0)
    for member in scenario.members:
        if not member.half_rest or not member_available(member):
            continue
        choices = [(t, variables[member.id, t.id]) for t in scenario.tasks if (member.id, t.id) in variables]
        if not choices:
            continue
        worked = model.new_bool_var(f'half_rest_worked_{member.id}')
        model.add_max_equality(worked, [v for _, v in choices])
        participation.append(1 - worked)
        latest = model.new_int_var(0, end_bound, f'half_rest_end_{member.id}')
        model.add_max_equality(latest, [t.end * v for t, v in choices])
        if member.half_rest == 'early-finish':
            early_end.append(latest)
        else:
            late_end.append(end_bound - latest)
            late_count.extend(v for _, v in choices)
    for terms, bound in ((participation, len(participation)), (early_end, len(early_end)*end_bound),
                         (late_end, len(late_end)*end_bound), (late_count, len(late_count))):
        if terms:
            half_rest_costs.append((sum(terms), bound))
    priority_costs = (*half_rest_costs, *scarce_costs, *priority_costs)
    if scenario.team_leader_last:
        leader_ids = {m.id for m in scenario.members if m.team_leader}
        leader_choices = [v for (member, _), v in variables.items() if member in leader_ids]
        priority_costs = ((sum(leader_choices), len(leader_choices)), *priority_costs)
    for cost, bound in reversed(priority_costs):
        if bound:
            weight = preference_bound + 1
            objective += weight * cost
            preference_bound += weight * bound
    weighted_bound = (preference_bound + 1) * sum(task.required_count for task in scenario.tasks) + preference_bound
    if weighted_bound > 2 ** 60:
        # Large real models cannot encode every priority in one 64-bit weight.
        # Optimize the same tiers in order, fixing each proven optimum, within
        # the original total time budget; a timeout retains the last safe result.
        objectives = ([sum(vacancies)] if vacancies else []) + [cost for cost, bound in priority_costs if bound] + [base_objective]
    else:
        objectives = [(preference_bound + 1) * sum(vacancies) + objective]
    solver = cp_model.CpSolver()
    if control:
        control.stage("计算人员安排")
        control.attach_solver(solver)
    def assignments_from(reader):
        return tuple(Assignment(task.id, member.id) for task in scenario.tasks for member in scenario.members
                     if (member.id, task.id) in variables and reader.value(variables[member.id, task.id]))
    class Solutions(cp_model.CpSolverSolutionCallback):
        def on_solution_callback(self):
            if control:
                control.solution(scenario, assignments_from(self))
    assignments = None
    status = cp_model.UNKNOWN
    completed = True
    deadline = monotonic() + time_limit_seconds
    try:
        for objective in objectives:
            remaining = deadline - monotonic()
            if remaining <= 0 or control and control.stopped.is_set():
                completed = False
                break
            solver.parameters.max_time_in_seconds = remaining
            model.minimize(objective)
            status = solver.solve(model, Solutions() if control else None)
            if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                completed = False
                break
            assignments = assignments_from(solver)
            if status != cp_model.OPTIMAL:
                completed = False
                break
            model.add(objective == solver.value(objective))
    finally:
        if control:
            control.attach_solver(None)
    if assignments is not None:
        result_status = "OPTIMAL" if completed else "FEASIBLE"
    elif status == cp_model.INFEASIBLE:
        return SolveResult("INFEASIBLE", diagnostics=_infeasibility_diagnostics(scenario))
    elif status == cp_model.MODEL_INVALID:
        return SolveResult("MODEL_INVALID", diagnostics=("OR-Tools 拒绝了模型",))
    else:
        return SolveResult("TIMED_OUT", diagnostics=("求解在时限内未找到可行解",))

    return SolveResult(
        result_status,
        assignments=assignments,
        external_support_by_task={item.task_id: item.count for item in scenario.external_support_requirements},
    )
