"""Generate editable qualification suggestions from the owner's historical Markdown."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gate_schedule.excel_io import export_qualifications, import_qualifications
from gate_schedule.historical_qualifications import analyze_history


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--blank-output", type=Path, required=True)
    parser.add_argument("--alias", action="append", default=[], help="Owner确认的原姓名=统一姓名")
    args = parser.parse_args()
    aliases = {}
    for value in args.alias:
        original, separator, target = value.partition("=")
        if not separator or not original.strip() or not target.strip():
            parser.error("姓名别名使用 原姓名=统一姓名")
        aliases[original.strip()] = target.strip()
    if args.output.resolve() == args.blank_output.resolve():
        parser.error("建议表和空白模板必须是不同文件")
    for path in (args.output, args.blank_output):
        if path.exists():
            parser.error(f"文件已存在，拒绝覆盖：{path}")
    source = args.source.read_bytes()
    result = analyze_history(source.decode("utf-8-sig"), name_aliases=aliases)
    if result["pendingNames"]:
        parser.error("以下姓名需核对后再生成：" + "、".join(result["pendingNames"]))
    details = (f"来源：{args.source.name}；SHA256={hashlib.sha256(source).hexdigest()}；"
               f"{result['dayCount']}个工作日、{result['flightCount']}个航班列。"
               f"姓名统一：{aliases}（Owner确认同音为一人）。外援标签不建人员，备注不推断岗位。"
               "每个历史航班岗位格计一次，轮换名单不推断实际工作分钟。")
    filled = export_qualifications(result["payload"], result["evidence"], source_details=details)
    blank = export_qualifications(result["payload"], blank=True, source_details=details)
    assert import_qualifications(filled) == result["payload"]
    parsed_blank = import_qualifications(blank)
    assert all(not role["memberNames"] for role in parsed_blank["roles"])
    for path, content in [(args.output, filled), (args.blank_output, blank)]:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as output:
            output.write(content)
    print(f"{result['dayCount']}天 / {result['flightCount']}航班 / {len(result['payload']['people'])}人 / {len(result['evidence'])}项建议资质")
    for role in result["payload"]["roles"]:
        print(f"{role['roleName']}：{len(role['memberNames'])}人；" + "、".join(role["memberNames"]))
    print(f"建议表：{args.output}\n空白表：{args.blank_output}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
