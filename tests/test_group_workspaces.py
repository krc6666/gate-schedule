from copy import deepcopy
from pathlib import Path
from io import BytesIO
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from gate_schedule.workbench import Workbench
from gate_schedule.group_workspaces import GroupWorkspaces
from gate_schedule.server import create_server_app
from gate_schedule.excel_io import import_complete_configuration
from tests.test_balance_configuration import configuration


ROOT = Path(__file__).resolve().parents[1]


class GroupWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=ROOT/'.tmp', prefix='groups-')
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)/'state.sqlite3'
        self.old = Workbench(self.path)
        self.old.execute(0, 'import-complete-configuration', configuration())
        self.before = self.old.backup()
        self.groups = GroupWorkspaces(self.path)
        self.groups.initialize()

    def test_existing_a_preserved_b_empty_and_opposite_roster(self):
        a, b = self.groups.workbench('A'), self.groups.workbench('B')
        self.assertEqual(self.before, a.backup())
        bs = b.snapshot()
        self.assertEqual([], bs['people'])
        self.assertEqual({}, bs['plans'])
        self.assertEqual(a.snapshot()['flightTemplates'], bs['flightTemplates'])
        self.assertEqual(a.snapshot()['configuration'], bs['configuration'])
        from datetime import date
        self.assertEqual(1, (date.fromisoformat(bs['dutyRoster']['anchorDate']) -
                            date.fromisoformat(a.snapshot()['dutyRoster']['anchorDate'])).days)

    def test_same_names_rules_counts_history_and_restart_are_isolated(self):
        b = self.groups.workbench('B')
        b.execute(b.snapshot()['revision'], 'save-person', {'name': '甲', 'eligibleRoleNames': ['登机主验']})
        a_before = self.groups.workbench('A').backup()
        b.execute(b.snapshot()['revision'], 'save-role', {'roleName': '登机主验', 'requiredCount': 3})
        self.assertEqual(a_before, self.groups.workbench('A').backup())
        self.assertEqual([], b.priority_statistics()['roles'])
        restored = GroupWorkspaces(self.path)
        self.assertEqual('甲', restored.workbench('B').snapshot()['people'][0]['name'])
        self.assertNotEqual(restored.workbench('A').snapshot()['people'][0]['id'],
                            restored.workbench('B').snapshot()['people'][0]['id'])
        self.assertEqual(self.before['state']['plans'], restored.workbench('A').backup()['state']['plans'])

    def test_shared_flight_times_and_weekly_presets_not_group_role_rules(self):
        a, b = self.groups.workbench('A'), self.groups.workbench('B')
        b.execute(b.snapshot()['revision'], 'save-flight-template', {
            'flightNo': 'A100', 'windowStart': '09:00', 'windowEnd': '10:00',
            'roles': [{'roleName': '登机主验', 'requiredCount': 3}]})
        ash, bsh = a.snapshot(), b.snapshot()
        self.assertEqual('09:00', next(f for f in ash['flightTemplates'] if f['flightNo'] == 'A100')['windowStart'])
        self.assertNotEqual(next(f for f in ash['flightLibrary'] if f['flightNo'] == 'A100')['roles'],
                            next(f for f in bsh['flightLibrary'] if f['flightNo'] == 'A100')['roles'])
        a.execute(ash['revision'], 'save-weekly-flights', {'weekday': '0', 'flights': ash['flightTemplates']})
        self.assertEqual(a.snapshot()['weeklyFlightPresets'], b.snapshot()['weeklyFlightPresets'])

    def test_cross_group_plan_and_stale_shared_write_rejected_without_changes(self):
        a, b = self.groups.workbench('A'), self.groups.workbench('B')
        old_b = b.snapshot()['revision']
        a.execute(a.snapshot()['revision'], 'save-flight-template',
                  {'flightNo': 'A100', 'windowStart': '08:00', 'windowEnd': '09:00'})
        before = self.groups.backup()
        with self.assertRaises(ValueError):
            b.execute(old_b, 'save-person', {'name': '不应写入'})
        with self.assertRaises(ValueError):
            b.execute(b.snapshot()['revision'], 'select-plan', {}, a.snapshot()['selectedPlanId'])
        self.assertEqual(before, self.groups.backup())

    def test_independent_group_or_preference_race_retries_without_lost_changes(self):
        original = self.groups.store.compare_and_save
        for action in ('preference', 'other-group', 'same-group'):
            a = self.groups.workbench('A')
            revision = a.snapshot()['revision']
            raced = False
            def save(key, expected, value):
                nonlocal raced
                if not raced:
                    raced = True
                    if action == 'preference':
                        self.groups.select('B')
                    else:
                        other = self.groups.workbench('A' if action == 'same-group' else 'B')
                        other.execute(other.snapshot()['revision'], 'save-person', {'name': action})
                original(key, expected, value)
            with patch.object(self.groups.store, 'compare_and_save', side_effect=save):
                if action == 'same-group':
                    with self.assertRaises(ValueError):
                        a.execute(revision, 'save-person', {'name': 'must not overwrite'})
                else:
                    a.execute(revision, 'save-person', {'name': action+' A saved'})
            self.assertEqual('B', self.groups.info()['activeGroupId'])
        self.assertIn('other-group', [p['name'] for p in self.groups.workbench('B').snapshot()['people']])
        self.assertIn('same-group', [p['name'] for p in a.snapshot()['people']])
        self.assertNotIn('must not overwrite', [p['name'] for p in a.snapshot()['people']])

    def test_all_groups_configuration_roundtrip_front_edits_restart_and_use(self):
        b = self.groups.workbench('B')
        cfg = configuration()
        cfg['peopleIds'] = {'甲': 'b-a', '乙': 'b-b'}
        cfg['balanceSettings']['priorityAdjustments'] = []
        cfg['balanceSettings']['priorityFlightRoles'] = [{'flightNo': 'A100', 'roleName': '登机主验'}]
        b.execute(b.snapshot()['revision'], 'import-complete-configuration', cfg)
        b.execute(b.snapshot()['revision'], 'adjust-priority-count', {
            'month': '2026-08', 'memberId': 'b-a', 'flightNo': 'A100', 'roleName': '登机主验', 'delta': 1})
        content = self.groups.export_configuration('B')
        decoded = import_complete_configuration(content)
        decoded['rotationMinutes'] = 45
        for role in decoded['day']['roleConfigs']:
            if role['roleName'] != '登机主验':
                role['requiredCount'] = 0
        target = GroupWorkspaces(Path(self.directory.name)/'target.sqlite3')
        target.initialize()
        preview = target.preview_configuration('B', decoded)
        target.import_configuration('B', preview['expectedRevision'], preview['payload'])
        target = GroupWorkspaces(Path(self.directory.name)/'target.sqlite3')
        for identity, plan in self.groups.workbench('A').backup()['state']['plans'].items():
            self.assertEqual(plan, target.workbench('A').backup()['state']['plans'][identity])
        self.assertEqual(1, target.workbench('B').priority_statistics('2026-08')['totals'][0]['count'])
        self.assertEqual(45, target.workbench('B').snapshot()['configuration']['rotationMinutes'])
        a_before = target.workbench('A').backup()
        state = target.workbench('B').snapshot()
        target.workbench('B').execute(state['revision'], 'solve', {}, state['selectedPlanId'])
        self.assertEqual(a_before, target.workbench('A').backup())

    def test_backup_restore_all_groups_and_legacy_restore_only_selected(self):
        b = self.groups.workbench('B')
        b.execute(b.snapshot()['revision'], 'save-person', {'name': 'B独有'})
        self.groups.select('B')
        backup = self.groups.backup()
        target = GroupWorkspaces(Path(self.directory.name)/'restored.sqlite3')
        target.initialize()
        target.restore('A', target.workbench('A').snapshot()['revision'], backup)
        self.assertEqual('B', target.info()['activeGroupId'])
        before_a = target.workbench('A').backup()
        target.restore('B', target.workbench('B').snapshot()['revision'], self.before)
        self.assertEqual(before_a, target.workbench('A').backup())
        self.assertEqual(2, len(target.workbench('B').snapshot()['people']))
        damaged = deepcopy(backup)
        del damaged['groups']['B']
        before = target.backup()
        with self.assertRaises(ValueError):
            target.restore('A', target.workbench('A').snapshot()['revision'], damaged)
        self.assertEqual(before, target.backup())

    def test_http_tabs_keep_explicit_scope_and_invalid_groups_never_fall_back(self):
        static = Path(self.directory.name)/'static'
        static.mkdir()
        (static/'index.html').write_text('test', encoding='utf-8')
        client = TestClient(create_server_app(static, self.path))
        self.assertEqual(200, client.get('/api/groups').status_code)
        self.assertEqual([], client.get('/api/groups/B/workspace').json()['people'])
        self.assertEqual(200, client.post('/api/groups/select', json={'groupId': 'B'}).status_code)
        self.assertEqual(2, len(client.get('/api/groups/A/workspace').json()['people']))
        self.assertEqual(2, len(client.get('/api/workspace').json()['people']))
        self.assertEqual(422, client.post('/api/groups/select', json={'groupId': 'C'}).status_code)
        self.assertEqual(404, client.get('/api/groups/C/workspace').status_code)

    def test_http_full_backup_requires_current_preview_and_legacy_runs_share_a(self):
        from gate_schedule.backup_excel import import_backup
        static = Path(self.directory.name)/'static-backup'
        static.mkdir()
        (static/'index.html').write_text('test', encoding='utf-8')
        client = TestClient(create_server_app(static, self.path))
        content = client.get('/api/groups/A/workspace/backup.xlsx').content
        self.assertEqual(self.groups.backup(), import_backup(content))
        preview = client.post('/api/groups/A/workspace/data/preview', content=content).json()
        b = self.groups.workbench('B')
        b.execute(b.snapshot()['revision'], 'save-person', {'name': '预览后B组新增'})
        before = self.groups.backup()
        path = '/api/groups/A/workspace/restore?expectedRevision=' + str(preview['expectedRevision'])
        self.assertEqual(422, client.post(path, content=content).status_code)
        self.assertEqual(422, client.post(path+'&expectedGroupsRevision='+str(preview['expectedGroupsRevision']), content=content).status_code)
        self.assertEqual(before, self.groups.backup())
        a = self.groups.workbench('A').snapshot()
        run = client.post('/api/workspace/runs', json={
            'expectedRevision': a['revision'], 'action': 'solve', 'planId': a['selectedPlanId']}).json()
        self.assertEqual(200, client.get('/api/groups/A/workspace/runs/'+run['id']).status_code)
        self.assertEqual(404, client.get('/api/groups/B/workspace/runs/'+run['id']).status_code)
        deadline = time.monotonic()+15
        while time.monotonic() < deadline:
            result = client.get('/api/groups/A/workspace/runs/'+run['id']).json()
            if result['status'] != 'running':
                break
            time.sleep(.02)
        self.assertEqual('completed', result['status'], result)

    def test_visible_excel_edit_missing_attachment_and_stale_other_group_are_atomic(self):
        book = load_workbook(BytesIO(self.groups.export_configuration('A')))
        for row in book['岗位配置'].iter_rows(min_row=2):
            if row[0].value == '登机主验':
                row[1].value = 2
        stream = BytesIO()
        book.save(stream)
        decoded = import_complete_configuration(stream.getvalue())
        preview = self.groups.preview_configuration('A', decoded)
        before = self.groups.backup()
        with patch.object(self.groups.store, 'compare_and_save', side_effect=OSError('磁盘失败')):
            with self.assertRaises(OSError):
                self.groups.import_configuration('A', preview['expectedRevision'], preview['payload'])
        self.assertEqual(before, self.groups.backup())
        b = self.groups.workbench('B')
        b.execute(b.snapshot()['revision'], 'save-person', {'name': '预览后新增'})
        before = self.groups.backup()
        with self.assertRaises(ValueError):
            self.groups.import_configuration('A', preview['expectedRevision'], preview['payload'])
        self.assertEqual(before, self.groups.backup())
        preview = self.groups.preview_configuration('A', decoded)
        state = self.groups.import_configuration('A', preview['expectedRevision'], preview['payload'])
        self.assertEqual(2, next(r for r in state['configuration']['roleConfigs'] if r['roleName'] == '登机主验')['requiredCount'])
        self.assertEqual(1, next(r for r in self.groups.workbench('B').snapshot()['configuration']['roleConfigs'] if r['roleName'] == '登机主验')['requiredCount'])
        del book['_班组快照']
        stream = BytesIO()
        book.save(stream)
        book.close()
        with self.assertRaises(ValueError):
            import_complete_configuration(stream.getvalue())

    def test_background_runs_history_and_statistics_never_follow_group_preference(self):
        static = Path(self.directory.name)/'static-runs'
        static.mkdir()
        (static/'index.html').write_text('test', encoding='utf-8')
        client = TestClient(create_server_app(static, self.path))
        b = self.groups.workbench('B')
        cfg = configuration()
        cfg['balanceSettings']['priorityAdjustments'] = []
        cfg['balanceSettings']['priorityFlightRoles'] = [{'flightNo': 'A100', 'roleName': '登机主验'}]
        b.execute(b.snapshot()['revision'], 'import-complete-configuration', cfg)
        a_before = self.groups.workbench('A').backup()
        state = b.snapshot()
        run = client.post('/api/groups/B/workspace/runs', json={
            'expectedRevision': state['revision'], 'action': 'solve', 'planId': state['selectedPlanId']}).json()
        client.post('/api/groups/select', json={'groupId': 'A'})
        self.assertEqual(404, client.get('/api/groups/A/workspace/runs/'+run['id']).status_code)
        deadline = time.monotonic()+15
        while time.monotonic() < deadline:
            result = client.get('/api/groups/B/workspace/runs/'+run['id']).json()
            if result['status'] != 'running':
                break
            time.sleep(.02)
        self.assertEqual('completed', result['status'], result)
        state = b.snapshot()
        b.execute(state['revision'], 'adopt', {}, state['selectedPlanId'])
        self.assertEqual(a_before, self.groups.workbench('A').backup())
        self.assertEqual(1, sum(t['count'] for t in b.priority_statistics('2026-08')['totals']))
        self.assertEqual(0, sum(t['count'] for t in self.groups.workbench('A').role_statistics('2026-08')['totals']))
        self.assertEqual(200, client.get('/api/groups/B/duty-statistics?month=2026-08').status_code)
        self.assertEqual(200, client.get('/api/groups/B/role-statistics?month=2026-08').status_code)
