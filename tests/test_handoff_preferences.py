from copy import deepcopy
from dataclasses import replace
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from openpyxl import load_workbook

from gate_schedule.domain import Assignment, Flight, Member, Scenario, Task, handoff_rank
from gate_schedule.solver import solve
from gate_schedule.validator import validate_schedule
from gate_schedule.workbench import Workbench
from gate_schedule.excel_io import import_complete_configuration, export_complete_configuration
from tests.test_complete_excel import example


def preference(flight='*', source='接机2', targets=None, enabled=True):
    return {'flightNo': flight, 'arrivalRole': source, 'boardingRoles': targets or ['登机核验'], 'enabled': enabled}


def scenario():
    roles = ['接机1', '接机2', '登机主验', '登机核验', '登机人证核对/队伍秩序维护']
    tasks = tuple(Task(str(i), 'f', r, 600, 680, 1, 'mobile', service_stage='arrival' if i < 2 else 'boarding', timing_mode='phase', locked_member_ids=('甲',) if i == 0 else ('乙',) if i == 1 else ()) for i, r in enumerate(roles))
    return Scenario('2026-08-02', (Flight('f', 'TW616', '2026-08-02', 600, 680),), tuple(Member(n, n, frozenset(roles), 0) for n in ['甲', '乙', '丙']), tasks, release_policy='task')


class HandoffSolverTests(unittest.TestCase):
    def test_legacy_and_disabled_paths_have_equal_rank_and_custom_roles_work(self):
        legacy=preference(targets=['登机核验','登机主验'])
        for item in [legacy,dict(legacy,ordered=False),dict(legacy,ordered=True,enabled=False)]:
            self.assertEqual(0,handoff_rank([item],'TW616','接机2','登机主验'))
        custom=dict(preference(source='自定义接机岗',targets=['新核验岗','新主验岗']),ordered=True)
        self.assertEqual(0,handoff_rank([custom],'TW616','自定义接机岗','新核验岗'))
        self.assertEqual(1,handoff_rank([custom],'TW616','自定义接机岗','新主验岗'))

    def test_ranked_paths_follow_manual_order_and_fall_back_legally(self):
        sc = scenario()
        for targets in [['登机核验', '登机主验'], ['登机主验', '登机核验']]:
            prefs = [dict(preference(targets=targets), ordered=True)]
            result = solve(sc, handoff_preferences=prefs)
            self.assertFalse(validate_schedule(sc, result.assignments))
            first = next(t for t in sc.tasks if t.role_id == targets[0])
            self.assertIn(Assignment(first.id, '乙'), result.assignments)
            limited = replace(sc, members=tuple(replace(m, eligible_role_ids=m.eligible_role_ids - {targets[0]}) if m.id=='乙' else m for m in sc.members))
            result = solve(limited, handoff_preferences=prefs, allow_partial=True)
            self.assertEqual(5, len(result.assignments))
            self.assertFalse(validate_schedule(limited, result.assignments))
            second = next(t for t in sc.tasks if t.role_id == targets[1])
            self.assertIn(Assignment(second.id, '乙'), result.assignments)

    def test_preferred_path_wins_before_history_and_can_be_changed(self):
        sc = scenario()
        for target in ['登机主验', '登机核验', '登机人证核对/队伍秩序维护']:
            prefs = [preference(targets=[target])]
            result = solve(sc, handoff_preferences=prefs, previous_roles={('乙', target)})
            self.assertIn(result.status, ['OPTIMAL', 'FEASIBLE'])
            self.assertFalse(validate_schedule(sc, result.assignments))
            task = next(t for t in sc.tasks if t.role_id == target)
            self.assertIn(Assignment(task.id, '乙'), result.assignments)
        # Explicit disable permits a fixed alternative; preference never becomes a hard rule.
        sc = replace(sc, tasks=tuple(replace(t, locked_member_ids=('丙',)) if t.id == '3' else t for t in sc.tasks))
        for enabled in [True, False]:
            result = solve(sc, handoff_preferences=[preference(enabled=enabled)])
            self.assertEqual(5, len(result.assignments))
            self.assertFalse(validate_schedule(sc, result.assignments))

    def test_no_qualification_or_conflict_exemption_or_vacancy_for_preference(self):
        sc = scenario()
        sc = replace(sc, members=tuple(replace(m, eligible_role_ids=m.eligible_role_ids - {'登机核验'}) if m.id == '乙' else m for m in sc.members))
        result = solve(sc, handoff_preferences=[preference()], allow_partial=True)
        self.assertEqual(5, len(result.assignments))
        self.assertNotIn(Assignment('3', '乙'), result.assignments)
        self.assertFalse(validate_schedule(sc, result.assignments))
        broken = tuple(a for a in result.assignments if a.task_id != '3') + (Assignment('3', '乙'),)
        self.assertIn('ineligible', [i.code for i in validate_schedule(sc, broken)])
        # A preference referring to another flight cannot affect this flight.
        result = solve(sc, handoff_preferences=[preference(flight='OTHER')])
        self.assertFalse(validate_schedule(sc, result.assignments))


class HandoffConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / '.tmp', prefix='handoff-')
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'state.sqlite3'
        self.wb = Workbench(self.path)
        state = self.wb.execute(0, 'import-complete-configuration', example())
        self.pid = state['selectedPlanId']

    def command(self, action, payload):
        return self.wb.execute(self.wb.snapshot()['revision'], action, payload)

    def test_ranked_order_excel_legacy_and_fresh_device(self):
        prefs = [dict(preference(targets=['登机核验','登机主验']),ordered=True)]
        self.command('save-handoff-preferences', {'preferences':prefs})
        content=self.wb.export_complete_configuration(self.pid)
        data=import_complete_configuration(content)
        self.assertEqual(prefs,data['handoffPreferences'])
        other=Workbench(Path(self.directory.name)/'ranked-new.sqlite3')
        other.execute(0,'import-complete-configuration',data)
        self.assertEqual(prefs,Workbench(Path(self.directory.name)/'ranked-new.sqlite3').snapshot()['handoffPreferences'])
        restored=Workbench(Path(self.directory.name)/'ranked-restored.sqlite3')
        restored.restore(0,other.backup())
        self.assertEqual(prefs,restored.snapshot()['handoffPreferences'])
        book=load_workbook(BytesIO(content));sheet=book['岗位接续偏好']
        self.assertEqual('按顺序优先',sheet.cell(1,5).value)
        sheet.cell(2,5,'错误值');stream=BytesIO();book.save(stream)
        with self.assertRaises(ValueError):import_complete_configuration(stream.getvalue())
        sheet.delete_cols(5);stream=BytesIO();book.save(stream);book.close()
        legacy=import_complete_configuration(stream.getvalue())['handoffPreferences'][0]
        self.assertNotIn('ordered',legacy)
        self.assertEqual(prefs[0]['boardingRoles'],legacy['boardingRoles'])

    def test_invalid_rank_type_rejected_without_write(self):
        before=self.wb.backup()
        with self.assertRaises(ValueError):self.command('save-handoff-preferences', {'preferences':[dict(preference(),ordered=1)]})
        self.assertEqual(before,self.wb.backup())

    def test_latest_preferences_excel_fresh_device_restart_and_backup(self):
        original = deepcopy(self.wb.backup()['state']['plans'])
        prefs = [preference(), preference('*', '接机1', ['登机人证核对/队伍秩序维护'], False)]
        self.command('save-handoff-preferences', {'preferences': prefs})
        self.command('save-display-preferences', {'layout': 'matrix', 'zoom': 120})
        data = import_complete_configuration(self.wb.export_complete_configuration(self.pid))
        self.assertEqual(prefs, data['handoffPreferences'])
        self.assertEqual({'layout': 'matrix', 'zoom': 120}, data['displayPreferences'])
        preview = self.wb.preview_complete_configuration(data)
        self.assertEqual(original, self.wb.backup()['state']['plans'])
        other = Workbench(Path(self.directory.name) / 'device.sqlite3')
        other.execute(0, 'import-complete-configuration', preview['payload'])
        other = Workbench(Path(self.directory.name) / 'device.sqlite3')
        self.assertEqual(prefs, other.snapshot()['handoffPreferences'])
        self.assertEqual(data['displayPreferences'], other.snapshot()['displayPreferences'])
        restored = Workbench(Path(self.directory.name) / 'restore.sqlite3')
        restored.restore(0, other.backup())
        self.assertEqual(prefs, restored.snapshot()['handoffPreferences'])

    def test_missing_old_sheet_preserves_but_empty_new_sheet_clears(self):
        prefs = [preference()]
        self.command('save-handoff-preferences', {'preferences': prefs})
        book = load_workbook(BytesIO(self.wb.export_complete_configuration(self.pid)))
        del book['岗位接续偏好']
        stream = BytesIO(); book.save(stream)
        data = import_complete_configuration(stream.getvalue())
        self.assertNotIn('handoffPreferences', data)
        self.command('import-complete-configuration', data)
        self.assertEqual(prefs, self.wb.snapshot()['handoffPreferences'])
        book.create_sheet('岗位接续偏好').append(['航班号', '接机岗位', '优先登机岗位', '启用'])
        stream = BytesIO(); book.save(stream); book.close()
        data = import_complete_configuration(stream.getvalue())
        self.assertEqual([], data['handoffPreferences'])
        self.command('import-complete-configuration', data)
        self.assertEqual([], Workbench(self.path).snapshot()['handoffPreferences'])

    def test_invalid_values_or_restore_do_not_partially_write(self):
        before = self.wb.backup()
        for prefs in [[preference(), preference()], [preference(targets=['接机2'])], [dict(preference(), enabled='是')], [dict(preference(), boardingRoles=[])]]:
            with self.subTest(prefs=prefs), self.assertRaises(ValueError):
                self.command('save-handoff-preferences', {'preferences': prefs})
            self.assertEqual(before, self.wb.backup())
        bad = deepcopy(before); bad['state']['handoffPreferences'] = [dict(preference(), enabled=1)]
        with self.assertRaises(ValueError): self.wb.restore(before['state']['revision'], bad)
        self.assertEqual(before, self.wb.backup())

    def test_latest_long_term_qualifications_settings_and_inactive_status_export(self):
        people = self.wb.snapshot()['people']
        person = people[0]
        self.command('save-person', {'id': person['id'], 'name': person['name'], 'active': False})
        self.command('save-role', {'roleName': '接机1', 'requiredCount': 3, 'qualifiedMemberIds': [people[1]['id']], 'workMode': 'arrival', 'concurrent': True})
        self.command('save-config', {'rotationEnabled': False, 'rotationMinutes': 95, 'transitionMinutes': 7})
        data = import_complete_configuration(self.wb.export_complete_configuration(self.pid))
        self.assertFalse(data['peopleActive'][person['name']])
        self.assertFalse(next(a['participating'] for a in data['day']['memberAvailability'] if a['memberName'] == person['name']))
        self.assertFalse(data['rotationEnabled'])
        self.assertEqual(95, data['rotationMinutes'])
        self.assertEqual(7, data['day']['transitionMinutes'])
        role = next(r for r in data['day']['roleConfigs'] if r['roleName'] == '接机1')
        self.assertEqual((3, 'arrival', True), (role['requiredCount'], role['workMode'], role['concurrent']))
        self.assertEqual([people[1]['name']], [m['memberName'] for m in data['day']['memberConfigs'] if '接机1' in m['eligibleRoleNames']])
        other = Workbench(Path(self.directory.name) / 'latest.sqlite3')
        other.execute(0, 'import-complete-configuration', data)
        self.assertFalse(next(p['active'] for p in other.snapshot()['people'] if p['name'] == person['name']))

    def test_legacy_implicit_roles_export_as_same_regular_defaults(self):
        backup = self.wb.backup()
        for role in backup['state']['configuration']['roleConfigs']:
            role.pop('workMode', None)
            role.pop('concurrent', None)
        self.wb.restore(backup['state']['revision'], backup)
        snapshot = self.wb.snapshot()
        data = import_complete_configuration(self.wb.export_complete_configuration(self.pid))
        for role in data['day']['roleConfigs']:
            default = snapshot['newRoleDefaults'][role['roleName']]
            self.assertEqual(default['workMode'], role['workMode'])
            self.assertEqual(default['concurrent'], role['concurrent'])

    def test_daily_manual_overrides_and_latest_base_both_survive_device_change(self):
        plan = self.wb.snapshot()['plans'][self.pid]
        day = deepcopy(plan['day'])
        role = next(r for r in day['roleConfigs'] if r['roleName']=='接机1')
        role.update(requiredCount=4, workMode='arrival', concurrent=True)
        member = day['memberConfigs'][0]
        member['eligibleRoleNames'] = [r for r in member['eligibleRoleNames'] if r!='接机1']
        self.wb.execute(self.wb.snapshot()['revision'], 'save-input', {'day':day,'rotationEnabled':False,'rotationMinutes':85}, self.pid)
        self.command('save-role', {'roleName':'接机1','requiredCount':2,'workMode':'arrival','concurrent':False})
        self.command('save-config', {'rotationEnabled':True,'rotationMinutes':95,'transitionMinutes':6})
        data = import_complete_configuration(self.wb.export_complete_configuration(self.pid))
        other = Workbench(Path(self.directory.name) / 'daily-device.sqlite3')
        state = other.execute(0, 'import-complete-configuration', data)
        base = next(r for r in state['configuration']['roleConfigs'] if r['roleName']=='接机1')
        self.assertEqual((2,False),(base['requiredCount'],base['concurrent']))
        current = state['plans'][state['selectedPlanId']]
        daily = next(r for r in current['day']['roleConfigs'] if r['roleName']=='接机1')
        self.assertEqual((4,True),(daily['requiredCount'],daily['concurrent']))
        self.assertFalse(current['rotationEnabled'])
        self.assertEqual(85,current['rotationMinutes'])
        self.assertNotIn('接机1',next(m['eligibleRoleNames'] for m in current['day']['memberConfigs'] if m['memberName']==member['memberName']))
        self.assertIn('接机1',next(p['eligibleRoleNames'] for p in state['people'] if p['name']==member['memberName']))

    def test_renamed_person_exports_current_identity_and_keeps_original_plan(self):
        person = self.wb.snapshot()['people'][0]
        original = deepcopy(self.wb.backup()['state']['plans'])
        self.command('save-person', {'id':person['id'],'name':person['name']+'改','active':True})
        data = import_complete_configuration(self.wb.export_complete_configuration(self.pid))
        self.assertIn(person['name']+'改',data['day']['roster'])
        self.assertNotIn(person['name'],data['day']['roster'])
        self.assertEqual(original,self.wb.backup()['state']['plans'])

    def test_excel_to_empty_workbench_actually_applies_paths_and_allows_manual_exception(self):
        sc = scenario()
        day = {'serviceDate':sc.service_date, 'title':'偏好实排', 'roleNames':[t.role_id for t in sc.tasks],
               'flights':[{'id':'f','flightNo':'TW616','windowStart':'10:00','windowEnd':'11:20'}],
               'roleConfigs':[{'roleName':t.role_id,'requiredCount':1,'workMode':t.service_stage,'concurrent':False} for t in sc.tasks],
               'roster':[m.name for m in sc.members], 'memberConfigs':[{'memberName':m.name,'eligibleRoleNames':sorted(m.eligible_role_ids)} for m in sc.members],
               'memberAvailability':[{'memberName':m.name,'participating':True,'availableFrom':None,'availableUntil':None} for m in sc.members],
               'remarks':[], 'remarksReviewed':True, 'extraOccupancies':[], 'transitionMinutes':0, 'releasePolicy':'task', 'attendanceMode':'duty', 'phaseHandoffs':[],
               'cells':[{'id':t.id,'flightId':'f','flightNo':'TW616','roleName':t.role_id,'rawValue':'','internalNames':[],'externalLabels':[],'semantics':'empty','fixedMemberNames':list(t.locked_member_ids),'requiredCountOverride':None,'workStart':None,'workEnd':None} for t in sc.tasks]}
        prefs = [dict(preference(targets=['登机核验','登机主验']),ordered=True), preference(source='接机1', targets=['登机人证核对/队伍秩序维护'])]
        data = import_complete_configuration(export_complete_configuration({'day':day,'source':'测试','rotationEnabled':False,'rotationMinutes':60,'handoffPreferences':prefs}))
        path = Path(self.directory.name) / 'solve-device.sqlite3'
        other = Workbench(path)
        state = other.execute(0, 'import-complete-configuration', data)
        other = Workbench(path)
        pid = state['selectedPlanId']
        state = other.execute(state['revision'], 'solve', {}, pid)
        plan = state['plans'][pid]
        self.assertTrue(plan['valid'])
        ids = {p['name']:p['id'] for p in state['people']}
        check = next(t for t in plan['schedule']['tasks'] if t['roleId']=='登机核验')
        self.assertIn({'taskId':check['id'],'memberId':ids['乙']}, plan['draft'])
        # An otherwise legal manual exchange is accepted despite departing from the preference.
        main = next(t for t in plan['schedule']['tasks'] if t['roleId']=='登机主验')
        changed = [dict(a, memberId=ids['丙'] if a['taskId']==check['id'] else ids['乙'] if a['taskId']==main['id'] else a['memberId']) for a in plan['draft']]
        state = other.execute(state['revision'], 'assign', {'assignments':changed}, pid)
        self.assertTrue(state['plans'][pid]['valid'])
        state = other.execute(state['revision'], 'adopt', {}, pid)
        self.assertEqual(changed, state['plans'][pid]['adopted']['assignments'])
