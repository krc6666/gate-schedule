"""Rebuild the single maintainable trial workbook without changing any database."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gate_schedule.domain import phase_role_config
from gate_schedule.excel_io import imported_day_from_dict, imported_day_to_dict, export_complete_configuration


def main():
    source = json.loads((ROOT/'templates/登机口完整试用配置-20260802.json').read_text(encoding='utf-8'))['state']
    original = source['plans'][source['selectedPlanId']]
    raw = deepcopy(original['day'])
    raw['title'] = '环节与岗位性质试用 · 服务08.02'
    raw['remarks'] = [r for r in raw['remarks'] if not r['rawText'].startswith(('试用时段：', '当前矩阵由'))]
    raw['remarks'].append({'kind':'other','rawText':'接机和登机按环节，督导可兼任；入境申报、特服暂按全程保障，可人工调整。未确认任何跨航班衔接；导入后点击重新排班。出勤、人数和资格仍为试用建议。','people':[],'time':None})
    day = imported_day_from_dict(raw)
    day = replace(day, release_policy='task', attendance_mode='duty', phase_handoffs=(),
                  member_availability=tuple(replace(a, available_from=None, available_until=None) for a in day.member_availability),
                  role_configs=tuple(phase_role_config(r) for r in day.role_configs),
                  cells=tuple(replace(c, work_start=None, work_end=None, work_mode_override=None) for c in day.cells))
    content = export_complete_configuration({'day':imported_day_to_dict(day),'rotationEnabled':original['rotationEnabled'],
                                            'rotationMinutes':original['rotationMinutes'],'source':'历史资质建议与真实航班；环节方案，试用出勤/人数待核对'})
    target = Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'templates/登机口完整配置-可维护试用.xlsx'
    target.write_bytes(content)
    print(target)


if __name__ == '__main__':
    main()
