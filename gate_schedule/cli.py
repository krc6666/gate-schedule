from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .io import scenario_from_dict
from .domain import format_minutes
from .projections import member_paths, task_matrix
from .solver import solve
from .validator import validate_schedule


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="登机口保障排班阶段 0 求解原型")
    parser.add_argument("input", type=Path, help="领域事实 JSON 文件")
    parser.add_argument("--time-limit", type=float, default=5, help="CP-SAT 求解时限（秒）")
    args = parser.parse_args()
    try:
        scenario = scenario_from_dict(json.loads(args.input.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "MODEL_INVALID", "diagnostics": [f"无法读取领域事实：{error}"]}, ensure_ascii=False))
        return 2

    result = solve(scenario, args.time_limit)
    payload = {
        "status": result.status,
        "assignments": [asdict(item) for item in result.assignments],
        "externalSupportByTask": result.external_support_by_task,
        "externalSupportRequirements": [asdict(item) for item in scenario.external_support_requirements],
        "diagnostics": result.diagnostics,
        "validationIssues": [asdict(item) for item in validate_schedule(scenario, result.assignments)] if result.assignments else [],
        "taskMatrix": task_matrix(scenario, result.assignments),
        "taskTimes": {t.id: {"start": format_minutes(t.start), "end": format_minutes(t.end)} for t in scenario.tasks},
        "memberPaths": member_paths(scenario, result.assignments),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.status in {"OPTIMAL", "FEASIBLE"} else 2
