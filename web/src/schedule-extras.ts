import { html, type State, type Plan, type Schedule, type DisplayPreferences } from "./workbench-model";
import { flightHeader } from "./schedule-layout";

function previousBoard(previous: Plan): string {
  const schedule = previous.formalSchedule!;
  const day = previous.formalDay;
  const columns = [...schedule.flights.map(f => f.flightNo), ...(Object.values(schedule.matrix).some(cells => cells["支援"]?.length) ? ["支援"] : [])];
  const roles = [...new Set([...(day?.roleNames ?? []), ...Object.keys(schedule.matrix)])];
  if (!columns.length || !roles.length) return '<p class="text-muted previous-empty">上一班归档尚无航班岗位记录。</p>';
  const rows = roles.map(role => `<tr>${columns.map(column => {
    const segments = schedule.matrix[role]?.[column] ?? [];
    const cell = day?.cells.find(c => c.roleName === role && c.flightNo === column);
    const pending = cell && previous.formalCellDrafts?.[cell.id];
    const groups = segments.map(segment => {
      const task = schedule.tasks.find(t => t.id === segment.task_id);
      return [...segment.members, ...Array(Math.max(0, (task?.requiredCount ?? 0) - segment.members.length)).fill("待安排")].join(" / ");
    }).filter((names, index, all) => !index || names !== all[index - 1]);
    const text = pending ? `${pending}（待完善）` : groups.join(" / ") || (cell?.rawValue ? `${cell.rawValue}（待配置）` : cell && (cell.requiredCountOverride ?? day?.roleConfigs.find(r => r.roleName === role)?.requiredCount) !== 0 ? "待配置" : "");
    const issues = (previous.formalIssues ?? []).filter(issue => (!!cell && issue.taskId === cell.id) || segments.some(s => s.task_id === issue.taskId || issue.relatedTaskIds?.includes(s.task_id)));
    const conflict = issues.some(issue => issue.code !== "coverage_shortfall");
    const vacancy = issues.some(issue => issue.code === "coverage_shortfall");
    return `<th scope="row" class="previous-role">${html(role)}</th><td class="previous-person${conflict ? " previous-cell-conflict" : vacancy ? " previous-cell-vacancy" : ""}" title="${html(issues.map(i => i.message).join("；"))}">${html(text)}${issues.length ? '<span class="previous-warning" aria-label="归档问题"> !</span>' : ""}</td>`;
  }).join("")}</tr>`).join("");
  return `<div class="wb-scroll previous-board" tabindex="0" role="region" aria-label="上一班完整班表，可横向滚动"><table class="previous-schedule-grid" style="min-width:${Math.max(260, columns.length * 220)}px"><caption class="visually-hidden">${html(day?.serviceDate ?? previous.day.serviceDate)} 上一班班表</caption><colgroup>${columns.map(() => '<col class="previous-role-column"><col class="previous-person-column">').join("")}</colgroup><thead><tr>${columns.map(column => `<th scope="colgroup" colspan="2">${flightHeader(column, schedule.flights)}</th>`).join("")}</tr><tr>${columns.map(() => '<th scope="col">岗位</th><th scope="col">人员</th>').join("")}</tr></thead><tbody>${rows}</tbody></table></div>`;
}

function previousComparison(previous: Plan | undefined): string {
  return `<section id="previous-comparison" class="wb-card previous-schedule-comparison" aria-label="上一班班表对照"><div class="previous-comparison-heading"><div><h3 class="schedule-panel-heading">上一班班表</h3>${previous ? `<span>${html(previous.formalDay?.serviceDate ?? previous.day.serviceDate)} · ${previous.formalSchedule!.flights.length} 个航班</span>` : ""}</div>${previous ? `<span>${previous.formalIssues?.length ? "带问题归档 · " : ""}只读对照</span>` : ""}</div>${previous ? previousBoard(previous) : '<p class="text-muted previous-empty">当前日期之前还没有已采用的归档班表。</p>'}</section>`;
}
export function scheduleExtras(state: State, p: Plan, schedule: Schedule | null): string {
  if (!schedule) return "";
  const previous = Object.values(state.plans).filter(other => other.day.serviceDate < p.day.serviceDate && other.formalSchedule)
    .sort((a,b)=>b.day.serviceDate.localeCompare(a.day.serviceDate) || b.id.localeCompare(a.id))[0];
  const rows = (schedule.memberFlights ?? []).map(member =>
    `<tr><th><button class="issue-jump" data-highlight-person="${html(member.memberId)}">${html(member.memberName)}</button></th><td>${member.count}</td><td>${html(member.flightNos.join("、") || "尚无安排")}</td></tr>`
  );
  return `<div class="sheet-zoom"><label>表格大小 <input id="schedule-zoom" type="range" min="70" max="140" step="10" value="100" /></label><output id="zoom-value">100%</output><button id="reset-zoom">还原</button></div><section id="people-overview" class="wb-card"><h3 class="schedule-panel-heading">人员安排概览（${schedule.members.length}人）</h3><p class="text-muted">同一航班只统计一次；点击姓名定位原班表。</p><div class="wb-scroll"><table class="wb-table"><thead><tr><th>姓名</th><th>航班数</th><th>参与航班</th></tr></thead><tbody>${rows.join("")}</tbody></table></div></section>${previousComparison(previous)}`;
}
export function bindScheduleExtras(root: HTMLElement, preferences: DisplayPreferences, save: (preferences: DisplayPreferences) => void): void {
  const zoomControl = root.querySelector(".sheet-zoom");
  const toolbar = root.querySelector(".toolbar-meta");
  if (zoomControl && toolbar) toolbar.append(zoomControl);
  const slider = root.querySelector<HTMLInputElement>("#schedule-zoom");
  if (!slider) return;
  const apply = (value: number) => {
    const zoom = Math.min(140, Math.max(70, Number.isFinite(value) ? value : 100));
    slider.value = String(zoom); root.querySelector("#zoom-value")!.textContent = `${zoom}%`;
    const table = root.querySelector<HTMLElement>("[data-zoom-sheet] table");
    if (table) { table.style.setProperty("font-size", `${zoom}%`); table.style.setProperty("zoom", String(zoom / 100)); }
  };
  apply(preferences.zoom); slider.oninput = () => apply(Number(slider.value));
  slider.onchange = () => save({ ...preferences, zoom: Number(slider.value) });
  root.querySelector<HTMLButtonElement>("#reset-zoom")!.onclick = () => { apply(100); save({ ...preferences, zoom: 100 }); };
  root.querySelectorAll<HTMLButtonElement>("[data-highlight-person]").forEach(b=>b.onclick=()=>{
    root.querySelectorAll(".person-focused").forEach(el=>el.classList.remove("person-focused"));
    const cells=[...root.querySelectorAll<HTMLElement>(".wb-matrix [data-drag-member]")].filter(el=>el.dataset.dragMember===b.dataset.highlightPerson).map(el=>el.closest("td")!);
    cells.forEach(c=>c.classList.add("person-focused")); cells[0]?.scrollIntoView({block:"center",inline:"center",behavior:"smooth"});
  });
}
