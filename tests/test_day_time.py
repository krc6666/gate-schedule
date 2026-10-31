import unittest
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from io import BytesIO

from openpyxl import Workbook, load_workbook

from gate_schedule.domain import parse_minutes, format_minutes
from gate_schedule.excel_io import import_day, export_imported_day, validate_imported_day
from gate_schedule.io import scenario_to_dict
from tests.test_phase_zero import feasible_scenario
from dataclasses import replace


class ServiceDayTimeTests(unittest.TestCase):
    def test_cli_labels_next_day_instead_of_ambiguous_clock_only(self):
        original = feasible_scenario()
        shifted = replace(original,
            flights=tuple(replace(f, window_start=f.window_start+900, window_end=f.window_end+900) for f in original.flights),
            members=tuple(replace(m, available_from=m.available_from+900) for m in original.members),
            tasks=tuple(replace(t, start=t.start+900, end=t.end+900) for t in original.tasks),
            rotations=tuple(replace(r, segment_start=r.segment_start+900, segment_end=r.segment_end+900) for r in original.rotations))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text(json.dumps(scenario_to_dict(shifted)), encoding="utf-8")
            result = subprocess.run([sys.executable, "-B", "-m", "gate_schedule", str(path)], capture_output=True, text=True, encoding="utf-8", check=True)
        output = json.loads(result.stdout)
        self.assertEqual("次日01:00", output["taskTimes"]["cx-baggage-3"]["end"])
    def test_continuous_time_axis_and_explicit_date_round_trip(self):
        for minute, label in ((0, "00:00"), (1385, "23:05"), (1445, "次日00:05"), (2890, "第2日00:10")):
            self.assertEqual(minute, parse_minutes(label))
            self.assertEqual(label, format_minutes(minute))
        for invalid in ("24:05", "00:65", "-1:00", "第-1日00:00", True, -1):
            with self.subTest(invalid=invalid), self.assertRaises((TypeError, ValueError)):
                parse_minutes(invalid)

    def test_complete_night_flight_headers_and_confirmed_tw616_are_preserved(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["保障日"])
        sheet.append(["岗位", "AK151 / 2305-0005", "TR121 / 2330-0055", "TW616 / 0010-0110"])
        sheet.append(["接机1", "甲", "乙", "丙"])
        stream = BytesIO()
        workbook.save(stream)
        day = import_day(stream.getvalue(), "2026-08-02")
        self.assertEqual(["次日00:05", "次日00:55", "次日01:10"], [flight.window_end for flight in day.flights])
        self.assertEqual("次日00:10", day.flights[2].window_start)
        self.assertEqual([], validate_imported_day(day))
        restored = import_day(export_imported_day(day), "2026-08-02")
        self.assertEqual([(f.window_start, f.window_end) for f in day.flights], [(f.window_start, f.window_end) for f in restored.flights])


if __name__ == "__main__":
    unittest.main()
