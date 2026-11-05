import { groupFetch, selectApiGroup } from "./group-api";
import { relaxedSummaryHtml, bindRelaxedRules, bindRelaxedStatistics } from "./relaxed-shifts";
import { WorkbenchEdits } from "./workbench-edits";
import { halfRestSelector, bindHalfRest, syncHalfRest, halfRestLabel } from "./half-rest";
import { statisticsPage, bindDutyStatistics } from "./duty-statistics";
import { bindRulesSearch } from "./rules-view";
import "./workbench.css";
import { runSchedule } from "./schedule-run";
import { flightGrid, flightHeader, shareSheet, sharedBaggageGroups } from "./schedule-layout";
import { bindTemplateConfig, templateForm, flightLibraryHtml } from "./template-config";
import { bindHandoffPreferences } from "./handoff-preferences";
import { weeklyFlightsHtml, bindWeeklyFlights } from "./weekly-flights";
import { startPreparation, loadPreparationConfiguration, applyWeeklyFlights, copyPreparationAttendance, confirmPreparation, weekdayOf, newCell, type Preparation } from "./day-preparation";
import { ScheduleEditor } from "./schedule-editor";
import { ScheduleDrag } from "./schedule-drag";
import { paletteHtml, bindPalette } from "./schedule-palette";
import { scheduleExtras, bindScheduleExtras } from "./schedule-extras";
import { bindPriorityStatistics } from "./priority-statistics";
import { bindRoleStatistics } from "./role-statistics";
import { html, type State, type Day, type Plan, type Flight, type Candidate, type BalanceSettings, type QualificationImport, type CompleteConfigurationImport } from "./workbench-model";
import { configPage, flightsPage, rulesPage, historyPage, modal } from "./workbench-pages";

type Section = "config" | "flights" | "schedule" | "rules" | "statistics" | "history";
type DragSource = { memberId: string; taskIds: string[]; planId: string; revision: number };
type Dialog = ({ kind: "prepare" } & Preparation | { kind: "template"; flightNo?: string } | { kind: "copy-template"; sourceNo: string } | { kind: "move"; source: DragSource; targetIds: string[]; targetMember?: string } | { kind: "cell-settings"; cellId: string } | { kind: "assignment"; role: string; column: string; taskIds?: string[] } | { kind: "complete-configuration"; preview: CompleteConfigurationImport } | { kind: "person"; personId: string | null } | { kind: "role" } | { kind: "flight" } | { kind: "qualification-import"; preview: QualificationImport } | { kind: "qualification"; role: string; scope: "base" | "day"; selected: Set<string> }) & { fields?: Record<string, string | boolean> };
const root = document.querySelector<HTMLDivElement>("#app")!;
let state: State;
let activeGroup: "A" | "B" = "A";
let section: Section = "config";
let busy = false;
let message = "";
let dialogError = "";
const relaxedSelection = { month: "", query: "" };
let disposeRelaxedStatistics = () => {};
const dutySelection = { month: "" };
let disposeDutyStatistics = () => {};
let rulesQuery = "";
const edits = new WorkbenchEdits({
  context: () => ({ state, plan: plan() }),
  execute: run,
  validate: validateRuleInputs,
  confirmDayChange: () => confirm("修改当天资料会清除当前草稿分配与预览，已采用结果保留。确定保存？"),
  saveCells: flushCells,
  changed: () => { dialogError = ""; render(); },
});
const hasUnsaved = () => edits.hasChanges;
function updateSaveStatus(): void {
  const text = hasUnsaved() ? "有未保存的修改" : "已保存";
  root.querySelectorAll(".save-state, #rules-save-status").forEach(el => el.textContent = text);
}

const priorityStatisticsSelection = { month: "", roleName: "", flightNo: "" };
let disposePriorityStatistics = () => {};
const roleStatisticsSelection = { month: "", roleName: "", query: "" };
let disposeRoleStatistics = () => {};
let previousSection: Section | null = null;
let dialog: Dialog | null = null;
let selectedPaletteTarget = "";
let disposePalette = () => {};
let viewingFormal = false;
let scheduleLayout: "flights" | "matrix" = "flights";
let dragSource: DragSource | null = null;
let placingPerson: DragSource | null = null;
let warningMessage = false;
let editToast = false;
let editSaved = false;
const manualActions = new Set(["assign", "edit-cell", "edit-slot", "configure-cell", "move-assignment", "clear-draft", "undo-edit"]);
const editor = new ScheduleEditor(root, {
  context: () => ({ state, plan: plan()!, disabled: busy || edits.dayDirty || viewingFormal || !!dialog }),
  changed: (id, text) => { edits.cells.set(id, text); root.querySelector(".save-state")!.textContent = "姓名待保存（回车确认）"; },
  save: saveCell,
  cancel: id => { edits.cells.delete(id); render(); },
  slot: (taskId, slot, memberId) => run("edit-slot", { taskId, slot, memberId }),
});
const pointerDrag = new ScheduleDrag<DragSource>(root, {
  source: el => busy || edits.dayDirty || viewingFormal || edits.cells.size || dialog || !plan() ? null : { memberId: el.dataset.dragMember!, taskIds: JSON.parse(el.dataset.taskIds!), planId: plan()!.id, revision: state.revision },
  started: () => editor.dismiss(),
  drop: (source, target) => { void requestDrop(source, target.ids, target.member, target.cellId); },
});
let selectedDate = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Singapore", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
const plan = (): Plan | null => state.selectedPlanId && state.plans[state.selectedPlanId]?.day.serviceDate === selectedDate ? state.plans[state.selectedPlanId] : null;
const split = (value: string) => value.split(/[、,，\n]+/).map(v => v.trim()).filter(Boolean);
const navigation: { id: Section; title: string; icon: string }[] = [
  { id: "config", title: "配置", icon: "sliders" }, { id: "flights", title: "航班", icon: "plane" },
  { id: "schedule", title: "排班", icon: "calendar" }, { id: "rules", title: "规则", icon: "rules" }, { id: "statistics", title: "统计", icon: "rules" }, { id: "history", title: "历史", icon: "history" },
];
const paths: Record<string, string> = {
  sliders: '<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="8" cy="6" r="2"/><circle cx="16" cy="12" r="2"/><circle cx="10" cy="18" r="2"/>',
  plane: '<path d="m21 3-7 18-3-8-8-3 18-7ZM11 13 21 3"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4m10-4v4M3 11h18m-13 5 3 3 5-6"/>',
  rules: '<rect x="9" y="3" width="6" height="5" rx="1"/><path d="M12 8v5M5 17v-4h14v4"/><rect x="2" y="17" width="6" height="4" rx="1"/><rect x="16" y="17" width="6" height="4" rx="1"/>',
  history: '<path d="M3 11a9 9 0 1 1 2 7M3 4v7h7m2-5v6l4 2"/>',
  upload: '<path d="M12 16V3m-5 5 5-5 5 5M4 15v6h16v-6"/>',
  download: '<path d="M12 3v13m-5-5 5 5 5-5M4 15v6h16v-6"/>',
};
const icon = (name: string) => `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] ?? paths.calendar}</svg>`;

async function responseJson(response: Response): Promise<State> {
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail ?? data.message ?? "操作失败，原数据保留");
  return data as State;
}
function markBusy(): void {
  if (busy) root.querySelectorAll<HTMLButtonElement | HTMLInputElement | HTMLSelectElement>("button, input, select").forEach(b => { b.disabled = true; });
  if (busy) root.querySelectorAll<HTMLElement>("[contenteditable=true]").forEach(el => el.contentEditable = "false");
}
async function canLeave(): Promise<boolean> {
  return edits.beforeContinue();
}
function discardChanges(): void {
  edits.discard(); resetEditable();
}
function validateRuleInputs(): boolean {
  for (const input of root.querySelectorAll<HTMLInputElement>(".view-root input[type=number]")) {
    if (!input.disabled && (input.value === "" || !input.checkValidity())) {
      input.closest<HTMLDetailsElement>("details")!.open = true;
      if (edits.confirming) { dialogError = "有数字未填写或超出范围，请选择继续编辑后检查。修改尚未保存。"; render(); return false; }
      input.reportValidity(); return false;
    }
  }
  return true;
}

function resetEditable(): void {
  edits.resetDay(plan());
  viewingFormal = !!plan()?.adopted && !plan()?.schedule?.tasks.length;
}
async function run(action: string, payload: unknown = {}, id = plan()?.id ?? null): Promise<boolean> {
  if (busy) return false;
  if (dialog) {
    dialog.fields = Object.fromEntries([...root.querySelectorAll<HTMLInputElement | HTMLSelectElement>("#app-dialog input[id], #app-dialog select[id]")].map(el => [el.id, el instanceof HTMLInputElement && el.type === "checkbox" ? el.checked : el.value]));
  }
  dialogError = "";
  warningMessage = false;
  editToast = manualActions.has(action);
  editSaved = false;
  busy = true; markBusy();
  let success = false;
  try {
    const command = { expectedRevision: state.revision, action, planId: id, payload };
    state = ["solve", "prepare-solve", "archive-next"].includes(action) ? await runSchedule(command, activeGroup) : await responseJson(await groupFetch(`/api/groups/${activeGroup}/workspace/command`, { method: "POST", headers: { "Content-Type": "application/json", "X-Gate-Rule-Schema": "flight-role-v1" }, body: JSON.stringify(command) }));
    const selectedPlan = state.selectedPlanId ? state.plans[state.selectedPlanId] : null;
    const effect = edits.committed(action, selectedPlan);
    if (effect.followPlan && selectedPlan) selectedDate = selectedPlan.day.serviceDate;
    if (effect.resetDay) viewingFormal = !!plan()?.adopted && !plan()?.schedule?.tasks.length;
    message = action === "import-qualifications" ? "人员与资质已补充；原有资格、当天计划和正式结果保留。新计划会沿用，已有计划如需更新请载入最新人员。" : ["save-role", "save-roles", "save-person", "save-config", "save-flight-template", "copy-flight-roles"].includes(action) ? "配置已保存；已有当天资料和正式结果保持。" : plan()?.message ?? "已保存";
    if (effect.closeDialog) dialog = null;
    if (action === "save-rules") {
      message = "配置已保存；长期规则在重新排班后生效，原正式班表保留。";
    }
    if (action === "save-duty-settings") message = "轮值配置已保存，并随完整Excel和备份迁移。";
    if (["update-duty-roster", "reset-duty-roster", "rebalance-duty-month", "import-duty-roster"].includes(action)) message = "值班备勤已保存，航班班表与正式历史保留。";
    if (action === "delete-person") message = "人员已删除；已有班表与历史记录保留，重新排班后不再安排。";
    if (action === "update-balance-settings") message = "重点轮换与航班数规则已保存；重新排班后生效，原班表保留。";
    if (action === "adjust-priority-count") message = "累计次数已修正，原历史保留；下次重新排班使用最新次数。";
    if (action === "save-handoff-preferences") message = "岗位接续偏好已保存，重新排班后生效；当前安排保留。";
    if (["save-flight-template", "add-template-role", "copy-flight-roles"].includes(action)) message = "航班岗位配置已保存，新一天自动沿用。本日需点“重新排班 → 载入最新岗位配置”再确认；原班表保留。新岗位请设置人数、工作环节及资质。";
    if (action === "save-display-preferences") message = "表格设置已保存，并随完整配置导出。";
    if (action === "edit-cell" && dialog?.kind === "assignment") dialog.fields = {};
    if (effect.schedule) { section = "schedule"; viewingFormal = false; }
    if (manualActions.has(action)) {
      const feedback = plan()?.editFeedback;
      warningMessage = !!feedback?.hasWarnings;
      message = feedback?.message ?? message;
    }
    placingPerson = null;
    success = true;
    editSaved = true;
  } catch (error) { message = error instanceof Error ? error.message : "保存失败"; dialogError = message; warningMessage = true; }
  finally { busy = false; render(); }
  return success;
}
function status(p: Plan): string {
  if (p.preparationNotice) return "草稿已保存 · 待选择当天人员";
  if (edits.cells.size) return "姓名待保存";
  if (!p.valid) return `草稿已保存 · ${p.issues.length}项待处理`;
  const same = p.adopted && p.adopted.inputRevision === p.inputRevision &&
    JSON.stringify([...p.adopted.assignments].sort((a,b)=>(a.taskId+a.memberId).localeCompare(b.taskId+b.memberId))) === JSON.stringify([...p.draft].sort((a,b)=>(a.taskId+a.memberId).localeCompare(b.taskId+b.memberId)));
  return same ? "已采用正式结果" : p.adopted ? "草稿已修改 · 可继续归档" : "排班已生成 · 可继续归档";
}
function render(): void {
  scheduleLayout = state.displayPreferences.layout;
  editor.dispose();
  pointerDrag.dispose();
  disposePalette();
  disposePriorityStatistics();
  disposeRoleStatistics();
  disposeRelaxedStatistics();
  disposeDutyStatistics();
  const openDetails = new Map(previousSection === section ? [...root.querySelectorAll<HTMLDetailsElement>(".view-root details")].map((d,i) => [d.id || String(i), d.open] as const) : []);
  previousSection = section;
  const p = plan();
  const current = navigation.find(n => n.id === section)!;
  const noticeTag = section === "schedule" ? "div" : "details";
  const noticeHeadingTag = section === "schedule" ? "div" : "summary";
  const notice = state.trialInfo ? `<aside id="trial-notice" class="trial-notice"><${noticeTag}><${noticeHeadingTag}><strong>${html(state.trialInfo.label)}</strong><span>出勤、人数和时段含试用假设${section === "schedule" ? "" : " · 查看说明"}</span></${noticeHeadingTag}><p>航班和人员取自历史资料；出勤、岗位人数及时段含试用假设。可直接自动排班或手动调整。</p><p>${html(state.trialInfo.source)}</p>${plan()?.day.roleConfigs.some(r=>r.workMode && r.workMode!=="timed") ? "<p>当前为环节方案：旧试用的20/25分钟不用于环节岗位计算，以当前岗位性质和航班衔接为准。</p>" : ""}<ul>${state.trialInfo.assumptions.map(item => `<li>${html(item)}</li>`).join("")}</ul></${noticeTag}></aside>` : "";
  const body = section === "config" ? configPage(state, edits.day, edits.dayDirty) : section === "flights" ? (weeklyFlightsHtml(state, edits.day, selectedDate) + flightLibraryHtml(state) + `<details id="day-preparation-details" class="workspace-section"><summary>当天航班与人员 · 临时调整</summary>${flightsPage(p, edits.day, selectedDate, edits.dayDirty)}</details>`) : section === "rules" ? rulesPage({ ...state, configuration: edits.settings.configuration ?? state.configuration, balanceSettings: edits.settings.balanceSettings ?? state.balanceSettings, teamLeaderSettings: edits.settings.teamLeaderSettings ?? state.teamLeaderSettings, halfRestSettings: edits.settings.halfRestSettings ?? state.halfRestSettings, relaxedShiftSettings: edits.settings.relaxedShiftSettings ?? state.relaxedShiftSettings, handoffPreferences: edits.settings.handoffPreferences ?? state.handoffPreferences }, p, edits.day) : section === "statistics" ? statisticsPage(state,edits.settings.dutyRoster??null,selectedDate) : section === "history" ? historyPage(state) : scheduleHtml(p);
  root.innerHTML = `<header class="app-header"><div class="app-header-inner"><div class="brand-mark">${icon("calendar")}</div><div class="brand-copy"><h1>自动排班</h1><div>${document.documentElement.dataset.gateLocal === "true" ? "本机保存 · 换设备请导出" : "登机口保障 · 本地工作台"}</div></div><div class="group-switcher" role="group" aria-label="排班组"><label for="active-group">当前组</label><select id="active-group" aria-label="当前组"><option value="A" ${activeGroup === "A" ? "selected" : ""}>A 组</option><option value="B" ${activeGroup === "B" ? "selected" : ""}>B 组</option></select></div><label class="date-control">${icon("calendar")}<input id="service-date" type="date" value="${html(selectedDate)}" aria-label="排班日期" /></label><span class="save-state">${busy ? "正在保存…" : hasUnsaved() ? "有未保存修改" : "✓ 已保存"}</span></div></header>
  <div class="app-layout"><nav class="app-nav" aria-label="主要导航">${navigation.map(n => `<button class="nav-item ${section === n.id ? "active" : ""}" data-view="${n.id}" ${section === n.id ? 'aria-current="page"' : ""}>${icon(n.icon)}<span>${n.title}</span></button>`).join("")}<div class="nav-spacer"></div><label class="nav-item nav-data-action">${icon("upload")}<span>导入数据</span><input id="restore-workspace" type="file" accept=".xlsx,.json" hidden /></label><button id="export-complete-configuration" class="nav-item nav-data-action" title="导出人员、规则、航班与全部历史班表">${icon("download")}<span>导出配置</span></button></nav>
  <main class="app-main wb-main"><div class="content-head"><div><h2>${current.title}</h2><div class="text-muted">${p?.day.flights.length ?? 0} 个航班 · ${p?.day.memberAvailability.filter(a => a.participating).length ?? 0} 人出勤 · ${p?.draft.length ?? 0} 个岗位已排${edits.dayDirty ? ' · <span class="text-danger">当天资料未保存</span>' : ""}</div></div><select id="plan-picker" aria-label="选择当天方案"><option value="">选择当天方案</option>${Object.values(state.plans).filter(v => v.day.serviceDate === selectedDate).map((v, i) => `<option value="${html(v.id)}" ${p?.id === v.id ? "selected" : ""}>方案${i + 1} · ${html(v.day.title)} · ${v.adopted ? "已采用" : "草稿"}</option>`).join("")}</select></div>${message ? `<div class="app-message ${warningMessage ? "edit-warning" : ""}" role="${warningMessage ? "alert" : "status"}">${html(message)}</div>` : ""}${notice}<div class="view-root">${body}</div></main></div>${dialog && !edits.confirming ? dialogHtml(dialog) : ""}${edits.confirming ? modal("有未保存的修改", `<p>是否先保存本次修改，再继续刚才的操作？</p>${dialogError ? `<p class="text-danger" role="alert">${html(dialogError)}</p>` : ""}`, '<button id="leave-save" class="btn btn-primary">保存并继续</button><button id="leave-discard" class="btn btn-outline-danger">放弃修改</button><button id="leave-stay" class="btn btn-outline-secondary">继续编辑</button>', null) : ""}`;
  root.querySelectorAll<HTMLDetailsElement>(".view-root details").forEach((d,i) => { const wasOpen = openDetails.get(d.id || String(i)); if (wasOpen !== undefined) d.open = wasOpen; });
  bindShared(); bindDayInputs();
  if (section === "config") bindConfig();
  if (section === "flights") bindFlights();
  if (section === "rules") bindRules();
  if (section === "schedule") bindSchedule();
  if (section === "history") bindHistory();
  if (section === "statistics") {
    disposePriorityStatistics = bindPriorityStatistics(root,state,priorityStatisticsSelection,run);
    disposeRelaxedStatistics = bindRelaxedStatistics(root,state,relaxedSelection);
    disposeRoleStatistics = bindRoleStatistics(root,state,roleStatisticsSelection);
    disposeDutyStatistics = bindDutyStatistics(root,()=>state,edits.settings.dutyRoster??null,dutySelection,selectedDate,run,value=>{edits.settings.dutyRoster=value;updateSaveStatus();},()=>edits.save(),canLeave);
  }
  if (dialog && !edits.confirming) bindDialog();
  if (dialog?.kind === "assignment" && plan()?.editFeedback?.hasWarnings) {
    const warning = root.querySelector(".assignment-validation");
    if (warning) { warning.setAttribute("role", "alert"); warning.textContent = plan()!.editFeedback!.message; }
  }
  if (edits.rotation && section === "rules") {
    root.querySelector<HTMLInputElement>("#day-rotation-enabled")!.checked = edits.rotation.enabled;
    root.querySelector<HTMLInputElement>("#day-rotation-minutes")!.value = String(edits.rotation.minutes);
  }
  updateSaveStatus();
  root.querySelector<HTMLElement>(".view-root")?.addEventListener("change", updateSaveStatus);
  if (section === "rules") root.querySelector<HTMLElement>(".view-root")?.addEventListener("input", event => {
    const input = event.target as HTMLInputElement;
    if (input.matches('input:not([type=checkbox]):not([type=search])')) input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  if (edits.confirming) {
    const prompt = root.querySelector<HTMLDialogElement>("#app-dialog")!;
    prompt.showModal();
    prompt.oncancel = event => { event.preventDefault(); void edits.resolveLeave("stay"); };
    prompt.querySelector<HTMLButtonElement>("[data-close-dialog]")!.onclick = () => void edits.resolveLeave("stay");
    root.querySelector<HTMLButtonElement>("#leave-stay")!.onclick = () => void edits.resolveLeave("stay");
    root.querySelector<HTMLButtonElement>("#leave-discard")!.onclick = () => void edits.resolveLeave("discard");
    root.querySelector<HTMLButtonElement>("#leave-save")!.onclick = () => void edits.resolveLeave("save");
  }
  markBusy();
  if (editToast && message && section === "schedule" && !dialog) {
    const toast = document.createElement("aside"); toast.className = `manual-feedback ${warningMessage ? "edit-warning" : ""}`;
    toast.setAttribute("role", warningMessage ? "alert" : "status");
    toast.innerHTML = `<button aria-label="关闭操作提示">×</button><strong>${!editSaved ? "本次修改未保存" : warningMessage ? "草稿已保存，有待处理问题" : "本次操作"}</strong><p>${html(message)}</p>`;
    toast.querySelector<HTMLButtonElement>("button")!.onclick = () => { editToast = false; toast.remove(); };
    root.append(toast);
  }
}
async function navigate(next: Section): Promise<void> {
  if (busy || next === section) return;
  if (!await edits.beforeContinue(true)) return;
  section = next; dialog = null; render(); window.scrollTo(0, 0);
}

function bindShared(): void {
  root.querySelector<HTMLSelectElement>("#active-group")!.onchange = async event => {
    const next = (event.target as HTMLSelectElement).value;
    if ((next !== "A" && next !== "B") || busy || next === activeGroup || !await canLeave()) { render(); return; }
    busy = true; markBusy();
    try {
      const response = await fetch(`/api/groups/${next}/workspace`);
      const incoming = await responseJson(response);
      const preference = await fetch("/api/groups/select", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({groupId:next})});
      if (!preference.ok) throw new Error((await preference.json()).detail ?? "切换失败");
      activeGroup = next; selectApiGroup(next); state = incoming;
      discardChanges(); dialog = null; dragSource = null; placingPerson = null; selectedPaletteTarget = "";
      message = `已切换到 ${next} 组`; warningMessage = false; editToast = false;
      selectedDate = state.selectedPlanId ? state.plans[state.selectedPlanId].day.serviceDate : selectedDate;
      priorityStatisticsSelection.month = ""; priorityStatisticsSelection.flightNo = ""; priorityStatisticsSelection.roleName = "";
      roleStatisticsSelection.month = ""; relaxedSelection.month = ""; dutySelection.month = "";
      resetEditable(); previousSection = null;
    } catch (error) { message = String(error); }
    finally { busy = false; render(); }
  };
  root.querySelectorAll<HTMLButtonElement>("[data-view], [data-go]").forEach(b => b.onclick = () => navigate((b.dataset.view ?? b.dataset.go) as Section));
  root.querySelector<HTMLInputElement>("#service-date")!.onchange = async event => {
    const date = (event.target as HTMLInputElement).value;
    if (busy || !await canLeave()) { render(); return; }
    if (!date) { render(); return; }
    const existing = Object.values(state.plans).find(p => p.day.serviceDate === date);
    if (existing) { await run("select-plan", {}, existing.id); return; }
    selectedDate = date; resetEditable(); section = "flights"; message = "此日期还没有计划，可新建或导入日表。"; render();
  };
  root.querySelector<HTMLSelectElement>("#plan-picker")!.onchange = async event => {
    const id = (event.target as HTMLSelectElement).value;
    if (!id || !await canLeave()) { render(); return; }
    await run("select-plan", {}, id);
  };
  root.querySelector<HTMLButtonElement>("#export-complete-configuration")!.onclick = async () => {
    if (busy || !await canLeave()) return;
    busy = true; markBusy();
    try {
      const response = await groupFetch(`/api/workspace/configuration/export?serviceDate=${encodeURIComponent(selectedDate)}`);
      if (!response.ok) throw new Error((await response.json()).detail);
      download(await response.blob(), '登机口排班工具配置.xlsx');
    } catch (error) { message = String(error); }
    finally { busy = false; render(); }
  };
  root.querySelector<HTMLInputElement>("#restore-workspace")!.onchange = async event => {
    const file = (event.target as HTMLInputElement).files?.[0];
    if (busy || !file || !await canLeave()) return;
    busy = true; markBusy(); dialogError = "";
    try {
      const response = await groupFetch('/api/workspace/data/preview', {method:'POST', body:file});
      const preview = await response.json();
      if (!response.ok) throw new Error(preview.detail ?? '导入文件校验失败');
      if (preview.expectedRevision !== state.revision) throw new Error('数据已更新，请刷新页面后重新导入。原资料保持。');
      if (preview.kind === 'configuration') {
        dialog = {kind:'complete-configuration', preview:preview as CompleteConfigurationImport};
      } else if (confirm(`${preview.scopeLabel ?? "恢复会替换当前工作台"}（${preview.peopleCount}人、${preview.historyPlanCount}份方案）。确定继续？`)) {
        state = await responseJson(await groupFetch(`/api/workspace/restore?expectedRevision=${state.revision}${preview.expectedGroupsRevision === undefined ? "" : `&expectedGroupsRevision=${preview.expectedGroupsRevision}`}`, {method:'POST',body:file}));
        selectedDate = state.selectedPlanId ? state.plans[state.selectedPlanId].day.serviceDate : selectedDate;
        resetEditable(); section = plan() ? 'schedule' : 'config'; message = '完整数据已导入';
      }
    } catch (error) { message = String(error); }
    finally { busy = false; render(); }
  };
  root.querySelector<HTMLButtonElement>("#save-preparation")?.addEventListener("click", saveDay);
  root.querySelector<HTMLButtonElement>("#save-handoffs")?.addEventListener("click", saveDay);
  root.querySelectorAll<HTMLSelectElement>("[data-role-mode]").forEach(input => input.onchange = () => {
    const name = input.dataset.roleMode!;
    if(input.dataset.scope === 'base') { void run('save-role', {roleName:name,workMode:input.value}); return; }
    let r=edits.day!.roleConfigs.find(r=>r.roleName===name);
    if(!r) {r={roleName:name,requiredCount:null};edits.day!.roleConfigs.push(r);}
    r.workMode=input.value; edits.day!.releasePolicy='task'; edits.dayDirty=true; render();
  });
  root.querySelectorAll<HTMLInputElement>("[data-role-concurrent]").forEach(input => input.onchange = () => {
    const name=input.dataset.roleConcurrent!;
    if(input.dataset.scope==='base') {void run('save-role',{roleName:name,concurrent:input.checked});return;}
    let r=edits.day!.roleConfigs.find(r=>r.roleName===name);
    if(!r) {r={roleName:name,requiredCount:null};edits.day!.roleConfigs.push(r);}
    r.concurrent=input.checked; edits.dayDirty=true;
  });
  root.querySelectorAll<HTMLInputElement>("[data-role-exclusive]").forEach(input => input.onchange = () => {
    const name = input.dataset.roleExclusive!;
    if (input.dataset.scope === "base") { void run("save-role", { roleName: name, exclusiveSameFlight: input.checked }); return; }
    let role = edits.day!.roleConfigs.find(role => role.roleName === name);
    if (!role) { role = { roleName: name, requiredCount: null }; edits.day!.roleConfigs.push(role); }
    role.exclusiveSameFlight = input.checked; edits.dayDirty = true;
  });
  root.querySelectorAll<HTMLInputElement>("[data-phase-source]").forEach(input=>input.onchange=()=>{
    const source=input.dataset.phaseSource!,target=input.dataset.phaseTarget!;
    edits.day!.phaseHandoffs=(edits.day!.phaseHandoffs??[]).filter(([a,b])=>a!==source||b!==target);
    if(input.checked) edits.day!.phaseHandoffs.push([source,target]);
    edits.dayDirty=true;
  });
}
async function saveDay(): Promise<void> {
  await edits.save("day");
}
function bindConfig(): void {
  root.querySelectorAll<HTMLInputElement | HTMLSelectElement>("[data-person-field]").forEach(input => input.onchange = () => {
    const person = state.people.find(p => p.id === input.dataset.personId)!;
    const field = input.dataset.personField!;
    const value = input instanceof HTMLInputElement && input.type === "checkbox" ? input.checked : input.value;
    void run("save-person", { ...person, [field]: value });
  });
  root.querySelectorAll<HTMLButtonElement>("[data-delete-person]").forEach(button => button.onclick = () => {
    const person = state.people.find(p => p.id === button.dataset.deletePerson)!;
    if (!confirm(`确定删除“${person.name}”？\n此人将退出人员和资质名单；重新排班后不再安排。已有班表和历史记录保留。`)) return;
    void run("delete-person", { id: person.id });
  });
  root.querySelectorAll<HTMLInputElement>("[data-team-leader]").forEach(input => input.onchange = () => {
    const person = state.people.find(p => p.id === input.dataset.teamLeader)!;
    void run("save-person", { id: person.id, name: person.name, active: person.active, teamLeader: input.checked });
  });
  bindTemplateConfig(root, state, run, number => { dialog = { kind: "copy-template", sourceNo: number }; render(); });
  root.querySelector<HTMLInputElement>("#import-complete-configuration")!.onchange = async event => {
    const file = (event.target as HTMLInputElement).files?.[0];
    if (busy || !file || !await canLeave()) return;
    busy = true; markBusy(); dialogError = "";
    try {
      const response = await groupFetch("/api/workspace/configuration/preview", { method: "POST", body: await file.arrayBuffer() });
      const preview = await response.json();
      if (!response.ok) throw new Error(preview.detail ?? "完整Excel读取失败");
      if (preview.expectedRevision !== state.revision) throw new Error("数据已更新，请刷新页面后导入。原资料保持。");
      dialog = { kind: "complete-configuration", preview: preview as CompleteConfigurationImport };
    } catch (error) { message = String(error); }
    finally { busy = false; render(); }
  };

  root.querySelector<HTMLButtonElement>("#download-qualification-template")!.onclick = async () => {
    try {
      const response = await groupFetch("/api/workspace/qualifications/template");
      if (!response.ok) throw new Error("资质模板下载失败");
      download(await response.blob(), "登机口人员资质.xlsx");
    } catch (error) { message = String(error); render(); }
  };
  root.querySelector<HTMLInputElement>("#import-qualifications")!.onchange = async event => {
    const file = (event.target as HTMLInputElement).files?.[0];
    if (busy || !file) return;
    busy = true; markBusy(); dialogError = "";
    try {
      const response = await groupFetch("/api/workspace/qualifications/preview", { method: "POST", body: await file.arrayBuffer() });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail ?? "资质表读取失败");
      if (data.expectedRevision !== state.revision) throw new Error("数据已由其他窗口更新，请刷新页面后再导入。原资料保持。");
      dialog = { kind: "qualification-import", preview: data as QualificationImport };
    } catch (error) { message = String(error); }
    finally { busy = false; render(); }
  };
  root.querySelector<HTMLButtonElement>("#add-person")!.onclick = () => { dialog = { kind: "person", personId: null }; render(); };
  root.querySelectorAll<HTMLButtonElement>("[data-edit-person]").forEach(b => b.onclick = () => { dialog = { kind: "person", personId: b.dataset.editPerson! }; render(); });
  root.querySelector<HTMLButtonElement>("#open-add-role")!.onclick = () => { dialog = { kind: "role" }; render(); };
  root.querySelectorAll<HTMLInputElement>("[data-default-demand]").forEach(input => input.onchange = () => void run("save-role", { roleName: input.dataset.defaultDemand, requiredCount: input.value === "" ? null : Number(input.value) }));
  root.querySelectorAll<HTMLButtonElement>("[data-qualification]").forEach(b => b.onclick = () => {
    const role = b.dataset.qualification!; const scope = b.dataset.scope as "base" | "day";
    const selected = scope === "base" ? state.people.filter(p => p.eligibleRoleNames.includes(role)).map(p => p.id) : edits.day!.memberConfigs.filter(c => c.eligibleRoleNames.includes(role)).map(c => c.memberName);
    dialog = { kind: "qualification", role, scope, selected: new Set(selected) }; render();
  });
  root.querySelector<HTMLButtonElement>("#apply-config")?.addEventListener("click", async () => {
    if (!await canLeave() || !confirm("载入基础人数、资质与规则将重置当前草稿；已采用结果保留。继续？")) return;
    await run("apply-config");
  });
}
function bindFlights(): void {
  root.querySelector<HTMLButtonElement>("#new-flight-template")!.onclick = event => { event.preventDefault(); dialog = { kind: "template" }; render(); };
  root.querySelectorAll<HTMLButtonElement>("[data-edit-template]").forEach(button=>button.onclick=()=>{dialog={kind:"template",flightNo:button.dataset.editTemplate!};render();});
  bindWeeklyFlights(root,state,edits.day,selectedDate,
    async (weekday,flights,remove) => { const ok=await run("save-weekly-flights",{weekday,flights,remove}); if(ok){message="每周常用航班已保存；当天班表未改变";render();}return ok; },
    flights=>{if(!edits.day){void run("new-plan",{serviceDate:selectedDate,flightNos:flights.map(f=>f.flightNo)});return;}applyWeeklyFlights(edits.day,state,flights);edits.dayDirty=true;message="已填入当天，核对航班后保存当天资料";render();});
  root.querySelector<HTMLButtonElement>("#new-plan")!.onclick = async () => { if (await canLeave()) await run("new-plan", { serviceDate: selectedDate }); };
  root.querySelector<HTMLInputElement>("#import-workspace")!.onchange = async event => {
    const file = (event.target as HTMLInputElement).files?.[0]; if (busy || !file || !await canLeave()) return;
    busy = true; markBusy();
    try { state = await responseJson(await groupFetch(`/api/workspace/import?serviceDate=${selectedDate}&expectedRevision=${state.revision}`, { method: "POST", body: await file.arrayBuffer() })); resetEditable(); message = "已导入新方案；请核对资质、日期、人数、时段和出勤。原方案保留。"; }
    catch (error) { message = String(error); }
    finally { busy = false; render(); }
  };
  root.querySelector<HTMLButtonElement>("#open-flight")?.addEventListener("click", () => { dialog = { kind: "flight" }; render(); });
  root.querySelectorAll<HTMLButtonElement>("[data-remove-flight]").forEach(b => b.onclick = () => { edits.day!.flights = edits.day!.flights.filter(f => f.id !== b.dataset.removeFlight); edits.day!.cells = edits.day!.cells.filter(c => c.flightId !== b.dataset.removeFlight); edits.day!.phaseHandoffs=(edits.day!.phaseHandoffs??[]).filter(([a,c])=>a!==b.dataset.removeFlight&&c!==b.dataset.removeFlight); edits.dayDirty = true; render(); });
  root.querySelectorAll<HTMLButtonElement>("[data-config-flight]").forEach(b => b.onclick = () => { section = "config"; render(); const group = [...root.querySelectorAll<HTMLDetailsElement>(".day-flight-group")].find(g => g.querySelector("summary strong")?.textContent === edits.day?.flights.find(f => f.id === b.dataset.configFlight)?.flightNo); if (group) { group.open = true; group.scrollIntoView({ block: "start", behavior: "smooth" }); } });
  root.querySelector<HTMLButtonElement>("#refresh-people")?.addEventListener("click", async () => { if (await canLeave() && confirm("更新当天人员及资质会清除草稿分配；正式结果保留。继续？")) await run("refresh-people"); });
}
function bindRules(): void {
  bindRelaxedRules(root, { ...state, relaxedShiftSettings: edits.settings.relaxedShiftSettings ?? state.relaxedShiftSettings }, value => { edits.settings.relaxedShiftSettings = value; updateSaveStatus(); });
  bindRulesSearch(root, rulesQuery, value => { rulesQuery = value; });
  bindHandoffPreferences(root, { ...state, handoffPreferences: edits.settings.handoffPreferences ?? state.handoffPreferences }, async (_action, payload) => {
    edits.settings.handoffPreferences = (payload as { preferences: State["handoffPreferences"] }).preferences; render(); return true;
  });
  const readBalance = (): BalanceSettings => ({
    ...(edits.settings.balanceSettings ?? state.balanceSettings),
    priorityEnabled: root.querySelector<HTMLInputElement>("#priority-enabled")!.checked,
    priorityRoleNames: [],
    priorityFlightRoles: [...root.querySelectorAll<HTMLInputElement>("[data-priority-role]:checked")].map(input=>({flightNo: input.dataset.priorityFlight!, roleName: input.dataset.priorityRole!})),
    flightCountGap: root.querySelector<HTMLInputElement>("#flight-count-gap-enabled")!.checked ? root.querySelector<HTMLInputElement>("#flight-count-gap")!.valueAsNumber : null,
  });
  root.querySelectorAll<HTMLInputElement>('[data-coverage-role]').forEach(input => input.onchange = () => {
    const balance = structuredClone(edits.settings.balanceSettings ?? state.balanceSettings);
    const roleName = input.dataset.coverageRole!;
    balance.coveragePriorities = (balance.coveragePriorities ?? []).filter(item => item.roleName !== roleName);
    if (input.checked) {
      root.querySelectorAll<HTMLInputElement>('[data-coverage-role]').forEach(other => { if (other !== input && other.dataset.coverageRole === roleName) other.checked = false; });
      balance.coveragePriorities.push({ roleName, priority: input.dataset.coveragePriority as NonNullable<BalanceSettings['coveragePriorities']>[number]['priority'] });
    }
    edits.settings.balanceSettings = balance;
    updateSaveStatus();
  });
  for (const input of root.querySelectorAll<HTMLInputElement>("#priority-enabled, [data-priority-role], #flight-count-gap-enabled, #flight-count-gap")) input.onchange=()=>{
    root.querySelector<HTMLInputElement>("#flight-count-gap")!.disabled=!root.querySelector<HTMLInputElement>("#flight-count-gap-enabled")!.checked;
    edits.settings.balanceSettings=readBalance(); root.querySelector(".save-state")!.textContent="有未保存修改";
    root.querySelector('#priority-selected-summary')!.textContent = `已选：${edits.settings.balanceSettings.priorityFlightRoles!.map(p => `${p.flightNo} · ${p.roleName}`).join('、') || '尚未选择，请先选航班，再勾选岗位。'}`;
  };
  root.querySelector<HTMLSelectElement>('#priority-rule-flight')!.onchange = event => {
    const flight = (event.target as HTMLSelectElement).value;
    root.querySelectorAll<HTMLElement>('[data-priority-flight-group]').forEach(group => group.hidden = group.dataset.priorityFlightGroup !== flight);
  };
  for (const input of root.querySelectorAll<HTMLInputElement>("#team-leader-last, #team-leader-exempt")) input.onchange = () => {
    edits.settings.teamLeaderSettings = { lastUsed: root.querySelector<HTMLInputElement>("#team-leader-last")!.checked, exemptFlightCount: root.querySelector<HTMLInputElement>("#team-leader-exempt")!.checked };
    root.querySelector(".save-state")!.textContent = "有未保存修改";
  };
  root.querySelector<HTMLInputElement>("#half-rest-exempt")!.onchange = event => {
    edits.settings.halfRestSettings = { exemptFlightCount: (event.target as HTMLInputElement).checked }; updateSaveStatus();
  };
  const summaries = () => {
    const set = (id: string, text: string) => { const el = root.querySelector(`#${id} [data-rule-summary]`); if (el) el.textContent = text; };
    const balance = edits.settings.balanceSettings ?? state.balanceSettings;
    const config = edits.settings.configuration ?? state.configuration;
    const leaders = edits.settings.teamLeaderSettings ?? state.teamLeaderSettings;
    set("half-rest-rules", (edits.settings.halfRestSettings ?? state.halfRestSettings).exemptFlightCount ? "上午 / 下午 · 不比较航班数" : "上午 / 下午 · 参与航班数比较");
    set("team-leader-rules", `${leaders.lastUsed ? "最后使用" : "正常参与"} · ${leaders.exemptFlightCount ? "不比较航班数" : "参与航班数比较"}`);
    set("priority-role-rules", balance.priorityEnabled ? `已启用 · ${balance.priorityFlightRoles?.length ?? 0} 个航班岗位` : "未启用");
    set("flight-balance-rules", balance.flightCountGap != null ? `硬约束 · 差距不超过 ${Number.isNaN(balance.flightCountGap) ? "待填写" : balance.flightCountGap}` : "未启用");
    set("base-rule-settings", `${config.rotationEnabled ? `轮换 ${config.rotationMinutes} 分钟` : "轮换关闭"} · 走动 ${config.transitionMinutes} 分钟`);
    set("phase-handoff-rules", `当天确认 · ${edits.day?.phaseHandoffs?.length ?? 0} 项衔接`);
  };
  root.querySelector(".view-root")!.addEventListener("change", summaries);
  root.querySelector<HTMLButtonElement>("#save-rules")!.onclick = () => { void edits.save("rules"); };
  for (const selector of ["#base-rotation-enabled", "#base-rotation-minutes", "#base-transition-minutes"]) root.querySelector<HTMLInputElement>(selector)!.onchange = () => {
    edits.settings.configuration = { ...state.configuration, rotationEnabled: root.querySelector<HTMLInputElement>("#base-rotation-enabled")!.checked, rotationMinutes: root.querySelector<HTMLInputElement>("#base-rotation-minutes")!.valueAsNumber, transitionMinutes: root.querySelector<HTMLInputElement>("#base-transition-minutes")!.valueAsNumber };
    root.querySelector(".save-state")!.textContent = "有未保存修改";
  };

  for (const selector of ["#day-rotation-enabled", "#day-rotation-minutes"]) root.querySelector<HTMLInputElement>(selector)?.addEventListener("change", () => { edits.rotation = { enabled: root.querySelector<HTMLInputElement>("#day-rotation-enabled")!.checked, minutes: root.querySelector<HTMLInputElement>("#day-rotation-minutes")!.valueAsNumber }; edits.dayDirty = true; });
  root.querySelector<HTMLInputElement>("#day-transition-minutes")?.addEventListener("change", event => { edits.day!.transitionMinutes = (event.target as HTMLInputElement).valueAsNumber; edits.dayDirty = true; });
  root.querySelector<HTMLInputElement>("#remarks-reviewed")?.addEventListener("change", event => { edits.day!.remarksReviewed = (event.target as HTMLInputElement).checked; edits.dayDirty = true; });
  root.querySelector<HTMLButtonElement>("#add-occupancy")?.addEventListener("click", () => { edits.day!.extraOccupancies.push({ id: crypto.randomUUID(), label: "", memberNames: [], start: "", end: "" }); edits.dayDirty = true; render(); });
  root.querySelectorAll<HTMLButtonElement>("[data-remove-occupancy]").forEach(b => b.onclick = () => { edits.day!.extraOccupancies = edits.day!.extraOccupancies.filter(o => o.id !== b.dataset.removeOccupancy); edits.dayDirty = true; render(); });
}
function bindDayInputs(): void {
  root.querySelectorAll<HTMLInputElement>("[data-flight]").forEach(input => input.onchange = () => { const f = edits.day!.flights.find(f => f.id === input.dataset.flight)!; if (input.dataset.field === "windowStart") f.windowStart = input.value; else f.windowEnd = input.value; edits.dayDirty = true; });
  root.querySelectorAll<HTMLInputElement>("[data-demand]").forEach(input => input.onchange = () => { const roleName = input.dataset.demand!; const prior=edits.day!.roleConfigs.find(c=>c.roleName===roleName); edits.day!.roleConfigs = edits.day!.roleConfigs.filter(c => c.roleName !== roleName); edits.day!.roleConfigs.push({ ...prior, roleName, requiredCount: input.value === "" ? null : Number(input.value) }); edits.dayDirty = true; });
  root.querySelectorAll<HTMLSelectElement>('[data-cell-mode]').forEach(input=>input.onchange=()=>{edits.day!.cells.find(c=>c.id===input.dataset.cellMode)!.workModeOverride=input.value||null;edits.dayDirty=true;render();});
  root.querySelectorAll<HTMLInputElement>("[data-cell]").forEach(input => input.onchange = () => {
    const cell = edits.day!.cells.find(c => c.id === input.dataset.cell)!;
    switch (input.dataset.field) { case "workStart": cell.workStart = input.value || null; break; case "workEnd": cell.workEnd = input.value || null; break; case "requiredCountOverride": cell.requiredCountOverride = input.value === "" ? null : Number(input.value); break; case "fixedMemberNames": cell.fixedMemberNames = split(input.value); break; } edits.dayDirty = true;
  });
  root.querySelectorAll<HTMLSelectElement>("[data-semantics]").forEach(input => input.onchange = () => { edits.day!.cells.find(c => c.id === input.dataset.semantics)!.semantics = input.value; edits.dayDirty = true; });
  root.querySelectorAll<HTMLInputElement>("[data-attendance-name]").forEach(input => input.onchange = () => {
    const name = input.dataset.attendanceName!; let entry = edits.day!.memberAvailability.find(a => a.memberName === name);
    if (!entry) { entry = { memberName: name, participating: false, availableFrom: null, availableUntil: null }; edits.day!.memberAvailability.push(entry); }
    if (input.dataset.attendanceField === "participating") { entry.participating = input.checked; if (!entry.participating) entry.halfRest = null; edits.dayDirty = true; render(); }
    else if (input.dataset.attendanceField === "availableFrom") entry.availableFrom = input.value || null;
    else entry.availableUntil = input.value || null; edits.dayDirty = true;
  });
  root.querySelectorAll<HTMLSelectElement>("[data-attendance-half-rest]").forEach(input => input.onchange = () => {
    const entry = edits.day!.memberAvailability.find(a=>a.memberName===input.dataset.attendanceHalfRest)!;
    entry.halfRest = input.value as import("./workbench-model").HalfRest || null; edits.dayDirty = true; updateSaveStatus();
  });
  root.querySelectorAll<HTMLInputElement>("[data-occupancy]").forEach(input => input.onchange = () => {
    const occupancy = edits.day!.extraOccupancies.find(o => o.id === input.dataset.occupancy)!;
    if (input.dataset.field === "memberNames") occupancy.memberNames = split(input.value);
    else if (input.dataset.field === "label") occupancy.label = input.value;
    else if (input.dataset.field === "start") occupancy.start = input.value;
    else occupancy.end = input.value; edits.dayDirty = true;
  });
}
function bindHistory(): void {
  root.querySelectorAll<HTMLButtonElement>("[data-open-plan]").forEach(b => b.onclick = async () => { if (!await canLeave()) return; if (await run("select-plan", {}, b.dataset.openPlan!)) { section = "schedule"; viewingFormal = false; render(); } });
  root.querySelectorAll<HTMLButtonElement>("[data-export-plan]").forEach(b => b.onclick = () => void exportPlan(b.dataset.exportPlan!));
}
async function exportPlan(id: string): Promise<void> {
  try { const response = await groupFetch(`/api/workspace/export/${encodeURIComponent(id)}`); if (!response.ok) throw new Error((await response.json()).detail); download(await response.blob(), `登机口正式班表-${state.plans[id].day.serviceDate}.xlsx`); }
  catch (error) { message = String(error); render(); }
}
function dialogHtml(d: Dialog): string {
  if (d.kind === "template") return modal(d.flightNo ? "编辑航班模板" : "新增航班模板", templateForm(state.flightLibrary.find(f=>f.flightNo===d.flightNo)), '<button id="save-flight-template" class="btn btn-primary">保存</button>');
  if (d.kind === "copy-template") return modal("复制岗位规则", `<p>将 ${html(d.sourceNo)} 的岗位人数和例外复制到所选航班。已有班表保留。</p><div class="weekday-choices">${state.flightLibrary.filter(f=>f.flightNo!==d.sourceNo).map(f=>`<label><input type="checkbox" data-copy-target="${html(f.flightNo)}" />${html(f.flightNo)}</label>`).join("")}</div>`, '<button id="confirm-copy-template" class="btn btn-primary">确认复制</button>');
  if (d.kind === "prepare") {
    const archive = d.mode === "archive";
    const hasPeople = d.day.memberAvailability.some(a=>a.participating);
    return modal(archive ? "归档并排后天 · 确认准备" : "重新排班 · 确认准备", `<label class="prepare-date">${archive ? "下一班日期" : "排班日期"}<input id="prepare-date" type="date" value="${html(d.day.serviceDate)}" ${archive ? "" : "disabled"} /></label><p class="section-note">${archive ? `选好航班、当天人员和设置后，一次生成后天班表并归档 ${html(plan()!.day.serviceDate)}。资料有缺项或暂时排不开时，仍归档原班并保留后天待处理草稿。` : "勾选需要排的航班，核对出勤，再重新计算。资料变更会新建当天方案，原草稿和正式班表保留。"}</p><h3>选择航班</h3><div class="button-row"><button id="prepare-all-flights">全选</button><button id="prepare-no-flights">清空</button><button id="prepare-weekly-flights">恢复该星期预设</button><button id="prepare-latest-config">载入最新岗位配置</button></div><p class="section-note">可从本地已有航班选择，时间可修改；额外选择的航班使用当天岗位设置。</p><div class="table-responsive"><table class="data-table"><thead><tr><th>排班</th><th>航班</th><th>开始</th><th>结束</th></tr></thead><tbody>${d.day.flights.map(f => `<tr><td><input type="checkbox" data-prepare-flight="${html(f.id)}" ${d.selected.has(f.id) ? "checked" : ""} /></td><td>${html(f.flightNo)}</td><td><input data-prepare-window="${html(f.id)}" data-field="windowStart" value="${html(f.windowStart)}" /></td><td><input data-prepare-window="${html(f.id)}" data-field="windowEnd" value="${html(f.windowEnd)}" /></td></tr>`).join("")}</tbody></table></div>${archive ? '<label class="section-note"><input id="confirm-role-times" type="checkbox" />确认沿用特殊岗位时段及跨航班衔接（岗位性质与人数照常沿用）</label>' : ""}<h3 id="prepare-people-heading">选择当天人员</h3><div class="button-row"><button id="prepare-all-people" class="btn btn-sm">全选</button><button id="prepare-no-people" class="btn btn-sm">全不选</button><button id="prepare-copy-attendance" class="btn btn-sm">沿用并确认原出勤</button></div><p class="section-note">已带入当前人员选择，请核对当天谁来上班；确认前不会保存。无需填写到岗、离岗时间，晚到早退才设置限制。</p>${halfRestSelector(d.day,state.people)}<div class="table-responsive"><table class="data-table"><thead><tr><th>参加</th><th>姓名</th><th>特殊时间限制（可选）</th></tr></thead><tbody>${d.day.roster.map(name => {
      const a = d.day.memberAvailability.find(a => a.memberName === name);
      return `<tr><td><input type="checkbox" data-prepare-person="${html(name)}" data-field="participating" ${a?.participating ? "checked" : ""} /></td><td>${html(name)}</td><td><details ${a?.availableFrom || a?.availableUntil ? "open" : ""}><summary>${a?.availableFrom || a?.availableUntil ? `${html(a?.availableFrom)}—${html(a?.availableUntil)}` : "不限时 · 设置晚到/早退"}</summary><label>到岗<input data-prepare-person="${html(name)}" data-field="availableFrom" value="${html(a?.availableFrom)}" placeholder="可选，起止一起填写" /></label><label>离岗<input data-prepare-person="${html(name)}" data-field="availableUntil" value="${html(a?.availableUntil)}" placeholder="如次日02:00" /></label><small>两项清空表示不限时；填写后按此范围检查。</small></details></td></tr>`;
    }).join("")}</tbody></table></div><p class="section-note">环节岗位无需逐格时间，特殊实际时段仍按分钟检查；日期与次日可在航班/配置修改。资料不完整时会在此提示，已选内容保留，可修改后直接重试。</p>${archive ? `<p id="archive-preparation-note" class="section-note">${hasPeople ? "生成后可直接调整，再点“归档并排后天”继续，无需额外点采用。" : "尚未选择后天人员，也可归档并建立待处理草稿。"}</p>` : ""}`, `<span id="prepare-selection-summary" class="prepare-selection-summary" role="status">${d.selected.size} 个航班 · ${d.day.memberAvailability.filter(a=>a.participating).length} 人上班${!hasPeople ? " · 还未选择人员" : !d.selected.size ? " · 还未选择航班" : ""}</span>${archive ? `<button id="prepare-jump-people" class="btn btn-outline-secondary" ${hasPeople ? "hidden" : ""}>去选择人员</button>` : ""}<button id="confirm-prepare" class="btn ${archive ? "btn-success" : "btn-primary"}" >${archive ? "归档并生成后天排班" : "确认并重新排班"}</button>`);
  }
  if (d.kind === "cell-settings") {
    const p = plan()!, cell = p.day.cells.find(c => c.id === d.cellId)!;
    const roleConfig=p.day.roleConfigs.find(r=>r.roleName===cell.roleName);
    const effectiveMode=cell.workModeOverride ?? roleConfig?.workMode ?? "timed";
    const phase=effectiveMode!=="timed";
    const count = cell.requiredCountOverride ?? roleConfig?.requiredCount;
    return modal(`${html(cell.flightNo)} · ${html(cell.roleName)} · 岗位资料`, `<p class="section-note">这格会作为真实岗位参与排班。请明确人数；环节岗位无需逐格时间，输入的姓名不会自动授予资质；资料未齐可先保存草稿。</p><div class="form-grid"><label>本航班工作方式<select id="cell-settings-mode" ${cell.roleName === "大件行李拦截" ? "disabled" : ""}><option value="">沿用岗位环节</option>${Object.entries(state.workModes).map(([k,v])=>`<option value="${k}" ${cell.workModeOverride===k ? "selected" : ""}>${html(v)}</option>`).join("")}</select></label><label>人员姓名<input id="cell-settings-text" value="${html(p.cellDrafts?.[cell.id] ?? edits.cells.get(cell.id) ?? "")}" placeholder="甲 / 乙" /></label><label>同时需要人数<input id="cell-settings-count" type="number" min="0" value="${count ?? ""}" /></label><label>${phase ? html(state.workModes[effectiveMode])+"（无需填写）" : "实际开始"}<input id="cell-settings-start" value="${html(cell.workStart)}" ${cell.roleName === "大件行李拦截" || phase ? "disabled" : ""} /></label><label>实际结束<input id="cell-settings-end" value="${html(cell.workEnd)}" ${cell.roleName === "大件行李拦截" || phase ? "disabled" : ""} /></label></div>${cell.roleName === "大件行李拦截" ? '<p class="section-note">大件按航班窗口共享轮换，轮换人选到格子详情逐段填写。</p>' : ""}`, '<button id="save-cell-settings" class="btn btn-primary">保存岗位资料</button>');
  }
  if (d.kind === "move") {
    const p = plan()!, tasks = p.schedule!.tasks;
    const option = (id: string) => { const task = tasks.find(t => t.id === id)!; return `<option value="${html(id)}">${html(task.roleId)} · ${html(task.timingLabel ?? `${task.start}—${task.end}`)}</option>`; };
    const segmented = d.source.taskIds.length > 1 || d.targetIds.length > 1;
    return modal(segmented ? "选择要调整的分段" : "选择要交换的人员", `<p class="section-note">${segmented ? "此格包含多个实际任务，请指定一段，避免把轮换名单全部移动。" : "目标岗位有多名人员，请选择要交换的人，其余人员保持。"}</p>${d.source.taskIds.length ? `<label>来源${segmented ? "分段" : "岗位"}<select id="move-source">${d.source.taskIds.map(option).join("")}</select></label>` : ""}<label>目标${segmented ? "分段" : "岗位"}<select id="move-target">${d.targetIds.map(option).join("")}</select></label><label>目标人员（替换或交换）<select id="move-target-member"></select></label>`, '<button id="confirm-move" class="btn btn-primary">确认调整</button>');
  }
  if (d.kind === "assignment") {
    const p = plan()!, schedule = p.schedule!;
    const segments = d.taskIds ? [...new Map(Object.values(schedule.matrix[d.role] ?? {}).flat().filter(s => d.taskIds!.includes(s.task_id)).map(s => [s.task_id, s])).values()].sort((a,b) => a.start-b.start) : schedule.matrix[d.role]?.[d.column] ?? [];
    return modal(`${html(d.column)} · ${html(d.role)}`, `<p class="section-note">选择后自动保存草稿，未排齐也可以关闭后继续。</p><div class="assignment-editor">${segments.map(segment => {
      const task = schedule.tasks.find(t => t.id === segment.task_id)!;
      const assignments = p.draft.filter(a => a.taskId === task.id);
      return `<section class="assignment-group"><label class="task-text-label">直接输入姓名<input id="task-${html(task.id)}" data-task-text="${html(task.id)}" data-cell-id="${html(p.day.cells.find(c => c.roleName === d.role && schedule.matrix[d.role]?.[c.flightNo]?.some(s => s.task_id === task.id))?.id)}" value="${html(assignments.map(a => schedule.members.find(m => m.id === a.memberId)?.name ?? a.memberId).join(" / "))}" /></label><div class="assignment-people">${Array.from({ length: Math.max(task.requiredCount, assignments.length) }, (_, index) => `<select data-assignment-task="${html(task.id)}" data-slot="${index}" aria-label="${html(d.role)} 人员${index + 1}"><option value="">待安排</option>${schedule.members.filter(m => task.qualifiedMemberIds.includes(m.id) || assignments[index]?.memberId === m.id).map(m => `<option value="${html(m.id)}" ${assignments[index]?.memberId === m.id ? "selected" : ""} ${task.qualifiedMemberIds.includes(m.id) ? "" : "disabled"}>${html(m.name)}${task.qualifiedMemberIds.includes(m.id) ? "" : "（原草稿，资格不符）"}</option>`).join("")}</select>`).join('<span aria-hidden="true">/</span>')}</div><details id="assignment-info-${html(task.id)}" class="assignment-info"><summary>查看时段与人选</summary><p>${html(task.timingLabel ?? `${task.start}—${task.end}`)}${d.role === "大件行李拦截" ? " · 共享轮换任务，修改同步到覆盖航班" : ""}</p>${task.lockedMemberIds.length ? `<p>固定：${task.lockedMemberIds.map(id => html(schedule.members.find(m => m.id === id)?.name ?? id)).join("、")}</p>` : ""}<button data-candidates="${html(task.id)}">查看人选与原因</button></details></section>`;
    }).join("")}</div><p class="assignment-validation ${p.valid ? "" : "text-danger"}" role="status">${p.valid ? "草稿校验通过" : `${p.issues.length}项待处理，可保留问题归档和导出`}</p><div id="candidate-result" role="status"></div>`, '<button class="btn btn-primary" data-close-dialog>完成</button>', null);
  }
  if (d.kind === "complete-configuration") {
    const p = d.preview, day = p.payload.day;
    return modal("导入完整Excel · 核对更新", `<div class="import-summary"><strong>${html(p.scopeLabel ?? "导入当前组配置")}</strong><span>${html(p.serviceDate)} · ${p.flightCount}航班 · ${p.peopleCount}人</span><span data-configuration-history>${p.historyPlanCount ? `携带 ${p.historyDayCount} 天 / ${p.historyPlanCount} 份历史方案；补入 ${p.newHistoryPlanCount} 份，已有 ${p.retainedHistoryPlanCount} 份保留本机版本` : "此文件未携带历史；已有历史保留"}</span><span>新增人员 ${p.newPeopleCount}</span><span>增加资质 ${p.newQualificationCount}</span><span class="text-danger">取消资质 ${p.removedQualificationCount}</span><span>接续偏好：${p.payload.handoffPreferences ? `按Excel更新（${p.payload.handoffPreferences.length}项）` : "旧文件未含此表，保留当前设置"}</span><span>重点轮换与航班差距：${p.payload.balanceSettings ? "按Excel更新，保留文件内累计次数修正" : "旧文件未含此设置，保留当前规则和次数修正"}</span><span>状态与备注：${p.payload.peopleStatus ? "按Excel更新正常/病假/休假" : "旧文件未含状态列，保留当前状态"}；${p.payload.peopleRemarks ? "同步备注（空白清空）" : "保留已有备注"}</span><span>分队长：${p.payload.peopleTeamLeaders ? "按Excel人员状态表更新" : "旧文件未含此列，保留已有标记"}；${p.payload.teamLeaderSettings ? "同步最后使用和航班数豁免设置" : "旧文件未含规则，保留当前设置"}</span><span>人员状态：${p.payload.peopleActive ? "按Excel更新启停" : "保留现有启停"}</span><span>界面偏好：${p.payload.displayPreferences ? "按Excel更新布局和缩放" : "保留现有设置"}</span></div><p class="section-note">来源：${html(p.payload.source)}。确认后更新表中人员×岗位范围内的资格，0或空白取消资格；人数、性质、轮换/走动及文件包含的偏好和人员状态同步到基础配置，并新增当天方案。当天岗位、资质和设置例外仅用于新方案，长期默认与当天调整均保留。旧计划、草稿和正式班表保留。</p><p class="section-note">出勤 ${day.memberAvailability.filter(a=>a.participating).length} 人；大件轮换${p.payload.rotationEnabled ? `每${p.payload.rotationMinutes}分钟` : "关闭"}；走动 ${day.transitionMinutes} 分钟。输入资料缺项会在新草稿中标出，正式采用仍需独立校验。</p><div class="table-responsive"><table class="data-table"><thead><tr><th>岗位</th><th>人数</th><th>Excel资质人员</th></tr></thead><tbody>${day.roleNames.map(role=>`<tr><td>${html(role)}</td><td>${day.roleConfigs.find(r=>r.roleName===role)?.requiredCount ?? "未配置"}</td><td>${day.memberConfigs.filter(m=>m.eligibleRoleNames.includes(role)).map(m=>html(m.memberName)).join("、") || "无人"}</td></tr>`).join("")}</tbody></table></div><p class="section-note">取消不保存任何修改。确认后可直接到排班点“自动排班”；文件携带的每日航班、草稿和正式班表会补入“历史”，同ID保留本机版本；本机已有同日正式优先计次。历史附表请完整保留，日常修改使用可编辑维护表。</p>`, '<button id="confirm-complete-configuration" class="btn btn-primary">确认更新并新建方案</button>');
  }

  if (d.kind === "qualification-import") {
    const p = d.preview;
    return modal("导入人员资质 · 核对预览", `<p class="section-note">来源：${html(p.payload.source)}。历史做过仅作建议，确认后才补充到系统。</p><div class="import-summary"><span>新增人员 <strong>${p.newPeopleCount}</strong></span><span>匹配已有 <strong>${p.existingPeopleCount}</strong></span><span>新增资质 <strong>${p.newQualificationCount}</strong></span></div><p class="section-note">人员：${p.payload.people.map(html).join("、")}</p>${p.inactivePeople.length ? `<p class="wb-warning">${p.inactivePeople.map(html).join("、")} 已停用；导入后仍保持停用。</p>` : ""}<div class="table-responsive"><table class="data-table qualification-import-table"><thead><tr><th>岗位</th><th>本次补充人员</th></tr></thead><tbody>${p.payload.roles.map(r => `<tr><td>${html(r.roleName)}</td><td>${r.memberNames.map(html).join("、") || "无；原有资格保留"}</td></tr>`).join("")}</tbody></table></div><p class="section-note">需要调整请取消，在Excel改1或留空后重新导入。空白不删除已有资格；当天出勤、岗位人数、时段、草稿和正式结果保持。</p>`, '<button id="confirm-qualification-import" class="btn btn-primary">确认补充资质</button>');
  }
  if (d.kind === "person") {
    const p = state.people.find(p => p.id === d.personId);
    return modal(p ? "编辑人员" : "新增人员", `<form id="person-form" class="form-grid"><label>姓名<input id="person-name" required value="${html(p?.name)}" autocomplete="off" /></label><label class="switch-label"><input id="person-active" type="checkbox" ${p?.active !== false ? "checked" : ""} />启用此人员</label><label class="switch-label"><input id="person-team-leader" type="checkbox" ${p?.teamLeader ? "checked" : ""} />分队长</label><label>状态<select id="person-status">${state.personnelStatuses.map(status=>`<option value="${html(status)}" ${(p?.status ?? "正常")===status ? "selected" : ""}>${html(status)}</option>`).join("")}</select></label><label>备注<input id="person-remark" value="${html(p?.remark)}" placeholder="可选" /></label></form><p class="section-note">分队长最后安排，普通人员不足时补位；默认不参与航班数差距比较。可在规则页调整。</p><p class="section-note">资质在岗位行的“资质人员”中勾选。</p>`, '<button id="save-person" class="btn btn-primary">保存</button>');
  }
  if (d.kind === "role") return modal("批量新增岗位", '<form id="role-form" class="form-grid"><label>岗位名称（多个用逗号分隔）<input id="new-role" required /></label><label>同时需要人数<input id="new-role-count" type="number" min="0" placeholder="可暂不填写" /></label></form>', '<button id="save-role" class="btn btn-primary">保存</button>');
  if (d.kind === "flight") return modal("添加航班", '<form id="flight-form" class="form-grid"><label>航班号<input id="flight-number" required placeholder="例如 AK151" /></label><label>保障开始<input id="flight-start" required placeholder="例如 23:05" /></label><label>保障结束<input id="flight-end" required placeholder="例如 次日00:05" /></label></form><p class="section-note">航班保障窗口仍需明确；普通环节岗无需逐格时间，特殊实际时段可在“配置”单独设置。</p>', '<button id="add-flight" class="btn btn-primary">添加</button>');
  if (d.kind !== "qualification") return "";
  const choices = d.scope === "base" ? state.people.map(p => ({ id: p.id, name: p.name, active: p.active })) : edits.day!.roster.map(name => ({ id: name, name, active: state.people.find(p => p.name === name)?.active !== false }));
  return modal(`${html(d.role)} · 资质人员`, `<div class="qualification-tools"><span>${d.scope === "base" ? "基础岗位资质" : "当天岗位资质，同岗各航班共用"}</span><div class="button-row"><button id="select-all" class="btn btn-sm btn-outline-secondary">全选</button><button id="select-none" class="btn btn-sm btn-outline-secondary">全不选</button></div></div><div class="qualified-grid">${choices.map(p => `<label class="qualified-check"><input type="checkbox" data-qualified-person="${html(p.id)}" ${d.selected.has(p.id) ? "checked" : ""} /><span>${html(p.name)}<small>${p.active ? "正常" : "停用，历史资质保留"}</small></span></label>`).join("") || '<p class="text-muted">暂无人员，请先新增人员。</p>'}</div>`, '<button id="save-qualification" class="btn btn-primary">保存</button>');
}
function bindDialog(): void {
  if (dialog?.kind === "template") {
    root.querySelector<HTMLButtonElement>("#save-flight-template")!.onclick = () => {
      if (!root.querySelector<HTMLFormElement>("#template-form")!.reportValidity()) return;
      void run("save-flight-template", { flightNo: root.querySelector<HTMLInputElement>("#template-number")!.value, windowStart: root.querySelector<HTMLInputElement>("#template-start")!.value, windowEnd: root.querySelector<HTMLInputElement>("#template-end")!.value });
    };
  }
  if (dialog?.kind === "copy-template") {
    const sourceNo = dialog.sourceNo;
    root.querySelector<HTMLButtonElement>("#confirm-copy-template")!.onclick = () => void run("copy-flight-roles", { sourceNo, targetNos: [...root.querySelectorAll<HTMLInputElement>("[data-copy-target]:checked")].map(el=>el.dataset.copyTarget!) });
  }
  const element = root.querySelector<HTMLDialogElement>("#app-dialog")!;
  if (dialogError) { const error = document.createElement("p"); error.className = "dialog-error"; error.setAttribute("role", "alert"); error.textContent = dialogError; element.querySelector(".modal-body")!.prepend(error); }
  for (const [id, v] of Object.entries(dialog!.fields ?? {})) { const input = element.querySelector<HTMLInputElement>(`#${id}`); if (input) { if (typeof v === "boolean") input.checked = v; else input.value = v; } }
  element.showModal();
  const close = () => { dialog = null; dialogError = ""; render(); };
  root.querySelectorAll<HTMLButtonElement>("[data-close-dialog]").forEach(b => b.onclick = close);
  element.oncancel = event => { if (busy) event.preventDefault(); else { event.preventDefault(); close(); } };
  if (dialog!.kind === "prepare") {
    const d = dialog as Extract<Dialog, { kind: "prepare" }>;
    bindHalfRest(root, d.day);
    const updatePreparationStatus = () => {
      syncHalfRest(root, d.day);
      const count=d.day.memberAvailability.filter(a=>a.participating).length;
      root.querySelector("#prepare-selection-summary")!.textContent=`${d.selected.size} 个航班 · ${count} 人上班${!count ? " · 还未选择人员" : !d.selected.size ? " · 还未选择航班" : ""}`;
      if(d.mode!=="archive")return;
      root.querySelector<HTMLButtonElement>("#confirm-prepare")!.disabled=false;
      root.querySelector<HTMLButtonElement>("#prepare-jump-people")!.hidden=!!count;
      root.querySelector("#archive-preparation-note")!.textContent=count ? "生成后可直接调整，再点“归档并排后天”继续，无需额外点采用。" : "尚未选择后天人员，也可归档并建立待处理草稿。";
    };
    root.querySelector<HTMLButtonElement>("#prepare-jump-people")?.addEventListener("click",()=>{
      root.querySelector("#prepare-people-heading")!.scrollIntoView({block:"start"});
      root.querySelector<HTMLInputElement>('[data-prepare-person][data-field="participating"]')?.focus({preventScroll:true});
    });
    const updateFlightChecks=()=>{root.querySelectorAll<HTMLInputElement>("[data-prepare-flight]").forEach(i=>i.checked=d.selected.has(i.dataset.prepareFlight!));updatePreparationStatus();};
    root.querySelector<HTMLButtonElement>("#prepare-all-flights")!.onclick=()=>{d.day.flights.forEach(f=>d.selected.add(f.id));updateFlightChecks();};
    root.querySelector<HTMLButtonElement>("#prepare-no-flights")!.onclick=()=>{d.selected.clear();updateFlightChecks();};
    const useWeekly=()=>{const date=root.querySelector<HTMLInputElement>("#prepare-date")!.value;const preset=state.weeklyFlightPresets[weekdayOf(date)];if(!preset){dialogError="该星期尚未保存预设，请在航班页维护；当前选择保留";d.day.serviceDate=date;render();return;}d.selected.clear();d.day.flights.filter(f=>preset.some(t=>t.flightNo===f.flightNo)).forEach(f=>d.selected.add(f.id));updateFlightChecks();};
    root.querySelector<HTMLButtonElement>("#prepare-weekly-flights")!.onclick=useWeekly;
    root.querySelector<HTMLButtonElement>("#prepare-latest-config")!.onclick=()=>{
      if (!confirm("以长期配置替换本次准备中的人数、资质和岗位设置？当前已保存班表不变，确认排班后才使用。")) return;
      loadPreparationConfiguration(d.day,state); render();
    };
    root.querySelector<HTMLInputElement>("#prepare-date")!.onchange=()=>{const date=root.querySelector<HTMLInputElement>("#prepare-date")!.value;d.day.serviceDate=date;if(state.weeklyFlightPresets[weekdayOf(date)])useWeekly();};
    root.querySelectorAll<HTMLInputElement>("[data-prepare-flight]").forEach(input => input.onchange = () => { if (input.checked) d.selected.add(input.dataset.prepareFlight!); else d.selected.delete(input.dataset.prepareFlight!); updatePreparationStatus(); });
    root.querySelectorAll<HTMLInputElement>("[data-prepare-window]").forEach(input => input.onchange = () => { const f = d.day.flights.find(f => f.id === input.dataset.prepareWindow)!; f[input.dataset.field as "windowStart" | "windowEnd"] = input.value; });
    root.querySelectorAll<HTMLInputElement>("[data-prepare-person]").forEach(input => input.onchange = () => {
      const name = input.dataset.preparePerson!;
      let a = d.day.memberAvailability.find(a => a.memberName === name);
      if (!a) { a = { memberName: name, participating: false, availableFrom: null, availableUntil: null }; d.day.memberAvailability.push(a); }
      if (input.dataset.field === "participating") { a.participating = input.checked; if (!a.participating) a.halfRest = null; }
      else a[input.dataset.field as "availableFrom" | "availableUntil"] = input.value || null;
      updatePreparationStatus();
    });
    for (const [id, participating] of [["prepare-all-people", true], ["prepare-no-people", false]] as const) {
      root.querySelector<HTMLButtonElement>(`#${id}`)!.onclick = () => {
        for (const name of d.day.roster) {
          let a = d.day.memberAvailability.find(a => a.memberName === name);
          if (!a) { a = { memberName: name, participating, availableFrom: null, availableUntil: null }; d.day.memberAvailability.push(a); }
          a.participating = participating; if (!participating) a.halfRest = null;
        }
        root.querySelectorAll<HTMLInputElement>('[data-prepare-person][data-field="participating"]').forEach(el => { el.checked = participating; });
        updatePreparationStatus();
      };
    }
    root.querySelector<HTMLButtonElement>("#prepare-copy-attendance")!.onclick = () => {
      const date = root.querySelector<HTMLInputElement>("#prepare-date")!;
      const confirmTimes = root.querySelector<HTMLInputElement>("#confirm-role-times");
      d.fields = { ...d.fields, "prepare-date": date.value, ...(confirmTimes ? { "confirm-role-times": confirmTimes.checked } : {}) };
      d.day.serviceDate = date.value; copyPreparationAttendance(d,plan()!.day); render();
    };
    root.querySelector<HTMLButtonElement>("#confirm-prepare")!.onclick = async () => {
      let day: Day;
      try {
        day = confirmPreparation(d,root.querySelector<HTMLInputElement>("#prepare-date")!.value,root.querySelector<HTMLInputElement>("#confirm-role-times")?.checked ?? false);
      } catch (error) { dialogError = (error as Error).message; render(); return; }
      await run(d.mode === "archive" ? "archive-next" : "prepare-solve", d.mode === "archive" ? { day, rotationMinutes: state.configuration.rotationMinutes, rotationEnabled: state.configuration.rotationEnabled } : { day });
    };
  }
  if (dialog!.kind === "cell-settings") {
    root.querySelector<HTMLSelectElement>('#cell-settings-mode')!.onchange=()=>{
      const selected=root.querySelector<HTMLSelectElement>('#cell-settings-mode')!.value;
      const c=plan()!.day.cells.find(c=>c.id===(dialog as Extract<Dialog,{kind:'cell-settings'}>).cellId)!;
      const mode=selected || plan()!.day.roleConfigs.find(r=>r.roleName===c.roleName)?.workMode || 'timed';
      root.querySelector<HTMLInputElement>('#cell-settings-start')!.disabled=mode!=='timed';
      root.querySelector<HTMLInputElement>('#cell-settings-end')!.disabled=mode!=='timed';
    };

    const d = dialog as Extract<Dialog, { kind: "cell-settings" }>;
    root.querySelector<HTMLButtonElement>("#save-cell-settings")!.onclick = async () => {
      const count = root.querySelector<HTMLInputElement>("#cell-settings-count")!.value;
      if (count === "") { dialogError = "请明确填写同时需要人数"; render(); return; }
      const ok = await run("configure-cell", { cellId: d.cellId, text: root.querySelector<HTMLInputElement>("#cell-settings-text")!.value, requiredCount: Number(count), workModeOverride: root.querySelector<HTMLSelectElement>("#cell-settings-mode")!.value || null, workStart: root.querySelector<HTMLInputElement>("#cell-settings-start")!.value, workEnd: root.querySelector<HTMLInputElement>("#cell-settings-end")!.value });
      if (ok) edits.cells.delete(d.cellId);
    };
  }
  if (dialog!.kind === "move") {
    const d = dialog as Extract<Dialog, { kind: "move" }>;
    const target = root.querySelector<HTMLSelectElement>("#move-target")!;
    const updateMembers = () => {
      const assignments = plan()!.draft.filter(a => a.taskId === target.value);
      const task = plan()!.schedule!.tasks.find(t => t.id === target.value)!;
      root.querySelector<HTMLSelectElement>("#move-target-member")!.innerHTML = `${assignments.length < task.requiredCount ? '<option value="">填入空位</option>' : ""}${assignments.map(a => `<option value="${html(a.memberId)}" ${a.memberId === d.targetMember ? "selected" : ""}>${html(plan()!.schedule!.members.find(m => m.id === a.memberId)?.name)}</option>`).join("")}`;
    };
    target.onchange = updateMembers; updateMembers();
    root.querySelector<HTMLButtonElement>("#confirm-move")!.onclick = () => void performMove(d.source, root.querySelector<HTMLSelectElement>("#move-source")?.value, target.value, root.querySelector<HTMLSelectElement>("#move-target-member")!.value || undefined);
  }
  root.querySelectorAll<HTMLInputElement>("[data-task-text]").forEach(input => {
    const save = () => { dialog!.fields = { ...dialog!.fields, [`task-${input.dataset.taskText}`]: input.value }; input.id = `task-${input.dataset.taskText}`; void run("edit-cell", { cellId: input.dataset.cellId, taskId: input.dataset.taskText, text: input.value }); };
    input.onchange = save;
    input.onkeydown = event => { if (event.key === "Enter" && !event.isComposing) { event.preventDefault(); input.blur(); } };
  });
  if (dialog!.kind === "complete-configuration") {
    const d = dialog as Extract<Dialog, { kind: "complete-configuration" }>;
    root.querySelector<HTMLButtonElement>("#confirm-complete-configuration")!.onclick = () => void run("import-complete-configuration", d.preview.payload);
  }
  if (dialog!.kind === "qualification-import") {
    const d = dialog as Extract<Dialog, { kind: "qualification-import" }>;
    root.querySelector<HTMLButtonElement>("#confirm-qualification-import")!.onclick = () => void run("import-qualifications", d.preview.payload);
  }
  if (dialog!.kind === "person") root.querySelector<HTMLButtonElement>("#save-person")!.onclick = () => {
    if (!root.querySelector<HTMLFormElement>("#person-form")!.reportValidity()) return;
    const d = dialog as Extract<Dialog, { kind: "person" }>;
    void run("save-person", { id: d.personId, name: root.querySelector<HTMLInputElement>("#person-name")!.value, active: root.querySelector<HTMLInputElement>("#person-active")!.checked, teamLeader: root.querySelector<HTMLInputElement>("#person-team-leader")!.checked, status: root.querySelector<HTMLSelectElement>("#person-status")!.value, remark: root.querySelector<HTMLInputElement>("#person-remark")!.value });
  };
  if (dialog!.kind === "role") root.querySelector<HTMLButtonElement>("#save-role")!.onclick = () => {
    if (!root.querySelector<HTMLFormElement>("#role-form")!.reportValidity()) return;
    const input = root.querySelector<HTMLInputElement>("#new-role-count")!;
    void run("save-roles", { roleNames: split(root.querySelector<HTMLInputElement>("#new-role")!.value), requiredCount: input.value === "" ? null : Number(input.value) });
  };
  if (dialog!.kind === "flight") root.querySelector<HTMLButtonElement>("#add-flight")!.onclick = () => {
    if (!root.querySelector<HTMLFormElement>("#flight-form")!.reportValidity()) return;
    const flightNo = root.querySelector<HTMLInputElement>("#flight-number")!.value.trim();
    if (edits.day!.flights.some(f => f.flightNo === flightNo)) { dialogError = "当天已有该航班号"; dialog!.fields = Object.fromEntries([...element.querySelectorAll<HTMLInputElement>("input[id]")].map(el => [el.id, el.value])); render(); return; }
    const f: Flight = { id: crypto.randomUUID(), flightNo, windowStart: root.querySelector<HTMLInputElement>("#flight-start")!.value, windowEnd: root.querySelector<HTMLInputElement>("#flight-end")!.value };
    edits.day!.flights.push(f); edits.day!.cells.push(...edits.day!.roleNames.map(r => newCell(f, r))); edits.dayDirty = true; close();
  };
  for (const [formId, buttonId] of [["person-form", "save-person"], ["role-form", "save-role"], ["flight-form", "add-flight"]]) root.querySelector<HTMLFormElement>(`#${formId}`)?.addEventListener("submit", event => { event.preventDefault(); root.querySelector<HTMLButtonElement>(`#${buttonId}`)?.click(); });
  if (dialog!.kind === "qualification") {
    const d = dialog as Extract<Dialog, { kind: "qualification" }>;
    root.querySelectorAll<HTMLInputElement>("[data-qualified-person]").forEach(input => input.onchange = () => { if (input.checked) d.selected.add(input.dataset.qualifiedPerson!); else d.selected.delete(input.dataset.qualifiedPerson!); });
    root.querySelector<HTMLButtonElement>("#select-all")!.onclick = () => { root.querySelectorAll<HTMLInputElement>("[data-qualified-person]").forEach(input => { input.checked = true; d.selected.add(input.dataset.qualifiedPerson!); }); };
    root.querySelector<HTMLButtonElement>("#select-none")!.onclick = () => { d.selected.clear(); root.querySelectorAll<HTMLInputElement>("[data-qualified-person]").forEach(input => { input.checked = false; }); };
    root.querySelector<HTMLButtonElement>("#save-qualification")!.onclick = () => {
      if (d.scope === "base") void run("save-role", { roleName: d.role, qualifiedMemberIds: [...d.selected] });
      else {
        for (const name of edits.day!.roster) {
          let c = edits.day!.memberConfigs.find(c => c.memberName === name);
          if (!c) { c = { memberName: name, eligibleRoleNames: [] }; edits.day!.memberConfigs.push(c); }
          c.eligibleRoleNames = c.eligibleRoleNames.filter(r => r !== d.role); if (d.selected.has(name)) c.eligibleRoleNames.push(d.role);
        }
        edits.dayDirty = true; close();
      }
    };
  }
}

function scheduleHtml(p: Plan | null): string {
  if (!p) return '<section class="wb-card"><h2>先建立当天计划</h2><p>到“航班”建立当天计划，在“配置”确认岗位人数和资质。</p></section>';
  if (viewingFormal && p.formalDay) p = {...p, day:p.formalDay, schedule:p.formalSchedule};
  const schedule = viewingFormal ? p.formalSchedule : p.schedule;
  const waitingForPeople = !viewingFormal && !!p.preparationNotice;
  const shownIssues = viewingFormal ? (p.formalIssues ?? []) : waitingForPeople ? p.issues.filter(i=>!["coverage_shortfall","lock_missing", ...(p.needsPreparation ? ["missing_work_time", "incomplete_work_time", "incomplete_availability"] : [])].includes(i.code)) : p.issues;
  const issueSummary = waitingForPeople ? "待完成：选择当天人员" : viewingFormal && shownIssues.length ? `带问题归档（${shownIssues.length} 项待处理）` : `缺项与冲突（${shownIssues.length}）`;
  const columns = [...(schedule?.flights.map(f => f.flightNo) ?? p.flightColumns ?? p.day.flights.map(f => f.flightNo)), ...(schedule?.tasks.some(t => !t.flightId) ? ["支援"] : [])];
  const roles = [...new Set([...p.day.roleNames, ...Object.keys(schedule?.matrix ?? {})])];
  roles.sort((a, b) => (p.day.roleNames.indexOf(a) < 0 ? Infinity : p.day.roleNames.indexOf(a)) - (p.day.roleNames.indexOf(b) < 0 ? Infinity : p.day.roleNames.indexOf(b)));
  const rows = schedule ? roles.map(role => {
    const cells = schedule.matrix[role] ?? {};
    const shared = role === "大件行李拦截" ? sharedBaggageGroups(columns, cells).filter(g => g.columns.length > 1 && !p.day.cells.some(c => c.roleName === role && g.columns.includes(c.flightNo) && (edits.cells.has(c.id) || p.cellDrafts?.[c.id]))) : [];
    return `<tr><th scope="row">${html(role)}</th>${columns.map(column => {
    const merged = shared.find(g => g.columns.includes(column));
    if (merged && merged.columns[0] !== column) return "";
    const segments = merged?.segments ?? cells[column] ?? [];
    const span = merged ? `colspan="${merged.columns.length}" data-shared-baggage="${html(JSON.stringify(merged.columns))}"` : "";
    const cell = p.day.cells.find(c => c.roleName === role && c.flightNo === column);
    const groups = segments.map(segment => {
      const task = schedule.tasks.find(t => t.id === segment.task_id)!;
      const assignments = (viewingFormal ? schedule.assignments : p.draft).filter(a => a.taskId === task.id);
      const names = assignments.map(a => schedule.members.find(m => m.id === a.memberId)?.name ?? a.memberId);
      while (names.length < task.requiredCount) names.push("待安排");
      return names.join(" / ");
    }).filter((names, index, all) => !index || names !== all[index - 1]);
    const names = groups.join(" / ");
    const cellIssues = shownIssues.filter(issue => issue.taskId === cell?.id || merged && p.day.cells.some(c => c.roleName === role && merged.columns.includes(c.flightNo) && c.id === issue.taskId) || segments.some(s => s.task_id === issue.taskId || issue.relatedTaskIds?.includes(s.task_id)));
    const hasIssue = cellIssues.length > 0;
    const hasVacancy = cellIssues.some(issue => issue.code === "coverage_shortfall");
    const hasConflict = cellIssues.some(issue => issue.code !== "coverage_shortfall");
    if (viewingFormal) {
      const pending = cell && p.formalCellDrafts?.[cell.id];
      const text = pending ? `${pending}（待完善）` : segments.map(segment => {
        const task = schedule.tasks.find(t=>t.id===segment.task_id);
        return segment ? [...segment.members,...Array(Math.max(0,(task?.requiredCount ?? 0)-segment.members.length)).fill("待安排")].join(" / ") : "";
      }).join(" / ") || (cell?.rawValue ? `${cell.rawValue}（待配置）` : cell && (cell.requiredCountOverride ?? p.day.roleConfigs.find(r=>r.roleName===role)?.requiredCount) !== 0 ? "待配置" : "");
      return `<td ${span} class="${hasConflict ? "schedule-cell-conflict" : hasVacancy ? "schedule-cell-vacancy" : ""}" title="${html(cellIssues.map(i=>i.message).join("；"))}">${html(text)}${hasIssue ? '<span class="archive-cell-warning" aria-label="归档问题"> !</span>' : ""}</td>`;
    }
    const grouped: { ids: string[]; members: string[]; key: string; count: number }[] = [];
    for (const s of segments) {
      const task = schedule.tasks.find(t => t.id === s.task_id)!;
      const members = p.draft.filter(a => a.taskId === task.id).map(a => a.memberId);
      const key = JSON.stringify([members, task.requiredCount]);
      const last = grouped.at(-1);
      if (last?.key === key) last.ids.push(task.id); else grouped.push({ ids: [task.id], members, key, count: task.requiredCount });
    }
    const contents = grouped.map(group => Array.from({ length: Math.max(group.count, group.members.length) }, (_, i) => {
      const id = group.members[i];
      const name = schedule.members.find(m => m.id === id)?.name ?? id;
      return id ? `<span class="schedule-name" contenteditable="false" draggable="${!edits.dayDirty}" data-drag-member="${html(id)}" data-task-ids="${html(JSON.stringify(group.ids))}" data-drop-member="${html(id)}"><span class="schedule-drag-handle" data-drag-handle role="button" tabindex="0" aria-label="拖动 ${html(name)}" title="按住拖动到另一岗位；拖到姓名上交换"><svg viewBox="0 0 12 18" width="10" height="15" aria-hidden="true"><path d="M3 3h1m4 0h1M3 9h1m4 0h1M3 15h1m4 0h1" stroke="currentColor" stroke-width="3" stroke-linecap="round"/></svg></span>${html(name)}</span>` : '<span class="unfilled-name" aria-label="待安排">待安排</span>';
    }).join(' / ')).join(' / ');
    const pending = cell ? edits.cells.get(cell.id) ?? p.cellDrafts?.[cell.id] : undefined;
    return `<td ${span} class="${hasIssue ? "schedule-cell-issue" : ""} ${hasConflict ? "schedule-cell-conflict" : hasVacancy ? "schedule-cell-vacancy" : ""}" data-drop-ids="${html(JSON.stringify(segments.map(s => s.task_id)))}" ${cell ? `data-cell-id="${html(cell.id)}"` : ""} title="${html(cellIssues.map(i=>i.message).join("；"))}"><div class="schedule-cell" data-edit-cell="${html(role)}" data-column="${html(column)}" ${cell && !merged ? `data-cell-text="${html(cell.id)}"` : ""} ${merged ? `data-shared-editor title="共享轮换：点击姓名或＋选人，双击此格逐段输入姓名"` : ""} contenteditable="${!edits.dayDirty && !!cell && !merged}" role="textbox" aria-label="${html(column)} ${html(role)} 人员姓名" aria-multiline="false" tabindex="0">${pending === undefined ? contents : html(pending)}</div>${!viewingFormal ? `<button class="cell-pick-button" data-pick-cell aria-label="${html(column)} ${html(role)} 选择人员" ${edits.dayDirty ? "disabled" : ""}>＋</button>` : ""}${hasIssue ? `<button class="cell-warning" data-cell-issues="${html(cell?.id ?? segments[0]?.task_id ?? "")}" aria-label="查看本格问题">!</button>` : ""}<button class="cell-detail-button" data-cell-detail="${html(role)}" data-column="${html(column)}" ${merged ? `data-shared-tasks="${html(JSON.stringify(segments.map(s => s.task_id)))}"` : ""} aria-label="${html(column)} ${html(role)} 选人及岗位详情" ${edits.dayDirty ? "disabled" : ""}>⋯</button></td>`;
  }).join("")}</tr>`; }).join("") : "";
  return `<div class="wb-title"><h2>${html(p.day.serviceDate)} · 航班排班</h2><span class="wb-tag">${html(status(p))}</span><p>格子可直接打字，回车或离开保存；姓名可拖动，拖到姓名上交换。</p></div>${waitingForPeople ? `<section id="choose-people-notice" class="wb-card preparation-next"><div><strong>下一步：选择当天人员</strong><p>${html(p.preparationNotice)}</p></div><button id="choose-duty-workers" class="btn btn-primary">选择当天人员</button></section>` : ""}${edits.dayDirty ? '<p class="wb-warning">当天资料有未保存修改，请先到航班或配置页保存；当前矩阵仍对应上次已保存输入。</p>' : ""}<div class="wb-actionbar toolbar-band schedule-toolbar"><div class="toolbar-primary"><button id="solve-draft" class="btn btn-primary" ${edits.dayDirty ? "disabled" : ""}>↻ 重新排班</button><button id="archive-next" class="btn btn-success" ${edits.dayDirty ? "disabled" : ""}>归档并排后天</button><button id="export-formal" class="btn btn-outline-success" ${!edits.dayDirty ? "" : "disabled"}>导出结果</button><button id="share-html" class="btn btn-outline-secondary" title="导出HTML" ${!edits.dayDirty ? "" : "disabled"}>HTML</button><button id="share-png" class="btn btn-outline-secondary" title="导出图片" ${!edits.dayDirty ? "" : "disabled"}>图片</button><button id="archive-only" class="btn btn-outline-secondary" ${!edits.dayDirty ? "" : "disabled"}>仅归档</button><button id="configure-half-rest" class="btn btn-outline-secondary" ${viewingFormal ? "disabled" : ""}>半休人员 ${p.day.memberAvailability.filter(a=>a.participating&&a.halfRest).length || ""}</button><button id="previous-schedule" class="btn btn-outline-secondary">上班对照</button><button id="undo-move" class="btn btn-outline-secondary" ${p.undoAvailable && !edits.cells.size ? "" : "disabled"}>撤销修改</button><div class="schedule-secondary-actions"><button id="schedule-flights">航班 / 当天人员</button><button id="phase-config">岗位性质 / 兼任</button><button id="clear-draft">清空排班</button>${p.preview ? '<button id="cancel-preview">取消本次预览</button>' : ""}<button id="adopt-draft" ${!edits.dayDirty ? "" : "disabled"}>采用并保存</button><button id="view-formal" ${p.adopted ? "" : "disabled"}>${viewingFormal ? "返回草稿编辑" : "查看正式结果"}</button><button id="toggle-schedule-layout">${scheduleLayout === "flights" ? "原保障表布局" : "按航班布局"}</button></div></div><div class="toolbar-meta"><span>${waitingForPeople ? "先核对当天人员" : p.valid ? "✓ 可归档" : `草稿 · ${p.issues.length} 项待处理`}</span></div></div><section id="schedule-issues" class="wb-card schedule-issues"><h3 class="schedule-panel-heading">${html(issueSummary)}</h3><ul>${waitingForPeople ? `<li>${html(p.preparationNotice)}</li>${shownIssues.map(i=>`<li>${html(issueText(i.message,p))}</li>`).join("")}` : !viewingFormal && p.needsPreparation && shownIssues.length ? '<li>点击“重新排班”，选择当天人员；普通岗位和正常出勤无需填写起止时间。原方案保留。</li>' : shownIssues.map(i => `<li><button class="issue-jump" data-issue-tasks="${html(JSON.stringify(i.relatedTaskIds?.length ? i.relatedTaskIds : [i.taskId].filter(Boolean)))}">${html(issueText(i.message, p))}</button></li>`).join("") || "<li>当前草稿可采用</li>"}</ul></section>${schedule?.members.some(m=>m.halfRest) ? `<p class="section-note half-rest-summary">半休：${schedule.members.filter(m=>m.halfRest).map(m=>`${html(m.name)}（${halfRestLabel(m.halfRest)}${schedule.assignments.some(a=>a.memberId===m.id)?"":"，当天无安排，保留休息"}）`).join("、")}</p>` : ""}${!viewingFormal ? paletteHtml(p, schedule, selectedPaletteTarget, placingPerson?.memberId ?? "") + '<p class="schedule-legend"><span class="legend-vacancy">琥珀色：岗位缺人</span><span class="legend-conflict">红色：安排有冲突或资料待处理</span></p>' : ""}<section class="wb-card wb-scroll schedule-sheet" data-zoom-sheet><table class="wb-table wb-matrix ${scheduleLayout === "flights" ? "flight-grid" : ""}" ${scheduleLayout === "flights" ? `style="min-width:${Math.max(660,columns.length*270)}px"` : ""}><caption>服务 ${html(p.day.serviceDate.slice(5).replace("-", "."))}</caption>${scheduleLayout === "flights" && rows ? flightGrid(rows, columns, schedule?.flights ?? p.day.flights) : `<thead><tr><th>岗位</th>${columns.map(c => `<th scope="col">${flightHeader(c, schedule?.flights ?? p.day.flights)}</th>`).join("")}</tr></thead><tbody>${rows || '<tr><td>当前资料尚未形成任务，原姓名和问题保留在归档中。</td></tr>'}</tbody>`}</table></section>${relaxedSummaryHtml(viewingFormal ? state.formalRelaxedShiftSummary : state.relaxedShiftSummary)}${scheduleExtras(state, p, schedule)}`;
}


function issueText(message: string, p: Plan): string {
  let text = message;
  for (const task of p.schedule?.tasks ?? []) {
    const flight = p.day.flights.find(f => f.id === task.flightId)?.flightNo ?? "支援";
    text = text.split(task.id).join(`${flight} ${task.roleId}`);
  }
  for (const member of p.schedule?.members ?? []) text = text.split(member.id).join(member.name);
  return text;
}


function openCellDetails(role: string, column: string, taskIds?: string[]): void {
  const p = plan()!;
  const segments = p.schedule?.matrix[role]?.[column] ?? [];
  const cell = p.day.cells.find(c => c.roleName === role && c.flightNo === column);
  dialog = !taskIds && cell && (!segments.length || p.cellDrafts?.[cell.id]) ? { kind: "cell-settings", cellId: cell.id } : { kind: "assignment", role, column, taskIds };
  dialogError = ""; render();
}

async function saveCell(cellId: string): Promise<boolean> {
  const text = edits.cells.get(cellId);
  if (text === undefined) return true;
  if (busy || edits.dayDirty || viewingFormal) return false;
  const ok = await run("edit-cell", { cellId, text });
  if (ok) {
    edits.cells.delete(cellId);
    if (plan()?.cellDrafts?.[cellId]) dialog = { kind: "cell-settings", cellId };
    render();
  }
  return ok;
}

async function flushCells(): Promise<boolean> {
  for (const id of [...edits.cells.keys()]) if (!await saveCell(id)) return false;
  return true;
}

async function openPreparation(mode: Preparation["mode"]): Promise<void> {
  if (busy || edits.dayDirty || !await flushCells()) return;
  dialog = { kind: "prepare", ...startPreparation(plan()!.preparationDay,state,mode) }; dialogError = ""; render();
}

async function performMove(source: DragSource, sourceTaskId: string | undefined, targetTaskId: string, targetMemberId?: string): Promise<void> {
  if (busy || edits.dayDirty || viewingFormal) return;
  if (source.planId !== plan()?.id || source.revision !== state.revision) { message = "班表已改变，请重新拖动"; render(); return; }
  await run("move-assignment", { memberId: source.memberId, sourceTaskId, targetTaskId, targetMemberId });
}

async function requestDrop(source: DragSource, ids: string[], targetMember?: string, cellId?: string): Promise<void> {
  if (busy || edits.dayDirty || viewingFormal) return;
  if (edits.cells.size) { message = "请先回车保存正在输入的姓名，再拖动"; render(); return; }
  if (source.revision !== state.revision || source.planId !== plan()?.id) { message = "班表已改变，请重新选择人员"; render(); return; }
  if (!ids.length) {
    if (cellId && !source.taskIds.length) {
      const name = plan()!.schedule!.members.find(m => m.id === source.memberId)!.name;
      edits.cells.set(cellId, name); await saveCell(cellId);
    } else { message = "请先点击该格详情确认岗位人数和工作方式，再移动人员"; render(); }
    return;
  }
  if (!targetMember && ids.length === 1) {
    const task = plan()!.schedule!.tasks.find(t => t.id === ids[0])!;
    const assigned = plan()!.draft.filter(a => a.taskId === task.id);
    if (assigned.length >= task.requiredCount && assigned.length === 1) targetMember = assigned[0].memberId;
  }
  const fullMultiple = ids.length === 1 && !targetMember && plan()!.draft.filter(a => a.taskId === ids[0]).length >= plan()!.schedule!.tasks.find(t => t.id === ids[0])!.requiredCount;
  if (source.taskIds.length > 1 || ids.length > 1 || fullMultiple) { dialog = { kind: "move", source, targetIds: ids, targetMember }; dialogError = ""; render(); return; }
  await performMove(source, source.taskIds[0], ids[0], targetMember);
}

function bindScheduleEditing(): void {
  root.querySelectorAll<HTMLElement>("[data-shared-editor]").forEach(el => {
    const open = () => el.closest("td")?.querySelector<HTMLButtonElement>("[data-cell-detail]")?.click();
    el.addEventListener("dblclick", open);
    el.addEventListener("keydown", event => { if (event.target === el && event.key === "Enter") { event.preventDefault(); open(); } });
  });
  root.querySelector<HTMLButtonElement>("#undo-move")?.addEventListener("click", async () => {
    if (!plan()?.undoAvailable || edits.cells.size) return;
    await run("undo-edit");
  });
  root.querySelector<HTMLButtonElement>("#clear-draft")?.addEventListener("click", async () => { if (await flushCells() && confirm("清空当前草稿中的人员安排？已采用的正式班表保留。")) await run("clear-draft"); });
  editor.bind();
  bindScheduleExtras(root, state.displayPreferences, preferences => { void run("save-display-preferences", preferences); });
  root.querySelectorAll<HTMLButtonElement>("[data-issue-tasks]").forEach(button => button.onclick = () => {
    const ids: string[] = JSON.parse(button.dataset.issueTasks!);
    root.querySelectorAll(".issue-focused").forEach(el=>el.classList.remove("issue-focused"));
    const cells = [...root.querySelectorAll<HTMLElement>("[data-drop-ids]")].filter(cell => JSON.parse(cell.dataset.dropIds!).some((id:string)=>ids.includes(id)) || ids.includes(cell.dataset.cellId ?? ""));
    cells.forEach(cell=>cell.classList.add("issue-focused")); cells[0]?.scrollIntoView({block:"center",inline:"center",behavior:"smooth"});
  });
  root.querySelectorAll<HTMLButtonElement>("[data-cell-issues]").forEach(button => button.onclick = () => {
    const td=button.closest<HTMLElement>("[data-drop-ids]")!;
    const ids: string[]=JSON.parse(td.dataset.dropIds!);
    const issues=plan()!.issues.filter(i=>i.taskId===td.dataset.cellId || ids.includes(i.taskId ?? "") || i.relatedTaskIds?.some(id=>ids.includes(id)));
    message=issues.map(i=>i.message).join("；"); warningMessage=true; render();
    const related = new Set(issues.flatMap(i=>i.relatedTaskIds ?? [i.taskId]));
    root.querySelectorAll<HTMLElement>("[data-drop-ids]").forEach(cell=>{if(JSON.parse(cell.dataset.dropIds!).some((id:string)=>related.has(id)))cell.classList.add("issue-focused");});
  });
  const clearTargets = () => root.querySelectorAll(".is-drop-target").forEach(el => el.classList.remove("is-drop-target"));
  const bindPersonnel = (scope: HTMLElement) => {
    pointerDrag.bind(scope);
    scope.querySelectorAll<HTMLElement>('[draggable="true"][data-drag-member]').forEach(el => {
      el.ondragstart = event => {
        if (busy || edits.dayDirty || viewingFormal || edits.cells.size || !event.dataTransfer) { event.preventDefault(); return; }
        dragSource = { memberId: el.dataset.dragMember!, taskIds: JSON.parse(el.dataset.taskIds!), planId: plan()!.id, revision: state.revision };
        event.dataTransfer.effectAllowed = "move"; event.dataTransfer.setData("application/x-gate-schedule", JSON.stringify(dragSource));
        el.classList.add("is-dragging");
      };
      el.ondragend = () => { dragSource = null; clearTargets(); el.classList.remove("is-dragging"); };
    });
    scope.querySelectorAll<HTMLButtonElement>("[data-palette-member]:not(:disabled)").forEach(button => button.onclick = () => {
      if (busy || edits.dayDirty || viewingFormal) return;
      placingPerson = placingPerson?.memberId === button.dataset.paletteMember ? null : { memberId: button.dataset.paletteMember!, taskIds: [], planId: plan()!.id, revision: state.revision };
      message = placingPerson ? "已选人员，请点击要安排的岗位格；再次点击姓名取消" : "已取消选择"; render();
    });
  };
  bindPersonnel(root);
  disposePalette = bindPalette(root, state, plan()!, {
    targetChanged: taskId => { selectedPaletteTarget = taskId; placingPerson = null; render(); },
    peopleChanged: bindPersonnel,
  });
  root.querySelectorAll<HTMLElement>("[data-drop-ids]").forEach(cell => {
    cell.ondragover = event => { if (dragSource && !busy && !edits.dayDirty && !viewingFormal) { event.preventDefault(); clearTargets(); cell.classList.add("is-drop-target"); if (event.dataTransfer) event.dataTransfer.dropEffect = "move"; } };
    cell.ondragleave = event => { if (!cell.contains(event.relatedTarget as Node | null)) cell.classList.remove("is-drop-target"); };
    cell.ondrop = event => {
      event.preventDefault(); clearTargets();
      const source = dragSource; dragSource = null;
      if (!source) return;
      const target = (event.target as Element).closest<HTMLElement>("[data-drop-member]");
      void requestDrop(source, JSON.parse(target?.dataset.taskIds ?? cell.dataset.dropIds!), target?.dataset.dropMember, cell.dataset.cellId);
    };
    cell.addEventListener("click", event => {
      if (!placingPerson || (event.target as Element).closest("[data-cell-detail]")) return;
      event.preventDefault();
      const source = placingPerson; placingPerson = null;
      const target = (event.target as Element).closest<HTMLElement>("[data-drop-member]");
      void requestDrop(source, JSON.parse(target?.dataset.taskIds ?? cell.dataset.dropIds!), target?.dataset.dropMember, cell.dataset.cellId);
    });
  });
}

function bindSchedule(): void {
  root.querySelector<HTMLButtonElement>("#archive-only")?.addEventListener("click", async()=>{if(await flushCells())await run("archive-only");});
  root.querySelector<HTMLButtonElement>("#toggle-schedule-layout")?.addEventListener("click", async()=>{if(await flushCells())await run("save-display-preferences",{...state.displayPreferences,layout:scheduleLayout === "flights" ? "matrix" : "flights"});});
  root.querySelector<HTMLButtonElement>("#previous-schedule")?.addEventListener("click",()=>{root.querySelector<HTMLElement>("#previous-comparison")?.scrollIntoView({block:"start",behavior:"smooth"});});
  for (const format of ["html","png"] as const) root.querySelector<HTMLButtonElement>(`#share-${format}`)?.addEventListener("click",async()=>{if(!viewingFormal && (!await flushCells() || !await run("adopt")))return;const previous=viewingFormal;try{viewingFormal=true;render();shareSheet(format,selectedDate);}catch(error){message=String(error);}finally{viewingFormal=previous;render();}});
  root.querySelector<HTMLButtonElement>("#choose-duty-workers")?.addEventListener("click",async()=>{
    await openPreparation("reschedule");
    root.querySelector("#prepare-people-heading")?.scrollIntoView({block:"start"});
  });
  root.querySelector<HTMLButtonElement>('#phase-config')?.addEventListener('click',()=>{section='config';render();const el=document.querySelector<HTMLDetailsElement>('#day-demands');if(el){el.open=true;el.scrollIntoView({block:'center'});}});

  root.querySelector<HTMLButtonElement>("#schedule-flights")?.addEventListener("click", () => navigate("flights"));
  root.querySelector<HTMLButtonElement>("#view-formal")?.addEventListener("click", () => { viewingFormal = !viewingFormal; render(); });
  if (viewingFormal) {
    for (const selector of ["#solve-draft", "#adopt-draft", "#cancel-preview", "#archive-next", "#archive-only", "#undo-move", "#clear-draft"]) root.querySelector(selector)?.remove();
    root.querySelector(".wb-title p")!.textContent = "正在查看已采用的正式快照，人员与时段和正式Excel一致。返回草稿可继续编辑。";
    root.querySelector(".wb-title .wb-tag")!.textContent = "已采用正式快照";
    root.querySelector<HTMLElement>(".wb-actionbar span")!.textContent = plan()!.formalIssues?.length ? `带问题归档 · ${plan()!.formalIssues!.length} 项待处理` : "归档结果校验通过";
    root.querySelector<HTMLElement>("#schedule-issues")!.hidden = !plan()!.formalIssues?.length;
  }
  root.querySelector<HTMLButtonElement>("#configure-half-rest")?.addEventListener("click", async () => {
    await openPreparation("reschedule"); const selector=root.querySelector<HTMLDetailsElement>("#half-rest-selector");
    if(selector){selector.open=true;selector.scrollIntoView({block:"center"});}
  });
  root.querySelector<HTMLButtonElement>("#solve-draft")?.addEventListener("click", () => { if (!edits.dayDirty) void openPreparation("reschedule"); });
  root.querySelector<HTMLButtonElement>("#archive-next")?.addEventListener("click", () => { if (!edits.dayDirty) void openPreparation("archive"); });
  bindScheduleEditing();
  root.querySelector<HTMLButtonElement>("#cancel-preview")?.addEventListener("click", async () => { if (await flushCells()) await run("cancel-preview"); });
  root.querySelector<HTMLButtonElement>("#adopt-draft")?.addEventListener("click", async () => { if (!edits.dayDirty && await flushCells() && !dialog && confirm("保存当前草稿为归档班表？原安排、空缺和校验问题都会保留，并替换此计划的归档结果。")) await run("adopt"); });
  root.querySelector<HTMLButtonElement>("#export-formal")?.addEventListener("click", async () => {
    if (!viewingFormal && (!await flushCells() || !await run("adopt"))) return;
    try {
      const response = await groupFetch(`/api/workspace/export/${encodeURIComponent(state.selectedPlanId!)}`);
      if (!response.ok) throw new Error((await response.json()).detail);
      download(await response.blob(), `登机口正式班表-${plan()!.day.serviceDate}.xlsx`);
    } catch (error) { message = String(error); render(); }
  });
  root.querySelectorAll<HTMLButtonElement>("[data-cell-detail]").forEach(button => button.onclick = async () => {
    if (busy || edits.dayDirty || viewingFormal) return;
    if (!await flushCells()) return;
    openCellDetails(button.dataset.cellDetail!, button.dataset.column!, button.dataset.sharedTasks ? JSON.parse(button.dataset.sharedTasks) : undefined);
  });
  root.querySelectorAll<HTMLSelectElement>("[data-assignment-task]").forEach(input => input.onchange = async () => {
    if (busy || edits.dayDirty) return;
    await run("edit-slot", {taskId:input.dataset.assignmentTask!,slot:Number(input.dataset.slot),memberId:input.value});
  });
  root.querySelectorAll<HTMLSelectElement>("[data-assignment-task]").forEach(input => {
    const p=plan()!, revision=state.revision;
    input.disabled = true;
    input.title = "正在检查该位置的人选…";
    void groupFetch(`/api/workspace/candidates/${encodeURIComponent(p.id)}/${encodeURIComponent(input.dataset.assignmentTask!)}?slot=${input.dataset.slot}`)
      .then(async response=>{if(!response.ok)throw Error("候选检查未完成");return response.json() as Promise<Candidate[]>;})
      .then(items=>{
        if(!input.isConnected || state.revision!==revision || plan()?.id!==p.id)return;
        if(!Array.isArray(items) || !items.length || items.some(item=>item.revision!==revision))throw Error("岗位资料或班表版本已改变，请重新读取后检查");
        for(const option of input.options){
          if(!option.value)continue;
          const candidate=items.find(i=>i.id===option.value);
          option.disabled=!(candidate?.available || candidate?.manualAllowed);
          if(candidate) option.textContent=`${candidate.name}（${!candidate.available && candidate.manualAllowed ? "填入后报警" : candidate.label ?? (candidate.available ? "可安排" : "不可安排")}${candidate.reasons.length ? `：${candidate.reasons.join("；")}` : ""}）`;
        }
        input.disabled=busy||edits.dayDirty; input.title="已按该位置核对人选";
      })
      .catch(error=>{if(input.isConnected){input.disabled=true;input.title=String(error);input.insertAdjacentHTML("afterend",'<small class="text-danger" role="alert">人选检查未完成，请关闭后重试。</small>');}});
  });
  let candidateQueryGeneration = 0;
  root.querySelectorAll<HTMLButtonElement>("[data-candidates]").forEach(button => button.onclick = async () => {
    const generation = ++candidateQueryGeneration;
    const revision=state.revision, planId=plan()!.id, result=root.querySelector<HTMLElement>("#candidate-result")!;
    result.textContent="正在核对人选…";
    try {
      const response = await groupFetch(`/api/workspace/candidates/${encodeURIComponent(planId)}/${encodeURIComponent(button.dataset.candidates!)}`);
      if (!response.ok) throw new Error("候选查询失败");
      const items = await response.json() as Candidate[];
      if(generation!==candidateQueryGeneration || !button.isConnected || !result.isConnected || revision!==state.revision || plan()?.id!==planId)return;
      if(items.some(item=>item.revision!==revision))throw Error("班表已改变，请重新检查人选");
      result.innerHTML = items.length ? `<h3>该任务首个人员位置的人选</h3><ul>${items.map(i => `<li>${html(i.name)}：${html(i.label ?? (i.available ? "可安排" : "不可安排"))}${i.reasons.length ? ` · ${i.reasons.map(html).join("；")}` : ""}</li>`).join("")}</ul>` : "岗位资料尚未完善，暂时不能判断人选。";
    } catch (error) { if(generation===candidateQueryGeneration && result.isConnected && revision===state.revision)result.textContent=String(error); }
  });
}


function download(blob: Blob, name: string): void {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob); link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}


window.addEventListener("beforeunload", event => { if (hasUnsaved()) { event.preventDefault(); event.returnValue = ""; } });
async function start(): Promise<void> {
  try {
    const response = await fetch("/api/groups");
    if (!response.ok) throw new Error("读取班组失败，请检查数据或刷新页面");
    const groups = await response.json();
    activeGroup = groups.activeGroupId === "B" ? "B" : "A";
    selectApiGroup(activeGroup);
    state = await responseJson(await groupFetch("/api/workspace"));
    if (state.selectedPlanId) selectedDate = state.plans[state.selectedPlanId].day.serviceDate;
    resetEditable();
    section = plan() ? "schedule" : "config";
    render();
  } catch (error) {
    root.innerHTML = `<main class="app-main"><section class="workspace-section"><h1>数据需要检查或恢复</h1><p>${html(String(error))}</p><p>原文件保留。先下载原库，再在独立项目数据目录恢复已知完整备份。</p><a class="btn btn-outline-primary" href="/api/workspace/raw-database">下载原数据库</a><button id="reload" class="btn btn-primary">重新读取</button></section></main>`;
    root.querySelector<HTMLButtonElement>("#reload")!.onclick = () => location.reload();
  }
}
void start();
