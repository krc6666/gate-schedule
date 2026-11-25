"""Shared baggage scopes compare the pool's flights, not one anchored flight."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from gate_schedule.domain import (Assignment, Flight, Member, Rotation, Scenario, Task,
    flow_target_scope_allows, preferred_mobile_handoffs, task_pair_conflict)
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule


def flexible_configuration():
    from tests.test_continuation_chains import baggage_chain_configuration
    data=baggage_chain_configuration()
    data['day']['flights']=[{'id':'A','flightNo':'CX937','windowStart':'10:10','windowEnd':'11:30'},
        {'id':'B','flightNo':'KE0166','windowStart':'11:00','windowEnd':'12:15'}]
    data['day']['roleNames']=['大件行李拦截','接机1','秩序维护']
    data['day']['roleConfigs']=[r for r in data['day']['roleConfigs'] if r['roleName'] in data['day']['roleNames']]
    templates=[c for c in data['day']['cells'] if c['roleName'] in data['day']['roleNames']]
    data['day']['cells']=[{**deepcopy(c),'id':f"{f['id']}-{c['roleName']}",'flightId':f['id'],'flightNo':f['flightNo'],
        'requiredCountOverride':1 if f['id']=='A' or c['roleName']=='大件行李拦截' else 0}
        for f in data['day']['flights'] for c in templates]
    for m in data['day']['memberConfigs']:
        m['eligibleRoleNames']=[r for r in m['eligibleRoleNames'] if r in data['day']['roleNames']]
    data['handoffPreferences']=[{'flightNo':'*','arrivalRole':'大件2','boardingRoles':['接机1'],'enabled':True,
        'targetFlightScopes':{'接机1':'earlier-overlap'}},
        {'flightNo':'*','arrivalRole':'接机1','boardingRoles':['秩序维护'],'enabled':True,
         'targetFlightScopes':{'秩序维护':'same-flight'}}]
    data['day']['flowPreferences']=deepcopy(data['handoffPreferences'])
    data['workRules']['baggageWindow']['handoffMode']='flexible'
    data['day']['workRules']=deepcopy(data['workRules'])
    return data


def shared_scene(scope='earlier-overlap', target='early'):
    return Scenario('2026-08-24',
        (Flight('early','CX937','2026-08-24',610,690),Flight('late','KE0166','2026-08-24',660,735)),
        (Member('m','示例甲',frozenset({'大件行李拦截','接机1','秩序维护'}),0),),
        (Task('bag','early','大件行李拦截',610,670,1,'rotation',covered_flight_ids=('early','late')),
         Task('arrival',target,'接机1',610 if target=='early' else 660,690 if target=='early' else 735,1,'mobile',timing_mode='phase',service_stage='arrival')),
        rotations=(Rotation('bag','pool',610,670,1,('m',),0),),release_policy='task',
        flow_preferences=({'flightNo':'*','arrivalRole':'大件1','boardingRoles':['接机1'],'enabled':True,
            'ordered':True,'targetFlightScopes':{'接机1':scope}},))


class SharedBaggageScopesTests(unittest.TestCase):
    def test_shared_pool_accepts_same_role_with_separate_early_and_late_scopes(self):
        scene=shared_scene()
        early=replace(scene.tasks[1],id='order-early',role_id='秩序维护',flight_id='early')
        late=replace(scene.tasks[1],id='order-late',role_id='秩序维护',flight_id='late',start=660,end=735)
        row={'flightNo':'*','arrivalRole':'大件1','boardingRoles':['秩序维护','秩序维护'],'enabled':True,
             'targetFlightScopeList':['earlier-overlap','later-overlap']}
        self.assertTrue(flow_target_scope_allows(scene,scene.tasks[0],early,row))
        self.assertTrue(flow_target_scope_allows(scene,scene.tasks[0],late,row))
        row['boardingRoles']=['秩序维护']
        row['targetFlightScopeList']=['earlier-overlap']
        self.assertFalse(flow_target_scope_allows(scene,scene.tasks[0],late,row))

    def test_old_cloud_without_handoff_mode_loads_readonly_and_old_excel_preserves_fixed(self):
        from io import BytesIO
        from openpyxl import load_workbook
        from gate_schedule.browser_runtime import BrowserStore,validate_cloud_documents
        from gate_schedule.group_workspaces import GroupWorkspaces
        from gate_schedule.workbench import Workbench
        from gate_schedule.excel_io import import_complete_configuration
        data=flexible_configuration()
        with tempfile.TemporaryDirectory(dir=Path.cwd()/'.tmp',prefix='pool-legacy-') as directory:
            groups=GroupWorkspaces(Path(directory)/'groups.db',store=BrowserStore())
            source=groups.workbench('A');source.execute(0,'import-complete-configuration',data)
            original=deepcopy(groups.store.documents)
            for state in original[GroupWorkspaces.KEY]['groups'].values():
                window=(state.get('workRules') or {}).get('baggageWindow')
                if window:window.pop('handoffMode',None)
            untouched=deepcopy(original);validate_cloud_documents(original)
            self.assertEqual(untouched,original,'登录校验不能改写旧云资料')
            raw=source.export_complete_configuration()
            book=load_workbook(BytesIO(raw));book['大件保障规则'].delete_cols(8)
            for name in ['接续航班范围','禁止衔接']:
                if name in book:
                    for row in book[name]:
                        for cell in row:
                            if cell.value=='重叠时段中较早结束的航班':cell.value='重叠且早结束的其他航班'
                            elif cell.value=='重叠时段中较晚结束的航班':cell.value='重叠且晚结束的其他航班'
            output=BytesIO();book.save(output);book.close()
            legacy=import_complete_configuration(output.getvalue())
            self.assertEqual(data['handoffPreferences'],legacy['handoffPreferences'])
            self.assertNotIn('handoffMode',legacy['workRules']['baggageWindow'])
            target=Workbench(Path(directory)/'fixed.db');fixed=deepcopy(data);fixed['workRules']['baggageWindow']['handoffMode']='fixed'
            state=target.execute(0,'import-complete-configuration',fixed)
            state=target.execute(state['revision'],'import-complete-configuration',legacy)
            self.assertEqual('fixed',state['workRules']['baggageWindow']['handoffMode'])
            empty=Workbench(Path(directory)/'empty.db');state=empty.execute(0,'import-complete-configuration',legacy)
            self.assertEqual('flexible',state['workRules']['baggageWindow']['handoffMode'])

    def test_flexible_pool_can_handoff_before_nominal_last_turn_and_survive_transfer(self):
        from gate_schedule.workbench import Workbench
        from gate_schedule.excel_io import import_complete_configuration
        with tempfile.TemporaryDirectory(dir=Path.cwd()/'.tmp',prefix='flexible-pool-') as directory:
            root=Path(directory);data=flexible_configuration();w=Workbench(root/'source.db')
            state=w.execute(0,'import-complete-configuration',data)
            for role in state['configuration']['roleConfigs']:
                if role['roleName'] not in data['day']['roleNames']:
                    state=w.execute(state['revision'],'save-role',{**role,'requiredCount':0})
            for stage in ['source','excel','backup']:
                if stage=='excel':
                    decoded=import_complete_configuration(w.export_complete_configuration())
                    self.assertEqual('flexible',decoded['workRules']['baggageWindow']['handoffMode'])
                    w=Workbench(root/'excel.db');w.execute(0,'import-complete-configuration',decoded)
                    w=Workbench(root/'excel.db')
                elif stage=='backup':
                    backup=w.backup();w=Workbench(root/'backup.db');w.restore(0,backup);w=Workbench(root/'backup.db')
                state=w.snapshot();pid=state['selectedPlanId'];state=w.execute(state['revision'],'solve',{},pid)
                plan=state['plans'][pid];self.assertTrue(plan['valid'],(stage,plan['issues']))
                self.assertEqual(4,len(plan['draft']))
                worker=next(m['id'] for m in state['people'] if m['name']=='甲')
                self.assertEqual(3,sum(a['memberId']==worker for a in plan['draft']))
                last=next(t for t in plan['schedule']['tasks'] if 'baggage' in t['id'] and '大件2' in t['timingLabel'])
                self.assertEqual('phase',last['timingMode'])
                self.assertIn('参考',last['timingLabel'])
                self.assertTrue(any(a['taskId']==last['id'] and a['memberId']==worker for a in plan['draft']))
            state=w.execute(state['revision'],'adopt',{},pid);formal=deepcopy(state['plans'][pid]['adopted'])
            rules=deepcopy(state['workRules']);rules['baggageWindow']['handoffMode']='fixed'
            state=w.execute(state['revision'],'save-rules',{'configuration':state['configuration'],
                'balanceSettings':state['balanceSettings'],'preferences':state['handoffPreferences'],'workRules':rules})
            state=w.execute(state['revision'],'solve',{},pid)
            self.assertFalse(state['plans'][pid]['valid'],'固定模式仍不能倒接已结束的早班')
            self.assertEqual(formal,state['plans'][pid]['adopted'],'交接方式改变不追改旧正式')
            decoded=import_complete_configuration(w.export_complete_configuration())
            self.assertEqual('fixed',decoded['workRules']['baggageWindow']['handoffMode'])

    def test_flexible_pool_still_checks_complete_coverage_distinct_people_and_real_occupancies(self):
        from gate_schedule.day_document import imported_day_from_dict
        from gate_schedule.imported_schedule import imported_day_to_scenario
        from gate_schedule.validator import validate_scenario
        scene,issues=imported_day_to_scenario(imported_day_from_dict(flexible_configuration()['day']))
        self.assertFalse(issues)
        result=solve(scene);self.assertEqual('OPTIMAL',result.status)
        broken=replace(scene,rotations=(scene.rotations[0],replace(scene.rotations[1],segment_start=scene.rotations[1].segment_start+5)))
        self.assertIn('invalid_baggage_pool',{i.code for i in validate_scenario(broken)})
        bags=[t for t in scene.tasks if t.role_id=='大件行李拦截']
        same=tuple(Assignment(t.id,'甲') for t in scene.tasks)
        self.assertIn('baggage_rotation_person',{i.code for i in validate_schedule(scene,same)})
        occupied=Task('occupied',None,'明确支援',620,630,1,'fixed',locked_member_ids=('甲',))
        blocked=replace(scene,tasks=(*scene.tasks,occupied),members=tuple(replace(m,eligible_role_ids=m.eligible_role_ids|{'明确支援'}) for m in scene.members))
        self.assertIsNotNone(task_pair_conflict(blocked,bags[1],occupied),'真实支援不会被灵活交接绕过')

    def test_pool_earlier_and_later_include_the_covered_flight_without_anchor_bias(self):
        for scope,target in [('earlier-overlap','early'),('later-overlap','late')]:
            for anchor in ['early','late']:
                scene=shared_scene(scope,target)
                scene=replace(scene,tasks=(replace(scene.tasks[0],flight_id=anchor),scene.tasks[1]))
                with self.subTest(scope=scope,anchor=anchor):
                    self.assertTrue(flow_target_scope_allows(scene,*scene.tasks,scene.flow_preferences[0]))
                    self.assertIsNone(task_pair_conflict(scene,*scene.tasks))
                    result=solve(scene)
                    self.assertEqual('OPTIMAL',result.status,result.diagnostics)
                    self.assertEqual({Assignment(t.id,'m') for t in scene.tasks},set(result.assignments))
                    self.assertFalse(validate_schedule(scene,result.assignments))

    def test_pool_range_does_not_mean_every_covered_flight_or_a_single_flight(self):
        for scope,target in [('earlier-overlap','late'),('later-overlap','early')]:
            scene=shared_scene(scope,target)
            self.assertFalse(flow_target_scope_allows(scene,*scene.tasks,scene.flow_preferences[0]))
        base=shared_scene()
        for other in [replace(base.flights[1],window_end=690),replace(base.flights[1],window_start=690,window_end=735)]:
            scene=replace(base,flights=(base.flights[0],other))
            self.assertFalse(flow_target_scope_allows(scene,*scene.tasks,scene.flow_preferences[0]))
        single=replace(base,tasks=(replace(base.tasks[0],covered_flight_ids=('early',)),base.tasks[1]))
        self.assertFalse(flow_target_scope_allows(single,*single.tasks,single.flow_preferences[0]))

    def test_flexible_pool_inherits_a_later_flight_from_the_actual_arrival_step(self):
        base=shared_scene()
        first=replace(base.tasks[0],start=610,end=735,timing_mode='phase')
        last=replace(first,id='last')
        target=Task('later-arrival','late','接机2',660,735,1,'mobile',timing_mode='phase',service_stage='arrival')
        scene=replace(base,tasks=(first,base.tasks[1],target,last),
            members=(replace(base.members[0],eligible_role_ids=base.members[0].eligible_role_ids|{'接机2'}),Member('n','示例乙',frozenset({'大件行李拦截'}),0)),
            rotations=(Rotation('bag','pool',610,670,1,('m','n'),0),Rotation('last','pool',670,735,1,('m','n'),1)),
            work_rules={'pairs':[],'baggage':[],'baggageWindow':{'startBeforeMinutes':180,'endBeforeMinutes':60,'rotationMinutes':60,'handoffMode':'flexible'}},
            flow_preferences=(*base.flow_preferences,{'flightNo':'*','arrivalRole':'接机1','boardingRoles':['接机2'],'enabled':True,'targetFlightScopes':{'接机2':'later-overlap'}}))
        self.assertIsNone(task_pair_conflict(scene,first,base.tasks[1]))
        self.assertIsNone(task_pair_conflict(scene,base.tasks[1],target))
        self.assertIsNone(task_pair_conflict(scene,first,target))
        result=solve(scene)
        self.assertEqual('OPTIMAL',result.status,result.diagnostics)
        self.assertEqual({Assignment('bag','m'),Assignment('arrival','m'),Assignment('later-arrival','m'),Assignment('last','n')},set(result.assignments))
        self.assertFalse(validate_schedule(scene,result.assignments))

    def test_pool_first_role_inherits_arrival_downstream_without_copying_the_rule(self):
        scene=shared_scene()
        board=Task('board','early','秩序维护',610,690,1,'mobile',timing_mode='phase',service_stage='boarding')
        scene=replace(scene,tasks=(*scene.tasks,board),flow_preferences=(*scene.flow_preferences,
            {'flightNo':'*','arrivalRole':'接机1','boardingRoles':['秩序维护'],'enabled':True,
             'targetFlightScopes':{'秩序维护':'same-flight'}}))
        result=solve(scene)
        self.assertEqual('OPTIMAL',result.status,result.diagnostics)
        self.assertEqual({Assignment(t.id,'m') for t in scene.tasks},set(result.assignments))
        self.assertFalse(validate_schedule(scene,result.assignments))
        rules={'pairs':[],'baggage':[],'forbiddenHandoffs':[{'flightNo':'*','sourceRole':'大件1',
            'targets':[{'role':'秩序维护','flightScope':'earlier-overlap'}],'enabled':True}]}
        self.assertIn('forbidden_handoff',{i.code for i in validate_schedule(replace(scene,work_rules=rules),result.assignments)})

    def test_late_fragment_uses_the_whole_pool_for_scope_but_cannot_go_back_in_time(self):
        scene=shared_scene()
        last=Task('last','late','大件行李拦截',690,735,1,'rotation',covered_flight_ids=('late',))
        scene=replace(scene,tasks=(*scene.tasks,last),
            rotations=(*scene.rotations,Rotation('last','pool',690,735,1,('m',),1)),
            flow_preferences=({'flightNo':'*','arrivalRole':'大件2','boardingRoles':['接机1'],'enabled':True,
                'targetFlightScopes':{'接机1':'earlier-overlap'}},))
        self.assertTrue(flow_target_scope_allows(scene,last,scene.tasks[1],scene.flow_preferences[0]))
        self.assertIsNotNone(task_pair_conflict(scene,last,scene.tasks[1]))
        self.assertNotIn(('last','arrival'),[(a.id,b.id) for a,b in preferred_mobile_handoffs(scene)])
        illegal=(Assignment('last','m'),Assignment('arrival','m'))
        self.assertTrue(any(i.code!='coverage_shortfall' for i in validate_schedule(scene,illegal)))
        shared_last=replace(last,start=670,covered_flight_ids=('early','late'))
        shared_scene_late=replace(scene,tasks=(*scene.tasks[:2],shared_last))
        self.assertTrue(flow_target_scope_allows(shared_scene_late,shared_last,scene.tasks[1],scene.flow_preferences[0]))
        self.assertIsNotNone(task_pair_conflict(shared_scene_late,shared_last,scene.tasks[1]))
        self.assertNotIn(('last','arrival'),[(a.id,b.id) for a,b in preferred_mobile_handoffs(shared_scene_late)])

    def test_pool_scope_keeps_actual_blocks_qualifications_and_prohibitions(self):
        base=shared_scene()
        legal=(Assignment('bag','m'),Assignment('arrival','m'))
        self.assertFalse(validate_schedule(base,legal))
        for kind in ['fixed','support','rotation']:
            actual=replace(base,tasks=(base.tasks[0],replace(base.tasks[1],task_kind=kind,timing_mode='exact')))
            self.assertIsNotNone(task_pair_conflict(actual,*actual.tasks))
        unqualified=replace(base,members=(replace(base.members[0],eligible_role_ids=frozenset({'大件行李拦截'})),))
        self.assertIn('ineligible',{i.code for i in validate_schedule(unqualified,legal)})
        rules={'pairs':[],'baggage':[],'forbiddenHandoffs':[{'flightNo':'*','sourceRole':'大件1',
            'targets':[{'role':'接机1','flightScope':'earlier-overlap'}],'enabled':True}]}
        self.assertEqual('forbidden_handoff',task_pair_conflict(replace(base,work_rules=rules),*base.tasks)[0])


if __name__=='__main__':
    unittest.main()
