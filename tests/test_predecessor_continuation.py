"""Existing downstream rules are referenced, not copied, when a source is prepended."""
from dataclasses import replace
import unittest

from gate_schedule.domain import Assignment,Task,task_pair_conflict
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule
from tests.test_flow_scheduling import flow_scene,preference


class PredecessorContinuationTests(unittest.TestCase):
    def test_preview_is_readonly_and_simplified_rules_survive_export_restart_and_restore(self):
        from copy import deepcopy
        from pathlib import Path
        import tempfile
        from gate_schedule.workbench import Workbench
        from gate_schedule.excel_io import import_complete_configuration
        from tests.test_continuation_chains import baggage_chain_configuration
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp',prefix='predecessor-') as directory:
            root=Path(directory);w=Workbench(root/'source.db');data=baggage_chain_configuration()
            data['handoffPreferences'].append(dict(id='legacy',mode='chain',flightNo='*',arrivalRole='大件1',
                boardingRoles=['接机1','秩序维护','登机查验'],enabled=True,ordered=True,
                targetFlightScopes=dict.fromkeys(['接机1','秩序维护','登机查验'],'same-flight')))
            s=w.execute(0,'import-complete-configuration',data)
            for role in s['configuration']['roleConfigs']:
                if role['roleName'] not in data['day']['roleNames']:
                    s=w.execute(s['revision'],'save-role',{**role,'requiredCount':0})
            pid=s['selectedPlanId'];s=w.execute(s['revision'],'solve',{},pid)
            s=w.execute(s['revision'],'adopt',{},pid);formal=deepcopy(s['plans'][pid]['adopted'])
            original=w.backup();preview=w.snapshot()['handoffSimplification']
            self.assertEqual(original,w.backup(),'读预览不写资料')
            self.assertEqual(s['handoffPreferences'],preview['original'])
            s=w.execute(s['revision'],'save-handoff-preferences',{'preferences':preview['preferences']})
            ordinary=deepcopy(s['handoffPreferences'])
            self.assertFalse(any(r.get('mode')=='chain' for r in ordinary))
            self.assertEqual(formal,s['plans'][pid]['adopted'])
            for stage in ('source','excel','backup'):
                if stage=='excel':
                    decoded=import_complete_configuration(w.export_complete_configuration())
                    self.assertEqual(ordinary,decoded['handoffPreferences'])
                    w=Workbench(root/'excel.db');w.execute(0,'import-complete-configuration',decoded);w=Workbench(root/'excel.db')
                elif stage=='backup':
                    backup=w.backup();w=Workbench(root/'backup.db');w.restore(0,backup);w=Workbench(root/'backup.db')
                s=w.snapshot();self.assertEqual(ordinary,s['handoffPreferences'],stage)
                self.assertNotIn('handoffSimplification',s)
                self.assertEqual(formal,s['plans'][pid]['adopted'],stage)
                s=w.execute(s['revision'],'solve',{},pid);p=s['plans'][pid]
                self.assertTrue(p['valid'],(stage,p['issues']))
                worker=next(m['id'] for m in s['people'] if m['name']=='甲')
                self.assertEqual(4,sum(a['memberId']==worker for a in p['draft']),stage)
                self.assertEqual(5,len(p['draft']),stage)

    def test_legacy_group_can_be_explicitly_simplified_without_overwriting_successors(self):
        from gate_schedule.domain import simplify_handoff_preferences
        from copy import deepcopy
        downstream=preference(['秩序维护','登机查验'],source='接机1')
        downstream['targetFlightScopes']={'秩序维护':'same-flight','登机查验':'same-flight'}
        rows=[downstream,dict(id='old',mode='chain',flightNo='*',arrivalRole='大件1',
            boardingRoles=['接机1','秩序维护'],enabled=True,ordered=True,
            targetFlightScopes={'接机1':'same-flight','秩序维护':'same-flight'})]
        before=deepcopy(rows);result=simplify_handoff_preferences(rows)
        self.assertEqual(before,rows,'预览不改原资料')
        self.assertEqual(downstream['boardingRoles'],result[0]['boardingRoles'])
        self.assertEqual('same-flight',result[0]['targetFlightScopes']['秩序维护'])
        bag=next(r for r in result if r['arrivalRole']=='大件1')
        self.assertEqual(['接机1'],bag['boardingRoles'])
        self.assertFalse(any(r.get('mode')=='chain' or 'id' in r for r in result))

    def test_ambiguous_legacy_scopes_are_rejected_instead_of_expanded(self):
        from gate_schedule.domain import simplify_handoff_preferences
        ordinary={**preference(['接机1'],source='大件1'),'targetFlightScopes':{'接机1':'later-overlap'}}
        chain=dict(id='old',mode='chain',flightNo='*',arrivalRole='大件1',boardingRoles=['接机1'],
            enabled=True,targetFlightScopes={'接机1':'same-flight'})
        with self.assertRaisesRegex(ValueError,'范围'):
            simplify_handoff_preferences([ordinary,chain])
        specific={**chain,'flightNo':'CX937','boardingRoles':['接机1','秩序维护'],
            'targetFlightScopes':{'接机1':'later-overlap','秩序维护':'same-flight'}}
        with self.assertRaisesRegex(ValueError,'来源航班'):
            simplify_handoff_preferences([specific])
        disabled_exception={**chain,'flightNo':'CX937','enabled':False}
        with self.assertRaisesRegex(ValueError,'特例'):
            simplify_handoff_preferences([chain,disabled_exception])

    def test_prepend_baggage_uses_latest_arrival_downstream_without_a_group(self):
        base=flow_scene()
        board=Task('board','A','秩序维护',480,660,1,'mobile',timing_mode='phase',service_stage='boarding')
        other=replace(board,id='other',role_id='登机查验')
        scene=replace(base,tasks=(base.tasks[0],base.tasks[2],board,other),
            members=(replace(base.members[0],eligible_role_ids=base.members[0].eligible_role_ids|{'秩序维护','登机查验'}),),
            rotations=(replace(base.rotations[0],candidate_member_ids=('甲',)),),
            flow_preferences=(preference(['接机1'],source='大件1'),preference(['秩序维护'],source='接机1'),
                preference(['登机查验'],source='秩序维护')))
        result=solve(scene)
        self.assertEqual('OPTIMAL',result.status,result.diagnostics)
        self.assertEqual({Assignment(t.id,'甲') for t in scene.tasks},set(result.assignments))
        self.assertFalse(validate_schedule(scene,result.assignments))
        # The baggage rule remains unchanged when arrival's later link changes.
        changed=replace(scene,flow_preferences=(scene.flow_preferences[0],
            preference(['登机查验'],source='接机1'),preference(['秩序维护'],source='登机查验')))
        revised=solve(changed)
        self.assertEqual(set(result.assignments),set(revised.assignments))
        self.assertEqual(scene.flow_preferences[0],changed.flow_preferences[0])
        self.assertFalse(validate_schedule(changed,revised.assignments))
        # Removing the middle rule removes transitive permission immediately.
        disabled=replace(scene,flow_preferences=(scene.flow_preferences[0],
            preference(['秩序维护'],source='接机1',enabled=False),scene.flow_preferences[2]))
        self.assertIsNotNone(task_pair_conflict(disabled,disabled.tasks[0],board))
        self.assertTrue(validate_schedule(disabled,result.assignments))

    def test_inherited_work_cannot_bypass_a_first_to_last_prohibition(self):
        base=flow_scene();board=Task('board','A','秩序维护',480,660,1,'mobile',timing_mode='phase',service_stage='boarding')
        scene=replace(base,tasks=(base.tasks[0],base.tasks[2],board),
            members=(replace(base.members[0],eligible_role_ids=base.members[0].eligible_role_ids|{'秩序维护'}),),
            rotations=(replace(base.rotations[0],candidate_member_ids=('甲',)),),
            flow_preferences=(preference(['接机1'],source='大件1'),preference(['秩序维护'],source='接机1')))
        values=solve(scene).assignments
        blocked=replace(scene,work_rules={'pairs':[],'baggage':[],
            'forbiddenHandoffs':[dict(flightNo='*',sourceRole='大件1',
                targets=[dict(role='秩序维护',flightScope='same-flight')],enabled=True)]})
        self.assertIn('forbidden_handoff',{i.code for i in validate_schedule(blocked,values)})
