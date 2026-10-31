"""Formal-history ledger and monthly priority counts, shared by solving and display."""
from collections import defaultdict
from datetime import date

from .domain import Assignment, member_role_flight_counts, task_flight_ids
from .io import scenario_from_dict


def _month(value):
    if not isinstance(value, str) or len(value) != 7 or date.fromisoformat(value + '-01').strftime('%Y-%m') != value:
        raise ValueError('统计月份必须为 YYYY-MM')
    return value


def formal_history(state, *, before=None, month=None):
    """One deterministic latest formal per date; drafts and target/future dates excluded."""
    selected = {}
    for index, (identity, plan) in enumerate(state['plans'].items()):
        formal = plan.get('adopted')
        if not formal:
            continue
        day = formal['scenario']['serviceDate']
        if before is not None and day >= before or month is not None and not day.startswith(month + '-'):
            continue
        rank = (formal.get('savedRevision', -1), index, identity)
        if day not in selected or rank > selected[day][0]:
            selected[day] = (rank, formal)
    return [selected[day][1] for day in sorted(selected)]


def next_formal_revision(state):
    """Archive ordering survives a restore that restarts workspace CAS revisions."""
    return max(state['revision'], *(plan['adopted'].get('savedRevision', 0)
        for plan in state['plans'].values() if plan.get('adopted')), 0) + 1


def _ledger(state, month, *, before=None):
    counts = defaultdict(int)
    dates = defaultdict(list)
    for formal in formal_history(state, before=before, month=month):
        scenario = scenario_from_dict(formal['scenario'])
        assignments = tuple(Assignment(a['taskId'], a['memberId']) for a in formal['assignments'])
        for key, count in member_role_flight_counts(scenario, assignments).items():
            counts[key] += count
        flights = {f.id: f.flight_no for f in scenario.flights}
        tasks = {t.id: t for t in scenario.tasks}
        seen = set()
        for assignment in assignments:
            task = tasks[assignment.task_id]
            for flight_id in task_flight_ids(task):
                key = (assignment.member_id, task.role_id, flight_id)
                if key not in seen:
                    dates[key[:2]].append(f'{scenario.service_date} · {flights[flight_id]}')
                    seen.add(key)
    return counts, dates


def _adjustments(state, month):
    return {(a['memberId'], a['roleName']): a['delta']
        for a in state['balanceSettings']['priorityAdjustments'] if a['month'] == month}


def priority_counts_before(state, service_date):
    """Only earlier formal dates in target month; negative corrections floor at zero."""
    date.fromisoformat(service_date)
    month = service_date[:7]
    counts, _ = _ledger(state, month, before=service_date)
    adjustments = _adjustments(state, month)
    return {key: max(0, counts.get(key, 0) + adjustments.get(key, 0))
        for key in counts.keys() | adjustments.keys()}


def priority_statistics(state, month=None):
    if month is None:
        selected = state['plans'].get(state.get('selectedPlanId'))
        month = selected['day']['serviceDate'][:7] if selected else date.today().strftime('%Y-%m')
    month = _month(month)
    counts, dates = _ledger(state, month)
    adjustments = _adjustments(state, month)
    months = {month, *(f['scenario']['serviceDate'][:7] for f in formal_history(state)),
        *(a['month'] for a in state['balanceSettings']['priorityAdjustments'])}
    roles = []
    for role in state['balanceSettings']['priorityRoleNames']:
        members = []
        for person in state['people']:
            key = (person['id'], role)
            history, adjustment = counts.get(key, 0), adjustments.get(key, 0)
            qualified = not person.get('deleted', False) and role in person['eligibleRoleNames']
            if not qualified and not history and not adjustment:
                continue
            members.append({'memberId': person['id'], 'memberName': person['name'],
                'qualified': qualified, 'historyCount': history, 'adjustment': adjustment,
                'effectiveCount': max(0, history + adjustment), 'dates': dates.get(key, [])})
        roles.append({'roleName': role, 'members': members})
    totals = [{'memberId': person['id'], 'count': sum(
        row['effectiveCount'] for role in roles for row in role['members'] if row['memberId'] == person['id'])}
        for person in state['people']]
    return {'month': month, 'months': sorted(months, reverse=True), 'roles': roles, 'totals': totals,
        'revision': state['revision']}
