from __future__ import annotations

from .domain import ExternalSupportRequirement, Flight, Member, Rotation, Scenario, Task, parse_minutes, validate_balance_settings, default_team_leader_settings, validate_team_leader_settings


from .domain import default_half_rest_settings, validate_half_rest_settings


def scenario_from_dict(data: dict) -> Scenario:
    flights = tuple(
        Flight(
            id=item["id"],
            flight_no=item["flightNo"],
            service_date=item.get("serviceDate", data["serviceDate"]),
            window_start=parse_minutes(item["windowStart"]),
            window_end=parse_minutes(item["windowEnd"]),
            location_id=item.get("locationId"),
            category=item.get("category"),
        )
        for item in data["flights"]
    )
    tasks = tuple(
        Task(
            id=item["id"],
            flight_id=item.get("flightId"),
            role_id=item["roleId"],
            start=parse_minutes(item["start"]),
            end=parse_minutes(item["end"]),
            required_count=item["requiredCount"],
            task_kind=item["taskKind"],
            location_id=item.get("locationId"),
            locked_member_ids=tuple(item.get("lockedMemberIds", ())),
            service_stage=item.get("serviceStage"),
            covered_flight_ids=tuple(item.get("coveredFlightIds", ())),
            timing_mode=item.get("timingMode", "exact"), concurrent=item.get("concurrent", False), exclusive_same_flight=item.get("exclusiveSameFlight", False),
        )
        for item in data["tasks"]
    )
    policy = data.get("releasePolicy", "flight")
    tasks, task_ids_by_original = _split_boarding_tasks_at_arrival_flight_ends(tasks, flights) if policy == "flight" else (tasks, {t.id: (t.id,) for t in tasks})
    tasks_by_id = {task.id: task for task in tasks}
    priority_roles = data.get("priorityRoleIds", [])
    balance = validate_balance_settings({
        "flightCountGap": data.get("flightCountGap"),
        "priorityEnabled": bool(data.get("priorityRoleIds", [])),
        "priorityRoleNames": priority_roles,
        "priorityAdjustments": [],
    }, [role for role in priority_roles if isinstance(role, str)] if isinstance(priority_roles, list) else [])
    leaders = validate_team_leader_settings(data.get("teamLeaderSettings", default_team_leader_settings()))
    return Scenario(
        service_date=data["serviceDate"],
        model_version=data.get("modelVersion", "phase-0"),
        minimum_transition_minutes=data.get("minimumTransitionMinutes", 0),
        release_policy=policy, phase_handoffs=tuple(tuple(pair) for pair in data.get("phaseHandoffs", ())),
        flight_count_gap=balance["flightCountGap"], priority_role_ids=tuple(balance["priorityRoleNames"]),
        team_leader_last=leaders["lastUsed"], balance_exempt_team_leaders=leaders["exemptFlightCount"],
        balance_exempt_half_rest=validate_half_rest_settings(data.get("halfRestSettings", default_half_rest_settings()))["exemptFlightCount"],
        flights=flights,
        members=tuple(
            Member(
                id=item["id"],
                name=item["name"],
                eligible_role_ids=frozenset(item["eligibleRoleIds"]),
                available_from=parse_minutes(item["availableFrom"]),
                available_until=parse_minutes(item["availableUntil"]) if item.get("availableUntil") is not None else None,
                team_leader=item.get("teamLeader", False),
                status=item.get("status", "正常"), half_rest=item.get("halfRest"),
            )
            for item in data["members"]
        ),
        tasks=tasks,
        rotations=tuple(
            Rotation(
                task_id=task_id,
                group_id=item["groupId"],
                segment_start=tasks_by_id[task_id].start if task_id != item["taskId"] else parse_minutes(item["segmentStart"]),
                segment_end=tasks_by_id[task_id].end if task_id != item["taskId"] else parse_minutes(item["segmentEnd"]),
                required_count=item["requiredCount"],
                candidate_member_ids=tuple(item["candidateMemberIds"]),
                rotation_index=item.get("rotationIndex"),
            )
            for item in data.get("rotations", ())
            for task_id in task_ids_by_original.get(item["taskId"], (item["taskId"],))
        ),
        external_support_requirements=tuple(
            ExternalSupportRequirement(
                task_id=task_id,
                count=item["count"],
                source_label=item["sourceLabel"],
                resolved_member_name=item.get("resolvedMemberName"),
            )
            for item in data.get("externalSupportRequirements", ())
            for task_id in task_ids_by_original.get(item["taskId"], (item["taskId"],))
        ),
    )


def scenario_to_dict(scenario: Scenario) -> dict:
    return {
        "serviceDate": scenario.service_date,
        "modelVersion": scenario.model_version,
        "minimumTransitionMinutes": scenario.minimum_transition_minutes,
        "releasePolicy": scenario.release_policy, "phaseHandoffs": [list(pair) for pair in scenario.phase_handoffs],
        "flightCountGap": scenario.flight_count_gap, "priorityRoleIds": list(scenario.priority_role_ids),
        "teamLeaderSettings": {"lastUsed": scenario.team_leader_last, "exemptFlightCount": scenario.balance_exempt_team_leaders},
        "halfRestSettings": {"exemptFlightCount": scenario.balance_exempt_half_rest},
        "flights": [
            {"id": item.id, "flightNo": item.flight_no, "serviceDate": item.service_date,
             "windowStart": item.window_start, "windowEnd": item.window_end,
             "locationId": item.location_id, "category": item.category}
            for item in scenario.flights
        ],
        "members": [
            {"id": item.id, "name": item.name, "eligibleRoleIds": sorted(item.eligible_role_ids),
             "availableFrom": item.available_from, "availableUntil": item.available_until, "teamLeader": item.team_leader, "status": item.status, "halfRest": item.half_rest}
            for item in scenario.members
        ],
        "tasks": [
            {"id": item.id, "flightId": item.flight_id, "roleId": item.role_id, "start": item.start, "end": item.end,
             "requiredCount": item.required_count, "taskKind": item.task_kind, "locationId": item.location_id,
             "lockedMemberIds": list(item.locked_member_ids), "serviceStage": item.service_stage,
             "coveredFlightIds": list(item.covered_flight_ids), "timingMode": item.timing_mode, "concurrent": item.concurrent, **({"exclusiveSameFlight": True} if item.exclusive_same_flight else {})}
            for item in scenario.tasks
        ],
        "rotations": [
            {"taskId": item.task_id, "groupId": item.group_id, "segmentStart": item.segment_start,
             "segmentEnd": item.segment_end, "requiredCount": item.required_count,
             "candidateMemberIds": list(item.candidate_member_ids), "rotationIndex": item.rotation_index} for item in scenario.rotations
        ],
        "externalSupportRequirements": [
            {"taskId": item.task_id, "count": item.count, "sourceLabel": item.source_label,
             "resolvedMemberName": item.resolved_member_name} for item in scenario.external_support_requirements
        ],
    }


def _split_boarding_tasks_at_arrival_flight_ends(
    tasks: tuple[Task, ...], flights: tuple[Flight, ...]
) -> tuple[tuple[Task, ...], dict[str, tuple[str, ...]]]:
    flights_by_id = {flight.id: flight for flight in flights}
    arrival_flight_ends = {
        task.flight_id: flights_by_id[task.flight_id].window_end
        for task in tasks
        if task.service_stage == "arrival" and task.flight_id in flights_by_id
    }
    expanded: list[Task] = []
    task_ids_by_original: dict[str, tuple[str, ...]] = {}
    for task in tasks:
        destination = flights_by_id.get(task.flight_id)
        cut_points = sorted({
            flight_end
            for source_flight_id, flight_end in arrival_flight_ends.items()
            if task.service_stage == "boarding"
            and task.flight_id is not None
            and source_flight_id != task.flight_id
            and task.start < flight_end < task.end
            and destination is not None
            and destination.window_end > flight_end
        })
        boundaries = [task.start, *cut_points, task.end]
        if not cut_points:
            expanded.append(task)
            task_ids_by_original[task.id] = (task.id,)
            continue
        segment_ids = []
        for index, (start, end) in enumerate(zip(boundaries, boundaries[1:]), start=1):
            task_id = f"{task.id}::segment-{index}"
            segment_ids.append(task_id)
            expanded.append(
                Task(
                    id=task_id,
                    flight_id=task.flight_id,
                    role_id=task.role_id,
                    start=start,
                    end=end,
                    required_count=task.required_count,
                    task_kind=task.task_kind,
                    location_id=task.location_id,
                    locked_member_ids=task.locked_member_ids,
                    service_stage=task.service_stage,
                    covered_flight_ids=task.covered_flight_ids,
                    timing_mode=task.timing_mode, concurrent=task.concurrent, exclusive_same_flight=task.exclusive_same_flight,
                )
            )
        task_ids_by_original[task.id] = tuple(segment_ids)
    return tuple(expanded), task_ids_by_original


def normalize_saved_scenario(raw: dict, assignments: list[dict]) -> tuple[dict, list[dict], dict]:
    """Migrate old assignments through the exact same normalization as new inputs."""
    normalized = scenario_from_dict(raw)
    original_tasks = tuple(Task(
        id=t["id"], flight_id=t.get("flightId"), role_id=t["roleId"],
        start=parse_minutes(t["start"]), end=parse_minutes(t["end"]),
        required_count=t["requiredCount"], task_kind=t["taskKind"],
        location_id=t.get("locationId"), locked_member_ids=tuple(t.get("lockedMemberIds", ())), service_stage=t.get("serviceStage"),
        covered_flight_ids=tuple(t.get("coveredFlightIds", ())), timing_mode=t.get("timingMode", "exact"), concurrent=t.get("concurrent", False), exclusive_same_flight=t.get("exclusiveSameFlight", False),
    ) for t in raw["tasks"])
    _, mapping = _split_boarding_tasks_at_arrival_flight_ends(original_tasks, normalized.flights) if normalized.release_policy == "flight" else (original_tasks, {t.id: (t.id,) for t in original_tasks})
    migrated = []
    for item in assignments:
        if item["taskId"] not in mapping:
            raise ValueError(f"旧分配引用未知任务：{item['taskId']}")
        migrated.extend({"taskId": task_id, "memberId": item["memberId"]} for task_id in mapping[item["taskId"]])
    return scenario_to_dict(normalized), migrated, {key: list(value) for key, value in mapping.items()}
