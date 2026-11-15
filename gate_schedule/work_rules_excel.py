"""Visible Excel tables for work relations; domain owns all rule decisions."""
from copy import deepcopy
from .domain import empty_work_rules, validate_work_rules, task_handoff_options, task_handoff_key, format_minutes

PAIR_HEADER = ['设置范围','适用航班','来源岗位','目标岗位','适用关系','搭配方式']
BAG_HEADER = ['设置范围','目标航班','大件轮次','优先后续岗位','启用']
HANDOFF_HEADER = ['来源航班','来源岗位','来源开始','来源结束','目标航班','目标岗位','目标开始','目标结束']
GROUP_SHEET = '岗位分人组'
GROUP_HEADER = ['设置范围','分组ID','组名称','适用航班','组内岗位','启用']
LEGACY_SHEETS = {'岗位搭配设置','岗位搭配','大件接岗去向','当天接岗确认'}
FORBIDDEN_SHEET = '禁止衔接'
FORBIDDEN_HEADER = ['设置范围','来源航班','来源岗位','目标岗位','目标航班','启用']
BAGGAGE_WINDOW_SHEET = '大件保障规则'
BAGGAGE_WINDOW_HEADER = ['设置范围','启用','模板结束前开检分钟','起飞前截载分钟','每人轮值分钟']
SHEETS = LEGACY_SHEETS | {GROUP_SHEET,FORBIDDEN_SHEET,BAGGAGE_WINDOW_SHEET}
SCOPES = {'same-flight':'同航班','later-flight':'晚结束航班','after-baggage':'大件完成后'}
MODES = {'separate':'必须分人','sequence':'先后接续','concurrent':'同时兼做'}
FLOW_SCOPE_SHEET = '接续航班范围'
FLOW_SCOPE_HEADER = ['适用来源航班','来源岗位','目标岗位','接续范围']
FLOW_SCOPES = {'same-flight':'本航班','later-overlap':'重叠且晚结束的其他航班'}


def write_flow_scopes(book, preferences):
    if not any('targetFlightScopes' in p for p in preferences):
        return
    sheet=book.create_sheet(FLOW_SCOPE_SHEET);sheet.append(FLOW_SCOPE_HEADER)
    for row in preferences:
        for target,scope in row.get('targetFlightScopes',{}).items():
            sheet.append(['通用' if row['flightNo']=='*' else row['flightNo'],row['arrivalRole'],target,FLOW_SCOPES[scope]])


def read_flow_scopes(book, data):
    if FLOW_SCOPE_SHEET not in book.sheetnames:
        return
    if 'handoffPreferences' not in data:
        raise ValueError('接续航班范围需要配套岗位接续偏好表')
    rows=list(book[FLOW_SCOPE_SHEET].values)
    if not rows or list(rows[0])!=FLOW_SCOPE_HEADER:
        raise ValueError('接续航班范围表头无效')
    by_key={(r['flightNo'],r['arrivalRole']):r for r in data['handoffPreferences']}
    for row in by_key.values():row['targetFlightScopes']={}
    scopes={v:k for k,v in FLOW_SCOPES.items()}
    for flight,source,target,scope in rows[1:]:
        if all(v is None or v=='' for v in (flight,source,target,scope)):continue
        row=by_key.get(('*' if flight=='通用' else flight,source))
        if row is None or target not in row['boardingRoles'] or scope not in scopes or target in row['targetFlightScopes']:
            raise ValueError('接续航班范围引用未知或重复的来源、目标或范围')
        row['targetFlightScopes'][target]=scopes[scope]


def write_work_rules(book, settings, day, rotation_minutes, rotation_enabled):
    from .day_document import imported_day_from_dict
    from .imported_schedule import imported_day_to_scenario
    for name in SHEETS:
        if name in book.sheetnames:
            del book[name]
    meta = book.create_sheet('岗位搭配设置')
    for row in [['项目','内容'],['格式版本',1],['当天独立设置','是' if day.get('workRulesOverride') else '否'],
                ['说明','搭配表决定能否同人；大件去向仅优先，不绑定姓名或固定分钟。通用填通用，航班例外填航班号。'],
                ['当天确认','表示来源任务完成后确实能接上目标，不改变实际时段；改航班/轮换后须重新确认。']]:
        meta.append(row)
    pairs = book.create_sheet('岗位搭配'); pairs.append(PAIR_HEADER)
    baggage = book.create_sheet('大件接岗去向'); baggage.append(BAG_HEADER)
    groups = None
    if 'separationGroups' in settings or (day.get('workRulesOverride') and 'separationGroups' in (day.get('workRules') or {})):
        groups = book.create_sheet(GROUP_SHEET); groups.append(GROUP_HEADER)
    forbidden = None
    if 'forbiddenHandoffs' in settings or (day.get('workRulesOverride') and 'forbiddenHandoffs' in (day.get('workRules') or {})):
        forbidden=book.create_sheet(FORBIDDEN_SHEET);forbidden.append(FORBIDDEN_HEADER)
    windows = None
    if 'baggageWindow' in settings or (day.get('workRulesOverride') and 'baggageWindow' in (day.get('workRules') or {})):
        windows=book.create_sheet(BAGGAGE_WINDOW_SHEET);windows.append(BAGGAGE_WINDOW_HEADER)
    for label,rules in [('长期',settings),('当天',day.get('workRules') if day.get('workRulesOverride') else None)]:
        if rules is None:
            continue
        if 'baggageWindow' in rules:
            window=rules['baggageWindow']
            windows.append([label,'是' if window is not None else '否',
                window['startBeforeMinutes'] if window is not None else None,
                window['endBeforeMinutes'] if window is not None else None,
                window['rotationMinutes'] if window is not None else None])
        for r in rules['pairs']:
            pairs.append([label,'通用' if r['flightNo']=='*' else r['flightNo'],r['firstRole'],r['secondRole'],SCOPES[r['scope']],MODES[r['mode']]])
        for r in rules['baggage']:
            baggage.append([label,'通用' if r['flightNo']=='*' else r['flightNo'],r['position'],'、'.join(r['targetRoles']),'是' if r['enabled'] else '否'])
        for r in rules.get('separationGroups', []):
            groups.append([label,r['id'],r['name'],'通用' if r['flightNo']=='*' else r['flightNo'],
                           '、'.join(r['roles']),'是' if r['enabled'] else '否'])
        for r in rules.get('forbiddenHandoffs', []):
            for target in r['targets'] or [None]:
                forbidden.append([label,'通用' if r['flightNo']=='*' else r['flightNo'],r['sourceRole'],
                    target['role'] if target else None,FLOW_SCOPES[target['flightScope']] if target else None,'是' if r['enabled'] else '否'])
    handoffs = book.create_sheet('当天接岗确认'); handoffs.append(HANDOFF_HEADER)
    raw = deepcopy(day)
    if not raw.get('workRulesOverride'):
        raw['workRules'] = deepcopy(settings)
    scenario,_ = imported_day_to_scenario(imported_day_from_dict(raw),rotation_minutes,rotation_enabled)
    tasks = {t.id:t for t in scenario.tasks}; flights = {f.id:f.flight_no for f in scenario.flights}
    import json
    for key in day.get('taskHandoffs',[]):
        try:
            a,b = json.loads(key)
            source,target = tasks[a[0]],tasks[b[0]]
            if task_handoff_key(source,target,scenario.service_date) != key:
                raise ValueError()
        except (ValueError,TypeError,KeyError,IndexError):
            raise ValueError('当天接岗确认已失效，请清除后重新导出')
        handoffs.append([flights.get(source.flight_id,''),source.role_id,format_minutes(source.start),format_minutes(source.end),
                         flights.get(target.flight_id,''),target.role_id,format_minutes(target.start),format_minutes(target.end)])


def read_work_rules(book, data):
    from .excel_io import configuration_day
    from .day_document import imported_day_from_dict
    from .imported_schedule import imported_day_to_scenario
    found = SHEETS & set(book.sheetnames)
    if not found:
        return
    if found & LEGACY_SHEETS != LEGACY_SHEETS:
        raise ValueError('岗位搭配、大件去向及当天确认四张表须完整保留')
    def rows(name,header):
        raw = list(book[name].values)
        if list(raw[0]) != header:
            raise ValueError(name+'表头无效')
        return [list(r) for r in raw[1:] if any(c is not None and c != '' for c in r)]
    meta = dict(rows('岗位搭配设置',['项目','内容']))
    if meta.get('格式版本') != 1 or meta.get('当天独立设置') not in {'是','否'}:
        raise ValueError('岗位搭配版本或当天设置标记无效')
    by_scope = {'长期':empty_work_rules(),'当天':empty_work_rules()}
    if BAGGAGE_WINDOW_SHEET in found:
        for rules in by_scope.values():rules['baggageWindow']=None
        seen=set()
        for label,enabled,start,end,minutes in rows(BAGGAGE_WINDOW_SHEET,BAGGAGE_WINDOW_HEADER):
            if label not in by_scope or label in seen or enabled not in {'是','否'}:
                raise ValueError('大件保障规则设置范围、启用或重复行无效')
            seen.add(label)
            if enabled=='否':
                if any(v is not None and v!='' for v in (start,end,minutes)):
                    raise ValueError('关闭的大件保障规则请清空分钟列，避免误解')
            else:
                by_scope[label]['baggageWindow']=dict(startBeforeMinutes=start,endBeforeMinutes=end,rotationMinutes=minutes)
    if FORBIDDEN_SHEET in found:
        entries={};scopes={v:k for k,v in FLOW_SCOPES.items()}
        for rules in by_scope.values():rules['forbiddenHandoffs']=[]
        for label,flight,source,target,scope,enabled in rows(FORBIDDEN_SHEET,FORBIDDEN_HEADER):
            if label not in by_scope or enabled not in {'是','否'}:
                raise ValueError('禁止衔接设置范围或开关无效')
            key=(label,flight,source)
            if key not in entries:
                entries[key]=dict(flightNo='*' if flight=='通用' else flight,sourceRole=source,targets=[],enabled=enabled=='是')
                by_scope[label]['forbiddenHandoffs'].append(entries[key])
            entry=entries[key]
            if entry['enabled']!=(enabled=='是'):
                raise ValueError('同来源的禁止衔接启用状态不一致')
            if target is None and scope is None and enabled=='否':continue
            if scope not in scopes:
                raise ValueError('禁止衔接目标航班无效')
            entry['targets'].append(dict(role=target,flightScope=scopes[scope]))
    if GROUP_SHEET in found:
        import re
        for rules in by_scope.values():
            rules['separationGroups'] = []
        for label,key,name,flight,roles,enabled in rows(GROUP_SHEET,GROUP_HEADER):
            if label not in by_scope or enabled not in {'是','否'}:
                raise ValueError('岗位分人组范围或启用选项无效')
            by_scope[label]['separationGroups'].append(dict(id=key,name=name,
                flightNo='*' if flight=='通用' else flight,
                roles=[s.strip() for s in re.split('[、,，\n]',roles or '') if s.strip()],enabled=enabled=='是'))
    scopes = {v:k for k,v in SCOPES.items()}; modes = {v:k for k,v in MODES.items()}
    for label,flight,a,b,scope,mode in rows('岗位搭配',PAIR_HEADER):
        if label not in by_scope or scope not in scopes or mode not in modes:
            raise ValueError('岗位搭配设置范围或选项无效')
        by_scope[label]['pairs'].append(dict(flightNo='*' if flight=='通用' else flight,firstRole=a,secondRole=b,scope=scopes[scope],mode=modes[mode]))
    for label,flight,position,targets,enabled in rows('大件接岗去向',BAG_HEADER):
        if label not in by_scope or enabled not in {'是','否'}:
            raise ValueError('大件接岗范围或启用选项无效')
        import re
        by_scope[label]['baggage'].append(dict(flightNo='*' if flight=='通用' else flight,position=position,
            targetRoles=[s.strip() for s in re.split('[、,，\n]',targets or '') if s.strip()],enabled=enabled=='是'))
    for rules in by_scope.values():
        validate_work_rules(rules,data['day']['roleNames'])
    override = meta['当天独立设置']=='是'
    if not override and any(by_scope['当天'].values()):
        raise ValueError('当天独立设置关闭时不应填写当天搭配，避免静默忽略')
    data['workRules'] = by_scope['长期']
    data['day']['workRulesOverride'] = override
    data['day']['workRules'] = by_scope['当天'] if override else deepcopy(data['workRules'])
    raw = configuration_day(data)
    settings = data.get('dayOverrides',{}).get('settings',{})
    scenario, _ = imported_day_to_scenario(imported_day_from_dict(raw),settings.get('rotationMinutes',data['rotationMinutes']),settings.get('rotationEnabled',data['rotationEnabled']))
    options = task_handoff_options(scenario)
    tasks = {t.id:t for t in scenario.tasks}; flights = {f.id:f.flight_no for f in scenario.flights}
    keys = {}
    for o in options:
        source,target = tasks[o['sourceTaskId']],tasks[o['targetTaskId']]
        row = (flights.get(source.flight_id,''),source.role_id,format_minutes(source.start),format_minutes(source.end),
               flights.get(target.flight_id,''),target.role_id,format_minutes(target.start),format_minutes(target.end))
        keys[row] = o['key']
    confirmed = []
    for row in rows('当天接岗确认',HANDOFF_HEADER):
        key = keys.get(tuple(row))
        if not key or key in confirmed:
            raise ValueError('当天接岗确认已失效或重复，请按当前航班/岗位/轮次重新确认')
        confirmed.append(key)
    if confirmed:
        data['day']['taskHandoffs'] = confirmed
