from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from gate_schedule.backup_excel import append_backup_sheets, export_backup_excel
from gate_schedule.excel_io import import_complete_configuration
from gate_schedule.server import create_server_app
from gate_schedule.workbench import Workbench
from tests.test_balance_workflow import small_configuration


class ConfigurationTransferTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path.cwd() / '.tmp', prefix='compact-transfer-')
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'state.sqlite3'
        self.workbench = Workbench(self.path)
        self.client = TestClient(create_server_app(database_path=self.path), raise_server_exceptions=False)
        self.addCleanup(self.client.close)

    def test_empty_device_exports_and_imports_without_invented_flights(self):
        before = self.workbench.backup()
        response = self.client.get('/api/workspace/configuration/export?serviceDate=2026-11-03')
        self.assertEqual(200, response.status_code, response.text[:200] if response.status_code != 200 else '')
        incoming = import_complete_configuration(response.content)
        self.assertEqual([], incoming['day']['flights'])
        self.assertEqual(before, self.workbench.backup())
        preview = self.client.post('/api/workspace/data/preview', content=response.content)
        self.assertEqual(200, preview.status_code, preview.text)
        self.assertEqual('configuration', preview.json()['kind'])
        self.assertEqual(0, preview.json()['historyPlanCount'])
        result = self.client.post('/api/workspace/command', json={
            'action': 'import-complete-configuration', 'expectedRevision': 0, 'payload': preview.json()['payload']})
        self.assertEqual(200, result.status_code, result.text)
        state = Workbench(self.path).snapshot()
        self.assertEqual([], state['plans'][state['selectedPlanId']]['day']['flights'])

    def test_unified_preview_accepts_old_excel_and_json_backups_without_writes(self):
        source = self.workbench.backup()
        for content in (export_backup_excel(source), json.dumps(source).encode()):
            response = self.client.post('/api/workspace/data/preview', content=content)
            self.assertEqual(200, response.status_code, response.text)
            self.assertEqual('backup', response.json()['kind'])
            self.assertEqual(0, response.json()['expectedRevision'])
            self.assertEqual(source, self.workbench.backup())

    def test_v4_node_history_still_imports_and_corrupt_inputs_are_atomic(self):
        self.workbench.execute(0, 'import-complete-configuration', small_configuration(None))
        content = self.workbench.export_complete_configuration()
        book = load_workbook(BytesIO(content))
        for name in ['历史目录', '历史航班', '历史排班', '历史说明', '_历史快照']:
            del book[name]
        # Version 4 carried exactly the same history in the old lossless node tables.
        for row in book['使用说明'].iter_rows():
            for cell in row:
                if cell.value == '登机口完整配置-5':
                    cell.value = '登机口完整配置-4'
        append_backup_sheets(book, self.workbench.backup(), configuration=True)
        stream = BytesIO()
        book.save(stream)
        book.close()
        response = self.client.post('/api/workspace/data/preview', content=stream.getvalue())
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual('configuration', response.json()['kind'])
        self.assertEqual(1, response.json()['historyPlanCount'])
        before = self.workbench.backup()
        for content in (b'PKbroken', b'not an excel file', content[:100]):
            response = self.client.post('/api/workspace/data/preview', content=content)
            self.assertEqual(422, response.status_code, response.text)
            self.assertEqual(before, self.workbench.backup())


if __name__ == '__main__':
    unittest.main()
