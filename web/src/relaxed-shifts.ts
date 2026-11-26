import { groupFetch, groupUrl } from "./group-api";
import { html, type State } from './workbench-model';

export type RelaxedSettings = { earlyDepartureCutoffTime: string; afternoonRestStartTime: string; afternoonRestEndTime: string; lateShiftEndTime: string; lateFlightNos: string[]; statisticsFlightNos?: string[]; earlyDepartureBalanceEnabled: boolean; earlyDepartureMaxDifference: number; afternoonRestBalanceEnabled: boolean; afternoonRestMaxDifference: number };
type SummaryPerson = { memberId: string; memberName: string; lastFlightNo: string; lastEndTime: string; monthlyCount: number };
export type RelaxedSummary = { date: string; earlyDepartures: SummaryPerson[]; afternoonRest: SummaryPerson[] };
type Report = { revision: number; month: string; settings: RelaxedSettings; current: RelaxedSummary; flightScope: {candidates: string[]; selected: string[]}; rows: { memberId: string; memberName: string; earlyDepartureDatesByFlight: Record<string,string[]>; morningFreeDates: string[]; afternoonFreeDates: string[]; afternoonOffDates: string[] }[] };

export function relaxedSummaryHtml(summary?: RelaxedSummary | null): string {
  return `<aside id="schedule-relaxed-shifts" class="relaxed-summary"><h3>当日轻松班次</h3><div class="relaxed-summary-grid">${(['earlyDepartures', 'afternoonRest'] as const).map((key, i) => `<section><h4>${i === 0 ? '提前下班人员' : '下午无航班人员'}</h4><div class="relaxed-people">${summary?.[key].map(p => `<span class="relaxed-person"><strong>${html(p.memberName)}</strong>${i === 0 ? `<small>${html(p.lastFlightNo)} / ${html(p.lastEndTime)}</small>` : ''}<small>本月 ${p.monthlyCount} 次</small></span>`).join('') || '<span class="text-muted">今日暂无</span>'}</div></section>`).join('')}</div><p class="section-note">按已保存班表及规则页设置统计；手工调整保存后同步更新。未安排航班任务的人员不计入。</p></aside>`;
}

export function relaxedRulesHtml(state: State): string {
  const settings = state.relaxedShiftSettings;
  const flights = [...new Set([...(state.flightLibrary ?? []).map(f => f.flightNo), ...Object.values(state.plans).flatMap(p => p.day.flights.map(f => f.flightNo)), ...settings.lateFlightNos])].sort();
  const balance = `<h4>按月均衡休息机会</h4><div class="form-grid">${(['earlyDeparture', 'afternoonRest'] as const).map((kind, i) => `<div class="relaxed-balance-option"><label><input type="checkbox" data-relaxed-enabled="${kind}" ${settings[`${kind}BalanceEnabled`] !== false ? 'checked' : ''} />启用${i === 0 ? '提前下班' : '下午无航班'}均衡</label><label>本月次数目标差距<input type="number" min="0" max="31" step="1" data-relaxed-gap="${kind}" value="${Number.isNaN(settings[`${kind}MaxDifference`]) ? '' : settings[`${kind}MaxDifference`] ?? 1}" /></label></div>`).join('')}</div><p class="section-note">默认开启，两项目标差距均为1。保存后重新排班，参考本月此前已归档班表，优先把休息机会分给次数少的人。先保留正常排班的休息机会数量，再尽量缩小次数差距；资格、固定、冲突、半休及保岗优先，人手或求解时限受限时可能超出目标。关闭只停用该项均衡，仍照常统计。</p>`;
  return `<details id="relaxed-shift-rules" class="workspace-section rule-group"><summary><strong>末班航班与轻松班次</strong><span data-rule-summary>末班 ${settings.lateFlightNos.length} 个 · 提前下班 ${html(settings.earlyDepartureCutoffTime)} 前</span><i class="chevron" aria-hidden="true">⌄</i></summary><div class="rule-group-body">${balance}<div class="form-grid">${([['earlyDepartureCutoffTime', '提前下班节点'], ['afternoonRestStartTime', '下午统计开始'], ['afternoonRestEndTime', '下午统计结束'], ['lateShiftEndTime', '晚航班结束界线']] as const).map(([key, title]) => `<label>${title}<input type="time" data-relaxed-time="${key}" value="${html(settings[key])}" /></label>`).join('')}</div><h4>手动设置末班航班</h4><div class="button-row"><button class="btn btn-sm" data-relaxed-all>全选</button><button class="btn btn-sm" data-relaxed-clear>清空</button></div><div class="weekday-choices">${flights.map(n => `<label><input type="checkbox" data-late-flight value="${html(n)}" ${settings.lateFlightNos.includes(n) ? 'checked' : ''} />${html(n)}</label>`).join('') || '<span class="text-muted">请先配置航班。</span>'}</div><p class="section-note">提前下班：本人最后航班任务严格早于节点结束，且不是当天值班人员、未参与勾选的末班航班；参与的任一航班保障结束晚于晚航班界线（默认23:00，等于允许），也不计提前下班。凌晨任务按日表明确的次日时间处理。</p><p class="section-note">下午无航班：本人所有航班任务均不占用下午统计时段，结束恰好等于开始节点、开始恰好等于结束节点均允许；值班和备勤照常计入。仅统计有实际航班任务的正常常规人员，分队长和零任务人员不计。每人每天两项各最多1次；末班按人、日期、航班号去重计次。</p><p class="section-note">修改后统一保存，当前和历史次数按最新设置重算。启用均衡后自动排班也参考次数；设置随完整Excel、JSON和完整备份迁移。排班页直接显示两份名单，统计页可筛选月份和姓名查看明细。</p></div></details>`;
}

export function bindRelaxedRules(root: HTMLElement, state: State, changed: (settings: RelaxedSettings) => void): void {
  const settings = structuredClone(state.relaxedShiftSettings);
  const update = () => {
    root.querySelectorAll<HTMLInputElement>('[data-relaxed-time]').forEach(i => { settings[i.dataset.relaxedTime as 'earlyDepartureCutoffTime' | 'afternoonRestStartTime' | 'afternoonRestEndTime' | 'lateShiftEndTime'] = i.value; });
    root.querySelectorAll<HTMLInputElement>('[data-relaxed-enabled]').forEach(i => { settings[`${i.dataset.relaxedEnabled as 'earlyDeparture' | 'afternoonRest'}BalanceEnabled`] = i.checked; });
    root.querySelectorAll<HTMLInputElement>('[data-relaxed-gap]').forEach(i => { settings[`${i.dataset.relaxedGap as 'earlyDeparture' | 'afternoonRest'}MaxDifference`] = i.value.trim() === '' ? NaN : Number(i.value); });
    settings.lateFlightNos = [...root.querySelectorAll<HTMLInputElement>('[data-late-flight]:checked')].map(i => i.value);
    changed(structuredClone(settings));
    root.querySelector('#relaxed-shift-rules [data-rule-summary]')!.textContent = `末班 ${settings.lateFlightNos.length} 个 · 提前下班 ${settings.earlyDepartureCutoffTime} 前`;
  };
  root.querySelectorAll<HTMLInputElement>('[data-relaxed-time],[data-late-flight],[data-relaxed-enabled],[data-relaxed-gap]').forEach(i => i.onchange = update);
  root.querySelectorAll<HTMLButtonElement>('[data-relaxed-all],[data-relaxed-clear]').forEach(b => b.onclick = () => {
    root.querySelectorAll<HTMLInputElement>('[data-late-flight]').forEach(i => i.checked = b.hasAttribute('data-relaxed-all'));
    update();
  });
}

export function bindRelaxedStatistics(root: HTMLElement, state: State, selection: { month: string; query: string; expanded?: Set<string>; scopeOpen?: boolean }, saveFlights: (numbers: string[]) => Promise<boolean>): () => void {
  const container = root.querySelector<HTMLElement>('#relaxed-statistics-root')!;
  const controller = new AbortController(); let generation = 0;
  const day = state.plans[state.selectedPlanId ?? '']?.day.serviceDate;
  selection.month ||= day?.slice(0, 7) ?? new Date().toISOString().slice(0, 7);
  selection.expanded ??= new Set();
  container.innerHTML = `<section class="workspace-section relaxed-shift-statistics"><div class="section-heading"><div><h3>月度轻松班次统计</h3><span><span id="relaxed-month-label">${html(selection.month)}</span> · 航班列沿用提前下班口径，每人每天最多一次；点击次数查看日期</span></div><div class="late-priority-toolbar"><label>统计月份<input id="relaxed-statistics-month" aria-label="轻松班次统计月份" type="month" value="${html(selection.month)}" /></label></div></div><div id="relaxed-report" aria-live="polite"></div></section>`;
  const report = container.querySelector<HTMLElement>('#relaxed-report')!;
  let data: Report | null = null;
  const active = () => !controller.signal.aborted && container.isConnected;
  const draw = () => {
    if (!data) return;
    const columns = [...data.flightScope.selected, '上午空', '下午空', '下午休'];
    const dateCell = (person: Report['rows'][number], column: string) => {
      const dates = column === '上午空' ? person.morningFreeDates : column === '下午空' ? person.afternoonFreeDates : column === '下午休' ? person.afternoonOffDates : person.earlyDepartureDatesByFlight[column] ?? [];
      const key = JSON.stringify([data!.month, person.memberId, column]);
      return `<td data-label="${html(column)}"><details class="late-priority-count-detail relaxed-shift-count-detail" data-relaxed-cell="${html(key)}" ${selection.expanded!.has(key) ? 'open' : ''}><summary title="展开${html(column)}日期" aria-label="${html(column)}：${dates.length}次，展开日期">${dates.length}</summary><div><small class="late-priority-detail-list">${dates.length ? dates.map(d => html(d.slice(5))).join('、') : '暂无日期'}</small></div></details></td>`;
    };
    const today = data.current.date?.startsWith(data.month + '-') ? `<div class="relaxed-shift-today"><div><strong>今日提前下班</strong><small>任一航班保障结束晚于 ${html(data.settings.lateShiftEndTime)} 不计；否则最后航班任务严格早于 ${html(data.settings.earlyDepartureCutoffTime)}，排除当日值班及所选末班航班</small><div>${data.current.earlyDepartures.map(p => `<span><strong>${html(p.memberName)}</strong> ${html(p.lastFlightNo)} / ${html(p.lastEndTime)} <em>本月 ${p.monthlyCount} 次</em></span>`).join('') || '<span class="is-empty">今日没有符合节点的提前下班人员</span>'}</div></div><div><strong>今日下午无航班</strong><small>${html(data.settings.afternoonRestStartTime)}-${html(data.settings.afternoonRestEndTime)} 个人无已排岗位重叠；值班和备勤照常计入</small><div>${data.current.afternoonRest.map(p => `<span><strong>${html(p.memberName)}</strong> <em>本月 ${p.monthlyCount} 次</em></span>`).join('') || '<span class="is-empty">今日没有下午无航班人员</span>'}</div></div></div>` : '';
    report.innerHTML = `<details class="relaxed-shift-flight-scope" ${selection.scopeOpen ? 'open' : ''}><summary>调整统计航班（已选 ${data.flightScope.selected.length} 个）</summary><div class="relaxed-flight-choices" role="group" aria-label="轻松班次统计航班"><button type="button" class="btn btn-sm btn-outline-secondary" data-relaxed-scope-all>全选</button><button type="button" class="btn btn-sm btn-outline-secondary" data-relaxed-scope-clear>清空</button>${data.flightScope.candidates.map(n => `<label><input type="checkbox" data-relaxed-flight="${html(n)}" aria-label="轻松班次统计航班：${html(n)}" ${data!.flightScope.selected.includes(n) ? 'checked' : ''} />${html(n)}</label>`).join('')}</div><p id="relaxed-scope-error" role="alert"></p></details><p class="section-note">上午空：12:00前无航班任务，当天其他时段有任务；下午空：${html(data.settings.afternoonRestStartTime)}—${html(data.settings.afternoonRestEndTime)} 无岗位重叠，之后仍有航班；下午休：没有开始时间晚于12:00的任务。仅统计当天实际参加排班的常规人员。</p>${today}<div class="table-responsive"><table class="data-table relaxed-shift-table"><thead><tr><th>人员</th>${columns.map(c => `<th>${html(c)}</th>`).join('')}</tr></thead><tbody>${data.rows.map(p => `<tr><td data-label="人员"><strong>${html(p.memberName)}</strong></td>${columns.map(c => dateCell(p,c)).join('')}</tr>`).join('') || `<tr><td colspan="${columns.length + 1}">暂无人员</td></tr>`}</tbody></table></div>`;
    const scope = report.querySelector<HTMLDetailsElement>('.relaxed-shift-flight-scope')!;
    scope.ontoggle = () => { selection.scopeOpen = scope.open; };
    report.querySelectorAll<HTMLDetailsElement>('[data-relaxed-cell]').forEach(cell => { cell.ontoggle = () => { const key = cell.dataset.relaxedCell!; if (cell.open) selection.expanded!.add(key); else selection.expanded!.delete(key); }; });
    const save = async (numbers: string[]) => {
      report.querySelectorAll<HTMLInputElement | HTMLButtonElement>('.relaxed-flight-choices input,.relaxed-flight-choices button').forEach(el => { el.disabled = true; });
      try { if (!await saveFlights(numbers) && active()) { draw(); report.querySelector('#relaxed-scope-error')!.textContent = '统计航班未保存，请重试；原选择保持。'; } }
      catch { if (active()) { draw(); report.querySelector('#relaxed-scope-error')!.textContent = '统计航班保存失败，请重试；原选择保持。'; } }
    };
    report.querySelector('[data-relaxed-scope-all]')!.addEventListener('click', () => void save(data!.flightScope.candidates));
    report.querySelector('[data-relaxed-scope-clear]')!.addEventListener('click', () => void save([]));
    report.querySelectorAll<HTMLInputElement>('[data-relaxed-flight]').forEach(input => { input.onchange = () => void save(input.checked ? [...data!.flightScope.selected,input.dataset.relaxedFlight!] : data!.flightScope.selected.filter(n => n !== input.dataset.relaxedFlight)); });
  };
  const load = async () => {
    const request = ++generation, month = selection.month; data = null; report.textContent = '正在读取轻松班次统计…';
    try {
      const response = await groupFetch(`/api/relaxed-shift-statistics?month=${encodeURIComponent(month)}`, { signal: controller.signal });
      if (!response.ok) throw Error('轻松班次读取失败，请重试。');
      const result: Report = await response.json();
      if (!active() || request !== generation) return;
      if (result.revision !== state.revision || result.month !== month) throw Error('数据已改变，请重新读取工作台。');
      data = result; draw();
    } catch (error) {
      if (!active() || request !== generation) return;
      report.innerHTML = `<p class="text-danger" role="alert">${html(error instanceof Error ? error.message : String(error))}</p><button class="btn" id="retry-relaxed-report">重试</button>`;
      report.querySelector('#retry-relaxed-report')!.addEventListener('click', () => void load());
    }
  };
  const month = container.querySelector<HTMLInputElement>('#relaxed-statistics-month')!;
  month.onchange = () => { if (/^\d{4}-(0[1-9]|1[0-2])$/.test(month.value)) { selection.month = month.value; selection.expanded!.clear(); container.querySelector('#relaxed-month-label')!.textContent = month.value; void load(); } else month.value = selection.month; };
  void load(); return () => { generation++; controller.abort(); };
}
