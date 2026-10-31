"""只读回放规则 Markdown 的完整日表；不是现场原始 XLSX 验收。"""

import argparse
from io import BytesIO
import json
from pathlib import Path
import re
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rules", type=Path, required=True, help="排班事实 Markdown")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from openpyxl import Workbook
    from gate_schedule.excel_io import import_day
    from gate_schedule.imported_schedule import imported_day_to_scenario

    sections = re.findall(
        r"^## (2026\d{4})\n(.*?)(?=^## |\Z)",
        args.rules.read_text(encoding="utf-8"), re.M | re.S,
    )
    if not sections:
        parser.error("未找到形如 ## 20260802 的日表章节")
    failures = []
    days = []
    for date, content in sections:
        rows = [
            [part.strip() for part in line.strip().strip("|").split("|")]
            for line in content.splitlines() if line.startswith("|")
        ]
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["服务" + date])
        sheet.append(rows[0])
        for row in rows[2:]:
            sheet.append(row)
        stream = BytesIO()
        workbook.save(stream)
        try:
            day = import_day(stream.getvalue(), f"{date[:4]}-{date[4:6]}-{date[6:8]}")
            _, input_issues = imported_day_to_scenario(day)
            days.append({"date": date, "flights": len(day.flights), "next_day_flights": sum(f.window_start.startswith("次日") or f.window_end.startswith("次日") for f in day.flights), "ambiguous_cells": sum(c.semantics == "ambiguous" for c in day.cells), "missing_input_codes": sorted(set(i.code for i in input_issues)), "automatic_scheduling_attempted": False})
        except ValueError as error:
            failures.append({"date": date, "error": str(error)})
    print(json.dumps({
        "evidence": "真实 Markdown 重建完整日表；非现场原始 XLSX 验收",
        "total_days": len(sections), "passed": len(sections) - len(failures),
        "rejected": len(failures), "failures": failures,
        "automatic_scheduling_attempted": False,
        "days_with_incomplete_facts": sum(bool(d["missing_input_codes"]) for d in days),
        "days_with_ambiguous_cells": sum(bool(d["ambiguous_cells"]) for d in days),
        "days": days,
    }, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
