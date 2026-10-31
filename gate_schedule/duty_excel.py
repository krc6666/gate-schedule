"""Visible Excel adapters for the unified roster configuration."""


def write_roster_configuration(book, settings, people_ids):
    sheet=book.create_sheet('轮值设置');sheet.append(['项目','内容'])
    sheet.append(['启用值班备勤','是' if settings['enabled'] else '否'])
    sheet.append(['基准工作日',settings['anchorDate']])
    sheet=book.create_sheet('轮值资质');sheet.append(['人员','值班资质','备勤资质'])
    for name,identity in people_ids.items():
        sheet.append([name,'是' if identity in settings['dutyMemberIds'] else '否','是' if identity in settings['standbyMemberIds'] else '否'])
    names={identity:name for name,identity in people_ids.items()}
    sheet=book.create_sheet('值班备勤表');sheet.append(['工作班日期','值班人员','次日备勤一','次日备勤二'])
    for row in settings['overrides']:
        sheet.append([row['date'],*[names.get(i,'') for i in [row['dutyMemberId'],*row['standbyMemberIds']]]])
    # Only explicit human arrangements are configuration; automatic rows regenerate.


def read_roster_configuration(book, data, rows, pairs, yes_no):
    expected={'轮值设置','轮值资质','值班备勤表'}
    if not expected & set(book.sheetnames):return
    if not expected <= set(book.sheetnames) or 'peopleIds' not in data:
        raise ValueError('轮值设置、轮值资质、值班备勤表及人员身份必须完整保留')
    settings={}
    for key,value in rows('轮值设置',['项目','内容']):
        if key in settings:raise ValueError('轮值设置项目重复')
        settings[key]=value
    if set(settings)!={'启用值班备勤','基准工作日'}:raise ValueError('轮值设置项目不完整')
    ids=data['peopleIds'];qualified={'dutyMemberIds':[],'standbyMemberIds':[]};seen=set()
    for name,duty,standby in rows('轮值资质',['人员','值班资质','备勤资质']):
        if name not in ids or name in seen:raise ValueError('轮值资质姓名未知或重复')
        seen.add(name)
        for key,flag in [('dutyMemberIds',duty),('standbyMemberIds',standby)]:
            if yes_no(flag):qualified[key].append(ids[name])
    if seen!=set(ids):raise ValueError('轮值资质必须包含所有配置人员')
    overrides=[]
    for day,duty,one,two in rows('值班备勤表',['工作班日期','值班人员','次日备勤一','次日备勤二']):
        names=[duty,one,two]
        if any(n and n not in ids for n in names):raise ValueError('值班备勤表包含未知姓名')
        overrides.append({'date':str(day),'dutyMemberId':ids.get(duty),'standbyMemberIds':[ids.get(one),ids.get(two)]})
    data['dutyRoster']={'enabled':yes_no(settings['启用值班备勤']),'anchorDate':str(settings['基准工作日']),**qualified,'overrides':overrides}
