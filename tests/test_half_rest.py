from copy import deepcopy
from dataclasses import replace
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from gate_schedule.domain import Assignment, Flight, Member, Scenario, Task, flight_balance_members
from gate_schedule.io import scenario_from_dict, scenario_to_dict
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule
from gate_schedule.workbench import Workbench, WorkspaceError
from gate_schedule.excel_io import import_complete_configuration
from gate_schedule.excel_io import export_complete_configuration, export_formal_schedule
from openpyxl import load_workbook
from tests.test_balance_workflow import small_configuration


def half_rest_scenario():
    tasks = tuple(Task(str(i), str(i), '岗', start, end, 1, 'fixed') for i, (start, end) in enumerate(
        ((480, 540), (690, 780), (720, 810), (1200, 1260), (1500, 1560))))
    return Scenario('2026-08-02', tuple(Flight(t.id, 'F'+t.id, '2026-08-02', t.start, t.end) for t in tasks),
        (Member('a', '下午休', frozenset({'岗'}), 0, half_rest='early-finish'),
         Member('b', '上午休', frozenset({'岗'}), 0, half_rest='late-start'),
         Member('c', '全天', frozenset({'岗'}), 0)), tasks, release_policy='task')


class HalfRestTests(unittest.TestCase):
    def test_half_rest_exemption_changes_hard_flight_count_check(self):
        scenario=half_rest_scenario()
        scenario=replace(scenario,tasks=(replace(scenario.tasks[0],locked_member_ids=('a',)),),
                         flights=scenario.flights[:1],flight_count_gap=0)
        assignments=solve(scenario).assignments
        self.assertEqual([],validate_schedule(scenario,assignments))
        self.assertEqual((Assignment('0','a'),),assignments)
        strict=replace(scenario,balance_exempt_half_rest=False)
        self.assertIn('flight_count_spread',{i.code for i in validate_schedule(strict,assignments)})
        self.assertEqual('INFEASIBLE',solve(strict).status)

    def test_fixed_resting_member_cannot_be_forced_even_with_vacancy(self):
        scenario = half_rest_scenario()
        scenario = replace(scenario, tasks=(replace(scenario.tasks[0], locked_member_ids=('b',)),), flights=scenario.flights[:1])
        result = solve(scenario, allow_partial=True)
        self.assertFalse(any(a.member_id == 'b' for a in result.assignments))
        codes = {i.code for i in validate_schedule(scenario, [Assignment('0','b')])}
        self.assertIn('half_rest_period', codes)

    def test_bad_input_atomic_rejection_and_legacy_excel(self):
        config = small_configuration(None)
        config['halfRestSettings'] = {'exemptFlightCount': False}
        book = load_workbook(BytesIO(export_complete_configuration(config)))
        self.assertIn('半休设置', book.sheetnames)
        sheet = book['当日出勤']
        self.assertEqual('半休时段', sheet.cell(1,5).value)
        sheet.cell(2,5,'=1+1')
        stream = BytesIO();book.save(stream)
        with self.assertRaises(ValueError): import_complete_configuration(stream.getvalue())
        sheet.delete_cols(5); del book['半休设置']
        stream = BytesIO();book.save(stream)
        legacy = import_complete_configuration(stream.getvalue())
        self.assertNotIn('halfRestSettings', legacy)
        self.assertTrue(all(not a.get('halfRest') for a in legacy['day']['memberAvailability']))
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp') as directory:
            wb=Workbench(Path(directory)/'state.sqlite3')
            state=wb.execute(0,'import-complete-configuration',config)
            for bad in ({'exemptFlightCount':'否'}, {'exemptFlightCount':1}, {}):
                before=wb.backup()
                with self.assertRaises((ValueError,WorkspaceError)):
                    wb.execute(state['revision'],'save-rules',{'configuration':state['configuration'],
                        'balanceSettings':state['balanceSettings'],'preferences':state['handoffPreferences'],'halfRestSettings':bad})
                self.assertEqual(before,wb.backup())
            bad=deepcopy(config);bad['day']['memberAvailability'][0]['halfRest']='全天休'
            with self.assertRaises((ValueError,WorkspaceError)):
                wb.execute(state['revision'],'import-complete-configuration',bad)
            self.assertEqual(before,wb.backup())
            state=wb.execute(state['revision'],'import-complete-configuration',legacy)
            self.assertFalse(state['halfRestSettings']['exemptFlightCount'],'旧配置缺新开关保留现设置')
        scenario=half_rest_scenario();result=solve(scenario)
        exported=load_workbook(BytesIO(export_formal_schedule(scenario,result.assignments)))
        self.assertEqual('下午半休',exported['半休人员'].cell(2,2).value)

    def test_period_boundaries_overnight_and_independent_rejection(self):
        scenario = half_rest_scenario()
        for member, allowed in [('a', {0, 1}), ('b', {2, 3, 4})]:
            for index, task in enumerate(scenario.tasks):
                codes = {i.code for i in validate_schedule(scenario, [Assignment(task.id, member)])}
                self.assertEqual(index not in allowed, 'half_rest_period' in codes, (member, index, codes))
        solved = solve(scenario)
        self.assertIn(solved.status, ('OPTIMAL', 'FEASIBLE'))
        self.assertEqual([], validate_schedule(scenario, solved.assignments))
        broken = [a for a in solved.assignments if a.task_id != '0'] + [Assignment('0', 'b')]
        self.assertIn('half_rest_period', {i.code for i in validate_schedule(scenario, broken)})
        self.assertEqual({'c'}, {m.id for m in flight_balance_members(scenario)})
        self.assertEqual(3, len(flight_balance_members(replace(scenario, balance_exempt_half_rest=False))))

    def test_participation_early_finish_late_anchor_and_no_eligible_work(self):
        scenario = half_rest_scenario()
        result = solve(scenario)
        own = {m.id: {a.task_id for a in result.assignments if a.member_id == m.id} for m in scenario.members}
        self.assertEqual({'0'}, own['a'], '下午半休落实早班后尽量早下班')
        self.assertEqual({'2', '4'}, own['b'], '上午半休优先最晚结束，仅补齐无法由他人覆盖的午后岗')
        only_morning = replace(scenario, tasks=scenario.tasks[:1], flights=scenario.flights[:1])
        result = solve(only_morning)
        self.assertEqual([], validate_schedule(only_morning, result.assignments))
        self.assertFalse(any(a.member_id == 'b' for a in result.assignments), '无午后岗位也不解除上午半休')

    def test_scenario_roundtrip_and_legacy_default(self):
        scenario = half_rest_scenario()
        self.assertEqual(scenario, scenario_from_dict(scenario_to_dict(scenario)))
        legacy = scenario_to_dict(scenario)
        legacy.pop('halfRestSettings')
        for m in legacy['members']: m.pop('halfRest')
        restored = scenario_from_dict(legacy)
        self.assertTrue(restored.balance_exempt_half_rest)
        self.assertTrue(all(m.half_rest is None for m in restored.members))

    def test_workflow_manual_warning_excel_empty_import_restart_and_backup(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp', prefix='half-rest-') as directory:
            path = Path(directory)/'state.sqlite3'
            wb = Workbench(path)
            config = small_configuration(None)
            config['day']['flights'][1].update(windowStart='14:00', windowEnd='15:00')
            config['day']['memberAvailability'][0]['halfRest'] = 'late-start'
            config['day']['memberAvailability'][1]['halfRest'] = 'early-finish'
            config['halfRestSettings'] = {'exemptFlightCount': False}
            state = wb.execute(0, 'import-complete-configuration', config);pid = state['selectedPlanId']
            for role in state['configuration']['roleConfigs']:
                if role['roleName'] != '接机1':
                    state = wb.execute(state['revision'], 'save-role', {'roleName':role['roleName'], 'requiredCount':0})
            state = wb.execute(state['revision'], 'solve', {}, pid)
            self.assertTrue(state['plans'][pid]['valid'])
            formal_state = wb.execute(state['revision'], 'adopt', {}, pid)
            formal = deepcopy(formal_state['plans'][pid]['adopted'])
            changed = wb.execute(formal_state['revision'], 'edit-cell', {'cellId':'c0', 'text':'甲'}, pid)
            self.assertIn('half_rest_period', {i['code'] for i in changed['plans'][pid]['issues']})
            self.assertEqual('甲', next(m['name'] for m in changed['plans'][pid]['schedule']['members'] if m['id'] == changed['plans'][pid]['draft'][0]['memberId']))
            before = wb.backup()
            saved=wb.execute(changed['revision'], 'adopt', {}, pid)
            self.assertIn('half_rest_period',{i['code'] for i in saved['plans'][pid]['adopted']['issues']})
            self.assertEqual(formal, before['state']['plans'][pid]['adopted'])
            exported = wb.export_complete_configuration(pid)
            parsed = import_complete_configuration(exported)
            self.assertEqual(config['halfRestSettings'], parsed['halfRestSettings'])
            self.assertEqual(['late-start','early-finish'], [a['halfRest'] for a in parsed['day']['memberAvailability']])
            target = Workbench(Path(directory)/'new.sqlite3')
            state = target.execute(0, 'import-complete-configuration', parsed)
            target = Workbench(Path(directory)/'new.sqlite3')
            state = target.execute(state['revision'], 'solve', {}, state['selectedPlanId'])
            self.assertTrue(state['plans'][state['selectedPlanId']]['valid'])
            self.assertFalse(state['halfRestSettings']['exemptFlightCount'])
            restored = Workbench(Path(directory)/'restored.sqlite3');restored.restore(0,before)
            self.assertEqual(formal, restored.backup()['state']['plans'][pid]['adopted'])
            self.assertEqual('late-start', restored.snapshot()['plans'][pid]['day']['memberAvailability'][0]['halfRest'])
