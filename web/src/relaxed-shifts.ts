import { groupFetch, groupUrl } from "./group-api";
import { html, type State } from './workbench-model';

export type RelaxedSettings = { earlyDepartureCutoffTime: string; afternoonRestStartTime: string; afternoonRestEndTime: string; lateShiftEndTime: string; lateFlightNos: string[] };
type SummaryPerson = { memberId: string; memberName: string; lastFlightNo: string; lastEndTime: string; monthlyCount: number };
export type RelaxedSummary = { date: string; earlyDepartures: SummaryPerson[]; afternoonRest: SummaryPerson[] };
type Report = { revision: number; month: string; settings: RelaxedSettings; current: RelaxedSummary; rows: { memberId: string; memberName: string; earlyDepartureCount: number; afternoonRestCount: number; lateFlightCount: number; earlyDepartures: { date: string; flightNo: string; endTime: string }[]; afternoonRestDates: string[]; lateFlights: { date: string; flightNo: string }[] }[] };

export function relaxedSummaryHtml(summary?: RelaxedSummary | null): string {
  return `<aside id="schedule-relaxed-shifts" class="relaxed-summary"><h3>当日轻松班次</h3><div class="relaxed-summary-grid">${(['earlyDepartures', 'afternoonRest'] as const).map((key, i) => `<section><h4>${i === 0 ? '提前下班人员' : '下午无航班人员'}</h4><div class="relaxed-people">${summary?.[key].map(p => `<span class="relaxed-person"><strong>${html(p.memberName)}</strong>${i === 0 ? `<small>${html(p.lastFlightNo)} / ${html(p.lastEndTime)}</small>` : ''}<small>本月 ${p.monthlyCount} 次</small></span>`).join('') || '<span class="text-muted">今日暂无</span>'}</div></section>`).join('')}</div><p class="section-note">按已保存班表及规则页设置统计；手工调整保存后同步更新。未安排航班任务的人员不计入。</p></aside>`;
}

export function relaxedRulesHtml(state: State): string {
  const settings = state.relaxedShiftSettings;
  const flights = [...new Set([...(state.flightLibrary ?? []).map(f => f.flightNo), ...Object.values(state.plans).flatMap(p => p.day.flights.map(f => f.flightNo)), ...settings.lateFlightNos])].sort();
  return `<details id="relaxed-shift-rules" class="workspace-section rule-group"><summary><strong>末班航班与轻松班次</strong><span data-rule-summary>末班 ${settings.lateFlightNos.length} 个 · 提前下班 ${html(settings.earlyDepartureCutoffTime)} 前</span><i class="chevron" aria-hidden="true">⌄</i></summary><div class="rule-group-body"><div class="form-grid">${([['earlyDepartureCutoffTime', '提前下班节点'], ['afternoonRestStartTime', '下午统计开始'], ['afternoonRestEndTime', '下午统计结束'], ['lateShiftEndTime', '晚航班结束界线']] as const).map(([key, title]) => `<label>${title}<input type="time" data-relaxed-time="${key}" value="${html(settings[key])}" /></label>`).join('')}</div><h4>手动设置末班航班</h4><div class="button-row"><button class="btn btn-sm" data-relaxed-all>全选</button><button class="btn btn-sm" data-relaxed-clear>清空</button></div><div class="weekday-choices">${flights.map(n => `<label><input type="checkbox" data-late-flight value="${html(n)}" ${settings.lateFlightNos.includes(n) ? 'checked' : ''} />${html(n)}</label>`).join('') || '<span class="text-muted">请先配置航班。</span>'}</div><p class="section-note">提前下班：本人最后航班任务严格早于节点结束，且不是当天值班人员、未参与勾选的末班航班；参与的任一航班保障结束晚于晚航班界线（默认23:00，等于允许），也不计提前下班。凌晨任务按日表明确的次日时间处理。</p><p class="section-note">下午无航班：本人所有航班任务均不占用下午统计时段，结束恰好等于开始节点、开始恰好等于结束节点均允许；值班和备勤照常计入。仅统计有实际航班任务的正常常规人员，分队长和零任务人员不计。每人每天两项各最多1次；末班按人、日期、航班号去重计次。</p><p class="section-note">修改后统一保存，当前和历史次数按最新设置重算。这里只用于名单识别与次数统计；设置随完整Excel、JSON和完整备份迁移。排班页直接显示两份名单，统计页可筛选月份和姓名查看明细。</p></div></details>`;
}

export function bindRelaxedRules(root: HTMLElement, state: State, changed: (settings: RelaxedSettings) => void): void {
  const settings = structuredClone(state.relaxedShiftSettings);
  const update = () => {
    root.querySelectorAll<HTMLInputElement>('[data-relaxed-time]').forEach(i => { settings[i.dataset.relaxedTime as Exclude<keyof RelaxedSettings, 'lateFlightNos'>] = i.value; });
    settings.lateFlightNos = [...root.querySelectorAll<HTMLInputElement>('[data-late-flight]:checked')].map(i => i.value);
    changed(structuredClone(settings));
    root.querySelector('#relaxed-shift-rules [data-rule-summary]')!.textContent = `末班 ${settings.lateFlightNos.length} 个 · 提前下班 ${settings.earlyDepartureCutoffTime} 前`;
  };
  root.querySelectorAll<HTMLInputElement>('[data-relaxed-time],[data-late-flight]').forEach(i => i.onchange = update);
  root.querySelectorAll<HTMLButtonElement>('[data-relaxed-all],[data-relaxed-clear]').forEach(b => b.onclick = () => {
    root.querySelectorAll<HTMLInputElement>('[data-late-flight]').forEach(i => i.checked = b.hasAttribute('data-relaxed-all'));
    update();
  });
}

export function bindRelaxedStatistics(root: HTMLElement, state: State, selection: { month: string; query: string }): () => void {
  const container = root.querySelector<HTMLElement>('#relaxed-statistics-root')!;
  const controller = new AbortController(); let generation = 0;
  const day = state.plans[state.selectedPlanId ?? '']?.day.serviceDate;
  selection.month ||= day?.slice(0, 7) ?? new Date().toISOString().slice(0, 7);
  container.innerHTML = `<section class="workspace-section"><div class="section-heading"><h3>月度轻松班次统计</h3><div class="button-row"><label>统计月份<input id="relaxed-statistics-month" type="month" value="${html(selection.month)}" /></label><label>人员姓名<input id="relaxed-statistics-name" type="search" value="${html(selection.query)}" placeholder="筛选姓名" /></label></div></div><p class="section-note">本月正式记录加当前所选已保存草稿，同日替换不重复；手工换人、清空、撤销后同步计数。按规则页最新时段及末班范围重算，不代表实际完成工作。</p><div id="relaxed-report" aria-live="polite"></div></section>`;
  const report = container.querySelector<HTMLElement>('#relaxed-report')!;
  let data: Report | null = null;
  const active = () => !controller.signal.aborted && container.isConnected;
  const draw = () => {
    if (!data) return;
    const rows = data.rows.filter(p => p.memberName.includes(selection.query.trim()));
    report.innerHTML = `${data.current.date?.startsWith(data.month + '-') ? `<p class="section-note">当前班表 ${html(data.current.date)}：提前下班 ${data.current.earlyDepartures.length} 人，下午无航班 ${data.current.afternoonRest.length} 人。</p>` : ''}<div class="table-responsive"><table class="data-table relaxed-statistics-table"><thead><tr><th>常规人员</th><th>提前下班次数</th><th>日期 / 最后航班 / 结束</th><th>下午无航班次数</th><th>下午无航班日期</th><th>末班航班次数</th><th>末班日期 / 航班</th></tr></thead><tbody>${rows.map(p => `<tr><th>${html(p.memberName)}</th><td>${p.earlyDepartureCount}</td><td>${p.earlyDepartures.map(e => `${html(e.date.slice(5))} / ${html(e.flightNo)} / ${html(e.endTime)}`).join('<br/>') || '—'}</td><td>${p.afternoonRestCount}</td><td>${p.afternoonRestDates.map(d => html(d.slice(5))).join('、') || '—'}</td><td>${p.lateFlightCount}</td><td>${p.lateFlights.map(e => `${html(e.date.slice(5))} / ${html(e.flightNo)}`).join('<br/>') || '—'}</td></tr>`).join('') || '<tr><td colspan="7">没有符合筛选的人员</td></tr>'}</tbody></table></div>`;
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
  month.onchange = () => { if (/^\d{4}-\d{2}$/.test(month.value)) { selection.month = month.value; void load(); } else month.value = selection.month; };
  container.querySelector<HTMLInputElement>('#relaxed-statistics-name')!.oninput = event => { selection.query = (event.target as HTMLInputElement).value; draw(); };
  void load(); return () => { generation++; controller.abort(); };
}
