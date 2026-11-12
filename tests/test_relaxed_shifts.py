from copy import deepcopy
from dataclasses import replace
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from gate_schedule.domain import (Assignment, Flight, Member, Task, Scenario,
    default_relaxed_shift_settings, relaxed_shift_facts, validate_relaxed_shift_settings)
from gate_schedule.excel_io import import_complete_configuration
from gate_schedule.server import create_server_app
from gate_schedule.workbench import Workbench
from gate_schedule.relaxed_shift_statistics import relaxed_shift_statistics
from gate_schedule.relaxed_shift_statistics import relaxed_counts_before
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule
from tests.test_balance_workflow import small_configuration


class RelaxedShiftTests(unittest.TestCase):
    def test_automatic_rest_opportunities_rotate_across_formal_days(self):
        _, wb = self.workspace()
        config = small_configuration(None)
        day = config['day']
        day['flights'][0].update(windowStart='09:00', windowEnd='10:00')
        day['flights'][1].update(windowStart='14:00', windowEnd='15:00')
        day['cells'][0]['requiredCountOverride'] = 2
        state = self.command(wb, 'import-complete-configuration', config)
        pid = state['selectedPlanId']
        # An already unfair month: 甲 got both earlier rest opportunities.
        self.command(wb, 'edit-cell', {'cellId': 'c0', 'text': '甲 / 乙'}, pid)
        self.command(wb, 'edit-cell', {'cellId': 'c1', 'text': '乙'}, pid)
        self.command(wb, 'adopt', pid=pid)
        for target in ('2026-08-04', '2026-08-06'):
            next_day = deepcopy(wb.backup()['state']['plans'][pid]['day'])
            next_day['serviceDate'] = target
            state = self.command(wb, 'archive-next', {'day': next_day}, pid)
            pid = state['selectedPlanId']
            if target.endswith('04'):
                # Deliberately repeat the original unfair manual arrangement.
                self.command(wb, 'edit-cell', {'cellId': 'c0', 'text': '甲 / 乙'}, pid)
                self.command(wb, 'edit-cell', {'cellId': 'c1', 'text': '乙'}, pid)
        summary = wb.snapshot()['relaxedShiftSummary']
        for key in ('earlyDepartures', 'afternoonRest'):
            self.assertEqual(['乙'], [p['memberName'] for p in summary[key]], key)
        self.assertTrue(state['plans'][pid]['valid'])
        # Re-solving a day must not add its own draft/formal to the baseline.
        baseline = relaxed_counts_before(wb.backup()['state'], '2026-08-06')
        self.command(wb, 'adopt', pid=pid)
        self.assertEqual(baseline, relaxed_counts_before(wb.backup()['state'], '2026-08-06'))
        for target in ('2026-08-08', '2026-08-10', '2026-08-12', '2026-08-14'):
            next_day = deepcopy(wb.backup()['state']['plans'][pid]['day'])
            next_day['serviceDate'] = target
            state = self.command(wb, 'archive-next', {'day': next_day}, pid)
            pid = state['selectedPlanId']
            self.assertTrue(state['plans'][pid]['valid'])
            for key in ('earlyDepartureCount', 'afternoonRestCount'):
                values = [r[key] for r in wb.relaxed_shift_statistics('2026-08')['rows']]
                self.assertLessEqual(max(values) - min(values), 1)

    def test_balance_switches_independent_and_hard_constraints_preserved(self):
        morning = self.scenario()
        afternoon = replace(morning.tasks[0], id='pm', start=840, end=900)
        s = replace(morning, tasks=(replace(morning.tasks[0], required_count=2), afternoon))
        settings = default_relaxed_shift_settings()
        counts = {('a', 'earlyDeparture'): 2, ('b', 'earlyDeparture'): 0,
                  ('a', 'afternoonRest'): 0, ('b', 'afternoonRest'): 2}
        def rest(**overrides):
            result = solve(s, relaxed_settings={**settings, **overrides}, relaxed_counts=counts)
            self.assertEqual([], validate_schedule(s, result.assignments))
            return relaxed_shift_facts(s, result.assignments, settings)
        self.assertTrue(next(r for r in rest(afternoonRestBalanceEnabled=False) if r['memberId']=='b')['earlyDeparture'])
        self.assertTrue(next(r for r in rest(earlyDepartureBalanceEnabled=False) if r['memberId']=='a')['afternoonRest'])
        locked = replace(s, tasks=(s.tasks[0], replace(afternoon, locked_member_ids=('b',))))
        result = solve(locked, relaxed_settings=settings, relaxed_counts={('a', 'afternoonRest'): 20})
        self.assertEqual([], validate_schedule(locked, result.assignments))
        self.assertIn(Assignment('pm', 'b'), result.assignments, '不能为均衡突破固定人员')
        corrupted = tuple(Assignment(a.task_id, 'a' if a.task_id == 'pm' else a.member_id) for a in result.assignments)
        self.assertTrue(validate_schedule(locked, corrupted), '人工破坏固定必须仍被最终校验发现')
        for bad in (None, -1, 32, True, 1.5):
            with self.assertRaises(ValueError):
                validate_relaxed_shift_settings({**settings, 'afternoonRestMaxDifference': bad})
        with self.assertRaises(ValueError):
            validate_relaxed_shift_settings({**settings, 'earlyDepartureBalanceEnabled': 1})

    def test_legacy_settings_keep_local_switches_and_new_defaults_on_empty_restart(self):
        directory, wb = self.workspace()
        config = small_configuration(None)
        settings = {**default_relaxed_shift_settings(), 'earlyDepartureBalanceEnabled': False,
                    'earlyDepartureMaxDifference': 3, 'afternoonRestMaxDifference': 0}
        config['relaxedShiftSettings'] = settings
        state = self.command(wb, 'import-complete-configuration', config)
        book = load_workbook(BytesIO(wb.export_complete_configuration(state['selectedPlanId'])))
        sheet = book['轻松班次设置']
        sheet.delete_rows(6, 4)
        stream = BytesIO(); book.save(stream); book.close()
        legacy = import_complete_configuration(stream.getvalue())
        # This old format contains the four original time rows, no switches.
        self.assertNotIn('earlyDepartureBalanceEnabled', legacy['relaxedShiftSettings'])
        self.command(wb, 'import-complete-configuration', legacy)
        self.assertEqual(settings, Workbench(directory/'state.sqlite3').backup()['state']['relaxedShiftSettings'])
        empty = Workbench(directory/'legacy-empty.sqlite3')
        self.command(empty, 'import-complete-configuration', legacy)
        self.assertEqual(default_relaxed_shift_settings(), empty.backup()['state']['relaxedShiftSettings'])

    def scenario(self, start=480, end=600, flight_end=None):
        return Scenario(service_date='2026-08-02', flights=(Flight('f', 'F0', '2026-08-02', start, flight_end or end),),
            members=(Member('a', '甲', frozenset({'岗'}), 0), Member('b', '乙', frozenset({'岗'}), 0)),
            tasks=(Task('t', 'f', '岗', start, end, 1, 'fixed'),))

    def fact(self, scenario, settings=None, duty=None, assignments=None):
        return relaxed_shift_facts(scenario, assignments or (Assignment('t', 'a'),),
            settings or default_relaxed_shift_settings(), duty)

    def test_strict_cutoff_afternoon_edges_and_no_tasks(self):
        self.assertEqual(1, len(self.fact(self.scenario())))
        self.assertTrue(self.fact(self.scenario())[0]['earlyDeparture'])
        at_noon = self.fact(self.scenario(end=720))[0]
        self.assertFalse(at_noon['earlyDeparture'])
        self.assertTrue(at_noon['afternoonRest'])
        self.assertFalse(self.fact(self.scenario(start=710, end=730))[0]['afternoonRest'])
        self.assertFalse(self.fact(self.scenario(start=720, end=750))[0]['afternoonRest'])
        self.assertTrue(self.fact(self.scenario(start=1080, end=1140))[0]['afternoonRest'])
        late = self.fact(self.scenario(start=1500, end=1560))[0]
        self.assertFalse(late['earlyDeparture'])
        self.assertTrue(late['afternoonRest'])
        self.assertEqual('次日02:00', late['lastEndTime'])

    def test_duty_status_leaders_and_extra_occupancy(self):
        s = self.scenario()
        duty = self.fact(s, duty='a')[0]
        self.assertFalse(duty['earlyDeparture'])
        self.assertTrue(duty['afternoonRest'])
        for member in (replace(s.members[0], team_leader=True), replace(s.members[0], status='病假')):
            self.assertEqual([], self.fact(replace(s, members=(member,))))
        self.assertEqual([], self.fact(replace(s, tasks=(replace(s.tasks[0], flight_id=None),))))

    def test_selected_late_flights_any_late_window_and_deduplication(self):
        settings = {**default_relaxed_shift_settings(), 'earlyDepartureCutoffTime': '23:30'}
        s = self.scenario(end=1330, flight_end=1380)
        self.assertTrue(self.fact(s, settings)[0]['earlyDeparture'], '23:00等于界线允许')
        self.assertFalse(self.fact(replace(s, flights=(replace(s.flights[0], window_end=1381),)), settings)[0]['earlyDeparture'])
        settings['lateFlightNos'] = ['F0']
        s = replace(s, tasks=(*s.tasks, replace(s.tasks[0], id='t2', end=1340)))
        fact = self.fact(s, settings, assignments=(Assignment('t', 'a'), Assignment('t2', 'a'), Assignment('t', 'a')))[0]
        self.assertFalse(fact['earlyDeparture'])
        self.assertEqual(['F0'], fact['lateFlightNos'])

    def workspace(self):
        temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp', prefix='relaxed-')
        self.addCleanup(temp.cleanup)
        return Path(temp.name), Workbench(Path(temp.name)/'state.sqlite3')

    def command(self, wb, action, payload=None, pid=None):
        return wb.execute(wb.backup()['state']['revision'], action, payload or {}, pid)

    def test_saved_manual_edits_clear_undo_formal_latest_restart_readonly_api(self):
        directory, wb = self.workspace()
        state = self.command(wb, 'import-complete-configuration', small_configuration(None))
        pid = state['selectedPlanId']
        self.command(wb, 'edit-cell', {'cellId': 'c0', 'text': '甲'}, pid)
        self.command(wb, 'edit-cell', {'cellId': 'c1', 'text': '甲'}, pid)
        self.command(wb, 'adopt', pid=pid)
        def count():
            return {r['memberName']: r['earlyDepartureCount'] for r in wb.relaxed_shift_statistics('2026-08')['rows']}
        self.assertEqual({'甲': 1, '乙': 0}, count())
        self.command(wb, 'edit-cell', {'cellId': 'c1', 'text': '乙'}, pid)
        self.assertEqual({'甲': 1, '乙': 1}, count())
        self.assertEqual(2, len(wb.snapshot()['relaxedShiftSummary']['earlyDepartures']))
        self.assertEqual(1, len(wb.snapshot()['formalRelaxedShiftSummary']['earlyDepartures']))
        self.command(wb, 'clear-draft', pid=pid)
        self.assertEqual({'甲': 0, '乙': 0}, count())
        self.command(wb, 'undo-edit', pid=pid)
        self.assertEqual({'甲': 1, '乙': 1}, count())
        self.command(wb, 'adopt', pid=pid)
        self.assertEqual({'甲': 1, '乙': 1}, count())
        self.assertEqual(0, sum(r['earlyDepartureCount'] for r in wb.relaxed_shift_statistics('2026-09')['rows']))
        before = wb.backup()
        client = TestClient(create_server_app(database_path=directory/'state.sqlite3'))
        self.assertEqual(200, client.get('/api/relaxed-shift-statistics?month=2026-08').status_code)
        self.assertEqual(422, client.get('/api/relaxed-shift-statistics?month=2026-13').status_code)
        self.assertEqual(before, wb.backup())
        self.assertEqual(wb.relaxed_shift_statistics('2026-08'), Workbench(directory/'state.sqlite3').relaxed_shift_statistics('2026-08'))
        other = Workbench(directory/'restored.sqlite3')
        other.restore(0, before)
        self.assertEqual(wb.relaxed_shift_statistics('2026-08')['rows'], other.relaxed_shift_statistics('2026-08')['rows'])

    def test_settings_excel_empty_import_restart_actual_use_and_legacy_preservation(self):
        directory, wb = self.workspace()
        config = small_configuration(None)
        settings = {**default_relaxed_shift_settings(), 'earlyDepartureCutoffTime': '11:00', 'lateFlightNos': ['F0'],
                    'earlyDepartureBalanceEnabled': False, 'afternoonRestMaxDifference': 0}
        config['relaxedShiftSettings'] = settings
        state = self.command(wb, 'import-complete-configuration', config)
        pid = state['selectedPlanId']
        exported = wb.export_complete_configuration(pid)
        incoming = import_complete_configuration(exported)
        self.assertEqual(settings, incoming['relaxedShiftSettings'])
        other = Workbench(directory/'empty.sqlite3')
        state = self.command(other, 'import-complete-configuration', incoming)
        other = Workbench(directory/'empty.sqlite3')
        target_pid = state['selectedPlanId']
        day = state['plans'][target_pid]['day']
        first = next(c['id'] for c in day['cells'] if c['flightNo'] == 'F0' and c['roleName'] == '接机1')
        self.command(other, 'edit-cell', {'cellId': first, 'text': '甲'}, target_pid)
        row = next(r for r in other.relaxed_shift_statistics('2026-08')['rows'] if r['memberName'] == '甲')
        self.assertEqual(1, row['lateFlightCount'])
        self.assertEqual(0, row['earlyDepartureCount'])
        self.command(other, 'import-complete-configuration', small_configuration(None))
        self.assertEqual(settings, other.backup()['state']['relaxedShiftSettings'], '旧配置缺字段保留')
        before = wb.backup()
        for value in ('=NOW()', '25:00'):
            book = load_workbook(BytesIO(exported))
            book['轻松班次设置']['B2'] = value
            stream = BytesIO(); book.save(stream); book.close()
            with self.assertRaises(ValueError):
                import_complete_configuration(stream.getvalue())
        invalid = deepcopy(settings); invalid['lateFlightNos'] = ['F0', 'F0']
        with self.assertRaises(ValueError):
            self.command(wb, 'save-rules', {'configuration': state['configuration'], 'balanceSettings': state['balanceSettings'],
                'preferences': state['handoffPreferences'], 'relaxedShiftSettings': invalid})
        self.assertEqual(before, wb.backup())
        for override in ({'afternoonRestEndTime': '11:00'}, {'earlyDepartureCutoffTime': 720}):
            with self.assertRaises(ValueError): validate_relaxed_shift_settings({**settings, **override})

    def test_latest_formal_and_duty_snapshot_survive_later_roster_changes(self):
        _, wb = self.workspace()
        state = self.command(wb, 'import-complete-configuration', small_configuration(None))
        pid = state['selectedPlanId']
        ids = {p['name']: p['id'] for p in state['people']}
        roster = {**state['dutyRoster'], 'enabled': False, 'anchorDate': '2026-08-02', 'dutyMemberIds': [ids['甲']]}
        self.command(wb, 'save-duty-settings', roster)
        self.command(wb, 'update-duty-roster', {'date': '2026-08-02', 'slot': 'duty', 'memberId': ids['甲']})
        self.command(wb, 'edit-cell', {'cellId': 'c0', 'text': '甲'}, pid)
        self.command(wb, 'edit-cell', {'cellId': 'c1', 'text': '乙'}, pid)
        self.command(wb, 'adopt', pid=pid)
        state = wb.backup()['state']
        formal = state['plans'][pid]['adopted']
        self.assertEqual(ids['甲'], formal['dutyMemberId'])
        state['dutyRoster']['overrides'] = []
        report = relaxed_shift_statistics(state, '2026-08')
        rows = {r['memberName']: r for r in report['rows']}
        self.assertEqual(0, rows['甲']['earlyDepartureCount'])
        self.assertEqual(1, rows['甲']['afternoonRestCount'])
        self.assertEqual(1, rows['乙']['earlyDepartureCount'])
        older = deepcopy(state['plans'][pid]); older['adopted']['savedRevision'] = 0
        older['adopted']['dutyMemberId'] = ids['乙']
        state['plans']['older'] = older
        self.assertEqual(report['rows'], relaxed_shift_statistics(state, '2026-08')['rows'])
        current = deepcopy(formal); current['assignments'] = []
        self.assertEqual(0, sum(r['afternoonRestCount'] for r in relaxed_shift_statistics(state, '2026-08', current=current)['rows']))
