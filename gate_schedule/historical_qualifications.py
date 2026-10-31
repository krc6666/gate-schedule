"""Summarize observed work only; never infer availability, demand or certification."""
from __future__ import annotations

from collections import defaultdict
from io import BytesIO
import re

from openpyxl import Workbook

from .domain import GATE_ROLE_NAMES
from .excel_io import import_day, EXTERNAL_SUPPORT_LABEL


def analyze_history(text, *, name_aliases=None):
    aliases = name_aliases or {}
    sections = re.findall(r"^## (2026\d{4})\n(.*?)(?=^## |\Z)", text.replace("\r\n", "\n"), re.M | re.S)
    if not sections:
        raise ValueError("未找到历史日表章节")
    observations = defaultdict(list)
    pending = set()
    excluded = []
    flights = 0
    # The historical Markdown broke this one label over slash-separated lines.
    role_aliases = {"登机查验 / （VIP/F/ / 优先通道）": "登机查验（VIP/F/优先通道）"}
    seen_dates = set()
    for date, content in sections:
        if date in seen_dates:
            raise ValueError(f"重复历史日期：{date}")
        seen_dates.add(date)
        rows = [[part.strip() for part in line.strip().strip("|").split("|")] for line in content.splitlines() if line.startswith("|")]
        if len(rows) < 3:
            raise ValueError(f"{date}缺少完整岗位表")
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["服务" + date])
        sheet.append(rows[0])
        for row in rows[2:]:
            sheet.append(row)
        stream = BytesIO()
        workbook.save(stream)
        service_date = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
        day = import_day(stream.getvalue(), service_date)
        flights += len(day.flights)
        for cell in day.cells:
            role = role_aliases.get(cell.role_name, cell.role_name)
            if role not in GATE_ROLE_NAMES:
                raise ValueError(f"未知历史岗位需核对：{cell.role_name}")
            per_cell = set()
            for raw_name in cell.internal_names:
                if raw_name in {"支援", EXTERNAL_SUPPORT_LABEL}:
                    excluded.append({"date": service_date, "roleName": role, "label": raw_name})
                    continue
                name = aliases.get(raw_name, raw_name)
                if raw_name == "李扬" and raw_name not in aliases:
                    pending.add(raw_name)
                    continue
                if name in per_cell:
                    continue
                per_cell.add(name)
                observations[(role, name)].append({"date": service_date, "flightNo": cell.flight_no, "rawName": raw_name, "rawRole": cell.role_name, "rawValue": cell.raw_value})
            for label in cell.external_labels:
                excluded.append({"date": service_date, "roleName": role, "label": label})
    people = sorted({name for role, name in observations})
    roles = [{"roleName": role, "memberNames": [name for name in people if (role, name) in observations]} for role in GATE_ROLE_NAMES]
    evidence = []
    for role in GATE_ROLE_NAMES:
        for name in people:
            records = observations.get((role, name))
            if not records:
                continue
            dates = sorted({r["date"] for r in records})
            evidence.append({"roleName": role, "memberName": name, "flightCount": len(records), "dayCount": len(dates), "firstDate": dates[0], "lastDate": dates[-1], "records": records})
    return {"payload": {"people": people, "roles": roles, "source": "历史排班建议，待核对"}, "evidence": evidence, "pendingNames": sorted(pending), "excludedLabels": excluded, "dayCount": len(sections), "flightCount": flights}
