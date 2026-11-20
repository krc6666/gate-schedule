from dataclasses import replace
from copy import deepcopy
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
    # Competition is now expressed by the single dedicated switch.
    for role in day['roleConfigs']: role['exclusiveSameFlight'] = True
    day['roster'] = ['甲']
    day['memberConfigs'] = [{'memberName': '甲', 'eligibleRoleNames': list(day['roleNames'])}]
    day['memberAvailability'] = day['memberAvailability'][:1]
    day['cells'] += [{**cell, 'id': cell['id'] + '-low', 'roleName': '秩序维护'} for cell in day['cells']]
    return data


def shortage_handoff_configuration():
    data = configuration()
    data['balanceSettings'].update(flightCountGap=None, priorityEnabled=False,
        priorityRoleNames=[], priorityAdjustments=[], priorityFlightRoles=[],
        coveragePriorities=[{'roleName': '接机1', 'priority': 'protect'},
                            {'roleName': '登机查验', 'priority': 'leave-empty'}])
    data['peopleIds'] = {'甲': 'stable-a'}
    day = data['day']
    day['flights'][1].update(windowStart='10:30', windowEnd='11:30')
    day['roleNames'] = ['接机1', '登机查验']
    day['roleConfigs'] = [dict(roleName=role, requiredCount=1, workMode=mode, concurrent=True)
                          for role, mode in [('接机1', 'arrival'), ('登机查验', 'boarding')]]
    day['roster'] = ['甲']
    day['memberConfigs'] = [dict(memberName='甲', eligibleRoleNames=day['roleNames'])]
    day['memberAvailability'] = day['memberAvailability'][:1]
    cell = day['cells'][0]
    day['cells'] = [dict(cell, id='early', roleName='接机1'),
                    dict(cell, id='low', roleName='登机查验'),
                    dict(cell, id='late', flightId='B', flightNo='B200', roleName='接机1'),
                    dict(cell, id='unused', flightId='B', flightNo='B200', roleName='登机查验', requiredCountOverride=0)]
    data['handoffPreferences'] = [dict(flightNo='*', arrivalRole='接机1',
        boardingRoles=['登机查验'], enabled=True, ordered=True)]
    day['flowPreferences'] = deepcopy(data['handoffPreferences'])
    return data


class CoveragePriorityTests(unittest.TestCase):
    def test_configured_part_time_remains_when_other_arrival_is_short(self):
        roles = frozenset({'接机1', '登机查验'})
        scene = Scenario('2026-08-20',
            (Flight('A', 'CX937', '2026-08-20', 610, 690),
             Flight('B', 'KE0166', '2026-08-20', 660, 735)),
            (Member('m', '甲', roles, 0),),
            (Task('early', 'A', '接机1', 610, 690, 1, 'mobile',
                  timing_mode='phase', service_stage='arrival'),
             Task('low', 'A', '登机查验', 610, 690, 1, 'mobile',
                  timing_mode='phase', service_stage='boarding'),
             Task('late', 'B', '接机1', 660, 735, 1, 'mobile',
                  timing_mode='phase', service_stage='arrival')),
            release_policy='task',
            coverage_priorities=(('接机1', 'protect'), ('登机查验', 'leave-empty')),
            flow_preferences=({'flightNo': '*', 'arrivalRole': '接机1',
                'boardingRoles': ['登机查验'], 'enabled': True, 'ordered': True},))
        result = solve(scene, allow_partial=True)
        self.assertEqual('OPTIMAL', result.status)
        self.assertEqual({Assignment('early', 'm'), Assignment('low', 'm')}, set(result.assignments))
        self.assertEqual({('coverage_shortfall', 'late')},
            {(i.code, i.task_id) for i in validate_schedule(scene, result.assignments)})
        manual = result.assignments
        self.assertEqual({('coverage_shortfall', 'late')},
            {(i.code, i.task_id) for i in validate_schedule(scene, manual)})

        without_low = replace(scene, tasks=tuple(t for t in scene.tasks if t.id != 'low'))
        result = solve(without_low, allow_partial=True)
        self.assertEqual('OPTIMAL', result.status)
        self.assertEqual((Assignment('early', 'm'),), result.assignments)
        self.assertEqual({('coverage_shortfall', 'late')},
            {(i.code, i.task_id) for i in validate_schedule(without_low, result.assignments)})
        broken = result.assignments + (Assignment('late', 'm'),)
        self.assertIn('overlap', {i.code for i in validate_schedule(without_low, broken)})

        # A later independent window still staffs its leave-empty role.
        separate = replace(scene,
            flights=scene.flights + (Flight('C', 'FD573', scene.service_date, 735, 800),),
            tasks=scene.tasks + (Task('separate', 'C', '登机查验', 735, 800, 1, 'mobile',
                                     timing_mode='phase', service_stage='boarding'),))
        values = solve(separate, allow_partial=True).assignments
        self.assertEqual({'early', 'low', 'separate'}, {a.task_id for a in values})

        enough = replace(scene, members=scene.members + (replace(scene.members[0], id='n', name='乙'),))
        full = solve(enough, allow_partial=True)
        self.assertFalse(validate_schedule(enough, full.assignments))
        self.assertEqual({'early', 'low', 'late'}, {a.task_id for a in full.assignments})

    def test_leave_empty_boarding_roles_yield_to_normal_coverage_before_handoff(self):
        roles = ('接机1', '接机2', '登机主验', '登机核验', '登机人证核对/队伍秩序维护')
        tasks = tuple(Task(str(i), 'A', role, 600, 720, 2 if i == 2 else 1,
            'mobile', timing_mode='phase', service_stage='arrival' if i < 2 else 'boarding')
            for i, role in enumerate(roles))
        preferences = ({'flightNo': '*', 'arrivalRole': '接机1',
            'boardingRoles': list(roles[3:]), 'enabled': True, 'ordered': True},)
        rules = {'pairs': [], 'baggage': [], 'separationGroups': [
            {'id': 'boarding', 'name': '登机各岗分人', 'flightNo': '*',
             'roles': list(roles[2:]), 'enabled': True}]}
        scene = Scenario('2026-08-20', (Flight('A', 'CX937', '2026-08-20', 600, 720),),
            tuple(Member(str(i), str(i), frozenset(roles), 0) for i in range(2)), tasks,
            release_policy='task', flow_preferences=preferences, work_rules=rules,
            coverage_priorities=tuple((role, 'leave-empty') for role in roles[3:]))

        result = solve(scene, allow_partial=True)
        self.assertEqual('OPTIMAL', result.status)
        self.assertEqual({'0', '1', '2'}, {a.task_id for a in result.assignments})
        self.assertEqual(2, sum(a.task_id == '2' for a in result.assignments))
        self.assertEqual({('coverage_shortfall', '3'), ('coverage_shortfall', '4')},
            {(i.code, i.task_id) for i in validate_schedule(scene, result.assignments)})

        # More staff fills the low-priority roles; it never means always empty.
        enough = replace(scene, members=tuple(Member(str(i), str(i), frozenset(roles), 0)
                                               for i in range(4)))
        full = solve(enough, allow_partial=True)
        self.assertEqual('OPTIMAL', full.status)
        self.assertEqual(6, len(full.assignments))
        self.assertFalse(validate_schedule(enough, full.assignments))
        source_member = next(a.member_id for a in full.assignments if a.task_id == '0')
        self.assertIn(Assignment('3', source_member), full.assignments)

        # Withholding does not permit filling an unqualified normal role.
        limited = replace(scene, members=tuple(replace(m,
            eligible_role_ids=m.eligible_role_ids - {'登机主验'}) for m in scene.members))
        partial = solve(limited, allow_partial=True)
        self.assertFalse(any(a.task_id == '2' for a in partial.assignments))
        source_member = next(a.member_id for a in partial.assignments if a.task_id == '0')
        self.assertIn(Assignment('3', source_member), partial.assignments)
        self.assertFalse(any(a.task_id == '4' for a in partial.assignments))
        broken = partial.assignments + (Assignment('2', limited.members[0].id),)
        self.assertIn('ineligible', {i.code for i in validate_schedule(limited, broken)})

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
        self.assertFalse(values, '高档缺岗时留空岗也不自动安排')
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
        self.assertEqual({'low1'}, {a.task_id for a in result.assignments})
        self.assertEqual({'coverage_shortfall'}, {i.code for i in validate_schedule(scene, result.assignments)})
        self.assertIn('lock_missing', {i.code for i in validate_schedule(scene, (Assignment('high', 'm'),))})


class CoverageMigrationTests(unittest.TestCase):
    def test_shortage_withholding_excel_restart_archive_next_and_restore(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='leave-empty-archive-') as directory:
            work = Workbench(Path(directory) / 'source.db')
            data = shortage_handoff_configuration()
            state = work.execute(0, 'import-complete-configuration', data)
            for role in state['configuration']['roleConfigs']:
                if role['roleName'] not in data['day']['roleNames']:
                    state = work.execute(state['revision'], 'save-role', {**role, 'requiredCount': 0})
            state = work.execute(state['revision'], 'solve', {}, state['selectedPlanId'])
            self.assertEqual({'imported-early', 'imported-low'}, {a['taskId'] for a in state['plans'][state['selectedPlanId']]['draft']})
            payload = import_complete_configuration(work.export_complete_configuration())
            target_path = Path(directory) / 'target.db'
            target = Workbench(target_path)
            target.execute(0, 'import-complete-configuration', payload)
            target = Workbench(target_path)
            state = target.snapshot()
            pid = state['selectedPlanId']
            before = deepcopy(state['plans'][pid]['draft'])
            day = deepcopy(state['plans'][pid]['day'])
            day['serviceDate'] = '2026-08-22'
            state = target.execute(state['revision'], 'archive-next', {'day': day}, pid)
            self.assertEqual(before, state['plans'][pid]['adopted']['assignments'])
            current = state['plans'][state['selectedPlanId']]
            self.assertEqual(data['balanceSettings']['coveragePriorities'], current['balanceSettings']['coveragePriorities'])
            tasks = {t['id']: t for t in current['schedule']['tasks']}
            flights = {f['id']: f['flightNo'] for f in current['schedule']['flights']}
            self.assertEqual({('A100', '接机1'), ('A100', '登机查验')}, {(flights[tasks[a['taskId']]['flightId']], tasks[a['taskId']]['roleId']) for a in current['draft']})
            self.assertTrue(all(i['code'] == 'coverage_shortfall' for i in current['issues']))
            restored = Workbench(Path(directory) / 'restored.db')
            restored.restore(0, target.backup())
            state = restored.snapshot()
            state = restored.execute(state['revision'], 'solve', {}, state['selectedPlanId'])
            current = state['plans'][state['selectedPlanId']]
            tasks = {t['id']: t for t in current['schedule']['tasks']}
            flights = {f['id']: f['flightNo'] for f in current['schedule']['flights']}
            self.assertEqual({('A100', '接机1'), ('A100', '登机查验')}, {(flights[tasks[a['taskId']]['flightId']], tasks[a['taskId']]['roleId']) for a in current['draft']})

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
