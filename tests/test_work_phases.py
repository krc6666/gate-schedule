from copy import deepcopy
from dataclasses import replace
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from gate_schedule.domain import Assignment
from gate_schedule.excel_io import imported_day_from_dict, imported_day_to_dict, export_complete_configuration, import_complete_configuration
from gate_schedule.imported_schedule import imported_day_to_scenario
from gate_schedule.io import scenario_from_dict, scenario_to_dict
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule
from gate_schedule.workbench import Workbench


def phase_day():
    roles = ['接机1', '登机主验', '登机核验', '督导']
    return {
        'serviceDate': '2026-08-02', 'title': '环节测试', 'releasePolicy': 'task',
        'flights': [{'id': 'A', 'flightNo': 'A', 'windowStart': '10:10', 'windowEnd': '11:30'}],
        'roleNames': roles, 'roster': ['甲', '乙'], 'remarks': [], 'remarksReviewed': True,
        'roleConfigs': [{'roleName': r, 'requiredCount': 1, 'workMode': 'arrival' if r.startswith('接机') else 'boarding' if r.startswith('登机') else 'continuous', 'concurrent': r == '督导'} for r in roles],
        'memberConfigs': [{'memberName': n, 'eligibleRoleNames': roles} for n in ['甲', '乙']],
        'memberAvailability': [{'memberName': n, 'participating': True, 'availableFrom': '09:00', 'availableUntil': '次日02:00'} for n in ['甲', '乙']],
        'cells': [{'id': r, 'flightId': 'A', 'flightNo': 'A', 'roleName': r, 'rawValue': '', 'internalNames': [], 'externalLabels': [], 'semantics': 'empty'} for r in roles],
    }


class WorkPhaseTests(unittest.TestCase):
    def scenario(self, data=None):
        scenario, issues = imported_day_to_scenario(imported_day_from_dict(data or phase_day()))
        self.assertEqual([], issues)
        return scenario

    def test_same_flight_phase_handoff_and_concurrent_switch(self):
        scenario = self.scenario()
        result = solve(scenario)
        self.assertIn(result.status, {'OPTIMAL', 'FEASIBLE'})
        self.assertEqual([], validate_schedule(scenario, result.assignments))
        # A concurrent supervisor must not allow two ordinary boarding roles to share.
        bad = tuple(Assignment(a.task_id, '甲') for a in result.assignments)
        self.assertIn('overlap', {i.code for i in validate_schedule(scenario, bad)})
        day = phase_day()
        day['roleConfigs'][-1]['concurrent'] = False
        self.assertEqual('INFEASIBLE', solve(self.scenario(day)).status)

    def test_cross_flight_requires_confirmed_handoff_and_checks_other_work(self):
        day = phase_day()
        day['roleConfigs'] = day['roleConfigs'][:2]
        day['roleNames'] = day['roleNames'][:2]
        day['memberConfigs'] = [{'memberName': '甲', 'eligibleRoleNames': day['roleNames']}]
        day['roster'] = ['甲']; day['memberAvailability'] = day['memberAvailability'][:1]
        day['flights'].append({'id': 'B', 'flightNo': 'B', 'windowStart': '11:00', 'windowEnd': '12:15'})
        day['cells'] = [day['cells'][0], {**day['cells'][1], 'flightId': 'B', 'flightNo': 'B'}]
        scenario = self.scenario(day)
        self.assertEqual('INFEASIBLE', solve(scenario).status)
        day['phaseHandoffs'] = [['A', 'B']]
        scenario = self.scenario(day)
        result = solve(scenario)
        self.assertEqual([], validate_schedule(scenario, result.assignments))
        self.assertEqual(2, len(result.assignments))
        unconfirmed = replace(scenario, phase_handoffs=())
        self.assertIn('phase_handoff_unconfirmed', {i.code for i in validate_schedule(unconfirmed, result.assignments)})
        # A separate assignment still conflicts even when reception->boarding is allowed.
        extra = replace(scenario.tasks[0], id='another', service_stage='boarding')
        broken = replace(scenario, tasks=(*scenario.tasks, extra))
        self.assertIn('overlap', {i.code for i in validate_schedule(broken, (*result.assignments, Assignment('another', '甲')))})

    def test_actual_time_release_is_task_end_and_other_tasks_are_not_ignored(self):
        day = phase_day()
        day['roleConfigs'] = day['roleConfigs'][:2]
        day['roleNames'] = day['roleNames'][:2]
        day['flights'].append({'id': 'B', 'flightNo': 'B', 'windowStart': '11:00', 'windowEnd': '12:15'})
        day['cells'] = [day['cells'][0], {**day['cells'][1], 'flightId': 'B', 'flightNo': 'B'}]
        for r in day['roleConfigs']: r['workMode'] = 'timed'
        day['cells'][0].update(workStart='10:10', workEnd='10:30')
        day['cells'][1].update(workStart='11:00', workEnd='11:20')
        scenario = self.scenario(day)
        legal = tuple(Assignment(t.id, '甲') for t in scenario.tasks)
        self.assertEqual([], validate_schedule(scenario, legal))
        self.assertEqual(2, len(scenario.tasks), '不得按原航班结束无端拆开登机任务')
        broken = replace(scenario, tasks=(replace(scenario.tasks[0], end=11*60+10), scenario.tasks[1]))
        self.assertTrue(validate_schedule(broken, legal))

    def test_excel_json_keep_modes_and_handoff_and_reject_invalid_values(self):
        day = phase_day(); day['phaseHandoffs'] = []
        scenario = self.scenario(day)
        self.assertEqual(scenario, scenario_from_dict(scenario_to_dict(scenario)))
        payload = {'day': day, 'rotationEnabled': True, 'rotationMinutes': 60, 'source': '测试'}
        restored = import_complete_configuration(export_complete_configuration(payload))
        restored_day = imported_day_from_dict(restored['day'])
        self.assertEqual(imported_day_from_dict(day).role_configs, restored_day.role_configs)
        self.assertEqual('task', restored_day.release_policy)
        with self.assertRaises(ValueError):
            bad = deepcopy(day); bad['phaseHandoffs'] = [['A', 'missing']]
            imported_day_from_dict(bad)
        day['roleConfigs'][0]['concurrent'] = '否'
        with self.assertRaises(ValueError): imported_day_from_dict(day)

    def test_phase_mode_does_not_claim_unknown_walking_minutes(self):
        day = phase_day(); day['transitionMinutes'] = 5
        day['flights'].append({'id': 'B', 'flightNo': 'B', 'windowStart': '11:00', 'windowEnd': '12:15'})
        day['phaseHandoffs'] = [['A', 'B']]
        day['cells'][1].update(flightId='B', flightNo='B')
        scenario = self.scenario(day)
        values = tuple(Assignment(t.id, '甲' if t.role_id != '登机核验' else '乙') for t in scenario.tasks)
        self.assertIn('phase_transition_unknown', {i.code for i in validate_schedule(scenario, values)})

    def test_actual_exception_keeps_explicit_occupancy_and_next_day_checks(self):
        day=phase_day()
        for c in day['cells']:
            c.update(workModeOverride='timed',workStart='次日00:10',workEnd='次日00:30')
        day['cells'][0]['workEnd']='次日00:20'
        day['cells'][1]['workStart']='次日00:20'
        scenario=self.scenario(day)
        legal=tuple(Assignment(t.id,'乙' if t.role_id=='登机核验' else '甲') for t in scenario.tasks)
        self.assertEqual([],validate_schedule(scenario,legal))
        day['extraOccupancies']=[{'id':'支援','label':'临时支援','memberNames':['甲'],'start':'次日00:15','end':'次日00:25'}]
        broken=self.scenario(day)
        self.assertIn('overlap',{i.code for i in validate_schedule(broken,(*legal,Assignment('occupancy-支援-甲','甲')))})
        content=export_complete_configuration({'day':day,'rotationEnabled':True,'rotationMinutes':60,'source':'例外'})
        restored=import_complete_configuration(content)['day']
        self.assertTrue(all(c.get('workModeOverride')=='timed' for c in restored['cells']))

    def test_phase_availability_qualification_and_lock_cannot_be_bypassed(self):
        scenario=self.scenario()
        result=solve(scenario)
        self.assertEqual([],validate_schedule(scenario,result.assignments))
        member=next(m for m in scenario.members if m.id==result.assignments[0].member_id)
        task=next(t for t in scenario.tasks if t.id==result.assignments[0].task_id)
        bad=replace(scenario,members=tuple(replace(m,eligible_role_ids=frozenset()) if m==member else m for m in scenario.members))
        self.assertIn('ineligible',{i.code for i in validate_schedule(bad,result.assignments)})
        bad=replace(scenario,members=tuple(replace(m,available_from=11*60) if m==member else m for m in scenario.members))
        self.assertIn('member_unavailable',{i.code for i in validate_schedule(bad,result.assignments)})
        other=next(m.id for m in scenario.members if m.id!=member.id)
        bad=replace(scenario,tasks=tuple(replace(t,locked_member_ids=(other,)) if t==task else t for t in scenario.tasks))
        self.assertIn('lock_missing',{i.code for i in validate_schedule(bad,result.assignments)})


class PhaseWorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)/'state.sqlite3'
        self.workbench = Workbench(self.path)

    def tearDown(self):
        self.directory.cleanup()

    def command(self, action, payload=None, plan=None):
        return self.workbench.execute(self.workbench.snapshot()['revision'], action, payload or {}, plan)

    def test_real_workspace_modes_typing_candidates_adopt_excel_and_restart(self):
        day = phase_day()
        state = self.command('import-complete-configuration', {'day':day,'rotationMinutes':60,'rotationEnabled':True,'source':'测试'})
        pid = state['selectedPlanId']
        for role, name in [('接机1','甲'),('登机主验','甲'),('登机核验','乙'),('督导','甲')]:
            state = self.command('edit-cell',{'cellId':role,'text':name},pid)
        self.assertTrue(state['plans'][pid]['valid'])
        candidates = self.workbench.candidates(pid,'imported-登机核验')
        self.assertFalse(next(c['available'] for c in candidates if c['name']=='甲'))
        adopted = self.command('adopt',{},pid)['plans'][pid]['adopted']
        self.assertEqual('task',adopted['scenario']['releasePolicy'])
        self.assertTrue(self.workbench.export(pid).startswith(b'PK'))
        backup=self.workbench.backup()
        other=Workbench(self.path.parent/'restore.sqlite3')
        other.restore(0,backup)
        self.assertEqual(adopted,other.snapshot()['plans'][pid]['adopted'])
        self.assertEqual(adopted,Workbench(self.path).snapshot()['plans'][pid]['adopted'])
        bad=deepcopy(backup);bad['state']['plans'][pid]['day']['roleConfigs'][0]['workMode']='bad'
        with self.assertRaises(ValueError): self.workbench.restore(backup['state']['revision'],bad)
        self.assertEqual(backup,self.workbench.backup())
        # Changing the supervisor switch must invalidate the current draft, keeping history.
        day=state['plans'][pid]['day'];day['roleConfigs'][-1]['concurrent']=False
        self.command('save-input',{'day':day},pid)
        for role, name in [('接机1','甲'),('登机主验','甲'),('登机核验','乙'),('督导','甲')]:
            state=self.command('edit-cell',{'cellId':role,'text':name},pid)
        self.assertFalse(state['plans'][pid]['valid'])
        self.assertEqual(adopted,state['plans'][pid]['adopted'])
        saved=self.command('adopt',{},pid)['plans'][pid]
        self.assertTrue(saved['adopted']['issues'])

    def test_phase_plan_preserves_old_plan_and_new_default_is_usable(self):
        state=self.command('new-plan',{'serviceDate':'2026-08-02'})
        pid=state['selectedPlanId']
        self.assertEqual('arrival',next(r['workMode'] for r in state['plans'][pid]['day']['roleConfigs'] if r['roleName']=='接机1'))
        original=deepcopy(state['plans'][pid])
        state=self.command('phase-plan',{},pid)
        self.assertNotEqual(pid,state['selectedPlanId'])
        self.assertEqual(original,state['plans'][pid])
        # Bad switches fail atomically; explicit choices survive restart and new days.
        before=self.workbench.backup()
        with self.assertRaises(ValueError):self.command('save-role',{'roleName':'督导','concurrent':'是'})
        self.assertEqual(before,self.workbench.backup())
        self.command('save-role',{'roleName':'督导','concurrent':False,'workMode':'continuous'})
        state=self.command('new-plan',{'serviceDate':'2026-08-04'})
        config=next(r for r in state['plans'][state['selectedPlanId']]['day']['roleConfigs'] if r['roleName']=='督导')
        self.assertFalse(config['concurrent'])

    def test_nonempty_excel_handoff_roundtrip_and_bad_mode_rejected(self):
        from openpyxl import load_workbook
        day=phase_day()
        day['flights'].append({'id':'B','flightNo':'B','windowStart':'11:00','windowEnd':'12:15'})
        day['cells'] += [{**c,'id':c['id']+'B','flightId':'B','flightNo':'B'} for c in list(day['cells'])]
        day['phaseHandoffs']=[['A','B']]
        content=export_complete_configuration({'day':day,'rotationEnabled':True,'rotationMinutes':60,'source':'测试'})
        restored=import_complete_configuration(content)['day']
        ids={f['flightNo']:f['id'] for f in restored['flights']}
        self.assertEqual([[ids['A'],ids['B']]],restored['phaseHandoffs'])
        book=load_workbook(BytesIO(content));book['岗位性质']['B2']='随意'
        stream=BytesIO();book.save(stream)
        with self.assertRaises(ValueError): import_complete_configuration(stream.getvalue())


if __name__ == '__main__':
    unittest.main()
