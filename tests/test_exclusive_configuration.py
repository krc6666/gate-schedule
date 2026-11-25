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
    data['flightLibrary'] = [{
        'flightNo': flight['flightNo'], 'windowStart': flight['windowStart'], 'windowEnd': flight['windowEnd'],
        'roleNames': list(day['roleNames']),
        'roles': [{'roleName': name, 'requiredCount': None, 'workModeOverride': next(r['workMode'] for r in day['roleConfigs'] if r['roleName'] == name),
                   'exclusiveSameFlight': False} for name in day['roleNames']],
    } for flight in day['flights']]
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
        state = self.workspace.snapshot()
        for flight in deepcopy(state['flightLibrary']):
            for role in flight['roles']:
                if role['roleName'] == '登机主验':
                    role['exclusiveSameFlight'] = True
            state = self.command('save-flight-template', flight)
        original_pid = state['selectedPlanId']
        old_plans = deepcopy(state['plans'])
        content = self.workspace.export_complete_configuration(original_pid)
        imported = import_complete_configuration(content)
        self.assertTrue(all(next(r['exclusiveSameFlight'] for r in f['roles'] if r['roleName']=='登机主验')
                            for f in imported['flightLibrary']))
        # The old day stays off; explicitly loading the flight templates applies their flags.
        other_path = self.path.with_name('device.sqlite3'); other = Workbench(other_path)
        self.command('import-complete-configuration', imported, workspace=other)
        other = Workbench(other_path)
        self.assertTrue(all(next(r['exclusiveSameFlight'] for r in f['roles'] if r['roleName']=='登机主验')
                            for f in other.snapshot()['flightLibrary']))
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
        formal = self.command('adopt', {}, pid, other)['plans'][pid]['adopted']
        self.assertIn('exclusive_same_flight', {i['code'] for i in formal['issues']})
        self.assertEqual(old_plans, self.workspace.snapshot()['plans'])
        restored = Workbench(self.path.with_name('restore.sqlite3'))
        restored.restore(0, other.backup())
        self.assertTrue(all(next(r['exclusiveSameFlight'] for r in f['roles'] if r['roleName']=='登机主验')
                            for f in restored.snapshot()['flightLibrary']))
        self.assertEqual(formal, restored.snapshot()['plans'][pid]['adopted'])

    def test_legacy_missing_flag_preserves_current_and_explicit_no_disables(self):
        for template in deepcopy(self.workspace.snapshot()['flightLibrary']):
            for role in template['roles']:
                if role['roleName'] == '登机主验': role['exclusiveSameFlight'] = True
            self.command('save-flight-template', template)
        legacy = load_workbook(BytesIO(export_complete_configuration(configuration())))
        legacy['使用说明']['B2'] = '登机口完整配置-5'
        del legacy['岗位性质']
        legacy.create_sheet('岗位性质')
        legacy['岗位性质'].cell(1, 1, '岗位')
        legacy['岗位性质'].cell(1, 2, '工作方式')
        # Reconstruct the old three-column nature format, without the dedicated flag.
        legacy['岗位性质'].cell(1, 3, '岗位性质')
        for row in legacy['岗位性质'].iter_rows(min_row=2): row[2].value = '常规'
        legacy['岗位性质'].cell(1, 4, '同航班专岗专人')
        for row in legacy['岗位性质'].iter_rows(min_row=2): row[3].value = '是' if row[0].value == '登机主验' else '否'
        legacy['模板岗位'].delete_cols(7, 2)
        del legacy['当天航班岗位专人专岗']
        modes = {'timed':'特殊实际时段', 'arrival':'接机环节', 'boarding':'登机环节', 'continuous':'全程保障'}
        for role in configuration()['day']['roleConfigs']:
            legacy['岗位性质'].append([role['roleName'], modes[role.get('workMode', 'timed')], '常规', '是' if role['roleName']=='登机主验' else '否'])
        incoming = import_complete_configuration(book_bytes(legacy))
        self.assertTrue(next(r for r in incoming['day']['roleConfigs'] if r['roleName']=='登机主验')['exclusiveSameFlight'])
        state = self.command('import-complete-configuration', incoming)
        self.assertTrue(all(next(r['exclusiveSameFlight'] for r in f['roles'] if r['roleName']=='登机主验')
                            for f in state['flightLibrary']))
        incoming = import_complete_configuration(export_complete_configuration(configuration()))
        state = self.command('import-complete-configuration', incoming)
        self.assertFalse(any(next(r['exclusiveSameFlight'] for r in f['roles'] if r['roleName']=='登机主验') for f in state['flightLibrary']))
        fresh = Workbench(self.path.with_name('legacy-device.sqlite3'))
        state = self.command('import-complete-configuration', import_complete_configuration(book_bytes(load_workbook(BytesIO(export_complete_configuration(configuration()))))), workspace=fresh)
        self.assertFalse(any(next(r['exclusiveSameFlight'] for r in f['roles'] if r['roleName']=='登机主验') for f in state['flightLibrary']))

    def test_daily_on_and_off_survive_excel_and_old_daily_column_inherits_base(self):
        data = configuration()
        data['day']['cells'][0]['exclusiveSameFlightOverride'] = True
        restored = import_complete_configuration(export_complete_configuration(data))
        self.assertTrue(restored['day']['cells'][0]['exclusiveSameFlightOverride'])
        book = load_workbook(BytesIO(export_complete_configuration(data)))
        self.assertEqual('hidden', book['岗位性质'].sheet_state)
        book['当天航班岗位专人专岗']['C2'] = '否'
        restored = import_complete_configuration(book_bytes(book))
        self.assertFalse(restored['day']['cells'][0]['exclusiveSameFlightOverride'])

    def test_bad_boolean_or_excel_flag_never_writes(self):
        before = self.workspace.backup()
        for value in [None, 1, '是', 'false']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.command('save-role', {'roleName':'登机主验', 'exclusiveSameFlight':value})
            self.assertEqual(before, self.workspace.backup())
        book = load_workbook(BytesIO(export_complete_configuration(configuration())))
        book['模板岗位']['G2'] = '随便'
        with self.assertRaises(ValueError): import_complete_configuration(book_bytes(book))
