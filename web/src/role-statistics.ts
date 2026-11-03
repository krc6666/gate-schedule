import { html, type RoleStatistics, type State } from "./workbench-model";

export type RoleStatisticsSelection = { month: string; roleName: string; query: string; expandedKeys?: Set<string> };

/** Read-only counts from the shared ledger with the current saved draft. */
export function roleStatisticsHtml(data: RoleStatistics | null, selection: RoleStatisticsSelection, pending = false, error = ""): string {
  const roles = data?.roles ?? [];
  const selected = roles.some(role => role.roleName === selection.roleName) ? selection.roleName : "";
  const columns = selected ? roles.filter(role => role.roleName === selected) : roles;
  const people = (data?.members ?? []).filter(person => person.memberName.toLocaleLowerCase().includes(selection.query.trim().toLocaleLowerCase()));
  const table = columns.length ? `<div class="table-responsive"><table class="data-table priority-statistics-table role-statistics-table"><thead><tr><th>人员</th><th>筛选范围合计</th>${columns.map(role => `<th>${html(role.roleName)}</th>`).join("")}</tr></thead><tbody>${people.map(person => {
    const total = selected ? columns[0].members.find(m => m.memberId === person.memberId)?.count ?? 0 : data!.totals.find(m => m.memberId === person.memberId)?.count ?? 0;
    return `<tr data-role-member="${html(person.memberId)}"><th>${html(person.memberName)}${person.removed ? '<small class="text-muted">已移出名单</small>' : ""}</th><td data-role-total><strong>${total}</strong></td>${columns.map(role => {
      const item = role.members.find(m => m.memberId === person.memberId);
      if (!item) return '<td class="text-muted">—</td>';
      return `<td><details id="role-count-${encodeURIComponent(`${selection.month}/${person.memberId}/${role.roleName}`)}" class="priority-count-detail"><summary title="展开班表记录" aria-label="${html(person.memberName)} ${html(role.roleName)} 实际次数">${item.count}</summary><div class="priority-count-controls"><strong>${html(role.roleName)} · 实际 ${item.count} 次</strong>${item.dates.length ? `<small>班表记录：${item.dates.map(html).join("、")}</small>` : '<small>本月尚无班表记录</small>'}${item.qualified ? "" : '<small>当前无此岗资质，保留历史次数</small>'}</div></details></td>`;
    }).join("")}</tr>`;
  }).join("") || `<tr><td colspan="${columns.length + 2}" class="empty-cell">没有符合姓名筛选的人员。</td></tr>`}</tbody></table></div>` : '<p class="empty-cell">尚无岗位，请先配置岗位；已有正式历史的岗位会自动显示。</p>';
  return `<section class="workspace-section priority-statistics role-statistics" aria-label="所有岗位次数统计"><div class="section-heading"><div><h3>所有岗位次数统计</h3><span>${html(selection.month)} · 班表次数（含当前草稿）</span></div><label class="priority-month">统计月份<input id="role-statistics-month" type="month" aria-label="所有岗位统计月份" value="${html(selection.month)}" list="role-statistics-months" /><datalist id="role-statistics-months">${(data?.months ?? []).map(month => `<option value="${html(month)}"></option>`).join("")}</datalist></label></div><p class="section-note">包含所有岗位，无需勾选重点。当前所选已保存草稿替换同日正式记录，换人、清空、撤销即时更新；其他日期取最新正式班表，不重复计数。红色问题安排仍需核对，未落实到岗位任务的待配置文字不计。同一人、同一航班、同一岗位的分段只计一次，共享保障按覆盖航班分别计次。各岗位相加为岗位次数合计，同航班兼做不同岗位会分别计次。这里显示实际次数，重点岗位人工修正请在下方查看。</p><div class="role-statistics-tools"><label>筛选姓名<input id="role-statistics-name" type="search" aria-label="所有岗位统计姓名筛选" value="${html(selection.query)}" placeholder="输入姓名" /></label><span class="text-muted">${people.length} 人</span></div><div class="button-row priority-filter" role="group" aria-label="所有岗位筛选">${[{roleName: "", label: "全部岗位"}, ...roles.map(role => ({roleName: role.roleName, label: role.roleName}))].map(role => `<button type="button" class="btn btn-sm ${selected === role.roleName ? "btn-primary" : "btn-outline-secondary"}" data-role-filter="${html(role.roleName)}" aria-pressed="${selected === role.roleName}" ${pending ? "disabled" : ""}>${html(role.label)}</button>`).join("")}</div><p data-role-statistics-notice role="${error ? "alert" : "status"}" class="${error ? "text-danger" : "text-muted"}">${html(error || (pending ? "正在读取所有岗位次数…" : "点击次数展开日期和航班；— 表示当前无此岗资质且本月无记录。"))}</p>${error ? '<button type="button" id="retry-role-statistics" class="btn btn-sm btn-outline-secondary">重新读取次数</button>' : pending ? "" : table}</section>`;
}

export function bindRoleStatistics(root: HTMLElement, state: State, selection: RoleStatisticsSelection): () => void {
  const container = root.querySelector<HTMLElement>("#role-statistics-root");
  if (!container) return () => {};
  const controller = new AbortController(), revision = state.revision;
  let generation = 0, data: RoleStatistics | null = null, pending = false;
  if (!selection.month) selection.month = state.priorityStatistics.month;
  selection.expandedKeys ??= new Set();
  const active = () => !controller.signal.aborted && container.isConnected && root.contains(container) && state.revision === revision;
  const remember = () => container.querySelectorAll<HTMLDetailsElement>("details").forEach(detail => {
    if (detail.open) selection.expandedKeys!.add(detail.id); else selection.expandedKeys!.delete(detail.id);
  });
  const draw = (error = "") => {
    if (!active()) return;
    remember(); container.innerHTML = roleStatisticsHtml(data, selection, pending, error);
    container.querySelectorAll<HTMLDetailsElement>("details").forEach(detail => {detail.open = selection.expandedKeys!.has(detail.id); detail.ontoggle = () => {if (detail.isConnected) remember();};});
    container.querySelector<HTMLInputElement>("#role-statistics-month")!.onchange = event => {
      const input = event.target as HTMLInputElement;
      if (!/^\d{4}-\d{2}$/.test(input.value)) {input.value = selection.month; return;}
      selection.month = input.value; void load();
    };
    container.querySelectorAll<HTMLButtonElement>("[data-role-filter]").forEach(button => button.onclick = () => {selection.roleName = button.dataset.roleFilter!; draw();});
    container.querySelector<HTMLInputElement>("#role-statistics-name")!.oninput = event => {
      if ((event as InputEvent).isComposing) return;
      const input = event.target as HTMLInputElement, caret = input.selectionStart;
      selection.query = input.value; draw();
      const next = container.querySelector<HTMLInputElement>("#role-statistics-name")!;
      next.focus(); next.setSelectionRange(caret, caret);
    };
    container.querySelector("#retry-role-statistics")?.addEventListener("click", () => void load());
  };
  const load = async () => {
    const request = ++generation, month = selection.month;
    data = null; pending = true; draw();
    try {
      const response = await fetch(`/api/role-statistics?month=${encodeURIComponent(month)}`, {signal: controller.signal});
      if (!response.ok) throw Error("岗位次数读取失败，请重试；原记录保留。");
      const result: RoleStatistics = await response.json();
      if (!active() || request !== generation || selection.month !== month) return;
      if (result.revision !== revision || result.month !== month) throw Error("数据已改变，请重新读取工作台后查看次数。");
      data = result; pending = false; draw();
    } catch (error) {
      if (!active() || request !== generation) return;
      data = null; pending = false; draw(error instanceof Error ? error.message : String(error));
    }
  };
  void load();
  return () => {remember(); generation++; controller.abort();};
}
