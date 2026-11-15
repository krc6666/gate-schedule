"""Real scheduling contract for role relations and completed baggage rotations."""
from dataclasses import replace
import unittest

from gate_schedule.domain import Assignment, Flight, Member, Rotation, Scenario, Task
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule
from pathlib import Path
from copy import deepcopy
from io import BytesIO
import tempfile
from openpyxl import load_workbook
from gate_schedule.workbench import Workbench
from gate_schedule.excel_io import import_complete_configuration
from gate_schedule.workspace_document import plan_scenario
from gate_schedule.domain import task_handoff_options


def rules():
    return {'pairs': [
        {'flightNo': '*', 'firstRole': '接机1', 'secondRole': '接机2', 'scope': 'same-flight', 'mode': 'separate'},
        {'flightNo': '*', 'firstRole': '登机1', 'secondRole': '登机2', 'scope': 'same-flight', 'mode': 'separate'},
        {'flightNo': '*', 'firstRole': '入境申报', 'secondRole': '登机1', 'scope': 'same-flight', 'mode': 'separate'},
        {'flightNo': '*', 'firstRole': '入境申报', 'secondRole': '登机1', 'scope': 'later-flight', 'mode': 'sequence'},
        *[{'flightNo': '*', 'firstRole': '大件行李拦截', 'secondRole': role, 'scope': 'after-baggage', 'mode': 'sequence'} for role in ('入境申报','登机1','登机2')],
    ], 'baggage': [
        {'flightNo': '*', 'position': 1, 'targetRoles': ['入境申报'], 'enabled': True},
        {'flightNo': '*', 'position': 2, 'targetRoles': ['登机1', '登机2'], 'enabled': True},
        {'flightNo': '*', 'position': 3, 'targetRoles': ['登机1', '登机2'], 'enabled': True},
    ]}


def scene():
    flights = (Flight('A', 'A100', '2026-11-12', 480, 660), Flight('B', 'B200', '2026-11-12', 540, 720))
    roles = {'接机1', '接机2', '登机1', '登机2', '入境申报', '大件行李拦截'}
    tasks = tuple(Task(r, 'A', r, 480, 660, 1, 'mobile',
        service_stage='arrival' if r.startswith('接机') else 'boarding' if r.startswith('登机') else None,
        timing_mode='phase', concurrent=True) for r in sorted(roles - {'大件行李拦截'}))
    bag = tuple(Task('bag'+str(i), 'A', '大件行李拦截', 480+i*60, 540+i*60, 1, 'rotation') for i in range(3))
    return Scenario('2026-11-12', flights,
        tuple(Member(str(i), str(i), frozenset(roles), 0) for i in range(5)), (*tasks, *bag),
        rotations=tuple(Rotation(t.id, 'g', t.start, t.end, 1, tuple(str(i) for i in range(5)), i) for i,t in enumerate(bag)),
        release_policy='task')


def configuration():
    from tests.test_early_flight_coverage import coverage_configuration
    data=coverage_configuration(); data.update(rotationEnabled=True,rotationMinutes=60); day=data['day']
    day['flights']=[dict(id='A',flightNo='A100',windowStart='08:00',windowEnd='11:00'),dict(id='B',flightNo='B200',windowStart='09:00',windowEnd='12:00')]
    day['roleNames']=['大件行李拦截','入境申报','登机1','登机2']
    day['roleConfigs']=[dict(roleName=r,requiredCount=0,workMode='boarding' if r.startswith('登机') else 'continuous',concurrent=True) for r in day['roleNames']]
    needs={('A','大件行李拦截'):1,('A','入境申报'):1,('A','登机1'):1,('B','登机2'):1}
    day['cells']=[dict(id=f['id']+'-'+r,flightId=f['id'],flightNo=f['flightNo'],roleName=r,rawValue='',internalNames=[],externalLabels=[],semantics='empty',fixedMemberNames=[],requiredCountOverride=needs.get((f['id'],r),0),workStart=None,workEnd=None) for f in day['flights'] for r in day['roleNames']]
    for m in day['memberConfigs']:m['eligibleRoleNames']=list(day['roleNames'])
    data['workRules']=rules()
    data['workRules']['pairs']=[r for r in data['workRules']['pairs'] if {r['firstRole'],r['secondRole']} <= set(day['roleNames'])]
    return data


def setup(work):
    state=work.execute(work.snapshot()['revision'],'import-complete-configuration',configuration())
    for r in state['configuration']['roleConfigs']:
        if r['roleName'] not in configuration()['day']['roleNames']:
            state=work.execute(state['revision'],'save-role',{**r,'requiredCount':0})
    return state


class WorkRelationsTests(unittest.TestCase):
    def test_fragmented_rotation_releases_all_fragments_only_after_its_final_segment(self):
        from gate_schedule.domain import task_handoff_key, task_handoff_possible
        s=replace(scene(),work_rules=rules());tasks={t.id:t for t in s.tasks}
        original=tasks['bag0'];first=replace(original,end=510)
        final=replace(original,id='bag0-final',start=510)
        rotations=(replace(s.rotations[0],segment_end=510),replace(s.rotations[0],task_id=final.id,segment_start=510),*s.rotations[1:])
        s=replace(s,tasks=tuple(t for t in s.tasks if t.id!=original.id)+(first,final),rotations=rotations)
        target=tasks['入境申报']
        self.assertFalse(task_handoff_possible(s,first,target))
        self.assertTrue(task_handoff_possible(s,final,target))
        s=replace(s,task_handoffs=(task_handoff_key(final,target,s.service_date),))
        legal=(Assignment(first.id,'0'),Assignment(final.id,'0'),Assignment(target.id,'0'))
        self.assertFalse([i for i in validate_schedule(s,legal) if i.code!='coverage_shortfall'])
        broken=(*legal,Assignment('bag1','0'))
        self.assertIn('overlap',{i.code for i in validate_schedule(s,broken)})

    def test_configuration_tables_legacy_empty_partial_and_daily_override(self):
        from gate_schedule.work_rules_excel import SHEETS
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=root/'.tmp') as directory:
            work=Workbench(Path(directory)/'source.db');state=setup(work);pid=state['selectedPlanId']
            original=deepcopy(state['workRules'])
            day=deepcopy(state['plans'][pid]['day'])
            day.update(workRulesOverride=True,workRules={'pairs':[],'baggage':[]})
            state=work.execute(state['revision'],'save-input',{'day':day},pid)
            content=work.export_complete_configuration()
            def edited(remove=(),empty=False):
                book=load_workbook(BytesIO(content))
                for name in remove:
                    if name in book.sheetnames:del book[name]
                if empty:
                    for name in ('岗位搭配','大件接岗去向','当天接岗确认'):
                        book[name].delete_rows(2,book[name].max_row)
                output=BytesIO();book.save(output);book.close();return output.getvalue()
            payload=import_complete_configuration(content)
            self.assertTrue(payload['day']['workRulesOverride'])
            self.assertEqual({'pairs':[],'baggage':[]},payload['day']['workRules'])
            other=Workbench(Path(directory)/'target.db')
            other.execute(other.snapshot()['revision'],'import-complete-configuration',payload)
            other=Workbench(Path(directory)/'target.db');current=other.snapshot();newid=current['selectedPlanId']
            current=other.execute(current['revision'],'solve',{},newid)
            self.assertEqual({'pairs':[],'baggage':[]},current['plans'][newid]['day']['workRules'])
            legacy=import_complete_configuration(edited(SHEETS))
            self.assertNotIn('workRules',legacy)
            state=work.execute(state['revision'],'import-complete-configuration',legacy)
            self.assertEqual(original,state['workRules'])
            with self.assertRaises(ValueError):import_complete_configuration(edited(['岗位搭配']))
            payload=import_complete_configuration(edited(empty=True))
            state=work.execute(state['revision'],'import-complete-configuration',payload)
            self.assertEqual({'pairs':[],'baggage':[]},state['workRules'])

    def test_invalid_rules_save_is_atomic_and_complete_backup_preserves_groups(self):
        from gate_schedule.group_workspaces import GroupWorkspaces
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=root/'.tmp') as directory:
            groups=GroupWorkspaces(Path(directory)/'groups.db');work=groups.workbench('A');state=setup(work)
            payload={'configuration':deepcopy(state['configuration']),'balanceSettings':deepcopy(state['balanceSettings']),
                'preferences':deepcopy(state['handoffPreferences']),'workRules':deepcopy(state['workRules'])}
            payload['configuration']['transitionMinutes']=7
            payload['workRules']['pairs'][0]['secondRole']='未知岗位'
            before=groups.backup()
            with self.assertRaises(ValueError):work.execute(state['revision'],'save-rules',payload)
            self.assertEqual(before,groups.backup())
            restored=GroupWorkspaces(Path(directory)/'restored.db')
            preview=restored.preview_restore('A',before)
            restored.restore('A',preview['expectedRevision'],before,preview['expectedGroupsRevision'])
            reopened=GroupWorkspaces(Path(directory)/'restored.db')
            self.assertEqual(state['workRules'],reopened.workbench('A').snapshot()['workRules'])
            self.assertEqual(before['groups']['A']['plans'],reopened.backup()['groups']['A']['plans'])
            self.assertEqual(groups.workbench('B').snapshot()['workRules'],reopened.workbench('B').snapshot()['workRules'])

    def test_new_day_does_not_inherit_daily_rules_or_task_confirmations(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=root/'.tmp') as directory:
            work=Workbench(Path(directory)/'source.db');state=setup(work);pid=state['selectedPlanId']
            day=deepcopy(state['plans'][pid]['day'])
            day.update(serviceDate='2026-11-14',workRulesOverride=True,
                workRules={'pairs':[],'baggage':[]},taskHandoffs=['stale-confirmation'])
            state=work.execute(state['revision'],'archive-next',{'day':day},pid)
            next_day=state['plans'][state['selectedPlanId']]['day']
            self.assertFalse(next_day.get('workRulesOverride'))
            self.assertFalse(next_day.get('taskHandoffs'))
            self.assertEqual(state['workRules'],next_day['workRules'])

    def test_separate_rules_follow_their_direction_and_baggage_scope(self):
        from gate_schedule.domain import task_pair_conflict
        s=replace(scene(),work_rules=rules())
        source=next(t for t in s.tasks if t.id=='入境申报')
        target=replace(next(t for t in s.tasks if t.id=='登机1'),id='late',flight_id='B',start=540,end=720)
        next(r for r in s.work_rules['pairs'] if r['scope']=='later-flight')['mode']='separate'
        self.assertEqual('role_separation',task_pair_conflict(s,source,target)[0])
        # A rule for a later destination does not also ban the reverse direction.
        reverse_source=replace(source,flight_id='B',start=540,end=720)
        reverse_target=replace(target,flight_id='A',start=480,end=660)
        self.assertNotEqual('role_separation',(task_pair_conflict(s,reverse_source,reverse_target) or ('',))[0])
        next(r for r in s.work_rules['pairs'] if r['scope']=='after-baggage' and r['secondRole']=='入境申报')['mode']='separate'
        baggage=next(t for t in s.tasks if t.id=='bag0')
        after=replace(source,start=550,timing_mode='exact')
        legal=(Assignment(baggage.id,'0'),Assignment(after.id,'1'))
        scoped=replace(s,tasks=(baggage,after),rotations=(s.rotations[0],))
        self.assertFalse(validate_schedule(scoped,legal))
        self.assertIn('role_separation',{i.code for i in validate_schedule(scoped,(legal[0],replace(legal[1],member_id='0')))})

    def test_role_separation_and_arrival_boarding_reuse(self):
        s = replace(scene(), work_rules=rules())
        from gate_schedule.domain import task_pair_conflict
        tasks = {t.id: t for t in s.tasks}
        for a,b in [('接机1','接机2'), ('登机1','登机2'), ('入境申报','登机1')]:
            self.assertEqual('role_separation', task_pair_conflict(s,tasks[a],tasks[b])[0])
        self.assertIsNone(task_pair_conflict(s,tasks['接机1'],tasks['登机1']))
        values = solve(s, allow_partial=True).assignments
        self.assertFalse(validate_schedule(s, values))
        broken = tuple(a for a in values if a.task_id not in {'接机1','接机2'}) + (Assignment('接机1','0'),Assignment('接机2','0'))
        self.assertIn('role_separation', {i.code for i in validate_schedule(s,broken)})

    def test_baggage_release_confirmation_and_other_rotation_still_blocks(self):
        from gate_schedule.domain import task_handoff_key, task_pair_conflict
        s = replace(scene(), work_rules=rules())
        tasks = {t.id:t for t in s.tasks}
        bag, target = tasks['bag0'], tasks['入境申报']
        self.assertIsNotNone(task_pair_conflict(s,bag,target))
        s = replace(s, task_handoffs=(task_handoff_key(bag,target,s.service_date),))
        self.assertIsNone(task_pair_conflict(s,bag,target))
        self.assertIsNotNone(task_pair_conflict(s,tasks['bag1'],target))
        changed = replace(bag,end=550)
        self.assertIsNotNone(task_pair_conflict(s,changed,target), '时间变化后原确认失效')
        exact = replace(target,timing_mode='exact',start=500,end=600)
        s = replace(s, task_handoffs=(task_handoff_key(bag,exact,s.service_date),))
        self.assertIsNotNone(task_pair_conflict(s,bag,exact), '确认不能覆盖真实时间冲突')

    def test_declaration_can_only_transfer_to_confirmed_later_flight(self):
        from gate_schedule.domain import task_handoff_key, task_pair_conflict
        s = replace(scene(), work_rules=rules())
        source = next(t for t in s.tasks if t.id=='入境申报')
        target = replace(next(t for t in s.tasks if t.id=='登机1'), id='late',flight_id='B',start=540,end=720)
        self.assertIsNotNone(task_pair_conflict(s,source,target))
        s = replace(s,tasks=(*s.tasks,target),task_handoffs=(task_handoff_key(source,target,s.service_date),))
        self.assertIsNone(task_pair_conflict(s,source,target))
        same = replace(target,id='same',flight_id='A',start=480,end=660)
        s = replace(s,task_handoffs=(task_handoff_key(source,same,s.service_date),))
        self.assertEqual('role_separation',task_pair_conflict(s,source,same)[0])

    def test_permission_does_not_spread_and_dedicated_wins(self):
        from gate_schedule.domain import task_pair_conflict
        s = replace(scene(),work_rules=rules())
        a,b = [next(t for t in s.tasks if t.id==r) for r in ('接机1','登机1')]
        self.assertIsNone(task_pair_conflict(s,a,b))
        self.assertEqual('exclusive_same_flight',task_pair_conflict(s,replace(a,exclusive_same_flight=True),b)[0])

    def test_flight_exception_overrides_one_relation_and_permissions_do_not_transmit(self):
        from gate_schedule.domain import task_pair_conflict
        s=scene(); policy=rules()
        policy['pairs'].append(dict(flightNo='A100',firstRole='接机1',secondRole='接机2',scope='same-flight',mode='concurrent'))
        s=replace(s,work_rules=policy); tasks={t.id:t for t in s.tasks}
        self.assertIsNone(task_pair_conflict(s,tasks['接机1'],tasks['接机2']))
        self.assertEqual('role_separation',task_pair_conflict(s,tasks['登机1'],tasks['登机2'])[0])
        policy['pairs'].extend([dict(flightNo='*',firstRole='接机1',secondRole=r,scope='same-flight',mode='concurrent') for r in ('入境申报','登机1')])
        s=replace(s,work_rules=policy)
        values=tuple(Assignment(t,'0') for t in ('接机1','入境申报','登机1'))
        self.assertIn('role_separation',{i.code for i in validate_schedule(s,values)})
        s=replace(s,work_rules=rules(),tasks=tuple(replace(t,flight_id='B',start=540,end=720) if t.flight_id=='A' else t for t in s.tasks if t.task_kind!='rotation'),rotations=())
        self.assertEqual('role_separation',task_pair_conflict(s,next(t for t in s.tasks if t.role_id=='接机1'),next(t for t in s.tasks if t.role_id=='接机2'))[0])

    def test_final_validator_and_candidates_reject_other_unfinished_baggage(self):
        from gate_schedule.domain import task_handoff_key
        from gate_schedule.manual_editing import candidate_options
        s=replace(scene(),work_rules=rules()); tasks={t.id:t for t in s.tasks}
        s=replace(s,task_handoffs=(task_handoff_key(tasks['bag0'],tasks['入境申报'],s.service_date),))
        legal=(Assignment('bag0','0'),)
        candidate=next(o for o in candidate_options(s,legal,'入境申报',0) if o['id']=='0')
        self.assertTrue(candidate['available']);self.assertEqual('continuation',candidate['status'])
        occupied=(*legal,Assignment('bag1','0'))
        candidate=next(o for o in candidate_options(s,occupied,'入境申报',0) if o['id']=='0')
        self.assertFalse(candidate['available'])
        self.assertIn('overlap',{i.code for i in validate_schedule(s,(*occupied,Assignment('入境申报','0')))})
        changed=replace(s,service_date='2026-11-13')
        self.assertIn('invalid_work_rules',{i.code for i in validate_schedule(changed,legal)})

    def test_disabled_preference_does_not_authorize_or_block_handoff(self):
        from gate_schedule.domain import task_handoff_key,task_pair_conflict
        s=scene();policy=rules();policy['baggage']=[];s=replace(s,work_rules=policy)
        tasks={t.id:t for t in s.tasks}
        s=replace(s,task_handoffs=(task_handoff_key(tasks['bag0'],tasks['登机2'],s.service_date),))
        self.assertIsNone(task_pair_conflict(s,tasks['bag0'],tasks['登机2']))
        policy['pairs']=[r for r in policy['pairs'] if r['scope']!='after-baggage']
        self.assertIsNotNone(task_pair_conflict(s,tasks['bag0'],tasks['登机2']))

    def test_presets_do_not_fix_example_hours_or_rotation_destinations(self):
        from gate_schedule.domain import suggested_work_rules
        preset=suggested_work_rules(['大件行李拦截','入境申报','登机查验（VIP/F/优先通道）','登机人证核对/队伍秩序维护','接机1','接机2'])
        self.assertFalse(preset['baggage'])
        self.assertTrue(any(r['scope']=='after-baggage' for r in preset['pairs']))

    def test_real_three_people_release_and_migration_restart_formal_unchanged(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=root/'.tmp',prefix='work-relations-') as directory:
            work=Workbench(Path(directory)/'source.db');state=setup(work);pid=state['selectedPlanId']
            day=deepcopy(state['plans'][pid]['day']);day['workRules']=deepcopy(state['workRules'])
            raw=deepcopy(work.backup()['state']['plans'][pid]);raw['day']=day
            scenario,_=plan_scenario(raw)
            options=task_handoff_options(scenario)
            wanted={('imported-baggage-1-1','入境申报','A'),('imported-baggage-1-2','登机1','A'),('imported-baggage-1-3','登机2','B')}
            tasks={t.id:t for t in scenario.tasks}
            day['taskHandoffs']=[o['key'] for o in options if (o['sourceTaskId'],tasks[o['targetTaskId']].role_id,tasks[o['targetTaskId']].flight_id) in wanted]
            self.assertEqual(3,len(day['taskHandoffs']))
            state=work.execute(state['revision'],'save-input',{'day':day},pid)
            # Historical confirmations retain their old contract. Generate
            # that snapshot directly, then prove a current explicit replan
            # uses whole-flight protection without rewriting the old formal.
            legacy,_=plan_scenario(work.backup()['state']['plans'][pid])
            result=solve(legacy,allow_partial=True)
            state=work.execute(state['revision'],'assign',{'assignments':[dict(taskId=a.task_id,memberId=a.member_id) for a in result.assignments]},pid)
            self.assertTrue(state['plans'][pid]['valid'],state['plans'][pid]['issues'])
            self.assertEqual(6,len(state['plans'][pid]['draft']))
            state=work.execute(state['revision'],'adopt',{},pid)
            formal=deepcopy(work.backup()['state']['plans'][pid]['adopted'])
            exported=work.export_complete_configuration()
            book=load_workbook(BytesIO(exported));self.assertIn('岗位搭配',book.sheetnames);self.assertEqual(4,book['当天接岗确认'].max_row);book.close()
            incoming=import_complete_configuration(exported)
            target=Workbench(Path(directory)/'target.db')
            target.execute(target.snapshot()['revision'],'import-complete-configuration',incoming)
            target=Workbench(Path(directory)/'target.db');state=target.snapshot();newid=state['selectedPlanId']
            self.assertEqual(formal,target.backup()['state']['plans'][pid]['adopted'])
            self.assertEqual(3,len(state['plans'][newid]['day']['taskHandoffs']))
            state=target.execute(state['revision'],'solve',{},newid)
            self.assertIn('flowPreferences',state['plans'][newid]['day'])
            self.assertTrue(all(i['code']=='coverage_shortfall' for i in state['plans'][newid]['issues']))
            self.assertLess(len(state['plans'][newid]['draft']),6,'当前重排不能再借用重叠航班人员')
            self.assertEqual(formal,target.backup()['state']['plans'][pid]['adopted'])


if __name__=='__main__':
    unittest.main()
