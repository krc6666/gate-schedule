"""Audit existing-day initialization against maintained flight scope and demand."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from gate_schedule.domain import template_required_count, validate_imported_day
from gate_schedule.day_document import imported_day_from_dict
from gate_schedule.excel_io import import_complete_configuration
from gate_schedule.workbench import Workbench
from tests.test_balance_configuration import configuration


class ConfigurationScopeTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory(dir=Path.cwd() / '.tmp', prefix='configuration-scope-')
        self.addCleanup(folder.cleanup)
        self.directory = folder
        self.wb = Workbench(Path(folder.name) / 'state.sqlite3')
        state = self.wb.execute(0, 'import-complete-configuration', configuration())
        self.pid = state['selectedPlanId']

    def command(self, action, payload):
        return self.wb.execute(self.wb.snapshot()['revision'], action, payload)

    def add_scoped_role(self):
        flights = self.wb.snapshot()['flightLibrary']
        included, excluded = flights[0]['flightNo'], flights[1]['flightNo']
        self.command('add-template-role', {'flightNo': included, 'roleName': '专项服务岗'})
        self.command('save-role', {'roleName': '专项服务岗', 'requiredCount': 1, 'workMode': 'boarding'})
        template = next(f for f in self.wb.snapshot()['flightLibrary'] if f['flightNo'] == included)
        template['roles'].append({'roleName': '专项服务岗', 'requiredCount': 2})
        self.command('save-flight-template', template)
        return included, excluded

    def assert_scoped_role(self, plan, included, excluded):
        counts = {c['flightNo']: c['requiredCountOverride'] for c in plan['day']['cells'] if c['roleName'] == '专项服务岗'}
        self.assertEqual(2, counts[included])
        self.assertEqual(0, counts[excluded])
        tasks = [t for t in plan['schedule']['tasks'] if t['roleId'] == '专项服务岗']
        flights = {f['id']: f['flightNo'] for f in plan['schedule']['flights']}
        self.assertEqual([included], [flights[t['flightId']] for t in tasks])
        self.assertEqual([2], [t['requiredCount'] for t in tasks])
        self.assertTrue(any(i['code'] == 'coverage_shortfall' and i['taskId'] == tasks[0]['id'] for i in plan['issues']))

    def test_loading_base_configuration_adds_only_the_flights_selected_roles(self):
        included, excluded = self.add_scoped_role()
        before = deepcopy(self.wb.backup()['state']['plans'][self.pid])
        result = self.wb.execute(self.wb.snapshot()['revision'], 'apply-config', {}, self.pid)
        self.assert_scoped_role(result['plans'][self.pid], included, excluded)
        # An existing day cell is a deliberate exception; only missing cells
        # derive their initial demand from the maintained flight template.
        plan = result['plans'][self.pid]
        cell = next(c for c in plan['day']['cells'] if c['flightNo'] == excluded and c['roleName'] == '专项服务岗')
        cell['requiredCountOverride'] = 3
        self.wb.execute(result['revision'], 'save-input', {'day': plan['day']}, self.pid)
        result = self.wb.execute(self.wb.snapshot()['revision'], 'apply-config', {}, self.pid)
        self.assertEqual(3, next(c for c in result['plans'][self.pid]['day']['cells'] if c['id'] == cell['id'])['requiredCountOverride'])
        self.assertEqual(before['adopted'], result['plans'][self.pid]['adopted'])

    def test_export_new_role_initializes_flight_membership_and_counts_without_changing_current_plan(self):
        included, excluded = self.add_scoped_role()
        before = self.wb.backup()
        exported = import_complete_configuration(self.wb.export_complete_configuration(self.pid))
        self.assertEqual(before, self.wb.backup())
        target = Path(self.directory.name) / 'scoped-import.sqlite3'
        device = Workbench(target)
        state = device.execute(0, 'import-complete-configuration', exported)
        pid = state['selectedPlanId']
        device = Workbench(target)
        self.assert_scoped_role(device.snapshot()['plans'][pid], included, excluded)
        restored = Workbench(Path(self.directory.name) / 'scoped-restore.sqlite3')
        restored.restore(0, device.backup())
        self.assert_scoped_role(restored.snapshot()['plans'][pid], included, excluded)

    def test_new_cell_demand_distinguishes_absence_zero_inheritance_and_membership(self):
        for template, expected in [
            (None, None),
            ({'roles': []}, None),  # Old templates inherit the base setting.
            ({'roleNames': ['专项服务岗'], 'roles': []}, None),
            ({'roleNames': ['专项服务岗'], 'roles': [{'roleName': '专项服务岗', 'requiredCount': 0}]}, 0),
            ({'roleNames': ['专项服务岗'], 'roles': [{'roleName': '专项服务岗', 'requiredCount': 3}]}, 3),
            ({'roleNames': [], 'roles': [{'roleName': '专项服务岗', 'requiredCount': 3}]}, 0),
        ]:
            with self.subTest(template=template):
                self.assertEqual(expected, template_required_count(template, '专项服务岗'))

    def test_independent_input_validation_detects_an_entire_missing_position(self):
        day = deepcopy(self.wb.snapshot()['plans'][self.pid]['day'])
        removed = day['cells'].pop()
        issues = validate_imported_day(imported_day_from_dict(day))
        missing = [i for i in issues if i.code == 'missing_imported_cell']
        self.assertTrue(any(removed['flightNo'] in i.message and removed['roleName'] in i.message for i in missing))

