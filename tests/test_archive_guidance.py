from copy import deepcopy
from unittest.mock import patch
import unittest
import test_workspace as fixtures
from gate_schedule.workbench import Workbench, WorkspaceError


class ArchiveGuidanceTests(unittest.TestCase):
    setUp=fixtures.WorkspaceTests.setUp
    tearDown=fixtures.WorkspaceTests.tearDown
    command=fixtures.WorkspaceTests.command
    complete_plan=fixtures.WorkspaceTests.complete_plan

    def source(self):
        pid,_=self.complete_plan()
        self.command('solve',{},pid)
        return pid,self.workspace.snapshot()['plans'][pid]

    def test_archive_without_people_does_not_solve_or_advance(self):
        pid,p=self.source();day=deepcopy(p['day']);day['serviceDate']='2026-08-04'
        for a in day['memberAvailability']:a.update(participating=False,availableFrom=None,availableUntil=None)
        before=self.workspace.backup()
        with patch('gate_schedule.workbench.solve',side_effect=AssertionError('No people must not call solver')):
            with self.assertRaisesRegex(WorkspaceError,'请选择当天参加人员'):
                self.command('archive-next',{'day':day},pid)
        self.assertEqual(before,self.workspace.backup())

    def test_legacy_wall_of_diagnostics_is_readable_without_data_write(self):
        pid,p=self.source();day=deepcopy(p['day']);day['serviceDate']='2026-08-04'
        state=self.command('archive-next',{'day':day},pid);nid=state['selectedPlanId']
        backup=self.workspace.backup()
        for a in backup['state']['plans'][nid]['day']['memberAvailability']:a['participating']=False
        backup['state']['plans'][nid].update(draft=[],preview=None)
        original='原班表已归档；新日期暂时无合法安排，已保留未完成草稿：'+('任务 imported-c1 可用的合格内部人员不足：需要2，仅有0；'*45)
        backup['state']['plans'][nid]['message']=original
        self.workspace.restore(state['revision'],backup)
        before=self.workspace.backup();p=self.workspace.snapshot()['plans'][nid]
        self.assertNotIn('imported-',p['message']);self.assertLess(len(p['message']),150)
        self.assertIn('选择当天人员',p['preparationNotice'])
        self.assertEqual(before,self.workspace.backup());self.assertEqual(original,before['state']['plans'][nid]['message'])

    def test_real_shortage_advances_to_explicit_partial_draft_but_cannot_archive_again(self):
        pid,p=self.source();day=deepcopy(p['day']);day['serviceDate']='2026-08-04'
        for role in day['roleConfigs']:role['requiredCount']=4
        state=self.command('archive-next',{'day':day},pid)
        target=state['plans'][state['selectedPlanId']]
        self.assertTrue(state['plans'][pid]['adopted'])
        self.assertFalse(target['valid']);self.assertEqual(3,len(target['draft']))
        self.assertIn('AK151',target['message']);self.assertNotIn('imported-',target['message'])
        saved=self.workspace.backup()
        with self.assertRaises(WorkspaceError):self.command('adopt',{},target['id'])
        self.assertEqual(saved,self.workspace.backup())

    def test_continuous_archive_reuses_selected_configuration_without_extra_adopt(self):
        pid,p=self.source()
        for service_date in ('2026-08-04','2026-08-06','2026-08-08'):
            day=deepcopy(p['day']);day['serviceDate']=service_date
            for a in day['memberAvailability']:
                a.update(participating=a['memberName']!='丙',availableFrom=None,availableUntil=None)
            for c in day['cells']:c.update(workStart=None,workEnd=None)
            before=deepcopy(p)
            state=self.command('archive-next',{'day':day},pid)
            self.assertIsNotNone(state['plans'][pid]['adopted'])
            self.assertEqual(before['draft'],state['plans'][pid]['draft'])
            p=state['plans'][state['selectedPlanId']];pid=p['id']
            self.assertEqual(service_date,p['day']['serviceDate'])
            self.assertTrue(p['valid'],p['issues'])
            self.assertIsNone(p['adopted'])
            self.assertEqual(2,len(p['draft']))
            self.assertEqual(2,sum(a['participating'] for a in p['day']['memberAvailability']))
            self.assertEqual(2,p['day']['roleConfigs'][0]['requiredCount'])
        restored=Workbench(self.path.parent/'continuous.sqlite3');restored.restore(0,self.workspace.backup())
        self.assertEqual(self.workspace.snapshot()['plans'],restored.snapshot()['plans'])

    def test_missing_next_day_input_keeps_current_manual_draft_and_formal(self):
        pid,p=self.source()
        self.command('adopt',{},pid)
        people=self.workspace.snapshot()['people']
        self.command('assign',{'assignments':[{'taskId':p['draft'][0]['taskId'],'memberId':person['id']} for person in people[1:]]},pid)
        before=self.workspace.backup();day=deepcopy(p['day']);day['serviceDate']='2026-08-04'
        day['roleConfigs'][0].update(workMode='timed')
        for c in day['cells']:c.update(workStart=None,workEnd=None)
        with self.assertRaises(WorkspaceError):self.command('archive-next',{'day':day},pid)
        self.assertEqual(before,self.workspace.backup())
