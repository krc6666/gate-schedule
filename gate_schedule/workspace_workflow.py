"""Date-based operations; saved business rules stay with their existing owners."""
from copy import deepcopy
from .domain import personnel_status
from .priority_statistics import formal_history
from .flight_configuration import fill_day, seed_library, with_role_membership


def plan_for_date(state, service_date):
    selected = state['plans'].get(state.get('selectedPlanId'))
    if selected and selected['day']['serviceDate'] == service_date:
        return selected
    return next((p for p in reversed(list(state['plans'].values()))
                 if p['day']['serviceDate'] == service_date), None)


def history_plan_ids(state):
    # Reuse the ledger's ordering; retained legacy copies are not UI choices.
    return [next(identity for identity, plan in state['plans'].items()
                 if plan.get('adopted') is record) for record in formal_history(state)]


def carry_flights(day, source, state, flight_numbers):
    # A manually added day flight need not be a permanent template. Materialize
    # its existing data only for this new day, without changing the catalog.
    inherited = {'plans': {'source': {'day': source}}, 'weeklyFlightPresets': {}}
    seed_library(inherited)
    catalog = {'configuration': state['configuration'],
               'flightLibrary': deepcopy(state['flightLibrary'])}
    known = {f['flightNo'] for f in catalog['flightLibrary']}
    catalog['flightLibrary'].extend(f for f in inherited['flightLibrary'] if f['flightNo'] not in known)
    with_role_membership(catalog)
    fill_day(catalog, day, flight_numbers)


def carry_attendance(day, source, people, previous_states=None):
    previous = {a['memberName']: a for a in (source or {}).get('memberAvailability', [])}
    active = [p for p in people if p['active'] and not p.get('deleted', False)]
    day['roster'] = [p['name'] for p in active]
    day['memberConfigs'] = [{'memberName': p['name'], 'eligibleRoleNames': [r for r in p['eligibleRoleNames']
                            if r in day['roleNames']]} for p in active]
    day['memberAvailability'] = [{'memberName': p['name'],
        'participating': personnel_status(p) == '正常' and (
            p['id'] in (previous_states or {}) or previous.get(p['name'], {}).get('participating', True)),
        'availableFrom': None, 'availableUntil': None, 'halfRest': None} for p in active]


def clear_history(state):
    current = state['plans'].get(state.get('selectedPlanId'))
    state['plans'] = {current['id']: current} if current else {}
    if current:
        current['adopted'] = None
        current['preview'] = None
        for key in ('undoEdit', 'historyReferenceDate'):
            current.pop(key, None)
        current['message'] = '历史排班已清空，当前排班、人员及配置保留'
    else:
        state['selectedPlanId'] = None


def prepare_replacement(plan, day, state):
    """Replace current input on its date, retaining the archived snapshot."""
    from .workspace_document import apply_personnel_policy, sync_roster
    plan.update(day=deepcopy(day), inputRevision=plan['inputRevision'] + 1, draft=[], preview=None,
                balanceSettings=deepcopy(state['balanceSettings']))
    for key in ('undoEdit', 'lastEdit'):
        plan.pop(key, None)
    apply_personnel_policy(plan, state)
    sync_roster(plan, state['people'], refresh=False)
    if 'cellDrafts' in plan:
        plan['cellDrafts'] = {k: text for k, text in plan['cellDrafts'].items()
                              if k in {c['id'] for c in day['cells']}}
