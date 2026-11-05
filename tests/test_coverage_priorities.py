from dataclasses import replace
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from openpyxl import load_workbook

from gate_schedule.domain import Assignment, Flight, Member, Scenario, Task
from gate_schedule.excel_io import export_complete_configuration, import_complete_configuration
from gate_schedule.io import scenario_from_dict, scenario_to_dict
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule
from gate_schedule.workbench import Workbench
from gate_schedule.group_workspaces import GroupWorkspaces
from tests.test_balance_configuration import configuration, workbook_bytes


ROOT = Path(__file__).resolve().parents[1]


def competing():
    return Scenario('2026-08-20',
        (Flight('A', 'A100', '2026-08-20', 600, 720),),
        (Member('m', '甲', frozenset({'登机核验', '秩序维护'}), 0),),
        (Task('high', 'A', '登机核验', 600, 720, 1, 'fixed'),
         Task('low1', 'A', '秩序维护', 600, 660, 1, 'fixed'),
         Task('low2', 'A', '秩序维护', 660, 720, 1, 'fixed')), release_policy='task',
        coverage_priorities=(('登机核验', 'protect'), ('秩序维护', 'leave-empty')))


def maintained_configuration():
    data = configuration()
    data['balanceSettings'].update(flightCountGap=None, priorityEnabled=False, priorityFlightRoles=[], priorityAdjustments=[],
        coveragePriorities=[{'roleName': '登机主验', 'priority': 'protect'},
                            {'roleName': '秩序维护', 'priority': 'leave-empty'}])
    data['peopleIds'] = {'甲': 'stable-a'}
    day = data['day']
    day['roleNames'].append('秩序维护')
    day['roleConfigs'].append({'roleName': '秩序维护', 'requiredCount': 1, 'workMode': 'boarding', 'concurrent': False})
    day['roster'] = ['甲']
    day['memberConfigs'] = [{'memberName': '甲', 'eligibleRoleNames': list(day['roleNames'])}]
    day['memberAvailability'] = day['memberAvailability'][:1]
    day['cells'] += [{**cell, 'id': cell['id'] + '-low', 'roleName': '秩序维护'} for cell in day['cells']]
    return data


class CoveragePriorityTests(unittest.TestCase):
    def test_no_preselected_roles_and_same_tier_maximizes_coverage(self):
        scene = replace(competing(), coverage_priorities=())
        self.assertEqual({'low1', 'low2'}, {a.task_id for a in solve(scene, allow_partial=True).assignments})

    def test_protect_before_total_coverage_and_existing_preferences(self):
        scene = competing()
        result = solve(scene, allow_partial=True)
        self.assertEqual('OPTIMAL', result.status)
        self.assertEqual({'high'}, {a.task_id for a in result.assignments})
        self.assertEqual({'coverage_shortfall'}, {i.code for i in validate_schedule(scene, result.assignments)})

    def test_normal_coverage_outranks_leave_empty_coverage(self):
        scene = replace(competing(), coverage_priorities=(('登机核验', 'normal'), ('秩序维护', 'leave-empty')))
        result = solve(scene, allow_partial=True)
        self.assertEqual('OPTIMAL', result.status)
        self.assertEqual({'high'}, {a.task_id for a in result.assignments})

    def test_role_override_can_reverse_selection_and_survives_scenario_json(self):
        raw = scenario_to_dict(competing())
        raw['coveragePriorities'] = [{'roleName': '登机核验', 'priority': 'leave-empty'},
                                    {'roleName': '秩序维护', 'priority': 'protect'}]
        scene = scenario_from_dict(raw)
        self.assertEqual(raw, scenario_to_dict(scene))
        self.assertEqual({'low1', 'low2'}, {a.task_id for a in solve(scene, allow_partial=True).assignments})

    def test_role_policy_applies_to_both_flights_and_shared_task_once(self):
        scene = competing()
        second = Flight('B', 'B200', scene.service_date, 800, 920)
        scene = replace(scene, flights=scene.flights + (second,),
            tasks=scene.tasks + tuple(replace(t, id=t.id+'-b', flight_id='B', start=t.start+200, end=t.end+200) for t in scene.tasks))
        result = solve(scene, allow_partial=True)
        self.assertEqual({'high', 'high-b'}, {a.task_id for a in result.assignments})
        # A shared task with two flight identities is still one staffed position.
        shared = replace(scene.tasks[0], covered_flight_ids=('A', 'B'))
        scene = replace(scene, tasks=(shared,))
        result = solve(scene, allow_partial=True)
        self.assertEqual(1, len(result.assignments))
        self.assertFalse(validate_schedule(scene, result.assignments))

    def test_priority_cannot_override_qualification_or_exclusive_rule(self):
        scene = competing()
        scene = replace(scene, members=(replace(scene.members[0], eligible_role_ids=frozenset({'秩序维护'})),))
        values = solve(scene, allow_partial=True).assignments
        self.assertEqual({'low1', 'low2'}, {a.task_id for a in values})
        self.assertIn('ineligible', {i.code for i in validate_schedule(scene, values + (Assignment('high', 'm'),))})
        scene = competing()
        scene = replace(scene, tasks=tuple(replace(t, exclusive_same_flight=True) if t.id == 'high' else t for t in scene.tasks))
        values = solve(scene, allow_partial=True).assignments
        self.assertEqual({'high'}, {a.task_id for a in values})
        self.assertIn('exclusive_same_flight', {i.code for i in validate_schedule(scene, values + (Assignment('low1', 'm'),))})

    def test_invalid_priority_and_duplicate_reject(self):
        for items in ([{'roleName': '登机核验', 'priority': 'bad'}],
                      [{'roleName': '登机核验', 'priority': 'normal'}] * 2,
                      [{'flightNo': 'A100', 'roleName': '登机核验', 'priority': 'normal'}]):
            with self.subTest(items=items), self.assertRaises(ValueError):
                scenario_from_dict({**scenario_to_dict(competing()), 'coveragePriorities': items})

    def test_fixed_low_priority_assignment_remains_binding(self):
        scene = competing()
        scene = replace(scene, tasks=tuple(replace(t, locked_member_ids=('m',)) if t.id == 'low1' else t for t in scene.tasks))
        result = solve(scene, allow_partial=True)
        self.assertEqual({'low1', 'low2'}, {a.task_id for a in result.assignments})
        self.assertEqual({'coverage_shortfall'}, {i.code for i in validate_schedule(scene, result.assignments)})
        self.assertIn('lock_missing', {i.code for i in validate_schedule(scene, (Assignment('high', 'm'),))})


class CoverageMigrationTests(unittest.TestCase):
    def test_protected_shortfall_warns_and_old_page_omission_keeps_selection(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='coverage-warning-') as directory:
            work = Workbench(Path(directory) / 'state.db')
            data = maintained_configuration()
            data['day']['roleConfigs'][0]['requiredCount'] = 2
            state = work.execute(work.snapshot()['revision'], 'import-complete-configuration', data)
            state = work.execute(state['revision'], 'solve', {}, state['selectedPlanId'])
            plan = state['plans'][state['selectedPlanId']]
            self.assertIn('优先保岗仍有缺口', plan['message'])
            self.assertEqual({'coverage_shortfall'}, {i['code'] for i in plan['issues']})
            old_payload = {key: value for key, value in state['balanceSettings'].items() if key != 'coveragePriorities'}
            state = work.execute(state['revision'], 'update-balance-settings', {'balanceSettings': old_payload})
            self.assertEqual(data['balanceSettings']['coveragePriorities'], state['balanceSettings']['coveragePriorities'])
            empty = Workbench(Path(directory) / 'restored.db')
            empty.restore(0, work.backup())
            self.assertEqual(state['balanceSettings'], empty.snapshot()['balanceSettings'])

    def test_excel_empty_database_restart_actual_solve_and_legacy_preservation(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='coverage-') as directory:
            work = Workbench(Path(directory) / 'first.db')
            data = maintained_configuration()
            state = work.execute(work.snapshot()['revision'], 'import-complete-configuration', data)
            for role in state['configuration']['roleConfigs']:
                if role['roleName'] not in data['day']['roleNames']:
                    state = work.execute(state['revision'], 'save-role', {'roleName': role['roleName'], 'requiredCount': 0})
            decoded = import_complete_configuration(work.export_complete_configuration())
            self.assertEqual(data['balanceSettings']['coveragePriorities'], decoded['balanceSettings']['coveragePriorities'])
            target = Workbench(Path(directory) / 'empty.db')
            target.execute(target.snapshot()['revision'], 'import-complete-configuration', decoded)
            target = Workbench(Path(directory) / 'empty.db')
            state = target.snapshot()
            self.assertEqual(data['balanceSettings']['coveragePriorities'], state['balanceSettings']['coveragePriorities'])
            result = target.execute(state['revision'], 'solve', {}, state['selectedPlanId'])
            plan = result['plans'][result['selectedPlanId']]
            self.assertEqual({'登机主验'}, {t['roleId'] for t in plan['schedule']['tasks']
                if t['id'] in {a['taskId'] for a in plan['draft']}})
            self.assertEqual(2, len(plan['draft']), '两个航班统一按所选岗位保岗')
            target.execute(result['revision'], 'adopt', {}, result['selectedPlanId'])
            formal = target.backup()['state']['plans'][result['selectedPlanId']]['adopted']
            from gate_schedule.workspace_document import plan_scenario
            scene, issues = plan_scenario(target.backup()['state']['plans'][result['selectedPlanId']])
            self.assertFalse(issues)
            self.assertEqual(tuple((i['roleName'], i['priority']) for i in data['balanceSettings']['coveragePriorities']), scene.coverage_priorities)
            self.assertEqual(data['balanceSettings']['coveragePriorities'], formal['scenario']['coveragePriorities'])
            legacy = configuration()
            state = target.execute(target.snapshot()['revision'], 'import-complete-configuration', legacy)
            self.assertEqual(data['balanceSettings']['coveragePriorities'], state['balanceSettings']['coveragePriorities'])
            self.assertEqual(formal, target.backup()['state']['plans'][result['selectedPlanId']]['adopted'])
            clear = {**state['balanceSettings'], 'coveragePriorities': []}
            state = target.execute(state['revision'], 'update-balance-settings', {'balanceSettings': clear})
            self.assertEqual([], state['balanceSettings']['coveragePriorities'])

    def test_two_groups_opposite_choices_excel_restart_and_actual_effect(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='coverage-groups-') as directory:
            groups = GroupWorkspaces(Path(directory) / 'source.db')
            groups.initialize()
            for group in ('A', 'B'):
                work = groups.workbench(group)
                data = maintained_configuration()
                if group == 'B':
                    for item in data['balanceSettings']['coveragePriorities']:
                        item['priority'] = 'leave-empty' if item['priority'] == 'protect' else 'protect'
                state = work.execute(work.snapshot()['revision'], 'import-complete-configuration', data)
                for role in state['configuration']['roleConfigs']:
                    if role['roleName'] not in data['day']['roleNames']:
                        state = work.execute(work.snapshot()['revision'], 'save-role', {'roleName': role['roleName'], 'requiredCount': 0})
            content = groups.export_configuration('A')
            incoming = import_complete_configuration(content)
            target = GroupWorkspaces(Path(directory) / 'target.db')
            target.initialize()
            preview = target.preview_configuration('A', incoming)
            target.import_configuration('A', preview['expectedRevision'], preview['payload'])
            target = GroupWorkspaces(Path(directory) / 'target.db')
            for group, expected in (('A', '登机主验'), ('B', '秩序维护')):
                work = target.workbench(group)
                state = work.snapshot()
                self.assertEqual(groups.workbench(group).snapshot()['balanceSettings']['coveragePriorities'], state['balanceSettings']['coveragePriorities'])
                state = work.execute(state['revision'], 'solve', {}, state['selectedPlanId'])
                plan = state['plans'][state['selectedPlanId']]
                self.assertEqual(2, len(plan['draft']))
                self.assertEqual({expected}, {t['roleId'] for t in plan['schedule']['tasks']
                    if t['id'] in {a['taskId'] for a in plan['draft']}})

    def test_excel_missing_companion_and_invalid_value_reject(self):
        data = configuration()
        data['balanceSettings']['coveragePriorities'] = [{'roleName': '登机主验', 'priority': 'normal'}]
        content = export_complete_configuration(data)
        for remove in ('保岗优先级', '排班平衡'):
            book = load_workbook(BytesIO(content))
            del book[remove]
            with self.subTest(remove=remove), self.assertRaises(ValueError):
                import_complete_configuration(workbook_bytes(book))
        book = load_workbook(BytesIO(content))
        book['保岗优先级']['B2'] = '随便'
        with self.assertRaises(ValueError):
            import_complete_configuration(workbook_bytes(book))
