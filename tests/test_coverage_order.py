from copy import deepcopy
from dataclasses import replace
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from openpyxl import load_workbook

from gate_schedule.domain import Assignment
from gate_schedule.excel_io import export_complete_configuration, import_complete_configuration
from gate_schedule.group_workspaces import GroupWorkspaces
from gate_schedule.io import scenario_from_dict, scenario_to_dict
from gate_schedule.manual_editing import candidate_options, issue_data
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule
from gate_schedule.workbench import Workbench
from tests.test_coverage_priorities import competing, maintained_configuration


ROOT = Path(__file__).resolve().parents[1]
ORDER = ['登机核验', '秩序维护']


def scene(order=ORDER):
    original = competing()
    return replace(original, coverage_priorities=(), coverage_role_order=tuple(order),
                   tasks=(original.tasks[0], replace(original.tasks[1], end=720)))


def configured(order=None):
    data = maintained_configuration()
    data['balanceSettings']['coveragePriorities'] = []
    data['balanceSettings']['coverageRoleOrder'] = order or ['登机主验', '秩序维护']
    return data


def warnings(scenario, assignments):
    return [i for i in validate_schedule(scenario, assignments) if i.code == 'coverage_order_unmet']


class CoverageOrderTests(unittest.TestCase):
    def test_reversing_order_changes_shortage_choice_in_every_tier(self):
        for tier in ('protect', 'normal', 'leave-empty'):
            for order, expected in ((ORDER, 'high'), (ORDER[::-1], 'low1')):
                with self.subTest(tier=tier, order=order):
                    s = replace(scene(order), coverage_priorities=tuple((r, tier) for r in ORDER))
                    result = solve(s, allow_partial=True)
                    self.assertEqual('OPTIMAL', result.status)
                    self.assertEqual({expected}, {a.task_id for a in result.assignments})
                    self.assertFalse(warnings(s, result.assignments))

    def test_existing_tiers_and_coverage_count_remain_ahead_of_order(self):
        s = replace(scene(ORDER[::-1]), coverage_priorities=(('登机核验', 'protect'),))
        self.assertEqual({'high'}, {a.task_id for a in solve(s, allow_partial=True).assignments})
        s = replace(competing(), coverage_priorities=(), coverage_role_order=tuple(ORDER))
        self.assertEqual({'low1', 'low2'}, {a.task_id for a in solve(s, allow_partial=True).assignments})
        self.assertFalse(warnings(s, (Assignment('low1', 'm'), Assignment('low2', 'm'))))

    def test_unordered_legacy_round_trip_and_behavior_are_unchanged(self):
        raw = scenario_to_dict(competing())
        self.assertNotIn('coverageRoleOrder', raw)
        restored = scenario_from_dict(raw)
        self.assertEqual((), restored.coverage_role_order)
        self.assertEqual(raw, scenario_to_dict(restored))
        self.assertEqual(solve(competing(), allow_partial=True), solve(restored, allow_partial=True))

    def test_new_scenario_round_trip_and_bad_order_rejection(self):
        raw = scenario_to_dict(scene())
        self.assertEqual(ORDER, raw['coverageRoleOrder'])
        self.assertEqual(scene(), scenario_from_dict(raw))
        for bad in (None, '登机核验', [1], [''], [' 登机核验'], ORDER * 2):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                scenario_from_dict({**raw, 'coverageRoleOrder': bad})

    def test_earlier_flight_and_dedicated_coverage_still_win(self):
        s = scene()
        earlier = replace(s.flights[0], id='B', flight_no='B200', window_end=660)
        s = replace(s, flights=s.flights + (earlier,), tasks=(s.tasks[0], replace(s.tasks[1], flight_id='B', end=660)))
        values = solve(s, allow_partial=True).assignments
        self.assertEqual({'low1'}, {a.task_id for a in values})
        self.assertFalse(warnings(s, values))
        s = replace(scene(), tasks=(scene().tasks[0], replace(scene().tasks[1], exclusive_same_flight=True)))
        values = solve(s, allow_partial=True).assignments
        self.assertEqual({'low1'}, {a.task_id for a in values})
        self.assertFalse(warnings(s, values))

    def test_half_rest_conflict_rotation_and_shared_tasks_remain_hard(self):
        from gate_schedule.domain import Rotation
        s = scene()
        # The preferred morning role is forbidden for a late-start member.
        s = replace(s, flights=(replace(s.flights[0], window_end=780),),
            members=(replace(s.members[0], half_rest='late-start'),),
            tasks=(replace(s.tasks[0], end=660), replace(s.tasks[1], start=720, end=780)))
        values = solve(s, allow_partial=True).assignments
        self.assertEqual({'low1'}, {a.task_id for a in values})
        self.assertFalse(warnings(s, values))
        self.assertIn('half_rest_period', {i.code for i in validate_schedule(s, values + (Assignment('high', 'm'),))})
        s = replace(scene(), tasks=(replace(scene().tasks[0], task_kind='rotation'), scene().tasks[1]),
            rotations=(Rotation('high', 'pool', 600, 720, 1, ()),))
        values = solve(s, allow_partial=True).assignments
        self.assertEqual({'low1'}, {a.task_id for a in values})
        self.assertFalse(warnings(s, values))
        self.assertIn('rotation_candidate', {i.code for i in validate_schedule(s, values + (Assignment('high', 'm'),))})
        shared = replace(scene(), tasks=(replace(scene().tasks[0], covered_flight_ids=('A',)), scene().tasks[1]))
        values = solve(shared, allow_partial=True).assignments
        self.assertEqual(1, len(values), '共享任务只计一次')
        self.assertIn('overlap', {i.code for i in validate_schedule(shared, values + (Assignment('low1', 'm'),))})

    def test_manual_reverse_is_warning_and_candidate_remains_available(self):
        s = scene()
        values = (Assignment('low1', 'm'),)
        hints = warnings(s, values)
        self.assertEqual(1, len(hints))
        self.assertEqual('warning', hints[0].severity)
        projected = issue_data(s, hints)[0]
        self.assertEqual('warning', projected['severity'])
        self.assertIn('顺序偏好未满足', projected['message'])
        options = candidate_options(s, (), 'low1')
        self.assertTrue(options[0]['available'], '软顺序不能阻止人工选人')

    def test_hard_limits_suppress_unactionable_warning_and_still_reject_tampering(self):
        s = scene()
        variants = [
            (replace(s, members=(replace(s.members[0], eligible_role_ids=frozenset({'秩序维护'})),)), 'ineligible'),
            (replace(s, tasks=(replace(s.tasks[0], locked_member_ids=()), replace(s.tasks[1], locked_member_ids=('m',)))), 'lock_missing'),
            (replace(s, tasks=(replace(s.tasks[0], start=660), s.tasks[1]), members=(replace(s.members[0], available_until=650),)), 'member_unavailable'),
        ]
        for case, expected in variants:
            with self.subTest(expected=expected):
                # A fixed lower role is mandatory even when the preferred role could otherwise be staffed.
                if expected == 'lock_missing':
                    values = solve(case, allow_partial=True).assignments
                    self.assertEqual({'low1'}, {a.task_id for a in values})
                    self.assertFalse(warnings(case, values))
                self.assertIn(expected, {i.code for i in validate_schedule(case, (Assignment('high', 'm'),))})
        unqualified = variants[0][0]
        self.assertFalse(warnings(unqualified, (Assignment('low1', 'm'),)))
        exclusive = replace(s, tasks=tuple(replace(t, exclusive_same_flight=True) for t in s.tasks))
        legal = solve(exclusive, allow_partial=True).assignments
        self.assertIn('exclusive_same_flight', {i.code for i in validate_schedule(exclusive, legal + (Assignment('low1', 'm'),))})


class CoverageOrderMigrationTests(unittest.TestCase):
    def test_config_excel_restart_solve_manual_archive_export_restore_and_legacy(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='coverage-order-') as directory:
            work = Workbench(Path(directory) / 'source.db')
            data = configured()
            state = work.execute(work.snapshot()['revision'], 'import-complete-configuration', data)
            for role in state['configuration']['roleConfigs']:
                if role['roleName'] not in data['day']['roleNames']:
                    state = work.execute(state['revision'], 'save-role', {'roleName': role['roleName'], 'requiredCount': 0})
            # Exercise the rule save command, rather than relying on imported settings alone.
            settings = {**state['balanceSettings'], 'coverageRoleOrder': ['秩序维护', '登机主验']}
            state = work.execute(state['revision'], 'update-balance-settings', {'balanceSettings': settings})
            decoded = import_complete_configuration(work.export_complete_configuration())
            self.assertEqual(settings['coverageRoleOrder'], decoded['balanceSettings']['coverageRoleOrder'])
            target = Workbench(Path(directory) / 'target.db')
            target.execute(target.snapshot()['revision'], 'import-complete-configuration', decoded)
            target = Workbench(Path(directory) / 'target.db')
            state = target.snapshot()
            self.assertEqual(settings['coverageRoleOrder'], state['balanceSettings']['coverageRoleOrder'])
            state = target.execute(state['revision'], 'solve', {}, state['selectedPlanId'])
            plan_id = state['selectedPlanId']
            plan = state['plans'][plan_id]
            tasks = {t['id']: t for t in plan['schedule']['tasks']}
            self.assertEqual({'秩序维护'}, {tasks[a['taskId']]['roleId'] for a in plan['draft']})
            # Keep a legal manual reversal, then archive its explanation rather than blocking it.
            manual = [{'taskId': task['id'], 'memberId': plan['draft'][0]['memberId']}
                      for task in tasks.values() if task['roleId'] == '登机主验']
            state = target.execute(state['revision'], 'assign', {'assignments': manual}, plan_id)
            hints = [i for i in state['plans'][plan_id]['issues'] if i['code'] == 'coverage_order_unmet']
            self.assertTrue(hints)
            self.assertTrue(all(i['severity'] == 'warning' for i in hints))
            state = target.execute(state['revision'], 'adopt', {}, plan_id)
            formal = deepcopy(target.backup()['state']['plans'][plan_id]['adopted'])
            self.assertEqual(settings['coverageRoleOrder'], formal['scenario']['coverageRoleOrder'])
            book = load_workbook(BytesIO(target.export(plan_id)))
            self.assertIn('coverage_order_unmet', str(list(book['排班问题'].values)))
            self.assertEqual(hints, [i for i in formal['issues'] if i['code'] == 'coverage_order_unmet'])
            legacy = configured()
            del legacy['balanceSettings']['coverageRoleOrder']
            legacy = import_complete_configuration(export_complete_configuration(legacy))
            state = target.execute(state['revision'], 'import-complete-configuration', legacy)
            self.assertEqual(settings['coverageRoleOrder'], state['balanceSettings']['coverageRoleOrder'])
            self.assertEqual(formal, target.backup()['state']['plans'][plan_id]['adopted'])
            payload = {k: v for k, v in state['balanceSettings'].items() if k != 'coverageRoleOrder'}
            state = target.execute(state['revision'], 'update-balance-settings', {'balanceSettings': payload})
            self.assertEqual(settings['coverageRoleOrder'], state['balanceSettings']['coverageRoleOrder'])
            backup = target.backup()
            empty = Workbench(Path(directory) / 'restore.db')
            empty.restore(0, backup)
            restored = empty.backup()
            restored['state']['revision'] = backup['state']['revision']
            for document in (backup, restored):
                for saved_plan in document['state']['plans'].values():
                    saved_plan.pop('undoEdit', None)  # Restore deliberately expires transient undo receipts.
            self.assertEqual(backup, restored)
            cleared = configured()
            cleared['balanceSettings']['coverageRoleOrder'] = []
            state = target.execute(state['revision'], 'import-complete-configuration',
                import_complete_configuration(export_complete_configuration(cleared)))
            self.assertEqual([], state['balanceSettings']['coverageRoleOrder'])
            self.assertEqual(formal, target.backup()['state']['plans'][plan_id]['adopted'])

    def test_bad_configuration_order_is_atomic_and_cannot_overwrite_history(self):
        from gate_schedule.workbench import WorkspaceError
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='coverage-order-atomic-') as directory:
            work = Workbench(Path(directory) / 'state.db')
            state = work.execute(work.snapshot()['revision'], 'import-complete-configuration', configured())
            original = work.backup()
            for value in (['未知岗'], ['登机主验', '登机主验'], None):
                settings = {**state['balanceSettings'], 'coverageRoleOrder': value}
                with self.subTest(value=value), self.assertRaises((ValueError, WorkspaceError)):
                    work.execute(state['revision'], 'update-balance-settings', {'balanceSettings': settings})
                self.assertEqual(original, work.backup())

    def test_excel_missing_field_preserves_explicit_empty_clears_and_bad_table_rejects(self):
        data = configured()
        content = export_complete_configuration(data)
        book = load_workbook(BytesIO(content))
        self.assertIn('同档保岗顺序', book.sheetnames)
        for bad in ('missing-sheet', 'duplicate-role', 'bad-position', 'unknown-role'):
            broken = load_workbook(BytesIO(content))
            if bad == 'missing-sheet':
                del broken['同档保岗顺序']
            elif bad == 'duplicate-role':
                broken['同档保岗顺序'].append([3, '登机主验'])
            elif bad == 'bad-position':
                broken['同档保岗顺序']['A2'] = 0
            else:
                broken['同档保岗顺序']['B2'] = '未知岗'
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                import_complete_configuration(self._bytes(broken))
        empty = configured()
        empty['balanceSettings']['coverageRoleOrder'] = []
        self.assertEqual([], import_complete_configuration(export_complete_configuration(empty))['balanceSettings']['coverageRoleOrder'])
        legacy = configured()
        del legacy['balanceSettings']['coverageRoleOrder']
        self.assertNotIn('coverageRoleOrder', import_complete_configuration(export_complete_configuration(legacy))['balanceSettings'])

    @staticmethod
    def _bytes(book):
        output = BytesIO()
        book.save(output)
        return output.getvalue()

    def test_a_b_opposite_orders_survive_complete_excel_and_restart(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='coverage-order-groups-') as directory:
            groups = GroupWorkspaces(Path(directory) / 'source.db')
            groups.initialize()
            for group, order in [('A', ['登机主验', '秩序维护']), ('B', ['秩序维护', '登机主验'])]:
                work = groups.workbench(group)
                state = work.execute(work.snapshot()['revision'], 'import-complete-configuration', configured(order))
                for role in state['configuration']['roleConfigs']:
                    if role['roleName'] not in configured()['day']['roleNames']:
                        work.execute(work.snapshot()['revision'], 'save-role', {'roleName': role['roleName'], 'requiredCount': 0})
            incoming = import_complete_configuration(groups.export_configuration('A'))
            target = GroupWorkspaces(Path(directory) / 'target.db')
            target.initialize()
            preview = target.preview_configuration('A', incoming)
            target.import_configuration('A', preview['expectedRevision'], preview['payload'])
            target = GroupWorkspaces(Path(directory) / 'target.db')
            for group, expected in [('A', '登机主验'), ('B', '秩序维护')]:
                work = target.workbench(group)
                state = work.snapshot()
                self.assertEqual(expected, state['balanceSettings']['coverageRoleOrder'][0])
                state = work.execute(state['revision'], 'solve', {}, state['selectedPlanId'])
                plan = state['plans'][state['selectedPlanId']]
                tasks = {t['id']: t for t in plan['schedule']['tasks']}
                self.assertEqual({expected}, {tasks[a['taskId']]['roleId'] for a in plan['draft']})
