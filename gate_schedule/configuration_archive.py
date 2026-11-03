"""Compact lossless history with readable business tables in configuration XLSX."""
import base64
import hashlib
import json
import zlib

from .backup_excel import _append, _sheet


_CHUNK = 24000
_MAX_BYTES = 128 * 1024 * 1024
_MARKER = '登机口历史快照-1'


def append_configuration_history(book, history, schedules):
    state = history['state']
    directory = _sheet(book, '历史目录', ['方案ID', '日期', '标题', '草稿分配条数', '正式结果', '航班数'])
    flights = _sheet(book, '历史航班', ['方案ID', '日期', '航班号', '保障开始', '保障结束'])
    assignments = _sheet(book, '历史排班', ['方案ID', '日期', '版本', '航班号', '岗位', '人员ID', '姓名', '开始时间', '结束时间'])
    for identity, plan in state['plans'].items():
        day = plan['day']
        _append(directory, [identity, day['serviceDate'], day['title'], len(plan.get('draft', [])),
                            '有' if plan.get('adopted') else '无', len(day['flights'])])
        for flight in day['flights']:
            _append(flights, [identity, day['serviceDate'], flight['flightNo'], flight['windowStart'], flight['windowEnd']])
        for version, schedule in schedules.get(identity, {}).items():
            if not schedule:
                continue
            tasks = {t['id']: t for t in schedule['tasks']}
            members = {m['id']: m['name'] for m in schedule['members']}
            for entry in schedule['assignments']:
                task = tasks[entry['taskId']]
                for number in task['flightNos'] or ['']:
                    _append(assignments, [identity, schedule['serviceDate'], version, number, task['roleId'],
                        entry['memberId'], members[entry['memberId']], task['start'], task['end']])
    # Only the snapshot reconstructs old plans. The editable front tables remain
    # the single source for current configuration, so small edits never rewrite history.
    raw = json.dumps(history, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('ascii')
    encoded = base64.b64encode(zlib.compress(raw, 9)).decode('ascii')
    chunks = [encoded[i:i + _CHUNK] for i in range(0, len(encoded), _CHUNK)]
    info = _sheet(book, '历史说明', ['项目', '内容'])
    for row in [('格式', _MARKER), ('原始字节', len(raw)), ('分段数', len(chunks)),
                ('SHA256', hashlib.sha256(raw).hexdigest()), ('方案数', len(state['plans'])),
                ('使用方法', '人员、资质、航班和规则在前面的维护表修改；历史目录、历史航班和历史排班供核对。旧班表在工作台修改，历史附表和隐藏快照请完整保留。')]:
        _append(info, row)
    snapshot = _sheet(book, '_历史快照', ['段号', '内容'])
    for index, chunk in enumerate(chunks, 1):
        _append(snapshot, [index, chunk])
    snapshot.sheet_state = 'hidden'


def read_configuration_history(book):
    required = {'历史说明', '历史目录', '历史航班', '历史排班', '_历史快照'}
    if not required <= set(book.sheetnames):
        raise ValueError('配置历史附表缺失，请使用完整文件')
    def rows(name, header):
        iterator = book[name].iter_rows()
        if [c.value for c in next(iterator, ())] != header:
            raise ValueError('配置历史表头无效')
        for row in iterator:
            if len(row) != len(header) or any(c.data_type in ('f', 'e') for c in row):
                raise ValueError('配置历史不能包含公式、错误值或多余列')
            yield [c.value for c in row]
    info = {}
    for key, value in rows('历史说明', ['项目', '内容']):
        if key in info:
            raise ValueError('配置历史说明项目重复')
        info[key] = value
    if info.get('格式') != _MARKER or type(info.get('原始字节')) is not int or not 0 < info['原始字节'] <= _MAX_BYTES:
        raise ValueError('配置历史格式或长度无效')
    if type(info.get('分段数')) is not int or not 0 < info['分段数'] <= 10000:
        raise ValueError('配置历史分段数无效')
    chunks = []
    for index, chunk in rows('_历史快照', ['段号', '内容']):
        if type(index) is not int or index != len(chunks) + 1 or not isinstance(chunk, str) or not 0 < len(chunk) <= _CHUNK:
            raise ValueError('配置历史分段缺失、重复或内容无效')
        chunks.append(chunk)
    if len(chunks) != info['分段数']:
        raise ValueError('配置历史分段不完整')
    try:
        compressed = base64.b64decode(''.join(chunks), validate=True)
        inflater = zlib.decompressobj()
        raw = inflater.decompress(compressed, info['原始字节'] + 1)
        if not inflater.eof or inflater.unused_data or inflater.unconsumed_tail or len(raw) != info['原始字节']:
            raise ValueError('配置历史长度不一致')
        if hashlib.sha256(raw).hexdigest() != info.get('SHA256'):
            raise ValueError('配置历史内容校验失败')
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('配置历史对象字段重复')
                result[key] = value
            return result
        history = json.loads(raw.decode('ascii'), object_pairs_hook=unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError('配置历史数值无效')))
        if not isinstance(history, dict) or history.get('formatVersion') != 2 or not isinstance(history.get('state'), dict) or not isinstance(history['state'].get('plans'), dict):
            raise ValueError('配置历史结构无效')
        if len(history['state']['plans']) != info.get('方案数'):
            raise ValueError('配置历史方案数不一致')
        return history
    except (TypeError, KeyError, UnicodeError, zlib.error, ValueError) as error:
        raise ValueError('配置历史内容损坏或不完整；原数据未改变') from error
