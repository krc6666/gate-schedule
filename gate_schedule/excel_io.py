from __future__ import annotations

from copy import deepcopy
import re
from dataclasses import replace
from datetime import datetime, time as datetime_time
from datetime import date
from io import BytesIO
from zipfile import BadZipFile

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.comments import Comment
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.utils import get_column_letter

from .qualification_config import validate_qualification_config
from .domain import validate_duty_roster, HALF_REST_LABELS, validate_half_rest_settings
from .domain import validate_relaxed_shift_settings, default_relaxed_shift_settings
from .duty_excel import write_roster_configuration, read_roster_configuration

from .domain import (
    Assignment, ImportedCell, ImportedCellSemantics, ImportedDay, ImportedFlight, ImportedRemark, Scenario,
    ImportedRoleConfig, ImportedMemberConfig, ImportedMemberAvailability, ImportedOccupancy, ValidationIssue, BaggageRotationSettings, imported_member_names, imported_required_count, imported_work_mode, parse_minutes, format_minutes, WORK_MODES, task_timing_label, attendance_bounds,
    validate_handoff_preferences, validate_display_preferences, general_handoff_preferences,
    validate_balance_settings,
    validate_team_leader_settings,
    PERSONNEL_STATUSES,
)


EXTERNAL_SUPPORT_LABEL = "值机支援"
_FLIGHT_HEADER = re.compile(r"^\s*(?P<flight>[^/]+?)\s*/\s*(?P<start>(?:(?:次日|第\d+日)\s*)?\d{1,2}:?\d{2})\s*-\s*(?P<end>(?:(?:次日|第\d+日)\s*)?\d{1,2}:?\d{2})\s*$")
_ARRIVAL_REMARK = re.compile(r"(?P<time>\d{1,2}:?\d{2})\s*到岗")
_SPLIT_NAMES = re.compile(r"[、,，/\s]+")


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def import_qualifications(content: bytes, *, workbook=None, allow_empty=False):
    """Read a position-first matrix. Blank/0 never revokes existing qualifications."""
    owns_workbook = workbook is None
    if owns_workbook:
        try:
            workbook = load_workbook(BytesIO(content), data_only=False)
        except (BadZipFile, KeyError, EOFError, OSError) as error:
            raise ValueError("文件不是完整的xlsx资质表") from error
    try:
        if "岗位资质" not in workbook.sheetnames:
            raise ValueError("请选择资质模板，必须包含“岗位资质”工作表；日表请到航班页导入")
        sheet = workbook["岗位资质"]
        if sheet.cell(1, 1).value != "岗位":
            raise ValueError("“岗位资质”第一格必须为“岗位”，其余表头填写人员姓名")
        rows = list(sheet.iter_rows())
        for row in rows:
            if any(c.data_type == "f" or c.data_type == "e" for c in row):
                raise ValueError("资质表不能使用公式或Excel错误值，请改为姓名和1或空白")
        # Ignore unused trailing formatted columns, but reject gaps inside the header.
        last_column = max((c.column for row in rows for c in row if c.value is not None), default=1)
        people = [_text(sheet.cell(1, c).value) for c in range(2, last_column + 1)]
        if not people and not allow_empty:
            raise ValueError("请在岗位右侧的表头填写至少一位实际人员姓名")
        roles = []
        for row in rows[1:]:
            if not any(c.value is not None for c in row):
                continue
            name = _text(row[0].value)
            selected = []
            for column, person in enumerate(people, start=2):
                value = _text(sheet.cell(row[0].row, column).value)
                if value == "1":
                    selected.append(person)
                elif value not in {"", "0"}:
                    raise ValueError(f"{name or '未填写岗位'} / {person}：只能填1、0或留空")
            roles.append({"roleName": name, "memberNames": selected})
        if not roles:
            raise ValueError("资质表中没有岗位")
        source = "手动配置"
        if "使用说明" in workbook.sheetnames:
            for key, value, *_ in workbook["使用说明"].iter_rows(values_only=True):
                if key == "资质来源":
                    source = _text(value)
        return validate_qualification_config({"people": people, "roles": roles, "source": source})
    finally:
        if owns_workbook:
            workbook.close()


def export_qualifications(data, evidence=(), *, blank=False, source_details=""):
    """Same editable matrix for manual setup, history suggestions and export."""
    data = validate_qualification_config(data)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "岗位资质"
    people = data["people"] or ["（填写姓名）"]
    sheet.append(["岗位", *people])
    by_pair = {(e["roleName"], e["memberName"]): e for e in evidence}
    for role in data["roles"]:
        sheet.append([role["roleName"], *[1 if name in role["memberNames"] and not blank else None for name in people]])
        for column, name in enumerate(people, start=2):
            item = by_pair.get((role["roleName"], name))
            if item and not blank:
                cell = sheet.cell(sheet.max_row, column)
                cell.comment = Comment(f"历史建议，需核对。{item['dayCount']}天 / {item['flightCount']}个航班格；{item['firstDate']}至{item['lastDate']}。依据见后面的历史明细。", "历史排班")
                cell.fill = PatternFill("solid", fgColor="FFF1F2")
    sheet.freeze_panes = "B2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.column_dimensions["A"].width = 36
    for row in range(2, sheet.max_row + 1):
        sheet.row_dimensions[row].height = 36
    for column in range(2, len(people) + 2):
        sheet.column_dimensions[get_column_letter(column)].width = 13
    if data["roles"]:
        validation = DataValidation(type="list", formula1='"1,0"', allow_blank=True)
        validation.errorTitle = "请填1、0或留空"
        validation.error = "1表示本次补充该项资质；0和空白不新增，也不删除系统已有资质。"
        validation.showErrorMessage = True
        sheet.add_data_validation(validation)
        validation.add(f"B2:{get_column_letter(len(people) + 1)}{len(data['roles']) + 1}")
    guide = workbook.create_sheet("使用说明")
    for row in [
        ["项目", "内容"],
        ["模板版本", "登机口岗位资质-1"],
        ["资质来源", "手动配置" if blank else data["source"]],
        ["填写方式", "每行一个岗位，每列一个实际人员。填1表示补充该人员的此项资质，0或空白表示本次不补充。可增删行列。"],
        ["历史建议", "历史做过仅作为建议，不代表资质认证；没出现过不代表不会做。先核对姓名和岗位，再导入。"],
        ["导入位置", "配置 → 导入资质表 → 核对预览 → 确认补充资质。不是航班日表，也不是完整数据恢复。"],
        ["合并方式", "按完全相同姓名匹配。新姓名新增人员，已有人员补充勾选资质；不删除已有资格、不启用已停用人员。删除资质请用岗位行勾选弹窗。"],
        ["已有计划", "不修改当天计划、出勤、人数、时段、草稿或正式结果。新计划沿用；已有计划要用新资质，请明确载入最新人员。"],
        ["排班资料", "此表只提供人员和资质，不推断岗位人数、出勤或实际时段；自动排班前仍需补齐当天资料。"],
        ["数据依据", source_details or "由配置页导出，可在本文件修改后再次导入。"],
    ]:
        guide.append(row)
    guide.column_dimensions["A"].width = 18
    guide.column_dimensions["B"].width = 115
    for row in range(2, guide.max_row + 1):
        guide.row_dimensions[row].height = 45 if row != guide.max_row else 90
    if evidence and not blank:
        summary = workbook.create_sheet("历史次数")
        summary.append(["岗位", "姓名", "航班格次数", "工作日数", "首次日期", "末次日期"])
        records = workbook.create_sheet("历史明细")
        records.append(["岗位", "姓名", "日期", "航班", "原姓名", "原岗位", "原单元格"])
        for item in evidence:
            summary.append([item["roleName"], item["memberName"], item["flightCount"], item["dayCount"], item["firstDate"], item["lastDate"]])
            for record in item["records"]:
                records.append([item["roleName"], item["memberName"], record["date"], record["flightNo"], record["rawName"], record["rawRole"], record["rawValue"]])
        for target in [summary, records]:
            target.freeze_panes = "C2"
            target.auto_filter.ref = target.dimensions
            for column in target.columns:
                target.column_dimensions[column[0].column_letter].width = 25
            target.column_dimensions["A"].width = 36
    for target in workbook:
        for cell in target[1]:
            cell.fill = PatternFill("solid", fgColor="B4232D")
            cell.font = Font(color="FFFFFF", bold=True)
        for row in target:
            for cell in row:
                # Names and original historical text must remain literal, never formulas.
                if isinstance(cell.value, str):
                    cell.data_type = "s"
                cell.alignment = Alignment(vertical="top", wrap_text=True)
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def _work_time_text(value: object) -> str | None:
    if value is None or _text(value) == "":
        return None
    if isinstance(value, (datetime, datetime_time)):
        return value.strftime("%H:%M")
    return _text(value)


def _format_compact_time(value: str) -> str:
    if ":" in value or "日" in value:
        match = re.fullmatch(r"((?:次日|第\d+日)\s*)?(\d{1,2}):?(\d{2})", value.strip())
        if not match:
            raise ValueError(f"时间格式无效：{value}")
        return format_minutes(parse_minutes(f"{match[1] or ''}{int(match[2]):02d}:{match[3]}"))
    digits = value.replace(":", "").zfill(4)
    return f"{digits[:2]}:{digits[2:]}"


def _split_names(value: str) -> tuple[str, ...]:
    return tuple(item for item in _SPLIT_NAMES.split(value) if item)


def _cell_semantics(role_name: str, raw_value: str) -> tuple[tuple[str, ...], tuple[str, ...], ImportedCellSemantics]:
    if not raw_value or raw_value == "/":
        return (), (), "empty"

    values = tuple(item.strip() for item in raw_value.split("/") if item.strip())
    external_labels = tuple(item for item in values if item == EXTERNAL_SUPPORT_LABEL)
    internal_names = tuple(item for item in values if item != EXTERNAL_SUPPORT_LABEL)
    if external_labels:
        return internal_names, external_labels, "external"
    if len(internal_names) < 2:
        return internal_names, (), "single"
    if role_name == "大件行李拦截":
        return internal_names, (), "rotation"
    if role_name in {"接机1", "接机2"}:
        return internal_names, (), "simultaneous"
    return internal_names, (), "ambiguous"


def _remarks(raw_text: str) -> list[ImportedRemark]:
    normalized = raw_text.replace("：", ":")
    if normalized.startswith("0910支援"):
        people = _split_names(normalized.split(":", 1)[1] if ":" in normalized else "")
        return [ImportedRemark("support", raw_text, people)]
    matches = list(_ARRIVAL_REMARK.finditer(normalized))
    if matches:
        remarks: list[ImportedRemark] = []
        for index, match in enumerate(matches):
            next_start = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
            tail = normalized[match.end() : next_start].lstrip(": ")
            remarks.append(ImportedRemark("arrival", f"{match.group(0)}: {tail}", _split_names(tail), _format_compact_time(match.group("time"))))
        return remarks
    return [ImportedRemark("other", raw_text)]


def import_day(content: bytes, service_date: str, previous_day: ImportedDay | None = None, *, allow_empty=False) -> ImportedDay:
    try:
        workbook = load_workbook(BytesIO(content), data_only=True)
    except (BadZipFile, KeyError, EOFError) as error:
        raise ValueError("文件不是可读取的xlsx日表，请检查文件是否完整") from error
    sheet = None
    header_row_index = None
    for candidate in workbook.worksheets:
        candidate_header = next(
            (
                row_index
                for row_index, row in enumerate(candidate.iter_rows(values_only=True), start=1)
                if _text(row[0]) == "岗位" and (any(_FLIGHT_HEADER.match(_text(value)) for value in row[1:]) or allow_empty and candidate.title == '保障明细')
            ),
            None,
        )
        if candidate_header is not None:
            sheet = candidate
            header_row_index = candidate_header
            break
    if sheet is None or header_row_index is None:
        raise ValueError("未找到以“岗位”开头的表头")

    title = _text(sheet.cell(1, 1).value) or service_date
    flights: list[ImportedFlight] = []
    columns: list[tuple[int, ImportedFlight]] = []
    for column_index, value in enumerate(sheet[header_row_index][1:], start=2):
        match = _FLIGHT_HEADER.match(_text(value.value))
        if not match:
            continue
        window_start = _format_compact_time(match.group("start"))
        window_end = _format_compact_time(match.group("end"))
        try:
            start_time = parse_minutes(window_start)
            end_time = parse_minutes(window_end)
        except ValueError as error:
            raise ValueError(f"航班 {match.group('flight').strip()} 的起止时间无效") from error
        if end_time < start_time and "日" not in match.group("end"):
            end_time += 1440
        # Owner确认：历史表晚班后的TW616属于次日；不推广到其他凌晨航班。
        if match.group("flight").strip() == "TW616" and "日" not in match.group("start") and any(parse_minutes(item.window_end) >= 1440 for item in flights):
            start_time += 1440
            end_time += 1440
        if end_time <= start_time:
            raise ValueError(f"航班 {match.group('flight').strip()} 的结束时间必须晚于开始时间")
        flight = ImportedFlight(
            id=f"flight-{len(flights) + 1}",
            flight_no=match.group("flight").strip(),
            window_start=format_minutes(start_time),
            window_end=format_minutes(end_time),
            raw_header=_text(value.value),
        )
        flights.append(flight)
        columns.append((column_index, flight))
    if not flights and not allow_empty:
        raise ValueError("未找到“航班号 / 起止时间”列")

    role_names: list[str] = []
    cells: list[ImportedCell] = []
    remarks: list[ImportedRemark] = []
    for row_index in range(header_row_index + 1, sheet.max_row + 1):
        role_name = _text(sheet.cell(row_index, 1).value)
        if not role_name:
            continue
        if role_name.startswith("0910支援") or _ARRIVAL_REMARK.search(role_name.replace("：", ":")):
            remarks.extend(_remarks(role_name))
            continue
        role_names.append(role_name)
        for column_index, flight in columns:
            raw_value = _text(sheet.cell(row_index, column_index).value)
            internal_names, external_labels, semantics = _cell_semantics(role_name, raw_value)
            cells.append(
                ImportedCell(
                    id=f"cell-{row_index}-{column_index}",
                    role_name=role_name,
                    flight_id=flight.id,
                    flight_no=flight.flight_no,
                    raw_value=raw_value,
                    internal_names=internal_names,
                    external_labels=external_labels,
                    semantics=semantics,
                )
            )
    if "岗位时间" in workbook.sheetnames:
        time_sheet = workbook["岗位时间"]
        expected_headers = ("航班号", "岗位", "开始时间", "结束时间")
        headers = tuple(_text(time_sheet.cell(1, column).value) for column in range(1, 5))
        if headers != expected_headers:
            raise ValueError("“岗位时间”工作表表头必须为：航班号、岗位、开始时间、结束时间")
        cells_by_key = {(cell.flight_no, cell.role_name): cell for cell in cells}
        times_by_key: dict[tuple[str, str], tuple[str | None, str | None]] = {}
        for row_index in range(2, time_sheet.max_row + 1):
            flight_no = _text(time_sheet.cell(row_index, 1).value)
            role_name = _text(time_sheet.cell(row_index, 2).value)
            work_start = _work_time_text(time_sheet.cell(row_index, 3).value)
            work_end = _work_time_text(time_sheet.cell(row_index, 4).value)
            if not flight_no and not role_name and not work_start and not work_end:
                continue
            key = (flight_no, role_name)
            if not flight_no or not role_name:
                raise ValueError(f"“岗位时间”工作表第 {row_index} 行缺少航班号或岗位")
            if key not in cells_by_key:
                raise ValueError(f"“岗位时间”工作表第 {row_index} 行找不到对应的航班和岗位")
            if key in times_by_key:
                raise ValueError(f"“岗位时间”工作表第 {row_index} 行重复填写了航班和岗位")
            times_by_key[key] = (work_start, work_end)
        cells = [
            replace(
                cell,
                work_start=times_by_key[(cell.flight_no, cell.role_name)][0],
                work_end=times_by_key[(cell.flight_no, cell.role_name)][1],
            )
            if (cell.flight_no, cell.role_name) in times_by_key
            else cell
            for cell in cells
        ]
    if "岗位人数例外" in workbook.sheetnames:
        override_sheet = workbook["岗位人数例外"]
        headers = tuple(_text(override_sheet.cell(1, column).value) for column in range(1, 4))
        if headers != ("航班号", "岗位", "同时需要人数"):
            raise ValueError("“岗位人数例外”工作表表头必须为：航班号、岗位、同时需要人数")
        known = {(cell.flight_no, cell.role_name) for cell in cells}
        overrides: dict[tuple[str, str], int] = {}
        for row_index in range(2, override_sheet.max_row + 1):
            flight_no = _text(override_sheet.cell(row_index, 1).value)
            role_name = _text(override_sheet.cell(row_index, 2).value)
            count = override_sheet.cell(row_index, 3).value
            if not flight_no and not role_name and count is None:
                continue
            key = (flight_no, role_name)
            if key not in known or key in overrides:
                raise ValueError(f"“岗位人数例外”工作表第 {row_index} 行的航班岗位不存在或重复")
            if type(count) is not int or count < 0:
                raise ValueError(f"“岗位人数例外”工作表第 {row_index} 行的同时需要人数必须是 0 或正整数")
            overrides[key] = count
        cells = [replace(cell, required_count_override=overrides.get((cell.flight_no, cell.role_name))) for cell in cells]
    if "固定人员" in workbook.sheetnames:
        fixed_sheet = workbook["固定人员"]
        headers = tuple(_text(fixed_sheet.cell(1, column).value) for column in range(1, 4))
        if headers != ("航班号", "岗位", "固定人员"):
            raise ValueError("“固定人员”工作表表头必须为：航班号、岗位、固定人员")
        cells_by_key = {(cell.flight_no, cell.role_name): cell for cell in cells}
        fixed_by_key: dict[tuple[str, str], tuple[str, ...]] = {}
        for row_index in range(2, fixed_sheet.max_row + 1):
            flight_no = _text(fixed_sheet.cell(row_index, 1).value)
            role_name = _text(fixed_sheet.cell(row_index, 2).value)
            names = _split_names(_text(fixed_sheet.cell(row_index, 3).value))
            if not flight_no and not role_name and not names:
                continue
            key = (flight_no, role_name)
            if key not in cells_by_key or key in fixed_by_key:
                raise ValueError(f"“固定人员”工作表第 {row_index} 行的航班岗位不存在或重复")
            fixed_by_key[key] = names
        cells = [replace(cell, fixed_member_names=fixed_by_key.get((cell.flight_no, cell.role_name), ())) for cell in cells]
    role_configs: list[ImportedRoleConfig] = []
    if "岗位配置" in workbook.sheetnames:
        config_sheet = workbook["岗位配置"]
        expected_headers = ("岗位", "同时需要人数")
        headers = tuple(_text(config_sheet.cell(1, column).value) for column in range(1, 3))
        if headers != expected_headers:
            raise ValueError("“岗位配置”工作表表头必须为：岗位、同时需要人数")
        known_roles = set(role_names)
        seen_roles: set[str] = set()
        for row_index in range(2, config_sheet.max_row + 1):
            role_name = _text(config_sheet.cell(row_index, 1).value)
            raw_count = config_sheet.cell(row_index, 2).value
            if not role_name and raw_count in (None, ""):
                continue
            if role_name not in known_roles:
                raise ValueError(f"“岗位配置”工作表第 {row_index} 行的岗位不在日表中")
            if role_name in seen_roles:
                raise ValueError(f"“岗位配置”工作表第 {row_index} 行重复填写了岗位")
            if raw_count in (None, ""):
                required_count = None
            elif isinstance(raw_count, bool) or not isinstance(raw_count, int) or raw_count < 0:
                raise ValueError(f"“岗位配置”工作表第 {row_index} 行的同时需要人数必须是 0 或正整数")
            else:
                required_count = raw_count
            role_configs.append(ImportedRoleConfig(role_name, required_count))
            seen_roles.add(role_name)
        role_configs.extend(ImportedRoleConfig(name) for name in role_names if name not in seen_roles)
    member_configs: list[ImportedMemberConfig] = []
    if "人员配置" in workbook.sheetnames:
        member_sheet = workbook["人员配置"]
        headers = tuple(_text(member_sheet.cell(1, column).value) for column in range(1, 3))
        if headers != ("人员", "可做岗位"):
            raise ValueError("“人员配置”工作表表头必须为：人员、可做岗位")
        seen_members: set[str] = set()
        for row_index in range(2, member_sheet.max_row + 1):
            name = _text(member_sheet.cell(row_index, 1).value)
            roles_text = _text(member_sheet.cell(row_index, 2).value)
            if not name and not roles_text:
                continue
            if not name or name in seen_members:
                raise ValueError(f"“人员配置”工作表第 {row_index} 行的人员为空或重复")
            roles = tuple(role.strip() for role in roles_text.splitlines() if role.strip())
            member_configs.append(ImportedMemberConfig(name, roles))
            seen_members.add(name)
    attendance: list[ImportedMemberAvailability] = []
    if "当日出勤" in workbook.sheetnames:
        attendance_sheet = workbook["当日出勤"]
        headers = tuple(_text(attendance_sheet.cell(1, column).value) for column in range(1, 5))
        if headers != ("人员", "参加排班", "到岗时间", "离岗时间"):
            raise ValueError("“当日出勤”工作表表头必须为：人员、参加排班、到岗时间、离岗时间")
        for row_index in range(2, attendance_sheet.max_row + 1):
            name = _text(attendance_sheet.cell(row_index, 1).value)
            selected = attendance_sheet.cell(row_index, 2).value
            start = _work_time_text(attendance_sheet.cell(row_index, 3).value)
            end = _work_time_text(attendance_sheet.cell(row_index, 4).value)
            if not name and selected is None and start is None and end is None:
                continue
            if selected in ("是", True, 1):
                participating = True
            elif selected in ("否", False, 0, None, ""):
                participating = False
            else:
                raise ValueError(f"“当日出勤”工作表第 {row_index} 行的参加排班需填“是”或“否”")
            rest = _text(attendance_sheet.cell(row_index, 5).value) if attendance_sheet.max_column >= 5 else ""
            if rest not in ("", "不半休", "上午半休", "下午半休"):
                raise ValueError(f"当日出勤第{row_index}行半休时段无效")
            attendance.append(ImportedMemberAvailability(name, participating, start, end, {"上午半休":"late-start", "下午半休":"early-finish"}.get(rest)))
    extra_occupancies: list[ImportedOccupancy] = []
    if "额外占用" in workbook.sheetnames:
        occupancy_sheet = workbook["额外占用"]
        headers = tuple(_text(occupancy_sheet.cell(1, column).value) for column in range(1, 6))
        if headers != ("编号", "人员", "事项", "开始时间", "结束时间"):
            raise ValueError("“额外占用”工作表表头必须为：编号、人员、事项、开始时间、结束时间")
        occupancy_rows: dict[str, dict] = {}
        for row_index in range(2, occupancy_sheet.max_row + 1):
            occupancy_id = _text(occupancy_sheet.cell(row_index, 1).value)
            name = _text(occupancy_sheet.cell(row_index, 2).value)
            label = _text(occupancy_sheet.cell(row_index, 3).value)
            start = _work_time_text(occupancy_sheet.cell(row_index, 4).value)
            end = _work_time_text(occupancy_sheet.cell(row_index, 5).value)
            if not occupancy_id and not name and not label and not start and not end:
                continue
            if occupancy_id not in occupancy_rows:
                occupancy_rows[occupancy_id] = {"label": label, "start": start or "", "end": end or "", "names": []}
            group = occupancy_rows[occupancy_id]
            if (group["label"], group["start"], group["end"]) != (label, start or "", end or ""):
                raise ValueError(f"“额外占用”工作表第 {row_index} 行与同编号占用内容不一致")
            if name and name not in group["names"]:
                group["names"].append(name)
        extra_occupancies.extend(
            ImportedOccupancy(occupancy_id, item["label"], tuple(item["names"]), item["start"], item["end"])
            for occupancy_id, item in occupancy_rows.items()
        )
    remarks_reviewed = not any(remark.kind == "support" for remark in remarks)
    transition_minutes = 0
    if "自动排班设置" in workbook.sheetnames:
        settings_sheet = workbook["自动排班设置"]
        if tuple(_text(settings_sheet.cell(1, column).value) for column in range(1, 3)) != ("项目", "分钟"):
            raise ValueError("“自动排班设置”工作表表头必须为：项目、分钟")
        settings = {_text(settings_sheet.cell(row, 1).value): settings_sheet.cell(row, 2).value for row in range(2, settings_sheet.max_row + 1)}
        transition_minutes = settings.get("跨航班走动预留", 0)
        remarks_reviewed = settings.get("支援备注已核对", remarks_reviewed) in ("是", True, 1)
    day = ImportedDay(
        service_date, title, tuple(flights), tuple(role_names), tuple(cells), tuple(remarks),
        tuple(role_configs), tuple(member_configs), tuple(attendance), tuple(extra_occupancies), remarks_reviewed, transition_minutes,
    )
    if "当天名单" in workbook.sheetnames:
        roster_sheet = workbook["当天名单"]
        if _text(roster_sheet.cell(1, 1).value) != "人员":
            raise ValueError("“当天名单”工作表表头必须为：人员")
        roster = tuple(_text(roster_sheet.cell(row, 1).value) for row in range(2, roster_sheet.max_row + 1) if _text(roster_sheet.cell(row, 1).value))
        if len(roster) != len(set(roster)):
            raise ValueError("当天名单有重复人员")
        day = replace(day, roster=roster)
    if previous_day is not None:
        if "岗位配置" not in workbook.sheetnames:
            day = replace(day, role_configs=tuple(
                config for config in previous_day.role_configs if config.role_name in day.role_names
            ))
        if "人员配置" not in workbook.sheetnames:
            current_names = set(imported_member_names(day))
            day = replace(day, member_configs=tuple(
                replace(config, eligible_role_names=tuple(role for role in config.eligible_role_names if role in day.role_names))
                for config in previous_day.member_configs if config.member_name in current_names
            ))
    return _read_work_modes(workbook, day)


def update_imported_day(
    day: ImportedDay,
    updates: list[dict[str, str]],
    role_configs: list[dict] | None = None,
    member_configs: list[dict] | None = None,
    member_availability: list[dict] | None = None,
    extra_occupancies: list[dict] | None = None,
    remarks_reviewed: bool | None = None,
    transition_minutes: int | None = None,
) -> ImportedDay:
    updates_by_id = {item["id"]: item for item in updates}
    cells: list[ImportedCell] = []
    for current in day.cells:
        update = updates_by_id.get(current.id)
        if update is None:
            cells.append(current)
            continue
        raw_value = update.get("rawValue", current.raw_value).strip()
        internal_names, external_labels, semantics = _cell_semantics(current.role_name, raw_value)
        requested = update.get("semantics")
        if semantics == "ambiguous" and requested in {"simultaneous", "rotation"}:
            semantics = requested  # type: ignore[assignment]
        work_start_value = update.get("workStart", current.work_start)
        work_end_value = update.get("workEnd", current.work_end)
        work_start = (work_start_value.strip() or None) if isinstance(work_start_value, str) else None
        work_end = (work_end_value.strip() or None) if isinstance(work_end_value, str) else None
        cells.append(
            replace(
                current,
                raw_value=raw_value,
                internal_names=internal_names,
                external_labels=external_labels,
                semantics=semantics,
                work_start=work_start,
                work_end=work_end,
                work_mode_override=update.get("workModeOverride", current.work_mode_override),
                required_count_override=update.get("requiredCountOverride", current.required_count_override),
                fixed_member_names=tuple(update["fixedMemberNames"] or ()) if "fixedMemberNames" in update else current.fixed_member_names,
            )
        )
    if role_configs is None:
        configs = day.role_configs
    else:
        configs = [ImportedRoleConfig(item["roleName"], item.get("requiredCount"), item.get("workMode", "timed"), item.get("concurrent", False), item.get("exclusiveSameFlight", False)) for item in role_configs]
    members = day.member_configs if member_configs is None else tuple(
        ImportedMemberConfig(item["memberName"], tuple(item["eligibleRoleNames"])) for item in member_configs
    )
    attendance = day.member_availability if member_availability is None else tuple(
        ImportedMemberAvailability(
            item["memberName"], item["participating"], item.get("availableFrom") or None, item.get("availableUntil") or None,
        )
        for item in member_availability
    )
    occupancies = day.extra_occupancies if extra_occupancies is None else tuple(
        ImportedOccupancy(item["id"], item["label"], tuple(item["memberNames"]), item["start"], item["end"])
        for item in extra_occupancies
    )
    return replace(
        day, cells=tuple(cells), role_configs=tuple(configs), member_configs=members, member_availability=attendance,
        extra_occupancies=occupancies, remarks_reviewed=day.remarks_reviewed if remarks_reviewed is None else remarks_reviewed,
        transition_minutes=day.transition_minutes if transition_minutes is None else transition_minutes,
    )


def validate_imported_day(day: ImportedDay) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    all_internal_names = {name for item in day.cells for name in item.internal_names}
    known_names = set(imported_member_names(day))
    if type(day.transition_minutes) is not int or day.transition_minutes < 0 or day.transition_minutes > 180:
        issues.append(ValidationIssue("invalid_transition_minutes", "跨航班走动预留必须在 0 到 180 分钟之间"))
    if type(day.remarks_reviewed) is not bool:
        issues.append(ValidationIssue("invalid_remarks_reviewed", "支援备注核对状态无效"))
    if len(day.role_names) != len(set(day.role_names)):
        issues.append(ValidationIssue("duplicate_imported_role", "日表岗位名称重复"))
    flights_by_id = {flight.id: flight for flight in day.flights}
    seen_cells: set[tuple[str, str]] = set()
    for flight in day.flights:
        try:
            start = parse_minutes(flight.window_start)
            end = parse_minutes(flight.window_end)
        except (ValueError, TypeError):
            issues.append(ValidationIssue("invalid_flight_window", f"航班 {flight.flight_no} 的起止时间无效", flight.id))
            continue
        if end <= start:
            issues.append(ValidationIssue("invalid_flight_window", f"航班 {flight.flight_no} 的结束时间必须晚于开始时间", flight.id))
    for cell in day.cells:
        flight = flights_by_id.get(cell.flight_id)
        if flight is None or flight.flight_no != cell.flight_no:
            issues.append(ValidationIssue("unknown_cell_flight", f"{cell.role_name} / {cell.flight_no} 引用了不存在的航班", cell.id))
        if cell.role_name not in day.role_names:
            issues.append(ValidationIssue("unknown_cell_role", f"日表单元格引用了不存在的岗位：{cell.role_name}", cell.id))
        cell_key = (cell.flight_id, cell.role_name)
        if cell_key in seen_cells:
            issues.append(ValidationIssue("duplicate_imported_cell", f"{cell.role_name} / {cell.flight_no} 重复", cell.id))
        seen_cells.add(cell_key)
        if cell.required_count_override is not None and (type(cell.required_count_override) is not int or cell.required_count_override < 0):
            issues.append(ValidationIssue("invalid_count_override", f"{cell.role_name} / {cell.flight_no} 的例外人数必须是 0 或正整数", cell.id))
        if cell.work_mode_override is not None and cell.work_mode_override not in WORK_MODES:
            issues.append(ValidationIssue("invalid_work_mode", f"{cell.role_name} 工作方式例外无效", cell.id))
        if imported_work_mode(day, cell) != "timed":
            pass
        elif (cell.work_start is None) != (cell.work_end is None):
            issues.append(ValidationIssue("incomplete_work_time", f"{cell.role_name} / {cell.flight_no} 的开始和结束时间需要一起填写", cell.id))
        elif cell.work_start is not None and cell.work_end is not None:
            try:
                start_time = parse_minutes(cell.work_start)
                end_time = parse_minutes(cell.work_end)
            except (TypeError, ValueError):
                issues.append(ValidationIssue("invalid_work_time", f"{cell.role_name} / {cell.flight_no} 的岗位时间格式无效", cell.id))
            else:
                if end_time <= start_time:
                    issues.append(ValidationIssue("work_time_order", f"{cell.role_name} / {cell.flight_no} 的结束时间必须晚于开始时间", cell.id))
        if cell.semantics == "ambiguous":
            issues.append(ValidationIssue("ambiguous_slash", f"{cell.role_name} / {cell.flight_no} 的斜杠语义待确认", cell.id))
        if cell.semantics in {"simultaneous", "rotation"} and len(cell.internal_names) < 2:
            issues.append(ValidationIssue("insufficient_people", f"{cell.role_name} / {cell.flight_no} 需要至少两名内部人员", cell.id))
        if cell.semantics == "external" and not cell.external_labels:
            issues.append(ValidationIssue("external_missing", f"{cell.role_name} / {cell.flight_no} 缺少外援标签", cell.id))
        if len(cell.internal_names) != len(set(cell.internal_names)):
            issues.append(ValidationIssue("duplicate_imported_person", f"{cell.role_name} / {cell.flight_no} 有重复人员", cell.id))
        if len(cell.fixed_member_names) != len(set(cell.fixed_member_names)):
            issues.append(ValidationIssue("duplicate_fixed_member", f"{cell.role_name} / {cell.flight_no} 的固定人员重复", cell.id))
        if cell.fixed_member_names and imported_required_count(day, cell) == 0:
            issues.append(ValidationIssue("fixed_member_on_disabled_role", f"{cell.role_name} / {cell.flight_no} 已设为不需要人员，不能再固定人员", cell.id))
        for name in cell.fixed_member_names:
            if name not in all_internal_names and name not in day.roster:
                issues.append(ValidationIssue("unknown_fixed_member", f"固定人员不在日表名单中：{name}", cell.id))
            elif name not in cell.internal_names and name not in day.roster:
                issues.append(ValidationIssue("fixed_member_not_in_cell", f"{cell.role_name} / {cell.flight_no} 的固定人员必须来自本单元格原名单", cell.id))
    for flight in day.flights:
        for role in day.role_names:
            if (flight.id, role) not in seen_cells:
                issues.append(ValidationIssue("missing_imported_cell", f"{role} / {flight.flight_no} 缺少岗位格，请补齐需求资料"))
    seen_roles: set[str] = set()
    for config in day.role_configs:
        if config.role_name not in day.role_names:
            issues.append(ValidationIssue("unknown_role_config", f"岗位配置中的岗位不存在：{config.role_name}"))
        if config.role_name in seen_roles:
            issues.append(ValidationIssue("duplicate_role_config", f"岗位配置重复：{config.role_name}"))
        if config.required_count is not None and (type(config.required_count) is not int or config.required_count < 0):
            issues.append(ValidationIssue("invalid_required_count", f"岗位 {config.role_name} 的同时需要人数必须是 0 或正整数"))
        seen_roles.add(config.role_name)
    seen_occupancies: set[str] = set()
    for occupancy in day.extra_occupancies:
        if not occupancy.id or occupancy.id in seen_occupancies:
            issues.append(ValidationIssue("duplicate_occupancy", "额外占用编号为空或重复"))
        if not occupancy.label.strip():
            issues.append(ValidationIssue("empty_occupancy_label", "额外占用需要填写事项"))
        if not occupancy.member_names or len(occupancy.member_names) != len(set(occupancy.member_names)):
            issues.append(ValidationIssue("invalid_occupancy_members", "额外占用需要选择不重复的人员"))
        for name in occupancy.member_names:
            if name not in known_names:
                issues.append(ValidationIssue("unknown_occupancy_member", f"额外占用中的人员不在日表名单中：{name}"))
        try:
            start = parse_minutes(occupancy.start)
            end = parse_minutes(occupancy.end)
        except (TypeError, ValueError):
            issues.append(ValidationIssue("invalid_occupancy_time", f"额外占用 {occupancy.label} 的开始或结束时间无效"))
        else:
            if end <= start:
                issues.append(ValidationIssue("occupancy_time_order", f"额外占用 {occupancy.label} 的结束时间必须晚于开始时间"))
        seen_occupancies.add(occupancy.id)
    seen_members: set[str] = set()
    for config in day.member_configs:
        if config.member_name not in known_names:
            issues.append(ValidationIssue("unknown_member_config", f"人员配置中的人员不在日表名单中：{config.member_name}"))
        if config.member_name in seen_members:
            issues.append(ValidationIssue("duplicate_member_config", f"人员配置重复：{config.member_name}"))
        if len(config.eligible_role_names) != len(set(config.eligible_role_names)):
            issues.append(ValidationIssue("duplicate_member_role", f"人员 {config.member_name} 的可做岗位重复"))
        for role_name in config.eligible_role_names:
            if role_name not in day.role_names:
                issues.append(ValidationIssue("unknown_member_role", f"人员 {config.member_name} 的可做岗位不存在：{role_name}"))
        seen_members.add(config.member_name)
    seen_attendance: set[str] = set()
    for entry in day.member_availability:
        if entry.member_name not in known_names:
            issues.append(ValidationIssue("unknown_attendance_member", f"出勤人员不在日表名单中：{entry.member_name}"))
        if entry.member_name in seen_attendance:
            issues.append(ValidationIssue("duplicate_attendance_member", f"出勤人员重复：{entry.member_name}"))
        if type(entry.participating) is not bool:
            issues.append(ValidationIssue("invalid_participation", f"人员 {entry.member_name} 的参加排班选项无效"))
        _, issue = attendance_bounds(day, entry)
        if issue:
            issues.append(issue)
        seen_attendance.add(entry.member_name)
    return issues


def imported_day_to_dict(day: ImportedDay) -> dict:
    return {
        "serviceDate": day.service_date,
        "title": day.title,
        "flights": [
            {"id": flight.id, "flightNo": flight.flight_no, "windowStart": flight.window_start, "windowEnd": flight.window_end, "rawHeader": flight.raw_header}
            for flight in day.flights
        ],
        "roleNames": list(day.role_names),
        "roster": list(day.roster),
        "extraOccupancies": [
            {"id": item.id, "label": item.label, "memberNames": list(item.member_names), "start": item.start, "end": item.end}
            for item in day.extra_occupancies
        ],
        "remarksReviewed": day.remarks_reviewed,
        "transitionMinutes": day.transition_minutes,
        "releasePolicy": day.release_policy,
        **({"attendanceMode": day.attendance_mode} if day.attendance_mode != "bounded" else {}),
        "phaseHandoffs": [list(pair) for pair in day.phase_handoffs],
        "memberNames": list(imported_member_names(day)),
        "memberAvailability": [
            {
                "memberName": entry.member_name, "participating": entry.participating,
                "availableFrom": entry.available_from, "availableUntil": entry.available_until,
                **({"halfRest": entry.half_rest} if entry.half_rest is not None else {}),
            }
            for entry in day.member_availability
        ],
        "memberConfigs": [
            {"memberName": config.member_name, "eligibleRoleNames": list(config.eligible_role_names)}
            for config in day.member_configs
        ],
        "roleConfigs": [
            {"roleName": config.role_name, "requiredCount": config.required_count, **({"exclusiveSameFlight": True} if config.exclusive_same_flight else {}), **({"workMode": config.work_mode, "concurrent": config.concurrent} if day.attendance_mode == "duty" or config.work_mode != "timed" or config.concurrent else {})}
            for config in day.role_configs
        ],
        "cells": [
            {
                "id": cell.id,
                "roleName": cell.role_name,
                "flightId": cell.flight_id,
                "flightNo": cell.flight_no,
                "rawValue": cell.raw_value,
                "internalNames": list(cell.internal_names),
                "externalLabels": list(cell.external_labels),
                "semantics": cell.semantics,
                "workStart": cell.work_start,
                "workEnd": cell.work_end,
                **({"workModeOverride": cell.work_mode_override} if cell.work_mode_override is not None else {}),
                "requiredCountOverride": cell.required_count_override,
                "fixedMemberNames": list(cell.fixed_member_names),
                "effectiveRequiredCount": imported_required_count(day, cell),
            }
            for cell in day.cells
        ],
        "remarks": [
            {"kind": remark.kind, "rawText": remark.raw_text, "people": list(remark.people), "time": remark.time}
            for remark in day.remarks
        ],
    }


def imported_day_from_dict(data: dict) -> ImportedDay:
    if data.get("releasePolicy", "flight") not in ("flight", "task"):
        raise ValueError("支援释放规则无效")
    return ImportedDay(
        service_date=data["serviceDate"],
        title=data["title"],
        flights=tuple(
            ImportedFlight(item["id"], item["flightNo"], item["windowStart"], item["windowEnd"], item.get("rawHeader", ""))
            for item in data["flights"]
        ),
        role_names=tuple(data["roleNames"]),
        roster=tuple(data.get("roster", ())),
        extra_occupancies=tuple(
            ImportedOccupancy(item["id"], item["label"], tuple(item["memberNames"]), item["start"], item["end"])
            for item in data.get("extraOccupancies", [])
        ),
        remarks_reviewed=data.get("remarksReviewed", not any(item.get("kind") == "support" for item in data.get("remarks", []))),
        transition_minutes=data.get("transitionMinutes", 0),
        release_policy=data.get("releasePolicy", "flight"),
        attendance_mode=data.get("attendanceMode", "bounded"),
        phase_handoffs=_phase_handoffs(data.get("phaseHandoffs", []), {f["id"] for f in data["flights"]}),
        member_availability=tuple(
            ImportedMemberAvailability(item["memberName"], item["participating"], item.get("availableFrom"), item.get("availableUntil"), item.get("halfRest"))
            for item in data.get("memberAvailability", [])
        ),
        member_configs=tuple(
            ImportedMemberConfig(item["memberName"], tuple(item["eligibleRoleNames"]))
            for item in data.get("memberConfigs", [])
        ),
        role_configs=tuple(
            ImportedRoleConfig(item["roleName"], item.get("requiredCount"), item.get("workMode", "timed"), item.get("concurrent", False), item.get("exclusiveSameFlight", False))
            for item in data.get("roleConfigs", [])
        ),
        cells=tuple(
            ImportedCell(
                id=item["id"],
                role_name=item["roleName"],
                flight_id=item["flightId"],
                flight_no=item["flightNo"],
                raw_value=item["rawValue"],
                internal_names=tuple(item["internalNames"]),
                external_labels=tuple(item["externalLabels"]),
                semantics=item["semantics"],
                work_start=item.get("workStart"),
                work_end=item.get("workEnd"),
                work_mode_override=item.get("workModeOverride"),
                required_count_override=item.get("requiredCountOverride"),
                fixed_member_names=tuple(item.get("fixedMemberNames", ())),
            )
            for item in data["cells"]
        ),
        remarks=tuple(
            ImportedRemark(item["kind"], item["rawText"], tuple(item.get("people", ())), item.get("time"))
            for item in data["remarks"]
        ),
    )


def _phase_handoffs(values, known):
    if not isinstance(values, (list, tuple)):
        raise ValueError("航班衔接必须是列表")
    result = []
    for pair in values:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2 or any(not isinstance(p, str) or p not in known for p in pair) or pair[0] == pair[1] or tuple(pair) in result:
            raise ValueError("航班衔接引用未知航班、自身或重复关系")
        result.append(tuple(pair))
    return tuple(result)


def _write_work_modes(book, day):
    sheet = book.create_sheet("岗位性质")
    sheet.append(["岗位", "工作方式", "岗位性质", "同航班专岗专人"])
    for r in day.role_configs:
        sheet.append([r.role_name, WORK_MODES[r.work_mode], "可兼任" if r.concurrent else "常规", "是" if r.exclusive_same_flight else "否"])
    links = book.create_sheet("航班衔接")
    links.append(["来源航班", "目标航班", "接机完成可接登机"])
    flights = {f.id: f.flight_no for f in day.flights}
    for source, target in day.phase_handoffs:
        links.append([flights[source], flights[target], "是"])
    policy = book.create_sheet("支援释放规则")
    policy.append(["项目", "内容"])
    policy.append(["支援释放", "本人任务完成" if day.release_policy == "task" else "原航班结束（旧方案）"])
    policy.append(["当天出勤", "勾选参加，时间限制可选" if day.attendance_mode == "duty" else "到离岗时间必填（旧方案）"])
    overrides = book.create_sheet("岗位方式例外")
    overrides.append(["航班号", "岗位", "工作方式"])
    for c in day.cells:
        if c.work_mode_override is not None:
            overrides.append([c.flight_no, c.role_name, WORK_MODES[c.work_mode_override]])
    for sheet in (sheet, links, policy):
        sheet.freeze_panes = "A2"
        for c in sheet[1]: c.font = Font(bold=True)
        for col in "ABC": sheet.column_dimensions[col].width = 32


def _read_work_modes(book, day):
    names = {"岗位性质", "航班衔接", "支援释放规则"}
    if not names.intersection(book.sheetnames):
        return day
    if not names.issubset(book.sheetnames):
        raise ValueError("岗位性质、航班衔接和支援释放规则需一起保留")

    def rows(name, header):
        sheet = book[name]
        if tuple(_text(sheet.cell(1, i).value) for i in range(1, len(header)+1)) != tuple(header):
            raise ValueError(f"{name}表头无效")
        for row in sheet:
            if any(c.data_type in {'f', 'e'} for c in row) or any(c.value is not None for c in row[len(header):]):
                raise ValueError(f"{name}不能使用公式、错误值或表头之外的数据")
        return [[_text(sheet.cell(i, j).value) for j in range(1, len(header)+1)] for i in range(2, sheet.max_row+1) if any(sheet.cell(i, j).value is not None for j in range(1, len(header)+1))]

    values = rows("支援释放规则", ["项目", "内容"])
    policies = {"本人任务完成": "task", "原航班结束（旧方案）": "flight"}
    settings = dict(values)
    if len(settings) != len(values) or set(settings) - {"支援释放", "当天出勤"} or settings.get("支援释放") not in policies:
        raise ValueError("支援释放规则需明确本人任务完成或原航班结束（旧方案）")
    attendance_modes = {"勾选参加，时间限制可选": "duty", "到离岗时间必填（旧方案）": "bounded"}
    if "当天出勤" in settings and settings["当天出勤"] not in attendance_modes:
        raise ValueError("当天出勤方式无效")
    modes = {v: k for k, v in WORK_MODES.items()}
    current = {r.role_name: r for r in day.role_configs}
    seen = set()
    header = ["岗位", "工作方式", "岗位性质"]
    if _text(book["岗位性质"].cell(1, 4).value): header.append("同航班专岗专人")
    for row in rows("岗位性质", header):
        role, mode, nature = row[:3]
        if len(row) == 4 and row[3] not in {"是", "否"}:
            raise ValueError("同航班专岗专人请明确填写是或否")
        if role not in day.role_names or role in seen or mode not in modes or nature not in {"常规", "可兼任"}:
            raise ValueError("岗位性质包含未知/重复岗位或无效工作方式/性质")
        old = current.get(role, ImportedRoleConfig(role))
        current[role] = replace(old, work_mode=modes[mode], concurrent=nature == "可兼任", exclusive_same_flight=row[3] == "是" if len(row) == 4 else old.exclusive_same_flight)
        seen.add(role)
    flights = {f.flight_no: f.id for f in day.flights}
    pairs = []
    for source, target, confirmed in rows("航班衔接", ["来源航班", "目标航班", "接机完成可接登机"]):
        if source not in flights or target not in flights or confirmed not in {"是", "否"}:
            raise ValueError("航班衔接需填写有效航班及是/否")
        pair = (flights[source], flights[target])
        if pair in pairs or source == target:
            raise ValueError("航班衔接重复或引用自身")
        pairs.append(pair)
    # Explicit no rows are retained only as non-permissions.
    confirmed_pairs = [(flights[s], flights[t]) for s, t, yes in rows("航班衔接", ["来源航班", "目标航班", "接机完成可接登机"]) if yes == "是"]
    overrides = {}
    if "岗位方式例外" in book.sheetnames:
        known = {(c.flight_no, c.role_name) for c in day.cells}
        for flight, role, mode in rows("岗位方式例外", ["航班号", "岗位", "工作方式"]):
            if (flight, role) not in known or (flight, role) in overrides or mode not in modes:
                raise ValueError("岗位方式例外引用未知/重复岗位或无效工作方式")
            overrides[flight, role] = modes[mode]
    return replace(day, cells=tuple(replace(c, work_mode_override=overrides.get((c.flight_no, c.role_name))) for c in day.cells), role_configs=tuple(current.values()), release_policy=policies[settings["支援释放"]], attendance_mode=attendance_modes.get(settings.get("当天出勤"), "bounded"), phase_handoffs=_phase_handoffs(confirmed_pairs, set(flights.values())))


def export_imported_day(day: ImportedDay) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    if day.roster:
        roster_sheet = workbook.create_sheet("当天名单")
        roster_sheet.append(["人员"])
        for name in day.roster:
            roster_sheet.append([name])
    sheet.title = "保障明细"
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(day.flights) + 1)
    title_cell = sheet.cell(1, 1, day.title)
    title_cell.font = Font(bold=True, size=14)
    title_cell.alignment = Alignment(horizontal="center")
    header_fill = PatternFill("solid", fgColor="24505D")
    header = ["岗位", *[f"{flight.flight_no} / {flight.window_start.replace(':', '')}-{flight.window_end.replace(':', '')}" for flight in day.flights]]
    sheet.append(header)
    for item in sheet[2]:
        item.font = Font(bold=True, color="FFFFFF")
        item.fill = header_fill
        item.alignment = Alignment(horizontal="center")

    cells_by_key = {(cell.role_name, cell.flight_id): cell for cell in day.cells}
    for role_name in day.role_names:
        sheet.append([role_name, *[cells_by_key[(role_name, flight.id)].raw_value if (role_name, flight.id) in cells_by_key else '' for flight in day.flights]])
    for remark in day.remarks:
        sheet.append([remark.raw_text])
    timed_cells = [cell for cell in day.cells if cell.work_start is not None or cell.work_end is not None]
    if timed_cells:
        time_sheet = workbook.create_sheet("岗位时间")
        time_sheet.append(["航班号", "岗位", "开始时间", "结束时间"])
        for cell in timed_cells:
            time_sheet.append([cell.flight_no, cell.role_name, cell.work_start or "", cell.work_end or ""])
        for item in time_sheet[1]:
            item.font = Font(bold=True, color="FFFFFF")
            item.fill = header_fill
            item.alignment = Alignment(horizontal="center")
        for row in time_sheet.iter_rows(min_row=2):
            for item in row:
                item.alignment = Alignment(vertical="center", wrap_text=True)
        time_sheet.column_dimensions["A"].width = 16
        time_sheet.column_dimensions["B"].width = 32
        time_sheet.column_dimensions["C"].width = 14
        time_sheet.column_dimensions["D"].width = 14
    overridden_cells = [cell for cell in day.cells if cell.required_count_override is not None]
    if overridden_cells:
        override_sheet = workbook.create_sheet("岗位人数例外")
        override_sheet.append(["航班号", "岗位", "同时需要人数"])
        for cell in overridden_cells:
            override_sheet.append([cell.flight_no, cell.role_name, cell.required_count_override])
        for item in override_sheet[1]:
            item.font = Font(bold=True, color="FFFFFF")
            item.fill = header_fill
        override_sheet.column_dimensions["A"].width = 16
        override_sheet.column_dimensions["B"].width = 32
        override_sheet.column_dimensions["C"].width = 16
    fixed_cells = [cell for cell in day.cells if cell.fixed_member_names]
    if fixed_cells:
        fixed_sheet = workbook.create_sheet("固定人员")
        fixed_sheet.append(["航班号", "岗位", "固定人员"])
        for cell in fixed_cells:
            fixed_sheet.append([cell.flight_no, cell.role_name, "/".join(cell.fixed_member_names)])
        for item in fixed_sheet[1]:
            item.font = Font(bold=True, color="FFFFFF")
            item.fill = header_fill
        fixed_sheet.column_dimensions["A"].width = 16
        fixed_sheet.column_dimensions["B"].width = 32
        fixed_sheet.column_dimensions["C"].width = 28
    if day.role_configs:
        config_sheet = workbook.create_sheet("岗位配置")
        config_sheet.append(["岗位", "同时需要人数"])
        for config in day.role_configs:
            config_sheet.append([config.role_name, config.required_count])
        for item in config_sheet[1]:
            item.font = Font(bold=True, color="FFFFFF")
            item.fill = header_fill
            item.alignment = Alignment(horizontal="center")
        config_sheet.column_dimensions["A"].width = 32
        config_sheet.column_dimensions["B"].width = 16
    if day.member_configs:
        member_sheet = workbook.create_sheet("人员配置")
        member_sheet.append(["人员", "可做岗位"])
        for config in day.member_configs:
            member_sheet.append([config.member_name, "\n".join(config.eligible_role_names)])
        for item in member_sheet[1]:
            item.font = Font(bold=True, color="FFFFFF")
            item.fill = header_fill
        for row in member_sheet.iter_rows(min_row=2):
            for item in row:
                item.alignment = Alignment(vertical="center", wrap_text=True)
        member_sheet.column_dimensions["A"].width = 18
        member_sheet.column_dimensions["B"].width = 40
    if day.member_availability:
        attendance_sheet = workbook.create_sheet("当日出勤")
        attendance_sheet.append(["人员", "参加排班", "到岗时间", "离岗时间", "半休时段"])
        for entry in day.member_availability:
            attendance_sheet.append([entry.member_name, "是" if entry.participating else "否", entry.available_from, entry.available_until, HALF_REST_LABELS[entry.half_rest]])
        for item in attendance_sheet[1]:
            item.font = Font(bold=True, color="FFFFFF")
            item.fill = header_fill
        for column in ("A", "B", "C", "D"):
            attendance_sheet.column_dimensions[column].width = 16
    if day.extra_occupancies:
        occupancy_sheet = workbook.create_sheet("额外占用")
        occupancy_sheet.append(["编号", "人员", "事项", "开始时间", "结束时间"])
        for occupancy in day.extra_occupancies:
            for name in occupancy.member_names:
                occupancy_sheet.append([occupancy.id, name, occupancy.label, occupancy.start, occupancy.end])
        for item in occupancy_sheet[1]:
            item.font = Font(bold=True, color="FFFFFF")
            item.fill = header_fill
        for column in ("A", "B", "C", "D", "E"):
            occupancy_sheet.column_dimensions[column].width = 20
    if day.transition_minutes or not day.remarks_reviewed:
        settings_sheet = workbook.create_sheet("自动排班设置")
        settings_sheet.append(["项目", "分钟"])
        settings_sheet.append(["跨航班走动预留", day.transition_minutes])
        settings_sheet.append(["支援备注已核对", "是" if day.remarks_reviewed else "否"])
        for item in settings_sheet[1]:
            item.font = Font(bold=True, color="FFFFFF")
            item.fill = header_fill
        settings_sheet.column_dimensions["A"].width = 24
        settings_sheet.column_dimensions["B"].width = 16
    sheet.column_dimensions["A"].width = 30
    for column_index in range(2, len(day.flights) + 2):
        sheet.column_dimensions[chr(64 + column_index)].width = 22
    for row in sheet.iter_rows(min_row=2):
        for item in row:
            item.alignment = Alignment(vertical="center", wrap_text=True)
    _write_work_modes(workbook, day)
    result = BytesIO()
    workbook.save(result)
    return result.getvalue()


def export_imported_schedule(day: ImportedDay, scenario: Scenario, assignments: tuple[Assignment, ...]) -> bytes:
    workbook = load_workbook(BytesIO(export_imported_day(day)))
    sheet = workbook.create_sheet("自动排班结果")
    sheet.append(["航班", "岗位", "开始时间/环节", "结束时间", "需要人数", "安排人员", "固定人员"])
    members = {member.id: member.name for member in scenario.members}
    flights = {flight.id: flight.flight_no for flight in scenario.flights}
    assignments_by_task: dict[str, list[str]] = {}
    for assignment in assignments:
        assignments_by_task.setdefault(assignment.task_id, []).append(assignment.member_id)
    for task in sorted(scenario.tasks, key=lambda item: (item.start, item.end, item.role_id)):
        sheet.append([
            flights.get(task.flight_id, "额外占用"), task.role_id, task_timing_label(task) if task.timing_mode == "phase" else format_minutes(task.start),
            "" if task.timing_mode == "phase" else format_minutes(task.end), task.required_count,
            "/".join(members[item] for item in assignments_by_task.get(task.id, ()) if item in members),
            "/".join(members[item] for item in task.locked_member_ids if item in members),
        ])
    for item in sheet[1]:
        item.font = Font(bold=True, color="FFFFFF")
        item.fill = PatternFill("solid", fgColor="24505D")
    for column, width in zip("ABCDEFG", (16, 30, 14, 14, 14, 32, 32)):
        sheet.column_dimensions[column].width = width
    result = BytesIO()
    workbook.save(result)
    return result.getvalue()


def export_formal_schedule(scenario: Scenario, assignments: tuple[Assignment, ...], *, retained_issues=None) -> bytes:
    """Formal first sheet and detail consume the same adopted task projection."""
    from .projections import task_matrix, member_paths
    from .validator import validate_schedule
    if validate_schedule(scenario, assignments) and retained_issues is None:
        raise ValueError("正式班表未通过独立校验")
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "正式保障班表"
    sheet.append([f"登机口保障 {scenario.service_date} · 已采用结果"])
    columns = [f.flight_no for f in scenario.flights]
    matrix = task_matrix(scenario, assignments)
    if any("支援" in cells for cells in matrix.values()):
        columns.append("支援")
    sheet.append(["岗位", *columns])
    for role, cells in matrix.items():
        sheet.append([role, *("\n".join(f"{segment['timing_label']} {' / '.join(segment['members'])}" for segment in cells.get(column, [])) for column in columns)])
    detail = workbook.create_sheet("正式任务明细")
    detail.append(["任务", "航班", "岗位", "开始", "结束", "人数", "安排人员", "固定人员"])
    members = {m.id: m.name for m in scenario.members}
    flights = {f.id: f.flight_no for f in scenario.flights}
    for task in sorted(scenario.tasks, key=lambda t: (t.start, t.end, t.id)):
        detail.append([task.id, flights.get(task.flight_id, "支援"), task.role_id, task_timing_label(task) if task.timing_mode == "phase" else format_minutes(task.start), "" if task.timing_mode == "phase" else format_minutes(task.end), task.required_count, "/".join(members[a.member_id] for a in assignments if a.task_id == task.id), "/".join(members[m] for m in task.locked_member_ids)])
    paths = workbook.create_sheet("人员路径")
    paths.append(["人员", "岗位", "开始", "结束", "任务"])
    for values in member_paths(scenario, assignments).values():
        for value in values:
            paths.append([value["member_name"], value["role_id"], value["timing_label"] if value["timing_mode"] == "phase" else format_minutes(value["start"]), "" if value["timing_mode"] == "phase" else format_minutes(value["end"]), value["task_id"]])
    rest = workbook.create_sheet("半休人员")
    rest.append(["人员", "半休时段", "当天任务段数", "说明"])
    for member in scenario.members:
        if member.half_rest:
            count = sum(a.member_id == member.id for a in assignments)
            rest.append([member.name, HALF_REST_LABELS[member.half_rest], count,
                         "当天无安排，保留休息" if not count else "按岗位开始时间判断半休时段"])
    support = workbook.create_sheet("外援说明")
    support.append(["任务", "来源", "外援缺口"])
    for requirement in scenario.external_support_requirements:
        support.append([requirement.task_id, requirement.source_label, requirement.count])
    for target in workbook:
        for row in target:
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        for column in target.columns:
            target.column_dimensions[column[0].column_letter].width = 27
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def export_archived_schedule(raw_day, scenario, assignments, issues, pending):
    """Export a reviewed archive, retaining its original gaps and diagnostic facts."""
    from .projections import task_matrix
    from .domain import task_flight_ids
    day = imported_day_from_dict(raw_day)
    if scenario:
        book = load_workbook(BytesIO(export_formal_schedule(scenario, assignments, retained_issues=issues)))
    else:
        book = load_workbook(BytesIO(export_imported_day(day)))
    sheet = book.worksheets[0]
    sheet.title = '正式保障班表'
    # An input-only workbook may have merged its old title row.
    for merged in list(sheet.merged_cells.ranges):
        sheet.unmerge_cells(str(merged))
    sheet.delete_rows(1, sheet.max_row)
    sheet.append([f'登机口保障 {day.service_date} · ' + (f'带问题归档 · {len(issues)}项待处理' if issues else '已采用结果')])
    flights = list(day.flights)
    columns = [f.flight_no for f in flights]
    matrix = task_matrix(scenario, assignments) if scenario else {}
    if any('支援' in row for row in matrix.values()):
        columns.append('支援')
    sheet.append(['岗位', *columns])
    roles = list(dict.fromkeys([*day.role_names, *matrix]))
    cells = {(c.role_name,c.flight_no):c for c in day.cells}
    tasks = {t.id:t for t in scenario.tasks} if scenario else {}
    flight_names = {f.id:f.flight_no for f in scenario.flights} if scenario else {}
    for role in roles:
        row = [role]
        for number in columns:
            cell = cells.get((role,number))
            segments = matrix.get(role,{}).get(number,[])
            if cell and cell.id in pending:
                text = pending[cell.id] + '（待完善）'
            elif segments:
                text = '\n'.join(f"{s['timing_label']} {' / '.join([*s['members'], *['待安排'] * max(0,tasks[s['task_id']].required_count-len(s['members']))])}" for s in segments)
            else:
                text = (cell.raw_value + '（待配置）' if cell and cell.raw_value else '待配置') if cell and imported_required_count(day,cell) != 0 else ''
            row.append(text)
        sheet.append(row)
        for index,number in enumerate(columns,2):
            source = cells.get((role,number))
            relevant = []
            for issue in issues:
                related = set(issue.get('relatedTaskIds', [])) | {issue.get('taskId')}
                if source and source.id in related or any(t.role_id==role and number in {flight_names[f] for f in task_flight_ids(t)} for identity,t in tasks.items() if identity in related):
                    relevant.append(issue)
            if relevant:
                color = 'FFF3CD' if all(i['code']=='coverage_shortfall' for i in relevant) else 'FDE2E2'
                sheet.cell(sheet.max_row,index).fill=PatternFill('solid',fgColor=color)
    problems = book.create_sheet('排班问题')
    problems.append(['状态', '带问题归档' if issues else '校验通过'])
    problems.append(['问题代码','问题说明','岗位或任务','人员ID','关联任务'])
    for issue in issues:
        problems.append([issue['code'],issue['message'],issue.get('taskId'),issue.get('memberId'),' / '.join(issue.get('relatedTaskIds',[]))])
    if pending:
        texts = book.create_sheet('待完善姓名')
        texts.append(['岗位格','航班','岗位','原输入'])
        by_id={c.id:c for c in day.cells}
        for identity,text in pending.items():
            cell=by_id[identity]
            texts.append([identity,cell.flight_no,cell.role_name,text])
    for target in book:
        for row in target:
            for cell in row:
                if isinstance(cell.value,str):
                    cell.data_type='s'
                cell.alignment=Alignment(vertical='top',wrap_text=True)
        for column in target.columns:
            target.column_dimensions[column[0].column_letter].width=36
    result=BytesIO();book.save(result);book.close()
    return result.getvalue()


_COMPLETE_EXCEL_VERSION = "登机口完整配置-5"
from .domain import validate_weekly_flights
from .flight_configuration import validate_library


def configuration_day(data):
    """Apply explicit daily exceptions without changing the maintained base configuration."""
    day = deepcopy(data["day"])
    overrides = data.get("dayOverrides", {})
    configs = {r["roleName"]: r for r in day["roleConfigs"]}
    configs.update({r["roleName"]: {**configs.get(r["roleName"], {}), **deepcopy(r)} for r in overrides.get("roleConfigs", [])})
    day["roleConfigs"] = list(configs.values())
    members = {m["memberName"]: m for m in day["memberConfigs"]}
    for item in overrides.get("qualifications", []):
        member = members[item["memberName"]]
        roles = [r for r in member["eligibleRoleNames"] if r != item["roleName"]]
        if item["qualified"]:
            roles.append(item["roleName"])
        member["eligibleRoleNames"] = roles
    settings = overrides.get("settings", {})
    day["transitionMinutes"] = settings.get("transitionMinutes", day["transitionMinutes"])
    return day


def validate_complete_configuration(data):
    """Validate the editable input contract; this grants no scheduling exemption."""
    if not isinstance(data, dict) or not isinstance(data.get("day"), dict):
        raise ValueError("完整配置必须包含当天资料")
    day = imported_day_from_dict(data["day"])
    date.fromisoformat(day.service_date)
    settings = BaggageRotationSettings(data["rotationEnabled"], data["rotationMinutes"])
    source = data.get("source", "手动维护的完整Excel配置")
    if not isinstance(source, str):
        raise ValueError("配置来源必须是文字")
    if len(day.roster) != len(set(day.roster)) or any(not isinstance(n, str) or not n.strip() for n in day.roster):
        raise ValueError("完整配置需要不重复的实际人员名单")
    members = {c.member_name: c for c in day.member_configs}
    validate_qualification_config({"people": list(day.roster), "roles": [{"roleName": r, "memberNames": [n for n in day.roster if n in members and r in members[n].eligible_role_names]} for r in day.role_names]})
    if set(members) != set(day.roster) or len(day.member_configs) != len(day.roster):
        raise ValueError("岗位资质必须为每位人员提供唯一配置")
    if len({f.flight_no for f in day.flights}) != len(day.flights) or any(not f.flight_no.strip() for f in day.flights):
        raise ValueError("航班号不能为空或重复")
    if len(day.flights) != len({f.id for f in day.flights}):
        raise ValueError("航班身份重复")
    # Incomplete input can be maintained as a draft, but is never made feasible here.
    issues = [i for i in validate_imported_day(day) if i.code not in {"incomplete_availability", "incomplete_work_time", "ambiguous_slash"}]
    if issues:
        raise ValueError("；".join(i.message for i in issues))
    result = {"day": imported_day_to_dict(day), "rotationEnabled": settings.enabled, "rotationMinutes": settings.interval_minutes, "source": source}
    if "history" in data:
        history = data["history"]
        if not isinstance(history, dict) or history.get("formatVersion") != 2 or not isinstance(history.get("state"), dict):
            raise ValueError("配置历史必须包含完整的工作台快照")
        result["history"] = deepcopy(history)
    # Preserve explicit false versus absent in older complete configuration files.
    supplied_roles = {r["roleName"]: r for r in data["day"].get("roleConfigs", [])}
    for role in result["day"]["roleConfigs"]:
        supplied = supplied_roles.get(role["roleName"], {})
        if "exclusiveSameFlight" in supplied:
            role["exclusiveSameFlight"] = supplied["exclusiveSameFlight"]
    if "weeklyFlightPresets" in data:
        result["weeklyFlightPresets"] = validate_weekly_flights(data["weeklyFlightPresets"])
    if "flightLibrary" in data:
        result["flightLibrary"] = validate_library(data["flightLibrary"])
    if "handoffPreferences" in data:
        result["handoffPreferences"] = general_handoff_preferences(validate_handoff_preferences(data["handoffPreferences"], day.role_names, {f.flight_no for f in day.flights} | {f["flightNo"] for f in data.get("flightLibrary", [])}))
    if "displayPreferences" in data:
        result["displayPreferences"] = validate_display_preferences(data["displayPreferences"])
    if "peopleIds" in data:
        identities = data["peopleIds"]
        if not isinstance(identities, dict) or set(identities) - set(day.roster) or any(
            not isinstance(identity, str) or not identity.strip() or identity != identity.strip()
            for identity in identities.values()
        ) or len(set(identities.values())) != len(identities):
            raise ValueError("人员身份必须使用名单中的姓名和不重复的非空人员ID")
        result["peopleIds"] = deepcopy(identities)
    if "balanceSettings" in data:
        result["balanceSettings"] = validate_balance_settings(
            data["balanceSettings"], day.role_names,
            set(result["peopleIds"].values()) if "peopleIds" in result else None,
        )
    if "relaxedShiftSettings" in data:
        result["relaxedShiftSettings"] = validate_relaxed_shift_settings(data["relaxedShiftSettings"])
    if "halfRestSettings" in data:
        result["halfRestSettings"] = validate_half_rest_settings(data["halfRestSettings"])
    if "teamLeaderSettings" in data:
        result["teamLeaderSettings"] = validate_team_leader_settings(data["teamLeaderSettings"])
    if "dutyRoster" in data:
        if "peopleIds" not in result:
            raise ValueError('轮值设置必须同时保留人员身份表')
        result['dutyRoster'] = validate_duty_roster(data['dutyRoster'], set(result['peopleIds'].values()))
    if "peopleTeamLeaders" in data:
        leaders = data["peopleTeamLeaders"]
        if not isinstance(leaders, dict) or set(leaders) != set(day.roster) or any(type(v) is not bool for v in leaders.values()):
            raise ValueError("分队长名单必须为每位人员提供唯一布尔开关")
        result["peopleTeamLeaders"] = deepcopy(leaders)
    for key, label in (("peopleStatus", "人员状态"), ("peopleRemarks", "人员备注")):
        if key in data:
            values = data[key]
            if not isinstance(values, dict) or set(values) != set(day.roster) or any(not isinstance(v, str) for v in values.values()):
                raise ValueError(label + "必须为每位人员提供唯一文本值")
            if key == "peopleStatus" and any(v not in PERSONNEL_STATUSES for v in values.values()):
                raise ValueError("人员状态只能填写正常、病假或休假")
            result[key] = deepcopy(values)
    if "peopleActive" in data:
        active = data["peopleActive"]
        if not isinstance(active, dict) or set(active) != set(day.roster) or any(type(v) is not bool for v in active.values()):
            raise ValueError("人员状态必须为每位人员提供唯一启用开关")
        if any(a.participating and not active[a.member_name] for a in day.member_availability):
            raise ValueError("停用人员不能参加当天排班，请核对人员状态和出勤")
        result["peopleActive"] = active
    if "dayOverrides" in data:
        overrides = data["dayOverrides"]
        if not isinstance(overrides, dict) or set(overrides) - {"roleConfigs", "qualifications", "settings"}:
            raise ValueError("当天配置例外字段无效")
        roles = overrides.get("roleConfigs", [])
        if not isinstance(roles, list) or any(not isinstance(r, dict) or (set(r) - {"exclusiveSameFlight"}) != {"roleName", "requiredCount", "workMode", "concurrent"} or r["roleName"] not in day.role_names for r in roles) or len(roles) != len({r["roleName"] for r in roles}):
            raise ValueError("当天岗位配置引用无效或重复")
        for r in roles:
            ImportedRoleConfig(r["roleName"], r["requiredCount"], r["workMode"], r["concurrent"], r.get("exclusiveSameFlight", False))
            if r["requiredCount"] is not None and (type(r["requiredCount"]) is not int or r["requiredCount"] < 0):
                raise ValueError("当天岗位例外人数必须为空或非负整数")
        qualifications = overrides.get("qualifications", [])
        if not isinstance(qualifications, list):
            raise ValueError("当天资质例外必须是列表")
        seen = set()
        for q in qualifications:
            if not isinstance(q, dict) or set(q) != {"memberName", "roleName", "qualified"} or q["memberName"] not in day.roster or q["roleName"] not in day.role_names or type(q["qualified"]) is not bool:
                raise ValueError("当天资质例外人员/岗位/开关无效")
            key = (q["memberName"], q["roleName"])
            if key in seen:
                raise ValueError("当天资质例外重复")
            seen.add(key)
        settings = overrides.get("settings", {})
        if settings:
            if set(settings) != {"rotationEnabled", "rotationMinutes", "transitionMinutes"} or type(settings["transitionMinutes"]) is not int or not 0 <= settings["transitionMinutes"] <= 180:
                raise ValueError("当天排班设置例外无效")
            BaggageRotationSettings(settings["rotationEnabled"], settings["rotationMinutes"])
        result["dayOverrides"] = deepcopy(overrides)
        derived = imported_day_from_dict(configuration_day(result))
        issues = [i for i in validate_imported_day(derived) if i.code not in {"incomplete_availability", "incomplete_work_time", "ambiguous_slash"}]
        if issues:
            raise ValueError("；".join(i.message for i in issues))
    return result


def export_complete_configuration(data, *, history_schedules=None):
    """One visible workbook, one editable cell source per input fact."""
    data = validate_complete_configuration(data)
    day = imported_day_from_dict(data["day"])
    book = load_workbook(BytesIO(export_imported_day(day)))
    # The explicit flight table and position-first matrix replace these old views.
    for name in ("保障明细", "当天名单", "人员配置", "自动排班设置"):
        if name in book.sheetnames:
            del book[name]
    guide = book.create_sheet("使用说明", 0)
    for row in [
        ["项目", "内容"], ["模板版本", _COMPLETE_EXCEL_VERSION], ["排班日期", day.service_date], ["计划标题", day.title], ["资质来源", data["source"]],
        ["日常维护", "同一文件维护人员、岗位资质、岗位性质、工作环节、航班、人数、出勤与衔接；保存xlsx后到配置页导入完整Excel，核对预览并确认，再点重新排班。"],
        ["资质更新", "岗位为行、人员为列。1=有资质，0或空白=取消该资质。以本表列出的人员×岗位范围为准更新基础配置；范围外资料保留。新增姓名新增人员，不自动启用已停用人员。"],
        ["既有排班", "每次确认新增当天维护方案；文件附带的全部日计划、航班、草稿和正式班表同时补入历史。已有同ID方案保留设备上的版本，不重复导入。"],
        ["新增人员", "在岗位资质末尾添加姓名列，并在当日出勤添加对应姓名；资质填1或0。"],
        ["新增岗位或航班", "岗位在岗位资质增加一行，填写岗位配置人数和岗位性质（常规/可兼任、工作环节）；航班在航班表增加一行；人数例外可按航班覆盖。可兼任只对同航班生效，两个常规岗位的冲突不会因此消失。"],
        ["同航班专岗专人", "在岗位性质的同航班专岗专人列填是或否。是表示做该岗的人不能兼做同一航班其他岗位，优先于可兼任/接续许可；其他航班仍按时间和冲突检查。同名岗位共用开关，当天不同可在当天岗位例外表覆盖。旧文件缺此列保留现有设置，空库默认关闭。"],
        ["时段与出勤", "环节岗位无需逐格时间。特殊情况在岗位方式例外设置该航班该岗为特殊实际时段，再在岗位时间填写起止；次日明确写次日00:10。日常出勤勾选参加即可，到离岗仅作特殊限制且起止一起填；旧方案直接点重新排班。大件按有需求航班窗口共享覆盖。"],
        ["跨航班衔接", "支援释放选择本人任务完成；航班衔接表填来源/目标航班和是，表示接机完成确实能接上目标登机。未确认不会自动复用；仍检查此人的其他任务。正数走动预留需实际时段来验证。"],
        ["固定与原文", "固定人员只有明确填写才锁定。岗位原文只保留旧名单/外援和已确认斜杠含义，不等同于新排班或资质。备注需核对，0910不猜占用。"],
        ["配置迁移", "导出使用最新长期人数、性质、资质、轮换、走动与航班模板，同时包含当天航班和例外。人员状态、岗位接续偏好、界面偏好均在可见表中；换设备导入此文件即可保留。全部日期的航班、草稿与正式历史随本文件迁移，在历史页查看。"],
        ["排班平衡维护", "排班平衡表维护当天航班数最大差距（空白表示关闭）和重点轮换启用；新版重点在航班重点岗位表逐行填写航班号和岗位，空表清空。重点次数修正按月份、人员ID、航班号和岗位维护增减；可填负整数，有效次数最低为0。旧修正航班号空白时保留待归属，不猜测分配；核对后补填航班号。排班平衡、航班重点岗位、重点次数修正三表与范围版本一起保留。旧文件缺航班范围时保留本机新选择、空库不勾选；旧岗位名单仅兼容保留，不再作为新版重点。姓名只供核对，不授予资质。"],
        ["人员身份维护", "人员身份表保留稳定人员ID，换设备后重点次数修正仍对应同一个人。已有人员不要手改ID；新增姓名可以不填ID，由系统建立。修正表的人员ID与姓名须和身份表一致。历史附表保留全部草稿、正式班表和累计次数依据；请勿手改历史数据，历史班表应在工作台维护。"],
        ["分队长维护", "人员状态表的分队长列填是/否。分队长设置表维护最后使用和不参与航班数比较，默认都是。先补齐岗位，再尽量少用分队长，之后考虑重点轮换/接续。豁免只影响航班数比较，其他人员硬差距不放松，分队长仍须有资质、出勤且无冲突。旧文件无新列/表时保留当前值，新设备默认不标记任何分队长；显式否取消。"],
        ["人员状态与删除", "人员状态表的状态填正常、病假或休假，备注可自由填写。病假/休假及停用人员不参与新排班；恢复正常且启用后可继续参加，仍须选择当天出勤。旧文件缺状态/备注列保留当前值，新设备默认正常/空备注，显式正常或空白备注更新。人员删除请在配置页点击删除并确认；旧班表和历史保留，可编辑名单只列现有人员；历史附表保留已删除人员的身份和旧班表。"],
        ["接续偏好维护", "适用范围填写通用，所有航班共用。旧航班号表导入时按来源岗合并目标、初始同等优先。优先登机岗位用顿号分隔。按顺序优先填是时，从左到右为第一、第二等优先；否或空白表示同等优先。启用填是/否；可在规则页勾选、上下移调整。旧四列表保持同等优先；不满足偏好仍可合法排班。表存在则以表更新，空表清空；旧文件无此表保留当前偏好。"],
        ["每航班岗位", "航班模板的本航班岗位用顿号分隔，可添加或移除名称，空白表示无岗位；模板岗位维护相应人数/工作方式例外。删除单个航班岗位不删除通用资质，新增通用岗位不会自动加入其他已配置航班。旧文件缺清单列时，已有库保留当前选择，空库沿用通用岗位。"],
        ["当天例外", "长期岗位人数/性质/资质及轮换设置保存在通用表；当天不同的设置在当天岗位例外、当天资质例外、当天设置例外三表中，仅覆盖导入的新日计划，不改长期默认。空表表示当天沿用长期配置，三表应一起保留。"],
        ["上午下午半休", "当日出勤的半休时段填写不半休、上午半休或下午半休。06:00至12:00前开始算早班；上午休不排早班，下午休只排早班，12点及凌晨归其他时段，跨午按开始判断。半休设置表默认不参与航班数比较，仍按实际岗位计次；无岗保留休息。旧四列出勤默认不半休，旧文件无设置表保留当前开关。新日默认清空当天半休，同日重排保留。"],
        ["末班与轻松班次", "轻松班次设置表维护提前下班节点、下午统计开始/结束、晚航班结束界线（当日HH:MM）；末班航班范围表逐行填写航班号，空表清空。两表一起保留或一起缺失，旧文件均无时保留当前设置，空库默认12:00/12:00/18:00/23:00及空末班。只用于名单识别和次数统计，历史按最新设置重算；实际班表与历史次数依据随本文件的历史附表迁移。"],
    ]:
        guide.append(row)
    guide.append(["携带历史", "是" if "history" in data else "否"])
    guide.append(["历史附表", "历史目录、历史航班和历史排班可核对每天班表；历史附表和隐藏快照请完整保留。日常小改使用前面的人员、资质、航班、规则等维护表；导入预览会显示补入和保留的历史数量。"])
    qualifications = book.create_sheet("岗位资质", 1)
    if "handoffPreferences" in data:
        paths = book.create_sheet("岗位接续偏好")
        paths.append(["适用范围", "接机岗位", "优先登机岗位", "启用", "按顺序优先"])
        for item in data["handoffPreferences"]:
            paths.append(["通用", item["arrivalRole"], "、".join(item["boardingRoles"]), "是" if item["enabled"] else "否", ("是" if item["ordered"] else "否") if "ordered" in item else None])
    if "displayPreferences" in data:
        display = book.create_sheet("界面偏好")
        display.append(["项目", "内容"])
        display.append(["排班布局", "按航班" if data["displayPreferences"]["layout"] == "flights" else "原保障表"])
        display.append(["表格大小", data["displayPreferences"]["zoom"]])
    if "peopleIds" in data:
        identities = book.create_sheet("人员身份")
        identities.append(["姓名", "人员ID"])
        for name, identity in data["peopleIds"].items():
            identities.append([name, identity])
    if "balanceSettings" in data:
        balance = data["balanceSettings"]
        sheet = book.create_sheet("排班平衡")
        sheet.append(["项目", "内容"])
        sheet.append(["当天航班数最大差距", balance["flightCountGap"]])
        sheet.append(["重点岗位轮换启用", "是" if balance["priorityEnabled"] else "否"])
        sheet.append(["重点岗位", "、".join(balance["priorityRoleNames"])])
        by_flight = 'priorityFlightRoles' in balance or any('flightNo' in item for item in balance['priorityAdjustments'])
        if by_flight:
            sheet.append(['重点岗位范围版本', 1])
            selected = book.create_sheet('航班重点岗位')
            selected.append(['航班号', '岗位'])
            for item in balance.get('priorityFlightRoles', []):
                selected.append([item['flightNo'], item['roleName']])
        adjustments = book.create_sheet("重点次数修正")
        adjustments.append(["月份", "人员ID", "姓名", "岗位", "修正次数"] + (['航班号'] if by_flight else []))
        names_by_id = {identity: name for name, identity in data.get("peopleIds", {}).items()}
        for item in balance["priorityAdjustments"]:
            adjustments.append([item["month"], item["memberId"], names_by_id.get(item["memberId"], ""), item["roleName"], item["delta"]] + ([item.get('flightNo')] if by_flight else []))
    if "relaxedShiftSettings" in data:
        relaxed = book.create_sheet("轻松班次设置")
        relaxed.append(["项目", "内容"])
        for key in default_relaxed_shift_settings():
            if key != 'lateFlightNos':
                relaxed.append([{'earlyDepartureCutoffTime': '提前下班节点', 'afternoonRestStartTime': '下午统计开始',
                    'afternoonRestEndTime': '下午统计结束', 'lateShiftEndTime': '晚航班结束界线'}[key], data['relaxedShiftSettings'][key]])
        late = book.create_sheet('末班航班范围')
        late.append(['航班号'])
        for number in data['relaxedShiftSettings']['lateFlightNos']:
            late.append([number])
    if "halfRestSettings" in data:
        rest = book.create_sheet("半休设置")
        rest.append(["项目", "内容"])
        rest.append(["半休人员不参与航班数比较", "是" if data["halfRestSettings"]["exemptFlightCount"] else "否"])
    if "teamLeaderSettings" in data:
        leaders = book.create_sheet("分队长设置")
        leaders.append(["项目", "内容"])
        leaders.append(["分队长最后使用", "是" if data["teamLeaderSettings"]["lastUsed"] else "否"])
        leaders.append(["分队长不参与航班数比较", "是" if data["teamLeaderSettings"]["exemptFlightCount"] else "否"])
    if 'dutyRoster' in data:
        write_roster_configuration(book, data['dutyRoster'], data['peopleIds'])
    if any(key in data for key in ("peopleActive", "peopleTeamLeaders", "peopleStatus", "peopleRemarks")):
        statuses = book.create_sheet("人员状态")
        status_header = ["人员", "启用"]
        if "peopleTeamLeaders" in data or "peopleStatus" in data or "peopleRemarks" in data: status_header.append("分队长")
        if "peopleStatus" in data or "peopleRemarks" in data: status_header.append("状态")
        if "peopleRemarks" in data: status_header.append("备注")
        statuses.append(status_header)
        for name in day.roster:
            row = [name, "是" if data.get("peopleActive", {}).get(name, True) else "否"]
            if len(status_header) >= 3: row.append("是" if data.get("peopleTeamLeaders", {}).get(name, False) else "否")
            if len(status_header) >= 4: row.append(data.get("peopleStatus", {}).get(name, "正常"))
            if len(status_header) >= 5: row.append(data["peopleRemarks"][name])
            statuses.append(row)
    if "dayOverrides" in data:
        overrides = data["dayOverrides"]
        daily = book.create_sheet("当天岗位例外")
        daily.append(["岗位", "同时需要人数", "工作方式", "可兼任", "同航班专岗专人"])
        for r in overrides.get("roleConfigs", []):
            daily.append([r["roleName"], r["requiredCount"], r["workMode"], "是" if r["concurrent"] else "否", "是" if r.get("exclusiveSameFlight", next((base.exclusive_same_flight for base in day.role_configs if base.role_name == r["roleName"]), False)) else "否"])
        daily = book.create_sheet("当天资质例外")
        daily.append(["人员", "岗位", "有资质"])
        for q in overrides.get("qualifications", []):
            daily.append([q["memberName"], q["roleName"], "是" if q["qualified"] else "否"])
        daily = book.create_sheet("当天设置例外")
        daily.append(["轮换启用", "轮换分钟", "走动预留分钟"])
        if overrides.get("settings"):
            s = overrides["settings"]
            daily.append(["是" if s["rotationEnabled"] else "否", s["rotationMinutes"], s["transitionMinutes"]])
    if "flightLibrary" in data:
        templates = book.create_sheet("航班模板")
        templates.append(["航班号", "保障开始", "保障结束", "本航班岗位"])
        positions = book.create_sheet("模板岗位")
        positions.append(["航班号", "岗位", "同时需要人数", "工作方式例外", "特殊开始", "特殊结束"])
        for flight in data["flightLibrary"]:
            templates.append([flight["flightNo"], flight["windowStart"], flight["windowEnd"], "、".join(flight["roleNames"]) if "roleNames" in flight else "沿用通用岗位"])
            for role in flight.get("roles", []):
                positions.append([flight["flightNo"], role["roleName"], role.get("requiredCount"), role.get("workModeOverride"), role.get("workStart"), role.get("workEnd")])
    if "weeklyFlightPresets" in data:
        weekly = book.create_sheet("每周航班")
        weekly.append(["星期（日0至六6）", "航班号", "保障开始", "保障结束"])
        for weekday, flights in data["weeklyFlightPresets"].items():
            if not flights:
                weekly.append([int(weekday), "", "", ""])
            for flight in flights:
                weekly.append([int(weekday), flight["flightNo"], flight["windowStart"], flight["windowEnd"]])
    qualifications.append(["岗位", *day.roster])
    by_name = {c.member_name: c.eligible_role_names for c in day.member_configs}
    for role in day.role_names:
        qualifications.append([role, *[1 if role in by_name[name] else 0 for name in day.roster]])
    qualifications.freeze_panes = "B2"
    qualifications.auto_filter.ref = qualifications.dimensions
    selection = DataValidation(type="list", formula1='"1,0"', allow_blank=True)
    selection.showErrorMessage = True
    selection.error = "1表示有资质，0或空白表示取消该资质。"
    qualifications.add_data_validation(selection)
    if day.roster and day.role_names:
        selection.add(f"B2:{get_column_letter(len(day.roster)+1)}{len(day.role_names)+1}")
    flights = book.create_sheet("航班", 2)
    flights.append(["航班号", "保障开始", "保障结束"])
    for flight in day.flights:
        flights.append([flight.flight_no, flight.window_start, flight.window_end])
    expected = {
        "岗位配置": ["岗位", "同时需要人数"],
        "岗位时间": ["航班号", "岗位", "开始时间", "结束时间"],
        "岗位人数例外": ["航班号", "岗位", "同时需要人数"],
        "固定人员": ["航班号", "岗位", "固定人员"],
        "当日出勤": ["人员", "参加排班", "到岗时间", "离岗时间"],
        "额外占用": ["编号", "人员", "事项", "开始时间", "结束时间"],
    }
    for name, header in expected.items():
        if name not in book.sheetnames:
            book.create_sheet(name).append(header)
    # Missing attendance stays explicitly absent, not an invented all-day shift.
    attendance_names = {a.member_name for a in day.member_availability}
    for name in day.roster:
        if name not in attendance_names:
            book["当日出勤"].append([name, "否", "", ""])
    settings = book.create_sheet("自动排班设置")
    for row in [["项目", "分钟"], ["跨航班走动预留", day.transition_minutes], ["支援备注已核对", "是" if day.remarks_reviewed else "否"], ["大件轮换启用", "是" if data["rotationEnabled"] else "否"], ["大件轮换分钟", data["rotationMinutes"]]]:
        settings.append(row)
    remarks = book.create_sheet("备注")
    remarks.append(["类型", "原文", "涉及人员", "到岗时间"])
    for remark in day.remarks:
        remarks.append([remark.kind, remark.raw_text, "、".join(remark.people), remark.time])
    raw = book.create_sheet("岗位原文")
    raw.append(["航班号", "岗位", "原名单", "斜杠含义"])
    for cell in day.cells:
        if cell.raw_value:
            raw.append([cell.flight_no, cell.role_name, cell.raw_value, cell.semantics])
    for sheet in book:
        sheet.freeze_panes = "B2" if sheet.title == "岗位资质" else "A2"
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="AC1F2A")
        for column in range(1, sheet.max_column + 1):
            sheet.column_dimensions[get_column_letter(column)].width = 16 if sheet.title == "岗位资质" and column > 1 else 30
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                if isinstance(cell.value, str):
                    cell.data_type = "s"
        sheet.row_dimensions[1].height = 26
    guide.column_dimensions["B"].width = 115
    for row in range(2, guide.max_row+1):
        guide.row_dimensions[row].height = 44
    book["备注"].column_dimensions["B"].width = 100
    if "history" in data:
        from .configuration_archive import append_configuration_history
        append_configuration_history(book, data["history"], history_schedules or {})
    result = BytesIO()
    book.save(result)
    book.close()
    return result.getvalue()


def import_complete_configuration(content):
    """Read visible tables, then reuse the existing day adapter for shared fields."""
    try:
        book = load_workbook(BytesIO(content), data_only=False)
    except (BadZipFile, KeyError, EOFError, OSError) as error:
        raise ValueError("文件不是完整的xlsx配置") from error
    try:
        required = ("使用说明", "岗位资质", "航班", "岗位配置", "岗位时间", "岗位人数例外", "固定人员", "当日出勤", "额外占用", "自动排班设置", "备注", "岗位原文")
        if any(name not in book.sheetnames for name in required):
            raise ValueError("请选择完整Excel配置，工作表缺失；仅资质表或日表请用原入口")
        if any(name in book.sheetnames for name in ("人员配置", "保障明细", "当天名单")):
            raise ValueError("完整配置不能同时包含旧日表的重复输入视图")
        for sheet in book:
            if any(c.data_type in {"f", "e"} for row in sheet for c in row):
                raise ValueError("完整配置不能使用公式或Excel错误值，请填写明确文字与数值")

        def rows(name, header):
            sheet = book[name]
            if tuple(_text(sheet.cell(1, n).value) for n in range(1,len(header)+1)) != tuple(header):
                raise ValueError(f"“{name}”工作表表头必须为：{'、'.join(header)}")
            if any(c.value is not None for row in sheet for c in row[len(header):]):
                raise ValueError(f"“{name}”存在表头范围外的数据")
            return [tuple(sheet.cell(n,c).value for c in range(1,len(header)+1)) for n in range(2,sheet.max_row+1) if any(sheet.cell(n,c).value is not None for c in range(1,len(header)+1))]

        def pairs(name):
            result = {}
            for key,value in rows(name,["项目","内容"] if name in {"使用说明", "界面偏好", "排班平衡", "分队长设置", "半休设置", "轻松班次设置"} else ["项目","分钟"]):
                key=_text(key)
                if not key or key in result:
                    raise ValueError(f"“{name}”项目为空或重复")
                result[key]=value
            return result

        guide=pairs("使用说明")
        from .group_excel import read_groups
        group_transfer = read_groups(book, guide)
        if guide.get('模板版本') in (_COMPLETE_EXCEL_VERSION, '登机口完整配置-4') and guide.get('携带历史') not in ('是', '否'):
            raise ValueError('请保留使用说明中的携带历史标记，不能静默丢失历史')
        compact_sheets = {'历史说明', '历史目录', '历史航班', '历史排班', '_历史快照'}
        history_sheets = {"备份说明", "人员一览", "方案一览", "长文本"} | {n for n in book.sheetnames if n.startswith("完整数据")}
        has_history = bool(history_sheets & set(book.sheetnames))
        imported_history = None
        if compact_sheets & set(book.sheetnames):
            if has_history:
                raise ValueError('配置不能同时包含两种历史快照')
            from .configuration_archive import read_configuration_history
            imported_history = read_configuration_history(book)
            for name in compact_sheets:
                del book[name]
        elif guide.get("携带历史") == "是" or has_history:
            if not history_sheets <= set(book.sheetnames):
                raise ValueError("配置中的历史工作表缺失，请保留历史附表后重新导入")
            from .backup_excel import read_backup_sheets
            imported_history = read_backup_sheets(book)
            # The existing day adapter only needs editable inputs, not another copy of history.
            for name in history_sheets:
                del book[name]
        if guide.get("模板版本") not in (_COMPLETE_EXCEL_VERSION, "登机口完整配置-4", "登机口完整配置-3", "登机口完整配置-2", "登机口完整配置-1"):
            raise ValueError("完整Excel配置版本不支持")
        if guide.get("模板版本") in (_COMPLETE_EXCEL_VERSION, "登机口完整配置-4", "登机口完整配置-3", "登机口完整配置-2") and any(n not in book.sheetnames for n in ("岗位性质", "航班衔接", "支援释放规则", "岗位方式例外")):
            raise ValueError("新版完整配置缺少岗位性质、航班衔接或支援释放规则，不能丢失新规则后导入")
        if guide.get("模板版本") in (_COMPLETE_EXCEL_VERSION, "登机口完整配置-4", "登机口完整配置-3") and not any(_text(row[0].value) == "当天出勤" for row in book["支援释放规则"].iter_rows(min_row=2)):
            raise ValueError("新版完整配置缺少当天出勤方式，不能丢失时间限制规则后导入")
        service_date=guide.get("排班日期")
        service_date=service_date.date().isoformat() if isinstance(service_date,datetime) else _text(service_date)
        date.fromisoformat(service_date)
        qualification=import_qualifications(content, workbook=book, allow_empty=True)
        roles=[r["roleName"] for r in qualification["roles"]]
        settings=pairs("自动排班设置")
        expected_settings={"跨航班走动预留","支援备注已核对","大件轮换启用","大件轮换分钟"}
        if set(settings) != expected_settings:
            raise ValueError("自动排班设置项目缺失或未知")

        def yes_no(value):
            if value not in ("是","否"):
                raise ValueError("轮换启用和备注核对需明确填是或否")
            return value=="是"

        enabled=yes_no(settings["大件轮换启用"])
        reviewed=yes_no(settings["支援备注已核对"])
        BaggageRotationSettings(enabled,settings["大件轮换分钟"])
        flight_rows=rows("航班",["航班号","保障开始","保障结束"])
        # Convert only the table shape; import_day remains the owner of legacy field parsing.
        main=book.create_sheet("保障明细",0)
        main.append([_text(guide.get("计划标题")) or "Excel保障计划"])
        headers=[]
        seen=set()
        for number,start,end in flight_rows:
            number=_text(number)
            if not number or number in seen or "/" in number:
                raise ValueError("航班号不能为空、重复或含斜杠")
            seen.add(number)
            start,end=_work_time_text(start),_work_time_text(end)
            if not start or not end or parse_minutes(end)<=parse_minutes(start):
                raise ValueError(f"{number}航班时间无效，跨午夜请明确填写次日")
            headers.append(f"{number} / {start}-{end}")
        main.append(["岗位",*headers])
        raw_rows=rows("岗位原文",["航班号","岗位","原名单","斜杠含义"])
        raw_by_pair={}
        for flight,role,value,meaning in raw_rows:
            key=(_text(flight),_text(role))
            if key in raw_by_pair or key[0] not in seen or key[1] not in roles or _text(meaning) not in {"empty","single","simultaneous","rotation","external","ambiguous"}:
                raise ValueError("岗位原文航班/岗位/斜杠含义无效或重复")
            raw_by_pair[key]=(_text(value),_text(meaning))
        for role in roles:
            main.append([role,*[raw_by_pair.get((_text(f[0]),role),("","empty"))[0] for f in flight_rows]])
        # Matrix is the only qualification source. Never read a second editable list.
        members=book.create_sheet("人员配置")
        members.append(["人员","可做岗位"])
        roster=book.create_sheet("当天名单")
        roster.append(["人员"])
        for name in qualification["people"]:
            members.append([name,"\n".join(r["roleName"] for r in qualification["roles"] if name in r["memberNames"])])
            roster.append([name])
        for sheet,header in [("岗位配置",["岗位","同时需要人数"]),("岗位时间",["航班号","岗位","开始时间","结束时间"]),("岗位人数例外",["航班号","岗位","同时需要人数"]),("固定人员",["航班号","岗位","固定人员"]),("当日出勤",["人员","参加排班","到岗时间","离岗时间"]),("额外占用",["编号","人员","事项","开始时间","结束时间"])]:
            if sheet == "当日出勤" and book[sheet].max_column >= 5:
                header = [*header, "半休时段"]
            rows(sheet,header)
        remark_rows=rows("备注",["类型","原文","涉及人员","到岗时间"])
        remarks=[]
        for kind,text,names,arrival in remark_rows:
            if kind not in {"support","arrival","other"} or not _text(text):
                raise ValueError("备注类型需为support/arrival/other且有原文")
            remarks.append(ImportedRemark(kind,_text(text),_split_names(_text(names)),_work_time_text(arrival)))
        stream=BytesIO()
        book.save(stream)
        day=import_day(stream.getvalue(),service_date,allow_empty=True)
        day=replace(day,remarks=tuple(remarks),remarks_reviewed=reviewed,cells=tuple(replace(c,semantics=raw_by_pair[(c.flight_no,c.role_name)][1]) if (c.flight_no,c.role_name) in raw_by_pair else c for c in day.cells))
        data = {"day":imported_day_to_dict(day),"rotationEnabled":enabled,"rotationMinutes":settings["大件轮换分钟"],"source":_text(guide.get("资质来源"))}
        if "岗位性质" in book.sheetnames and _text(book["岗位性质"].cell(1, 4).value):
            flags = {_text(row[0]): row[3] == "是" for row in rows("岗位性质", ["岗位", "工作方式", "岗位性质", "同航班专岗专人"])}
            for role in data["day"]["roleConfigs"]:
                if role["roleName"] in flags: role["exclusiveSameFlight"] = flags[role["roleName"]]
        if "人员身份" in book.sheetnames:
            identities = {}
            identity_names = set()
            for name, identity in rows("人员身份", ["姓名", "人员ID"]):
                name = _text(name)
                if name not in day.roster or name in identity_names:
                    raise ValueError("人员身份姓名不在名单中或重复")
                identity_names.add(name)
                if identity not in (None, ""):
                    identities[name] = identity
            data["peopleIds"] = identities
        balance_sheets = {"排班平衡", "重点次数修正"}
        if '航班重点岗位' in book.sheetnames and not balance_sheets <= set(book.sheetnames):
            raise ValueError('航班重点岗位、排班平衡和重点次数修正必须一起保留')
        if balance_sheets & set(book.sheetnames):
            if not balance_sheets <= set(book.sheetnames):
                raise ValueError("排班平衡和重点次数修正两张表必须一起保留")
            entries = pairs("排班平衡")
            by_flight = '重点岗位范围版本' in entries
            if by_flight and (entries['重点岗位范围版本'] != 1 or '航班重点岗位' not in book.sheetnames):
                raise ValueError('航班重点岗位表缺失或范围版本无效')
            if not by_flight and '航班重点岗位' in book.sheetnames:
                raise ValueError('航班重点岗位范围版本缺失')
            if set(entries) != {"当天航班数最大差距", "重点岗位轮换启用", "重点岗位"} | ({'重点岗位范围版本'} if by_flight else set()):
                raise ValueError("排班平衡需完整填写差距开关值、重点轮换启用和重点岗位项目")
            if entries["重点岗位轮换启用"] not in {"是", "否"}:
                raise ValueError("重点岗位轮换启用请明确填是或否")
            names_by_id = {identity: name for name, identity in data.get("peopleIds", {}).items()}
            corrections = []
            for row in rows("重点次数修正", ["月份", "人员ID", "姓名", "岗位", "修正次数"] + (['航班号'] if by_flight else [])):
                month, identity, name, role, delta = row[:5]
                if _text(name) and names_by_id.get(identity) != _text(name):
                    raise ValueError("重点次数修正的人员ID与姓名不一致，请核对人员身份表")
                corrections.append({"month": _text(month), "memberId": identity, "roleName": _text(role), "delta": delta,
                    **({'flightNo': _text(row[5])} if by_flight and row[5] not in (None, '') else {})})
            data["balanceSettings"] = {
                "flightCountGap": None if entries["当天航班数最大差距"] in (None, "") else entries["当天航班数最大差距"],
                "priorityEnabled": entries["重点岗位轮换启用"] == "是",
                "priorityRoleNames": [role.strip() for role in re.split(r"[、,，;；\n]+", _text(entries["重点岗位"])) if role.strip()],
                "priorityAdjustments": corrections,
            }
            if by_flight:
                data['balanceSettings']['priorityFlightRoles'] = [
                    {'flightNo': _text(number), 'roleName': _text(role)}
                    for number, role in rows('航班重点岗位', ['航班号', '岗位'])]
        if "每周航班" in book.sheetnames:
            weekly = {}
            empty = set()
            for weekday, number, start, end in rows("每周航班", ["星期（日0至六6）", "航班号", "保障开始", "保障结束"]):
                if type(weekday) is not int or not 0 <= weekday <= 6:
                    raise ValueError("每周航班星期需为0（日）至6（六）")
                key = str(weekday)
                weekly.setdefault(key, [])
                if not _text(number) and not _text(start) and not _text(end):
                    if weekly[key] or key in empty:
                        raise ValueError("同一星期的空预设不可重复或夹有航班")
                    empty.add(key)
                else:
                    if key in empty:
                        raise ValueError("同一星期的空预设不可夹有航班")
                    weekly[key].append({"flightNo": _text(number), "windowStart": _work_time_text(start), "windowEnd": _work_time_text(end)})
            data["weeklyFlightPresets"] = weekly
        if "航班模板" in book.sheetnames:
            header = ["航班号", "保障开始", "保障结束"]
            if _text(book["航班模板"].cell(1, 4).value):
                header.append("本航班岗位")
            library = []
            for row in rows("航班模板", header):
                number, start, end = row[:3]
                template = {"flightNo": _text(number), "windowStart": _work_time_text(start), "windowEnd": _work_time_text(end), "roles": []}
                if len(row) == 4 and _text(row[3]) != "沿用通用岗位":
                    template["roleNames"] = [r.strip() for r in re.split(r"[、,，;；\n]+", _text(row[3])) if r.strip()]
                library.append(template)
            by_number = {f["flightNo"]: f for f in library}
            if "模板岗位" in book.sheetnames:
                for number, role, count, mode, start, end in rows("模板岗位", ["航班号", "岗位", "同时需要人数", "工作方式例外", "特殊开始", "特殊结束"]):
                    if _text(number) not in by_number:
                        raise ValueError("模板岗位引用未知航班")
                    by_number[_text(number)]["roles"].append({"roleName": _text(role), "requiredCount": count, "workModeOverride": _text(mode) or None, "workStart": _work_time_text(start), "workEnd": _work_time_text(end)})
            data["flightLibrary"] = validate_library(library)
        elif "模板岗位" in book.sheetnames:
            raise ValueError("模板岗位需要配套航班模板表")
        if "岗位接续偏好" in book.sheetnames:
            scope_header = _text(book["岗位接续偏好"].cell(1, 1).value)
            header = [scope_header if scope_header in {"航班号", "适用范围"} else "适用范围", "接机岗位", "优先登机岗位", "启用"]
            if _text(book["岗位接续偏好"].cell(1, 5).value):
                header.append("按顺序优先")
            data["handoffPreferences"] = []
            for row in rows("岗位接续偏好", header):
                number, role, targets, enabled = row[:4]
                scope = _text(number)
                if scope_header == "适用范围" and scope not in {"通用", "*"}:
                    raise ValueError("岗位接续偏好的适用范围请填写通用")
                item = {"flightNo": "*" if scope in {"通用", "*"} else scope, "arrivalRole": _text(role), "boardingRoles": [r.strip() for r in re.split(r"[、,，;；\n]+", _text(targets)) if r.strip()], "enabled": yes_no(enabled)}
                if len(row) == 5 and _text(row[4]):
                    item["ordered"] = yes_no(row[4])
                data["handoffPreferences"].append(item)
        if "界面偏好" in book.sheetnames:
            entries = pairs("界面偏好")
            if set(entries) != {"排班布局", "表格大小"} or entries["排班布局"] not in {"按航班", "原保障表"}:
                raise ValueError("界面偏好需完整填写排班布局和表格大小")
            data["displayPreferences"] = {"layout": "flights" if entries["排班布局"] == "按航班" else "matrix", "zoom": entries["表格大小"]}
        if {'轻松班次设置', '末班航班范围'} & set(book.sheetnames):
            if not {'轻松班次设置', '末班航班范围'} <= set(book.sheetnames):
                raise ValueError('轻松班次设置与末班航班范围须一起保留')
            entries = pairs('轻松班次设置')
            keys = {'提前下班节点': 'earlyDepartureCutoffTime', '下午统计开始': 'afternoonRestStartTime',
                    '下午统计结束': 'afternoonRestEndTime', '晚航班结束界线': 'lateShiftEndTime'}
            if set(entries) != set(keys):
                raise ValueError('轻松班次设置项目不完整或未知')
            data['relaxedShiftSettings'] = {keys[k]: _work_time_text(v) for k, v in entries.items()}
            data['relaxedShiftSettings']['lateFlightNos'] = [_text(r[0]) for r in rows('末班航班范围', ['航班号'])]
        if "半休设置" in book.sheetnames:
            rest = pairs("半休设置")
            if set(rest) != {"半休人员不参与航班数比较"}: raise ValueError("半休设置需保留航班数比较项目")
            data["halfRestSettings"] = {"exemptFlightCount": yes_no(rest["半休人员不参与航班数比较"])}
        if "分队长设置" in book.sheetnames:
            entries = pairs("分队长设置")
            if set(entries) != {"分队长最后使用", "分队长不参与航班数比较"}:
                raise ValueError("分队长设置必须保留最后使用和航班数比较两个项目")
            data["teamLeaderSettings"] = {"lastUsed": yes_no(entries["分队长最后使用"]), "exemptFlightCount": yes_no(entries["分队长不参与航班数比较"])}
        read_roster_configuration(book, data, rows, pairs, yes_no)
        if "人员状态" in book.sheetnames:
            active = {}
            leaders = {}
            statuses, remarks = {}, {}
            header = ["人员", "启用"]
            if _text(book["人员状态"].cell(1, 3).value): header.append("分队长")
            if _text(book["人员状态"].cell(1, 4).value): header.append("状态")
            if _text(book["人员状态"].cell(1, 5).value): header.append("备注")
            for row in rows("人员状态", header):
                name, enabled = _text(row[0]), row[1]
                if name in active:
                    raise ValueError("人员状态姓名重复")
                active[name] = yes_no(enabled)
                if len(header) >= 3: leaders[name] = yes_no(row[2])
                if len(header) >= 4: statuses[name] = _text(row[3])
                if len(header) >= 5: remarks[name] = "" if row[4] is None else row[4]
            data["peopleActive"] = active
            if len(header) >= 3: data["peopleTeamLeaders"] = leaders
            if len(header) >= 4: data["peopleStatus"] = statuses
            if len(header) >= 5: data["peopleRemarks"] = remarks
        daily_names = {"当天岗位例外", "当天资质例外", "当天设置例外"}
        if daily_names & set(book.sheetnames):
            if not daily_names <= set(book.sheetnames):
                raise ValueError("当天例外三张表必须完整保留，不能静默丢失设置")
            daily_header = ["岗位", "同时需要人数", "工作方式", "可兼任"]
            if _text(book["当天岗位例外"].cell(1, 5).value): daily_header.append("同航班专岗专人")
            role_overrides = []
            for row in rows("当天岗位例外", daily_header):
                role, count, mode, concurrent = row[:4]
                item = {"roleName":_text(role), "requiredCount":count, "workMode":_text(mode), "concurrent":yes_no(concurrent)}
                if len(row) == 5:
                    flag = yes_no(row[4])
                    base = next((r.exclusive_same_flight for r in day.role_configs if r.role_name == item["roleName"]), False)
                    if flag or flag != base: item["exclusiveSameFlight"] = flag
                role_overrides.append(item)
            overrides = {"roleConfigs": role_overrides,
                         "qualifications": [{"memberName":_text(name),"roleName":_text(role),"qualified":yes_no(qualified)} for name,role,qualified in rows("当天资质例外", ["人员", "岗位", "有资质"])], "settings":{}}
            settings_rows = rows("当天设置例外", ["轮换启用", "轮换分钟", "走动预留分钟"])
            if len(settings_rows) > 1:
                raise ValueError("当天排班设置例外只能填写一行")
            if settings_rows:
                enabled, minutes, transition = settings_rows[0]
                overrides["settings"] = {"rotationEnabled":yes_no(enabled),"rotationMinutes":minutes,"transitionMinutes":transition}
            data["dayOverrides"] = overrides
        if imported_history is not None:
            data["history"] = imported_history
        result = validate_complete_configuration(data)
        if group_transfer is not None:
            result['groupTransfer'] = group_transfer
        return result
    finally:
        book.close()
