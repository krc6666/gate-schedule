import type { Cell, Day, Flight, State } from "./workbench-model";

export type Preparation = { mode: "archive" | "reschedule"; day: Day; selected: Set<string> };

export const weekdayOf = (date: string) => String(new Date(`${date}T12:00:00Z`).getUTCDay());

export function newCell(flight: Flight, roleName: string): Cell {
  return { id: crypto.randomUUID(), flightId: flight.id, flightNo: flight.flightNo, roleName, rawValue: "", internalNames: [], externalLabels: [], semantics: "empty", workStart: null, workEnd: null, fixedMemberNames: [], requiredCountOverride: null };
}

function addTemplates(day: Day, templates: State["flightTemplates"]): void {
  for (const template of templates) {
    if (day.flights.some(f => f.flightNo === template.flightNo)) continue;
    const flight: Flight = { ...template, id: crypto.randomUUID() };
    day.flights.push(flight);
    day.cells.push(...day.roleNames.map(role => newCell(flight, role)));
  }
}

// 这里只准备可编辑输入；领域归一化和最终校验仍由Python负责。
function applyConfiguration(day: Day, state: State, policy: "keep-day" | "latest"): void {
  for (const person of state.people.filter(p => p.active)) {
    if (!day.roster.includes(person.name)) day.roster.push(person.name);
    let member = day.memberConfigs.find(m => m.memberName === person.name);
    if (!member) { member = { memberName: person.name, eligibleRoleNames: [...person.eligibleRoleNames] }; day.memberConfigs.push(member); }
    else if (policy === "latest") member.eligibleRoleNames = [...person.eligibleRoleNames];
    if (!day.memberAvailability.some(a => a.memberName === person.name)) day.memberAvailability.push({ memberName: person.name, participating: false, availableFrom: null, availableUntil: null });
  }
  for (const entry of day.memberAvailability) if (!state.people.some(p => p.active && (p.status ?? "正常") === "正常" && p.name === entry.memberName)) entry.participating = false;
  const defaults = state.configuration.roleConfigs.map(r => structuredClone(state.newRoleDefaults[r.roleName] ?? r));
  day.roleConfigs = policy === "latest" ? defaults : [...day.roleConfigs, ...defaults.filter(r => !day.roleConfigs.some(existing => existing.roleName === r.roleName))];
  for (const role of day.roleConfigs) {
    if (!day.roleNames.includes(role.roleName)) day.roleNames.push(role.roleName);
    for (const flight of day.flights) if (!day.cells.some(c => c.flightId === flight.id && c.roleName === role.roleName)) {
      const cell = newCell(flight, role.roleName);
      const template = state.flightLibrary.find(f => f.flightNo === flight.flightNo);
      if (template?.roleNames && !template.roleNames.includes(role.roleName)) cell.requiredCountOverride = 0;
      day.cells.push(cell);
    }
  }
  if (policy === "keep-day") return;
  day.transitionMinutes = state.configuration.transitionMinutes;
  for (const flight of day.flights) {
    const template = state.flightLibrary.find(f => f.flightNo === flight.flightNo);
    if (!template) continue;
    flight.windowStart = template.windowStart; flight.windowEnd = template.windowEnd;
    for (const cell of day.cells.filter(c => c.flightId === flight.id)) {
      const role = template.roles?.find(r => r.roleName === cell.roleName);
      const included = !template.roleNames || template.roleNames.includes(cell.roleName);
      cell.requiredCountOverride = included ? role?.requiredCount ?? null : 0;
      cell.workModeOverride = included ? role?.workModeOverride ?? null : null;
      cell.workStart = included ? role?.workStart ?? null : null; cell.workEnd = included ? role?.workEnd ?? null : null;
    }
  }
}

export function startPreparation(source: Day, state: State, mode: Preparation["mode"]): Preparation {
  const day = structuredClone(source);
  if (mode === "archive") {
    const date = new Date(`${day.serviceDate}T12:00:00Z`); date.setUTCDate(date.getUTCDate() + 2);
    day.serviceDate = date.toISOString().slice(0, 10);
    day.memberAvailability = day.memberAvailability.map(a => ({ ...a, availableFrom: null, availableUntil: null, halfRest: null }));
  }
  const selected = new Set(day.flights.map(f => f.id));
  addTemplates(day, state.flightTemplates);
  if (mode === "archive") {
    const preset = state.weeklyFlightPresets[weekdayOf(day.serviceDate)];
    if (preset) {
      addTemplates(day, preset);
      selected.clear();
      day.flights.filter(f => preset.some(t => t.flightNo === f.flightNo)).forEach(f => selected.add(f.id));
    }
  }
  applyConfiguration(day, state, mode === "archive" ? "latest" : "keep-day");
  return { mode, day, selected };
}

export function loadPreparationConfiguration(day: Day, state: State): void {
  applyConfiguration(day, state, "latest");
}

function retainFlights(day: Day, selected: Set<string>): void {
  day.flights = day.flights.filter(f => selected.has(f.id));
  day.cells = day.cells.filter(c => selected.has(c.flightId));
}

function retainHandoffs(day: Day): void {
  if (day.phaseHandoffs) day.phaseHandoffs = day.phaseHandoffs.filter(([a, b]) => day.flights.some(f => f.id === a) && day.flights.some(f => f.id === b));
}

export function applyWeeklyFlights(day: Day, state: State, templates: State["flightTemplates"]): void {
  addTemplates(day, templates);
  const chosen = new Set(templates.map(f => f.flightNo));
  retainFlights(day, new Set(day.flights.filter(f => chosen.has(f.flightNo)).map(f => f.id)));
  // 沿用周计划填入当天时补齐空衔接列表的行为。
  day.phaseHandoffs ??= [];
  retainHandoffs(day);
  loadPreparationConfiguration(day, state);
}

export function copyPreparationAttendance(preparation: Preparation, source: Day): void {
  preparation.day.memberAvailability = structuredClone(source.memberAvailability);
  if (preparation.mode === "archive") preparation.day.memberAvailability.forEach(a => a.halfRest = null);
}

// 保留准备副本，计算失败或取消请求后仍可修改重试。
export function confirmPreparation(preparation: Preparation, serviceDate: string, copyTimes: boolean): Day {
  const day = structuredClone(preparation.day);
  day.serviceDate = serviceDate;
  retainFlights(day, preparation.selected);
  if (!day.serviceDate) throw new Error("请填写排班日期");
  if (preparation.mode !== "archive" && !day.flights.length) throw new Error("请至少选择一个航班");
  if (preparation.mode !== "archive" && !day.memberAvailability.some(a => a.participating)) throw new Error("请选择当天参加人员");
  if (preparation.mode === "archive") {
    day.title = `${day.serviceDate} 保障计划`;
    for (const c of day.cells) {
      c.rawValue = ""; c.internalNames = []; c.externalLabels = []; c.semantics = "empty"; c.fixedMemberNames = [];
      if (!copyTimes) { c.workStart = null; c.workEnd = null; }
    }
    day.extraOccupancies = []; day.remarks = []; day.remarksReviewed = true;
  }
  retainHandoffs(day);
  if (preparation.mode === "archive" && day.phaseHandoffs && !copyTimes) day.phaseHandoffs = [];
  return day;
}
