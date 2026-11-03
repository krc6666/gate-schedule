"""Contextual manual edits and explanations; legality belongs to validator/domain."""
from difflib import get_close_matches

from .domain import Assignment, task_pair_conflict
from .validator import validate_schedule


def replace_slot(scenario, assignments, task_id, slot, member_id):
    task = next((t for t in scenario.tasks if t.id == task_id), None)
    if task is None:
        raise ValueError("岗位已改变，请重新选择")
    existing = [a for a in assignments if a.task_id == task_id]
    if type(slot) is not int or slot < 0 or slot >= max(task.required_count, len(existing)) or slot > len(existing):
        raise ValueError("人员位置已改变，请重新选择")
    if member_id and member_id not in {m.id for m in scenario.members}:
        raise ValueError("该人员未参加当天排班，请先确认当天人员")
    if member_id and any(a.member_id == member_id for i, a in enumerate(existing) if i != slot):
        raise ValueError("该人员已在本岗，不能重复选择")
    group = existing[:]
    if slot < len(group):
        group.pop(slot)
    if member_id:
        group.insert(slot, Assignment(task_id, member_id))
    return tuple(a for a in assignments if a.task_id != task_id) + tuple(group)


def issue_data(scenario, issues):
    tasks = {t.id: t for t in scenario.tasks} if scenario else {}
    flights = {f.id: f.flight_no for f in scenario.flights} if scenario else {}
    members = {m.id: m.name for m in scenario.members} if scenario else {}
    labels = {t.id: f"{flights.get(t.flight_id, '支援')} {t.role_id}" for t in tasks.values()}
    replacements = {**labels, **members}
    result = []
    for issue in dict.fromkeys(issues):
        related = tuple(dict.fromkeys((*(issue.related_task_ids or ()), *((issue.task_id,) if issue.task_id else ()))))
        # Whole-day balance belongs in the summary, never in every cell's warning.
        if issue.code == 'flight_count_spread':
            related = ()
        message = issue.message
        for key in sorted(replacements, key=len, reverse=True):
            message = message.replace(key, replacements[key])
        if len(related) > 1 and not all(labels.get(k, k) in message for k in related):
            message = ' / '.join(labels.get(k, k) for k in related) + '：' + message
        result.append({"code": issue.code, "message": message, "taskId": issue.task_id,
                       "memberId": issue.member_id, "relatedTaskIds": list(related)})
    return result


def slot_issues(scenario, assignments, task_id, slot, member_id):
    trial = replace_slot(scenario, assignments, task_id, slot, member_id)
    return trial, [i for i in validate_schedule(scenario, trial)
                   if i.code not in {'coverage_shortfall', 'flight_count_spread'} and (i.task_id == task_id or task_id in i.related_task_ids or i.member_id == member_id)]


def candidate_options(scenario, assignments, task_id, slot=0):
    """Trial every member through the same final checks used by a manual edit.

    Positive labels describe an allowed relationship to the target task, never
    an inferred idle interval. Existing slot/partner restrictions stay intact.
    """
    tasks = {task.id: task for task in scenario.tasks}
    target = tasks.get(task_id)
    if target is None:
        raise ValueError("岗位已改变，请重新选择")
    options = []
    for member in scenario.members:
        existing = [tasks[a.task_id] for a in assignments
                    if a.member_id == member.id and a.task_id in tasks]
        try:
            _, problems = slot_issues(scenario, assignments, task_id, slot, member.id)
            manual_allowed = True
            explained = issue_data(scenario, problems)
            reasons = [issue["message"] for issue in explained]
        except ValueError as error:
            manual_allowed = False
            reasons, explained = [str(error)], []
        if any(task.id == task_id for task in existing):
            status, label = "assigned", "已在本岗"
        elif reasons:
            status, label = "blocked", "不可安排"
        else:
            allowed = [task for task in existing
                       if task_pair_conflict(scenario, task, target) is None]
            if target.service_stage == "boarding" and any(
                    task.service_stage == "arrival" and (
                        task.flight_id is not None and task.flight_id == target.flight_id
                        or task.end <= target.start
                        or task.timing_mode == target.timing_mode == "phase"
                        and task.start < target.end and target.start < task.end
                        and (task.flight_id, target.flight_id) in scenario.phase_handoffs)
                    for task in allowed):
                status, label = "continuation", "可接续"
            elif any(target.flight_id is not None and task.flight_id == target.flight_id
                     and task.start < target.end and target.start < task.end
                     and (task.concurrent or target.concurrent) for task in allowed):
                status, label = "concurrent", "可兼任"
            else:
                status, label = "available", "可安排"
        options.append({"id": member.id, "name": member.name, "available": not reasons, "manualAllowed": manual_allowed,
                        "reasons": reasons, "issues": explained, "status": status, "label": label})
    return options


def name_suggestions(text, names):
    import re
    unknown = [n.strip() for n in re.split(r'[/／、,，;；\n]+', text) if n.strip() and n.strip() not in names]
    matches = []
    for query in unknown:
        matches.extend(n for n in names if query in n or n in query)
        matches.extend(get_close_matches(query, names, n=5, cutoff=0.35))
    return list(dict.fromkeys(matches))[:8]
