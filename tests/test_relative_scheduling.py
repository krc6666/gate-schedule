import unittest
import tempfile
from pathlib import Path
from io import BytesIO
from copy import deepcopy
from dataclasses import replace

from gate_schedule.domain import Assignment, Flight, Member, Rotation, Scenario, Task, parse_minutes, format_minutes
from gate_schedule.excel_io import export_complete_configuration, import_complete_configuration
from gate_schedule.day_document import imported_day_from_dict, imported_day_to_dict
from gate_schedule.imported_schedule import imported_day_to_scenario
from gate_schedule.io import scenario_from_dict, scenario_to_dict
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule
from tests.test_work_phases import phase_day
from gate_schedule.workbench import Workbench
from gate_schedule.workspace_document import plan_scenario
from gate_schedule.manual_editing import candidate_options
from openpyxl import load_workbook


def relative_day():
    day = phase_day()
    day.update(attendanceMode='duty', dedicatedFlightProtection=True)
    day['roleNames'] = ['登机核验']
    day['roleConfigs'] = [{'roleName': '登机核验', 'requiredCount': 1, 'workMode': 'boarding',
                           'relativeTiming': {'anchor': 'departure', 'offsetMinutes': -40, 'durationMinutes': 40}}]
    day['flights'][0].update(windowStart='前日23:30', windowEnd='00:30', departureTime='00:20')
    day['cells'] = [day['cells'][2]]
    day['memberAvailability'] = [{'memberName': n, 'participating': True} for n in day['roster']]
    day['memberConfigs'] = [{'memberName': n, 'eligibleRoleNames': day['roleNames']} for n in day['roster']]
    return day


def shared_pool_day():
    data = relative_day()
    roles = ['大件行李拦截', '入境申报', '登机1', '登机2']
    data.update(roleNames=roles, roster=['甲', '乙', '丙'])
    data['flights'] = [dict(id='A', flightNo='A', windowStart='08:00', windowEnd='10:00'),
                       dict(id='B', flightNo='B', windowStart='09:00', windowEnd='11:00')]
    data['roleConfigs'] = [dict(roleName=r, requiredCount=0, workMode='timed') for r in roles]
    data['memberConfigs'] = [dict(memberName=n, eligibleRoleNames=roles) for n in data['roster']]
    data['memberAvailability'] = [dict(memberName=n, participating=True) for n in data['roster']]
    data['cells'] = [dict(id=f['id']+r, roleName=r, flightId=f['id'], flightNo=f['flightNo'],
                         rawValue='', internalNames=[], externalLabels=[], semantics='empty',
                         requiredCountOverride=1 if r == roles[0] or f['id'] == 'B' else 0,
                         workStart='09:00' if r == '入境申报' else '11:00',
                         workEnd='10:00' if r == '入境申报' else '11:30') for f in data['flights'] for r in roles]
    data['workRules'] = {'pairs': [dict(flightNo='*', firstRole='登机1', secondRole='登机2',
                                      scope='same-flight', mode='separate')], 'baggage': []}
    return data


class RelativeSchedulingTests(unittest.TestCase):
    def test_previous_day_generation_solve_and_json(self):
        day = imported_day_from_dict(relative_day())
        scenario, issues = imported_day_to_scenario(day)
        self.assertEqual([], issues)
        self.assertEqual((-20, 20, 'exact'), (scenario.tasks[0].start, scenario.tasks[0].end, scenario.tasks[0].timing_mode))
        self.assertEqual('前日23:40', format_minutes(-20))
        self.assertEqual(-20, parse_minutes('前日23:40'))
        self.assertEqual(-1460, parse_minutes(format_minutes(-1460)))
        self.assertEqual(day, imported_day_from_dict(imported_day_to_dict(day)))
        self.assertEqual(scenario, scenario_from_dict(scenario_to_dict(scenario)))
        result = solve(scenario)
        self.assertEqual('OPTIMAL', result.status)
        self.assertEqual([], validate_schedule(scenario, result.assignments))
        bounded = replace(scenario, members=tuple(replace(m, available_from=0) for m in scenario.members))
        self.assertEqual('INFEASIBLE', solve(bounded).status)

    def test_missing_anchor_and_invalid_rule_do_not_guess(self):
        data = relative_day(); data['flights'][0].pop('departureTime')
        _, issues = imported_day_to_scenario(imported_day_from_dict(data))
        self.assertIn('missing_relative_anchor', {i.code for i in issues})
        for patch in ({'durationMinutes': 0}, {'offsetMinutes': True}, {'anchor': 'windowEnd'}):
            data = relative_day(); data['roleConfigs'][0]['relativeTiming'].update(patch)
            with self.assertRaises(ValueError):
                imported_day_from_dict(data)

    def test_arrival_and_explicit_actual_time_priority(self):
        data = relative_day()
        data['flights'][0]['arrivalTime'] = '前日23:35'
        data['roleConfigs'][0]['relativeTiming'] = {'anchor': 'arrival', 'offsetMinutes': 0, 'durationMinutes': 30}
        scenario, issues = imported_day_to_scenario(imported_day_from_dict(data))
        self.assertEqual([], issues)
        self.assertEqual((-25, 5), (scenario.tasks[0].start, scenario.tasks[0].end))
        data['cells'][0].update(workModeOverride='timed', workStart='00:00', workEnd='00:10')
        scenario, issues = imported_day_to_scenario(imported_day_from_dict(data))
        self.assertEqual([], issues)
        self.assertEqual((0, 10), (scenario.tasks[0].start, scenario.tasks[0].end))

    def test_relative_settings_complete_excel_roundtrip(self):
        data = relative_day()
        payload = {'day': data, 'rotationEnabled': True, 'rotationMinutes': 60}
        restored = import_complete_configuration(export_complete_configuration(payload))['day']
        self.assertEqual(data['roleConfigs'][0]['relativeTiming'], restored['roleConfigs'][0]['relativeTiming'])
        self.assertEqual('00:20', restored['flights'][0]['departureTime'])
        scenario, issues = imported_day_to_scenario(imported_day_from_dict(restored))
        self.assertEqual([], issues)
        self.assertEqual(-20, scenario.tasks[0].start)
        self.assertTrue(scenario.dedicated_flight_protection)

    def test_dedicated_protection_uses_original_flight_end_and_rejects_manual_break(self):
        flights = (Flight('A', 'A', '2026-08-02', 600, 690), Flight('B', 'B', '2026-08-02', 650, 740))
        tasks = (Task('ded', 'A', '督导', 600, 620, 1, 'mobile', exclusive_same_flight=True),
                 Task('next', 'B', '登机核验', 660, 680, 1, 'mobile'))
        people = tuple(Member(n, n, frozenset(t.role_id for t in tasks), 0) for n in ('甲', '乙'))
        scenario = Scenario('2026-08-02', flights, people, tasks, release_policy='task')
        result = solve(scenario)
        self.assertEqual('OPTIMAL', result.status)
        self.assertEqual([], validate_schedule(scenario, result.assignments))
        broken = tuple(Assignment(t.id, '甲') for t in tasks)
        self.assertIn('dedicated_flight_protection', {i.code for i in validate_schedule(scenario, broken)})
        choices = candidate_options(scenario, (Assignment('ded', '甲'),), 'next')
        self.assertFalse(next(c['available'] for c in choices if c['id'] == '甲'))
        after = replace(scenario, tasks=(tasks[0], replace(tasks[1], start=690, end=710)))
        self.assertEqual([], validate_schedule(after, broken))
        legacy = scenario_to_dict(scenario); legacy.pop('dedicatedFlightProtection')
        self.assertEqual([], validate_schedule(scenario_from_dict(legacy), broken))

    def test_general_mobile_handoff_preferred_over_unrelated_worker(self):
        flights = (Flight('A', 'A', '2026-08-02', 480, 660), Flight('B', 'B', '2026-08-02', 600, 720))
        tasks = (Task('bag', 'A', '大件行李拦截', 480, 540, 1, 'rotation', locked_member_ids=('甲',)),
                 Task('board', 'B', '登机核验', 540, 600, 1, 'mobile', service_stage='boarding'))
        people = tuple(Member(n, n, frozenset(t.role_id for t in tasks), 0) for n in ('甲', '乙'))
        scenario = Scenario('2026-08-02', flights, people, tasks, release_policy='task',
                            rotations=(Rotation('bag', 'pool', 480, 540, 1, ('甲', '乙'), 0),))
        result = solve(scenario, previous_roles={('乙', '登机核验')})
        self.assertEqual('OPTIMAL', result.status)
        self.assertIn(Assignment('board', '甲'), result.assignments)
        self.assertEqual([], validate_schedule(scenario, result.assignments))
        overlap = replace(scenario, tasks=(tasks[0], replace(tasks[1], start=530)))
        self.assertIn('overlap', {i.code for i in validate_schedule(overlap, result.assignments)})

    def test_real_shared_pool_keeps_later_blocks_independent(self):
        data = shared_pool_day()
        scenario, issues = imported_day_to_scenario(imported_day_from_dict(data), 60)
        self.assertEqual([], issues)
        blocks = sorted((t for t in scenario.tasks if t.task_kind == 'rotation'), key=lambda t: t.start)
        self.assertEqual([(480, 540), (540, 600), (600, 660)], [(t.start, t.end) for t in blocks])
        result = solve(scenario)
        self.assertEqual('OPTIMAL', result.status)
        self.assertEqual([], validate_schedule(scenario, result.assignments))
        owner = next(a.member_id for a in result.assignments if a.task_id == blocks[0].id)
        self.assertIn(Assignment('imported-B入境申报', owner), result.assignments)
        broken = tuple(a for a in result.assignments if a.task_id != blocks[1].id) + (Assignment(blocks[1].id, owner),)
        self.assertIn('overlap', {i.code for i in validate_schedule(scenario, broken)})
        # A preference never overrides qualification or walking requirements.
        restricted = replace(scenario, minimum_transition_minutes=5)
        self.assertEqual([], validate_schedule(restricted, solve(restricted).assignments))

    def test_unified_configuration_empty_database_restart_and_legacy_retention(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()/'.tmp', prefix='relative-') as directory:
            source = Workbench(Path(directory)/'source.db')
            state = source.execute(0, 'import-complete-configuration',
                {'day': relative_day(), 'rotationEnabled': True, 'rotationMinutes': 60})
            pid = state['selectedPlanId']
            role = next(r for r in state['configuration']['roleConfigs'] if r['roleName'] == '登机核验')
            timing = {**role['relativeTiming'], 'offsetMinutes': -50, 'durationMinutes': 50}
            state = source.execute(state['revision'], 'save-role', {**role, 'relativeTiming': timing})
            # This source device also has the standard role catalogue; disable
            # its unrelated roles before applying the maintained configuration.
            for unrelated in state['configuration']['roleConfigs']:
                if unrelated['roleName'] != '登机核验':
                    state = source.execute(state['revision'], 'save-role', {**unrelated, 'requiredCount': 0})
            state = source.execute(state['revision'], 'apply-config', {}, pid)
            content = source.export_complete_configuration(pid)
            target_path = Path(directory)/'target.db'
            target = Workbench(target_path)
            state = target.execute(0, 'import-complete-configuration', import_complete_configuration(content))
            target = Workbench(target_path); state = target.snapshot(); pid = state['selectedPlanId']
            self.assertEqual(timing, next(r['relativeTiming'] for r in state['configuration']['roleConfigs'] if r['roleName'] == '登机核验'))
            scenario, issues = plan_scenario(state['plans'][pid])
            self.assertEqual([], issues)
            self.assertEqual(-30, scenario.tasks[0].start)
            state = target.execute(state['revision'], 'solve', {}, pid)
            self.assertTrue(state['plans'][pid]['valid'])
            # Legacy files missing the extension retain maintained local rules.
            book = load_workbook(BytesIO(content))
            for name in ('相对岗位时间', '航班起降时间', '任务规则版本'):
                del book[name]
            for row in book['使用说明'].iter_rows():
                if row[0].value == '相对时间版本':
                    row[0].value = '旧格式说明'
            stream = BytesIO(); book.save(stream); book.close()
            payload = import_complete_configuration(stream.getvalue())
            state = target.execute(state['revision'], 'import-complete-configuration', payload)
            self.assertEqual(timing, next(r['relativeTiming'] for r in state['configuration']['roleConfigs'] if r['roleName'] == '登机核验'))

    def test_relative_daily_override_and_explicit_disable_are_portable(self):
        data = relative_day()
        alternate = {'anchor': 'departure', 'offsetMinutes': -30, 'durationMinutes': 30}
        override = {**data['roleConfigs'][0], 'concurrent': False, 'relativeTiming': alternate}
        payload = {'day': data, 'rotationEnabled': True, 'rotationMinutes': 60,
                   'dayOverrides': {'roleConfigs': [override], 'qualifications': [], 'settings': {}}}
        decoded = import_complete_configuration(export_complete_configuration(payload))
        self.assertEqual(alternate, decoded['dayOverrides']['roleConfigs'][0]['relativeTiming'])
        override['relativeTiming'] = None
        decoded = import_complete_configuration(export_complete_configuration(payload))
        self.assertIsNone(decoded['dayOverrides']['roleConfigs'][0]['relativeTiming'])

    def test_missing_extension_sheet_rejected_instead_of_clearing_settings(self):
        content = export_complete_configuration({'day': relative_day(), 'rotationEnabled': True, 'rotationMinutes': 60})
        book = load_workbook(BytesIO(content)); del book['相对岗位时间']
        stream = BytesIO(); book.save(stream); book.close()
        with self.assertRaises(ValueError):
            import_complete_configuration(stream.getvalue())


if __name__ == '__main__':
    unittest.main()
