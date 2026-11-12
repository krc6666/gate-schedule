"""Compile the domain's relaxed-shift facts into optional monthly soft goals."""
from .domain import member_available, relaxed_shift_facts, relaxed_shift_task_groups, validate_relaxed_shift_settings


def relaxed_shift_costs(model, scenario, variables, settings, counts, duty_member_id, baseline):
    if settings is None:
        return ()
    settings = validate_relaxed_shift_settings(settings)
    if not isinstance(counts, dict) or any(type(v) is not int or not 0 <= v <= 31 for v in counts.values()):
        raise ValueError('月度轻松班次历史次数须为0至31的整数')
    tasks, blockers = relaxed_shift_task_groups(scenario, settings)
    baseline_facts = relaxed_shift_facts(scenario, baseline, settings, duty_member_id)
    excesses, distribution = [], []
    distribution_bound = 0
    for kind in ('earlyDeparture', 'afternoonRest'):
        if not settings[kind + 'BalanceEnabled']:
            continue
        projected, opportunities = [], []
        for member in scenario.members:
            if member.team_leader or member.status != '正常' or not member_available(member):
                continue
            if kind == 'earlyDeparture' and member.id == duty_member_id:
                continue
            choices = [(t, variables[member.id, t.id]) for t in tasks if (member.id, t.id) in variables]
            if not choices:
                continue
            worked = model.new_bool_var(f'relaxed_worked_{kind}_{member.id}')
            model.add_max_equality(worked, [v for _, v in choices])
            rest = model.new_bool_var(f'relaxed_{kind}_{member.id}')
            blocked = [v for t, v in choices if t.id in blockers[kind]]
            model.add(rest <= worked)
            for variable in blocked:
                model.add(rest + variable <= 1)
            model.add(rest >= worked - sum(blocked))
            previous = counts.get((member.id, kind), 0)
            projected.append(previous + rest)
            opportunities.append(rest)
            # At a fixed number of opportunities this is the increment in the
            # sum of squared monthly counts, so ties go to those with fewer.
            distribution.append(previous * rest)
            distribution_bound += previous
        # Preserve the normal schedule's opportunity count, rather than
        # splitting useful handoffs or cancelling rest merely to equalize it.
        model.add(sum(opportunities) == sum(f[kind] for f in baseline_facts))
        for index, first in enumerate(projected):
            for second in projected[index + 1:]:
                excess = model.new_int_var(0, 32, f'relaxed_excess_{kind}_{index}_{len(excesses)}')
                gap = settings[kind + 'MaxDifference']
                model.add_max_equality(excess, [0, first - second - gap, second - first - gap])
                excesses.append(excess)
    return ((sum(excesses), 32 * len(excesses)), (sum(distribution), distribution_bound))
