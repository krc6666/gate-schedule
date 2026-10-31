import json
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from gate_schedule.workbench import Workbench, WorkspaceError


ROOT = Path(__file__).resolve().parents[1]


class TrialWorkspaceTests(unittest.TestCase):
    def test_single_file_can_restore_and_resolve_without_other_imports(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "trial.json"
            source = ROOT / "backups/20261029-full-trial/source.md"
            result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/build-trial-workspace.py"), str(source), "--output", str(output)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(0, result.returncode, result.stderr)
            config = json.loads(output.read_text(encoding="utf-8"))
            workspace = Workbench(Path(directory) / "restored.sqlite3")
            state = workspace.restore(0, config)
            self.assertEqual(15, len(state["people"]))
            self.assertEqual(115, sum(len(p["eligibleRoleNames"]) for p in state["people"]))
            self.assertEqual(1, len(state["plans"]))
            plan_id = state["selectedPlanId"]
            plan = state["plans"][plan_id]
            self.assertEqual(7, len(plan["day"]["flights"]))
            self.assertEqual(70, len(plan["day"]["cells"]))
            self.assertTrue(plan["valid"], plan["issues"])
            self.assertTrue(plan["draft"])
            self.assertIsNone(plan["adopted"])
            self.assertIn("assumptions", state["trialInfo"])
            malformed = deepcopy(config)
            malformed["state"]["trialInfo"]["assumptions"] = "无效说明"
            with self.assertRaises(WorkspaceError):
                workspace.restore(state["revision"], malformed)
            self.assertEqual(state, workspace.snapshot())
            self.assertTrue(plan["day"]["title"].startswith("试用示例"))
            tw = next(f for f in plan["day"]["flights"] if f["flightNo"] == "TW616")
            self.assertEqual("次日00:10", tw["windowStart"])
            original = plan["draft"]
            state = workspace.execute(state["revision"], "solve", {}, plan_id)
            self.assertTrue(state["plans"][plan_id]["valid"])
            state = workspace.execute(state["revision"], "cancel-preview", {}, plan_id)
            self.assertEqual(original, state["plans"][plan_id]["draft"])
            state = workspace.execute(state["revision"], "adopt", {}, plan_id)
            self.assertTrue(workspace.export(plan_id).startswith(b"PK"))
            restored = Workbench(Path(directory) / "second.sqlite3")
            restored.restore(0, workspace.backup())
            self.assertEqual(state["plans"], restored.snapshot()["plans"])
            result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/build-trial-workspace.py"), str(source), "--output", str(output)], cwd=ROOT, capture_output=True)
            self.assertNotEqual(0, result.returncode, "已有文件必须拒绝覆盖")


if __name__ == "__main__":
    unittest.main()
