"""Drag work scales with changed schedules, not every unchanged history view."""
import base64
from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from gate_schedule.browser_runtime import BrowserStore, request_sync
from gate_schedule.group_workspaces import GroupWorkspaces
from gate_schedule import workbench


def drag_documents(days=3):
    """A complete isolated history used by route and real-browser timing tests."""
    # Public regression data is generated, never copied from a local day sheet.
    from tests.test_balance_workflow import small_configuration
    store = BrowserStore()
    groups = GroupWorkspaces(Path('/unused'), store=store)
    groups.initialize()
    wb = groups.workbench('A')
    config = small_configuration(None)
    day = config['day']
    roles = ['接机1', *(f'合成岗位{i}' for i in range(1, 9))]
    people = [f'合成人员{i:02}' for i in range(15)]
    day['roleNames'] = roles
    day['roleConfigs'] = [{'roleName': role, 'requiredCount': 1, 'workMode': 'continuous'} for role in roles]
    day['roster'] = people
    day['memberConfigs'] = [{'memberName': name, 'eligibleRoleNames': roles} for name in people]
    day['memberAvailability'] = [{'memberName': name, 'participating': True} for name in people]
    day['flights'] = [{'id': f'f{i}', 'flightNo': f'TEST{i}', 'windowStart': '10:00', 'windowEnd': '11:00'} for i in range(7)]
    day['cells'] = [{**deepcopy(day['cells'][0]), 'id': f'c{i}-{j}', 'flightId': flight['id'],
                     'flightNo': flight['flightNo'], 'roleName': role,
                     'requiredCountOverride': 0 if i == 6 and j > 6 else 1}
                    for i, flight in enumerate(day['flights']) for j, role in enumerate(roles)]
    state = wb.execute(0, 'import-complete-configuration', config)
    pid = state['selectedPlanId']
    plan = state['plans'][pid]
    assignments = [{'taskId': task['id'], 'memberId': plan['memberIds'][people[i % len(people)]]}
                   for i, task in enumerate(plan['schedule']['tasks'])]
    state = wb.execute(state['revision'], 'assign', {'assignments': assignments}, pid)
    backup = wb.backup()
    original = next(iter(backup['state']['plans'].values()))
    original.pop('undoEdit', None)
    backup['state']['plans'] = {}
    for index in range(days):
        plan = deepcopy(original)
        plan['id'] = f'perf-{index}'
        plan['day']['serviceDate'] = (date(2062, 8, 2) + timedelta(days=index)).isoformat()
        if plan.get('adopted'):
            plan['adopted']['day']['serviceDate'] = plan['day']['serviceDate']
            plan['adopted']['scenario']['serviceDate'] = plan['day']['serviceDate']
        backup['state']['plans'][plan['id']] = plan
    backup['state']['selectedPlanId'] = plan['id']
    groups.restore('A', state['revision'], backup)
    return store.documents


class DragPerformanceTests(unittest.TestCase):
    def request(self, documents, command=None):
        wire = json.loads(request_sync(json.dumps({
            'documents': documents, 'url': '/api/groups/A/workspace' + ('/command' if command else ''),
            'method': 'POST' if command else 'GET', 'headers': {'X-Gate-Rule-Schema': 'flight-role-v2'},
            'body': base64.b64encode(json.dumps(command).encode()).decode() if command else '',
        })))
        self.assertEqual(200, wire['response']['status'], wire['response'])
        return json.loads(base64.b64decode(wire['response']['body'])), wire['documents']

    def test_browser_swap_reuses_unchanged_history_views_and_rechecks_changed_plan(self):
        before, documents = self.request(drag_documents())
        pid = before['selectedPlanId']
        current = before['plans'][pid]
        first, second = next((a,b) for a in current['draft'] for b in current['draft']
                             if a['taskId'] != b['taskId'] and a['memberId'] != b['memberId'])
        with (patch.object(workbench, 'schedule_data', wraps=workbench.schedule_data) as project,
              patch.object(workbench, 'solve', side_effect=AssertionError('拖动不得调用自动求解')),
              patch.object(workbench.Workbench, '_solve', side_effect=AssertionError('拖动不得重新排班'))):
            after, saved = self.request(documents, {'expectedRevision': before['revision'],
                'action': 'move-assignment', 'planId': pid, 'payload': {
                    'memberId': first['memberId'], 'sourceTaskId': first['taskId'],
                    'targetTaskId': second['taskId'], 'targetMemberId': second['memberId']}})
        self.assertLessEqual(project.call_count, 2, '拖一人不应重建未修改的历史班表显示')
        for identity in before['plans']:
            if identity != pid:
                self.assertEqual(before['plans'][identity], after['plans'][identity])
        self.assertIn({'taskId': second['taskId'], 'memberId': first['memberId']}, after['plans'][pid]['draft'])
        self.assertEqual(current['adopted'], after['plans'][pid]['adopted'])
        # Returning a view must never expose a mutable cached result to callers.
        after['plans'][pid]['schedule']['tasks'].clear()
        reread, _ = self.request(saved)
        self.assertTrue(reread['plans'][pid]['schedule']['tasks'])
        # A changed day's eligibility is a different cache key. Never preserve
        # an earlier green result after changing the underlying input facts.
        day = deepcopy(reread['plans'][pid]['day'])
        name = next(name for name, identity in current['memberIds'].items() if identity == first['memberId'])
        next(row for row in day['memberConfigs'] if row['memberName'] == name)['eligibleRoleNames'] = []
        changed, _ = self.request(saved, {'expectedRevision': reread['revision'],
            'action': 'save-input', 'planId': pid, 'payload': {'day': day}})
        self.assertTrue(all(first['memberId'] not in task['qualifiedMemberIds']
                            for task in changed['plans'][pid]['schedule']['tasks']))

    def test_browser_store_read_and_write_are_isolated(self):
        source = {'root': {'revision': 0, 'values': [{'text': '中文', 'zero': 0, 'empty': None}]}}
        store = BrowserStore(source)
        source['root']['values'].clear()
        received = store.read_document('root')
        received['values'][0]['text'] = 'changed'
        self.assertEqual('中文', store.read_document('root')['values'][0]['text'])
        store.compare_and_save('root', 0, {**received, 'revision': 1})
        received['values'].clear()
        self.assertEqual('changed', store.read_document('root')['values'][0]['text'])

    def test_group_projection_and_snapshot_never_modify_stored_document(self):
        store = BrowserStore()
        groups = GroupWorkspaces(Path('/unused'), store=store)
        groups.initialize()
        original = deepcopy(store.documents)
        state = groups.workbench('A').snapshot()
        state['configuration']['roleConfigs'].clear()
        state['flightLibrary'].clear()
        self.assertEqual(original, store.documents)
        next_state = groups.workbench('A').snapshot()
        self.assertTrue(next_state['configuration']['roleConfigs'])
