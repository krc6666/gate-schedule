"""Cloud loading must fail closed before optional local migrations can seed data."""
from copy import deepcopy
import unittest

from gate_schedule.browser_runtime import BrowserStore, validate_cloud_documents
from gate_schedule.group_workspaces import GroupWorkspaces
from pathlib import Path
import json
from tests.test_balance_configuration import configuration


class CloudDocumentsTests(unittest.TestCase):
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

    def test_old_cloud_snapshot_without_time_basis_opens_without_server_mutation(self):
        from tests.test_baggage_window import baggage_day

        rules = baggage_day()['workRules']
        rules['baggageWindow'].pop('timeBasis', None)
        for state in self.documents[GroupWorkspaces.KEY]['groups'].values():
            state['workRules'] = deepcopy(rules)
        before = deepcopy(self.documents)

        validate_cloud_documents(self.documents)

        self.assertEqual(before, self.documents)

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
