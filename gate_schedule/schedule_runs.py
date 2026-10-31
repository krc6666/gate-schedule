"""Background scheduling with atomic cancellation and revision-protected persistence."""
from copy import deepcopy
from threading import Event, Lock, Thread
from uuid import uuid4
from .domain import draft_blocking_issues
from .validator import validate_schedule
from .workbench import WorkspaceError


class RunCancelled(WorkspaceError):
    pass


class RunControl:
    STEPS = ("准备排班资料", "检查输入与资格", "建立岗位与人员候选", "检查冲突、固定与轮换", "计算人员安排", "独立复查资格、冲突与缺口", "保存草稿与核对数据版本")

    def __init__(self):
        self.lock = Lock()
        self.stopped = Event()
        self.keep = False
        self.solver = None
        self.latest = None
        self.view = {"status": "running", "stage": "准备排班资料", "stages": ["准备排班资料"], "solutionCount": 0, "canKeep": False}

    def stage(self, label):
        with self.lock:
            if self.stopped.is_set() and not self.keep:
                raise RunCancelled("计算已停止，原班表和日期保留")
            self.view["stage"] = label
            if label not in self.view["stages"]:
                self.view["stages"].append(label)

    def attach_solver(self, solver):
        with self.lock:
            self.solver = solver
            if solver and self.stopped.is_set():
                solver.stop_search()

    def solution(self, scenario, assignments):
        if draft_blocking_issues(validate_schedule(scenario, assignments)):
            return
        with self.lock:
            self.latest = assignments
            self.view.update(canKeep=True, solutionCount=self.view["solutionCount"] + 1)

    def stop(self, keep):
        with self.lock:
            if self.view["status"] != "running":
                return
            if keep and self.latest is None:
                raise WorkspaceError("尚无通过校验的安排，请稍候或选择停止不采用")
            self.keep = keep
            self.stopped.set()
            if self.solver:
                self.solver.stop_search()

    def finish(self, commit):
        with self.lock:
            if self.stopped.is_set() and not self.keep:
                raise RunCancelled("计算已停止，原班表和日期保留")
            result = commit()
            self.view.update(status="completed", stage="草稿已保存", revision=result["revision"])
            return result

    def progress(self):
        """Pipeline steps only: never estimate the solver's search completion."""
        done = self.view['status'] == 'completed'
        index = len(self.STEPS) if done else self.STEPS.index(self.view['stage'])
        status = self.view['status']
        return {'percent': 100 * index // len(self.STEPS), 'completedSteps': index, 'totalSteps': len(self.STEPS),
                'steps': [{'label': label, 'status': 'completed' if i < index else ('active' if status == 'running' else 'failed' if status == 'failed' else 'stopped') if i == index else 'pending'} for i, label in enumerate(self.STEPS)]}


class ScheduleRuns:
    def __init__(self, workspace):
        self.workspace = workspace
        self.lock = Lock()
        self.runs = {}

    def start(self, expected, action, payload, plan_id):
        if action not in {"solve", "prepare-solve", "archive-next"}:
            raise WorkspaceError("此操作无需后台排班")
        with self.lock:
            if any(c.view["status"] == "running" for c, _ in self.runs.values()):
                raise WorkspaceError("已有排班正在计算，请等待或停止后重试")
            control = RunControl()
            run_id = str(uuid4())
            def work():
                try:
                    self.workspace.execute(expected, action, deepcopy(payload), plan_id, control=control)
                except RunCancelled as error:
                    with control.lock:
                        control.view.update(status="cancelled", message=str(error))
                except Exception as error:
                    with control.lock:
                        control.view.update(status="failed", message=str(error) if isinstance(error, (ValueError, KeyError, TypeError)) else "计算或保存失败，原数据保留，请重新读取后重试")
            thread = Thread(target=work, daemon=True)
            self.runs[run_id] = (control, thread)
            for old in list(self.runs)[:-20]:
                del self.runs[old]
            thread.start()
        return self.get(run_id)

    def get(self, run_id):
        if run_id not in self.runs:
            raise WorkspaceError("计算任务不存在，请重新读取班表")
        control, _ = self.runs[run_id]
        with control.lock:
            return {"id": run_id, **deepcopy(control.view), **control.progress()}

    def stop(self, run_id, keep=False):
        if run_id not in self.runs:
            raise WorkspaceError("计算任务不存在")
        self.runs[run_id][0].stop(keep)
        return self.get(run_id)

    def join(self, run_id, timeout):
        self.runs[run_id][1].join(timeout)
