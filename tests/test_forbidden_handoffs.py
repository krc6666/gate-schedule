"""Hard handoff exclusions, independent of continuation preference paths."""
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from openpyxl import load_workbook
from gate_schedule.domain import Assignment, Flight, validate_work_rules
from gate_schedule.excel_io import import_complete_configuration
from gate_schedule.manual_editing import candidate_options
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule
from gate_schedule.workbench import Workbench
from gate_schedule.workspace_document import plan_scenario
from tests.test_separation_groups import scene
from tests.test_work_relations import setup


def prohibition(scope='*', enabled=True, targets=None):
    return dict(flightNo=scope,sourceRole='接机1',targets=targets if targets is not None else [dict(role='登机1',flightScope='same-flight')],enabled=enabled)


class ForbiddenHandoffTests(unittest.TestCase):
    def test_only_prohibitions_propagate_to_new_day_and_daily_override_stays_independent(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()/'.tmp') as folder:
            wb=Workbench(Path(folder)/'source.db');state=setup(wb);pid=state['selectedPlanId']
            row=dict(flightNo='*',sourceRole='大件行李拦截',targets=[dict(role='入境申报',flightScope='same-flight')],enabled=True)
            rules=dict(pairs=[],baggage=[],forbiddenHandoffs=[row])
            state=wb.execute(state['revision'],'save-rules',dict(configuration=state['configuration'],balanceSettings=state['balanceSettings'],preferences=state['handoffPreferences'],workRules=rules))
            day=deepcopy(state['plans'][pid]['day']);day.pop('workRules',None);day.pop('workRulesOverride',None)
            state=wb.execute(state['revision'],'save-input',dict(day=day),pid)
            state=wb.execute(state['revision'],'solve',{},pid)
            self.assertEqual(rules,state['plans'][pid]['day']['workRules'])
            day=deepcopy(state['plans'][pid]['day']);day.update(workRulesOverride=True,workRules=dict(pairs=[],baggage=[]))
            state=wb.execute(state['revision'],'save-input',dict(day=day),pid)
            state=wb.execute(state['revision'],'solve',{},pid)
            self.assertNotIn('forbiddenHandoffs',state['plans'][pid]['day']['workRules'])

    def test_excel_disabled_draft_two_scopes_and_daily_override_round_trip_and_rejects_duplicates(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()/'.tmp') as folder:
            wb=Workbench(Path(folder)/'source.db');state=setup(wb);pid=state['selectedPlanId']
            row=dict(flightNo='*',sourceRole='大件行李拦截',targets=[dict(role='入境申报',flightScope=s) for s in ('same-flight','later-overlap')],enabled=True)
            stopped=dict(flightNo='CX*',sourceRole='大件行李拦截',targets=[],enabled=False)
            rules={**state['workRules'],'forbiddenHandoffs':[row,stopped]}
            state=wb.execute(state['revision'],'save-rules',dict(configuration=state['configuration'],balanceSettings=state['balanceSettings'],preferences=state['handoffPreferences'],workRules=rules))
            day=deepcopy(state['plans'][pid]['day']);day.update(workRulesOverride=True,workRules=dict(pairs=[],baggage=[],forbiddenHandoffs=[stopped]))
            state=wb.execute(state['revision'],'save-input',dict(day=day),pid)
            content=wb.export_complete_configuration();payload=import_complete_configuration(content)
            self.assertEqual(rules,payload['workRules']);self.assertEqual(day['workRules'],payload['day']['workRules'])
            book=load_workbook(BytesIO(content));sheet=book['禁止衔接'];sheet.append([c.value for c in sheet[2]])
            out=BytesIO();book.save(out);book.close()
            with self.assertRaises(ValueError):import_complete_configuration(out.getvalue())
            book=load_workbook(BytesIO(content));book['禁止衔接']['F3']='否';out=BytesIO();book.save(out);book.close()
            with self.assertRaises(ValueError):import_complete_configuration(out.getvalue())

    def test_hard_prohibition_overrides_direct_and_transitive_preferences_and_manual(self):
        base=scene();rules={**base.work_rules,'forbiddenHandoffs':[prohibition()]}
        sc=replace(base,work_rules=rules)
        legal=(Assignment('接机1','甲'),Assignment('接机2','乙'),Assignment('登机1','乙'),Assignment('登机2','甲'))
        self.assertEqual([],validate_schedule(sc,legal))
        broken=(*legal[:2],Assignment('登机1','甲'),legal[3])
        self.assertIn('forbidden_handoff',{i.code for i in validate_schedule(sc,broken)})
        candidate=next(c for c in candidate_options(sc,legal[:2],'登机1') if c['id']=='甲')
        self.assertFalse(candidate['available']);self.assertIn('禁止衔接',' '.join(candidate['reasons']))
        result=solve(sc);self.assertEqual([],validate_schedule(sc,result.assignments))
        people={a.task_id:a.member_id for a in result.assignments};self.assertNotEqual(people['接机1'],people['登机1'])
        partial=solve(replace(sc,members=sc.members[:1]),allow_partial=True)
        self.assertEqual({'coverage_shortfall'},{i.code for i in validate_schedule(sc,partial.assignments)})

    def test_scope_is_selected_before_targets_and_disabled_does_not_fall_back(self):
        base=scene();rows=[prohibition(),prohibition('CX*',targets=[dict(role='登机2',flightScope='same-flight')]),prohibition('CX9*',False,[]),prohibition('CX937')]
        values=(Assignment('接机1','甲'),Assignment('登机1','甲'))
        codes=lambda rows:{i.code for i in validate_schedule(replace(base,work_rules={**base.work_rules,'forbiddenHandoffs':rows}),values)}
        self.assertIn('forbidden_handoff',codes(rows))
        self.assertNotIn('forbidden_handoff',codes(rows[:-1]))
        self.assertNotIn('forbidden_handoff',codes(rows[:2]))
        self.assertIn('forbidden_handoff',codes(rows[:1]))

    def test_same_flight_does_not_forbid_later_and_cross_exclusion_does_not_forbid_own(self):
        from tests.test_flow_scheduling import preference
        base=scene();sc=replace(base,flights=(*base.flights,Flight('B','CX931',base.service_date,540,720)),
            tasks=(base.tasks[0],base.tasks[2],replace(base.tasks[3],id='later',flight_id='B',start=540,end=720)),
            flow_preferences=(dict(**preference(['登机1','登机2'],source='接机1'),targetFlightScopes={'登机1':'same-flight','登机2':'later-overlap'}),),
            work_rules=dict(pairs=[],baggage=[],forbiddenHandoffs=[prohibition()]))
        values=(Assignment('接机1','甲'),Assignment('登机1','乙'),Assignment('later','甲'))
        self.assertEqual([],validate_schedule(sc,values))
        prohibited=replace(sc,work_rules=dict(pairs=[],baggage=[],forbiddenHandoffs=[prohibition(targets=[dict(role='登机2',flightScope='later-overlap')])]))
        self.assertIn('forbidden_handoff',{i.code for i in validate_schedule(prohibited,values)})
        self.assertNotIn('forbidden_handoff',{i.code for i in validate_schedule(prohibited,(Assignment('接机1','甲'),Assignment('登机1','甲')))})
        result=solve(prohibited);self.assertEqual([],validate_schedule(prohibited,result.assignments))
        tasks={a.task_id:a.member_id for a in result.assignments};self.assertNotEqual(tasks['接机1'],tasks['later'])
        shared=replace(sc,tasks=(replace(sc.tasks[0],covered_flight_ids=('A','B')),replace(sc.tasks[1],flight_id='B')),work_rules=dict(pairs=[],baggage=[],forbiddenHandoffs=[prohibition()]))
        self.assertIn('forbidden_handoff',{i.code for i in validate_schedule(shared,(Assignment('接机1','甲'),Assignment('登机1','甲')))})

    def test_validation_rejects_unknown_duplicate_and_ambiguous_inputs(self):
        invalid=[prohibition(targets=[dict(role='接机1',flightScope='same-flight')]),prohibition(targets=[dict(role='登机1',flightScope='all')]),
                 prohibition(targets=[dict(role='未知',flightScope='same-flight')]),prohibition(targets=[]),
                 prohibition(targets=[dict(role='登机1',flightScope='same-flight')]*2)]
        for row in invalid:
            with self.subTest(row=row),self.assertRaises(ValueError):validate_work_rules(dict(pairs=[],baggage=[],forbiddenHandoffs=[row]),['接机1','登机1'])
        with self.assertRaises(ValueError):validate_work_rules(dict(pairs=[],baggage=[],forbiddenHandoffs=[prohibition(),prohibition()]))
        self.assertEqual([],validate_work_rules(dict(pairs=[],baggage=[],forbiddenHandoffs=[]))['forbiddenHandoffs'])

    def test_save_export_old_file_preserve_empty_import_restart_use_and_atomic_failure(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()/'.tmp') as folder:
            wb=Workbench(Path(folder)/'source.db');state=setup(wb)
            row=dict(flightNo='*',sourceRole='大件行李拦截',targets=[dict(role='入境申报',flightScope='same-flight')],enabled=True)
            rules={**state['workRules'],'forbiddenHandoffs':[row]}
            payload=dict(configuration=state['configuration'],balanceSettings=state['balanceSettings'],preferences=state['handoffPreferences'],workRules=rules)
            state=wb.execute(state['revision'],'save-rules',payload)
            content=wb.export_complete_configuration();book=load_workbook(BytesIO(content));self.assertEqual('visible',book['禁止衔接'].sheet_state)
            book.remove(book['禁止衔接']);out=BytesIO();book.save(out);book.close()
            state=wb.execute(state['revision'],'import-complete-configuration',import_complete_configuration(out.getvalue()))
            self.assertEqual([row],state['workRules']['forbiddenHandoffs'])
            target=Workbench(Path(folder)/'empty.db');state=target.execute(0,'import-complete-configuration',import_complete_configuration(content))
            target=Workbench(Path(folder)/'empty.db');state=target.snapshot();self.assertEqual(rules,state['workRules'])
            pid=state['selectedPlanId'];state=target.execute(state['revision'],'solve',{},pid);sc,issues=plan_scenario(state['plans'][pid]);self.assertEqual([],issues)
            self.assertEqual(rules,sc.work_rules)
            draft=tuple(Assignment(a['taskId'],a['memberId']) for a in state['plans'][pid]['draft'])
            self.assertNotIn('forbidden_handoff',{i.code for i in validate_schedule(sc,draft)})
            before=target.backup();bad=deepcopy(rules);bad['forbiddenHandoffs'][0]['targets'][0]['role']='未知'
            with self.assertRaises(ValueError):target.execute(state['revision'],'save-rules',{**payload,'configuration':state['configuration'],'workRules':bad})
            self.assertEqual(before,target.backup())
            book=load_workbook(BytesIO(content));book['禁止衔接'].delete_rows(2,book['禁止衔接'].max_row);out=BytesIO();book.save(out);book.close()
            state=target.execute(state['revision'],'import-complete-configuration',import_complete_configuration(out.getvalue()))
            self.assertEqual([],state['workRules']['forbiddenHandoffs'])


if __name__=='__main__':unittest.main()
