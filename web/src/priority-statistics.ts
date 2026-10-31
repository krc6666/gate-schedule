import { html, type PriorityStatistics, type State } from "./workbench-model";

export type PriorityStatisticsSelection = { month: string; roleName: string; expandedKeys?: Set<string> };
type Run = (action: string, payload: unknown, planId?: string | null) => Promise<boolean>;

/** Displays the backend ledger. Month queries and corrections never edit history. */
export function priorityStatisticsHtml(data: PriorityStatistics | null, selection: PriorityStatisticsSelection, pending = false, error = ""): string {
  const roles = data?.roles ?? [];
  const selected = roles.some(role=>role.roleName===selection.roleName) ? selection.roleName : "";
  const columns = roles;
  const members = new Map<string, string>();
  for (const role of selected ? roles.filter(role=>role.roleName===selected) : roles) for (const member of role.members) {
    if (member.qualified || member.historyCount || member.adjustment || member.effectiveCount) members.set(member.memberId, member.memberName);
  }
  const table = columns.length ? `<div class="table-responsive"><table class="data-table priority-statistics-table"><thead><tr><th>人员</th><th>合计</th>${columns.map(role=>`<th>${html(role.roleName)}</th>`).join("")}</tr></thead><tbody>${[...members].map(([id,name])=>`<tr><th>${html(name)}</th><td><strong>${data!.totals.find(total=>total.memberId===id)?.count ?? "—"}</strong></td>${columns.map(role=>{
    const item=role.members.find(member=>member.memberId===id);
    if (!item || !(item.qualified || item.historyCount || item.adjustment || item.effectiveCount)) return '<td class="text-muted">—</td>';
    const key=encodeURIComponent(`${id}/${role.roleName}`);
    return `<td><details id="priority-count-${key}" class="priority-count-detail"><summary title="展开次数明细" aria-label="${html(name)} ${html(role.roleName)} 次数">${item.effectiveCount}</summary><div class="priority-count-controls"><strong>${html(role.roleName)}</strong><span><button type="button" class="btn btn-sm btn-outline-secondary" data-priority-adjust="-1" data-member-id="${html(id)}" data-role-name="${html(role.roleName)}" aria-label="${html(name)} ${html(role.roleName)} 减少一次" ${item.effectiveCount<=0 || pending ? "disabled" : ""}>−</button><output>${item.effectiveCount}</output><button type="button" class="btn btn-sm btn-outline-secondary" data-priority-adjust="1" data-member-id="${html(id)}" data-role-name="${html(role.roleName)}" aria-label="${html(name)} ${html(role.roleName)} 增加一次" ${pending ? "disabled" : ""}>+</button></span><small>实际 ${item.historyCount} · 修正 ${item.adjustment>=0?"+":""}${item.adjustment}</small>${item.dates.length ? `<small>正式记录：${item.dates.map(html).join("、")}</small>` : '<small>本月尚无正式记录</small>'}${item.qualified ? "" : '<small>当前无此岗资质，保留已有次数</small>'}</div></details></td>`;
  }).join("")}</tr>`).join("") || `<tr><td colspan="${columns.length+2}" class="empty-cell">当前没有符合范围的人员记录。</td></tr>`}</tbody></table></div>` : '<p class="empty-cell">重点岗位集合为空，请先在规则页勾选岗位。</p>';
  return `<section class="workspace-section priority-statistics" aria-label="重点岗位统计"><div class="section-heading"><div><h3>重点岗位统计</h3><span>${html(selection.month)} · 各岗位分别轮换</span></div><label class="priority-month">统计月份<input id="priority-statistics-month" type="month" aria-label="重点岗位统计月份" value="${html(selection.month)}" list="priority-statistics-months" /><datalist id="priority-statistics-months">${(data?.months??[]).map(month=>`<option value="${html(month)}"></option>`).join("")}</datalist></label></div><p class="section-note">只统计已选重点岗位的正式记录；点击数字查看实际次数和修正，−/+只调整这名人员、这个月份和岗位的累计次数，不改原历史或资质。下次重新排班使用最新次数；完整Excel和备份保留修正。</p><div class="button-row priority-filter" role="group" aria-label="重点岗位筛选">${[{roleName:"",label:"全部"},...roles.map(role=>({roleName:role.roleName,label:role.roleName}))].map(role=>`<button type="button" class="btn btn-sm ${selected===role.roleName?"btn-primary":"btn-outline-secondary"}" data-priority-filter="${html(role.roleName)}" aria-pressed="${selected===role.roleName}" ${pending?"disabled":""}>${html(role.label)}</button>`).join("")}</div><p data-priority-statistics-notice role="${error?"alert":"status"}" class="${error?"text-danger":"text-muted"}">${html(error || (pending?"正在读取次数…":"次数按岗位和月份分别计算，同一航班的任务分段不重复计次。"))}</p>${error?'<button type="button" id="retry-priority-statistics" class="btn btn-sm btn-outline-secondary">重新读取次数</button>':pending?"":table}</section>`;
}

export function bindPriorityStatistics(root: HTMLElement, state: State, selection: PriorityStatisticsSelection, run: Run): () => void {
  const container=root.querySelector<HTMLElement>("#priority-statistics-root");
  if (!container) return ()=>{};
  const controller=new AbortController();
  const revision=state.revision;
  let generation=0, data: PriorityStatistics | null=null, pending=false;
  if (!selection.month) selection.month=state.priorityStatistics.month;
  const active=()=>!controller.signal.aborted && container.isConnected && root.contains(container) && state.revision===revision;
  const rememberExpanded=()=>{
    selection.expandedKeys ??= new Set();
    container.querySelectorAll<HTMLDetailsElement>("details").forEach(detail=>{if(detail.open)selection.expandedKeys!.add(detail.id);else selection.expandedKeys!.delete(detail.id);});
  };
  const draw=(error="")=>{
    if (!active()) return;
    rememberExpanded();
    container.innerHTML=priorityStatisticsHtml(data,selection,pending,error);
    container.querySelectorAll<HTMLDetailsElement>("details").forEach(detail=>{detail.open=selection.expandedKeys!.has(detail.id);detail.ontoggle=()=>{if(detail.isConnected)rememberExpanded();};});
    container.querySelector<HTMLInputElement>("#priority-statistics-month")!.onchange=event=>{
      const value=(event.target as HTMLInputElement).value;
      if (!/^\d{4}-\d{2}$/.test(value)) { (event.target as HTMLInputElement).value=selection.month; return; }
      selection.month=value; void load();
    };
    container.querySelectorAll<HTMLButtonElement>("[data-priority-filter]").forEach(button=>button.onclick=()=>{selection.roleName=button.dataset.priorityFilter!;draw();});
    container.querySelector<HTMLButtonElement>("#retry-priority-statistics")?.addEventListener("click",()=>void load());
    container.querySelectorAll<HTMLButtonElement>("[data-priority-adjust]").forEach(button=>button.onclick=async()=>{
      if (pending || !data || !active()) return;
      pending=true; draw();
      const ok=await run("adjust-priority-count",{month:selection.month,memberId:button.dataset.memberId,roleName:button.dataset.roleName,delta:Number(button.dataset.priorityAdjust)},null);
      if (!active()) return;
      pending=false;
      if (ok) void load(); else draw("次数修改未保存，请查看操作提示后重试；原记录保留。");
    });
  };
  const load=async()=>{
    const request=++generation, month=selection.month;
    data=null; pending=true; draw();
    try {
      const response=await fetch(`/api/priority-statistics?month=${encodeURIComponent(month)}`,{signal:controller.signal});
      if (!response.ok) throw Error("次数读取失败，请重试；原记录保留。");
      const result: PriorityStatistics=await response.json();
      if (!active() || request!==generation || selection.month!==month) return;
      if (result.revision!==revision || result.month!==month) throw Error("数据已改变，请重新读取工作台后查看次数。");
      data=result; pending=false; draw();
    } catch (error) {
      if (!active() || request!==generation) return;
      data=null; pending=false; draw(error instanceof Error?error.message:String(error));
    }
  };
  const snapshot=state.priorityStatistics;
  if (snapshot.month===selection.month && snapshot.revision===revision) {data=snapshot;draw();} else void load();
  return ()=>{rememberExpanded();generation++;controller.abort();};
}
