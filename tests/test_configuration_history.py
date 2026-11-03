from copy import deepcopy
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from openpyxl import load_workbook

from gate_schedule.excel_io import import_complete_configuration
from gate_schedule.workbench import Workbench
from gate_schedule.backup_excel import import_backup
from tests.test_balance_workflow import small_configuration


class ConfigurationHistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / '.tmp', prefix='configuration-history-')
        self.addCleanup(self.directory.cleanup)
        self.source = Workbench(Path(self.directory.name) / 'source.sqlite3')
        for index in range(10):
            data = small_configuration(None)
            data['day']['serviceDate'] = f'2026-08-{index + 1:02d}'
            self.command(self.source, 'import-complete-configuration', data)
            self.command(self.source, 'edit-cell', {'cellId': 'c0', 'text': '甲'})
            self.command(self.source, 'edit-cell', {'cellId': 'c1', 'text': '乙'})
            self.command(self.source, 'adopt')
        self.original = self.source.backup()
        self.content = self.source.export_complete_configuration(self.source.snapshot()['selectedPlanId'])

    @staticmethod
    def command(workbench, action, payload=None):
        state = workbench.snapshot()
        return workbench.execute(state['revision'], action, payload or {}, state['selectedPlanId'])

    def target(self, name='target'):
        return Workbench(Path(self.directory.name) / f'{name}.sqlite3')

    def edit_excel(self, callback):
        book = load_workbook(BytesIO(self.content))
        callback(book)
        stream = BytesIO()
        book.save(stream)
        book.close()
        return stream.getvalue()

    def test_ten_days_and_editable_rules_survive_empty_device_restart(self):
        def edit_count(book):
            for row in book['岗位配置'].iter_rows(min_row=2):
                if row[0].value == '接机1':
                    row[1].value = 2
        content = self.edit_excel(edit_count)
        incoming = import_complete_configuration(content)
        target = self.target()
        preview = target.preview_complete_configuration(incoming)
        self.assertEqual(10, preview['historyDayCount'])
        self.assertEqual(10, preview['newHistoryPlanCount'])
        self.assertEqual(0, target.snapshot()['revision'], '预览不得写入')
        self.command(target, 'import-complete-configuration', incoming)
        target = self.target()
        actual = target.backup()['state']
        for pid, plan in self.original['state']['plans'].items():
            expected = deepcopy(plan)
            expected.pop('undoEdit', None)
            received = deepcopy(actual['plans'][pid])
            received.pop('undoEdit', None)
            self.assertEqual(expected, received)
            self.assertIsNotNone(target.snapshot()['plans'][pid]['formalSchedule'])
        self.assertEqual(10, len({p['day']['serviceDate'] for p in actual['plans'].values()}))
        self.assertEqual(2, next(r['requiredCount'] for r in actual['configuration']['roleConfigs'] if r['roleName'] == '接机1'))
        self.assertEqual(self.source.role_statistics('2026-08')['totals'], target.role_statistics('2026-08')['totals'])
        self.assertEqual(self.original, self.source.backup())

    def test_reimport_retains_newer_local_history_and_deduplicates(self):
        target = self.target()
        incoming = import_complete_configuration(self.content)
        self.command(target, 'import-complete-configuration', incoming)
        pid = next(iter(self.original['state']['plans']))
        state = target.snapshot()
        target.execute(state['revision'], 'edit-cell', {'cellId': 'c0', 'text': '乙'}, pid)
        state = target.snapshot()
        target.execute(state['revision'], 'adopt', {}, pid)
        retained = deepcopy(target.backup()['state']['plans'][pid])
        preview = target.preview_complete_configuration(incoming)
        self.assertEqual(0, preview['newHistoryPlanCount'])
        self.assertEqual(10, preview['retainedHistoryPlanCount'])
        self.command(target, 'import-complete-configuration', incoming)
        actual = target.backup()['state']['plans']
        self.assertEqual(retained, actual[pid])
        self.assertEqual(10, sum(bool(p['adopted']) for p in actual.values()))

    def test_history_remaps_existing_same_name_identities_and_preserves_latest_local_date(self):
        target = self.target()
        data = small_configuration(None)
        data['day']['serviceDate'] = '2026-08-01'
        self.command(target, 'import-complete-configuration', data)
        self.command(target, 'edit-cell', {'cellId': 'c0', 'text': '乙'})
        self.command(target, 'edit-cell', {'cellId': 'c1', 'text': '乙'})
        self.command(target, 'adopt')
        before = target.backup()['state']
        self.command(target, 'import-complete-configuration', import_complete_configuration(self.content))
        after = target.backup()['state']
        self.assertEqual([(p['id'], p['name']) for p in before['people']], [(p['id'], p['name']) for p in after['people']])
        self.assertEqual(before['plans'], {pid: after['plans'][pid] for pid in before['plans']})
        identities = {p['id'] for p in after['people']}
        for plan in after['plans'].values():
            if plan['adopted']:
                self.assertTrue(all(a['memberId'] in identities for a in plan['adopted']['assignments']))
        role = next(r for r in target.role_statistics('2026-08')['roles'] if r['roleName'] == '接机1')
        counts = {r['memberName']: r['count'] for r in role['members']}
        self.assertEqual({'甲': 9, '乙': 11}, counts)

    def test_invalid_or_incomplete_history_is_atomic_and_old_files_still_import(self):
        incoming = import_complete_configuration(self.content)
        target = self.target()
        incoming['history']['state']['plans'][next(iter(incoming['history']['state']['plans']))]['adopted']['assignments'][0]['memberId'] = 'unknown'
        before = target.backup()
        with self.assertRaises(ValueError):
            self.command(target, 'import-complete-configuration', incoming)
        self.assertEqual(before, target.backup())
        with self.assertRaisesRegex(ValueError, '配置Excel'):
            import_backup(self.content)
        def delete_history_sheet(book):
            del book['_历史快照']
        with self.assertRaises(ValueError):
            import_complete_configuration(self.edit_excel(delete_history_sheet))
        self.command(target, 'import-complete-configuration', small_configuration(None))
        self.assertEqual(1, len(target.snapshot()['plans']))

    def test_deleted_identity_and_same_name_new_person_remain_separate(self):
        state = self.source.snapshot()
        old_id = next(p['id'] for p in state['people'] if p['name'] == '甲')
        self.command(self.source, 'delete-person', {'id': old_id})
        self.command(self.source, 'save-person', {'name': '甲', 'active': True})
        content = self.source.export_complete_configuration(self.source.snapshot()['selectedPlanId'])
        target = self.target()
        self.command(target, 'import-complete-configuration', import_complete_configuration(content))
        people = [p for p in target.backup()['state']['people'] if p['name'] == '甲']
        self.assertEqual(2, len(people))
        self.assertEqual({False, True}, {p.get('deleted', False) for p in people})
        self.assertTrue(next(p for p in people if p['id'] == old_id)['deleted'])
        self.assertEqual(10, sum(p['adopted'] is not None for p in target.snapshot()['plans'].values()))

    def test_latest_manual_draft_and_valid_undo_migrate_without_breaking_preview(self):
        state = self.command(self.source, 'edit-cell', {'cellId': 'c0', 'text': '乙'})
        pid = state['selectedPlanId']
        incoming = import_complete_configuration(self.source.export_complete_configuration(pid))
        target = self.target()
        self.assertEqual(10, target.preview_complete_configuration(incoming)['newHistoryPlanCount'])
        state = self.command(target, 'import-complete-configuration', incoming)
        self.assertEqual(self.source.backup()['state']['plans'][pid]['draft'], target.backup()['state']['plans'][pid]['draft'])
        target.execute(state['revision'], 'undo-edit', {}, pid)
        self.assertEqual(self.original['state']['plans'][pid]['draft'], target.backup()['state']['plans'][pid]['draft'])


if __name__ == '__main__':
    unittest.main()
