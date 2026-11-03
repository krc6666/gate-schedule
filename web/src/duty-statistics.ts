import { html, type State, type DutyRoster, type DutyStatistics } from "./workbench-model";

type Run = (action: string, payload: unknown, planId?: string | null) => Promise<boolean>;
export type DutySelection = { month: string; expanded?: boolean };

export function statisticsPage(state: State, draft: DutyRoster | null, reference: string): string {
  const settings=draft ?? state.dutyRoster ?? {enabled:true,anchorDate:reference,dutyMemberIds:[],standbyMemberIds:[],overrides:[]};
  return `<section class="workspace-section duty-roster-details-section"><div class="section-heading"><div><h3>月度轮值明细</h3><span>工作班值班1人 · 次日休息日备勤2人</span></div><div class="button-row duty-roster-toolbar"><label>轮值月份<input id="duty-month" type="month" aria-label="值班与备勤轮换月份" /></label><button id="download-duty-template" class="btn btn-sm btn-outline-secondary">↓ 下载值班备勤模板</button><label class="btn btn-sm btn-primary">↑ 导入值班备勤表<input id="import-duty-roster" type="file" accept=".xlsx" hidden /></label></div></div><details id="duty-qualification-settings" class="position-rule-group"><summary><strong>值班与备勤设置</strong><span>资格人员、工作班日期</span></summary><div class="rule-group-body"><div class="form-grid"><label><input id="duty-enabled" type="checkbox" ${settings.enabled?'checked':''} />启用值班备勤轮换</label><label>基准工作班日期<input id="duty-anchor" type="date" value="${html(settings.anchorDate)}" /></label></div><p class="section-note">从基准日每隔2天为一个工作班，跨月连续。自动轮值使用正常启用的常规人员；分队长不参加。两种资质可分别勾选。修改后统一保存。</p><div class="duty-qualification-groups">${(['dutyMemberIds','standbyMemberIds'] as const).map((key,index)=>`<fieldset><legend>${index===0?'值班资质人员':'备勤资质人员'}</legend><div class="button-row"><button type="button" class="btn btn-sm btn-outline-secondary" data-duty-select-all="${key}">全选</button><button type="button" class="btn btn-sm btn-outline-secondary" data-duty-clear-all="${key}">清空</button></div><div class="weekday-choices">${state.people.map(p=>`<label><input type="checkbox" data-duty-qualification="${key}" value="${html(p.id)}" ${settings[key].includes(p.id)?'checked':''} />${html(p.name)}${p.teamLeader?'（分队长）':(p.status??'正常')!=='正常'?`（${html(p.status)}）`:!p.active?'（停用）':''}</label>`).join('')}</div></fieldset>`).join('')}</div><div class="button-row"><button id="save-duty-settings" class="btn btn-primary">保存轮值配置</button><span id="duty-save-state" class="save-state">${draft?'有未保存的修改':'已保存'}</span></div></div></details><p id="duty-import-notice" class="text-danger" role="alert" hidden></p><div id="duty-report" aria-live="polite">正在读取月度轮值…</div></section><div id="relaxed-statistics-root"></div><div id="role-statistics-root"></div><div id="priority-statistics-root"></div>`;
}

export function dutyReportHtml(data: DutyStatistics, state: State): string {
  const names=new Map(state.people.map(p=>[p.id,p.name]));
  data.people.forEach(p=>names.set(p.memberId,p.memberName));
  const label=(id:string|null)=>id ? names.get(id)??'原人员（已移出名单）':'未配置';
  const short=(dates:string[])=>dates.map(d=>d.slice(5)).join('、')||'—';
  const settings=state.dutyRoster;
  const duty=data.people.filter(p=>p.dutyQualified&&p.available),standby=data.people.filter(p=>p.standbyQualified&&p.available);
  const missing=duty.filter(p=>p.dutyCount===0), floor=standby.filter(p=>p.standbyCount<2);
  const issues=data.rows.filter(r=>r.issues.length);
  const select=(row:DutyStatistics['rows'][number],slot:string,id:string|null,key:'dutyMemberIds'|'standbyMemberIds')=>`<select data-duty-date="${html(row.date)}" data-duty-slot="${slot}" aria-label="${html(row.date)} ${slot==='duty'?'值班人员':slot==='standby-0'?'次日备勤一':'次日备勤二'}"><option value="">未配置</option>${[...new Set([...(settings?.[key]??[]),...(id?[id]:[])])].map(i=>`<option value="${html(i)}" ${i===id?'selected':''}>${html(label(i))}</option>`).join('')}</select>`;
  return `<details id="duty-general-details" class="duty-roster-details"><summary><span>值班与备勤轮换 <small>${data.month} · ${data.rows.length}个工作班</small></span><i class="chevron" aria-hidden="true">⌄</i></summary><div class="duty-roster-detail-body"><div class="duty-balance-summary"><span>值班席位 <strong>${data.rows.length}</strong></span><span>值班资质 <strong>${duty.length}</strong></span><span>首轮覆盖 <strong>${duty.length-missing.length}/${duty.length}</strong></span><span>值班差值 <strong>${data.dutyDifference}</strong></span><span>备勤差值 <strong>${data.standbyDifference}</strong></span><span>备勤资质 <strong>${standby.length}</strong></span><span>备勤目标 <strong>每人至少2次</strong></span></div><p class="section-note">以下是月度轮值计划次数，不代表已经完成。备勤显示实际次日日期；月底的次日可能属于下月，仍按本月工作班归组。</p>${issues.length?`<p class="duty-warning" role="alert">${issues.length}个工作班存在空缺或需复核，请检查表内提示。</p>`:''}${missing.length?`<p class="duty-warning">首轮待安排：${missing.map(p=>html(p.memberName)).join('、')}。席位不足时在后续月份轮换；人工安排不会被自动覆盖。</p>`:''}${floor.length?`<p class="duty-warning">备勤未满2次：${floor.map(p=>`${html(p.memberName)} ${p.standbyCount}次`).join('、')}。先保障值班与不重复，资质或席位不足时保留缺额。</p>`:''}${data.dutyDifference>1||data.standbyDifference>1?'<p class="duty-warning">当前轮值次数有差距，可检查人工安排或重新均衡本月。</p>':''}<div class="table-responsive"><table class="data-table duty-roster-fairness-table"><thead><tr><th>人员</th><th>值班保障</th><th>本月值班</th><th>本月备勤</th><th>轮值日期</th></tr></thead><tbody>${data.people.map(p=>`<tr><th>${html(p.memberName)}${p.available?'':'<small class="text-danger">当前不可自动轮值</small>'}</th><td>${!p.dutyQualified?'不参与值班':p.dutyCount===0?'<span class="badge">首轮待安排</span>':p.dutyCount===1?'<span class="badge badge-success">首轮已保障</span>':`已进入第${p.dutyCount}轮`}</td><td>${p.dutyCount}</td><td>${p.standbyCount}</td><td><small>值班：${short(p.dutyDates)}<br/>备勤：${short(p.standbyDates)}</small></td></tr>`).join('')||'<tr><td colspan="5">请展开上方设置，勾选值班和备勤资质人员后保存。</td></tr>'}</tbody></table></div><div class="button-row duty-roster-tools"><button id="rebalance-duty-month" class="btn btn-outline-secondary">重新均衡本月</button></div><div class="table-responsive"><table class="data-table duty-roster-table"><thead><tr><th>工作班日期</th><th>值班人员</th><th>次日备勤一</th><th>次日备勤二</th><th>操作与提示</th></tr></thead><tbody>${data.rows.map(r=>`<tr class="${r.issues.length?'duty-row-warning':''}"><th>${r.date.slice(5)}<small>次日 ${r.standbyDate.slice(5)}</small>${r.adjusted?'<span class="badge">已调整</span>':''}</th><td>${select(r,'duty',r.dutyMemberId,'dutyMemberIds')}</td><td>${select(r,'standby-0',r.standbyMemberIds[0],'standbyMemberIds')}</td><td>${select(r,'standby-1',r.standbyMemberIds[1],'standbyMemberIds')}</td><td><button class="btn btn-sm" data-reset-duty="${r.date}" ${r.adjusted?'':'disabled'}>恢复自动</button>${r.issues.map(i=>`<small class="${i.endsWith('待安排')?'duty-vacancy':'text-danger'}">${html(i)}</small>`).join('')}</td></tr>`).join('')}</tbody></table></div><p class="section-note">选择同一排已经安排的人会尝试交换位置，所有位置重新核对资质。修改即保存；恢复自动与重新均衡需确认，航班班表和正式历史保留。</p></div></details>`;
}

export function bindDutyStatistics(root: HTMLElement,state: State,draft: DutyRoster|null,selection: DutySelection,reference: string,run: Run,changed:(value:DutyRoster)=>void,save:()=>Promise<boolean>,canLeave:()=>Promise<boolean>):()=>void {
  const controller=new AbortController(); const container=root.querySelector<HTMLElement>('#duty-report')!;
  const settings=structuredClone(draft??state.dutyRoster??{enabled:true,anchorDate:reference,dutyMemberIds:[],standbyMemberIds:[],overrides:[]});
  let generation=0;const active=()=>!controller.signal.aborted&&container.isConnected;
  selection.month ||= reference.slice(0,7);
  const month=root.querySelector<HTMLInputElement>('#duty-month')!;month.value=selection.month;
  const update=()=>{
    settings.enabled=root.querySelector<HTMLInputElement>('#duty-enabled')!.checked;
    settings.anchorDate=root.querySelector<HTMLInputElement>('#duty-anchor')!.value;
    for(const key of ['dutyMemberIds','standbyMemberIds'] as const)settings[key]=[...root.querySelectorAll<HTMLInputElement>(`[data-duty-qualification="${key}"]:checked`)].map(i=>i.value);
    changed(structuredClone(settings));root.querySelector('#duty-save-state')!.textContent='有未保存的修改';
  };
  root.querySelectorAll<HTMLInputElement>('#duty-enabled,#duty-anchor,[data-duty-qualification]').forEach(i=>i.onchange=update);
  root.querySelectorAll<HTMLButtonElement>('[data-duty-select-all],[data-duty-clear-all]').forEach(button=>button.onclick=()=>{
    const key=button.dataset.dutySelectAll??button.dataset.dutyClearAll;
    root.querySelectorAll<HTMLInputElement>(`[data-duty-qualification="${key}"]`).forEach(input=>{input.checked=button.dataset.dutySelectAll!==undefined;});
    update();
  });
  root.querySelector<HTMLButtonElement>('#save-duty-settings')!.onclick=()=>void save();
  const failure=(error:unknown)=>{if(active())container.innerHTML=`<p role="alert" class="text-danger">${html(error instanceof Error?error.message:String(error))}</p><button id="retry-duty-report" class="btn">重新读取轮值</button>`;container.querySelector<HTMLButtonElement>('#retry-duty-report')?.addEventListener('click',()=>void load());};
  const load=async()=>{
    const request=++generation;const selected=selection.month;container.textContent='正在读取月度轮值…';
    try{
      const response=await fetch(`/api/duty-statistics?month=${encodeURIComponent(selected)}`,{signal:controller.signal});
      if(!response.ok)throw Error('轮值读取失败，请重试；原数据保留。');
      const data:DutyStatistics=await response.json();if(!active()||request!==generation)return;
      if(data.revision!==state.revision||data.month!==selected)throw Error('轮值数据已改变，请重新读取工作台。');
      container.innerHTML=dutyReportHtml(data,state);
      const detail=container.querySelector<HTMLDetailsElement>('#duty-general-details')!;
      detail.open=selection.expanded??false;detail.ontoggle=()=>{selection.expanded=detail.open;};
      container.querySelectorAll<HTMLSelectElement>('[data-duty-slot]').forEach(input=>input.onchange=async()=>{
        const value=input.value;if(!await canLeave())return;
        await run('update-duty-roster',{date:input.dataset.dutyDate,slot:input.dataset.dutySlot,memberId:value},null);
      });
      container.querySelectorAll<HTMLButtonElement>('[data-reset-duty]').forEach(b=>b.onclick=async()=>{if(await canLeave()&&confirm('恢复该日自动轮值？该日的人工安排将清除，其他日期和航班班表保留。'))await run('reset-duty-roster',{date:b.dataset.resetDuty},null);});
      container.querySelector<HTMLButtonElement>('#rebalance-duty-month')!.onclick=async()=>{if(await canLeave()&&confirm(`重新均衡${selected}？只清除本月人工轮值，航班班表和历史保留。`))await run('rebalance-duty-month',{month:selected},null);};

    }catch(error){if(active()&&request===generation)failure(error);}
  };
  month.onchange=()=>{const next=month.value;if(!/^\d{4}-\d{2}$/.test(next))return;selection.month=next;if(active())void load();};
  root.querySelector<HTMLButtonElement>('#download-duty-template')!.onclick=async()=>{if(await canLeave())window.location.href=`/api/workspace/duty-roster/export?month=${encodeURIComponent(selection.month)}`;};
  root.querySelector<HTMLInputElement>('#import-duty-roster')!.onchange=async event=>{
    const file=(event.target as HTMLInputElement).files?.[0];if(!file||!await canLeave())return;
    const reportTarget=root.querySelector<HTMLElement>('#duty-report');
    if(!reportTarget)return;
    const importNotice=root.querySelector<HTMLElement>('#duty-import-notice')!;importNotice.hidden=true;delete importNotice.dataset.dutyImportError;
    (event.target as HTMLInputElement).value='';
    try{
      // Saving pending settings replaces the view and aborts its old controller.
      // This explicit import continues against the newly rendered report.
      const response=await fetch('/api/workspace/duty-roster/preview',{method:'POST',body:file});const preview=await response.json();
      if(!response.ok)throw Error(preview.detail??'轮值表未导入');if(!reportTarget.isConnected)return;
      const dialog=document.createElement('dialog');dialog.className='app-dialog duty-import-preview';
      dialog.innerHTML=`<h3>核对值班备勤表</h3><p>${preview.rows.length}个工作班。确认后按表替换这些日期，空白明确清空；其他日期及航班历史保留。</p><div class="table-responsive"><table class="data-table"><thead><tr><th>工作班</th><th>值班</th><th>次日备勤一</th><th>次日备勤二</th></tr></thead><tbody>${preview.rows.map((r: {date:string;dutyMemberId:string|null;standbyMemberIds:(string|null)[]})=>`<tr><td>${html(r.date)}</td>${[r.dutyMemberId,...r.standbyMemberIds].map(id=>`<td>${html(state.people.find(p=>p.id===id)?.name??'未配置')}</td>`).join('')}</tr>`).join('')}</tbody></table></div><div class="button-row"><button data-confirm-duty-import class="btn btn-primary">确认导入</button><button data-cancel-duty-import class="btn">取消</button></div>`;
      root.append(dialog);dialog.showModal();dialog.oncancel=()=>dialog.remove();dialog.querySelector('[data-cancel-duty-import]')!.addEventListener('click',()=>dialog.remove());
      dialog.querySelector('[data-confirm-duty-import]')!.addEventListener('click',async()=>{dialog.remove();await run('import-duty-roster',preview,null);});
    }catch(error){if(importNotice.isConnected){importNotice.dataset.dutyImportError='';importNotice.hidden=false;importNotice.textContent=error instanceof Error?error.message:String(error);}}
  };
  void load();return()=>{generation++;controller.abort();};
}
