from copy import deepcopy
from dataclasses import replace
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from openpyxl import load_workbook

from gate_schedule.domain import Assignment, preferred_handoff, handoff_rank, validate_handoff_preferences
from gate_schedule.excel_io import export_complete_configuration, import_complete_configuration
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule
from gate_schedule.workbench import Workbench
from gate_schedule.group_workspaces import GroupWorkspaces
from tests.test_handoff_preferences import scenario, preference


def configuration(preferences):
    sc = scenario()
    day = {'serviceDate': sc.service_date, 'title': '通用与航班特例',
           'roleNames': [t.role_id for t in sc.tasks],
           'flights': [{'id': 'f', 'flightNo': 'TW616', 'windowStart': '10:00', 'windowEnd': '11:20'}],
           'roleConfigs': [{'roleName': t.role_id, 'requiredCount': 1, 'workMode': t.service_stage, 'concurrent': False} for t in sc.tasks],
           'roster': [m.name for m in sc.members],
           'memberConfigs': [{'memberName': m.name, 'eligibleRoleNames': sorted(m.eligible_role_ids)} for m in sc.members],
           'memberAvailability': [{'memberName': m.name, 'participating': True, 'availableFrom': None, 'availableUntil': None} for m in sc.members],
           'remarks': [], 'remarksReviewed': True, 'extraOccupancies': [], 'transitionMinutes': 0,
           'releasePolicy': 'task', 'attendanceMode': 'duty', 'phaseHandoffs': [],
           'cells': [{'id': t.id, 'flightId': 'f', 'flightNo': 'TW616', 'roleName': t.role_id, 'rawValue': '',
                      'internalNames': [], 'externalLabels': [], 'semantics': 'empty',
                      'fixedMemberNames': list(t.locked_member_ids), 'requiredCountOverride': None, 'workStart': None, 'workEnd': None} for t in sc.tasks]}
    return {'day': day, 'source': '隔离验收', 'rotationEnabled': False, 'rotationMinutes': 60,
            'handoffPreferences': preferences}


class HandoffExceptionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp')
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)/'state.sqlite3'
        self.wb = Workbench(self.path)
        self.common = dict(preference(targets=['登机核验', '登机主验']), ordered=True)
        self.exception = dict(preference(flight='TW616', targets=['登机主验']), ordered=True)

    def test_specific_replaces_common_independent_of_order_and_other_source_inherits(self):
        for prefs in ([self.common, self.exception], [self.exception, self.common]):
            self.assertTrue(preferred_handoff(prefs, 'TW616', '接机2', '登机主验'))
            self.assertFalse(preferred_handoff(prefs, 'TW616', '接机2', '登机核验'))
            self.assertEqual(0, handoff_rank(prefs, 'TW616', '接机2', '登机主验'))
            self.assertEqual(1, handoff_rank(prefs, 'NEW123', '接机2', '登机主验'))
        other = preference(source='接机1', targets=['登机核验'])
        self.assertTrue(preferred_handoff([self.common, self.exception, other], 'TW616', '接机1', '登机核验'))
        disabled = dict(self.exception, enabled=False, boardingRoles=[])
        validate_handoff_preferences([self.common, disabled])
        self.assertFalse(any(preferred_handoff([self.common, disabled], 'TW616', '接机2', r) for r in self.common['boardingRoles']))
        self.assertTrue(preferred_handoff([self.common, disabled], 'OTHER', '接机2', '登机核验'))

    def test_actual_solver_specific_fallback_and_hard_rules_still_apply(self):
        for flight, prefs, expected in [('TW616', [self.common, self.exception], '2'),
                                       ('NEW123', [self.common, self.exception], '3'),
                                       ('TW616', [self.common], '3')]:
            sc = scenario(); sc = replace(sc, flights=(replace(sc.flights[0], flight_no=flight),))
            result = solve(sc, handoff_preferences=prefs)
            self.assertFalse(validate_schedule(sc, result.assignments))
            self.assertEqual(5, len(result.assignments))
            self.assertIn(Assignment(expected, '乙'), result.assignments)
        sc = scenario()
        sc = replace(sc, members=tuple(replace(m, eligible_role_ids=m.eligible_role_ids-{'登机主验'}) if m.id=='乙' else m for m in sc.members))
        result = solve(sc, handoff_preferences=[self.common, self.exception], allow_partial=True)
        self.assertEqual(5, len(result.assignments))
        self.assertFalse(validate_schedule(sc, result.assignments))
        broken = tuple(a for a in result.assignments if a.task_id!='2')+(Assignment('2', '乙'),)
        self.assertIn('ineligible', {i.code for i in validate_schedule(sc, broken)})

    def test_excel_empty_device_restart_use_and_backup_preserve_scopes_and_disabled(self):
        disabled = dict(preference(flight='TW616', source='接机1'), enabled=False, boardingRoles=[])
        prefs = [self.common, self.exception, disabled]
        raw = export_complete_configuration(configuration(prefs))
        book = load_workbook(BytesIO(raw))
        self.assertEqual(['通用', 'TW616', 'TW616'], [row[0].value for row in list(book['岗位接续偏好'])[1:]])
        book.close()
        data = import_complete_configuration(raw)
        self.assertEqual(prefs, data['handoffPreferences'])
        state = self.wb.execute(0, 'import-complete-configuration', data)
        pid = state['selectedPlanId']
        original = deepcopy(self.wb.backup()['state']['plans'])
        self.wb = Workbench(self.path)
        self.assertEqual(prefs, self.wb.snapshot()['handoffPreferences'])
        state = self.wb.execute(state['revision'], 'solve', {}, pid)
        self.assertTrue(state['plans'][pid]['valid'], state['plans'][pid]['issues'])
        member = next(p['id'] for p in state['people'] if p['name']=='乙')
        main = next(t['id'] for t in state['plans'][pid]['schedule']['tasks'] if t['roleId']=='登机主验')
        self.assertIn({'taskId':main,'memberId':member}, state['plans'][pid]['draft'])
        adopted = self.wb.execute(state['revision'], 'adopt', {}, pid)['plans'][pid]['adopted']
        before = self.wb.snapshot()
        self.wb.execute(before['revision'], 'save-handoff-preferences', {'preferences':[self.common]})
        self.assertEqual(adopted, self.wb.snapshot()['plans'][pid]['adopted'])
        target = Workbench(Path(self.directory.name)/'restored.sqlite3')
        restored = target.restore(0, self.wb.backup())
        self.assertEqual([self.common], restored['handoffPreferences'])
        self.assertEqual(adopted, restored['plans'][pid]['adopted'])
        self.assertEqual([], original[pid]['draft'])

    def test_saving_and_deleting_exceptions_is_atomic_and_other_group_unchanged(self):
        state = self.wb.execute(0,'import-complete-configuration',configuration([self.common]))
        other = GroupWorkspaces(self.path).workbench('B').backup()
        pid = state['selectedPlanId']; plans = deepcopy(self.wb.backup()['state']['plans'])
        state = self.wb.execute(state['revision'],'save-handoff-preferences',{'preferences':[self.common,self.exception]})
        self.assertEqual([self.common,self.exception], state['handoffPreferences'])
        self.assertEqual(plans,self.wb.backup()['state']['plans'])
        before = self.wb.backup()
        with self.assertRaises(ValueError):
            self.wb.execute(state['revision'],'save-handoff-preferences',{'preferences':[self.common,self.exception,self.exception]})
        self.assertEqual(before,self.wb.backup())
        state = self.wb.execute(state['revision'],'save-handoff-preferences',{'preferences':[self.common]})
        self.assertTrue(preferred_handoff(state['handoffPreferences'],'TW616','接机2','登机核验'))
        self.assertEqual(other, GroupWorkspaces(self.path).workbench('B').backup())
        self.assertEqual(pid, state['selectedPlanId'])

    def test_exception_for_historical_temporary_flight_exports_and_restarts(self):
        state = self.wb.execute(0, 'import-complete-configuration', configuration([self.common]))
        backup = self.wb.backup()
        temporary = deepcopy(backup['state']['plans'][state['selectedPlanId']])
        temporary['id'] = 'historical-temporary'
        temporary['day']['serviceDate'] = '2026-08-01'
        temporary['day']['flights'][0]['flightNo'] = 'TEMP777'
        for cell in temporary['day']['cells']:
            cell['flightNo'] = 'TEMP777'
        backup['state']['plans'][temporary['id']] = temporary
        state = self.wb.restore(state['revision'], backup)
        prefs = [self.common, dict(self.exception, flightNo='TEMP777')]
        state = self.wb.execute(state['revision'], 'save-handoff-preferences', {'preferences':prefs})
        raw = self.wb.export_complete_configuration(state['selectedPlanId'])
        data = import_complete_configuration(raw)
        target = Workbench(Path(self.directory.name)/'temporary-device.sqlite3')
        target.execute(0, 'import-complete-configuration', data)
        self.assertEqual(prefs, Workbench(Path(self.directory.name)/'temporary-device.sqlite3').snapshot()['handoffPreferences'])
        cleared = self.wb.execute(state['revision'], 'clear-history', {})
        self.assertEqual(prefs, Workbench(self.path).snapshot()['handoffPreferences'])
        self.assertNotIn('historical-temporary', cleared['plans'])
        # Clearing old plans must not invalidate an independent saved preference.
        raw = self.wb.export_complete_configuration(cleared['selectedPlanId'])
        self.assertEqual(prefs, import_complete_configuration(raw)['handoffPreferences'])

    def test_old_flights_and_roles_are_snapshots_and_clear_keeps_current_configuration(self):
        old = configuration([self.common])
        def rename(value, before, after):
            if isinstance(value, dict):
                return {key: rename(item, before, after) for key, item in value.items()}
            if isinstance(value, list):
                return [rename(item, before, after) for item in value]
            return after if value == before else value
        old = rename(rename(old, 'TW616', 'OLD777'), '登机主验', '旧主验岗')
        state = self.wb.execute(0, 'import-complete-configuration', old)
        pid = state['selectedPlanId']
        state = self.wb.execute(state['revision'], 'solve', {}, pid)
        self.assertTrue(state['plans'][pid]['valid'])
        state = self.wb.execute(state['revision'], 'adopt', {}, pid)
        original = deepcopy(self.wb.backup()['state']['plans'][pid])
        current = configuration([dict(self.common, boardingRoles=['新主验岗'])])
        current = rename(rename(current, 'TW616', 'NEW888'), '登机主验', '新主验岗')
        current['day']['serviceDate'] = '2026-08-04'
        state = self.wb.execute(state['revision'], 'import-complete-configuration', current)
        selected = state['selectedPlanId']
        state = self.wb.execute(state['revision'], 'solve', {}, selected)
        self.assertTrue(state['plans'][selected]['valid'], state['plans'][selected]['issues'])
        self.assertEqual(original, self.wb.backup()['state']['plans'][pid])
        self.assertIn('旧主验岗', state['plans'][pid]['adopted']['day']['roleNames'])
        raw = self.wb.export_complete_configuration(selected)
        target = Workbench(Path(self.directory.name)/'old-new-device.sqlite3')
        target.execute(0, 'import-complete-configuration', import_complete_configuration(raw))
        self.assertEqual(original['adopted'], target.snapshot()['plans'][pid]['adopted'])
        before = self.wb.backup()['state']
        state = self.wb.execute(state['revision'], 'clear-history', {})
        after = self.wb.backup()['state']
        for key in ('configuration', 'flightLibrary', 'handoffPreferences', 'balanceSettings', 'dutyRoster', 'people', 'weeklyFlightPresets'):
            self.assertEqual(before.get(key), after.get(key), key)
        self.assertEqual(before['plans'][selected]['day'], after['plans'][selected]['day'])
        self.assertEqual([], state['historyPlanIds'])
