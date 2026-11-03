from copy import deepcopy
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import tempfile
import unittest

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from gate_schedule.backup_excel import export_backup_excel, import_backup
from gate_schedule.server import create_server_app
from gate_schedule.workbench import Workbench
from tests.test_balance_workflow import small_configuration


class ExcelBackupTests(unittest.TestCase):
    def workspace(self, filename='state.sqlite3'):
        directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp', prefix='excel-backup-')
        self.addCleanup(directory.cleanup)
        path = Path(directory.name)/filename
        return Path(directory.name), Workbench(path), TestClient(create_server_app(database_path=path))

    def command(self, wb, action, payload=None, pid=None):
        return wb.execute(wb.backup()['state']['revision'], action, payload or {}, pid)

    def fixture(self):
        directory, wb, client = self.workspace()
        config = small_configuration(None)
        config['relaxedShiftSettings'] = {'earlyDepartureCutoffTime': '15:00', 'afternoonRestStartTime': '12:00',
            'afternoonRestEndTime': '18:00', 'lateShiftEndTime': '23:00', 'lateFlightNos': ['F1']}
        state = self.command(wb, 'import-complete-configuration', config)
        pid = state['selectedPlanId']
        self.command(wb, 'edit-cell', {'cellId': 'c0', 'text': '甲'}, pid)
        self.command(wb, 'edit-cell', {'cellId': 'c1', 'text': '乙'}, pid)
        self.command(wb, 'adopt', pid=pid)
        self.command(wb, 'edit-cell', {'cellId': 'c0', 'text': '乙'}, pid)
        return directory, wb, client, pid

    def test_genuine_xlsx_keeps_every_type_long_unicode_and_formula_like_text(self):
        backup = {'formatVersion': 2, 'state': {'people': [], 'plans': {}, 'edgeCases': [None, True, False, 0,
            900719925474099312345, -0.0, 0.125, {}, [], '', '=NOW()', '+SUM(A1)', '_x0041_', '甲\r\n乙\t丙',
            '🙂中文'*18000, '原文\x00\x0b\ud800结束', {'': '', '长键'*10000: '值'}]}}
        content = export_backup_excel(backup)
        self.assertTrue(content.startswith(b'PK'))
        book = load_workbook(BytesIO(content), data_only=False)
        self.assertIn('备份说明', book.sheetnames)
        self.assertIn('人员一览', book.sheetnames)
        self.assertIn('方案一览', book.sheetnames)
        for sheet in book:
            for row in sheet:
                self.assertFalse(any(c.data_type == 'f' for c in row), '原文不能变成Excel公式')
        book.close()
        restored = import_backup(content)
        self.assertEqual(backup, restored)
        self.assertIs(type(restored['state']['edgeCases'][3]), int)
        self.assertIs(type(restored['state']['edgeCases'][5]), float)

    def test_http_download_restore_into_empty_database_restart_and_real_use(self):
        directory, wb, client, pid = self.fixture()
        original = wb.backup()
        response = client.get('/api/workspace/backup.xlsx')
        self.assertEqual(200, response.status_code)
        self.assertIn('spreadsheetml.sheet', response.headers['content-type'])
        self.assertIn('.xlsx', response.headers['content-disposition'])
        self.assertEqual(original, import_backup(response.content))
        self.assertEqual(original, wb.backup(), '导出只读')
        target_path = directory/'target.sqlite3'
        target = TestClient(create_server_app(database_path=target_path))
        restored = target.post('/api/workspace/restore?expectedRevision=0', content=response.content,
            headers={'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'})
        self.assertEqual(200, restored.status_code, restored.text)
        new_wb = Workbench(target_path)
        self.assertEqual(original['state']['plans'][pid]['adopted'], new_wb.backup()['state']['plans'][pid]['adopted'])
        self.assertEqual(original['state']['plans'][pid]['draft'], new_wb.backup()['state']['plans'][pid]['draft'])
        self.assertEqual(original['state']['relaxedShiftSettings'], new_wb.backup()['state']['relaxedShiftSettings'])
        self.assertEqual(wb.role_statistics('2026-08')['roles'], new_wb.role_statistics('2026-08')['roles'])
        self.assertEqual(wb.relaxed_shift_statistics('2026-08')['rows'], new_wb.relaxed_shift_statistics('2026-08')['rows'])
        state = self.command(new_wb, 'undo-edit', pid=pid)
        self.assertTrue(state['plans'][pid]['valid'], '恢复后撤销仍可继续使用')
        self.command(new_wb, 'adopt', pid=pid)
        self.assertEqual(original, wb.backup())

    def test_invalid_truncated_formula_wrong_file_and_stale_revision_never_write(self):
        _, wb, client, _ = self.fixture()
        before = wb.backup()
        content = client.get('/api/workspace/backup.xlsx').content
        variants = [b'not an Excel backup', content[:200]]
        for mode in ['formula', 'missing-row', 'invalid-reference', 'wrong-marker']:
            book = load_workbook(BytesIO(content))
            data = book['完整数据001']
            if mode == 'formula': data['G2'] = '=NOW()'
            if mode == 'missing-row': data.delete_rows(data.max_row)
            if mode == 'invalid-reference': data['B3'] = 999999
            if mode == 'wrong-marker': book['备份说明']['B2'] = '其他Excel'
            stream = BytesIO(); book.save(stream); book.close(); variants.append(stream.getvalue())
        for invalid in variants:
            with self.subTest(size=len(invalid)):
                response = client.post('/api/workspace/restore?expectedRevision='+str(before['state']['revision']), content=invalid)
                self.assertEqual(422, response.status_code, response.text)
                self.assertEqual(before, wb.backup())
        stale = client.post('/api/workspace/restore?expectedRevision=0', content=content)
        self.assertEqual(422, stale.status_code)
        self.assertEqual(before, wb.backup())

    def test_old_json_and_empty_excel_remain_restorable(self):
        import json
        _, wb, client, _ = self.fixture()
        before = wb.backup()
        self.assertEqual(before, import_backup(json.dumps(before, ensure_ascii=False).encode('utf-8-sig')))
        response = client.post('/api/workspace/restore?expectedRevision='+str(before['state']['revision']), json=before)
        self.assertEqual(200, response.status_code, response.text)
        _, empty, _ = self.workspace()
        self.assertEqual(empty.backup(), import_backup(export_backup_excel(empty.backup())))

    def test_split_data_sheets_preserve_whole_document(self):
        _, wb, _, _ = self.fixture()
        before = wb.backup()
        with patch('gate_schedule.backup_excel._ROWS_PER_SHEET', 30):
            content = export_backup_excel(before)
        self.assertEqual(before, import_backup(content))
