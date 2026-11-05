from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from gate_schedule.domain import Assignment, Flight, Member, Scenario, Task
from gate_schedule.io import scenario_to_dict
from gate_schedule.priority_statistics import priority_statistics, priority_counts_before, formal_history
from gate_schedule.workbench import Workbench
from gate_schedule.server import create_server_app
from tests.test_complete_excel import example
from tests.test_balance_workflow import small_configuration


def formal(day, member='a', revision=1):
    sc = Scenario(day, (Flight('f', 'CX937', day, 600, 700),),
        (Member('a', '甲', frozenset({'督导'}), 0), Member('b', '乙', frozenset({'督导'}), 0)),
        (Task('t', 'f', '督导', 600, 700, 1, 'mobile'),), release_policy='task')
    return {'day': {'serviceDate': day}, 'adopted': {'scenario': scenario_to_dict(sc),
        'assignments': [{'taskId': 't', 'memberId': member}], 'savedRevision': revision}}


def state():
    return {'revision': 9, 'selectedPlanId': 'draft', 'people': [
        {'id': 'a', 'name': '甲', 'eligibleRoleNames': ['督导'], 'active': True},
        {'id': 'b', 'name': '乙', 'eligibleRoleNames': ['督导'], 'active': True}],
        'balanceSettings': {'flightCountGap': None, 'priorityEnabled': True,
            'priorityRoleNames': ['督导'], 'priorityAdjustments': []},
        'plans': {'july': formal('2026-07-31'), 'old': formal('2026-08-02', 'a', 1),
            'new': formal('2026-08-02', 'b', 2), 'future': formal('2026-08-10'),
            'draft': {'day': {'serviceDate': '2026-08-04'}, 'adopted': None}}}


class PriorityStatisticsTests(unittest.TestCase):
    def test_monthly_counts_same_day_latest_formal_and_no_drafts(self):
        data = priority_statistics(state(), '2026-08')
        self.assertEqual(9, data['revision'])
        members = {m['memberId']: m for m in data['roles'][0]['members']}
        self.assertEqual(1, members['a']['historyCount'])
        self.assertEqual(1, members['b']['historyCount'])
        self.assertEqual(2, len(formal_history(state(), before='2026-08-04')))
        self.assertEqual({('b', '督导'): 1}, {k: v for k, v in priority_counts_before(state(), '2026-08-04').items() if v})

    def test_signed_adjustment_and_effective_floor(self):
        value = state()
        value['balanceSettings']['priorityAdjustments'] = [
            {'month': '2026-08', 'memberId': 'a', 'roleName': '督导', 'delta': 3},
            {'month': '2026-08', 'memberId': 'b', 'roleName': '督导', 'delta': -5}]
        members = priority_statistics(value, '2026-08')['roles'][0]['members']
        self.assertEqual([4, 0], [m['effectiveCount'] for m in members])
        self.assertEqual(3, priority_counts_before(value, '2026-08-04')[('a', '督导')])
        with self.assertRaises(ValueError):
            priority_statistics(value, '2026-13')


class PriorityWorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp', prefix='priority-wb-')
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)/'state.sqlite3'
        self.wb = Workbench(self.path)
        self.wb.execute(0, 'import-complete-configuration', example())

    def command(self, action, payload, pid=None):
        return self.wb.execute(self.wb.snapshot()['revision'], action, payload, pid)

    def test_settings_adjustments_persist_and_rule_save_cannot_overwrite_counts(self):
        original = deepcopy(self.wb.backup()['state']['plans'])
        value = {'flightCountGap': 1, 'priorityEnabled': True, 'priorityRoleNames': [],
            'priorityFlightRoles': [{'flightNo': 'CX937', 'roleName': '督导'}], 'priorityAdjustments': []}
        current = self.command('update-balance-settings', {'balanceSettings': value})
        person = current['people'][0]
        self.command('adjust-priority-count', {'month': '2026-08', 'memberId': person['id'], 'flightNo': 'CX937', 'roleName': '督导', 'delta': 1})
        before = self.wb.backup()
        self.assertEqual(original, before['state']['plans'])
        self.assertEqual(1, Workbench(self.path).priority_statistics('2026-08')['roles'][0]['members'][0]['effectiveCount'])
        with self.assertRaises(ValueError):
            self.command('update-balance-settings', {'balanceSettings': value})
        self.assertEqual(before, self.wb.backup())
        self.command('adjust-priority-count', {'month': '2026-08', 'memberId': person['id'], 'flightNo': 'CX937', 'roleName': '督导', 'delta': -1})
        before = self.wb.backup()
        with self.assertRaises(ValueError):
            self.command('adjust-priority-count', {'month': '2026-08', 'memberId': person['id'], 'flightNo': 'CX937', 'roleName': '督导', 'delta': -1})
        self.assertEqual(before, self.wb.backup())

    def test_http_month_query_is_readonly_and_settings_do_not_rewrite_old_plans(self):
        before = self.wb.backup()
        client = TestClient(create_server_app(database_path=self.path))
        result = client.get('/api/priority-statistics', params={'month': '2026-08'})
        self.assertEqual(200, result.status_code)
        self.assertEqual(before['state']['revision'], result.json()['revision'])
        self.assertEqual(422, client.get('/api/priority-statistics', params={'month': '2026-13'}).status_code)
        self.assertEqual(before, self.wb.backup())

    def test_increment_from_imported_negative_floor_visibly_increases_one(self):
        current = self.wb.snapshot()
        member = current['people'][0]['id']
        config = example()
        config['balanceSettings'] = {'flightCountGap': None, 'priorityEnabled': True,
            'priorityRoleNames': [], 'priorityFlightRoles': [{'flightNo': 'CX937', 'roleName': '督导'}], 'priorityAdjustments': [
                {'month': '2026-08', 'memberId': member, 'flightNo': 'CX937', 'roleName': '督导', 'delta': -5}]}
        self.command('import-complete-configuration', config)
        self.assertEqual(0, self.wb.priority_statistics('2026-08')['roles'][0]['members'][0]['effectiveCount'])
        self.command('adjust-priority-count', {'month': '2026-08', 'memberId': member, 'flightNo': 'CX937', 'roleName': '督导', 'delta': 1})
        self.assertEqual(1, self.wb.priority_statistics('2026-08')['roles'][0]['members'][0]['effectiveCount'])

    def test_new_archive_after_restore_wins_over_old_device_revision(self):
        source = Workbench(Path(self.directory.name)/'archive-source.sqlite3')
        config = small_configuration(None)
        config['balanceSettings'].update(priorityEnabled=True, priorityRoleNames=[], priorityFlightRoles=[
            {'flightNo': 'F0', 'roleName': '接机1'}, {'flightNo': 'F1', 'roleName': '接机1'}])
        data = source.execute(0, 'import-complete-configuration', config)
        pid = data['selectedPlanId']
        members = data['people']
        assignments = [{'taskId': t['id'], 'memberId': members[0]['id']} for t in data['plans'][pid]['schedule']['tasks']]
        data = source.execute(data['revision'], 'assign', {'assignments': assignments}, pid)
        source.execute(data['revision'], 'adopt', {}, pid)
        document = source.backup()
        document['state']['revision'] = 100
        document['state']['plans'][pid]['adopted']['savedRevision'] = 100
        later = deepcopy(document['state']['plans'][pid])
        later.update(id='later-draft', adopted=None,
            draft=[dict(a, memberId=members[1]['id']) for a in assignments])
        document['state']['plans']['later-draft'] = later
        document['state']['selectedPlanId'] = 'later-draft'
        target = Workbench(Path(self.directory.name)/'archive-target.sqlite3')
        restored = target.restore(0, document)
        target.execute(restored['revision'], 'adopt', {}, 'later-draft')
        groups = target.priority_statistics('2026-08')['roles']
        self.assertEqual([[0, 1], [0, 1]], [[r['historyCount'] for r in group['members']] for group in groups])
