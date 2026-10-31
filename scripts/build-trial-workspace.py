"""Build one self-contained, clearly labelled trial workspace using existing commands."""
from __future__ import annotations

import argparse
from io import BytesIO
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile

from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gate_schedule.domain import format_minutes, parse_minutes
from gate_schedule.excel_io import import_day, imported_day_to_dict
from gate_schedule.historical_qualifications import analyze_history
from gate_schedule.workbench import Workbench


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("目标已存在，拒绝覆盖")
    source = args.source.read_bytes()
    text = source.decode("utf-8-sig").replace("\r\n", "\n")
    history = analyze_history(text, name_aliases={"李扬": "李杨"})
    section = re.search(r"^## 20260802\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not section:
        parser.error("来源没有20260802日表")
    rows = [[part.strip() for part in line.strip().strip("|").split("|")] for line in section[1].splitlines() if line.startswith("|")]
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["服务08.02"])
    sheet.append(rows[0])
    for row in rows[2:]:
        sheet.append(row)
    stream = BytesIO()
    workbook.save(stream)
    day = imported_day_to_dict(import_day(stream.getvalue(), "2026-08-02"))
    aliases = {record["rawRole"]: item["roleName"] for item in history["evidence"] for record in item["records"]}
    counts = {"督导": 1, "入境申报": 1, "接机1": 2, "接机2": 1, "大件行李拦截": 1, "登机主验": 1, "登机核验": 1, "登机查验（VIP/F/优先通道）": 0, "登机人证核对/队伍秩序维护": 2, "特服": 0}
    assumptions = [
        "这是看操作和排班效果的完整试用数据，不能作为2026-08-02现场真实班表。",
        "7个航班及窗口来自原文，TW616为次日；15人和115项资质来自30天历史建议，李扬统一李杨。",
        "试用出勤：15人均在当天10:00至次日02:00可用；不是现场已确认的实际出勤。",
        "试用时段：接机、入境申报为各航班开始后20分钟；登机各岗为结束前25分钟；督导覆盖航班窗口。",
        "试用人数：接机1和人证核对/秩序各2人，其他常用岗1人；VIP仅CX937为1人，特服为0。原斜杠在本试用中按同时搭档示例，不作为现场规则确认。",
        "试用大件：所有7航班均有1人需求，窗口合并共享并每60分钟轮换；走动预留为0。",
        "原0910支援只有姓名、无明确占用时段，本试用不安排该额外任务；原备注保留在配置来源信息中。",
        "当前矩阵由现有自动排班器求得，未锁定为原历史名单；只保存草稿，可再次自动排班、取消或手动调整。",
    ]
    original_remarks = day["remarks"]
    day["title"] = "试用示例（含假设）·2026-08-02完整配置"
    day["roleNames"] = [aliases.get(r, r) for r in day["roleNames"]]
    day["roleConfigs"] = [{"roleName": role, "requiredCount": counts[role]} for role in day["roleNames"]]
    day["roster"] = history["payload"]["people"]
    day["memberConfigs"] = [{"memberName": name, "eligibleRoleNames": [r["roleName"] for r in history["payload"]["roles"] if name in r["memberNames"]]} for name in day["roster"]]
    day["memberAvailability"] = [{"memberName": name, "participating": True, "availableFrom": "10:00", "availableUntil": "次日02:00"} for name in day["roster"]]
    day["extraOccupancies"] = []
    day["remarks"] = [{"kind": "other", "rawText": value, "people": [], "time": None} for value in assumptions]
    day["remarksReviewed"] = True
    day["transitionMinutes"] = 0
    flights = {f["id"]: f for f in day["flights"]}
    for cell in day["cells"]:
        cell["roleName"] = aliases.get(cell["roleName"], cell["roleName"])
        cell["internalNames"] = ["李杨" if name == "李扬" else name for name in cell["internalNames"] if name not in {"支援", "值机支援"}]
        if cell["semantics"] == "ambiguous":
            cell["semantics"] = "simultaneous"
        cell["fixedMemberNames"] = []
        cell["requiredCountOverride"] = 1 if cell["roleName"] == "登机查验（VIP/F/优先通道）" and cell["flightNo"] == "CX937" else None
        flight = flights[cell["flightId"]]
        start, end = parse_minutes(flight["windowStart"]), parse_minutes(flight["windowEnd"])
        if cell["roleName"].startswith("接机") or cell["roleName"] == "入境申报":
            cell["workStart"], cell["workEnd"] = format_minutes(start), format_minutes(start + 20)
        elif cell["roleName"].startswith("登机"):
            cell["workStart"], cell["workEnd"] = format_minutes(end - 25), format_minutes(end)
        elif cell["roleName"] == "督导":
            cell["workStart"], cell["workEnd"] = format_minutes(start), format_minutes(end)
        else:
            cell["workStart"], cell["workEnd"] = None, None
    temporary_root = Path(__file__).resolve().parents[1] / ".tmp"
    temporary_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="full-trial-build-", dir=temporary_root) as directory:
        workspace = Workbench(Path(directory) / "trial.sqlite3")
        state = workspace.execute(0, "import-qualifications", history["payload"])
        for role, count in counts.items():
            state = workspace.execute(state["revision"], "save-role", {"roleName": role, "requiredCount": count})
        state = workspace.execute(state["revision"], "new-plan", {"serviceDate": "2026-08-02"})
        plan_id = state["selectedPlanId"]
        state = workspace.execute(state["revision"], "save-input", {"day": day}, plan_id)
        state = workspace.execute(state["revision"], "solve", {}, plan_id)
        if not state["plans"][plan_id]["valid"]:
            raise ValueError(state["plans"][plan_id]["issues"])
        backup = workspace.backup()
        # Save the solved example as the starting draft, not a pending preview
        # whose cancellation would restore the empty draft used to build it.
        backup["state"]["plans"][plan_id]["preview"] = None
        backup["state"]["plans"][plan_id]["message"] = "完整试用草稿已载入；可直接查看、自动重排或手动调整。含演示假设，尚未正式采用。"
        # The optional notice travels with this isolated dataset through existing backup/restore.
        backup["state"]["trialInfo"] = {"label": "完整试用配置 · 含演示假设", "source": "2026-08-02真实航班 + 30天历史人员资质建议", "sourceSha256": hashlib.sha256(source).hexdigest(), "assumptions": assumptions, "originalRemarks": original_remarks}
        check = Workbench(Path(directory) / "check.sqlite3")
        checked = check.restore(0, backup)
        assert checked["plans"][plan_id]["valid"]
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(check.backup(), output, ensure_ascii=False, indent=2)
            output.write("\n")
        print(f"已生成完整配置：15人 / 115项资格 / 7航班 / 70岗位格 / {len(checked['plans'][plan_id]['draft'])}条自动分配，校验通过；仅为试用草稿。")
        print(args.output)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
