"""Lossless workbook adapter for Workbench backups; restore owns business validation."""
import base64
from io import BytesIO
import json
import math
import re

from openpyxl import Workbook, load_workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font, PatternFill


_MARKER = '登机口完整Excel备份'
_HEADER = ['节点编号', '上级节点', '字段类型', '字段/位置', '字段编码', '数据类型', '内容', '内容编码']
_TEXT_HEADER = ['文本编号', '段号', '编码', '内容']
_ROWS_PER_SHEET = 50000
_TEXT_LIMIT = 14000  # Also fits Excel's UTF-16 limit when every character is astral.


def _xml_text(value):
    return all(ord(c) in (9, 10, 13) or 0x20 <= ord(c) <= 0xD7FF
               or 0xE000 <= ord(c) <= 0xFFFD or 0x10000 <= ord(c) <= 0x10FFFF for c in value)


def _append(sheet, values, *, header=False):
    cells = []
    for value in values:
        cell = WriteOnlyCell(sheet, value=value)
        if isinstance(value, str):
            cell.data_type = 's'
        if header:
            cell.font = Font(bold=True, color='FFFFFF')
            cell.fill = PatternFill('solid', fgColor='B4232D')
        cells.append(cell)
    sheet.append(cells)


def _sheet(book, name, header):
    sheet = book.create_sheet(name)
    sheet.freeze_panes = 'A2'
    for column in ('A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'):
        sheet.column_dimensions[column].width = 24 if column not in ('D', 'G') else 48
    _append(sheet, header, header=True)
    return sheet


def export_backup_excel(backup):
    book = Workbook(write_only=True)
    append_backup_sheets(book, backup)
    stream = BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


def append_backup_sheets(book, backup, *, configuration=False):
    """Share the lossless format with editable configuration workbooks."""
    info = _sheet(book, '备份说明', ['项目', '内容'])
    _append(info, ['备份类型', _MARKER])
    _append(info, ['文件版本', 1])
    _append(info, ['用途', '在配置页导入完整Excel，更新维护表并补入全部历史。' if configuration else '在“导入数据”中选择此文件，可完整恢复人员、规则、草稿和正式历史。'])
    _append(info, ['使用方式', '前面的维护表可编辑；历史附表完整保留，旧班表在工作台修改。' if configuration else '人员和规则请在工作台维护后重新备份；此文件用于整库恢复。'])
    state = backup.get('state', {})
    people = _sheet(book, '人员一览', ['人员ID', '姓名', '启用', '分队长', '状态', '岗位资质'])
    plans = _sheet(book, '方案一览', ['方案ID', '日期', '标题', '草稿分配条数', '正式结果', '输入版本', '航班数', '航班号'])
    def preview(value):
        return ''.join(c if _xml_text(c) else '�' for c in str(value))[:5000]
    for person in state.get('people', []):
        _append(people, [preview(person.get('id', '')), preview(person.get('name', '')),
            '是' if person.get('active') else '否', '是' if person.get('teamLeader') else '否',
            preview(person.get('status', '')), preview('、'.join(person.get('eligibleRoleNames', [])))])
    for identity, plan in state.get('plans', {}).items():
        _append(plans, [preview(identity), preview(plan.get('day', {}).get('serviceDate', '')),
            preview(plan.get('day', {}).get('title', '')), len(plan.get('draft', [])),
            '有' if plan.get('adopted') else '无', plan.get('inputRevision'), len(plan.get('day', {}).get('flights', [])),
            preview('、'.join(f['flightNo'] for f in plan.get('day', {}).get('flights', [])))])
    texts = []
    def text_value(value):
        literal = _xml_text(value) and '\r' not in value
        if len(value) <= _TEXT_LIMIT and literal:
            return value, '原文'
        encoding = '原文' if literal else 'Base64'
        source = value if encoding == '原文' else base64.b64encode(value.encode('utf-8', 'surrogatepass')).decode('ascii')
        identity = len(texts) + 1
        chunks = [source[i:i + _TEXT_LIMIT] for i in range(0, len(source), _TEXT_LIMIT)]
        texts.append((identity, encoding, chunks))
        return str(identity), '分段'
    next_id = 0
    def records(value, parent=None, field_type='根', field='', depth=0):
        nonlocal next_id
        if depth > 100:
            raise ValueError('备份数据层级过深')
        next_id += 1
        identity = next_id
        key, key_encoding = text_value(str(field)) if field_type != '根' else ('', '')
        content, encoding = '', ''
        if isinstance(value, dict): kind = '对象'
        elif isinstance(value, list): kind = '列表'
        elif value is None: kind = '空值'
        elif type(value) is bool: kind, content, encoding = '布尔', '是' if value else '否', '原文'
        elif type(value) is int: kind, content, encoding = '整数', str(value), '原文'
        elif type(value) is float and math.isfinite(value): kind, content, encoding = '小数', repr(value), '原文'
        elif isinstance(value, str):
            kind = '文本'
            content, encoding = text_value(value)
        else:
            raise ValueError('备份包含不支持的数据类型')
        yield [identity, parent, field_type, key, key_encoding, kind, content, encoding]
        if kind == '对象':
            for key, child in value.items():
                if not isinstance(key, str): raise ValueError('备份字段名必须是文本')
                yield from records(child, identity, '字段', key, depth + 1)
        elif kind == '列表':
            for index, child in enumerate(value):
                yield from records(child, identity, '位置', str(index), depth + 1)
    sheet = None
    sheet_count = count = 0
    for row in records(backup):
        if count % _ROWS_PER_SHEET == 0:
            sheet_count += 1
            sheet = _sheet(book, f'完整数据{sheet_count:03d}', _HEADER)
        _append(sheet, row)
        count += 1
    pool = _sheet(book, '长文本', _TEXT_HEADER)
    chunk_count = 0
    for identity, encoding, chunks in texts:
        for index, chunk in enumerate(chunks, 1):
            _append(pool, [identity, index, encoding, chunk])
            chunk_count += 1
    for key, value in [('数据记录', count), ('数据工作表', sheet_count), ('文本分段', chunk_count),
                       ('人员数', len(state.get('people', []))), ('方案数', len(state.get('plans', {})))]:
        _append(info, [key, value])


def read_backup_sheets(book):
    """Decode an open workbook; the caller owns its lifetime and business checks."""
    def rows(name, header):
        iterator = book[name].iter_rows()
        first = next(iterator, ())
        if [c.value for c in first] != header:
            raise ValueError(f'{name}表头无效')
        for row in iterator:
            if len(row) != len(header) or any(c.data_type in ('f', 'e') for c in row):
                raise ValueError(f'{name}包含公式、错误值或多余列')
            yield [c.value for c in row]
    info = {}
    for key, value in rows('备份说明', ['项目', '内容']):
        if key in info: raise ValueError('备份说明有重复项目')
        info[key] = value
    if info.get('备份类型') != _MARKER or info.get('文件版本') != 1:
        raise ValueError('请选择本系统导出的完整Excel备份')
    data_sheets = [n for n in book.sheetnames if re.fullmatch(r'完整数据\d{3,}', n)]
    if type(info.get('数据工作表')) is not int or data_sheets != [f'完整数据{i:03d}' for i in range(1, info['数据工作表'] + 1)] or not data_sheets:
        raise ValueError('完整数据工作表缺失或顺序错误')
    pool = {}
    chunk_count = 0
    for identity, index, encoding, text in rows('长文本', _TEXT_HEADER):
        if type(identity) is not int or identity < 1 or type(index) is not int or encoding not in ('原文', 'Base64') or not isinstance(text, str):
            raise ValueError('长文本分段无效')
        item = pool.setdefault(str(identity), [encoding, []])
        if item[0] != encoding or index != len(item[1]) + 1:
            raise ValueError('长文本分段缺失、重复或顺序错误')
        item[1].append(text)
        chunk_count += 1
    if chunk_count != info.get('文本分段'): raise ValueError('长文本分段不完整')
    used_text = set()
    def decode(value, encoding):
        if value is None: value = ''
        if not isinstance(value, str): raise ValueError('备份文字或精确数字单元格类型无效')
        if encoding == '原文': return value
        if encoding != '分段' or value not in pool: raise ValueError('备份文本引用无效')
        used_text.add(value)
        kind, chunks = pool[value]
        text = ''.join(chunks)
        return text if kind == '原文' else base64.b64decode(text, validate=True).decode('utf-8', 'surrogatepass')
    nodes = {}
    result = None
    for name in data_sheets:
        for identity, parent, field_type, field, field_encoding, kind, raw, encoding in rows(name, _HEADER):
            if type(identity) is not int or identity != len(nodes) + 1:
                raise ValueError('备份数据记录缺失、重复或顺序错误')
            if kind in ('对象', '列表', '空值'):
                if raw is not None or encoding is not None: raise ValueError('备份容器或空值内容无效')
                value = {} if kind == '对象' else [] if kind == '列表' else None
            else:
                text = decode(raw, encoding)
                if kind == '文本': value = text
                elif kind == '布尔' and text in ('是', '否'): value = text == '是'
                elif kind == '整数' and re.fullmatch(r'0|-?[1-9]\d*', text): value = int(text)
                elif kind == '小数' and math.isfinite(float(text)): value = float(text)
                else: raise ValueError('备份数据类型或内容无效')
            if identity == 1:
                if parent is not None or field_type != '根' or field is not None or field_encoding is not None or kind != '对象':
                    raise ValueError('备份根记录无效')
                result = value
            else:
                if type(parent) is not int or parent not in nodes: raise ValueError('备份上级记录无效')
                target = nodes[parent]
                key = decode(field, field_encoding)
                if isinstance(target, dict) and field_type == '字段' and key not in target:
                    target[key] = value
                elif isinstance(target, list) and field_type == '位置' and key == str(len(target)):
                    target.append(value)
                else: raise ValueError('备份字段重复、位置缺失或上级类型无效')
            nodes[identity] = value
    if len(nodes) != info.get('数据记录') or used_text != set(pool): raise ValueError('备份数据不完整')
    return result


def _read_excel(content):
    book = load_workbook(BytesIO(content), read_only=True, data_only=False)
    try:
        if '使用说明' in book.sheetnames and '岗位资质' in book.sheetnames:
            raise ValueError('配置Excel请在配置页使用“导入完整Excel”，以应用维护表修改')
        return read_backup_sheets(book)
    finally:
        book.close()


def import_backup(content):
    """Accept new XLSX and the previously exported UTF-8 JSON backups."""
    try:
        return _read_excel(content) if content.startswith(b'PK') else json.loads(content.decode('utf-8-sig'))
    except Exception as error:
        raise ValueError('备份文件无法读取，请选择完整的Excel备份或旧JSON备份；配置Excel请在配置页导入完整Excel') from error
