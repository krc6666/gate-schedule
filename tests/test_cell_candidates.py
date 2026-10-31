from copy import deepcopy
import unittest

import test_workspace as fixtures
from test_work_phases import phase_day
from gate_schedule.workbench import Workbench


class CellCandidateTests(unittest.TestCase):
    setUp = fixtures.WorkspaceTests.setUp
    tearDown = fixtures.WorkspaceTests.tearDown
    command = fixtures.WorkspaceTests.command

    def setup_day(self, role='登机核验'):
        state = self.command('new-plan', {'serviceDate': '2026-08-02'})
        pid = state['selectedPlanId']
        day = phase_day()
        for member in day['memberConfigs']:
            member['eligibleRoleNames'] = list(member['eligibleRoleNames'])
        if role != '登机核验':
            day['roleNames'][2] = role
            day['roleConfigs'][2]['roleName'] = role
            day['cells'][2].update(id=role, roleName=role)
            for member in day['memberConfigs']:
                member['eligibleRoleNames'][2] = role
        day['cells'][2]['requiredCountOverride'] = 0
        self.command('save-input', {'day': day}, pid)
        self.command('edit-cell', {'cellId': '接机1', 'text': '甲'}, pid)
        self.command('edit-cell', {'cellId': '登机主验', 'text': '乙'}, pid)
        return pid, day

    def test_zero_demand_any_role_checks_other_tasks_without_creating_demand(self):
        for role in ('登机核验', '登机查验（VIP/F/优先通道）'):
            with self.subTest(role=role):
                pid, _ = self.setup_day(role)
                before = self.workspace.backup()
                options = {c['name']: c for c in self.workspace.cell_candidates(pid, role)}
                self.assertTrue(options['甲']['available'])
                self.assertEqual('continuation', options['甲']['status'])
                self.assertTrue(options['甲']['requiresConfiguration'])
                self.assertIn('待确认', options['甲']['label'])
                self.assertFalse(options['乙']['available'])
                self.assertIn('登机主验', '；'.join(options['乙']['reasons']))
                self.assertEqual(before, self.workspace.backup())
                self.assertEqual(before, Workbench(self.path).backup())

    def test_missing_timed_facts_never_labels_roster_available(self):
        pid, day = self.setup_day()
        day['roleConfigs'][2]['workMode'] = 'timed'
        self.command('save-input', {'day': day}, pid)
        before = self.workspace.backup()
        candidates = self.workspace.cell_candidates(pid, '登机核验')
        self.assertTrue(candidates)
        self.assertTrue(all(not c['available'] and c['status'] == 'unverified' for c in candidates))
        self.assertTrue(all('时间' in '；'.join(c['reasons']) for c in candidates))
        self.assertEqual(before, self.workspace.backup())

    def test_qualification_and_attendance_apply_to_temporary_roles(self):
        pid, day = self.setup_day()
        day['memberConfigs'][0]['eligibleRoleNames'].remove('登机核验')
        day['memberAvailability'][1]['participating'] = False
        self.command('save-input', {'day': day}, pid)
        candidates = {c['name']: c for c in self.workspace.cell_candidates(pid, '登机核验')}
        self.assertFalse(candidates['甲']['available'])
        self.assertIn('资格', '；'.join(candidates['甲']['reasons']))
        self.assertFalse(candidates['乙']['available'])
        self.assertIn('参加', '；'.join(candidates['乙']['reasons']))

    def test_existing_task_matches_slot_candidates_and_unknown_cell_rejects(self):
        pid, _ = self.setup_day()
        self.assertEqual(self.workspace.candidates(pid, 'imported-接机1'), self.workspace.cell_candidates(pid, '接机1'))
        with self.assertRaises(ValueError):
            self.workspace.cell_candidates(pid, '不存在')

    def test_http_candidates_are_read_only_and_stale_targets_return_actionable_errors(self):
        from fastapi.testclient import TestClient
        from gate_schedule.server import create_server_app
        pid, _ = self.setup_day()
        before = self.workspace.backup()
        with TestClient(create_server_app(database_path=self.path), raise_server_exceptions=False) as client:
            response = client.get(f'/api/workspace/cell-candidates/{pid}/登机核验')
            self.assertEqual(200, response.status_code)
            self.assertEqual(before['state']['revision'], response.json()[0]['revision'])
            self.assertEqual(422, client.get(f'/api/workspace/cell-candidates/{pid}/不存在').status_code)
            self.assertEqual(422, client.get(f'/api/workspace/candidates/{pid}/旧任务').status_code)
            self.assertEqual(404, client.get('/api/workspace/cell-candidates/旧计划/旧格').status_code)
        self.assertEqual(before, self.workspace.backup())

    def test_provisional_multiple_positions_and_rotation_require_configuration(self):
        pid, day = self.setup_day()
        candidates = self.workspace.cell_candidates(pid, '登机核验', 1)
        self.assertTrue(all(not c['available'] for c in candidates))
        day['roleNames'].append('大件行李拦截')
        day['roleConfigs'].append({'roleName': '大件行李拦截', 'requiredCount': 0})
        day['cells'].append({**day['cells'][0], 'id': '大件', 'roleName': '大件行李拦截', 'requiredCountOverride': 0})
        self.command('save-input', {'day': day}, pid)
        self.assertTrue(all(not c['available'] for c in self.workspace.cell_candidates(pid, '大件')))

    def test_legacy_split_changes_are_unverified_and_do_not_reinterpret_assignments(self):
        pid, day = self.setup_day()
        day['releasePolicy'] = 'flight'
        day['cells'][0]['requiredCountOverride'] = 0
        day['flights'].append({'id': 'B', 'flightNo': 'B', 'windowStart': '09:00', 'windowEnd': '11:00'})
        day['cells'].extend({**cell, 'id': 'B-' + cell['id'], 'flightId': 'B', 'flightNo': 'B', 'requiredCountOverride': 0} for cell in list(day['cells']))
        self.command('save-input', {'day': day}, pid)
        self.command('edit-cell', {'cellId': '登机主验', 'text': '乙'}, pid)
        before = self.workspace.backup()
        candidates = self.workspace.cell_candidates(pid, 'B-接机1')
        self.assertTrue(all(not c['available'] and c['status'] == 'unverified' for c in candidates))
        self.assertTrue(all('分段' in '；'.join(c['reasons']) for c in candidates))
        self.assertEqual(before, self.workspace.backup())
