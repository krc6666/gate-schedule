from pathlib import Path
import tempfile
import unittest

from gate_schedule.domain import Flight, Member, Scenario, Task, validate_balance_settings
from gate_schedule.io import scenario_to_dict
from gate_schedule.priority_statistics import priority_statistics
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule
from gate_schedule.workbench import Workbench
from gate_schedule.storage import LocalStateStore
from tests.test_balance_configuration import configuration


class PriorityFlightTests(unittest.TestCase):
    def test_same_role_on_two_flights_and_shared_segments(self):
        sc = Scenario('2026-08-03',
            (Flight('a', 'A100', '2026-08-03', 600, 700), Flight('b', 'B200', '2026-08-03', 720, 820)),
            (Member('m', '甲', frozenset({'督导'}), 0),),
            (Task('one', 'a', '督导', 600, 620, 1, 'mobile'),
             Task('shared', None, '督导', 620, 640, 1, 'mobile', covered_flight_ids=('a', 'b'))),
            release_policy='task')
        current = {'scenario': scenario_to_dict(sc), 'assignments': [
            {'taskId': 'one', 'memberId': 'm'}, {'taskId': 'shared', 'memberId': 'm'}]}
        state = {'revision': 0, 'people': [{'id': 'm', 'name': '甲', 'eligibleRoleNames': ['督导']}],
            'plans': {}, 'balanceSettings': {'priorityRoleNames': [], 'priorityFlightRoles': [
                {'flightNo': 'A100', 'roleName': '督导'}, {'flightNo': 'B200', 'roleName': '督导'}],
                'priorityAdjustments': [{'month': '2026-08', 'memberId': 'm', 'flightNo': 'A100', 'roleName': '督导', 'delta': 2}]}}
        report = priority_statistics(state, '2026-08', current=current)
        self.assertEqual(['A100', 'B200'], [g['flightNo'] for g in report['roles']])
        self.assertEqual([1, 1], [g['members'][0]['historyCount'] for g in report['roles']])
        self.assertEqual([3, 1], [g['members'][0]['effectiveCount'] for g in report['roles']])
        self.assertEqual(1, len(report['roles'][0]['members'][0]['dates']))
        scope = next(s for s in report['scopedTotals'] if s['flightNo'] == 'B200' and not s['roleName'])
        self.assertEqual(1, scope['totals'][0]['count'])
        state['balanceSettings']['priorityFlightRoles'].pop(0)
        report = priority_statistics(state, '2026-08', current=current)
        self.assertEqual(1, report['totals'][0]['count'])

    def test_solver_monthly_and_previous_rotation_are_isolated_by_flight(self):
        sc = Scenario('2026-08-03',
            (Flight('a', 'A100', '2026-08-03', 600, 700), Flight('b', 'B200', '2026-08-03', 720, 820)),
            (Member('m', '甲', frozenset({'督导'}), 0), Member('n', '乙', frozenset({'督导'}), 0)),
            (Task('one', 'a', '督导', 600, 620, 1, 'mobile'), Task('two', 'b', '督导', 720, 740, 1, 'mobile')),
            release_policy='task', priority_flight_roles=(('A100', '督导'), ('B200', '督导')))
        result = solve(sc, priority_counts={('m', 'A100', '督导'): 10, ('n', 'B200', '督导'): 10})
        self.assertFalse(validate_schedule(sc, result.assignments))
        self.assertEqual({'one': 'n', 'two': 'm'}, {a.task_id: a.member_id for a in result.assignments})
        result = solve(sc, previous_roles={('m', 'A100', '督导'), ('n', 'B200', '督导')})
        self.assertEqual({'one': 'n', 'two': 'm'}, {a.task_id: a.member_id for a in result.assignments})

    def test_invalid_combinations_rejected_and_scenario_roundtrip(self):
        settings = configuration()['balanceSettings']
        for pairs in [[{'flightNo': '', 'roleName': '登机主验'}],
                      [{'flightNo': 'A100', 'roleName': '未知'}],
                      [{'flightNo': 'A100', 'roleName': '登机主验'}]*2]:
            with self.subTest(pairs=pairs), self.assertRaises(ValueError):
                validate_balance_settings({**settings, 'priorityFlightRoles': pairs}, ['登机主验'])

    def test_new_excel_cannot_silently_drop_scope_and_old_import_keeps_new_scope(self):
        from io import BytesIO
        from openpyxl import load_workbook
        from gate_schedule.excel_io import export_complete_configuration, import_complete_configuration
        config = configuration()
        config['balanceSettings']['priorityFlightRoles'] = [{'flightNo': 'A100', 'roleName': '登机主验'}]
        content = export_complete_configuration(config)
        for missing in ['航班重点岗位', '排班平衡', '重点次数修正']:
            book = load_workbook(BytesIO(content))
            del book[missing]
            output = BytesIO()
            book.save(output)
            book.close()
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                import_complete_configuration(output.getvalue())
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp') as directory:
            wb = Workbench(Path(directory)/'state.sqlite3')
            state = wb.execute(0, 'import-complete-configuration', config)
            state = wb.execute(state['revision'], 'import-complete-configuration', configuration())
            self.assertEqual(config['balanceSettings']['priorityFlightRoles'], state['balanceSettings']['priorityFlightRoles'])

    def test_flight_correction_without_new_selection_field_exports_without_loss(self):
        from gate_schedule.excel_io import export_complete_configuration, import_complete_configuration
        config = configuration()
        config['balanceSettings']['priorityAdjustments'][0]['flightNo'] = 'A100'
        restored = import_complete_configuration(export_complete_configuration(config))
        self.assertEqual(config['balanceSettings']['priorityAdjustments'], restored['balanceSettings']['priorityAdjustments'])
        self.assertEqual([], restored['balanceSettings']['priorityFlightRoles'])

    def test_legacy_workspace_starts_unselected_and_preserves_history(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp') as directory:
            wb = Workbench(Path(directory)/'state.sqlite3')
            state = wb.execute(0, 'import-complete-configuration', configuration())
            self.assertEqual([], state['balanceSettings']['priorityFlightRoles'])
            self.assertEqual([], state['priorityStatistics']['roles'])
            self.assertEqual(2, len(state['balanceSettings']['priorityAdjustments']))
            self.assertEqual(2, len(state['priorityStatistics']['legacyAdjustments']))

            # Restore an actual pre-upgrade document: reading the new projection
            # must neither rewrite old plans nor silently select all their flights.
            old = wb.backup()['state']
            old['balanceSettings'].pop('priorityFlightRoles')
            for plan in old['plans'].values():
                plan['balanceSettings'].pop('priorityFlightRoles', None)
            LocalStateStore(wb.store.database_path).compare_and_save(Workbench.KEY, old['revision'], old)
            upgraded = Workbench(wb.store.database_path).snapshot()
            self.assertEqual([], upgraded['balanceSettings']['priorityFlightRoles'])
            self.assertEqual(old['plans'], wb.backup()['state']['plans'])

    def test_missing_or_other_flight_correction_rejects_atomically(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp') as directory:
            wb = Workbench(Path(directory)/'state.sqlite3')
            config = configuration()
            config['balanceSettings']['priorityFlightRoles'] = [{'flightNo': 'A100', 'roleName': '登机主验'}]
            state = wb.execute(0, 'import-complete-configuration', config)
            correction = {'month': '2026-08', 'memberId': 'stable-a', 'roleName': '登机主验', 'delta': 1}
            before = wb.backup()
            for payload in [correction, {**correction, 'flightNo': 'B200'}]:
                with self.subTest(payload=payload), self.assertRaises(ValueError):
                    wb.execute(state['revision'], 'adjust-priority-count', payload)
                self.assertEqual(before, wb.backup())

    def test_combo_settings_corrections_export_import_restart_and_solve(self):
        from gate_schedule.excel_io import import_complete_configuration
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp') as directory:
            wb = Workbench(Path(directory)/'source.sqlite3')
            config = configuration()
            config['balanceSettings'].update(priorityRoleNames=[], priorityFlightRoles=[
                {'flightNo': 'A100', 'roleName': '登机主验'}, {'flightNo': 'B200', 'roleName': '登机主验'}],
                priorityAdjustments=[], flightCountGap=None)
            state = wb.execute(0, 'import-complete-configuration', config)
            person = state['people'][0]['id']
            state = wb.execute(state['revision'], 'adjust-priority-count', {
                'month': '2026-08', 'memberId': person, 'flightNo': 'A100', 'roleName': '登机主验', 'delta': 1})
            self.assertEqual([1, 0], [g['members'][0]['effectiveCount'] for g in state['priorityStatistics']['roles']])
            for role in state['configuration']['roleConfigs']:
                if role['roleName'] != '登机主验':
                    state = wb.execute(state['revision'], 'save-role', {'roleName': role['roleName'], 'requiredCount': 0})
            restored = Workbench(Path(directory)/'target.sqlite3')
            exported = import_complete_configuration(wb.export_complete_configuration())
            restored.execute(0, 'import-complete-configuration', exported)
            restored = Workbench(Path(directory)/'target.sqlite3')
            self.assertEqual(state['balanceSettings'], restored.snapshot()['balanceSettings'])
            state = restored.snapshot()
            state = restored.execute(state['revision'], 'solve', {}, state['selectedPlanId'])
            schedule = state['plans'][state['selectedPlanId']]['schedule']
            flight_id = next(f['id'] for f in schedule['flights'] if f['flightNo'] == 'A100')
            task = next(t for t in schedule['tasks'] if t['flightId'] == flight_id)
            self.assertNotEqual(person, next(a['memberId'] for a in schedule['assignments'] if a['taskId'] == task['id']))
