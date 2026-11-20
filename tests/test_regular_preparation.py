from copy import deepcopy
from dataclasses import replace
from io import BytesIO
import unittest
from openpyxl import load_workbook

import test_workspace as fixtures
from gate_schedule.domain import Assignment
from gate_schedule.excel_io import export_complete_configuration, import_complete_configuration
from gate_schedule.day_document import imported_day_from_dict, imported_day_to_dict
from gate_schedule.imported_schedule import imported_day_to_scenario
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule
from gate_schedule.workbench import Workbench, WorkspaceError
from gate_schedule.workspace_document import plan_scenario


class RegularPreparationTests(unittest.TestCase):
    setUp = fixtures.WorkspaceTests.setUp
    tearDown = fixtures.WorkspaceTests.tearDown
    command = fixtures.WorkspaceTests.command
    complete_plan = fixtures.WorkspaceTests.complete_plan

    def baggage_plan(self):
        role = '大件行李拦截'
        for name in ('甲', '乙', '丙'):
            self.command('save-person', {'name': name, 'eligibleRoleNames': [role]})
        state = self.command('new-plan', {'serviceDate': '2026-08-02', 'roleNames': [role]})
        plan_id = state['selectedPlanId']
        day = state['plans'][plan_id]['day']
        day['roleConfigs'] = [{'roleName': role, 'requiredCount': 1, 'workMode': 'continuous'}]
        day['flights'] = [{'id': 'f1', 'flightNo': 'AK151', 'windowStart': '08:00', 'windowEnd': '10:00'}]
        day['cells'] = [{'id': 'c1', 'flightId': 'f1', 'flightNo': 'AK151', 'roleName': role,
                         'rawValue': '', 'internalNames': [], 'externalLabels': [], 'semantics': 'empty',
                         'workStart': '08:00', 'workEnd': '10:00', 'fixedMemberNames': [], 'requiredCountOverride': None}]
        return plan_id, self.command('save-input', {'day': day}, plan_id)['plans'][plan_id]

    def assert_baggage_tasks(self, plan, lengths, rotating):
        self.assertTrue(plan['valid'], plan['issues'])
        scenario, issues = plan_scenario(plan)
        self.assertEqual([], issues)
        self.assertEqual(lengths, [task.end - task.start for task in scenario.tasks])
        self.assertEqual(['rotation' if rotating else 'mobile'] * len(lengths), [task.task_kind for task in scenario.tasks])
        self.assertEqual(len(lengths) if rotating else 0, len(scenario.rotations))
        self.assertEqual({task.id for task in scenario.tasks}, {entry['taskId'] for entry in plan['draft']})

    def test_preparation_applies_only_explicit_rotation_values_to_actual_tasks(self):
        plan_id, plan = self.baggage_plan()
        self.command('solve', {}, plan_id)
        formal = self.command('adopt', {}, plan_id)['plans'][plan_id]['adopted']
        latest = {'rotationEnabled': False, 'rotationMinutes': 30}
        self.command('save-config', {**latest, 'transitionMinutes': 0})
        old = self.command('prepare-solve', {'day': plan['day']}, plan_id)['plans'][plan_id]
        self.assert_baggage_tasks(old, [60, 60], rotating=True)

        updated = self.command('prepare-solve', {'day': old['day'], **latest}, plan_id)['plans'][plan_id]
        self.assert_baggage_tasks(updated, [120], rotating=False)
        self.assertGreater(updated['inputRevision'], old['inputRevision'])
        self.assertEqual(formal, updated['adopted'])

        reenabled = self.command('prepare-solve', {'day': updated['day'], 'rotationEnabled': True}, plan_id)['plans'][plan_id]
        self.assert_baggage_tasks(reenabled, [30, 30, 30, 30], rotating=True)
        self.assertEqual(formal, reenabled['adopted'])

    def test_invalid_or_unsolvable_rotation_preparation_preserves_saved_state(self):
        plan_id, _ = self.baggage_plan()
        self.command('solve', {}, plan_id)
        plan = self.command('adopt', {}, plan_id)['plans'][plan_id]
        before = self.workspace.backup()
        for invalid, message in [({'rotationEnabled': 'false'}, '布尔值'), ({'rotationMinutes': 0}, '1440'),
                                 ({'rotationMinutes': True}, '1440'), ({'rotationMinutes': None}, '1440')]:
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, message):
                    self.command('prepare-solve', {'day': plan['day'], **invalid}, plan_id)
                self.assertEqual(before, self.workspace.backup())
        prepared = {'day': deepcopy(plan['day']), 'rotationEnabled': False, 'rotationMinutes': 30}
        for entry in prepared['day']['memberAvailability']:
            entry['participating'] = False
        with self.assertRaisesRegex(WorkspaceError, '请选择当天参加人员'):
            self.command('prepare-solve', prepared, plan_id)
        self.assertEqual(before, self.workspace.backup())
        prepared['day']['memberAvailability'][0]['participating'] = True
        self.assert_baggage_tasks(self.command('prepare-solve', prepared, plan_id)['plans'][plan_id], [120], rotating=False)

    def test_excel_and_backup_keep_base_rotation_and_independent_daily_turns(self):
        plan_id, plan = self.baggage_plan()
        daily_rules = {'pairs': [], 'baggage': [], 'baggageWindow': {'startBeforeMinutes': 180,
                       'endBeforeMinutes': 60, 'rotationMinutes': 45, 'remainderToleranceMinutes': 0}}
        day = {**plan['day'], 'workRulesOverride': True, 'workRules': daily_rules}
        latest = {'rotationEnabled': False, 'rotationMinutes': 30}
        state = self.workspace.snapshot()
        self.command('save-rules', {'configuration': {**state['configuration'], **latest},
                     'balanceSettings': state['balanceSettings'], 'preferences': state['handoffPreferences'],
                     'workRules': {**daily_rules, 'baggageWindow': {**daily_rules['baggageWindow'], 'rotationMinutes': 60}}})
        prepared = self.command('prepare-solve', {'day': day, **latest}, plan_id)['plans'][plan_id]
        self.assert_baggage_tasks(prepared, [45, 45, 30], rotating=True)
        formal = self.command('adopt', {}, plan_id)['plans'][plan_id]['adopted']

        for role in self.workspace.snapshot()['configuration']['roleConfigs']:
            if role['roleName'] not in day['roleNames']:
                self.command('save-role', {'roleName': role['roleName'], 'requiredCount': 0})
        excel = import_complete_configuration(self.workspace.export_complete_configuration(plan_id))
        for medium in ('excel', 'backup'):
            with self.subTest(medium=medium):
                target_path = self.path.parent / f'{medium}.sqlite3'
                target = Workbench(target_path)
                if medium == 'excel':
                    target.execute(0, 'import-complete-configuration', excel)
                else:
                    target.restore(0, self.workspace.backup())
                target = Workbench(target_path)
                state = target.snapshot()
                restored_id = state['selectedPlanId']
                restored = state['plans'][restored_id]
                self.assertTrue(restored['day']['workRulesOverride'])
                self.assertEqual(daily_rules, restored['day']['workRules'])
                self.assertEqual(formal, state['plans'][plan_id]['adopted'])
                for key, value in latest.items():
                    self.assertEqual(value, state['configuration'][key])
                    self.assertEqual(value, restored[key])
                result = target.execute(state['revision'], 'prepare-solve', {'day': restored['day']}, restored_id)
                self.assert_baggage_tasks(result['plans'][restored_id], [45, 45, 30], rotating=True)

    def legacy_day(self):
        plan_id, _ = self.complete_plan()
        day = deepcopy(self.workspace.snapshot()['plans'][plan_id]['day'])
        day.pop('releasePolicy', None)
        day.pop('attendanceMode', None)
        for c in day['cells']:
            c.update(workStart=None, workEnd=None)
        for a in day['memberAvailability']:
            a.update(availableFrom=None, availableUntil=None)
        self.command('save-input', {'day': day}, plan_id)
        return plan_id, day

    def test_direct_old_draft_replan_without_any_role_or_person_times_and_cancel(self):
        plan_id, day = self.legacy_day()
        original = self.workspace.backup()['state']['plans'][plan_id]
        state = self.command('prepare-solve', {'day': day}, plan_id)
        new_id = state['selectedPlanId']
        p = state['plans'][new_id]
        self.assertEqual(plan_id, new_id)
        self.assertTrue(p['valid'], p['issues'])
        self.assertEqual('duty', p['day']['attendanceMode'])
        self.assertEqual('arrival', p['day']['roleConfigs'][0]['workMode'])
        self.assertEqual(original['adopted'], p['adopted'])
        broken = self.workspace.backup()
        broken['state']['plans'][new_id]['preview']['returnPlanId'] = 'unknown'
        before = self.workspace.backup()
        with self.assertRaisesRegex(WorkspaceError, '返回方案'):
            self.workspace.restore(state['revision'], broken)
        self.assertEqual(before, self.workspace.backup())
        state = self.command('cancel-preview', {}, new_id)
        self.assertEqual(plan_id, state['selectedPlanId'])
        self.assertEqual('duty', state['plans'][plan_id]['day']['attendanceMode'])
        self.assertEqual(original['adopted'], state['plans'][plan_id]['adopted'])

    def test_no_participants_one_actionable_error_and_no_mutation(self):
        plan_id, day = self.legacy_day()
        for a in day['memberAvailability']: a['participating'] = False
        before = self.workspace.backup()
        with self.assertRaisesRegex(WorkspaceError, '^请选择当天参加人员$'):
            self.command('prepare-solve', {'day': day}, plan_id)
        self.assertEqual(before, self.workspace.backup())

    def test_implicit_old_mode_uses_current_single_dedicated_switch_on_replan(self):
        plan_id, day = self.legacy_day()
        day['roleNames'] = ['督导']
        day['roleConfigs'] = [{'roleName':'督导','requiredCount':1,'concurrent':False}]
        for c in day['cells']: c['roleName'] = '督导'
        for m in day['memberConfigs']: m['eligibleRoleNames'] = ['督导']
        state = self.command('prepare-solve',{'day':day},plan_id)
        p = state['plans'][state['selectedPlanId']]
        self.assertEqual('continuous',p['day']['roleConfigs'][0]['workMode'])
        self.assertTrue(p['day']['roleConfigs'][0]['concurrent'])

    def test_obsolete_special_minutes_are_retained_for_history_but_not_required_on_replan(self):
        plan_id, day = self.legacy_day()
        day['roleConfigs'][0]['workMode'] = 'timed'
        state = self.command('prepare-solve', {'day': day}, plan_id)
        self.assertIn('flowPreferences', state['plans'][state['selectedPlanId']]['day'])
        self.assertTrue(state['plans'][state['selectedPlanId']]['valid'])
        day['cells'][0].update(workStart='23:05', workEnd='次日00:05')
        state = self.command('prepare-solve', {'day':day}, plan_id)
        p = state['plans'][state['selectedPlanId']]
        content = export_complete_configuration({'day':p['day'],'rotationEnabled':True,'rotationMinutes':60,'source':'special'})
        raw = import_complete_configuration(content)['day']
        self.assertEqual('timed',raw['roleConfigs'][0]['workMode'])
        state = self.command('prepare-solve', {'day':raw}, p['id'])
        self.assertEqual('timed',state['plans'][state['selectedPlanId']]['day']['roleConfigs'][0]['workMode'])

    def test_optional_restriction_excel_restart_and_independent_negative_validation(self):
        plan_id, day = self.legacy_day()
        state = self.command('prepare-solve', {'day': day}, plan_id)
        p = state['plans'][state['selectedPlanId']]
        parsed = imported_day_from_dict(p['day'])
        scenario, issues = imported_day_to_scenario(parsed)
        self.assertEqual([], issues)
        result = solve(scenario)
        self.assertEqual([], validate_schedule(scenario, result.assignments))
        assigned = result.assignments[0].member_id
        restricted = replace(scenario, members=tuple(replace(m, available_until=23*60) if m.id == assigned else m for m in scenario.members))
        self.assertTrue(validate_schedule(restricted, result.assignments))
        raw = deepcopy(p['day'])
        raw['memberAvailability'][0].update(availableFrom='22:00', availableUntil='次日02:00')
        content = export_complete_configuration({'day':raw,'rotationEnabled':True,'rotationMinutes':60,'source':'test'})
        restored = import_complete_configuration(content)['day']
        self.assertEqual('duty', restored['attendanceMode'])
        self.assertEqual(raw['memberAvailability'], restored['memberAvailability'])
        workbook = load_workbook(BytesIO(content))
        sheet = workbook['支援释放规则']
        sheet.delete_rows(next(row[0].row for row in sheet if row[0].value == '当天出勤'))
        broken = BytesIO(); workbook.save(broken)
        with self.assertRaisesRegex(ValueError, '当天出勤方式'):
            import_complete_configuration(broken.getvalue())
        half = deepcopy(raw); half['memberAvailability'][0]['availableUntil'] = None
        with self.assertRaisesRegex(WorkspaceError, '完整'):
            self.command('prepare-solve', {'day':half}, p['id'])
        rebuilt = Workbench(self.path.parent/'roundtrip.sqlite3')
        rebuilt.restore(0, self.workspace.backup())
        self.assertTrue(rebuilt.snapshot()['plans'][p['id']]['valid'])

    def test_old_bounded_contract_stays_strict_and_duty_restriction_limits_solver(self):
        plan_id, day = self.legacy_day()
        _, issues = imported_day_to_scenario(imported_day_from_dict(day))
        self.assertIn('incomplete_availability', {i.code for i in issues})
        state = self.command('prepare-solve', {'day':day}, plan_id)
        raw = deepcopy(state['plans'][state['selectedPlanId']]['day'])
        for a in raw['memberAvailability']:
            a.update(availableFrom='20:00', availableUntil='21:00')
        scenario, issues = imported_day_to_scenario(imported_day_from_dict(raw))
        self.assertEqual([],issues)
        self.assertEqual('INFEASIBLE',solve(scenario).status)

    def test_archive_duty_without_time_keeps_demand_and_formal_legacy(self):
        plan_id, _ = self.complete_plan()
        self.command('solve', {}, plan_id)
        formal = self.command('adopt', {}, plan_id)['plans'][plan_id]['adopted']
        day = deepcopy(formal['day']); day['serviceDate'] = '2026-08-04'
        for a in day['memberAvailability']: a.update(availableFrom=None, availableUntil=None)
        for c in day['cells']: c.update(workStart=None, workEnd=None)
        state = self.command('archive-next', {'day':day}, plan_id)
        p = state['plans'][state['selectedPlanId']]
        self.assertTrue(p['valid'],p['issues'])
        self.assertEqual(2,p['day']['roleConfigs'][0]['requiredCount'])
        self.assertEqual(formal,state['plans'][plan_id]['adopted'])
