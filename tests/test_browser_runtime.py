import base64
from copy import deepcopy
import json
from pathlib import Path
import unittest

from gate_schedule.browser_runtime import BrowserStore, request_sync
from gate_schedule.group_workspaces import GroupWorkspaces
from gate_schedule.excel_io import import_complete_configuration
from tests.test_balance_configuration import configuration


class BrowserRuntimeTests(unittest.TestCase):
    def request(self, documents, url, payload=None, binary=None):
        body = binary if binary is not None else json.dumps(payload, ensure_ascii=False).encode() if payload is not None else b''
        result = json.loads(request_sync(json.dumps({
            'documents': documents, 'url': url, 'method': 'POST' if payload is not None or binary is not None else 'GET',
            'headers': {'X-Gate-Rule-Schema': 'flight-role-v1'}, 'body': base64.b64encode(body).decode(),
        })))
        response = result['response']
        content = base64.b64decode(response['body'])
        return response['status'], content, result['documents']

    def test_no_database_initialization_and_request_parity(self):
        status, content, documents = self.request({}, '/api/groups')
        self.assertEqual(200, status)
        self.assertEqual('A', json.loads(content)['activeGroupId'])
        self.assertEqual({'group-workspaces-v1'}, set(documents))
        status, content, documents = self.request(documents, '/api/groups/A/workspace/command', {
            'expectedRevision': 0, 'action': 'save-person', 'payload': {'name': '浏览器人员'},
        })
        self.assertEqual(200, status)
        self.assertEqual('浏览器人员', json.loads(content)['people'][0]['name'])
        _, b, _ = self.request(documents, '/api/groups/B/workspace')
        self.assertEqual([], json.loads(b)['people'])
        before = deepcopy(documents)
        status, _, after = self.request(documents, '/api/groups/A/workspace/command', {
            'expectedRevision': 0, 'action': 'save-person', 'payload': {'name': '过期写入'},
        })
        self.assertEqual(409, status)
        self.assertEqual(before, after)

    def test_excel_export_import_reuses_group_validation_and_history(self):
        store = BrowserStore()
        groups = GroupWorkspaces(Path('/unused'), store=store)
        groups.initialize()
        wb = groups.workbench('A')
        wb.execute(0, 'import-complete-configuration', configuration())
        state = wb.snapshot()
        wb.execute(state['revision'], 'solve', {}, state['selectedPlanId'])
        status, content, _ = self.request(store.documents, '/api/groups/A/workspace/configuration/export')
        self.assertEqual(200, status)
        decoded = import_complete_configuration(content)
        self.assertIn('groupTransfer', decoded)
        status, preview, fresh = self.request({}, '/api/groups/A/workspace/data/preview', binary=content)
        self.assertEqual(200, status)
        preview = json.loads(preview)
        self.assertEqual('configuration', preview['kind'])
        # Preview must not import the data.
        _, empty, _ = self.request(fresh, '/api/groups/A/workspace')
        self.assertEqual([], json.loads(empty)['people'])
        self.assertGreater(len(decoded['groupTransfer']['configurations']['A']['history']['state']['plans']), 0)
        status, imported, saved = self.request(fresh, '/api/groups/A/workspace/command', {
            'expectedRevision': preview['expectedRevision'], 'action': 'import-complete-configuration',
            'payload': preview['payload'],
        })
        self.assertEqual(200, status, imported)
        _, restarted, _ = self.request(saved, '/api/groups/A/workspace')
        restarted = json.loads(restarted)
        self.assertEqual(2, len(restarted['people']))
        self.assertTrue(any(plan['draft'] for plan in restarted['plans'].values()))
        before = deepcopy(saved)
        status, _, damaged = self.request(saved, '/api/groups/A/workspace/data/preview', binary=b'PKbroken')
        self.assertEqual(422, status)
        self.assertEqual(before, damaged)


if __name__ == '__main__':
    unittest.main()
