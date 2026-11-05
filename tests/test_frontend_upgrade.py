from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from gate_schedule.server import create_server_app, main
from tests.test_balance_workflow import small_configuration


ROOT = Path(__file__).resolve().parents[1]


class FrontendUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=ROOT/'.tmp', prefix='frontend-upgrade-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.static = self.root/'static'
        (self.static/'assets').mkdir(parents=True)
        (self.static/'index.html').write_text('<html>new flight scope</html>', encoding='utf-8')
        (self.static/'assets/entry.js').write_text('const version = 2;', encoding='utf-8')
        self.client = TestClient(create_server_app(self.static, self.root/'state.sqlite3'))

    def test_page_assets_and_live_state_do_not_reuse_old_browser_cache(self):
        for path in ['/', '/index.html', '/?v=new', '/assets/entry.js', '/api/workspace', '/api/priority-statistics']:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(200, response.status_code)
                self.assertEqual('no-store', response.headers.get('cache-control'))
                if 'etag' in response.headers:
                    again = self.client.get(path, headers={'If-None-Match': response.headers['etag'],
                        'If-Modified-Since': response.headers['last-modified']})
                    self.assertEqual(200, again.status_code, 'Do not reuse a pre-upgrade page on a conditional request')
                    self.assertEqual(response.content, again.content)

    def test_original_launcher_opens_the_current_page_version(self):
        page = ROOT/'web/dist/index.html'
        version = hashlib.sha256(page.read_bytes()).hexdigest()[:12]
        with patch('sys.argv', ['gate', '--port', '19765']), patch('gate_schedule.server.create_server_app'), \
                patch('gate_schedule.server.uvicorn.run'), patch('gate_schedule.server.threading.Timer') as timer, \
                patch('gate_schedule.server.webbrowser.open') as browser:
            self.assertEqual(0, main())
            timer.call_args.args[1]()
            browser.assert_called_once_with(f'http://127.0.0.1:19765/?v={version}')

    def test_old_page_save_is_rejected_atomically_then_new_page_saves_flight_scope(self):
        state = self.client.post('/api/workspace/command', json={'expectedRevision': 0,
            'action': 'import-complete-configuration', 'payload': small_configuration()}).json()
        before = self.client.get('/api/workspace/backup').json()
        legacy = deepcopy(state['balanceSettings'])
        legacy.pop('priorityFlightRoles')
        legacy.update(priorityEnabled=True, priorityRoleNames=['接机1'])
        for action in ['save-rules', 'update-balance-settings']:
            for echo_new_field in [False, True]:
                with self.subTest(action=action, echo_new_field=echo_new_field):
                    incoming = deepcopy(legacy)
                    if echo_new_field:
                        incoming['priorityFlightRoles'] = []
                    configuration = deepcopy(state['configuration'])
                    configuration['rotationMinutes'] += 1
                    response = self.client.post('/api/workspace/command', json={'expectedRevision': state['revision'],
                        'action': action, 'payload': {'configuration': configuration, 'balanceSettings': incoming,
                            'preferences': state['handoffPreferences']}})
                    self.assertEqual(409, response.status_code, response.text[:300])
                    self.assertIn('Ctrl+F5', response.json()['detail'])
                    self.assertEqual(before, self.client.get('/api/workspace/backup').json())
            with self.subTest(action=action, current_schema_missing_scope=True):
                configuration = deepcopy(state['configuration'])
                response = self.client.post('/api/workspace/command', headers={'X-Gate-Rule-Schema': 'flight-role-v1'}, json={'expectedRevision': state['revision'],
                    'action': action, 'payload': {'configuration': configuration, 'balanceSettings': legacy,
                        'preferences': state['handoffPreferences']}})
                self.assertEqual(409, response.status_code, response.text[:300])
                self.assertIn('Ctrl+F5', response.json()['detail'])
                self.assertEqual(before, self.client.get('/api/workspace/backup').json())
        settings = deepcopy(state['balanceSettings'])
        settings.update(priorityEnabled=True, priorityFlightRoles=[{'flightNo': 'F0', 'roleName': '接机1'}])
        response = self.client.post('/api/workspace/command', headers={'X-Gate-Rule-Schema': 'flight-role-v1'}, json={'expectedRevision': state['revision'],
            'action': 'save-rules', 'payload': {'configuration': state['configuration'], 'balanceSettings': settings,
                'preferences': state['handoffPreferences']}})
        self.assertEqual(200, response.status_code, response.text[:300])
        actual = self.client.get('/api/priority-statistics?month=2026-08').json()
        self.assertEqual([('F0', '接机1')], [(r['flightNo'], r['roleName']) for r in actual['roles']])
        after = self.client.get('/api/workspace/backup').json()
        self.assertEqual(before['state']['plans'], after['state']['plans'])
        restart = TestClient(create_server_app(self.static, self.root/'state.sqlite3'))
        self.assertEqual(after, restart.get('/api/workspace/backup').json())


if __name__ == '__main__':
    unittest.main()
