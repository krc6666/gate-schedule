import { groupFetch, groupUrl } from "./group-api";
import { html, type PriorityStatistics, type State } from "./workbench-model";

export type PriorityStatisticsSelection = { month: string; roleName: string; flightNo?: string; expandedKeys?: Set<string> };
type Run = (action: string, payload: unknown, planId?: string | null) => Promise<boolean>;

/** Displays the backend ledger. Month queries and corrections never edit history. */
export function priorityStatisticsHtml(data: PriorityStatistics | null, selection: PriorityStatisticsSelection, pending = false, error = ""): string {
  const roles = data?.roles ?? [];
  const flights = [...new Set(roles.flatMap(r => r.flightNo ? [r.flightNo] : []))];
  const flight = flights.includes(selection.flightNo ?? "") ? selection.flightNo! : "";
  const inFlight = flight ? roles.filter(r => r.flightNo === flight) : roles;
  const names = [...new Set(inFlight.map(r => r.roleName))];
  const selected = names.includes(selection.roleName) ? selection.roleName : "";
  const columns = selected ? inFlight.filter(r => r.roleName === selected) : inFlight;
  const members = new Map<string, string>();
  for (const group of columns) for (const member of group.members) members.set(member.memberId, member.memberName);
  const totals = data?.scopedTotals?.find(s => s.flightNo === flight && s.roleName === selected)?.totals
    ?? (!flight && !selected ? data?.totals : []);
  const label = (group: PriorityStatistics["roles"][number]) => group.flightNo ? `${group.flightNo} · ${group.roleName}` : group.roleName;
  const table = columns.length ? `<div class="table-responsive"><table class="data-table priority-statistics-table"><thead><tr><th>人员</th><th>合计</th>${columns.map(group => `<th>${html(label(group))}</th>`).join("")}</tr></thead><tbody>${[...members].map(([id, name]) => `<tr><td data-label="人员"><strong>${html(name)}</strong></td><td data-label="合计"><strong>${totals?.find(total => total.memberId === id)?.count ?? "—"}</strong></td>${columns.map(group => {
    const item = group.members.find(m => m.memberId === id);
    const title = html(label(group));
    if (!item) return `<td data-label="${title}" class="text-muted">—</td>`;
    const key = encodeURIComponent(JSON.stringify([selection.month, id, group.flightNo ?? "", group.roleName]));
    const attributes = `data-member-id="${html(id)}" data-flight-no="${html(group.flightNo ?? "")}" data-role-name="${html(group.roleName)}"`;
    return `<td data-label="${title}"><details id="priority-count-${key}" class="priority-count-detail"><summary title="展开班表日期和航班" aria-label="${html(name)} ${title} 次数，可查看班表日期、航班和修正">${item.effectiveCount}</summary><div class="priority-count-controls"><div class="priority-adjustment-row"><strong>${title}</strong><span class="priority-adjustments"><button type="button" class="btn btn-sm btn-outline-secondary" data-priority-adjust="-1" ${attributes} aria-label="${html(name)} ${title} 减少一次" ${item.effectiveCount <= 0 || pending ? "disabled" : ""}>−</button><output>${item.effectiveCount}</output><button type="button" class="btn btn-sm btn-outline-secondary" data-priority-adjust="1" ${attributes} aria-label="${html(name)} ${title} 增加一次" ${pending ? "disabled" : ""}>+</button></span><small>班表 ${item.historyCount} · 修正 ${item.adjustment >= 0 ? "+" : ""}${item.adjustment}</small></div>${item.dates.length ? `<span class="priority-detail-list">班表记录：${item.dates.map(html).join("、")}</span>` : '<span class="priority-detail-list">本月尚无班表记录</span>'}${item.qualified ? "" : '<span class="priority-detail-list">当前无此岗资质，保留已有次数</span>'}</div></details></td>`;
  }).join("")}</tr>`).join("") || `<tr><td colspan="${columns.length + 2}" class="empty-cell">当前没有符合范围的人员记录。</td></tr>`}</tbody></table></div>` : '<p class="empty-cell">尚未选择重点岗位，请到规则页选择航班并勾选岗位。</p>';
  const legacy = data?.legacyAdjustments ?? [];
  return `<section class="workspace-section priority-statistics" aria-label="重点岗位统计">
    <div class="section-heading">
      <div><h3>重点岗位统计</h3><span>${html(selection.month)} · 只统计已选航班岗位；次数进入重点岗位轮换</span></div>
      <div class="priority-statistics-toolbar">
        <label class="priority-month">统计月份<input id="priority-statistics-month" type="month" aria-label="重点岗位统计月份" value="${html(selection.month)}" list="priority-statistics-months" /><datalist id="priority-statistics-months">${(data?.months ?? []).map(month => `<option value="${html(month)}"></option>`).join("")}</datalist></label>
        <label class="priority-flight-filter">筛选航班<select id="priority-statistics-flight" aria-label="重点岗位统计航班" ${pending ? "disabled" : ""}><option value="">全部航班</option>${flights.map(f => `<option value="${html(f)}" ${flight === f ? "selected" : ""}>${html(f)}</option>`).join("")}</select></label>
        <div id="priority-statistics-role" class="priority-statistics-filter" role="group" aria-label="重点岗位统计岗位">${["", ...names].map(name => `<button type="button" class="btn btn-sm ${selected === name ? "btn-primary" : "btn-outline-secondary"}" data-priority-role="${html(name)}" aria-label="重点岗位统计岗位：${html(name || "全部")}" aria-pressed="${selected === name}" ${pending ? "disabled" : ""}>${html(name || "全部")}</button>`).join("")}</div>
      </div>
    </div>
    ${legacy.length ? `<details id="priority-legacy-adjustments" class="priority-legacy-adjustments"><summary>本月 ${legacy.length} 条旧修正未标明航班，已保留，未计入航班重点次数</summary>${legacy.map(a => `<p>${html(a.memberName)} · ${html(a.roleName)} · ${a.delta >= 0 ? "+" : ""}${a.delta}</p>`).join("")}<p class="section-note">请核对原记录后，通过配置Excel的“重点次数修正”表补填航班号；不确定时保留空白。</p></details>` : ""}
    <p data-priority-statistics-notice role="${error ? "alert" : "status"}" class="${error ? "text-danger" : "text-muted"}" ${error || pending ? "" : "hidden"}>${html(error || (pending ? "正在读取次数…" : ""))}</p>
    ${error ? '<button type="button" id="retry-priority-statistics" class="btn btn-sm btn-outline-secondary">重新读取次数</button>' : pending ? "" : table}
    <details id="priority-statistics-help" class="priority-statistics-help"><summary>计次说明</summary><p>同一航班、同一岗位的分段不重复计次。当前所选已保存草稿替换同日正式记录，其他日期取最新正式。点击次数查看班表日期和修正；−/+只调整这名人员本月这个航班岗位的次数，不改班表或资质。重新排班使用最新次数；完整Excel及备份保留设置和修正。</p></details>
  </section>`;
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
    container.querySelector<HTMLSelectElement>("#priority-statistics-flight")!.onchange = event => {
      selection.flightNo = (event.target as HTMLSelectElement).value; selection.roleName = ""; draw();
    };
    container.querySelectorAll<HTMLButtonElement>("[data-priority-role]").forEach(button => button.onclick = () => {
      selection.roleName = button.dataset.priorityRole ?? ""; draw();
    });
    container.querySelector<HTMLButtonElement>("#retry-priority-statistics")?.addEventListener("click",()=>void load());
    container.querySelectorAll<HTMLButtonElement>("[data-priority-adjust]").forEach(button=>button.onclick=async()=>{
      if (pending || !data || !active()) return;
      pending=true; draw();
      const ok=await run("adjust-priority-count",{month:selection.month,memberId:button.dataset.memberId,...(button.dataset.flightNo ? {flightNo:button.dataset.flightNo} : {}),roleName:button.dataset.roleName,delta:Number(button.dataset.priorityAdjust)},null);
      if (!active()) return;
      pending=false;
      if (ok) void load(); else draw("次数修改未保存，请查看操作提示后重试；原记录保留。");
    });
  };
  const load=async()=>{
    const request=++generation, month=selection.month;
    data=null; pending=true; draw();
    try {
      const response=await groupFetch(`/api/priority-statistics?month=${encodeURIComponent(month)}`,{signal:controller.signal});
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
