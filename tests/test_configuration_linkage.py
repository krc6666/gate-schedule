"""Saved settings must visibly reach an existing schedule, not only new plans."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from gate_schedule.workbench import Workbench
from gate_schedule.excel_io import import_complete_configuration
from gate_schedule.configuration_status import generation_context, configuration_status
from gate_schedule.day_document import imported_day_from_dict
from gate_schedule.imported_schedule import imported_day_to_scenario
from tests.test_baggage_window import baggage_day
from tests.test_balance_configuration import configuration


class ConfigurationLinkageTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory(dir=Path.cwd()/'.tmp', prefix='configuration-linkage-')
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name)/'state.sqlite3'
        self.wb = Workbench(self.path)
        state = self.wb.execute(0, 'import-complete-configuration', configuration())
        self.pid = state['selectedPlanId']
        for role in state['configuration']['roleConfigs']:
            if role['roleName'] != '登机主验':
                self.command('save-role', {'roleName':role['roleName'], 'requiredCount':0})
        self.command('solve', {})

    def command(self, action, payload):
        return self.wb.execute(self.wb.snapshot()['revision'], action, payload, self.pid)

    def test_changed_template_must_mark_existing_schedule_outdated(self):
        self.command('archive-only', {})
        before = deepcopy(self.wb.snapshot()['plans'][self.pid])
        template = deepcopy(self.wb.snapshot()['flightLibrary'][0])
        template.update(windowStart='09:00', windowEnd='10:00')
        state = self.command('save-flight-template', template)
        status = state['plans'][self.pid].get('configurationStatus', {})
        self.assertTrue(status.get('stale'), 'saved configuration change must be visible on the schedule')
        self.assertIn('航班与岗位', status.get('changed', []))
        self.assertEqual(before['draft'], state['plans'][self.pid]['draft'])
        self.assertEqual(before['adopted'], state['plans'][self.pid]['adopted'])
        self.assertTrue(Workbench(self.path).snapshot()['plans'][self.pid]['configurationStatus']['stale'])

    def test_display_only_changes_do_not_mark_business_rules_outdated(self):
        status = self.command('save-display-preferences', {'zoom':90, 'layout':'matrix'})['plans'][self.pid].get('configurationStatus')
        self.assertIsNotNone(status)
        self.assertFalse(status['stale'])

    def test_each_business_category_changes_and_same_values_do_not(self):
        state=self.wb.backup()['state'];plan=state['plans'][self.pid]
        self.assertFalse(configuration_status(state,plan)['stale'])
        for key,category in [('flightLibrary','航班与岗位'),('people','人员与资质'),
                ('handoffPreferences','接续兼职'),('workRules','大件与岗位限制'),('balanceSettings','保岗与均衡')]:
            copy=deepcopy(state)
            if key=='people':copy[key][0]['eligibleRoleNames']=[]
            elif key=='flightLibrary':copy[key][0]['windowStart']='07:00'
            elif key=='handoffPreferences':copy[key]=[{'probe':True}]
            elif key=='workRules':copy[key]={'pairs':[],'baggage':[],'baggageWindow':None}
            else:copy[key]['flightCountGap']+=1
            with self.subTest(key=key):self.assertIn(category,configuration_status(copy,plan)['changed'])
        copy=deepcopy(plan);copy['rotationMinutes']+=1
        self.assertIn('当天输入',configuration_status(state,copy)['changed'])
        self.command('save-flight-template',deepcopy(state['flightLibrary'][0]))
        self.assertFalse(self.wb.snapshot()['plans'][self.pid]['configurationStatus']['stale'])

    def test_archive_cancel_failure_and_new_generation_have_distinct_receipts(self):
        self.command('archive-only',{})
        old_formal=deepcopy(self.wb.backup()['state']['plans'][self.pid]['adopted'])
        template=deepcopy(self.wb.snapshot()['flightLibrary'][0]);template['windowEnd']='16:00'
        self.command('save-flight-template',template)
        self.command('archive-only',{})
        self.assertTrue(self.wb.snapshot()['plans'][self.pid]['configurationStatus']['stale'])
        before=self.wb.backup()
        day=deepcopy(before['state']['plans'][self.pid]['day'])
        for row in day['memberAvailability']:row['participating']=False
        with self.assertRaises(ValueError):self.command('prepare-solve',{'day':day})
        self.assertEqual(before,self.wb.backup())
        fresh=self.command('solve',{})['plans'][self.pid]
        self.assertFalse(fresh['configurationStatus']['stale'])
        self.assertTrue(fresh['formalConfigurationStatus']['stale'])
        self.assertEqual(old_formal,fresh['adopted'])
        canceled=self.command('cancel-preview',{})['plans'][self.pid]
        self.assertTrue(canceled['configurationStatus']['stale'])

    def test_backup_excel_restart_and_legacy_receipts(self):
        self.command('archive-only',{})
        target=Workbench(self.path.parent/'restore.db')
        target.restore(0,self.wb.backup())
        self.assertFalse(target.snapshot()['plans'][self.pid]['configurationStatus']['stale'])
        payload=import_complete_configuration(self.wb.export_complete_configuration(self.pid))
        device=Workbench(self.path.parent/'excel.db')
        state=device.execute(0,'import-complete-configuration',payload)
        found=[p for p in state['plans'].values() if p.get('configurationBaseline')]
        self.assertTrue(found,'complete Excel history must retain generation receipts')
        for p in found:self.assertFalse(p['configurationStatus']['stale'])
        backup=self.wb.backup();backup['state']['plans'][self.pid].pop('configurationBaseline')
        target.restore(target.snapshot()['revision'],backup)
        self.assertTrue(target.snapshot()['plans'][self.pid]['configurationStatus']['stale'])

    def test_corrupt_receipts_are_rejected_atomically(self):
        original=self.wb.backup()
        for location in ['draft','formal','preview']:
            backup=deepcopy(original);p=backup['state']['plans'][self.pid]
            if location=='formal':
                self.command('archive-only',{});backup=self.wb.backup();p=backup['state']['plans'][self.pid]
                target=p['adopted'];field='configurationBaseline'
            elif location=='preview':target=p['preview'];field='previousConfigurationBaseline'
            else:target=p;field='configurationBaseline'
            target[field]={'version':1,'categories':{'bad':'broken'}}
            before=self.wb.backup()
            with self.subTest(location=location),self.assertRaises(ValueError):self.wb.restore(before['state']['revision'],backup)
            self.assertEqual(before,self.wb.backup())

    def test_saved_flight_windows_are_not_backdated_again_for_new_baggage_calculations(self):
        payload = configuration()
        day = payload['day']
        day['roleNames']=['大件行李拦截']
        day['roleConfigs']=[{'roleName':'大件行李拦截','requiredCount':1,'workMode':'continuous','concurrent':True}]
        day['flights']=[{'id':'M','flightNo':'MF8683','windowStart':'15:40','windowEnd':'16:40'},
                        {'id':'F','flightNo':'FD573','windowStart':'17:25','windowEnd':'18:25'}]
        day['cells']=[{'id':f['id'],'flightId':f['id'],'flightNo':f['flightNo'],'roleName':'大件行李拦截',
                       'rawValue':'','internalNames':[],'externalLabels':[],'semantics':'empty'} for f in day['flights']]
        for row in day['memberConfigs']: row['eligibleRoleNames']=['大件行李拦截']
        payload.pop('balanceSettings')
        payload['workRules']={'pairs':[],'baggage':[], 'baggageWindow':{'startBeforeMinutes':180,'endBeforeMinutes':60,'rotationMinutes':60,'remainderToleranceMinutes':15}}
        state=self.command('import-complete-configuration',payload)
        self.pid=state['selectedPlanId']
        result=self.command('solve',{})['plans'][self.pid]
        groups=result['baggageCoverage']['groups']
        self.assertEqual([(['MF8683'],'15:40','16:40',1),(['FD573'],'17:25','18:25',1)],
                         [(g['flights'],g['start'],g['end'],g['requiredPeople']) for g in groups])

    def test_window_basis_boundary_cross_day_and_offsets_are_explicit(self):
        for start,end,expected in [('17:25','18:25',1),('17:25','18:40',1),
                ('17:25','18:41',2),('23:30','次日00:46',2)]:
            day=baggage_day([dict(id='F',flightNo='FD573',windowStart=start,windowEnd=end,departureTime='次日03:00')])
            day['workRules']['baggageWindow'].update(timeBasis='flight-window',remainderToleranceMinutes=15)
            scenario,issues=imported_day_to_scenario(imported_day_from_dict(day))
            with self.subTest(start=start,end=end):
                self.assertEqual([],issues)
                self.assertEqual(expected,len(scenario.rotations))
                from gate_schedule.domain import parse_minutes
                self.assertEqual(parse_minutes(start),min(t.start for t in scenario.tasks))
                self.assertEqual(parse_minutes(end),max(t.end for t in scenario.tasks))

    def test_time_basis_excel_and_backup_survive_actual_use_and_old_import(self):
        payload=configuration();payload['workRules']={'pairs':[],'baggage':[],
            'baggageWindow':dict(timeBasis='flight-window',startBeforeMinutes=180,endBeforeMinutes=60,rotationMinutes=60,remainderToleranceMinutes=15)}
        day = payload['day']
        role = '大件行李拦截'
        day['roleNames'] = [role]
        day['roleConfigs'] = [dict(roleName=role, requiredCount=1, workMode='continuous', concurrent=True)]
        day['flights'] = [dict(id='F', flightNo='FD573', windowStart='17:25', windowEnd='18:25')]
        day['cells'] = [dict(id='bag', flightId='F', flightNo='FD573', roleName=role,
                             rawValue='', internalNames=[], externalLabels=[], semantics='empty')]
        for member in day['memberConfigs']:
            member['eligibleRoleNames'] = [role]
        payload.pop('balanceSettings')
        self.command('import-complete-configuration',payload)
        for configured in self.wb.snapshot()['configuration']['roleConfigs']:
            if configured['roleName'] != role:
                self.command('save-role', dict(roleName=configured['roleName'], requiredCount=0))
        for basis in ['flight-window','departure-offsets']:
            raw=self.wb.backup();raw['state']['workRules']['baggageWindow']['timeBasis']=basis
            # This transfer test has one flight; the unrelated setup fixture's
            # exact flight-count equality would intentionally leave it vacant.
            raw['state']['balanceSettings']['flightCountGap'] = None
            self.wb.restore(self.wb.snapshot()['revision'],raw)
            data=import_complete_configuration(self.wb.export_complete_configuration())
            self.assertEqual(basis,data['workRules']['baggageWindow']['timeBasis'])
            target=Workbench(self.path.parent/(basis+'.db'))
            target.execute(0,'import-complete-configuration',data)
            target=Workbench(self.path.parent/(basis+'.db'))
            self.assertEqual(basis,target.snapshot()['workRules']['baggageWindow']['timeBasis'])
            expected = ('17:25', '18:25', 1) if basis == 'flight-window' else ('15:25', '17:25', 2)
            def check_actual_use(workbench):
                state = workbench.snapshot()
                plan_id = state['selectedPlanId']
                result = workbench.execute(state['revision'], 'solve', {}, plan_id)['plans'][plan_id]
                self.assertEqual([(['FD573'], *expected)], [
                    (g['flights'], g['start'], g['end'], g['requiredPeople'])
                    for g in result['baggageCoverage']['groups']])
                self.assertTrue(result['valid'], result['issues'])
            check_actual_use(target)
            restored_path = self.path.parent/(basis+'-backup.db')
            restored = Workbench(restored_path)
            restored.restore(0, target.backup())
            check_actual_use(Workbench(restored_path))
            legacy=deepcopy(data);legacy['workRules']['baggageWindow'].pop('timeBasis')
            state=target.execute(target.snapshot()['revision'],'import-complete-configuration',legacy)
            self.assertEqual(basis,state['workRules']['baggageWindow']['timeBasis'],'old Excel preserves explicitly chosen target basis')
            check_actual_use(target)

    def test_older_rule_editor_missing_basis_preserves_explicit_choice(self):
        state=self.wb.snapshot()
        window=dict(timeBasis='departure-offsets',startBeforeMinutes=180,endBeforeMinutes=60,rotationMinutes=60)
        payload={'configuration':state['configuration'],'balanceSettings':state['balanceSettings'],'preferences':state['handoffPreferences'],
                 'workRules':{'pairs':[],'baggage':[],'baggageWindow':window}}
        self.command('save-rules',payload)
        payload['workRules']['baggageWindow'].pop('timeBasis')
        saved=self.command('save-rules',payload)
        self.assertEqual('departure-offsets',saved['workRules']['baggageWindow']['timeBasis'])
