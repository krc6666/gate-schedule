import { html, type State, type Plan, type Schedule, type DisplayPreferences } from "./workbench-model";

function brief(schedule: Schedule, memberId: string): string[] {
  const path = schedule.memberPaths[memberId] ?? [];
  return [...new Set(path.map(item => {
    const task = schedule.tasks.find(t => t.id === item.task_id);
    return `${schedule.flights.find(f => f.id === task?.flightId)?.flightNo ?? "共享支援"} ${item.role_id}`;
  }))];
}
export function scheduleExtras(state: State, p: Plan, schedule: Schedule | null): string {
  if (!schedule) return "";
  const previous = Object.values(state.plans).filter(other => other.day.serviceDate < p.day.serviceDate && other.formalSchedule)
    .sort((a,b)=>b.day.serviceDate.localeCompare(a.day.serviceDate) || b.id.localeCompare(a.id))[0];
  const rows = (schedule.memberFlights ?? []).map(member =>
    `<tr><th><button class="issue-jump" data-highlight-person="${html(member.memberId)}">${html(member.memberName)}</button></th><td>${member.count}</td><td>${html(member.flightNos.join("、") || "尚无安排")}</td></tr>`
  );
  return `<div class="sheet-zoom"><label>表格大小 <input id="schedule-zoom" type="range" min="70" max="140" step="10" value="100" /></label><output id="zoom-value">100%</output><button id="reset-zoom">还原</button></div><details id="people-overview" class="wb-card"><summary>人员安排概览（${schedule.members.length}人）</summary><p class="text-muted">同一航班只统计一次；点击姓名定位原班表。</p><div class="wb-scroll"><table class="wb-table"><thead><tr><th>姓名</th><th>航班数</th><th>参与航班</th></tr></thead><tbody>${rows.join("")}</tbody></table></div></details><details id="previous-comparison" class="wb-card"><summary>对照上一份归档${previous ? `（${html(previous.day.serviceDate)}）` : ""}</summary>${previous?.formalSchedule ? `<div class="wb-scroll"><table class="wb-table"><thead><tr><th>姓名</th><th>上一归档安排</th><th>当前安排</th></tr></thead><tbody>${[...new Set([...previous.formalSchedule.members.map(m=>m.name), ...schedule.members.map(m=>m.name)])].map(name=>`<tr><th>${html(name)}</th><td>${html(brief(previous.formalSchedule!,previous.formalSchedule!.members.find(m=>m.name===name)?.id ?? "").join("；") || "无")}</td><td>${html(brief(schedule,schedule.members.find(m=>m.name===name)?.id ?? "").join("；") || "无")}</td></tr>`).join("")}</tbody></table></div>` : '<p class="text-muted">当前日期之前还没有已采用的归档班表。</p>'}</details>`;
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
