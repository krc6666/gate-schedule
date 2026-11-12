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

    def test_saved_checkboxes_survive_all_group_excel_transfer_and_restart(self):
        store = BrowserStore()
        groups = GroupWorkspaces(Path('/unused'), store=store)
        groups.initialize()
        expected = {}
        for group_id, enabled in [('A', True), ('B', False)]:
            wb = groups.workbench(group_id)

            def command(action, payload, plan_id=None):
                return wb.execute(wb.snapshot()['revision'], action, payload, plan_id)

            cfg = configuration()
            cfg['peopleIds'] = {'甲': group_id+'-a', '乙': group_id+'-b'}
            cfg['balanceSettings']['priorityAdjustments'] = []
            command('import-complete-configuration', cfg)
            command('save-person', {'name': '丙', 'active': False, 'eligibleRoleNames': []})
            state = wb.snapshot()
            person = next(p for p in state['people'] if p['name'] == '甲')
            command('save-person', {**person, 'teamLeader': enabled,
                                   'eligibleRoleNames': ['登机主验', '接机1'] if enabled else ['登机主验']})
            state = wb.snapshot()
            base = deepcopy(state['configuration'])
            base.update(rotationEnabled=enabled, rotationMinutes=45)
            for role in base['roleConfigs']:
                role['requiredCount'] = 1 if role['roleName'] == '登机主验' else 0
                role['concurrent'] = enabled
                role['exclusiveSameFlight'] = not enabled
                role['workMode'] = role.get('workMode', state['newRoleDefaults'][role['roleName']]['workMode'])
                command('save-role', role)
            balance = deepcopy(state['balanceSettings'])
            balance.update(priorityEnabled=enabled, flightCountGap=None if enabled else 1,
                           priorityFlightRoles=[{'flightNo': 'A100' if enabled else 'B200',
                                                 'roleName': '登机主验'}])
            relaxed = deepcopy(state['relaxedShiftSettings'])
            relaxed['lateFlightNos'] = ['A100' if enabled else 'B200']
            relaxed.update(earlyDepartureBalanceEnabled=enabled, afternoonRestBalanceEnabled=not enabled,
                           earlyDepartureMaxDifference=0 if enabled else 3,
                           afternoonRestMaxDifference=2 if enabled else 1)
            command('save-rules', {
                'configuration': base, 'balanceSettings': balance,
                'teamLeaderSettings': {'lastUsed': enabled, 'exemptFlightCount': not enabled},
                'halfRestSettings': {'exemptFlightCount': enabled},
                'relaxedShiftSettings': relaxed,
                'preferences': [{'flightNo': '*', 'arrivalRole': '接机1',
                                 'boardingRoles': ['登机主验'], 'enabled': enabled, 'ordered': not enabled}],
            })
            state = wb.snapshot()
            ids = {p['name']: p['id'] for p in state['people']}
            command('save-duty-settings', {**state['dutyRoster'], 'enabled': enabled,
                                          'dutyMemberIds': [ids['甲']], 'standbyMemberIds': [ids['乙']]})
            command('save-weekly-flights', {'weekday': '2', 'flights': state['flightTemplates'][:1]})
            state = wb.snapshot()
            pid = state['selectedPlanId']
            day = deepcopy(state['plans'][pid]['day'])
            day['remarksReviewed'] = enabled
            day['phaseHandoffs'] = [['A', 'B']] if enabled else []
            for entry in day['memberAvailability']:
                entry['participating'] = entry['memberName'] == '甲'
                entry['halfRest'] = 'late-start' if enabled and entry['participating'] else None
            for role in day['roleConfigs']:
                role['concurrent'] = not enabled
                role['exclusiveSameFlight'] = enabled
            for member in day['memberConfigs']:
                if member['memberName'] == '乙':
                    member['eligibleRoleNames'] = []
            day['cells'][1]['fixedMemberNames'] = ['甲']
            command('save-input', {'day': day, 'rotationEnabled': not enabled, 'rotationMinutes': 85}, pid)

        # Capture after both groups finish updating the shared weekly choices.
        for group_id in ('A', 'B'):
            expected[group_id] = groups.workbench(group_id).snapshot()

        # Either group's export must also preserve the other group's settings.
        for export_group in ('A', 'B'):
            with self.subTest(export_group=export_group):
                status, content, _ = self.request(
                    store.documents, f'/api/groups/{export_group}/workspace/configuration/export')
                self.assertEqual(200, status)
                status, preview, fresh = self.request(
                    {}, f'/api/groups/{export_group}/workspace/data/preview', binary=content)
                self.assertEqual(200, status, preview)
                preview = json.loads(preview)
                status, imported, saved = self.request(
                    fresh, f'/api/groups/{export_group}/workspace/command', {
                        'expectedRevision': preview['expectedRevision'],
                        'action': 'import-complete-configuration', 'payload': preview['payload'],
                    })
                self.assertEqual(200, status, imported)
                for group_id in ('A', 'B'):
                    status, restarted, saved = self.request(saved, f'/api/groups/{group_id}/workspace')
                    self.assertEqual(200, status)
                    restarted = json.loads(restarted)
                    before = expected[group_id]
                    for key in ('configuration', 'balanceSettings', 'teamLeaderSettings',
                                'halfRestSettings', 'relaxedShiftSettings', 'handoffPreferences',
                                'dutyRoster', 'weeklyFlightPresets', 'flightLibrary', 'displayPreferences'):
                        self.assertEqual(before[key], restarted[key], f'{group_id}: {key}')
                    for person in before['people']:
                        restored_person = next(p for p in restarted['people'] if p['id'] == person['id'])
                        for key in ('active', 'teamLeader', 'eligibleRoleNames'):
                            self.assertEqual(person.get(key, False), restored_person.get(key, False), key)
                    for identity, old_plan in before['plans'].items():
                        self.assertEqual(old_plan, restarted['plans'][identity])
                    source_day = before['plans'][before['selectedPlanId']]
                    current = restarted['plans'][restarted['selectedPlanId']]
                    self.assertEqual(source_day['day']['remarksReviewed'], current['day']['remarksReviewed'])
                    def handoff_flights(day):
                        numbers = {f['id']: f['flightNo'] for f in day['flights']}
                        return [[numbers[a], numbers[b]] for a, b in day['phaseHandoffs']]
                    self.assertEqual(handoff_flights(source_day['day']), handoff_flights(current['day']))
                    for entry in source_day['day']['memberAvailability']:
                        restored_entry = next(a for a in current['day']['memberAvailability']
                                              if a['memberName'] == entry['memberName'])
                        for key in ('participating', 'halfRest', 'availableFrom', 'availableUntil'):
                            self.assertEqual(entry.get(key), restored_entry.get(key), key)
                    self.assertFalse(next(a for a in current['day']['memberAvailability']
                                          if a['memberName'] == '丙')['participating'])
                    for member in source_day['day']['memberConfigs']:
                        restored_member = next(m for m in current['day']['memberConfigs']
                                               if m['memberName'] == member['memberName'])
                        original_roles = set(source_day['day']['roleNames'])
                        self.assertEqual(set(member['eligibleRoleNames']) & original_roles,
                                         set(restored_member['eligibleRoleNames']) & original_roles)
                    for role in source_day['day']['roleConfigs']:
                        restored_role = next(r for r in current['day']['roleConfigs']
                                             if r['roleName'] == role['roleName'])
                        for key in ('concurrent', 'exclusiveSameFlight'):
                            self.assertEqual(role.get(key, False), restored_role.get(key, False), key)
                    self.assertEqual(source_day['rotationEnabled'], current['rotationEnabled'])
                    self.assertEqual(85, current['rotationMinutes'])
                    self.assertEqual(['甲'], current['day']['cells'][1]['fixedMemberNames'])

                    # Consume the restored settings in scheduling and statistics,
                    # rather than just checking serialized flags. The new maintenance
                    # plan uses latest rules; the original historical plan stays intact.
                    restored_groups = GroupWorkspaces(Path('/unused'), store=BrowserStore(saved))
                    target = restored_groups.workbench(group_id)
                    self.assertEqual(groups.workbench(group_id).priority_statistics('2026-08')['roles'],
                                     target.priority_statistics('2026-08')['roles'])
                    actual = target.execute(restarted['revision'], 'solve', {}, restarted['selectedPlanId'])
                    actual_plan = actual['plans'][restarted['selectedPlanId']]
                    def scheduled_work(plan):
                        schedule = plan['schedule']
                        numbers = {f['id']: f['flightNo'] for f in schedule['flights']}
                        tasks = {t['id']: (numbers.get(t['flightId']), t['roleId'], t['start'], t['end'])
                                 for t in schedule['tasks']}
                        return sorted((*tasks[a['taskId']], a['memberId']) for a in plan['draft'])
                    expected_work = [('B200', '登机主验', '12:00', '13:00', group_id+'-a')]
                    if group_id == 'B':
                        expected_work.insert(0, ('A100', '登机主验', '10:00', '11:00', 'B-a'))
                    self.assertEqual(expected_work, scheduled_work(actual_plan))
                    self.assertNotIn('half_rest_period', {i['code'] for i in actual_plan['issues']})


if __name__ == '__main__':
    unittest.main()
