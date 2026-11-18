from __future__ import annotations

from time import monotonic

from .cp_backend import cp_model

from .domain import Assignment, Scenario, SolveResult, has_role_qualification, task_pair_conflict, validate_handoff_preferences, preferred_handoff, handoff_rank, task_flight_ids, flight_balance_members
from .validator import validate_scenario
from .domain import member_available, scarce_role_groups, half_rest_period_violation, coverage_task_groups, coverage_order_weights


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


def solve(scenario: Scenario, time_limit_seconds: float = 5, *, allow_partial=False, previous_roles=frozenset(), handoff_preferences=(), priority_counts=None, control=None, relaxed_settings=None, relaxed_counts=None, duty_member_id=None) -> SolveResult:
    started = monotonic()
    # A new plan carries its complete flow contract in the Scenario snapshot.
    # The separate argument remains an adapter for legacy snapshots only; it
    # must not be a second source of truth for the new contract.
    legacy_handoff_preferences = () if scenario.flow_preferences is not None else tuple(handoff_preferences)
    validate_handoff_preferences(list(legacy_handoff_preferences))
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

    baseline = None
    if relaxed_settings is not None:
        from .domain import validate_relaxed_shift_settings
        relaxed_settings = validate_relaxed_shift_settings(relaxed_settings)
        relaxed_counts = {} if relaxed_counts is None else relaxed_counts
        if not isinstance(relaxed_counts, dict) or any(type(v) is not int or not 0 <= v <= 31 for v in relaxed_counts.values()):
            raise ValueError('月度轻松班次历史次数须为0至31的整数')
        people = [m for m in scenario.members if member_available(m) and not m.team_leader and m.status == '正常']
        needs_balance = any(relaxed_settings[k + 'BalanceEnabled'] and len({relaxed_counts.get((m.id, k), 0)
            for m in people if k != 'earlyDeparture' or m.id != duty_member_id}) > 1
            for k in ('earlyDeparture', 'afternoonRest'))
        if needs_balance:
            baseline = solve(scenario, min(1.5, time_limit_seconds / 2), allow_partial=allow_partial,
                previous_roles=previous_roles, handoff_preferences=legacy_handoff_preferences,
                priority_counts=priority_counts, control=control)
            if baseline.status not in ('OPTIMAL', 'FEASIBLE'):
                # No opportunity budget exists yet; give the original model
                # the remaining budget, without inventing empty-day rest.
                return solve(scenario, max(.01, time_limit_seconds - (monotonic() - started)),
                    allow_partial=allow_partial, previous_roles=previous_roles,
                    handoff_preferences=legacy_handoff_preferences, priority_counts=priority_counts, control=control)
            if control and control.stopped.is_set():
                return baseline

    model = cp_model.CpModel()
    if control:
        control.stage("建立岗位与人员候选")
    variables: dict[tuple[str, str], cp_model.IntVar] = {}
    for task in scenario.tasks:
        for member in scenario.members:
            if _eligible(scenario, member, task):
                variables[member.id, task.id] = model.new_bool_var(f"assign_{member.id}_{task.id}")

    missing_by_task = {}
    coverage_groups = coverage_task_groups(scenario) if allow_partial else ()
    for task in scenario.tasks:
        candidates = [variable for (member_id, task_id), variable in variables.items() if task_id == task.id]
        if allow_partial:
            missing = model.new_int_var(0, task.required_count, f"missing_{task.id}")
            missing_by_task[task.id] = missing
            model.add(sum(candidates) + missing == task.required_count)
        else:
            model.add(sum(candidates) == task.required_count)
        for member_id in task.locked_member_ids:
            variable = variables.get((member_id, task.id))
            if variable is None:
                return SolveResult("INFEASIBLE", diagnostics=(f"任务 {task.id} 的锁定人员 {member_id} 不满足资格或到岗时间",))
            model.add(variable == 1)

    if baseline is not None:
        assigned_counts = {}
        for assignment in baseline.assignments:
            assigned_counts[assignment.task_id] = assigned_counts.get(assignment.task_id, 0) + 1
        for group in coverage_groups:
            missing_before = sum(max(0, t.required_count - assigned_counts.get(t.id, 0)) for t in group)
            model.add(sum(missing_by_task[t.id] for t in group) <= missing_before)

    if control:
        control.stage("检查冲突、固定与轮换")
    pair_conflicts = {}
    for member in scenario.members:
        member_tasks = [task for task in scenario.tasks if (member.id, task.id) in variables]
        for index, first in enumerate(member_tasks):
            for second in member_tasks[index + 1 :]:
                key = (first.id, second.id)
                if key not in pair_conflicts:
                    pair_conflicts[key] = task_pair_conflict(scenario, first, second)
                if pair_conflicts[key]:
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
    mobile_handoffs = []
    preferred_handoffs = []
    ranked_handoffs = []
    rotation_repeats = []
    rotation_changes = []
    pair_index = 0
    outgoing, incoming = {}, {}
    flow_ranks, route_order = {}, {}
    from .domain import preferred_mobile_handoffs
    for source, target in preferred_mobile_handoffs(scenario):
        source_flight = flights[source.flight_id]
        for member in scenario.members:
            a = variables.get((member.id, source.id))
            b = variables.get((member.id, target.id))
            if a is None or b is None:
                continue
            pair = model.new_bool_var(f'mobile_handoff_{pair_index}')
            pair_index += 1
            mobile_handoffs.append(pair)
            if scenario.flow_preferences is not None:
                from .domain import flow_target_rank
                rank = flow_target_rank(scenario, source, target)
                if rank is not None:
                    flow_ranks.setdefault(rank, []).append(pair)
                for task in (source, target):
                    if (member.id, task.id) not in route_order:
                        route_order[member.id, task.id] = model.new_int_var(0, len(scenario.tasks), f'flow_order_{member.id}_{task.id}')
                # Linear implication also runs on the browser CP-SAT adapter.
                model.add(route_order[member.id, source.id] + 1 <= route_order[member.id, target.id]
                          + (len(scenario.tasks) + 1) * (1 - pair))
            if scenario.flow_preferences is None and source.flight_id == target.flight_id and preferred_handoff(legacy_handoff_preferences, source_flight.flight_no, source.role_id, target.role_id):
                preferred_handoffs.append(pair)
                rank = handoff_rank(legacy_handoff_preferences, source_flight.flight_no, source.role_id, target.role_id)
                if rank:
                    ranked_handoffs.append((rank, pair))
            model.add(pair <= a)
            model.add(pair <= b)
            outgoing.setdefault((member.id, source.id), []).append(pair)
            incoming.setdefault((member.id, target.id), []).append(pair)
    # A continuation is a route edge, not every pair in a person's itinerary.
    # At most one predecessor/successor prevents quadratic rewards for long days.
    for paths in (*outgoing.values(), *incoming.values()):
        model.add(sum(paths) <= 1)

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
    rank_weight = len(rotation_repeats) * rotation_weight + len(rotation_changes) * stability_weight + 1
    rank_bound = sum(rank for rank, _ in ranked_handoffs)
    path_weight = (rank_bound + 1) * rank_weight
    mobile_weight = len(preferred_handoffs) * path_weight + path_weight
    roles_by_task = {task.id: task.role_id for task in scenario.tasks}
    history_repeats = [variable for (member_id, task_id), variable in variables.items()
                       if roles_by_task[task_id] not in scenario.priority_role_ids
                       and (member_id, roles_by_task[task_id]) in previous_roles]
    history_weight = 1
    preference_scale = len(history_repeats) + 1
    preference_bound = (len(rotation_repeats) * rotation_weight
                        + len(rotation_changes) + len(mobile_handoffs) * mobile_weight + len(preferred_handoffs) * path_weight + rank_bound * rank_weight) * preference_scale + len(history_repeats)
    base_objective = (
        history_weight * sum(history_repeats)
        + preference_scale * (
        rotation_weight * sum(rotation_repeats)
        + stability_weight * sum(rotation_changes)
        + rank_weight * sum(rank * pair for rank, pair in ranked_handoffs)
        - mobile_weight * sum(mobile_handoffs) - path_weight * sum(preferred_handoffs))
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
    end_origin = min(0, min((t.end for t in scenario.tasks), default=0))
    end_bound = max((t.end - end_origin for t in scenario.tasks), default=0)
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
        model.add_max_equality(latest, [(t.end - end_origin) * v for t, v in choices])
        if member.half_rest == 'early-finish':
            early_end.append(latest)
        else:
            late_end.append(end_bound - latest)
            late_count.extend(v for _, v in choices)
    for terms, bound in ((participation, len(participation)), (early_end, len(early_end)*end_bound),
                         (late_end, len(late_end)*end_bound), (late_count, len(late_count))):
        if terms:
            half_rest_costs.append((sum(terms), bound))
    from .relaxed_shift_balance import relaxed_shift_costs
    rest_costs = relaxed_shift_costs(model, scenario, variables, relaxed_settings,
                                   relaxed_counts or {}, duty_member_id, baseline.assignments) if baseline else ()
    priority_costs = (*half_rest_costs, *rest_costs, *scarce_costs, *priority_costs)
    # The owner-configured first choice wins before the second, rather than
    # adding ranks and trading one first choice for several lower choices.
    if flow_ranks:
        priority_costs = (*((len(paths) - sum(paths), len(paths)) for _, paths in sorted(flow_ranks.items())), *priority_costs)
    from .domain import baggage_targets
    baggage_paths = []
    for source in scenario.tasks if scenario.flow_preferences is None else ():
        if source.role_id != '大件行李拦截':
            continue
        for target in scenario.tasks:
            if target.role_id not in baggage_targets(scenario,source,target) or task_pair_conflict(scenario,source,target):
                continue
            for member in scenario.members:
                a,b = variables.get((member.id,source.id)),variables.get((member.id,target.id))
                if a is None or b is None:
                    continue
                pair = model.new_bool_var(f'baggage_path_{source.id}_{target.id}_{member.id}')
                model.add(pair <= a)
                model.add(pair <= b)
                model.add(pair >= a + b - 1)
                baggage_paths.append(1-pair)
    if baggage_paths:
        priority_costs = (*priority_costs, (sum(baggage_paths),len(baggage_paths)))
    if scenario.team_leader_last:
        leader_ids = {m.id for m in scenario.members if m.team_leader}
        leader_choices = [v for (member, _), v in variables.items() if member in leader_ids]
        priority_costs = ((sum(leader_choices), len(leader_choices)), *priority_costs)
    # Domain owns dedicated/flight-end/role ordering, including shared tasks.
    coverage_costs = tuple((sum(missing_by_task[t.id] for t in group),
                            sum(t.required_count for t in group)) for group in coverage_groups)
    order_weights = coverage_order_weights(scenario)
    ordered_tasks = [(t, order_weights.get(t.role_id, 0)) for t in scenario.tasks]
    order_bound = sum(t.required_count * weight for t, weight in ordered_tasks)
    order_costs = ((sum(missing_by_task[t.id] * weight for t, weight in ordered_tasks), order_bound),) if allow_partial and order_bound else ()
    priority_costs = (*coverage_costs, *order_costs, *priority_costs)
    for cost, bound in reversed(priority_costs):
        if bound:
            weight = preference_bound + 1
            # Native OR-Tools mutates SumArray with +=. Keep the base objective
            # intact for the separate final stage when weights exceed int64.
            objective = objective + weight * cost
            preference_bound += weight * bound
    weighted_bound = preference_bound
    if weighted_bound > 2 ** 60:
        # Large real models cannot encode every priority in one 64-bit weight.
        # Pack consecutive tiers into safe integer objectives. Solving every
        # flight separately can exhaust the budget before later flights receive
        # staff, even when full coverage is feasible. The packed ordering is
        # exactly the same lexicographic policy, with fewer solver restarts.
        objectives = []
        packed, packed_bound = 0, 0
        for cost, bound in priority_costs:
            if not bound:
                continue
            next_bound = packed_bound * (bound + 1) + bound
            if packed_bound and next_bound > 2 ** 50:
                objectives.append(packed)
                packed, packed_bound = 0, 0
            packed = packed * (bound + 1) + cost
            packed_bound = packed_bound * (bound + 1) + bound
        if packed_bound:
            objectives.append(packed)
        objectives.append(base_objective)
    else:
        objectives = [objective]
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
    deadline = started + time_limit_seconds
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
        if baseline is not None:
            return SolveResult('FEASIBLE', assignments=baseline.assignments,
                               external_support_by_task=baseline.external_support_by_task)
        return SolveResult("TIMED_OUT", diagnostics=("求解在时限内未找到可行解",))

    return SolveResult(
        result_status,
        assignments=assignments,
        external_support_by_task={item.task_id: item.count for item in scenario.external_support_requirements},
    )
