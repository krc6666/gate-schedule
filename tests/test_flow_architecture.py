"""Exercise domain explanations, snapshot ownership and adapter independence."""
from dataclasses import replace
from collections import Counter
import subprocess
import sys
import unittest
from unittest.mock import patch

from gate_schedule import domain, solver
from gate_schedule.domain import Assignment, Flight
from gate_schedule.manual_editing import candidate_options
from gate_schedule.validator import validate_schedule
from tests.test_flow_scheduling import flow_scene, preference


class FlowArchitectureTests(unittest.TestCase):
    def test_new_snapshot_does_not_consult_legacy_preference_reader(self):
        scene = flow_scene()
        with patch.object(solver, 'preferred_handoff', side_effect=AssertionError('legacy reader')):
            result = solver.solve(scene, handoff_preferences=[preference(['接机2'])])
        self.assertEqual([], validate_schedule(scene, result.assignments))
        self.assertIn(Assignment('r1', '甲'), result.assignments)
        legacy = replace(scene, flow_preferences=None)
        with self.assertRaises(ValueError):
            solver.solve(legacy, handoff_preferences=[{'invalid': True}])

    def test_candidate_relationship_uses_domain_and_respects_direction(self):
        scene = flow_scene()
        self.assertEqual('continuation', domain.task_candidate_relationship(scene, (scene.tasks[0],), scene.tasks[2]))
        self.assertEqual('available', domain.task_candidate_relationship(scene, (scene.tasks[2],), scene.tasks[0]))
        # A configured source can be later than its target; exact blocks must
        # never be described as a forward continuation in that direction.
        exact = replace(scene, tasks=(replace(scene.tasks[0], start=600, end=660),
                                     replace(scene.tasks[2], start=480, end=540, timing_mode='exact')))
        option = next(c for c in candidate_options(exact, (Assignment('bag1','甲'),), 'r1') if c['id'] == '甲')
        self.assertEqual((True, 'available'), (option['available'], option['status']))
        cross = replace(scene, flights=(*scene.flights, Flight('B','B',scene.service_date,720,780)),
            tasks=(replace(scene.tasks[0],flight_id='B',start=720,end=780), scene.tasks[2]))
        option = next(c for c in candidate_options(cross, (Assignment('bag1','甲'),), 'r1') if c['id'] == '甲')
        self.assertEqual((True, 'available'), (option['available'], option['status']))

    def test_candidate_hard_conflict_overrides_process_explanation(self):
        scene = flow_scene()
        scene = replace(scene, tasks=tuple(replace(t,exclusive_same_flight=True) if t.id=='r1' else t for t in scene.tasks))
        option = next(c for c in candidate_options(scene,(Assignment('bag1','甲'),),'r1') if c['id']=='甲')
        self.assertFalse(option['available'])
        self.assertEqual('blocked', option['status'])
        self.assertIn('exclusive_same_flight', {i['code'] for i in option['issues']})

    def test_new_conflict_path_does_not_read_legacy_confirmations(self):
        scene = flow_scene()
        with patch.object(domain,'confirmed_task_handoff',side_effect=AssertionError('legacy confirmation')):
            self.assertIsNone(domain.task_pair_conflict(scene,scene.tasks[0],scene.tasks[2]))
            self.assertIsNotNone(domain.task_pair_conflict(scene,scene.tasks[0],replace(scene.tasks[1],start=530)))
        legacy = replace(scene,flow_preferences=None)
        with patch.object(domain,'confirmed_task_handoff',return_value=False) as reader:
            domain.task_pair_conflict(legacy,legacy.tasks[0],legacy.tasks[2])
        self.assertTrue(reader.called)

    def test_person_independent_pair_check_is_reused_within_one_solve(self):
        original = flow_scene()
        scene = replace(original, members=(*original.members, *(replace(original.members[0],id=f'p{i}',name=f'p{i}') for i in range(21))))
        counts = Counter()
        check = solver.task_pair_conflict
        def measured(scenario, first, second):
            counts[first.id, second.id] += 1
            return check(scenario, first, second)
        with patch.object(solver, 'task_pair_conflict', side_effect=measured):
            result = solver.solve(scene)
        self.assertEqual([], validate_schedule(scene, result.assignments))
        self.assertTrue(counts)
        self.assertEqual(1, max(counts.values()))
        # A subsequent edit must be checked afresh, never served from an old
        # Scenario's conflict cache.
        edited = replace(original, tasks=tuple(replace(t,exclusive_same_flight=True) if t.id=='r1' else t for t in original.tasks))
        result = solver.solve(edited)
        self.assertEqual([], validate_schedule(edited, result.assignments))
        self.assertNotIn(Assignment('r1','甲'), result.assignments)

    def test_day_document_codec_loads_without_excel_library(self):
        script = '''
import builtins
real_import = builtins.__import__
def without_excel(name, *args, **kwargs):
    if name == 'openpyxl' or name.startswith('openpyxl.'):
        raise AssertionError('JSON document must not import Excel')
    return real_import(name, *args, **kwargs)
builtins.__import__ = without_excel
from gate_schedule.day_document import imported_day_from_dict, imported_day_to_dict, validate_imported_day
raw = dict(serviceDate='2057-08-02', title='empty', flights=[], roleNames=[], cells=[], remarks=[])
for marker in (False, True):
    if marker: raw['flowPreferences'] = []
    day = imported_day_from_dict(raw)
    assert validate_imported_day(day) == []
    saved = imported_day_to_dict(day)
    assert ('flowPreferences' in saved) == marker
    assert imported_day_from_dict(saved) == day
'''
        result = subprocess.run([sys.executable, '-B', '-c', script], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)


if __name__ == '__main__':
    unittest.main()
