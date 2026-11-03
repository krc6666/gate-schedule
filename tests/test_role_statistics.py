from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from gate_schedule.domain import Flight, Member, Scenario, Task
from gate_schedule.io import scenario_to_dict
from gate_schedule.priority_statistics import role_statistics, priority_counts_before
from gate_schedule.server import create_server_app
from gate_schedule.workbench import Workbench
from tests.test_balance_workflow import small_configuration


def statistics_state():
    scenario = Scenario('2026-08-02',
        (Flight('f1', 'F1', '2026-08-02', 600, 660), Flight('f2', 'F2', '2026-08-02', 660, 720)),
        (Member('a', '甲', frozenset({'普通岗', '旧岗位'}), 0), Member('b', '乙', frozenset({'普通岗'}), 0)),
        (Task('segment1', 'f1', '普通岗', 600, 630, 1, 'fixed'),
         Task('segment2', 'f1', '普通岗', 630, 660, 1, 'fixed'),
         Task('shared', None, '旧岗位', 600, 720, 1, 'rotation', covered_flight_ids=('f1', 'f2')),
         Task('occupancy', None, '额外占用', 500, 550, 1, 'fixed')))
    formal = {'scenario': scenario_to_dict(scenario), 'savedRevision': 2,
              'assignments': [{'taskId': t.id, 'memberId': 'a'} for t in scenario.tasks]}
    return {'revision': 9, 'selectedPlanId': 'draft', 'configuration': {'roleConfigs': [{'roleName': '普通岗'}, {'roleName': '未排岗位'}]},
        'people': [{'id': 'a', 'name': '甲', 'eligibleRoleNames': ['普通岗'], 'active': True},
                   {'id': 'b', 'name': '乙', 'eligibleRoleNames': ['普通岗'], 'active': True},
                   {'id': 'removed', 'name': '已移除', 'eligibleRoleNames': [], 'active': False, 'deleted': True}],
        'balanceSettings': {'priorityRoleNames': [], 'priorityAdjustments': [
            {'month': '2026-08', 'memberId': 'a', 'roleName': '普通岗', 'delta': 10}]},
        'plans': {'formal': {'adopted': formal}, 'draft': {'day': {'serviceDate': '2026-08-04'}, 'adopted': None}}}


class RoleStatisticsTests(unittest.TestCase):
    def test_manual_draft_replaces_formal_date_and_clear_undo_restart_follow_counts(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp', prefix='manual-counts-') as directory:
            path = Path(directory)/'state.sqlite3'
            wb = Workbench(path)
            config = small_configuration(None)
            config['balanceSettings']['priorityRoleNames'] = ['接机1']
            state = wb.execute(0, 'import-complete-configuration', config)
            pid = state['selectedPlanId']
            people = {p['name']: p['id'] for p in state['people']}
            def command(action, payload=None):
                return wb.execute(wb.snapshot()['revision'], action, payload or {}, pid)
            def counts():
                return {p['memberId']: p['count'] for p in wb.role_statistics('2026-08')['totals']}
            command('edit-cell', {'cellId': 'c0', 'text': '甲'})
            command('edit-cell', {'cellId': 'c1', 'text': '甲'})
            self.assertEqual(2, counts()[people['甲']], '未采用的手改草稿也要立即计次')
            command('adopt')
            formal = deepcopy(wb.backup()['state']['plans'][pid]['adopted'])
            command('edit-cell', {'cellId': 'c0', 'text': '乙'})
            self.assertEqual({people['甲']: 1, people['乙']: 1}, counts(), '当前草稿替换同日正式，不叠加')
            rows = {r['memberId']: r for r in wb.priority_statistics('2026-08')['roles'][0]['members']}
            self.assertEqual(1, rows[people['乙']]['effectiveCount'])
            self.assertEqual({(people['甲'], '接机1'): 2}, {k: v for k, v in priority_counts_before(wb.backup()['state'], '2026-08-04').items() if v}, '显示草稿不改求解使用的正式账本')
            command('adjust-priority-count', {'month': '2026-08', 'memberId': people['乙'], 'roleName': '接机1', 'delta': 1})
            self.assertEqual(2, next(r['effectiveCount'] for r in wb.priority_statistics('2026-08')['roles'][0]['members'] if r['memberId'] == people['乙']))
            self.assertEqual(1, counts()[people['乙']], '重点修正不能混入所有岗位实际次数')
            before = wb.backup()
            self.assertEqual(wb.role_statistics('2026-08'), Workbench(path).role_statistics('2026-08'))
            restored = Workbench(Path(directory)/'restored.sqlite3')
            restored.restore(0, before)
            self.assertEqual(wb.role_statistics('2026-08')['roles'], restored.role_statistics('2026-08')['roles'])
            command('edit-cell', {'cellId': 'c0', 'text': '甲/乙'})
            self.assertFalse(wb.snapshot()['plans'][pid]['valid'])
            self.assertEqual(3, sum(counts().values()), '红色问题任务按当前填写计数，仍禁止采用')
            command('undo-edit')
            command('clear-draft')
            self.assertEqual(0, sum(counts().values()), '清空草稿后不能回弹到旧正式次数')
            command('undo-edit')
            self.assertEqual({people['甲']: 1, people['乙']: 1}, counts())
            self.assertEqual(formal, wb.backup()['state']['plans'][pid]['adopted'])
            command('adopt')
            self.assertEqual({people['甲']: 1, people['乙']: 1}, counts(), '正式采用不重复增加')

    def test_current_draft_replaces_latest_same_date_and_does_not_cross_month(self):
        state = statistics_state()
        old = deepcopy(state['plans']['formal'])
        old['adopted']['savedRevision'] = 1
        state['plans']['old'] = old
        current = deepcopy(old['adopted'])
        for row in current['assignments']:
            row['memberId'] = 'b'
        data = role_statistics(state, '2026-08', current=current)
        self.assertEqual({'a': 0, 'b': 3}, {p['memberId']: p['count'] for p in data['totals']})
        self.assertEqual(0, sum(p['count'] for p in role_statistics(state, '2026-09', current=current)['totals']))

    def test_all_roles_include_unselected_and_historical_roles_with_actual_counts(self):
        state = statistics_state()
        before = deepcopy(state)
        data = role_statistics(state, '2026-08')
        roles = {r['roleName']: r for r in data['roles']}
        self.assertEqual({'普通岗', '旧岗位', '未排岗位'}, set(roles))
        common = {m['memberId']: m for m in roles['普通岗']['members']}
        self.assertEqual(1, common['a']['count'], '同航班分段不重复，不混入重点次数修正')
        self.assertEqual(['2026-08-02 · F1'], common['a']['dates'])
        self.assertEqual(0, common['b']['count'])
        self.assertEqual(2, roles['旧岗位']['members'][0]['count'])
        self.assertEqual(3, next(p['count'] for p in data['totals'] if p['memberId'] == 'a'))
        self.assertEqual({'a', 'b'}, {m['memberId'] for m in data['members']})
        self.assertEqual(before, state)

    def test_latest_formal_per_day_month_isolation_and_removed_identity(self):
        state = statistics_state()
        latest = deepcopy(state['plans']['formal'])
        latest['adopted']['savedRevision'] = 3
        latest['adopted']['scenario']['members'].append({**latest['adopted']['scenario']['members'][0], 'id': 'removed', 'name': '已移除'})
        for a in latest['adopted']['assignments']: a['memberId'] = 'removed'
        state['plans']['latest'] = latest
        july = deepcopy(latest)
        july['adopted']['scenario']['serviceDate'] = '2026-07-31'
        state['plans']['july'] = july
        data = role_statistics(state, '2026-08')
        counts = {p['memberId']: p['count'] for p in data['totals']}
        self.assertEqual({'a': 0, 'b': 0, 'removed': 3}, counts)
        self.assertIn('2026-07', data['months'])
        empty = role_statistics(state, '2026-06')
        self.assertEqual(0, sum(p['count'] for p in empty['totals']))
        self.assertNotIn('removed', {p['memberId'] for p in empty['members']})

    def test_empty_workspace_and_invalid_month(self):
        state = statistics_state()
        state.update(plans={}, people=[], configuration={'roleConfigs': []})
        data = role_statistics(state, '2026-08')
        self.assertEqual([], data['members'])
        self.assertEqual([], data['roles'])
        for month in ('2026-13', '2026-8', '', 'invalid'):
            with self.subTest(month=month), self.assertRaises(ValueError): role_statistics(state, month)

    def test_http_is_readonly_and_backup_restart_recomputes_the_same_counts(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp', prefix='role-stats-') as directory:
            path = Path(directory)/'state.sqlite3'
            wb = Workbench(path)
            state = wb.execute(0, 'import-complete-configuration', small_configuration(None))
            pid = state['selectedPlanId']
            tasks = state['plans'][pid]['schedule']['tasks']
            member = state['people'][0]['id']
            state = wb.execute(state['revision'], 'assign', {'assignments': [{'taskId': t['id'], 'memberId': member} for t in tasks]}, pid)
            wb.execute(state['revision'], 'adopt', {}, pid)
            before = wb.backup()
            client = TestClient(create_server_app(database_path=path))
            response = client.get('/api/role-statistics', params={'month': '2026-08'})
            self.assertEqual(200, response.status_code)
            data = response.json()
            self.assertEqual(2, next(t['count'] for t in data['totals'] if t['memberId'] == member))
            self.assertEqual(422, client.get('/api/role-statistics?month=2026-13').status_code)
            self.assertEqual(before, wb.backup())
            self.assertEqual(data, Workbench(path).role_statistics('2026-08'))
            restored = Workbench(Path(directory)/'restored.sqlite3')
            restored.restore(0, before)
            after = restored.role_statistics('2026-08')
            self.assertEqual(data['roles'], after['roles'])
            self.assertEqual(data['members'], after['members'])
