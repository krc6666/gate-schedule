"""Read-only legacy SQLite/JSON migration into a new independent workspace."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gate_schedule.workbench import Workbench


def main():
    parser = argparse.ArgumentParser(description="旧数据迁移演练：只读来源，拒绝覆盖目标")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    destination = args.destination.resolve()
    if destination.exists() or args.report.exists() or source == destination:
        parser.error("目标或报告已存在；请换一个新的目录/文件名，原文件不会被覆盖")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    if source.suffix.lower() in (".sqlite3", ".sqlite", ".db"):
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as connection:
            row = connection.execute("SELECT payload FROM app_state WHERE key='workspace-v2'").fetchone()
            if row:
                backup = {"formatVersion": 2, "state": json.loads(row[0])}
            else:
                row = connection.execute("SELECT payload FROM app_state WHERE key='current'").fetchone()
                if not row:
                    raise ValueError("旧库没有可恢复状态")
                backup = {"formatVersion": 1, "state": json.loads(row[0])}
    else:
        backup = json.loads(source.read_text(encoding="utf-8"))
    # Validate in a temporary independent database before creating the deliverable.
    import tempfile
    temporary_root = Path(__file__).resolve().parents[1] / ".tmp"
    temporary_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=temporary_root) as directory:
        rehearsal = Workbench(Path(directory) / "rehearsal.sqlite3")
        state = rehearsal.restore(0, backup)
        verified_backup = rehearsal.backup()
        for plan_id, plan in state["plans"].items():
            if plan["adopted"]:
                rehearsal.export(plan_id)
        restored = Workbench(Path(directory) / "restore.sqlite3")
        restored.restore(0, verified_backup)
        assert restored.backup() == verified_backup
    workspace = Workbench(destination)
    workspace.restore(0, verified_backup)
    report = {
        "source": str(source), "sourceSha256Before": before,
        "sourceSha256After": hashlib.sha256(source.read_bytes()).hexdigest(),
        "destination": str(destination), "formatVersion": 2,
        "plans": len(state["plans"]), "adoptedPlans": sum(bool(p["adopted"]) for p in state["plans"].values()),
        "adoptedAssignments": sum(len(p["adopted"]["assignments"]) for p in state["plans"].values() if p["adopted"]),
        "migrationReport": state["migrationReport"], "restoreVerified": True, "formalExportVerified": True,
        "note": "只读来源；新库演练，未切换真实数据；未重新求解",
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
