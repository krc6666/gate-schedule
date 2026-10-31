"""Monthly duty planning; adapters never reimplement rotation or eligibility."""
from calendar import monthrange
from collections import Counter
from copy import deepcopy
from datetime import date, timedelta
from functools import lru_cache
from io import BytesIO

from ortools.sat.python import cp_model
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment
from .domain import validate_duty_roster


def default_roster(state):
    dates = [p['day']['serviceDate'] for p in state['plans'].values()]
    return {'enabled': True, 'anchorDate': min(dates) if dates else date.today().isoformat(),
            'dutyMemberIds': [], 'standbyMemberIds': [], 'overrides': []}


def roster_eligible(person):
    return (not person.get('deleted', False) and person['active']
            and person.get('status', '正常') == '正常' and not person.get('teamLeader', False))


def month_dates(month, anchor):
    start = date.fromisoformat(month+'-01')
    if start.strftime('%Y-%m') != month:
        raise ValueError('轮值月份必须为YYYY-MM')
    origin = date.fromisoformat(anchor)
    return [(start+timedelta(days=i)).isoformat() for i in range(monthrange(start.year, start.month)[1])
            if ((start+timedelta(days=i))-origin).days % 2 == 0]


@lru_cache(maxsize=64)
def _automatic(dates, duty_ids, standby_ids):
    """Small independent model: coverage first, balanced rounds next, stable ties last."""
    if not duty_ids and not standby_ids:
        return tuple((d,None,None,None) for d in dates)
    model=cp_model.CpModel(); pools=[duty_ids, standby_ids, standby_ids]; variables={}; gaps=[]
    for d in range(len(dates)):
        for slot,pool in enumerate(pools):
            entries=[]
            for person in pool:
                variable=model.new_bool_var(f'{d}/{slot}/{person}'); variables[d,slot,person]=variable;entries.append(variable)
            gap=model.new_bool_var(f'gap/{d}/{slot}');model.add(sum(entries)+gap==1);gaps.append((slot,gap))
        for person in set(duty_ids)|set(standby_ids):
            model.add(sum(v for (day,slot,p),v in variables.items() if day==d and p==person)<=1)
    # Fairness and first-round coverage share the same max/min count objective.
    fairness=[]
    for label,pool,slots in [('duty',duty_ids,[0]),('standby',standby_ids,[1,2])]:
        if not pool: continue
        counts=[]
        for p in pool:
            count=model.new_int_var(0,len(dates),f'{label}/{p}')
            model.add(count==sum(v for (d,s,n),v in variables.items() if n==p and s in slots));counts.append(count)
        hi=model.new_int_var(0,len(dates),label+'max');lo=model.new_int_var(0,len(dates),label+'min')
        model.add_max_equality(hi,counts);model.add_min_equality(lo,counts);fairness.append(hi-lo)
    seed=int(dates[0][:4])*12+int(dates[0][5:7]) if dates else 0
    tie=sum(((pool.index(p)-d-seed-slot)%len(pool))*v
            for (d,slot,p),v in variables.items() for pool in [pools[slot]])
    tie_bound=max(1,len(variables)*max(len(duty_ids),len(standby_ids)))
    fair_weight=tie_bound+1;coverage_weight=fair_weight*(len(dates)*2+1)
    model.minimize(coverage_weight*sum((len(dates)*2+1 if s==0 else 1)*v for s,v in gaps)+fair_weight*sum(fairness)+tie)
    solver=cp_model.CpSolver();solver.parameters.num_search_workers=1;solver.parameters.max_time_in_seconds=3;solver.parameters.random_seed=0
    status=solver.solve(model)
    if status not in (cp_model.OPTIMAL,cp_model.FEASIBLE):
        # Timeout is explicit and cannot fabricate assignments.
        raise ValueError('月度轮值计算未完成，请重试；原安排保留')
    result=[]
    for d,day in enumerate(dates):
        selected=[next((p for p in pool if solver.value(variables[d,slot,p])),None) for slot,pool in enumerate(pools)]
        result.append((day,*selected))
    return tuple(result)


def validate_roster_row(state, row):
    people={p['id']:p for p in state['people']};settings=state['dutyRoster'];issues=[]
    values=[row['dutyMemberId'],*row['standbyMemberIds']]
    if len([p for p in values if p]) != len(set(p for p in values if p)):
        issues.append('值班与两名备勤不能重复')
    for slot,identity in enumerate(values):
        label=['值班','次日备勤一','次日备勤二'][slot]
        if not identity:
            issues.append(label+'待安排');continue
        person=people.get(identity)
        if not person:
            issues.append(label+'人员不存在');continue
        if identity not in settings['dutyMemberIds' if slot==0 else 'standbyMemberIds']:
            issues.append(f'{label}：{person["name"]}无对应资质')
        if not roster_eligible(person):
            reason = '已删除' if person.get('deleted') else '已停用' if not person['active'] else '分队长不参与轮值' if person.get('teamLeader') else person.get('status', '正常')
            issues.append(f'{label}：{person["name"]}当前{reason}')
    return issues


def duty_statistics(state, month=None):
    settings=state['dutyRoster']; month=month or settings['anchorDate'][:7]
    dates=month_dates(month,settings['anchorDate'])
    people={p['id']:p for p in state['people']}
    def pool(key):return tuple(i for i in settings[key] if i in people and roster_eligible(people[i]))
    automatic=_automatic(tuple(dates),pool('dutyMemberIds'),pool('standbyMemberIds')) if settings['enabled'] else tuple((d,None,None,None) for d in dates)
    overrides={r['date']:r for r in settings['overrides']};rows=[]
    for day,duty,one,two in automatic:
        row=deepcopy(overrides.get(day,{'date':day,'dutyMemberId':duty,'standbyMemberIds':[one,two]}))
        row.update(standbyDate=(date.fromisoformat(day)+timedelta(days=1)).isoformat(),adjusted=day in overrides)
        row['issues']=validate_roster_row(state,row) if settings['enabled'] or row['adjusted'] else []
        rows.append(row)
    duty=Counter(r['dutyMemberId'] for r in rows if r['dutyMemberId'])
    standby=Counter(p for r in rows for p in r['standbyMemberIds'] if p)
    members=[]
    for p in state['people']:
        identity=p['id'];dq=identity in settings['dutyMemberIds'];sq=identity in settings['standbyMemberIds']
        if not (dq or sq or duty[identity] or standby[identity]):continue
        members.append({'memberId':identity,'memberName':p['name'],'dutyQualified':dq,'standbyQualified':sq,'available':roster_eligible(p),
            'dutyCount':duty[identity],'standbyCount':standby[identity],
            'dutyDates':[r['date'] for r in rows if r['dutyMemberId']==identity],
            'standbyDates':[r['standbyDate'] for r in rows if identity in r['standbyMemberIds']]})
    def spread(key,qualified):
        counts=[p[key] for p in members if p[qualified] and p['available']]
        return max(counts,default=0)-min(counts,default=0)
    return {'revision':state['revision'],'month':month,'rows':rows,'people':members,
            'dutyDifference':spread('dutyCount','dutyQualified'),'standbyDifference':spread('standbyCount','standbyQualified')}


def update_slot(state,payload):
    day=payload['date']; slot=payload['slot']; identity=payload.get('memberId') or None
    if slot not in ['duty','standby-0','standby-1']:raise ValueError('轮值位置无效')
    row=next((r for r in duty_statistics(state,day[:7])['rows'] if r['date']==day),None)
    if row is None:raise ValueError('该日期不是工作班')
    values=[row['dutyMemberId'],*row['standbyMemberIds']];target=['duty','standby-0','standby-1'].index(slot)
    if identity and identity in values:
        source=values.index(identity);values[target],values[source]=values[source],values[target]
    else:values[target]=identity
    result={'date':day,'dutyMemberId':values[0],'standbyMemberIds':values[1:]}
    invalid=[m for m in validate_roster_row(state,result) if not m.endswith('待安排')]
    existing=set(validate_roster_row(state,row))
    introduced=[m for m in invalid if m not in existing]
    if introduced:raise ValueError('；'.join(introduced))
    state['dutyRoster']['overrides']=[r for r in state['dutyRoster']['overrides'] if r['date']!=day]+[result]


def export_roster(state,month):
    book=Workbook();sheet=book.active;sheet.title='值班备勤表'
    sheet.append(['工作班日期','值班人员','次日备勤一','次日备勤二'])
    names={p['id']:p['name'] for p in state['people']}
    for row in duty_statistics(state,month)['rows']:
        sheet.append([row['date'],*[names.get(i,'') for i in [row['dutyMemberId'],*row['standbyMemberIds']]]])
    guide=book.create_sheet('说明');guide.append(['工作班值班1人，次日休息日备勤2人。填写姓名；空白明确清空。导入前核对预览。'])
    for s in book:
        s.freeze_panes='B2'
        for cell in s[1]:cell.font=Font(color='FFFFFF',bold=True);cell.fill=PatternFill('solid',fgColor='B4232D')
        for col in ['A','B','C','D']:s.column_dimensions[col].width=24
        for row in s:
            for c in row:
                c.alignment=Alignment(wrap_text=True)
                if isinstance(c.value,str):c.data_type='s'
    stream=BytesIO();book.save(stream);book.close();return stream.getvalue()


def preview_roster(state,content):
    book=load_workbook(BytesIO(content),data_only=False)
    try:
        sheet=book['值班备勤表'];values=list(sheet.iter_rows(values_only=False))
        if [c.value for c in values[0]]!=['工作班日期','值班人员','次日备勤一','次日备勤二']:raise ValueError('请选择值班备勤模板')
        names={p['name']:p['id'] for p in state['people'] if not p.get('deleted',False)};rows=[]
        for cells in values[1:]:
            if all(c.value is None for c in cells):continue
            if any(c.data_type in {'f','e'} for c in cells):raise ValueError('轮值表不能使用公式或错误值')
            text=[str(c.value).strip() if c.value is not None else '' for c in cells]
            ids=[]
            for n in text[1:]:
                if n and n not in names:raise ValueError('未知人员：'+n)
                ids.append(names.get(n))
            rows.append({'date':text[0],'dutyMemberId':ids[0],'standbyMemberIds':ids[1:]})
        if not rows:raise ValueError('轮值表没有日期记录')
        candidate={**deepcopy(state['dutyRoster']),'overrides':rows}
        validate_duty_roster(candidate,{p['id'] for p in state['people']})
        for row in rows:
            issues=[m for m in validate_roster_row(state,row) if not m.endswith('待安排')]
            if issues:raise ValueError('；'.join(issues))
        return {'expectedRevision':state['revision'],'rows':rows}
    finally:book.close()
