from copy import deepcopy
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from openpyxl import load_workbook

from gate_schedule.workbench import Workbench, WorkspaceError
from gate_schedule.excel_io import export_complete_configuration, import_complete_configuration
from test_balance_workflow import small_configuration


def configuration():
    data = small_configuration(None)
    day = data['day']
    day['roleNames'].append('登机主验')
    day['roleConfigs'].append({'roleName': '登机主验', 'requiredCount': 1, 'workMode': 'boarding', 'concurrent': False})
    for member in day['memberConfigs']: member['eligibleRoleNames'].append('登机主验')
    day['cells'].extend([{**deepcopy(c), 'id': c['id']+'boarding', 'roleName': '登机主验'} for c in day['cells'][:]])
    return data


def book_bytes(book):
    stream = BytesIO(); book.save(stream); book.close(); return stream.getvalue()


class ExclusiveConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp', prefix='exclusive-')
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)/'state.sqlite3'
        self.workspace = Workbench(self.path)
        self.command('import-complete-configuration', configuration())
        for role in self.workspace.snapshot()['configuration']['roleConfigs']:
            if role['roleName'] not in {'接机1','登机主验'}:
                self.command('save-role', {'roleName':role['roleName'], 'requiredCount':0})

    def command(self, action, payload=None, pid=None, workspace=None):
        workspace = workspace or self.workspace
        return workspace.execute(workspace.snapshot()['revision'], action, payload or {}, pid)

    def role(self, state, name='登机主验'):
        return next(role for role in state['configuration']['roleConfigs'] if role['roleName'] == name)

    def test_export_empty_device_restart_solve_and_manual_corruption_rejected(self):
        state = self.command('save-role', {'roleName':'登机主验', 'exclusiveSameFlight':True})
        original_pid = state['selectedPlanId']
        old_plans = deepcopy(state['plans'])
        content = self.workspace.export_complete_configuration(original_pid)
        imported = import_complete_configuration(content)
        self.assertTrue(next(r for r in imported['day']['roleConfigs'] if r['roleName']=='登机主验')['exclusiveSameFlight'])
        # The exported old day's exception stays off; new plans use the enabled default.
        other_path = self.path.with_name('device.sqlite3'); other = Workbench(other_path)
        self.command('import-complete-configuration', imported, workspace=other)
        other = Workbench(other_path)
        self.assertTrue(self.role(other.snapshot())['exclusiveSameFlight'])
        pid = other.snapshot()['selectedPlanId']
        self.command('apply-config', {}, pid, other)
        result = self.command('solve', {}, pid, other)
        plan = result['plans'][pid]
        self.assertTrue(plan['valid'])
        for flight in plan['day']['flights']:
            cells = [c for c in plan['day']['cells'] if c['flightId']==flight['id'] and c['roleName'] in {'接机1','登机主验'}]
            assignments = {a['taskId']:a['memberId'] for a in plan['draft']}
            self.assertNotEqual(assignments['imported-'+cells[0]['id']], assignments['imported-'+cells[1]['id']])
        self.command('adopt', {}, pid, other)
        formal = deepcopy(other.snapshot()['plans'][pid]['adopted'])
        self.assertTrue(any(t.get('exclusiveSameFlight') for t in formal['scenario']['tasks']))
        board = next(c for c in plan['day']['cells'] if c['roleName']=='登机主验')
        arrival = next(c for c in plan['day']['cells'] if c['roleName']=='接机1' and c['flightId']==board['flightId'])
        person_id = next(a['memberId'] for a in plan['draft'] if a['taskId']=='imported-'+arrival['id'])
        person = next(p['name'] for p in result['people'] if p['id']==person_id)
        options = other.candidates(pid, 'imported-'+board['id'])
        self.assertFalse(next(p for p in options if p['id']==person_id)['available'])
        broken = self.command('edit-cell', {'cellId':board['id'], 'text':person}, pid, other)
        self.assertIn('exclusive_same_flight', {i['code'] for i in broken['plans'][pid]['issues']})
        with self.assertRaises(WorkspaceError): self.command('adopt', {}, pid, other)
        self.assertEqual(formal, other.snapshot()['plans'][pid]['adopted'])
        self.assertEqual(old_plans, self.workspace.snapshot()['plans'])
        restored = Workbench(self.path.with_name('restore.sqlite3'))
        restored.restore(0, other.backup())
        self.assertTrue(self.role(restored.snapshot())['exclusiveSameFlight'])
        self.assertEqual(formal, restored.snapshot()['plans'][pid]['adopted'])

    def test_legacy_missing_flag_preserves_current_and_explicit_no_disables(self):
        self.command('save-role', {'roleName':'登机主验', 'exclusiveSameFlight':True})
        legacy = load_workbook(BytesIO(export_complete_configuration(configuration())))
        legacy['岗位性质'].delete_cols(4)
        incoming = import_complete_configuration(book_bytes(legacy))
        self.assertNotIn('exclusiveSameFlight', next(r for r in incoming['day']['roleConfigs'] if r['roleName']=='登机主验'))
        state = self.command('import-complete-configuration', incoming)
        self.assertTrue(self.role(state)['exclusiveSameFlight'])
        self.assertTrue(next(r for r in state['plans'][state['selectedPlanId']]['day']['roleConfigs'] if r['roleName']=='登机主验')['exclusiveSameFlight'])
        incoming = import_complete_configuration(export_complete_configuration(configuration()))
        state = self.command('import-complete-configuration', incoming)
        self.assertFalse(self.role(state)['exclusiveSameFlight'])
        fresh = Workbench(self.path.with_name('legacy-device.sqlite3'))
        state = self.command('import-complete-configuration', import_complete_configuration(book_bytes(load_workbook(BytesIO(export_complete_configuration(configuration()))))), workspace=fresh)
        self.assertFalse(self.role(state).get('exclusiveSameFlight',False))

    def test_daily_on_and_off_survive_excel_and_old_daily_column_inherits_base(self):
        data = configuration()
        data['day']['roleConfigs'][1]['exclusiveSameFlight'] = True
        data['dayOverrides'] = {'roleConfigs':[{**data['day']['roleConfigs'][1], 'exclusiveSameFlight':False}], 'qualifications':[], 'settings':{}}
        restored = import_complete_configuration(export_complete_configuration(data))
        self.assertTrue(restored['day']['roleConfigs'][1]['exclusiveSameFlight'])
        self.assertFalse(restored['dayOverrides']['roleConfigs'][0]['exclusiveSameFlight'])
        state = self.command('import-complete-configuration', restored)
        self.assertTrue(self.role(state)['exclusiveSameFlight'])
        self.assertFalse(next(r for r in state['plans'][state['selectedPlanId']]['day']['roleConfigs'] if r['roleName']=='登机主验')['exclusiveSameFlight'])
        book = load_workbook(BytesIO(export_complete_configuration(data)));book['当天岗位例外'].delete_cols(5)
        restored = import_complete_configuration(book_bytes(book))
        state = self.command('import-complete-configuration', restored)
        self.assertTrue(next(r for r in state['plans'][state['selectedPlanId']]['day']['roleConfigs'] if r['roleName']=='登机主验')['exclusiveSameFlight'])

    def test_bad_boolean_or_excel_flag_never_writes(self):
        before = self.workspace.backup()
        for value in [None, 1, '是', 'false']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.command('save-role', {'roleName':'登机主验', 'exclusiveSameFlight':value})
            self.assertEqual(before, self.workspace.backup())
        book = load_workbook(BytesIO(export_complete_configuration(configuration())))
        book['岗位性质']['D2'] = '随便'
        with self.assertRaises(ValueError): import_complete_configuration(book_bytes(book))
