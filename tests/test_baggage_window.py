"""The check-in/cutoff window has one owner, shared by every delivery path."""
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from openpyxl import load_workbook
from gate_schedule.day_document import imported_day_from_dict
from gate_schedule.domain import Assignment, Flight, Member, Rotation, Scenario, Task, validate_work_rules
from gate_schedule.excel_io import import_complete_configuration
from gate_schedule.imported_schedule import imported_day_to_scenario
from gate_schedule.io import scenario_from_dict, scenario_to_dict
from gate_schedule.manual_editing import candidate_options
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule
from gate_schedule.workbench import Workbench
from tests.test_relative_scheduling import shared_pool_day


def window_rule():
    return dict(startBeforeMinutes=180,endBeforeMinutes=60,rotationMinutes=60)


def baggage_day(flights=None):
    day=shared_pool_day();day['flowPreferences']=[]
    day['flights']=flights or [dict(id='A',flightNo='CX937',windowStart='10:10',windowEnd='11:30'),dict(id='B',flightNo='KE0166',windowStart='11:00',windowEnd='12:15')]
    day['roleNames']=['大件行李拦截']
    day['roleConfigs']=[dict(roleName='大件行李拦截',requiredCount=1,concurrent=True)]
    day['memberConfigs']=[dict(memberName=n,eligibleRoleNames=day['roleNames']) for n in day['roster']]
    day['cells']=[dict(id=f['id']+'bag',flightId=f['id'],flightNo=f['flightNo'],roleName='大件行李拦截',rawValue='',internalNames=[],externalLabels=[],semantics='empty',fixedMemberNames=[]) for f in day['flights']]
    day['workRules']=dict(pairs=[],baggage=[],baggageWindow=window_rule())
    return day


class BaggageWindowTests(unittest.TestCase):
    def test_enable_on_existing_draft_discards_stale_turns_and_cancel_keeps_valid_references(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()/'.tmp') as folder:
            wb=Workbench(Path(folder)/'source.db');day=baggage_day();day['workRules'].pop('baggageWindow')
            day['roleNames'].append('接机1');day['roleConfigs'].append(dict(roleName='接机1',requiredCount=1,workMode='arrival',concurrent=True))
            for member in day['memberConfigs']:member['eligibleRoleNames']=list(day['roleNames'])
            day['cells'].append(dict(id='arrival',flightId='B',flightNo='KE0166',roleName='接机1',rawValue='',internalNames=[],externalLabels=[],semantics='empty',fixedMemberNames=[]))
            day['cells'].append(dict(id='unused-arrival',flightId='A',flightNo='CX937',roleName='接机1',rawValue='',internalNames=[],externalLabels=[],semantics='empty',fixedMemberNames=[],requiredCountOverride=0))
            day['flowPreferences']=[dict(flightNo='*',arrivalRole='大件行李拦截',boardingRoles=['接机1'],enabled=True,ordered=True)]
            state=wb.execute(0,'import-complete-configuration',dict(day=day,workRules=day['workRules'],handoffPreferences=day['flowPreferences'],rotationMinutes=60,rotationEnabled=True))
            for role in state['configuration']['roleConfigs']:
                if role['roleName'] not in day['roleNames']:state=wb.execute(state['revision'],'save-role',{**role,'requiredCount':0})
            pid=state['selectedPlanId'];state=wb.execute(state['revision'],'solve',{},pid)
            old=deepcopy(state['plans'][pid]['draft']);self.assertEqual(4,len(old))
            state=wb.execute(state['revision'],'adopt',{},pid);formal=deepcopy(state['plans'][pid]['adopted'])
            rules={**state['workRules'],'baggageWindow':window_rule()}
            state=wb.execute(state['revision'],'save-rules',dict(configuration=state['configuration'],balanceSettings=state['balanceSettings'],preferences=state['handoffPreferences'],workRules=rules))
            self.assertEqual(old,state['plans'][pid]['draft'])
            state=wb.execute(state['revision'],'solve',{},pid)
            self.assertEqual(3,len(state['plans'][pid]['draft']));self.assertEqual(formal,state['plans'][pid]['adopted'])
            state=wb.execute(state['revision'],'cancel-preview',{},pid)
            self.assertEqual([a for a in old if a['taskId']=='imported-arrival'],state['plans'][pid]['draft']);self.assertEqual(formal,state['plans'][pid]['adopted'])
            state=wb.execute(state['revision'],'solve',{},pid)
            state=wb.execute(state['revision'],'adopt',{},pid);kept=deepcopy(state['plans'][pid]['draft'])
            state=wb.execute(state['revision'],'solve',{},pid)
            state=wb.execute(state['revision'],'cancel-preview',{},pid)
            self.assertEqual(kept,state['plans'][pid]['draft'])

    def test_distinct_turns_are_hard_for_solver_candidates_and_manual_validation(self):
        tasks=tuple(Task(str(i),'A','大件行李拦截',480+60*i,540+60*i,1,'rotation') for i in range(3))
        people=tuple(Member(n,n,frozenset(['大件行李拦截']),0) for n in ('甲','乙','丙'))
        sc=Scenario('2057-08-01',(Flight('A','CX937','2057-08-01',480,660),),people,tasks,
            rotations=tuple(Rotation(t.id,'pool',t.start,t.end,1,tuple(m.id for m in people),i) for i,t in enumerate(tasks)),
            work_rules=dict(pairs=[],baggage=[],baggageWindow=window_rule()))
        result=solve(sc)
        self.assertEqual([],validate_schedule(sc,result.assignments))
        self.assertEqual(3,len({a.member_id for a in result.assignments}))
        owner=next(a.member_id for a in result.assignments if a.task_id=='0')
        bad=tuple(Assignment(a.task_id,owner) if a.task_id=='1' else a for a in result.assignments)
        self.assertIn('baggage_rotation_person',{i.code for i in validate_schedule(sc,bad)})
        options=candidate_options(sc,(Assignment('0',owner),),'1')
        option=next(c for c in options if c['id']==owner)
        self.assertFalse(option['available']);self.assertIn('baggage_rotation_person',{i['code'] for i in option['issues']})
        legacy=replace(sc,work_rules=dict(pairs=[],baggage=[]))
        self.assertEqual([],validate_schedule(legacy,bad))
        scarce=replace(sc,members=people[:2],rotations=tuple(replace(r,candidate_member_ids=('甲','乙')) for r in sc.rotations))
        self.assertEqual('INFEASIBLE',solve(scarce).status)
        partial=solve(scarce,allow_partial=True)
        self.assertEqual(2,len(partial.assignments));self.assertEqual(2,len({a.member_id for a in partial.assignments}))
        self.assertEqual({'coverage_shortfall'},{i.code for i in validate_schedule(scarce,partial.assignments)})
        other_pool=replace(sc,rotations=tuple(replace(r,group_id=r.task_id) for r in sc.rotations))
        self.assertEqual([],validate_schedule(other_pool,tuple(Assignment(t.id,'甲') for t in tasks)))

    def test_real_template_overlap_counts_full_hours_and_covers_tail(self):
        sc,issues=imported_day_to_scenario(imported_day_from_dict(baggage_day()),30,False)
        self.assertEqual([],issues)
        self.assertEqual([(510,570),(570,675)],[(t.start,t.end) for t in sc.tasks])
        self.assertEqual([0,1],[r.rotation_index for r in sc.rotations])
        self.assertEqual(('A','B'),sc.tasks[0].covered_flight_ids)
        self.assertEqual(('A','B'),sc.tasks[-1].covered_flight_ids)
        self.assertEqual(sc,scenario_from_dict(scenario_to_dict(sc)))
        result=solve(sc);self.assertEqual([],validate_schedule(sc,result.assignments))

    def test_cross_day_explicit_departure_empty_gaps_and_zero_demand(self):
        flights=[dict(id='A',flightNo='A',windowStart='前日23:30',windowEnd='00:20'),dict(id='B',flightNo='B',windowStart='22:30',windowEnd='次日00:55',departureTime='次日01:10')]
        day=baggage_day(flights);sc,issues=imported_day_to_scenario(imported_day_from_dict(day))
        self.assertEqual([],issues)
        self.assertEqual([(-160,-100),(-100,-40),(1315,1375),(1375,1450)],[(t.start,t.end) for t in sc.tasks])
        day['cells'][1]['requiredCountOverride']=0
        sc,issues=imported_day_to_scenario(imported_day_from_dict(day));self.assertEqual([],issues);self.assertEqual(2,len(sc.tasks))
        day['flights'][0]['departureTime']='前日21:00'
        _,issues=imported_day_to_scenario(imported_day_from_dict(day));self.assertIn('invalid_baggage_window',{i.code for i in issues})

    def test_full_hour_floor_tail_short_window_and_configured_rotation(self):
        for departure,minutes,expected in [
            ('12:45',60,[(510,570),(570,630),(630,705)]),
            ('12:30',60,[(510,570),(570,630),(630,690)]),
            ('10:10',60,[(510,550)]),
            ('12:45',90,[(510,600),(600,705)]),
        ]:
            with self.subTest(departure=departure,minutes=minutes):
                day=baggage_day([dict(id='A',flightNo='CX937',windowStart='10:10',windowEnd='11:30',departureTime=departure)])
                day['workRules']['baggageWindow']['rotationMinutes']=minutes
                sc,issues=imported_day_to_scenario(imported_day_from_dict(day),15,False)
                self.assertEqual([],issues);self.assertEqual(expected,[(t.start,t.end) for t in sc.tasks])
                result=solve(sc);self.assertEqual([],validate_schedule(sc,result.assignments))
                self.assertEqual(len(expected),len({a.member_id for a in result.assignments}))

    def test_transitive_overlap_touching_and_separate_pools(self):
        day=baggage_day([dict(id=str(i),flightNo=f'CX{i}',windowStart=end,windowEnd=end)
                         for i,end in enumerate(['11:00','12:30','14:30','17:00'])])
        # Only generation is under test; valid flight envelopes surround the
        # derived windows, without substituting envelope boundaries for them.
        for f in day['flights']:f['windowStart']='07:00'
        sc,issues=imported_day_to_scenario(imported_day_from_dict(day))
        self.assertEqual([],issues)
        groups={r.group_id for r in sc.rotations};self.assertEqual(2,len(groups))
        first=[r for r in sc.rotations if r.group_id=='baggage-1']
        self.assertEqual(5,len(first));self.assertEqual((480,810),(first[0].segment_start,first[-1].segment_end))
        self.assertEqual(2,len([r for r in sc.rotations if r.group_id=='baggage-2']))

    def test_fixed_members_do_not_split_a_turn_or_bypass_distinct_turns(self):
        day=baggage_day()
        day['cells'][0]['fixedMemberNames']=['甲'];day['cells'][1]['fixedMemberNames']=['乙']
        sc,issues=imported_day_to_scenario(imported_day_from_dict(day))
        self.assertEqual(2,len(sc.tasks));self.assertIn('too_many_fixed_members',{i.code for i in issues})
        day['cells'][1]['fixedMemberNames']=['甲']
        sc,issues=imported_day_to_scenario(imported_day_from_dict(day))
        self.assertEqual([],issues);self.assertEqual('INFEASIBLE',solve(sc).status)
        self.assertEqual('INFEASIBLE',solve(sc,allow_partial=True).status)
        day['cells'][0]['fixedMemberNames']=[];day['cells'][1]['fixedMemberNames']=[]
        day['cells'][1]['requiredCountOverride']=2
        _,issues=imported_day_to_scenario(imported_day_from_dict(day))
        self.assertIn('invalid_baggage_count',{i.code for i in issues})

    def test_actual_first_block_releases_to_configured_process_target(self):
        day=baggage_day();day['roleNames'].append('接机1')
        day['roleConfigs'].append(dict(roleName='接机1',requiredCount=1,workMode='arrival',concurrent=True))
        for config in day['memberConfigs']:config['eligibleRoleNames']=list(day['roleNames'])
        day['flowPreferences']=[dict(flightNo='*',arrivalRole='大件行李拦截',boardingRoles=['接机1'],enabled=True,ordered=True)]
        day['cells'].append(dict(id='Barrival',flightId='B',flightNo='KE0166',roleName='接机1',rawValue='',internalNames=[],externalLabels=[],semantics='empty',fixedMemberNames=['甲']))
        sc,issues=imported_day_to_scenario(imported_day_from_dict(day))
        self.assertEqual([],issues)
        first=sc.rotations[0].task_id
        sc=replace(sc,tasks=tuple(replace(t,locked_member_ids=('甲',)) if t.id==first else t for t in sc.tasks))
        result=solve(sc);self.assertEqual([],validate_schedule(sc,result.assignments))
        owned=[t for t in sc.tasks if any(a.task_id==t.id and a.member_id=='甲' for a in result.assignments)]
        self.assertEqual({'大件行李拦截','接机1'},{t.role_id for t in owned})
        self.assertEqual(1,len([t for t in owned if t.role_id=='大件行李拦截']))

    def test_missing_or_explicitly_disabled_rule_keeps_old_window_contract(self):
        day=baggage_day()
        for remove in (True,False):
            if remove:day['workRules'].pop('baggageWindow')
            else:day['workRules']['baggageWindow']=None
            sc,issues=imported_day_to_scenario(imported_day_from_dict(day));self.assertEqual([],issues)
            self.assertEqual(610,sc.tasks[0].start);self.assertEqual(735,sc.tasks[-1].end)

    def test_special_attendance_still_limits_early_baggage(self):
        day=baggage_day([dict(id='A',flightNo='A',windowStart='前日23:30',windowEnd='00:20')])
        sc,issues=imported_day_to_scenario(imported_day_from_dict(day));self.assertEqual([],issues)
        self.assertEqual([],validate_schedule(sc,solve(sc).assignments))
        for member in day['memberAvailability']:member.update(availableFrom='00:00',availableUntil='02:00')
        sc,issues=imported_day_to_scenario(imported_day_from_dict(day));self.assertEqual([],issues)
        self.assertEqual('INFEASIBLE',solve(sc).status)

    def test_only_window_rule_propagates_but_old_daily_override_stays_independent(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()/'.tmp') as folder:
            wb=Workbench(Path(folder)/'source.db');day=baggage_day()
            state=wb.execute(0,'import-complete-configuration',dict(day=day,workRules=day['workRules'],rotationMinutes=60,rotationEnabled=True))
            pid=state['selectedPlanId'];day=deepcopy(state['plans'][pid]['day'])
            day.pop('workRules',None);day.pop('workRulesOverride',None)
            state=wb.execute(state['revision'],'save-input',dict(day=day),pid)
            state=wb.execute(state['revision'],'solve',{},pid)
            self.assertEqual(window_rule(),state['plans'][pid]['day']['workRules']['baggageWindow'])
            day=deepcopy(state['plans'][pid]['day']);day.update(workRulesOverride=True,workRules=dict(pairs=[],baggage=[]))
            state=wb.execute(state['revision'],'save-input',dict(day=day),pid)
            state=wb.execute(state['revision'],'solve',{},pid)
            self.assertNotIn('baggageWindow',state['plans'][pid]['day']['workRules'])
            self.assertEqual('10:10',state['plans'][pid]['baggageCoverage']['groups'][0]['start'])
            day=deepcopy(state['plans'][pid]['day']);day['workRules']['baggageWindow']={**window_rule(),'rotationMinutes':90}
            state=wb.execute(state['revision'],'save-input',dict(day=day),pid)
            data=wb.export_complete_configuration();payload=import_complete_configuration(data)
            self.assertEqual(window_rule(),payload['workRules']['baggageWindow'])
            self.assertEqual(90,payload['day']['workRules']['baggageWindow']['rotationMinutes'])
            backup=wb.backup();restored=Workbench(Path(folder)/'restored.db');restored.restore(0,backup)
            after_restore=restored.backup()
            restored=Workbench(Path(folder)/'restored.db')
            self.assertEqual(after_restore,restored.backup())
            self.assertEqual(backup['state']['workRules'],after_restore['state']['workRules'])
            self.assertEqual(backup['state']['plans'],after_restore['state']['plans'])

    def test_rule_validation_rejects_bad_numbers_without_mutation(self):
        for key,value in [('startBeforeMinutes',True),('endBeforeMinutes',-1),('rotationMinutes',0),('startBeforeMinutes',30)]:
            rule=window_rule();rule[key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):validate_work_rules(dict(pairs=[],baggage=[],baggageWindow=rule))

    def test_rule_save_export_old_import_empty_import_restart_and_actual_use(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()/'.tmp') as folder:
            wb=Workbench(Path(folder)/'source.db');day=baggage_day()
            s=wb.execute(0,'import-complete-configuration',dict(day=day,workRules=day['workRules'],rotationMinutes=60,rotationEnabled=True))
            for role in s['configuration']['roleConfigs']:
                if role['roleName']!='大件行李拦截':s=wb.execute(s['revision'],'save-role',{**role,'requiredCount':0})
            pid=s['selectedPlanId'];s=wb.execute(s['revision'],'solve',{},pid)
            self.assertEqual(2,len(s['plans'][pid]['schedule']['tasks']))
            self.assertEqual(2,s['plans'][pid]['baggageCoverage']['requiredPeople'])
            data=wb.export_complete_configuration();book=load_workbook(BytesIO(data));self.assertEqual('visible',book['大件保障规则'].sheet_state)
            book.remove(book['大件保障规则']);out=BytesIO();book.save(out);book.close()
            s=wb.execute(s['revision'],'import-complete-configuration',import_complete_configuration(out.getvalue()))
            self.assertEqual(window_rule(),s['workRules']['baggageWindow'])
            target=Workbench(Path(folder)/'empty.db');s=target.execute(0,'import-complete-configuration',import_complete_configuration(data))
            target=Workbench(Path(folder)/'empty.db');s=target.snapshot();self.assertEqual(window_rule(),s['workRules']['baggageWindow'])
            s=target.execute(s['revision'],'solve',{},s['selectedPlanId']);self.assertEqual(2,len(s['plans'][s['selectedPlanId']]['schedule']['tasks']))
            before=target.backup();bad=deepcopy(s['workRules']);bad['baggageWindow']['rotationMinutes']=False
            with self.assertRaises(ValueError):target.execute(s['revision'],'save-rules',dict(configuration=s['configuration'],balanceSettings=s['balanceSettings'],preferences=s['handoffPreferences'],workRules=bad))
            self.assertEqual(before,target.backup())
            book=load_workbook(BytesIO(data));book['大件保障规则'].delete_rows(2,book['大件保障规则'].max_row);out=BytesIO();book.save(out);book.close()
            s=target.execute(s['revision'],'import-complete-configuration',import_complete_configuration(out.getvalue()))
            self.assertIsNone(s['workRules']['baggageWindow'])


if __name__=='__main__':unittest.main()
