"""Cloud loading must fail closed before optional local migrations can seed data."""
from copy import deepcopy
import unittest

from gate_schedule.browser_runtime import BrowserStore, validate_cloud_documents, request_sync
from gate_schedule.group_workspaces import GroupWorkspaces
from pathlib import Path
import json
from tests.test_balance_configuration import configuration


class CloudDocumentsTests(unittest.TestCase):
    def test_old_role_wide_dedication_can_open_and_materialize_without_writing_original(self):
        for group in ('A', 'B'):
            state = self.documents[GroupWorkspaces.KEY]['groups'][group]
            state['configuration']['roleConfigs'][0]['exclusiveSameFlight'] = True
            name = state['configuration']['roleConfigs'][0]['roleName']
            state['flightLibrary'] = [dict(flightNo='CX937',windowStart='10:00',windowEnd='11:00',
                                           roleNames=[name],roles=[dict(roleName=name,requiredCount=1,
                                               workModeOverride=None,workStart=None,workEnd=None)])]
        self.documents[GroupWorkspaces.KEY]['shared']['flightTemplates'] = [
            dict(flightNo='CX937',windowStart='10:00',windowEnd='11:00')]
        before = deepcopy(self.documents)
        validate_cloud_documents(self.documents)
        self.assertEqual(before, self.documents)
        loaded = GroupWorkspaces(Path('unused'), store=BrowserStore(self.documents))
        for group in ('A', 'B'):
            state = loaded.workbench(group).snapshot()
            self.assertTrue(state['flightLibrary'][0]['roles'][0]['exclusiveSameFlight'])
        self.assertEqual(before, self.documents)

    def test_saved_cloud_rules_without_new_time_basis_can_still_open(self):
        from tests.test_baggage_window import baggage_day
        rules = baggage_day()['workRules']
        rules['baggageWindow'].pop('timeBasis', None)
        for state in self.documents[GroupWorkspaces.KEY]['groups'].values():
            state['workRules'] = deepcopy(rules)
        before = deepcopy(self.documents)
        validate_cloud_documents(self.documents)
        self.assertEqual(before, self.documents, 'Opening must not overwrite the server snapshot')

    def test_legacy_cloud_login_save_and_reopen_preserve_history_and_explicit_settings(self):
        from tests.test_baggage_window import baggage_day
        for group in ('A', 'B'):
            wb = self.groups.workbench(group)
            state = wb.execute(wb.snapshot()['revision'], 'import-complete-configuration', configuration())
            pid = state['selectedPlanId']
            state = wb.execute(state['revision'], 'solve', {}, pid)
            wb.execute(state['revision'], 'adopt', {}, pid)
        documents = deepcopy(self.store.documents)
        groups = documents[GroupWorkspaces.KEY]['groups']
        for group, basis in [('A', None), ('B', 'departure-offsets')]:
            groups[group]['workRules'] = deepcopy(baggage_day()['workRules'])
            if basis is None:
                groups[group]['workRules']['baggageWindow'].pop('timeBasis', None)
            else:
                groups[group]['workRules']['baggageWindow']['timeBasis'] = basis
        before = deepcopy(documents)
        checked = json.loads(request_sync(json.dumps({'url': '/__cloud_validate__', 'documents': documents})))
        self.assertEqual(200, checked['response']['status'])
        self.assertEqual(before, checked['documents'])
        restored = GroupWorkspaces(Path('unused'), store=BrowserStore(documents))
        for group, basis in [('A', 'flight-window'), ('B', 'departure-offsets')]:
            state = restored.workbench(group).snapshot()
            self.assertEqual(basis, state['workRules']['baggageWindow']['timeBasis'])
            self.assertEqual(before[GroupWorkspaces.KEY]['groups'][group]['plans'],
                             restored.backup()['groups'][group]['plans'])
            restored.workbench(group).execute(state['revision'], 'save-display-preferences', {'zoom': 110, 'layout': 'flights'})
        saved = {GroupWorkspaces.KEY: restored.backup()}
        validate_cloud_documents(saved)
        reopened = GroupWorkspaces(Path('unused'), store=BrowserStore(saved))
        for group in ('A', 'B'):
            state = reopened.workbench(group).snapshot()
            self.assertEqual(110, state['displayPreferences']['zoom'])
            for pid, plan in before[GroupWorkspaces.KEY]['groups'][group]['plans'].items():
                self.assertEqual(plan, reopened.backup()['groups'][group]['plans'][pid])
        self.assertEqual(before, documents)

    def test_compatibility_never_allows_unrelated_repairs_or_invalid_time_basis(self):
        from tests.test_baggage_window import baggage_day
        original = deepcopy(self.documents)
        original[GroupWorkspaces.KEY]['groups']['A']['workRules'] = deepcopy(baggage_day()['workRules'])
        original[GroupWorkspaces.KEY]['groups']['A']['workRules']['baggageWindow'].pop('timeBasis', None)
        for case in ('missing-policy', 'invalid-basis', 'missing-core'):
            bad = deepcopy(original)
            state = bad[GroupWorkspaces.KEY]['groups']['A']
            if case == 'missing-policy':
                state['balanceSettings'].pop('priorityFlightRoles')
            elif case == 'invalid-basis':
                state['workRules']['baggageWindow']['timeBasis'] = 'guess'
            else:
                state.pop('configuration')
            before = deepcopy(bad)
            with self.subTest(case=case), self.assertRaises(ValueError):
                validate_cloud_documents(bad)
            self.assertEqual(before, bad)

    def test_baggage_tolerance_is_preserved_in_complete_cloud_documents(self):
        from tests.test_baggage_window import baggage_day
        for group, tolerance in [('A',15),('B',45)]:
            wb=self.groups.workbench(group);day=baggage_day()
            day['workRules']['baggageWindow']['remainderToleranceMinutes']=tolerance
            state=wb.execute(wb.snapshot()['revision'],'import-complete-configuration',dict(day=day,workRules=day['workRules'],rotationEnabled=True,rotationMinutes=60))
            for role in state['configuration']['roleConfigs']:
                if role['roleName']!='大件行李拦截':state=wb.execute(state['revision'],'save-role',{**role,'requiredCount':0})
            state=wb.execute(state['revision'],'solve',{},state['selectedPlanId'])
            wb.execute(state['revision'],'adopt',{},state['selectedPlanId'])
        documents=json.loads(json.dumps(self.store.documents));before=deepcopy(documents)
        validate_cloud_documents(documents);self.assertEqual(before,documents)
        restored=GroupWorkspaces(Path('unused'),store=BrowserStore(documents))
        for group,count,tolerance in [('A',3,15),('B',2,45)]:
            state=restored.workbench(group).snapshot()
            self.assertEqual(tolerance,state['workRules']['baggageWindow']['remainderToleranceMinutes'])
            self.assertEqual(count,state['plans'][state['selectedPlanId']]['baggageCoverage']['requiredPeople'])

    def setUp(self):
        self.store = BrowserStore()
        self.groups = GroupWorkspaces(Path('unused'), store=self.store)
        self.groups.initialize()
        self.documents = deepcopy(self.store.documents)

    def test_roundtrip_preserves_both_groups_and_rules(self):
        before = deepcopy(self.documents)
        validate_cloud_documents(self.documents)
        self.assertEqual(self.documents, before)

    def test_missing_group_or_configuration_never_seeds_defaults(self):
        for remove in ('groups', 'shared'):
            bad = deepcopy(self.documents)
            del bad[GroupWorkspaces.KEY][remove]
            with self.assertRaises(ValueError):
                validate_cloud_documents(bad)
        for remove in ('configuration', 'people', 'plans', 'balanceSettings', 'flightLibrary'):
            bad = deepcopy(self.documents)
            del bad[GroupWorkspaces.KEY]['groups']['A'][remove]
            before = deepcopy(bad)
            with self.assertRaises(ValueError):
                validate_cloud_documents(bad)
            self.assertEqual(bad, before)

    def test_corrupt_formal_or_unknown_envelope_is_rejected(self):
        for bad in ({}, {'other': {}}, {**self.documents, 'extra': {}},
                    {GroupWorkspaces.KEY: None}):
            with self.assertRaises(ValueError):
                validate_cloud_documents(bad)
        bad = deepcopy(self.documents)
        bad[GroupWorkspaces.KEY]['groups']['A']['revision'] = -1
        with self.assertRaises(ValueError):
            validate_cloud_documents(bad)

    def test_formal_history_statistics_and_two_groups_survive_exact_roundtrip(self):
        for group in ('A', 'B'):
            wb = self.groups.workbench(group)
            state = wb.execute(wb.snapshot()['revision'], 'import-complete-configuration', configuration())
            pid = state['selectedPlanId']
            state = wb.execute(state['revision'], 'solve', {}, pid)
            wb.execute(state['revision'], 'adopt', {}, pid)
        documents = json.loads(json.dumps(self.store.documents))
        before = deepcopy(documents)
        validate_cloud_documents(documents)
        self.assertEqual(before, documents)
        restored = GroupWorkspaces(Path('unused'), store=BrowserStore(documents))
        for group in ('A', 'B'):
            self.assertEqual(self.groups.workbench(group).snapshot(), restored.workbench(group).snapshot())
        # Breaking an originally legal formal snapshot must fail independent validation.
        state = documents[GroupWorkspaces.KEY]['groups']['A']
        formal = state['plans'][state['selectedPlanId']]['adopted']
        formal['assignments'][0]['memberId'] = 'unknown-person'
        with self.assertRaises(ValueError):
            validate_cloud_documents(documents)
