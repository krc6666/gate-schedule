from __future__ import annotations

from collections import defaultdict

from .domain import (
    Assignment,
    BaggageRotationSegment,
    BaggageRotationSettings,
    FlightHandoffSuggestion,
    ImportedDay,
    ImportedFlight,
    Scenario,
    format_minutes,
    task_timing_label,
)
from .io import parse_minutes


def flights_by_start(flights):
    """Stable display order using explicit service-day offsets; never mutate input."""
    return sorted(flights, key=lambda flight: parse_minutes(flight.window_start))


def baggage_rotation_segments(
    flights: tuple[ImportedFlight, ...], settings: BaggageRotationSettings
) -> tuple[BaggageRotationSegment, ...]:
    if not settings.enabled or not flights:
        return ()

    windows = sorted((parse_minutes(flight.window_start), parse_minutes(flight.window_end)) for flight in flights)
    merged: list[list[int]] = []
    for start, end in windows:
        if end <= start:
            raise ValueError("航班结束时间必须晚于开始时间")
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    segments: list[BaggageRotationSegment] = []
    for start, end in merged:
        while start < end:
            segment_end = min(start + settings.interval_minutes, end)
            segments.append(BaggageRotationSegment(_format_clock(start), _format_clock(segment_end)))
            start = segment_end
    return tuple(segments)


def _format_clock(value: int) -> str:
    return format_minutes(value)


def flight_handoff_suggestions(day: ImportedDay) -> tuple[FlightHandoffSuggestion, ...]:
    """List possible post-arrival support windows from the imported flight and role names."""
    if day.release_policy == "task":
        # The phase workflow consumes domain.phase_handoff_options and explicit
        # confirmations. It must never invent a minute-based release suggestion.
        return ()
    arrivals_by_flight: dict[str, list[str]] = defaultdict(list)
    boarding_names_by_flight: dict[str, set[str]] = defaultdict(set)
    for cell in day.cells:
        if cell.role_name in {"接机1", "接机2"}:
            for name in cell.internal_names:
                if name not in arrivals_by_flight[cell.flight_id]:
                    arrivals_by_flight[cell.flight_id].append(name)
        elif cell.role_name.startswith("登机"):
            boarding_names_by_flight[cell.flight_id].update(cell.internal_names)

    boarding_roles = tuple(role for role in day.role_names if role.startswith("登机"))
    if not boarding_roles:
        return ()

    windows = {
        flight.id: (parse_minutes(flight.window_start), parse_minutes(flight.window_end))
        for flight in day.flights
    }
    suggestions: list[FlightHandoffSuggestion] = []
    for source in day.flights:
        source_start, source_end = windows[source.id]
        for target in day.flights:
            if source.id == target.id:
                continue
            target_start, target_end = windows[target.id]
            overlaps = source_start < target_end and target_start < source_end
            if not overlaps or source_end >= target_end:
                continue

            candidates = tuple(
                name for name in arrivals_by_flight[source.id]
                if name not in boarding_names_by_flight[target.id]
            )
            if not candidates:
                continue
            suggestions.append(
                FlightHandoffSuggestion(
                    source_flight_no=source.flight_no,
                    target_flight_no=target.flight_no,
                    suggested_from=_format_clock(source_end),
                    suggested_until=_format_clock(target_end),
                    candidate_names=candidates,
                    boarding_roles=boarding_roles,
                )
            )
    return tuple(suggestions)


def task_matrix(scenario: Scenario, assignments: tuple[Assignment, ...]) -> dict[str, dict[str, list[dict[str, int | str | list[str]]]]]:
    """Return every task segment for a role and flight without losing rotations."""
    members = {member.id: member.name for member in scenario.members}
    names_by_task: dict[str, list[str]] = defaultdict(list)
    for assignment in assignments:
        if assignment.member_id in members:
            names_by_task[assignment.task_id].append(members[assignment.member_id])
    flights = {flight.id: flight.flight_no for flight in scenario.flights}
    matrix: dict[str, dict[str, list[dict[str, int | str | list[str]]]]] = defaultdict(lambda: defaultdict(list))
    for task in scenario.tasks:
        columns = [flights.get(task.flight_id, "支援") if task.flight_id else "支援"]
        if task.covered_flight_ids:
            columns = [flights[identity] for identity in task.covered_flight_ids]
        for column in columns:
            matrix[task.role_id][column].append(
                {"task_id": task.id, "start": task.start, "end": task.end, "timing_mode": task.timing_mode, "timing_label": task_timing_label(task), "members": names_by_task[task.id]}
            )
    return {role_id: dict(columns) for role_id, columns in matrix.items()}


def member_paths(scenario: Scenario, assignments: tuple[Assignment, ...]) -> dict[str, list[dict[str, int | str]]]:
    members = {member.id: member.name for member in scenario.members}
    tasks = {task.id: task for task in scenario.tasks}
    paths: dict[str, list[dict[str, int | str]]] = defaultdict(list)
    for assignment in assignments:
        task = tasks.get(assignment.task_id)
        name = members.get(assignment.member_id)
        if task is not None and name is not None:
            paths[assignment.member_id].append(
                {"member_name": name, "task_id": task.id, "role_id": task.role_id, "start": task.start, "end": task.end, "timing_mode": task.timing_mode, "timing_label": task_timing_label(task)}
            )
    for path in paths.values():
        path.sort(key=lambda item: (item["start"], item["end"]))
    return dict(paths)
