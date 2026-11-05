"""Lossless A/B configuration attachment using the existing history codec."""
from .configuration_archive import append_configuration_history, read_configuration_history
from .backup_excel import _append, _sheet


_NAMES = ('历史说明', '历史目录', '历史航班', '历史排班', '_历史快照')
_GROUP_NAMES = ('班组快照说明', '班组目录', '班组航班', '班组排班', '_班组快照')


def append_groups(book, transfer):
    # Reuse the bounded, checksummed archive decoder instead of another codec.
    from openpyxl import Workbook
    temporary = Workbook()
    temporary.remove(temporary.active)
    append_configuration_history(temporary, {'formatVersion': 2, 'state': {'plans': {}, 'groupTransfer': transfer}}, [])
    for old, new in zip(_NAMES, _GROUP_NAMES):
        source = temporary[old]
        target = book.create_sheet(new)
        for row in source:
            target.append([c.value for c in row])
        target.sheet_state = source.sheet_state
    temporary.close()
    guide = book['使用说明']
    _append(guide, ['班组格式', 'A/B配置-1'])
    _append(guide, ['当前维护班组', transfer['selectedGroupId']])
    info = _sheet(book, '班组说明', ['项目', '内容'])
    for row in [('格式', 'A/B配置-1'), ('当前维护班组', transfer['selectedGroupId']),
                ('使用方法', '前面的维护表属于当前维护班组；文件同时携带A/B两组配置与历史。切换到对应组后导入，班组附表须完整保留。')]:
        _append(info, row)


def read_groups(book, guide):
    required = set(_GROUP_NAMES) | {'班组说明'}
    if guide.get('班组格式') is None and not required.intersection(book.sheetnames):
        return None
    if guide.get('班组格式') != 'A/B配置-1' or not required <= set(book.sheetnames):
        raise ValueError('A/B配置附表缺失或格式无效；不能丢失另一组数据后导入')
    from openpyxl import Workbook
    temporary = Workbook()
    temporary.remove(temporary.active)
    try:
        for old, new in zip(_NAMES, _GROUP_NAMES):
            target = temporary.create_sheet(old)
            for row in book[new]:
                target.append([c.value for c in row])
        transfer = read_configuration_history(temporary)['state']['groupTransfer']
        if transfer.get('selectedGroupId') != guide.get('当前维护班组'):
            raise ValueError('维护班组标记与附表不一致')
        return transfer
    finally:
        temporary.close()
