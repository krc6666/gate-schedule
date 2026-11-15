import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from gate_schedule.domain import (Assignment, Flight, Member, Rotation, Scenario, Task,
    applicable_handoff_preference, baggage_targets, validate_handoff_preferences,
    validate_work_rules, work_pair_rule)
from gate_schedule.excel_io import import_complete_configuration
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule
from gate_schedule.manual_editing import candidate_options
from gate_schedule.workbench import Workbench
from gate_schedule.workspace_document import plan_scenario
from tests.test_relative_scheduling import relative_day


def preferences():
    return [dict(flightNo=f, arrivalRole='接机1', boardingRoles=targets, enabled=enabled)
            for f, targets, enabled in [('*', ['登机1'], True), ('CX*', ['登机2'], True),
                                       ('CX9*', [], False), ('CX937', ['登机1'], True)]]


def series_scenario():
    tasks = (Task('a', 'f', '接机1', 600, 620, 1, 'mobile'),
             Task('b', 'f', '登机1', 630, 650, 1, 'mobile'))
    people = tuple(Member(n, n, frozenset(t.role_id for t in tasks), 0) for n in ('甲', '乙'))
    pairs = [dict(flightNo=f, firstRole='接机1', secondRole='登机1', scope='same-flight', mode=m)
             for f, m in [('*', 'sequence'), ('CX*', 'separate')]]
    return Scenario('2026-11-13', (Flight('f', 'CX937', '2026-11-13', 600, 660),), people, tasks,
                    release_policy='task', work_rules=dict(pairs=pairs, baggage=[]))


class FlightSeriesTests(unittest.TestCase):
    def test_unified_rule_save_relative_times_atomic_and_legacy_retention(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()/'.tmp', prefix='series-save-') as directory:
            workbench = Workbench(Path(directory)/'state.db')
            state = workbench.snapshot()
            def payload(current):
                return dict(configuration=current['configuration'], balanceSettings=current['balanceSettings'], preferences=current['handoffPreferences'])
            incoming = payload(state)
            incoming['configuration']['roleConfigs'][0]['relativeTiming'] = dict(anchor='departure', offsetMinutes=-40, durationMinutes=40)
            state = workbench.execute(state['revision'], 'save-rules', incoming)
            self.assertEqual(-40, state['configuration']['roleConfigs'][0]['relativeTiming']['offsetMinutes'])
            before = workbench.backup()
            incoming = payload(state)
            incoming['configuration']['roleConfigs'][0]['relativeTiming']['durationMinutes'] = 0
            with self.assertRaises(ValueError):
                workbench.execute(state['revision'], 'save-rules', incoming)
            self.assertEqual(before, workbench.backup())
            incoming = payload(workbench.snapshot())
            incoming['configuration']['roleConfigs'] = None
            with self.assertRaises(ValueError):
                workbench.execute(state['revision'], 'save-rules', incoming)
            self.assertEqual(before, workbench.backup())
            state = workbench.snapshot()
            incoming = payload(state)
            incoming['configuration']['roleConfigs'][0].pop('relativeTiming')
            state = workbench.execute(state['revision'], 'save-rules', incoming)
            self.assertEqual(40, state['configuration']['roleConfigs'][0]['relativeTiming']['durationMinutes'])
            incoming = payload(state)
            incoming['configuration']['roleConfigs'][0]['relativeTiming'] = None
            state = workbench.execute(state['revision'], 'save-rules', incoming)
            self.assertIsNone(state['configuration']['roleConfigs'][0]['relativeTiming'])

    def test_exact_longest_series_and_disabled_do_not_merge(self):
        rows = preferences()
        validate_handoff_preferences(rows, ['接机1', '登机1', '登机2'], ['CX937'])
        for flight, expected in [('CX937', 'CX937'), ('CX931', 'CX9*'), ('CX888', 'CX*'), ('AK151', '*')]:
            with self.subTest(flight=flight):
                self.assertEqual(expected, applicable_handoff_preference(list(reversed(rows)), flight, '接机1')['flightNo'])
        self.assertFalse(applicable_handoff_preference(rows, 'CX931', '接机1')['enabled'])
        rows = [r for r in rows if r['flightNo'] != 'CX937']
        self.assertEqual('CX9*', applicable_handoff_preference(rows, 'CX937', '接机1')['flightNo'])

    def test_series_hard_rule_solver_and_independent_manual_rejection(self):
        scenario = series_scenario()
        result = solve(scenario)
        self.assertEqual('OPTIMAL', result.status)
        self.assertEqual([], validate_schedule(scenario, result.assignments))
        self.assertEqual(2, len({a.member_id for a in result.assignments}))
        broken = tuple(Assignment(t.id, '甲') for t in scenario.tasks)
        self.assertIn('role_separation', {i.code for i in validate_schedule(scenario, broken)})
        candidates = candidate_options(scenario, (Assignment('a', '甲'),), 'b')
        self.assertFalse(next(p['available'] for p in candidates if p['id'] == '甲'))
        exact = dict(flightNo='CX937', firstRole='接机1', secondRole='登机1', scope='same-flight', mode='sequence')
        scenario = replace(scenario, work_rules=dict(pairs=[exact, *scenario.work_rules['pairs']], baggage=[]))
        self.assertEqual('sequence', work_pair_rule(scenario, *scenario.tasks, 'same-flight')['mode'])
        self.assertEqual([], validate_schedule(scenario, broken))

    def test_baggage_series_uses_target_flight_and_disabled_stops_fallback(self):
        scenario = series_scenario()
        bag = Task('bag', None, '大件行李拦截', 540, 600, 1, 'rotation', covered_flight_ids=('f',))
        rows = [dict(flightNo=f, position=1, targetRoles=roles, enabled=enabled)
                for f, roles, enabled in [('*', ['登机1'], True), ('CX*', [], False)]]
        scenario = replace(scenario, tasks=(bag, *scenario.tasks),
            rotations=(Rotation('bag', 'pool', 540, 600, 1, ('甲', '乙'), 0),),
            work_rules=dict(pairs=[], baggage=rows))
        self.assertEqual([], baggage_targets(scenario, bag, scenario.tasks[-1]))
        scenario = replace(scenario, work_rules=dict(pairs=[], baggage=rows[:1]))
        self.assertEqual(['登机1'], baggage_targets(scenario, bag, scenario.tasks[-1]))

    def test_ambiguous_wildcards_rejected_by_all_rule_inputs(self):
        for value in ('CX**', '*CX', 'C*X', '**'):
            with self.subTest(value=value):
                row = {**preferences()[0], 'flightNo': value}
                with self.assertRaises(ValueError):
                    validate_handoff_preferences([row])
                pair = {**series_scenario().work_rules['pairs'][0], 'flightNo': value}
                with self.assertRaises(ValueError):
                    validate_work_rules(dict(pairs=[pair], baggage=[]))
                with self.assertRaises(ValueError):
                    validate_work_rules(dict(pairs=[], baggage=[dict(flightNo=value, position=1, targetRoles=[], enabled=False)]))

    def test_configuration_excel_empty_import_restart_and_actual_use(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()/'.tmp', prefix='series-') as directory:
            source = Workbench(Path(directory)/'source.db')
            day = relative_day()
            rules = dict(pairs=[], baggage=[dict(flightNo='CX*', position=1, targetRoles=['登机核验'], enabled=True)])
            prefs = [dict(flightNo='CX*', arrivalRole='接机1', boardingRoles=['登机核验'], enabled=True)]
            day['roleNames'].append('接机1')
            day['roleConfigs'].append(dict(roleName='接机1', requiredCount=0, workMode='arrival'))
            day['cells'].append({**day['cells'][0], 'id': 'arrival', 'roleName': '接机1'})
            for flight in day['flights']:
                flight['flightNo'] = 'CX937'
            for cell in day['cells']:
                cell['flightNo'] = 'CX937'
            state = source.execute(0, 'import-complete-configuration', dict(day=day, rotationEnabled=True, rotationMinutes=60, workRules=rules, handoffPreferences=prefs))
            pid = state['selectedPlanId']
            for role in state['configuration']['roleConfigs']:
                if role['roleName'] not in day['roleNames']:
                    state = source.execute(state['revision'], 'save-role', {**role, 'requiredCount': 0})
            state = source.execute(state['revision'], 'apply-config', {}, pid)
            payload = import_complete_configuration(source.export_complete_configuration(pid))
            target_path = Path(directory)/'target.db'
            target = Workbench(target_path)
            state = target.execute(0, 'import-complete-configuration', payload)
            target = Workbench(target_path)
            state = target.snapshot()
            self.assertEqual(prefs, state['handoffPreferences'])
            self.assertEqual(rules, state['workRules'])
            scenario, issues = plan_scenario(state['plans'][state['selectedPlanId']])
            self.assertEqual([], issues)
            self.assertEqual(-20, scenario.tasks[0].start)
            self.assertEqual([], validate_schedule(scenario, solve(scenario).assignments))


if __name__ == '__main__':
    unittest.main()
