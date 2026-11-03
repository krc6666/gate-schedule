from io import BytesIO
import unittest

from openpyxl import Workbook, load_workbook

from gate_schedule.configuration_archive import append_configuration_history, read_configuration_history


class ConfigurationArchiveTests(unittest.TestCase):
    def content(self):
        return {'formatVersion': 2, 'state': {'people': [], 'plans': {}, 'revision': 0,
            'edge': [None, False, True, 900719925474099312345, -0.0, '', [], {},
                     '=NOW()', '甲\r\n乙\x00\ud800', '🙂中文' * 16000]}}

    def workbook(self):
        book = Workbook()
        del book[book.sheetnames[0]]
        append_configuration_history(book, self.content(), {})
        stream = BytesIO()
        book.save(stream)
        book.close()
        return load_workbook(BytesIO(stream.getvalue()))

    def test_lossless_compact_history_survives_excel_save_and_has_business_tables(self):
        book = self.workbook()
        self.addCleanup(book.close)
        self.assertEqual(self.content(), read_configuration_history(book))
        self.assertIn('历史航班', book.sheetnames)
        self.assertIn('历史排班', book.sheetnames)
        self.assertEqual('hidden', book['_历史快照'].sheet_state)
        self.assertLess(book['_历史快照'].max_row, 10)
        self.assertIs(type(read_configuration_history(book)['state']['edge'][3]), int)

    def test_missing_corrupt_formula_or_duplicate_chunk_never_silently_drops_history(self):
        for mode in ('missing', 'corrupt', 'formula', 'duplicate'):
            with self.subTest(mode=mode):
                book = self.workbook()
                try:
                    sheet = book['_历史快照']
                    if mode == 'missing': sheet.delete_rows(2)
                    elif mode == 'corrupt': sheet['B2'] = 'invalid-data'
                    elif mode == 'formula': sheet['B2'] = '=NOW()'
                    else: sheet.append([sheet['A2'].value, sheet['B2'].value])
                    with self.assertRaises(ValueError):
                        read_configuration_history(book)
                finally:
                    book.close()


if __name__ == '__main__':
    unittest.main()
