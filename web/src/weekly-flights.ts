import { html, type State, type Day } from "./workbench-model";
import { weekdayOf } from "./day-preparation";

export const weekdays = ["星期日", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六"];
export function weeklyFlightsHtml(state: State, day: Day | null, date: string): string {
  const weekday = weekdayOf(date), current = state.weeklyFlightPresets?.[weekday];
  return `<details id="weekly-flights" class="workspace-section weekly-presets" open><summary>航班计划</summary><div class="weekday-tabs" role="tablist" aria-label="每周航班">${weekdays.map((name,i)=>`<button role="tab" data-weekday="${i}" aria-selected="${String(i)===weekday}">${name}</button>`).join("")}</div><select id="weekly-day" aria-label="维护星期" hidden>${weekdays.map((name,i)=>`<option value="${i}" ${String(i)===weekday ? "selected" : ""}>${name}</option>`).join("")}</select><div id="weekly-flight-choices"></div><div class="button-row"><button id="save-weekly-flights" class="btn btn-primary">保存航班选择</button><button id="apply-weekly-flights" class="btn btn-outline-primary">${day ? "用于当天排班" : "创建当天排班"}</button><button id="remove-weekly-flights" class="btn btn-outline-secondary">取消此星期预设</button></div><p id="weekly-note" class="section-note">${current ? `${weekdays[Number(weekday)]}已设置${current.length}班` : "勾选需要保障的航班"}。按星期保存常用选择，每次排班仍可手动调整。</p></details>`;
}
export function bindWeeklyFlights(root: HTMLElement, state: State, day: Day | null, date: string,
  save: (weekday: string, flights: State["flightTemplates"], remove?: boolean)=>Promise<boolean>,
  apply: (flights: State["flightTemplates"])=>void): void {
  const select=root.querySelector<HTMLSelectElement>("#weekly-day"); if (!select) return;
  const catalog=new Map(state.flightTemplates.map(f=>[f.flightNo,f]));
  for (const f of day?.flights ?? []) catalog.set(f.flightNo,{flightNo:f.flightNo,windowStart:f.windowStart,windowEnd:f.windowEnd});
  const renderChoices=()=>{
    const chosen=state.weeklyFlightPresets?.[select.value] ?? (select.value===weekdayOf(date) ? day?.flights ?? [] : []);
    root.querySelectorAll<HTMLButtonElement>("[data-weekday]").forEach(b=>b.setAttribute("aria-selected",String(b.dataset.weekday===select.value)));
    root.querySelector("#weekly-flight-choices")!.innerHTML=`<div class="weekday-choices flight-cards">${[...catalog.values()].map(f=>`<label class="flight-card"><input type="checkbox" data-weekly-flight="${html(f.flightNo)}" ${chosen.some(t=>t.flightNo===f.flightNo) ? "checked" : ""} /><span><strong>${html(f.flightNo)}</strong><small>${html(f.windowStart)}—${html(f.windowEnd)}</small></span></label>`).join("") || "先在下方新增航班模板，或导入完整配置"}</div>`;
  };
  select.onchange=renderChoices; renderChoices();
  root.querySelectorAll<HTMLButtonElement>("[data-weekday]").forEach(b=>b.onclick=()=>{select.value=b.dataset.weekday!;renderChoices();});
  root.querySelector<HTMLButtonElement>("#save-weekly-flights")!.onclick=()=>{
    const flights=[...root.querySelectorAll<HTMLInputElement>("[data-weekly-flight]:checked")].map(i=>catalog.get(i.dataset.weeklyFlight!)!);
    void save(select.value,flights);
  };
  root.querySelector<HTMLButtonElement>("#remove-weekly-flights")!.onclick=()=>void save(select.value,[],true);
  root.querySelector<HTMLButtonElement>("#apply-weekly-flights")!.onclick=()=>{
    const selected=[...root.querySelectorAll<HTMLInputElement>("[data-weekly-flight]:checked")].map(i=>catalog.get(i.dataset.weeklyFlight!)!);
    if (!selected.length) { root.querySelector("#weekly-note")!.textContent="请至少选择一个航班"; return; }
    if (!day || confirm("用所选航班替换当天选择？保存前可继续调整，原正式班表保留。")) apply(selected);
  };
}
