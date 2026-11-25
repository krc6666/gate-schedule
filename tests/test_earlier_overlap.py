"""Explicit earlier-ending overlap scopes use flight windows, never guessed minutes."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from gate_schedule.domain import (Assignment, Flight, Member, Scenario, Task,
    task_pair_conflict, flow_target_scope_allows, preferred_mobile_handoffs,
    validate_handoff_preferences, validate_work_rules, task_candidate_relationship)
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule


def earlier_scene(target_role='接机2'):
    roles=frozenset({'接机1',target_role})
    return Scenario('2026-08-24',
        (Flight('A','CX937','2026-08-24',600,720),Flight('B','KE0166','2026-08-24',630,690)),
        (Member('m','甲',roles,0),),
        (Task('source','A','接机1',600,720,1,'mobile',timing_mode='phase',service_stage='arrival'),
         Task('target','B',target_role,630,690,1,'mobile',timing_mode='phase',service_stage='arrival')),
        release_policy='task',work_rules={'pairs':[],'baggage':[]},
        flow_preferences=({'flightNo':'CX937','arrivalRole':'接机1','boardingRoles':[target_role],
            'enabled':True,'ordered':True,'targetFlightScopes':{target_role:'earlier-overlap'}},))


class EarlierOverlapTests(unittest.TestCase):
    def test_target_may_start_later_and_end_earlier_and_use_the_same_role(self):
        for role in ('接机1','接机2'):
            s=earlier_scene(role)
            with self.subTest(role=role):
                validate_handoff_preferences(list(s.flow_preferences))
                self.assertTrue(flow_target_scope_allows(s,*s.tasks,s.flow_preferences[0]))
                self.assertIsNone(task_pair_conflict(s,*s.tasks))
                self.assertEqual([('source','target')],[(a.id,b.id) for a,b in preferred_mobile_handoffs(s)])
                result=solve(s);self.assertEqual('OPTIMAL',result.status,result.diagnostics)
                self.assertEqual({Assignment('source','m'),Assignment('target','m')},set(result.assignments))
                self.assertFalse(validate_schedule(s,result.assignments))
                if role!='接机1':self.assertEqual('continuation',task_candidate_relationship(s,[s.tasks[0]],s.tasks[1]))

    def test_equal_later_touching_disjoint_or_same_flight_cannot_match_earlier(self):
        base=earlier_scene()
        for start,end in ((630,720),(630,750),(500,600),(480,590)):
            s=replace(base,flights=(base.flights[0],replace(base.flights[1],window_start=start,window_end=end)),
                tasks=(base.tasks[0],replace(base.tasks[1],start=start,end=end)))
            with self.subTest(window=(start,end)):
                self.assertFalse(flow_target_scope_allows(s,*s.tasks,s.flow_preferences[0]))
                self.assertNotIn(('source','target'),[(a.id,b.id) for a,b in preferred_mobile_handoffs(s)])
        same=replace(base,tasks=(base.tasks[0],replace(base.tasks[1],flight_id='A')))
        self.assertFalse(flow_target_scope_allows(same,*same.tasks,same.flow_preferences[0]))
        later=replace(base,flow_preferences=({**base.flow_preferences[0],'targetFlightScopes':{'接机2':'later-overlap'}},))
        self.assertIsNotNone(task_pair_conflict(later,*later.tasks))
        self.assertIn('overlap',{i.code for i in validate_schedule(later,(Assignment('source','m'),Assignment('target','m')))})

    def test_earlier_prohibition_beats_a_legal_continuation_and_cannot_reverse(self):
        s=earlier_scene();legal=solve(s).assignments
        rules={'pairs':[],'baggage':[],'forbiddenHandoffs':[dict(flightNo='CX937',sourceRole='接机1',
            targets=[dict(role='接机2',flightScope='earlier-overlap')],enabled=True)]}
        validate_work_rules(rules,['接机1','接机2'])
        blocked=replace(s,work_rules=rules)
        self.assertIn('forbidden_handoff',{i.code for i in validate_schedule(blocked,legal)})
        self.assertLess(len(solve(blocked,allow_partial=True).assignments),2)
        wrong=deepcopy(rules);wrong['forbiddenHandoffs'][0]['targets'][0]['flightScope']='later-overlap'
        self.assertFalse(validate_schedule(replace(s,work_rules=wrong),legal))

    def test_same_role_cross_flight_prohibition_is_valid_but_same_flight_self_is_not(self):
        s=earlier_scene('接机1')
        rules={'pairs':[],'baggage':[],'forbiddenHandoffs':[dict(flightNo='CX937',sourceRole='接机1',
            targets=[dict(role='接机1',flightScope='earlier-overlap')],enabled=True)]}
        validate_work_rules(rules,['接机1'])
        self.assertEqual('forbidden_handoff',task_pair_conflict(replace(s,work_rules=rules),*s.tasks)[0])
        bad=deepcopy(rules);bad['forbiddenHandoffs'][0]['targets'][0]['flightScope']='same-flight'
        with self.assertRaises(ValueError):validate_work_rules(bad,['接机1'])

    def test_actual_occupied_blocks_qualification_and_transition_still_win(self):
        base=earlier_scene();legal=(Assignment('source','m'),Assignment('target','m'))
        for kind in ('fixed','rotation','support'):
            s=replace(base,tasks=(base.tasks[0],replace(base.tasks[1],task_kind=kind,timing_mode='exact')))
            with self.subTest(kind=kind):self.assertIsNotNone(task_pair_conflict(s,*s.tasks))
        unqualified=replace(base,members=(replace(base.members[0],eligible_role_ids=frozenset({'接机1'})),))
        self.assertIn('ineligible',{i.code for i in validate_schedule(unqualified,legal)})
        transition=replace(base,minimum_transition_minutes=5)
        self.assertIn('phase_transition_unknown',{i.code for i in validate_schedule(transition,legal)})

    def test_cross_midnight_compares_explicit_absolute_flight_windows(self):
        base=earlier_scene()
        s=replace(base,flights=(replace(base.flights[0],window_start=1380,window_end=1560),
            replace(base.flights[1],window_start=1410,window_end=1500)),
            tasks=(replace(base.tasks[0],start=1380,end=1560),replace(base.tasks[1],start=1410,end=1500)))
        self.assertTrue(flow_target_scope_allows(s,*s.tasks,s.flow_preferences[0]))
        self.assertFalse(validate_schedule(s,solve(s).assignments))

    def test_earlier_prefers_real_target_without_overriding_leave_empty(self):
        base=earlier_scene()
        s=replace(base,tasks=(*base.tasks,Task('missing','A','无人资质岗',600,720,1,'fixed')),
            coverage_priorities=(('接机2','leave-empty'),))
        result=solve(s,allow_partial=True)
        self.assertIn(Assignment('source','m'),result.assignments)
        self.assertNotIn(Assignment('target','m'),result.assignments)

    def test_same_flight_separation_and_nonadjacent_cross_routes_remain_independent(self):
        base=earlier_scene()
        third=Task('third','A','秩序',600,720,1,'mobile',timing_mode='phase',service_stage='boarding')
        s=replace(base,tasks=(*base.tasks,third),members=(replace(base.members[0],eligible_role_ids=frozenset({'接机1','接机2','秩序'})),),
            flow_preferences=(*base.flow_preferences,dict(flightNo='KE0166',arrivalRole='接机2',boardingRoles=['秩序'],
                enabled=True,targetFlightScopes={'秩序':'later-overlap'})),
            work_rules={'pairs':[],'baggage':[],'separationGroups':[dict(id='same',name='同班分人',flightNo='*',roles=['接机1','秩序'],enabled=True)]})
        self.assertIsNone(task_pair_conflict(s,s.tasks[0],s.tasks[1]))
        self.assertIsNone(task_pair_conflict(s,s.tasks[1],s.tasks[2]))
        self.assertEqual('role_separation',task_pair_conflict(s,s.tasks[0],s.tasks[2])[0])
        self.assertIn('role_separation',{i.code for i in validate_schedule(s,tuple(Assignment(t.id,'m') for t in s.tasks))})

    def test_two_cross_links_do_not_grant_a_third_unconfigured_overlap(self):
        base=earlier_scene()
        c=Flight('C','TW616','2026-08-24',640,670)
        third=Task('third','C','秩序',640,670,1,'mobile',timing_mode='phase',service_stage='boarding')
        s=replace(base,flights=(*base.flights,c),tasks=(*base.tasks,third),
            flow_preferences=(*base.flow_preferences,dict(flightNo='KE0166',arrivalRole='接机2',boardingRoles=['秩序'],
                enabled=True,targetFlightScopes={'秩序':'earlier-overlap'})))
        self.assertIsNone(task_pair_conflict(s,s.tasks[0],s.tasks[1]))
        self.assertIsNone(task_pair_conflict(s,s.tasks[1],s.tasks[2]))
        self.assertEqual('overlap',task_pair_conflict(s,s.tasks[0],s.tasks[2])[0])

    def test_shared_pool_does_not_acquire_a_cross_flight_permission(self):
        base=earlier_scene()
        s=replace(base,tasks=(replace(base.tasks[0],covered_flight_ids=('A','B')),base.tasks[1]))
        self.assertIsNotNone(task_pair_conflict(s,*s.tasks))


def earlier_configuration():
    from tests.test_arrival_cross_flight import arrival_configuration
    data=arrival_configuration()
    for row in data['handoffPreferences']:
        row['targetFlightScopes']=dict.fromkeys(row['boardingRoles'],'earlier-overlap')
    data['day']['flowPreferences']=deepcopy(data['handoffPreferences'])
    data['workRules']['forbiddenHandoffs'][0]['targets'].append(dict(role='接机2',flightScope='earlier-overlap'))
    data['day']['workRules']=deepcopy(data['workRules'])
    return data


class EarlierOverlapMigrationTests(unittest.TestCase):
    def test_excel_empty_database_restart_backup_and_actual_four_assignments(self):
        from gate_schedule.workbench import Workbench
        from gate_schedule.excel_io import import_complete_configuration
        with tempfile.TemporaryDirectory(dir=Path.cwd()/'.tmp',prefix='earlier-') as folder:
            folder=Path(folder);w=Workbench(folder/'source.db');data=earlier_configuration()
            s=w.execute(0,'import-complete-configuration',data)
            for role in s['configuration']['roleConfigs']:
                if role['roleName'] not in data['day']['roleNames']:
                    s=w.execute(s['revision'],'save-role',{**role,'requiredCount':0})
            pid=s['selectedPlanId'];formal=None
            for stage in ('source','excel','backup'):
                if stage=='excel':
                    decoded=import_complete_configuration(w.export_complete_configuration())
                    self.assertEqual(data['handoffPreferences'],decoded['handoffPreferences'])
                    self.assertEqual(data['workRules'],decoded['workRules'])
                    w=Workbench(folder/'excel.db');w.execute(0,'import-complete-configuration',decoded);w=Workbench(folder/'excel.db')
                elif stage=='backup':
                    backup=w.backup();w=Workbench(folder/'backup.db');w.restore(0,backup);w=Workbench(folder/'backup.db')
                s=w.snapshot();self.assertEqual(data['handoffPreferences'],s['handoffPreferences'])
                self.assertEqual(data['workRules'],s['workRules'])
                s=w.execute(s['revision'],'solve',{},pid);p=s['plans'][pid]
                self.assertTrue(p['valid'],(stage,p['issues']));self.assertEqual(4,len(p['draft']))
                if formal is None:
                    s=w.execute(s['revision'],'adopt',{},pid);formal=deepcopy(s['plans'][pid]['adopted'])
                else:self.assertEqual(formal,p['adopted'])

    def test_new_scope_cloud_documents_validate_without_normalization_or_writes(self):
        from gate_schedule.browser_runtime import BrowserStore,validate_cloud_documents
        from gate_schedule.group_workspaces import GroupWorkspaces
        groups=GroupWorkspaces(Path('/unused-earlier'),store=BrowserStore())
        w=groups.workbench('A');w.execute(0,'import-complete-configuration',earlier_configuration())
        original=deepcopy(groups.store.documents)
        validate_cloud_documents(groups.store.documents)
        self.assertEqual(original,groups.store.documents)

    def test_unknown_scope_rejected_atomically_and_missing_field_preserves_settings(self):
        from gate_schedule.workbench import Workbench
        with tempfile.TemporaryDirectory(dir=Path.cwd()/'.tmp',prefix='earlier-atomic-') as folder:
            w=Workbench(Path(folder)/'source.db');data=earlier_configuration()
            s=w.execute(0,'import-complete-configuration',data);before=w.backup()
            invalid=deepcopy(s['handoffPreferences']);invalid[0]['targetFlightScopes']['接机1']='overlap-any'
            with self.assertRaises(ValueError):w.execute(s['revision'],'save-handoff-preferences',{'preferences':invalid})
            self.assertEqual(before,w.backup())
            old=deepcopy(data);old.pop('handoffPreferences');old.pop('workRules')
            s=w.execute(s['revision'],'import-complete-configuration',old)
            self.assertEqual(data['handoffPreferences'],s['handoffPreferences'])
            self.assertEqual(data['workRules'],s['workRules'])
