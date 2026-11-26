"""Monthly relaxed-shift ledger. Daily classification belongs to domain."""
from .domain import Assignment, relaxed_shift_facts
from .io import scenario_from_dict
from .priority_statistics import formal_history, statistics_history, _statistics_month
from .duty_roster import duty_statistics


def relaxed_shift_statistics(state, month=None, *, current=None, service_date=None, before=None):
    month = _statistics_month(state, month)
    rows = {p['id']: {'memberId': p['id'], 'memberName': p['name'],
                     'earlyDepartures': [], 'afternoonRestDates': [], 'lateFlights': [],
                     'morningFreeDates': [], 'afternoonFreeDates': [], 'afternoonOffDates': [], 'earlyDepartureDatesByFlight': {}}
            for p in state['people'] if not p.get('deleted', False) and not p.get('teamLeader', False)}
    duty_by_month = {}
    daily = {}
    for record in statistics_history(state, month, current=current, before=before):
        if not record.get('scenario'):
            continue
        scenario = scenario_from_dict(record['scenario'])
        day = scenario.service_date
        if day[:7] not in duty_by_month:
            duty_by_month[day[:7]] = {r['date']: r['dutyMemberId'] for r in duty_statistics(state, day[:7])['rows']}
        duty = record.get('dutyMemberId', duty_by_month[day[:7]].get(day))
        facts = relaxed_shift_facts(scenario, tuple(Assignment(a['taskId'], a['memberId'])
                                    for a in record['assignments']), state['relaxedShiftSettings'], duty)
        daily[day] = facts
        for fact in facts:
            row = rows.setdefault(fact['memberId'], {'memberId': fact['memberId'],
                'memberName': fact['memberName'], 'earlyDepartures': [], 'afternoonRestDates': [], 'lateFlights': [],
                'morningFreeDates': [], 'afternoonFreeDates': [], 'afternoonOffDates': [], 'earlyDepartureDatesByFlight': {}})
            if fact['earlyDeparture']:
                row['earlyDepartures'].append({'date': day, 'flightNo': fact['lastFlightNo'], 'endTime': fact['lastEndTime']})
                for number in fact['lastFlightNos']:
                    row['earlyDepartureDatesByFlight'].setdefault(number, []).append(day)
            for key in ('morningFree', 'afternoonFree', 'afternoonOff'):
                if fact[key]:
                    row[key + 'Dates'].append(day)
            if fact['afternoonRest']:
                row['afternoonRestDates'].append(day)
            row['lateFlights'].extend({'date': day, 'flightNo': n} for n in fact['lateFlightNos'])
    for row in rows.values():
        row.update(earlyDepartureCount=len(row['earlyDepartures']), afternoonRestCount=len(row['afternoonRestDates']), lateFlightCount=len(row['lateFlights']))
        for key in ('morningFree', 'afternoonFree', 'afternoonOff'):
            row[key + 'Count'] = len(row[key + 'Dates'])
    people = list(rows.values())
    current_facts = daily.get(service_date, [])
    summary = {'date': service_date, 'earlyDepartures': [], 'afternoonRest': []}
    for fact in current_facts:
        for flag, target, count in [('earlyDeparture', 'earlyDepartures', 'earlyDepartureCount'),
                                     ('afternoonRest', 'afternoonRest', 'afternoonRestCount')]:
            if fact[flag]:
                summary[target].append({**fact, 'monthlyCount': rows[fact['memberId']][count]})
    return {'revision': state['revision'], 'month': month, 'rows': people,
            'current': summary, 'settings': state['relaxedShiftSettings'], 'flightScope': relaxed_flight_scope(state)}


def relaxed_flight_scope(state):
    """Report columns only; independent from late-flight exclusions and solver goals."""
    candidates = list(dict.fromkeys(
        [f['flightNo'] for f in state['flightLibrary']]
        + [f['flightNo'] for p in state['plans'].values() for f in p['day']['flights']]
        + [f['flightNo'] for record in formal_history(state) for f in (record.get('scenario') or {}).get('flights', [])]
        + [n for n in state['relaxedShiftSettings'].get('statisticsFlightNos', [])]))
    selected = state['relaxedShiftSettings'].get('statisticsFlightNos',
        [n for n in candidates if n.startswith(('MF', 'CX')) and len(n) > 2 and n[2].isdigit()])
    return {'candidates': candidates, 'selected': selected}


def relaxed_counts_before(state, service_date):
    """Monthly formal history only: excludes the target day, drafts and future."""
    rows = relaxed_shift_statistics(state, service_date[:7], before=service_date)['rows']
    return {(row['memberId'], kind): row[kind + 'Count'] for row in rows
            for kind in ('earlyDeparture', 'afternoonRest')}
