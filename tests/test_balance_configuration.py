from copy import deepcopy
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from openpyxl import load_workbook

from gate_schedule.domain import Assignment
from gate_schedule.excel_io import (
    export_complete_configuration, import_complete_configuration,
    validate_complete_configuration,
)
from gate_schedule.imported_schedule import imported_day_to_scenario
from gate_schedule.excel_io import imported_day_from_dict
from gate_schedule.io import normalize_saved_scenario, scenario_from_dict, scenario_to_dict
from gate_schedule.validator import validate_schedule
from gate_schedule.workbench import Workbench


ROOT = Path(__file__).resolve().parents[1]


def configuration():
    role = '登机主验'
    names = ['甲', '乙']
    flights = [
        {'id': 'A', 'flightNo': 'A100', 'windowStart': '10:00', 'windowEnd': '11:00'},
        {'id': 'B', 'flightNo': 'B200', 'windowStart': '12:00', 'windowEnd': '13:00'},
    ]
    return {
        'source': '平衡配置迁移测试', 'rotationEnabled': False, 'rotationMinutes': 60,
        'peopleIds': {'甲': 'stable-a', '乙': 'stable-b'},
        'balanceSettings': {
            'flightCountGap': 0, 'priorityEnabled': True,
            'priorityRoleNames': [role],
            'priorityAdjustments': [
                {'month': '2026-08', 'memberId': 'stable-a', 'roleName': role, 'delta': 3},
                {'month': '2026-08', 'memberId': 'stable-b', 'roleName': role, 'delta': -1},
            ],
        },
        'day': {
            'serviceDate': '2026-08-20', 'title': '平衡迁移', 'releasePolicy': 'task',
            'attendanceMode': 'duty', 'transitionMinutes': 0, 'remarksReviewed': True,
            'remarks': [], 'extraOccupancies': [], 'phaseHandoffs': [],
            'flights': flights, 'roleNames': [role], 'roster': names,
            'roleConfigs': [{'roleName': role, 'requiredCount': 1, 'workMode': 'boarding', 'concurrent': False}],
            'memberConfigs': [{'memberName': name, 'eligibleRoleNames': [role]} for name in names],
            'memberAvailability': [{'memberName': name, 'participating': True, 'availableFrom': None, 'availableUntil': None} for name in names],
            'cells': [{'id': flight['id'], 'flightId': flight['id'], 'flightNo': flight['flightNo'],
                       'roleName': role, 'rawValue': '', 'internalNames': [], 'externalLabels': [], 'semantics': 'empty'} for flight in flights],
        },
    }


def workbook_bytes(book):
    result = BytesIO()
    book.save(result)
    book.close()
    return result.getvalue()


class BalanceConfigurationTests(unittest.TestCase):
    def test_json_contract_keeps_balance_and_stable_person_ids_without_aliasing(self):
        data = configuration()
        result = validate_complete_configuration(data)
        self.assertEqual(data['balanceSettings'], result.get('balanceSettings'))
        self.assertEqual(data['peopleIds'], result.get('peopleIds'))
        result['balanceSettings']['priorityRoleNames'].clear()
        result['peopleIds']['甲'] = 'edited-copy'
        self.assertEqual(['登机主验'], data['balanceSettings']['priorityRoleNames'])
        self.assertEqual('stable-a', data['peopleIds']['甲'])

    def test_excel_visible_editable_settings_and_identity_roundtrip(self):
        data = configuration()
        content = export_complete_configuration(data)
        book = load_workbook(BytesIO(content))
        self.assertTrue({'排班平衡', '重点次数修正', '人员身份'} <= set(book.sheetnames))
        self.assertTrue(all(sheet.sheet_state == 'visible' for sheet in book))
        self.assertEqual(['月份', '人员ID', '姓名', '岗位', '修正次数'], [cell.value for cell in book['重点次数修正'][1]])
        self.assertEqual(['2026-08', 'stable-a', '甲', '登机主验', 3], [cell.value for cell in book['重点次数修正'][2]])
        values = {row[0].value: row[1] for row in book['排班平衡'].iter_rows(min_row=2)}
        values['当天航班数最大差距'].value = 1
        values['重点岗位轮换启用'].value = '否'
        values['重点岗位'].value = None
        book['重点次数修正'].delete_rows(2, book['重点次数修正'].max_row)
        updated = import_complete_configuration(workbook_bytes(book))
        self.assertEqual({'flightCountGap': 1, 'priorityEnabled': False, 'priorityRoleNames': [], 'priorityAdjustments': []}, updated['balanceSettings'])
        self.assertEqual(data['peopleIds'], updated['peopleIds'])
        again = import_complete_configuration(export_complete_configuration(updated))
        self.assertEqual(updated['balanceSettings'], again['balanceSettings'])
        self.assertEqual(updated['peopleIds'], again['peopleIds'])

    def test_old_missing_tables_stay_absent_and_explicit_disabled_values_survive(self):
        data = configuration()
        data.pop('balanceSettings')
        data.pop('peopleIds')
        restored = import_complete_configuration(export_complete_configuration(data))
        self.assertNotIn('balanceSettings', restored)
        self.assertNotIn('peopleIds', restored)
        data['balanceSettings'] = {'flightCountGap': None, 'priorityEnabled': False, 'priorityRoleNames': [], 'priorityAdjustments': []}
        restored = import_complete_configuration(export_complete_configuration(data))
        self.assertEqual(data['balanceSettings'], restored['balanceSettings'])

    def test_partial_identity_table_allows_new_person_but_never_duplicate_or_wrong_ids(self):
        data = configuration()
        data['balanceSettings']['priorityAdjustments'] = []
        data['peopleIds'].pop('乙')
        self.assertEqual({'甲': 'stable-a'}, import_complete_configuration(export_complete_configuration(data))['peopleIds'])
        for ids in [{'甲': 'same', '乙': 'same'}, {'陌生人': 'unknown'}, {'甲': ''}, {'甲': ' a '}, []]:
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                validate_complete_configuration({**data, 'peopleIds': ids})

    def test_invalid_balance_contract_is_rejected_without_silent_coercion(self):
        data = configuration()
        bad = [
            {'flightCountGap': -1}, {'flightCountGap': True}, {'flightCountGap': '1'},
            {'priorityEnabled': '是'}, {'priorityRoleNames': ['未知岗位']},
            {'priorityRoleNames': ['登机主验', '登机主验']},
            {'priorityAdjustments': [{'month': '2026-13', 'memberId': 'stable-a', 'roleName': '登机主验', 'delta': 1}]},
            {'priorityAdjustments': [{'month': '2026-08', 'memberId': 'missing', 'roleName': '登机主验', 'delta': 1}]},
            {'priorityAdjustments': [{'month': '2026-08', 'memberId': 'stable-a', 'roleName': '登机主验', 'delta': True}]},
        ]
        for changes in bad:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_complete_configuration({**data, 'balanceSettings': {**data['balanceSettings'], **changes}})

    def test_excel_missing_companion_tables_and_identity_name_mismatch_reject(self):
        content = export_complete_configuration(configuration())
        for removed in ['排班平衡', '重点次数修正']:
            book = load_workbook(BytesIO(content))
            del book[removed]
            with self.subTest(removed=removed), self.assertRaises(ValueError):
                import_complete_configuration(workbook_bytes(book))
        book = load_workbook(BytesIO(content))
        book['重点次数修正']['C2'] = '乙'
        with self.assertRaises(ValueError):
            import_complete_configuration(workbook_bytes(book))

    def test_excel_balance_formula_and_duplicate_identity_reject(self):
        content = export_complete_configuration(configuration())
        for sheet, cell, value in [('排班平衡', 'B2', '=1'), ('重点次数修正', 'E2', 1.5), ('人员身份', 'B3', 'stable-a')]:
            book = load_workbook(BytesIO(content))
            book[sheet][cell] = value
            with self.subTest(sheet=sheet, value=value), self.assertRaises(ValueError):
                import_complete_configuration(workbook_bytes(book))

    def test_scenario_json_preserves_new_rules_and_legacy_is_disabled(self):
        day = imported_day_from_dict(configuration()['day'])
        scenario, issues = imported_day_to_scenario(day)
        self.assertFalse(issues)
        raw = scenario_to_dict(scenario)
        raw['flightCountGap'] = 0
        raw['priorityRoleIds'] = ['登机主验']
        restored = scenario_from_dict(raw)
        self.assertEqual(0, restored.flight_count_gap)
        self.assertEqual(('登机主验',), restored.priority_role_ids)
        self.assertEqual(raw, scenario_to_dict(restored))
        normalized, _, _ = normalize_saved_scenario(raw, [])
        self.assertEqual(0, normalized['flightCountGap'])
        self.assertEqual(['登机主验'], normalized['priorityRoleIds'])
        raw['priorityRoleIds'] = ['当日没有任务的已配置重点岗']
        self.assertEqual(raw, scenario_to_dict(scenario_from_dict(raw)), '当日无任务的重点配置可随快照保留，不能导致读取失败')
        raw.pop('flightCountGap')
        raw.pop('priorityRoleIds')
        legacy = scenario_from_dict(raw)
        self.assertIsNone(legacy.flight_count_gap)
        self.assertEqual((), legacy.priority_role_ids)

    def test_scenario_json_rejects_bad_balance_fields(self):
        scenario, _ = imported_day_to_scenario(imported_day_from_dict(configuration()['day']))
        raw = scenario_to_dict(scenario)
        for changes in [{'flightCountGap': -1}, {'flightCountGap': True}, {'priorityRoleIds': '登机主验'}, {'priorityRoleIds': ['登机主验', '登机主验']}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                scenario_from_dict({**raw, **changes})


class BalanceWorkspaceMigrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='balance-configuration-')
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'state.sqlite3'
        self.workspace = Workbench(self.path)

    def command(self, action, payload, plan_id=None, workspace=None):
        workspace = workspace or self.workspace
        return workspace.execute(workspace.snapshot()['revision'], action, payload, plan_id)

    def maintained(self):
        data = configuration()
        data['balanceSettings']['priorityAdjustments'] = data['balanceSettings']['priorityAdjustments'][:1]
        return data

    def test_modified_settings_excel_empty_database_restart_and_actual_hard_limit(self):
        data = self.maintained()
        state = self.command('import-complete-configuration', data)
        original_plan = state['selectedPlanId']
        for role in state['configuration']['roleConfigs']:
            if role['roleName'] not in data['day']['roleNames']:
                self.command('save-role', {'roleName': role['roleName'], 'requiredCount': 0})
        settings = deepcopy(data['balanceSettings'])
        settings['flightCountGap'] = 1
        self.command('update-balance-settings', {'balanceSettings': settings})
        self.command('adjust-priority-count', {'month': '2026-08', 'memberId': 'stable-a', 'roleName': '登机主验', 'delta': 1})
        settings['priorityAdjustments'][0]['delta'] = 4
        content = self.workspace.export_complete_configuration(original_plan)
        incoming = import_complete_configuration(content)
        self.assertEqual(settings, incoming['balanceSettings'])
        self.assertEqual(data['peopleIds'], incoming['peopleIds'])

        other_path = Path(self.directory.name) / 'device.sqlite3'
        other = Workbench(other_path)
        state = self.command('import-complete-configuration', incoming, workspace=other)
        other = Workbench(other_path)
        state = other.snapshot()
        self.assertEqual(settings, state['balanceSettings'])
        self.assertEqual(data['peopleIds'], {person['name']: person['id'] for person in state['people']})
        pid = state['selectedPlanId']
        state = self.command('solve', {}, pid, other)
        plan = state['plans'][pid]
        self.assertTrue(plan['valid'], plan['issues'])
        assigned = {identity: {a['taskId'] for a in plan['draft'] if a['memberId'] == identity} for identity in data['peopleIds'].values()}
        self.assertEqual({1}, {len(tasks) for tasks in assigned.values()}, '配置迁移后的硬差距1实际约束两个航班分给两个人')
        adopted = self.command('adopt', {}, pid, other)['plans'][pid]['adopted']
        self.assertEqual(1, adopted['scenario']['flightCountGap'])
        self.assertEqual(['登机主验'], adopted['scenario']['priorityRoleIds'])
        adopted_scenario = scenario_from_dict(adopted['scenario'])
        invalid_assignments = tuple(Assignment(task.id, 'stable-a') for task in adopted_scenario.tasks)
        self.assertTrue(any(issue.code == 'flight_count_spread' for issue in validate_schedule(adopted_scenario, invalid_assignments)))

        # A post-solve manual change must not bypass the migrated hard balance rule.
        for cell in plan['day']['cells']:
            state = self.command('edit-cell', {'cellId': cell['id'], 'text': '甲'}, pid, other)
        self.assertFalse(state['plans'][pid]['valid'])
        self.assertTrue(any(issue['code'] == 'flight_count_spread' for issue in state['plans'][pid]['issues']))
        before = other.backup()
        saved=self.command('adopt', {}, pid, other)
        adopted=saved['plans'][pid]['adopted']
        self.assertIn('flight_count_spread',{i['code'] for i in adopted['issues']})
        restored = Workbench(Path(self.directory.name) / 'backup-restored.sqlite3')
        restored.restore(0, other.backup())
        self.assertEqual(settings, restored.snapshot()['balanceSettings'])
        self.assertEqual(adopted, restored.snapshot()['plans'][pid]['adopted'])

    def test_priority_adjustments_excel_restart_change_actual_selection(self):
        data = self.maintained()
        data['balanceSettings']['flightCountGap'] = None
        data['day']['flights'] = data['day']['flights'][:1]
        data['day']['cells'] = data['day']['cells'][:1]
        incoming = import_complete_configuration(export_complete_configuration(data))
        state = self.command('import-complete-configuration', incoming)
        restarted = Workbench(self.path)
        pid = state['selectedPlanId']
        state = self.command('solve', {}, pid, restarted)
        plan = state['plans'][pid]
        self.assertTrue(plan['valid'], plan['issues'])
        self.assertEqual({'stable-b'}, {a['memberId'] for a in plan['draft']}, '迁移的月内重点修正使做得更少的乙优先')
        for _ in range(4):
            self.command('adjust-priority-count', {'month': '2026-08', 'memberId': 'stable-b', 'roleName': '登机主验', 'delta': 1}, workspace=restarted)
        state = self.command('solve', {}, pid, restarted)
        self.assertNotEqual(pid, state['selectedPlanId'], '设置变化新建排班快照，原草稿保留')
        self.assertEqual({'stable-a'}, {a['memberId'] for a in state['plans'][state['selectedPlanId']]['draft']}, '统计表调整后下一次计算使用最新修正')
        self.assertEqual(plan['draft'], state['plans'][pid]['draft'])

    def test_old_configuration_preserves_settings_and_explicit_empty_clears(self):
        initial = self.maintained()
        state = self.command('import-complete-configuration', initial)
        settings = state['balanceSettings']
        old = configuration()
        old.pop('balanceSettings')
        old.pop('peopleIds')
        incoming = import_complete_configuration(export_complete_configuration(old))
        self.command('import-complete-configuration', incoming)
        self.assertEqual(settings, Workbench(self.path).snapshot()['balanceSettings'])
        incoming['balanceSettings'] = {'flightCountGap': None, 'priorityEnabled': False, 'priorityRoleNames': [], 'priorityAdjustments': []}
        self.command('import-complete-configuration', incoming)
        self.assertEqual(incoming['balanceSettings'], Workbench(self.path).snapshot()['balanceSettings'])
        empty = Workbench(Path(self.directory.name) / 'old-empty.sqlite3')
        incoming.pop('balanceSettings')
        state = self.command('import-complete-configuration', incoming, workspace=empty)
        self.assertEqual({'flightCountGap': None, 'priorityEnabled': False, 'priorityRoleNames': [], 'priorityAdjustments': []}, state['balanceSettings'])

    def test_existing_name_identity_wins_and_adjustments_remap_without_changing_history(self):
        data = self.maintained()
        old = deepcopy(data)
        old.pop('peopleIds')
        old.pop('balanceSettings')
        state = self.command('import-complete-configuration', old)
        identities = {person['name']: person['id'] for person in state['people']}
        original_plans = deepcopy(self.workspace.backup()['state']['plans'])
        incoming = import_complete_configuration(export_complete_configuration(data))
        self.command('import-complete-configuration', incoming)
        state = Workbench(self.path).snapshot()
        self.assertEqual(identities, {person['name']: person['id'] for person in state['people']})
        self.assertEqual(identities['甲'], state['balanceSettings']['priorityAdjustments'][0]['memberId'])
        for pid, plan in original_plans.items():
            self.assertEqual(plan, self.workspace.backup()['state']['plans'][pid])

    def test_unmapped_adjustments_never_guess_identity_or_partially_write(self):
        data = self.maintained()
        data.pop('peopleIds')
        before = self.workspace.backup()
        with self.assertRaises(ValueError):
            self.command('import-complete-configuration', data)
        self.assertEqual(before, self.workspace.backup())

    def test_negative_correction_keeps_value_on_empty_device_and_effective_count_is_zero(self):
        data = configuration()
        incoming = import_complete_configuration(export_complete_configuration(data))
        state = self.command('import-complete-configuration', incoming)
        restarted = Workbench(self.path)
        report = restarted.priority_statistics('2026-08')
        members = {member['memberId']: member for member in report['roles'][0]['members']}
        self.assertEqual(0, members['stable-b']['historyCount'])
        self.assertEqual(-1, members['stable-b']['adjustment'])
        self.assertEqual(0, members['stable-b']['effectiveCount'])
        exported = import_complete_configuration(restarted.export_complete_configuration(state['selectedPlanId']))
        self.assertEqual(data['balanceSettings'], exported['balanceSettings'], '缺少原设备正式历史不应丢掉人工负修正')


if __name__ == '__main__':
    unittest.main()
