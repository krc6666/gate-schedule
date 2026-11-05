"""Configuration candidates share one interface across single and grouped workspaces."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from gate_schedule.configuration_transfer import prepare_import, prepare_export
from gate_schedule.excel_io import import_complete_configuration
from gate_schedule.workbench import Workbench
from tests.test_balance_configuration import configuration


class ConfigurationPreparationTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory(dir=Path.cwd() / '.tmp', prefix='transfer-preparation-')
        self.addCleanup(folder.cleanup)
        self.workbench = Workbench(Path(folder.name) / 'state.sqlite3')

    def test_import_builds_validated_candidate_without_changing_either_input(self):
        original = self.workbench.backup()['state']
        payload = configuration()
        saved_state, saved_payload = deepcopy(original), deepcopy(payload)
        candidate = prepare_import(original, payload)
        self.assertEqual(saved_state, original)
        self.assertEqual(saved_payload, payload)
        self.assertEqual(original['revision'] + 1, candidate.state['revision'])
        self.assertEqual(original['revision'], candidate.preview['expectedRevision'])
        self.assertEqual(2, candidate.preview['peopleCount'])
        self.assertIn(candidate.state['selectedPlanId'], candidate.state['plans'])
        candidate.state['people'][0]['name'] = 'candidate only'
        self.assertEqual(saved_state, original)
        self.assertEqual(saved_payload, payload)

    def test_failed_history_import_never_changes_local_document_or_payload(self):
        original = self.workbench.backup()['state']
        payload = configuration()
        payload['history'] = {'formatVersion': 2, 'state': deepcopy(original)}
        payload['history']['state']['selectedPlanId'] = 'missing-plan'
        before = deepcopy((original, payload))
        with self.assertRaises(ValueError):
            prepare_import(original, payload)
        self.assertEqual(before, (original, payload))

    def test_export_owns_a_copy_and_materializes_explicit_disabled_flags(self):
        self.workbench.execute(0, 'import-complete-configuration', configuration())
        original = self.workbench.backup()['state']
        before = deepcopy(original)
        prepared = prepare_export(original)
        decoded = import_complete_configuration(prepared.excel())
        self.assertEqual(before, original)
        self.assertEqual(before, prepared.payload['history']['state'])
        self.assertEqual(decoded['day']['roleConfigs'], prepared.payload['day']['roleConfigs'])
        self.assertTrue(all('exclusiveSameFlight' in r for r in prepared.payload['day']['roleConfigs']))
        # A new complete export's disabled flag replaces the target's enabled flag;
        # an old file with an absent flag must still preserve the target setting.
        target = deepcopy(original)
        for role in target['configuration']['roleConfigs']:
            role['exclusiveSameFlight'] = True
        candidate = prepare_import(target, prepared.payload)
        self.assertTrue(all(not r['exclusiveSameFlight'] for r in candidate.state['configuration']['roleConfigs']))
        legacy = deepcopy(prepared.payload)
        legacy.pop('history')
        for role in legacy['day']['roleConfigs']:
            role.pop('exclusiveSameFlight')
        retained = prepare_import(target, legacy)
        self.assertTrue(all(r['exclusiveSameFlight'] for r in retained.state['configuration']['roleConfigs']))
        prepared.payload['day']['roster'].clear()
        self.assertEqual(before, original)

    def test_old_missing_fields_preserve_settings_and_explicit_false_replaces_them(self):
        original = self.workbench.backup()['state']
        original['halfRestSettings']['exemptFlightCount'] = False
        payload = configuration()
        payload.pop('halfRestSettings', None)
        candidate = prepare_import(original, payload)
        self.assertFalse(candidate.state['halfRestSettings']['exemptFlightCount'])
        payload['halfRestSettings'] = {'exemptFlightCount': True}
        changed = prepare_import(original, payload)
        self.assertTrue(changed.state['halfRestSettings']['exemptFlightCount'])
