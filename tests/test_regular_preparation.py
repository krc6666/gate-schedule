from copy import deepcopy
from dataclasses import replace
from io import BytesIO
import unittest
from openpyxl import load_workbook

import test_workspace as fixtures
from gate_schedule.domain import Assignment
from gate_schedule.excel_io import imported_day_from_dict, imported_day_to_dict, export_complete_configuration, import_complete_configuration
from gate_schedule.imported_schedule import imported_day_to_scenario
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule
from gate_schedule.workbench import Workbench, WorkspaceError


class RegularPreparationTests(unittest.TestCase):
    setUp = fixtures.WorkspaceTests.setUp
    tearDown = fixtures.WorkspaceTests.tearDown
    command = fixtures.WorkspaceTests.command
    complete_plan = fixtures.WorkspaceTests.complete_plan

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
        self.assertNotEqual(plan_id, new_id)
        self.assertTrue(p['valid'], p['issues'])
        self.assertEqual('duty', p['day']['attendanceMode'])
        self.assertEqual('arrival', p['day']['roleConfigs'][0]['workMode'])
        self.assertEqual(original, self.workspace.backup()['state']['plans'][plan_id])
        broken = self.workspace.backup()
        broken['state']['plans'][new_id]['preview']['returnPlanId'] = 'unknown'
        before = self.workspace.backup()
        with self.assertRaisesRegex(WorkspaceError, '返回方案'):
            self.workspace.restore(state['revision'], broken)
        self.assertEqual(before, self.workspace.backup())
        state = self.command('cancel-preview', {}, new_id)
        self.assertEqual(plan_id, state['selectedPlanId'])
        self.assertEqual(original, self.workspace.backup()['state']['plans'][plan_id])

    def test_no_participants_one_actionable_error_and_no_mutation(self):
        plan_id, day = self.legacy_day()
        for a in day['memberAvailability']: a['participating'] = False
        before = self.workspace.backup()
        with self.assertRaisesRegex(WorkspaceError, '^请选择当天参加人员$'):
            self.command('prepare-solve', {'day': day}, plan_id)
        self.assertEqual(before, self.workspace.backup())

    def test_implicit_old_mode_keeps_explicit_supervisor_nature(self):
        plan_id, day = self.legacy_day()
        day['roleNames'] = ['督导']
        day['roleConfigs'] = [{'roleName':'督导','requiredCount':1,'concurrent':False}]
        for c in day['cells']: c['roleName'] = '督导'
        for m in day['memberConfigs']: m['eligibleRoleNames'] = ['督导']
        state = self.command('prepare-solve',{'day':day},plan_id)
        p = state['plans'][state['selectedPlanId']]
        self.assertEqual('continuous',p['day']['roleConfigs'][0]['workMode'])
        self.assertFalse(p['day']['roleConfigs'][0]['concurrent'])

    def test_explicit_special_mode_is_preserved_and_requires_its_time(self):
        plan_id, day = self.legacy_day()
        day['roleConfigs'][0]['workMode'] = 'timed'
        with self.assertRaisesRegex(WorkspaceError, '岗位起止时间'):
            self.command('prepare-solve', {'day': day}, plan_id)
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
