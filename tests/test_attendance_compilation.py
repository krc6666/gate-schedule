"""Public daily conversion must not rescan every position for every person."""
from dataclasses import replace
import unittest
from unittest.mock import patch

from gate_schedule import domain
from gate_schedule.day_document import imported_day_from_dict
from gate_schedule.domain import ImportedMemberAvailability, ImportedMemberConfig, ImportedOccupancy
from gate_schedule.imported_schedule import imported_day_to_scenario
from tests.test_relative_scheduling import relative_day


def large_day():
    day = imported_day_from_dict(relative_day())
    names = tuple(f'P{i}' for i in range(40))
    return replace(day, roster=names,
        member_configs=tuple(ImportedMemberConfig(n, day.role_names) for n in names),
        member_availability=tuple(ImportedMemberAvailability(n, True) for n in names))


class AttendanceCompilationTests(unittest.TestCase):
    def test_conversion_scans_default_start_once_for_all_people(self):
        day = large_day()
        with patch.object(domain, 'imported_task_timing', wraps=domain.imported_task_timing) as timing:
            scene, issues = imported_day_to_scenario(day)
        self.assertFalse(issues)
        self.assertEqual(40, len(scene.members))
        self.assertEqual({-20}, {m.available_from for m in scene.members})
        self.assertEqual({None}, {m.available_until for m in scene.members})
        self.assertLessEqual(timing.call_count, len(day.cells), 'Default day start was rescanned per person')

    def test_validation_scans_once_and_keeps_each_person_error(self):
        day = large_day()
        entries = day.member_availability + (
            ImportedMemberAvailability('P0', True, '08:00', None),
            ImportedMemberAvailability('P1', True, 'bad', '09:00'),
            ImportedMemberAvailability('P2', True, '10:00', '09:00'))
        day = replace(day, member_availability=entries)
        with patch.object(domain, 'imported_task_timing', wraps=domain.imported_task_timing) as timing:
            issues = domain.validate_imported_day(day)
        self.assertEqual(['duplicate_attendance_member', 'incomplete_availability',
                          'duplicate_attendance_member', 'invalid_availability',
                          'duplicate_attendance_member', 'availability_order'], [i.code for i in issues])
        self.assertLessEqual(timing.call_count, len(day.cells))

    def test_independent_inputs_and_explicit_times_keep_their_own_bounds(self):
        day = large_day()
        earlier = replace(day, extra_occupancies=(ImportedOccupancy('x', 'early', ('P0',), '前日23:00', '00:00'),))
        for data, expected in ((day, -20), (earlier, -60), (day, -20)):
            scene, issues = imported_day_to_scenario(data)
            self.assertFalse(issues)
            self.assertEqual({expected}, {m.available_from for m in scene.members})
        explicit = replace(day, member_availability=tuple(
            replace(a, available_from='前日23:00', available_until='次日00:20') for a in day.member_availability))
        with patch.object(domain, 'imported_task_timing', wraps=domain.imported_task_timing) as timing:
            scene, issues = imported_day_to_scenario(explicit)
        self.assertFalse(issues)
        self.assertEqual({(-60, 1460)}, {(m.available_from, m.available_until) for m in scene.members})
        self.assertEqual(0, timing.call_count, 'Explicit bounds should not scan default task times')

    def test_bounded_mode_still_rejects_missing_times(self):
        day = replace(large_day(), attendance_mode='bounded')
        scene, issues = imported_day_to_scenario(day)
        self.assertFalse(scene.members)
        self.assertEqual(['incomplete_availability'] * 40, [i.code for i in issues])


if __name__ == '__main__':
    unittest.main()
