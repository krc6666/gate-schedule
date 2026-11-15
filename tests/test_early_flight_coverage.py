"""Owner's shortage policy, through real solving and independent validation."""
from dataclasses import replace
from pathlib import Path
from io import BytesIO
import tempfile
import unittest

from openpyxl import load_workbook
from gate_schedule.domain import Assignment, Flight, Member, Scenario, Task
from gate_schedule.excel_io import import_complete_configuration
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule
from gate_schedule.workbench import Workbench
from tests.test_balance_configuration import configuration


ROOT = Path(__file__).resolve().parents[1]


def competing_flights():
    return Scenario('2026-11-12',
        (Flight('A', 'A100', '2026-11-12', 480, 600),
         Flight('B', 'B200', '2026-11-12', 500, 640)),
        (Member('m', '甲', frozenset({'普通岗', '专岗'}), 0),),
        (Task('early', 'A', '普通岗', 480, 600, 1, 'fixed', concurrent=True),
         Task('late', 'B', '普通岗', 500, 640, 1, 'fixed', concurrent=True)),
        release_policy='task')


def shortage_configuration():
    data = configuration()
    data['balanceSettings'].update(flightCountGap=None, priorityEnabled=False,
                                  priorityRoleNames=[], priorityAdjustments=[])
    data['day']['flights'][1].update(windowStart='10:20', windowEnd='11:40')
    data['day']['roleConfigs'][0]['exclusiveSameFlight'] = True
    data['day']['roleConfigs'][0]['concurrent'] = True  # Legacy duplicate switches.
    data['day']['roster'] = ['甲']
    data['day']['memberConfigs'] = data['day']['memberConfigs'][:1]
    data['day']['memberAvailability'] = data['day']['memberAvailability'][:1]
    data['peopleIds'] = {'甲': 'stable-a'}
    return data


def coverage_configuration():
    data = shortage_configuration()
    day = data['day']
    day['roleNames'].append('普通岗')
    day['roleConfigs'].append({'roleName': '普通岗', 'requiredCount': 1, 'workMode': 'boarding', 'concurrent': False})
    day['cells'] += [{**cell, 'id': cell['id'] + '-ordinary', 'roleName': '普通岗'} for cell in list(day['cells'])]
    day['roster'] = ['甲', '乙', '丙']
    day['memberConfigs'] = [{'memberName': name, 'eligibleRoleNames': list(day['roleNames'])} for name in day['roster']]
    day['memberAvailability'] = [{'memberName': name, 'participating': True, 'availableFrom': None, 'availableUntil': None} for name in day['roster']]
    data['peopleIds'] = {name: 'coverage-' + str(i) for i, name in enumerate(day['roster'])}
    return data


class EarlyFlightCoverageTests(unittest.TestCase):
    def test_earlier_end_wins_regardless_of_input_order(self):
        for reverse in (False, True):
            scene = competing_flights()
            if reverse:
                scene = replace(scene, flights=scene.flights[::-1], tasks=scene.tasks[::-1])
            with self.subTest(reverse=reverse):
                self.assertEqual({'early'}, {a.task_id for a in solve(scene, allow_partial=True).assignments})

    def test_earlier_flight_can_outrank_total_headcount_and_later_role_preference(self):
        scene = competing_flights()
        scene = replace(scene, tasks=(scene.tasks[0],
            replace(scene.tasks[1], id='late1', end=560, role_id='专岗'),
            replace(scene.tasks[1], id='late2', start=560, role_id='专岗')),
            coverage_priorities=(('专岗', 'protect'), ('普通岗', 'leave-empty')))
        self.assertEqual({'early'}, {a.task_id for a in solve(scene, allow_partial=True).assignments})

    def test_late_dedicated_position_precedes_early_ordinary_position(self):
        scene = competing_flights()
        scene = replace(scene, tasks=(scene.tasks[0],
            replace(scene.tasks[1], role_id='专岗', exclusive_same_flight=True)),
            coverage_priorities=(('普通岗', 'protect'), ('专岗', 'leave-empty')))
        self.assertEqual({'late'}, {a.task_id for a in solve(scene, allow_partial=True).assignments})

    def test_dedicated_shortage_minimizes_all_flights_before_ordinary_positions(self):
        scene = competing_flights()
        scene = replace(scene, tasks=(
            replace(scene.tasks[0], exclusive_same_flight=True),
            replace(scene.tasks[1], id='late1', end=560, exclusive_same_flight=True),
            replace(scene.tasks[1], id='late2', start=560, exclusive_same_flight=True)))
        self.assertEqual({'late1', 'late2'}, {a.task_id for a in solve(scene, allow_partial=True).assignments})

    def test_uses_flight_end_not_task_end_and_does_not_grant_cross_flight_permission(self):
        scene = competing_flights()
        scene = replace(scene, tasks=(scene.tasks[0], replace(scene.tasks[1], end=550)))
        values = solve(scene, allow_partial=True).assignments
        self.assertEqual({'early'}, {a.task_id for a in values})
        self.assertIn('overlap', {i.code for i in validate_schedule(scene, values + (Assignment('late', 'm'),))})

    def test_unqualified_or_fixed_late_assignment_cannot_be_bypassed(self):
        scene = competing_flights()
        scene = replace(scene, tasks=(replace(scene.tasks[0], role_id='unknown'), scene.tasks[1]))
        values = solve(scene, allow_partial=True).assignments
        self.assertEqual({'late'}, {a.task_id for a in values})
        self.assertIn('ineligible', {i.code for i in validate_schedule(scene, (Assignment('early', 'm'),))})
        scene = competing_flights()
        scene = replace(scene, tasks=(scene.tasks[0], replace(scene.tasks[1], locked_member_ids=('m',))))
        self.assertEqual({'late'}, {a.task_id for a in solve(scene, allow_partial=True).assignments})

    def test_explicit_later_work_time_allows_legal_continuation(self):
        scene = competing_flights()
        scene = replace(scene, tasks=(scene.tasks[0], replace(scene.tasks[1], start=600)))
        values = solve(scene, allow_partial=True).assignments
        self.assertEqual({'early', 'late'}, {a.task_id for a in values})
        self.assertFalse(validate_schedule(scene, values))

    def test_equal_flight_end_has_no_hidden_input_order_priority(self):
        scene = competing_flights()
        scene = replace(scene, flights=(replace(scene.flights[0], window_end=640), scene.flights[1]),
            tasks=(scene.tasks[0], replace(scene.tasks[1], id='late1', end=560),
                   replace(scene.tasks[1], id='late2', start=560)))
        self.assertEqual({'late1', 'late2'}, {a.task_id for a in solve(scene, allow_partial=True).assignments})

    def test_many_flight_end_levels_stay_within_integer_objective_limits(self):
        scene = competing_flights()
        flights = tuple(Flight(str(i), 'F'+str(i), scene.service_date, 480, 600+i) for i in range(70))
        tasks = tuple(replace(scene.tasks[0], id=f.id, flight_id=f.id, end=f.window_end) for f in flights)
        scene = replace(scene, flights=flights, tasks=tasks)
        result = solve(scene, allow_partial=True, time_limit_seconds=3)
        self.assertIn(result.status, {'OPTIMAL', 'FEASIBLE'})
        self.assertEqual({'0'}, {a.task_id for a in result.assignments})


class DedicatedShortageWorkflowTests(unittest.TestCase):
    def test_shortage_stays_visible_after_excel_empty_database_restart_and_archive(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='early-coverage-') as directory:
            work = Workbench(Path(directory) / 'source.db')
            state = work.execute(work.snapshot()['revision'], 'import-complete-configuration', shortage_configuration())
            for role in state['configuration']['roleConfigs']:
                if role['roleName'] != '登机主验':
                    state = work.execute(state['revision'], 'save-role', {'roleName': role['roleName'], 'requiredCount': 0})
            incoming = import_complete_configuration(work.export_complete_configuration())
            target = Workbench(Path(directory) / 'target.db')
            target.execute(target.snapshot()['revision'], 'import-complete-configuration', incoming)
            target = Workbench(Path(directory) / 'target.db')
            state = target.snapshot()
            role = next(r for r in state['configuration']['roleConfigs'] if r['roleName'] == '登机主验')
            self.assertTrue(role['exclusiveSameFlight'])
            state = target.execute(state['revision'], 'solve', {}, state['selectedPlanId'])
            plan = state['plans'][state['selectedPlanId']]
            self.assertEqual(1, len(plan['draft']))
            self.assertIn('专岗未配齐', plan['message'])
            self.assertTrue(all(t['exclusiveSameFlight'] for t in plan['schedule']['tasks']))
            self.assertEqual({'coverage_shortfall'}, {i['code'] for i in plan['issues']})
            self.assertFalse(plan['valid'])
            state = target.execute(state['revision'], 'adopt', {}, state['selectedPlanId'])
            self.assertEqual(plan['issues'], state['plans'][state['selectedPlanId']]['formalIssues'])
            book = load_workbook(BytesIO(target.export(state['selectedPlanId'])))
            self.assertTrue(any(cell.fill.fgColor.rgb == '00FDE2E2' for row in book.worksheets[0] for cell in row),
                            '专岗缺口在正式Excel中也标红')
            book.close()

    def test_one_switch_controls_actual_same_flight_sharing_without_rewriting_formal(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='single-switch-') as directory:
            work = Workbench(Path(directory) / 'source.db')
            data = shortage_configuration()
            day = data['day']
            day['flights'] = day['flights'][:1]
            day['cells'] = day['cells'][:1]
            day['roleNames'].append('登机核验')
            day['roleConfigs'].append({'roleName': '登机核验', 'requiredCount': 1, 'workMode': 'boarding', 'concurrent': False})
            day['memberConfigs'][0]['eligibleRoleNames'].append('登机核验')
            day['cells'].append({**day['cells'][0], 'id': 'other', 'roleName': '登机核验'})
            state = work.execute(0, 'import-complete-configuration', data)
            for role in state['configuration']['roleConfigs']:
                if role['roleName'] not in day['roleNames']:
                    state = work.execute(state['revision'], 'save-role', {'roleName': role['roleName'], 'requiredCount': 0})
            pid = state['selectedPlanId']
            state = work.execute(state['revision'], 'solve', {}, pid)
            self.assertEqual(1, len(state['plans'][pid]['draft']))
            state = work.execute(state['revision'], 'adopt', {}, pid)
            from copy import deepcopy
            original = deepcopy(work.backup()['state']['plans'][pid]['adopted'])
            state = work.execute(state['revision'], 'save-role', {'roleName': '登机主验', 'exclusiveSameFlight': False})
            role = next(r for r in state['configuration']['roleConfigs'] if r['roleName'] == '登机主验')
            self.assertTrue(role['concurrent'])
            state = work.execute(state['revision'], 'apply-config', {}, pid)
            state = work.execute(state['revision'], 'solve', {}, pid)
            self.assertTrue(state['plans'][pid]['valid'], state['plans'][pid]['issues'])
            self.assertEqual(2, len(state['plans'][pid]['draft']))
            self.assertEqual(original, work.backup()['state']['plans'][pid]['adopted'])
            restored = Workbench(Path(directory) / 'restore.db')
            restored.restore(0, work.backup())
            self.assertEqual(original, restored.backup()['state']['plans'][pid]['adopted'])


if __name__ == '__main__':
    unittest.main()
