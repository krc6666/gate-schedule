type Flight = { id: string; flightNo: string; windowStart: string; windowEnd: string };
type Member = { id: string; name: string; eligibleRoleIds: string[] };
type Task = {
  id: string;
  flightId: string | null;
  roleId: string;
  start: string;
  end: string;
  requiredCount: number;
  taskKind: string;
  lockedMemberIds: string[];
  serviceStage: "arrival" | "boarding" | null;
};
type Assignment = { taskId: string; memberId: string };
type MemberPath = { memberName: string; taskId: string; roleId: string; start: string; end: string };
type Issue = { code: string; message: string; taskId: string | null; memberId: string | null };
type ExternalSupport = { taskId: string; count: number; sourceLabel: string };
type Schedule = {
  serviceDate: string;
  flights: Flight[];
  members: Member[];
  tasks: Task[];
  assignments: Assignment[];
  assignmentSource: "fixture" | "manual" | "automatic";
  externalSupportRequirements: ExternalSupport[];
  memberPaths: Record<string, MemberPath[]>;
};
type Validation = { valid: boolean; issues: Issue[]; memberPaths: Record<string, MemberPath[]> };
type ImportedCell = {
  id: string;
  roleName: string;
  flightId: string;
  flightNo: string;
  rawValue: string;
  internalNames: string[];
  externalLabels: string[];
  semantics: "empty" | "single" | "simultaneous" | "rotation" | "external" | "ambiguous";
  workStart: string | null;
  workEnd: string | null;
  requiredCountOverride: number | null;
  fixedMemberNames: string[];
  effectiveRequiredCount: number | null;
};
type ImportedRoleConfig = { roleName: string; requiredCount: number | null };
type ImportedMemberConfig = { memberName: string; eligibleRoleNames: string[] };
type ImportedMemberAvailability = { memberName: string; participating: boolean; availableFrom: string | null; availableUntil: string | null };
type ImportedOccupancy = { id: string; label: string; memberNames: string[]; start: string; end: string };
type ImportedRemark = { kind: "support" | "arrival" | "other"; rawText: string; people: string[]; time: string | null };
type FlightHandoffSuggestion = {
  sourceFlightNo: string;
  targetFlightNo: string;
  suggestedFrom: string;
  suggestedUntil: string;
  candidateNames: string[];
  boardingRoles: string[];
};
type ImportedDay = {
  valid: boolean;
  issues: Issue[];
  serviceDate: string;
  title: string;
  flights: Flight[];
  roleNames: string[];
  cells: ImportedCell[];
  roleConfigs: ImportedRoleConfig[];
  memberNames: string[];
  memberConfigs: ImportedMemberConfig[];
  memberAvailability: ImportedMemberAvailability[];
  extraOccupancies: ImportedOccupancy[];
  remarksReviewed: boolean;
  transitionMinutes: number;
  remarks: ImportedRemark[];
  flightHandoffSuggestions: FlightHandoffSuggestion[];
};
type BaggageRotationSegment = { start: string; end: string };
type BaggageRotationSettings = {
  enabled: boolean;
  intervalMinutes: number;
  segments: BaggageRotationSegment[];
};
type SolveResult = Schedule & {
  status: "OPTIMAL" | "FEASIBLE" | "INFEASIBLE" | "TIMED_OUT" | "MODEL_INVALID";
  diagnostics: string[];
  externalSupportByTask: Record<string, number>;
  validationIssues: Issue[];
  accepted: boolean;
};

const appElement = document.querySelector<HTMLDivElement>("#app");
if (!appElement) throw new Error("找不到页面根节点");
const app: HTMLDivElement = appElement;

const roleNames: Record<string, string> = {
  supervisor: "督导",
  receive: "接机",
  baggage: "大件行李拦截",
  verify: "人证核对",
  support: "支援",
};

let schedule: Schedule;
let draft: Assignment[] = [];
let validation: Validation = { valid: true, issues: [], memberPaths: {} };
let selectedMemberId = "";
let saveMessage = "";
let importedDay: ImportedDay | null = null;
let importedDraft: ImportedCell[] = [];
let importSaveMessage = "";
let importedSolveMessage = "";
let importedSolveResult: {
  status?: string;
  accepted?: boolean;
  diagnostics?: string[];
  validationIssues?: Issue[];
  assignments?: Assignment[];
  tasks?: Task[];
  members?: Member[];
  flights?: Flight[];
} | null = null;
let baggageRotationSettings: BaggageRotationSettings = { enabled: true, intervalMinutes: 60, segments: [] };
let baggageRotationMessage = "";
let solveMessage = "";
let solveDiagnostics: string[] = [];
let externalSupport: Record<string, number> = {};

function escapeHtml(value: string): string {
  return value.replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character] ?? character);
}

function roleName(roleId: string): string {
  return roleNames[roleId] ?? roleId;
}

function issuesFor(taskId: string): Issue[] {
  return validation.issues.filter((issue) => issue.taskId === taskId);
}

function importedIssuesFor(cellId: string): Issue[] {
  return importedDay?.issues.filter((issue) => issue.taskId === cellId) ?? [];
}

function assignmentSourceLabel(source: Schedule["assignmentSource"]): string {
  return { fixture: "演示夹具", manual: "手工保存", automatic: "自动排班" }[source];
}

function changeAssignment(taskId: string, assignmentIndex: number, memberId: string): void {
  const assigned = draft.filter((item) => item.taskId === taskId);
  assigned.splice(assignmentIndex, 1, ...(memberId ? [{ taskId, memberId }] : []));
  draft = [...draft.filter((item) => item.taskId !== taskId), ...assigned];
}

async function requestValidation(): Promise<void> {
  const response = await fetch("/api/validate-manual-assignments", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ assignments: draft }),
  });
  validation = await response.json() as Validation;
}

function importedPayload(): {
  cells: Array<{ id: string; rawValue: string; semantics: string; workStart: string; workEnd: string; requiredCountOverride: number | null; fixedMemberNames: string[] }>;
  roleConfigs: ImportedRoleConfig[];
  memberConfigs: ImportedMemberConfig[];
  memberAvailability: ImportedMemberAvailability[];
  extraOccupancies: ImportedOccupancy[];
  remarksReviewed: boolean;
  transitionMinutes: number;
} {
  return {
    cells: importedDraft.map((cell) => ({
      id: cell.id,
      rawValue: cell.rawValue,
      semantics: cell.semantics,
      workStart: cell.workStart ?? "",
      workEnd: cell.workEnd ?? "",
      requiredCountOverride: cell.requiredCountOverride,
      fixedMemberNames: cell.fixedMemberNames,
    })),
    roleConfigs: importedDay?.roleConfigs ?? [],
    memberConfigs: importedDay?.memberConfigs ?? [],
    memberAvailability: importedDay?.memberAvailability ?? [],
    extraOccupancies: importedDay?.extraOccupancies ?? [],
    remarksReviewed: importedDay?.remarksReviewed ?? false,
    transitionMinutes: importedDay?.transitionMinutes ?? 0,
  };
}

async function requestImportedValidation(): Promise<void> {
  importedSolveResult = null;
  const response = await fetch("/api/imported-day/validate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(importedPayload()),
  });
  importedDay = await response.json() as ImportedDay;
  importedDraft = importedDay.cells;
}

function taskCell(task: Task): string {
  const assigned = draft.filter((item) => item.taskId === task.id);
  const issues = issuesFor(task.id);
  const external = schedule.externalSupportRequirements.filter((item) => item.taskId === task.id);
  const selectors = Array.from({ length: Math.max(task.requiredCount, assigned.length) }, (_, index) => {
    const selected = assigned[index]?.memberId ?? "";
    const options = ["<option value=\"\">未分配</option>"]
      .concat(schedule.members.map((member) => `<option value="${escapeHtml(member.id)}"${member.id === selected ? " selected" : ""}>${escapeHtml(member.name)}</option>`))
      .join("");
    return `<label class="assignment-person">${task.requiredCount > 1 ? `<span>第 ${index + 1} 人</span>` : ""}<select aria-label="${escapeHtml(roleName(task.roleId))} ${task.start} 第 ${index + 1} 人" data-task-id="${escapeHtml(task.id)}" data-assignment-index="${index}">${options}</select></label>`;
  }).join("");
  const messages = issues.map((issue) => `<li>${escapeHtml(issue.message)}</li>`).join("");
  const support = external.map((item) => `<span class="external">外援：${escapeHtml(item.sourceLabel)} × ${item.count}</span>`).join("");
  return `
    <div class="assignment ${issues.length ? "has-issue" : ""}">
      <span class="time">${task.start} - ${task.end}</span>
      ${selectors}
      ${support}
      ${messages ? `<ul class="issues">${messages}</ul>` : ""}
    </div>`;
}

function tableRows(columns: Array<{ id: string | null; title: string; subtitle: string }>): string {
  const roleIds = [...new Set(schedule.tasks.map((task) => task.roleId))];
  return roleIds.map((roleId) => {
    const cells = columns.map((column) => {
      const tasks = schedule.tasks.filter((task) => task.roleId === roleId && task.flightId === column.id);
      return `<td>${tasks.map(taskCell).join("")}</td>`;
    }).join("");
    return `<tr><th scope="row">${escapeHtml(roleName(roleId))}</th>${cells}</tr>`;
  }).join("");
}

function render(): void {
  if (importedDay) {
    renderImportedDay();
    return;
  }
  const columns = [
    ...schedule.flights.map((flight) => ({ id: flight.id, title: flight.flightNo, subtitle: `${flight.windowStart} - ${flight.windowEnd}` })),
    ...(schedule.tasks.some((task) => task.flightId === null) ? [{ id: null, title: "支援", subtitle: "非航班任务" }] : []),
  ];
  const path = validation.memberPaths[selectedMemberId] ?? schedule.memberPaths[selectedMemberId] ?? [];
  const statusClass = validation.valid ? "valid" : "invalid";
  const statusText = validation.valid ? (solveMessage || saveMessage || `当前为${assignmentSourceLabel(schedule.assignmentSource)}`) : "存在冲突，不能保存";
  app.innerHTML = `
    <header class="topbar">
      <div><h1>登机口保障排班</h1><p>${escapeHtml(schedule.serviceDate)} · ${schedule.assignmentSource === "fixture" ? "合成演示数据" : assignmentSourceLabel(schedule.assignmentSource)}</p></div>
      <div class="topbar-actions"><label class="file-button">导入 Excel<input id="excel-file" type="file" accept=".xlsx" /></label><input id="excel-service-date" class="date-input" type="date" value="${escapeHtml(schedule.serviceDate)}" /><button id="solve" type="button">自动排班</button><button id="backup" type="button">导出备份</button><label class="file-button">恢复备份<input id="backup-file" type="file" accept=".json" /></label><span class="status ${statusClass}">${escapeHtml(statusText)}</span><button id="save" type="button" ${validation.valid ? "" : "disabled"}>保存修改</button></div>
    </header>
    <main class="workspace">
      <section class="schedule-area" aria-label="岗位航班排班表">
        <div class="notice">${schedule.assignmentSource === "fixture" ? "开始使用：先选择日期并导入当天 Excel；继续编辑请点“打开已保存日表”。当前为演示数据，0910 支援时段仅为测试夹具。" : "可逐人选择替换人员，多人岗位的其他搭档会保留。调整通过校验后点击“保存修改”；导出 Excel 请点“打开已保存日表”。"}</div>
        ${solveDiagnostics.length || Object.keys(externalSupport).length ? `<section class="solve-result"><h2>自动排班结果</h2>${solveDiagnostics.length ? `<ul>${solveDiagnostics.map((message) => `<li>${escapeHtml(message)}</li>`).join("")}</ul>` : ""}${Object.keys(externalSupport).length ? `<p>外援缺口：${Object.entries(externalSupport).map(([taskId, count]) => `${escapeHtml(taskId)} × ${count}`).join("；")}</p>` : ""}</section>` : ""}
        <div class="table-wrap"><table>
          <thead><tr><th>岗位</th>${columns.map((column) => `<th>${escapeHtml(column.title)}<small>${escapeHtml(column.subtitle)}</small></th>`).join("")}</tr></thead>
          <tbody>${tableRows(columns)}</tbody>
        </table></div>
      </section>
      <aside class="member-panel" aria-label="人员当天任务">
        <label for="member-select">人员当天任务</label>
        <select id="member-select">${schedule.members.map((member) => `<option value="${escapeHtml(member.id)}"${member.id === selectedMemberId ? " selected" : ""}>${escapeHtml(member.name)}</option>`).join("")}</select>
        <ol class="path">${path.length ? path.map((item) => `<li><time>${item.start} - ${item.end}</time><strong>${escapeHtml(roleName(item.roleId))}</strong><span>${escapeHtml(item.taskId)}</span></li>`).join("") : "<li class=\"empty\">当天没有任务</li>"}</ol>
      </aside>
    </main>`;

  app.querySelectorAll<HTMLSelectElement>("[data-task-id]").forEach((select) => {
    select.addEventListener("change", async () => {
      changeAssignment(select.dataset.taskId ?? "", Number(select.dataset.assignmentIndex), select.value);
      saveMessage = "";
      await requestValidation();
      render();
    });
  });
  app.querySelector<HTMLSelectElement>("#member-select")?.addEventListener("change", (event) => {
    selectedMemberId = (event.target as HTMLSelectElement).value;
    render();
  });
  app.querySelector<HTMLButtonElement>("#save")?.addEventListener("click", save);
  app.querySelector<HTMLButtonElement>("#solve")?.addEventListener("click", solveSchedule);
  app.querySelector<HTMLButtonElement>("#backup")?.addEventListener("click", downloadBackup);
  const openSavedButton = document.createElement("button");
  openSavedButton.type = "button";
  openSavedButton.textContent = "打开已保存日表";
  openSavedButton.addEventListener("click", async () => {
    const response = await fetch("/api/imported-day/open", { method: "POST" });
    if (!response.ok) {
      saveMessage = "暂无已保存的日表，请先导入 Excel";
      render();
      return;
    }
    importedDay = await response.json() as ImportedDay;
    importedSolveResult = null;
    importedDraft = importedDay.cells;
    importSaveMessage = "已打开本机日表";
    render();
  });
  app.querySelector(".topbar-actions")?.append(openSavedButton);
  app.querySelector<HTMLInputElement>("#excel-file")?.addEventListener("change", async (event) => {
    const file = (event.target as HTMLInputElement).files?.[0];
    const serviceDate = app.querySelector<HTMLInputElement>("#excel-service-date")?.value || schedule.serviceDate;
    if (file) await importExcel(file, serviceDate);
  });
  app.querySelector<HTMLInputElement>("#backup-file")?.addEventListener("change", async (event) => {
    const file = (event.target as HTMLInputElement).files?.[0];
    if (file) await restoreBackup(file);
  });
}

async function save(): Promise<void> {
  const response = await fetch("/api/manual-assignments", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ assignments: draft }),
  });
  const data = await response.json();
  if (!response.ok) {
    validation = { valid: false, issues: data.issues, memberPaths: validation.memberPaths };
    render();
    return;
  }
  schedule = data as Schedule;
  draft = schedule.assignments;
  validation = { valid: true, issues: [], memberPaths: schedule.memberPaths };
  saveMessage = "已保存到本机";
  solveMessage = "";
  render();
}

async function solveSchedule(): Promise<void> {
  solveMessage = "正在使用现有硬约束求解";
  render();
  const response = await fetch("/api/solve", { method: "POST" });
  const result = await response.json() as SolveResult;
  if (result.accepted) {
    if (!confirm("自动预览已通过独立校验。确定采用并保存？取消会保留原班表。")) {
      solveMessage = "已取消自动预览，原班表保留";
      render();
      return;
    }
    const confirmed = await fetch("/api/solve/confirm", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ previewId: (result as SolveResult & { previewId: string }).previewId }) });
    if (!confirmed.ok) { solveMessage = "采用失败，原班表保留，请重新读取"; render(); return; }
  }
  schedule = result;
  draft = schedule.assignments;
  validation = { valid: result.validationIssues.length === 0, issues: result.validationIssues, memberPaths: schedule.memberPaths };
  solveDiagnostics = result.diagnostics;
  externalSupport = result.externalSupportByTask;
  solveMessage = result.accepted ? "已通过独立校验并采用" : "未找到合法安排，原班表保留";
  render();
}

async function downloadBackup(): Promise<void> {
  const response = await fetch("/api/backup");
  const blob = new Blob([await response.text()], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "登机口保障排班-完整备份.json";
  link.click();
  URL.revokeObjectURL(link.href);
}

async function restoreBackup(file: File): Promise<void> {
  const response = await fetch("/api/restore-backup", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: await file.text(),
  });
  if (!response.ok) {
    solveMessage = "备份无效，当前本机数据未被覆盖";
    render();
    return;
  }
  schedule = await response.json() as Schedule;
  await refreshBaggageRotationSettings();
  draft = schedule.assignments;
  validation = { valid: true, issues: [], memberPaths: schedule.memberPaths };
  solveMessage = "已从完整备份恢复到本机";
  solveDiagnostics = [];
  externalSupport = {};
  importedDay = null;
  render();
}

function importedCellFor(roleName: string, flightId: string): ImportedCell {
  const cell = importedDraft.find((item) => item.roleName === roleName && item.flightId === flightId);
  if (!cell) throw new Error("导入表格缺少单元格");
  return cell;
}

function importedCellHtml(cell: ImportedCell): string {
  const issues = importedIssuesFor(cell.id);
  const requiredCount = cell.effectiveRequiredCount;
  const baggageFlight = cell.roleName === "大件行李拦截"
    ? importedDay?.flights.find((flight) => flight.id === cell.flightId)
    : undefined;
  const label: Record<ImportedCell["semantics"], string> = {
    empty: "空岗", single: "单人", simultaneous: "同时搭档", rotation: "分时轮换", external: "内部 + 外援", ambiguous: "待确认",
  };
  const choices = cell.semantics === "ambiguous"
    ? `<select data-import-semantics="${escapeHtml(cell.id)}"><option value="">请选择斜杠语义</option><option value="simultaneous">同时搭档</option><option value="rotation">分时轮换</option></select>`
    : `<span class="semantic ${cell.semantics}">${label[cell.semantics]}</span>`;
  const workTimeControls = cell.roleName === "大件行李拦截"
    ? `<p class="work-time-note">按航班时间 ${escapeHtml(baggageFlight?.windowStart ?? "-")} - ${escapeHtml(baggageFlight?.windowEnd ?? "-")} 纳入大件覆盖；岗位时间不参与自动排班。</p>`
    : `<div class="work-time-controls">
      <label><span>开始</span><input type="time" data-work-start="${escapeHtml(cell.id)}" value="${escapeHtml(cell.workStart ?? "")}" aria-label="${escapeHtml(cell.roleName)} ${escapeHtml(cell.flightNo)} 开始时间" /></label>
      <label><span>结束</span><input type="time" data-work-end="${escapeHtml(cell.id)}" value="${escapeHtml(cell.workEnd ?? "")}" aria-label="${escapeHtml(cell.roleName)} ${escapeHtml(cell.flightNo)} 结束时间" /></label>
    </div>`;
  return `<div class="assignment ${issues.length ? "has-issue" : ""}">
    <input data-import-value="${escapeHtml(cell.id)}" value="${escapeHtml(cell.rawValue)}" aria-label="${escapeHtml(cell.roleName)} ${escapeHtml(cell.flightNo)}" />
    ${choices}${issues.length ? `<ul class="issues">${issues.map((issue) => `<li>${escapeHtml(issue.message)}</li>`).join("")}</ul>` : ""}
    <span class="role-demand">${requiredCount === null ? "人数未设置" : requiredCount === 0 ? "配置：不需要安排" : `同时需要 ${requiredCount} 人`}</span>
    <details class="staffing-override"${cell.requiredCountOverride === null ? "" : " open"}>
      <summary>${cell.requiredCountOverride === null ? "特殊航班人数例外" : "已设置本航班人数例外"}</summary>
      <label>本航班人数<input type="number" min="0" step="1" data-count-override="${escapeHtml(cell.id)}" value="${cell.requiredCountOverride === null ? "" : cell.requiredCountOverride}" placeholder="沿用岗位配置" aria-label="${escapeHtml(cell.roleName)} ${escapeHtml(cell.flightNo)} 人数例外" /></label>
      <small>留空沿用岗位配置；0 表示不需要。</small>
    </details>
    <details class="fixed-members"${cell.fixedMemberNames.length ? " open" : ""}>
      <summary>${cell.fixedMemberNames.length ? `已固定 ${cell.fixedMemberNames.length} 人` : "固定人员（可选）"}</summary>
      <small>原名单只作参考；勾选后自动排班会尽量保留此人。</small>
      ${cell.internalNames.length ? cell.internalNames.map((name) => `<label><input type="checkbox" data-fixed-member="${escapeHtml(cell.id)}" data-fixed-name="${escapeHtml(name)}"${cell.fixedMemberNames.includes(name) ? " checked" : ""} />${escapeHtml(name)}</label>`).join("") : "<span>没有可固定的内部人员</span>"}
    </details>
    ${workTimeControls}
  </div>`;
}

async function refreshBaggageRotationSettings(): Promise<void> {
  const response = await fetch("/api/baggage-rotation-settings");
  if (!response.ok) return;
  baggageRotationSettings = await response.json() as BaggageRotationSettings;
}

async function saveBaggageRotationSettings(): Promise<void> {
  const enabled = app.querySelector<HTMLInputElement>("#baggage-rotation-enabled")?.checked ?? true;
  const intervalMinutes = Number(app.querySelector<HTMLInputElement>("#baggage-rotation-minutes")?.value);
  if (!Number.isInteger(intervalMinutes) || intervalMinutes < 1 || intervalMinutes > 1440) {
    baggageRotationMessage = "轮换分钟数需在 1 到 1440 之间";
    renderImportedDay();
    return;
  }

  const response = await fetch("/api/baggage-rotation-settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled, intervalMinutes }),
  });
  if (!response.ok) {
    baggageRotationMessage = "轮换设置未保存";
    renderImportedDay();
    return;
  }
  baggageRotationSettings = await response.json() as BaggageRotationSettings;
  baggageRotationMessage = "设置已保存";
  renderImportedDay();
}

function renderImportedDay(): void {
  if (!importedDay) return;
  const day = importedDay;
  const saveClass = day.valid ? "valid" : "invalid";
  const status = day.valid ? (importSaveMessage || "导入日表待保存") : "日表有待处理问题，不能保存";
  const solvePanel = importedSolveResult
    ? `<section class="solve-result"><h2 class="rotation-title">自动排班结果</h2><p>${escapeHtml(importedSolveResult.accepted ? `已生成${importedSolveResult.status ?? ""}结果（预览）` : `未采用：${(importedSolveResult.diagnostics ?? []).join("；")}`)}</p>${importedSolveResult.accepted ? `<div class="table-wrap"><table><thead><tr><th>航班</th><th>岗位</th><th>时间</th><th>安排人员</th><th>固定</th></tr></thead><tbody>${(importedSolveResult.tasks ?? []).map((task) => {
      const flight = importedSolveResult?.flights?.find((item) => item.id === task.flightId);
      const names = (importedSolveResult?.assignments ?? []).filter((item) => item.taskId === task.id).map((item) => importedSolveResult?.members?.find((member) => member.id === item.memberId)?.name ?? item.memberId);
      return `<tr><td>${escapeHtml(flight?.flightNo ?? "额外占用")}</td><td>${escapeHtml(roleName(task.roleId))}</td><td>${task.start} - ${task.end}</td><td>${names.map(escapeHtml).join("、")}</td><td>${task.lockedMemberIds.map(escapeHtml).join("、") || "-"}</td></tr>`;
    }).join("")}</tbody></table></div><button type="button" id="confirm-imported-schedule">采用并保存这个排班</button>` : ""}${importedSolveResult.validationIssues?.length ? `<ul class="issues">${importedSolveResult.validationIssues.map((issue) => `<li>${escapeHtml(issue.message)}</li>`).join("")}</ul>` : ""}</section>`
    : "";
  const roleControls = day.roleNames.map((name) => {
    const count = day.roleConfigs.find((config) => config.roleName === name)?.requiredCount;
    return `<label class="role-config-item"><span>${escapeHtml(name)}</span><input type="number" min="0" step="1" data-role-count="${escapeHtml(name)}" value="${count == null ? "" : count}" placeholder="未设置" aria-label="${escapeHtml(name)} 同时需要人数" /></label>`;
  }).join("");
  const memberControls = day.memberNames.map((name) => {
    const roles = day.memberConfigs.find((config) => config.memberName === name)?.eligibleRoleNames ?? [];
    const attendance = day.memberAvailability.find((entry) => entry.memberName === name);
    return `<fieldset class="member-config-item"><legend>${escapeHtml(name)}</legend>
      <label class="attendance-toggle"><input type="checkbox" data-attendance-member="${escapeHtml(name)}" data-attendance-field="participating"${attendance?.participating ? " checked" : ""} />今天参加排班</label>
      <div class="work-time-controls">
        <label><span>到岗</span><input type="time" data-attendance-member="${escapeHtml(name)}" data-attendance-field="availableFrom" value="${escapeHtml(attendance?.availableFrom ?? "")}" aria-label="${escapeHtml(name)} 到岗时间" /></label>
        <label><span>离岗</span><input type="time" data-attendance-member="${escapeHtml(name)}" data-attendance-field="availableUntil" value="${escapeHtml(attendance?.availableUntil ?? "")}" aria-label="${escapeHtml(name)} 离岗时间" /></label>
      </div>
      <p class="member-roles-title">可做岗位</p>
      ${day.roleNames.map((role) => `<label><input type="checkbox" data-member-name="${escapeHtml(name)}" data-member-role="${escapeHtml(role)}"${roles.includes(role) ? " checked" : ""} />${escapeHtml(role)}</label>`).join("")}</fieldset>`;
  }).join("");
  const occupancyControls = day.extraOccupancies.map((occupancy) => `<fieldset class="occupancy-item" data-occupancy="${escapeHtml(occupancy.id)}">
    <label>事项<input type="text" data-occupancy-field="label" value="${escapeHtml(occupancy.label)}" /></label>
    <label>开始<input type="time" data-occupancy-field="start" value="${escapeHtml(occupancy.start)}" /></label>
    <label>结束<input type="time" data-occupancy-field="end" value="${escapeHtml(occupancy.end)}" /></label>
    <button type="button" data-remove-occupancy="${escapeHtml(occupancy.id)}" aria-label="删除额外占用">删除</button>
    <div class="occupancy-members">${day.memberNames.map((name) => `<label><input type="checkbox" data-occupancy-member="${escapeHtml(occupancy.id)}" data-member-name="${escapeHtml(name)}"${occupancy.memberNames.includes(name) ? " checked" : ""} />${escapeHtml(name)}</label>`).join("")}</div>
  </fieldset>`).join("");
  const tableRows = day.roleNames.map((roleName) => `<tr><th scope="row">${escapeHtml(roleName)}</th>${day.flights.map((flight) => `<td>${importedCellHtml(importedCellFor(roleName, flight.id))}</td>`).join("")}</tr>`).join("");
  const rotationSegments = baggageRotationSettings.enabled
    ? baggageRotationSettings.segments.length
      ? `<ol class="rotation-segments">${baggageRotationSettings.segments.map((segment) => `<li><time>${segment.start} - ${segment.end}</time><span>1 人</span></li>`).join("")}</ol>`
      : "<p class=\"rotation-empty\">当前没有航班覆盖时段</p>"
    : "<p class=\"rotation-empty\">自动轮换已关闭，仍安排一人覆盖每段有需求的时间</p>";
  const handoffSuggestions = day.flightHandoffSuggestions.length
    ? `<ul class="handoff-list">${day.flightHandoffSuggestions.map((suggestion) => `<li><div><strong>${escapeHtml(suggestion.sourceFlightNo)} 接机 → ${escapeHtml(suggestion.targetFlightNo)} 登机</strong><time>${escapeHtml(suggestion.suggestedFrom)} - ${escapeHtml(suggestion.suggestedUntil)}</time></div><p>候选人员：${suggestion.candidateNames.map(escapeHtml).join("、")}</p><p>可核对岗位：${suggestion.boardingRoles.map(escapeHtml).join("、")}</p></li>`).join("")}</ul>`
    : "<p class=\"rotation-empty\">当前没有符合条件且有接机名单的航班组合</p>";
  app.innerHTML = `
    <header class="topbar">
      <div><h1>登机口保障排班</h1><p>${escapeHtml(day.serviceDate)} · ${escapeHtml(day.title)} · 导入日表</p></div>
      <div class="topbar-actions"><button id="back-to-schedule" type="button">返回手工表</button><a class="download ${day.valid ? "" : "disabled"}" href="/api/export-excel">导出 Excel</a><span class="status ${saveClass}">${escapeHtml(status)}</span><button id="solve-import" type="button">生成自动排班预览</button><button id="save-import" type="button" ${day.valid ? "" : "disabled"}>确认保存</button></div>
    </header>
    <main class="workspace imported-workspace">
      <section class="schedule-area" aria-label="导入岗位航班表">
        <div class="notice">可为每个航班、每个岗位填写当天实际开始和结束时间；留空表示未填写，不会自动套用航班时间。其他带斜杠人员须确认语义。</div>
        <section class="role-config" aria-label="岗位配置">
          <h2 class="rotation-title">岗位配置</h2>
          <p>按岗位设置同时需要人数，同一日表的各航班共用。空白为未设置，0 为明确不需要；多人轮换的名单不等于同时需要人数。修改后请点“确认保存”。</p>
          <div class="role-config-grid">${roleControls}</div>
          <ul id="role-config-errors" class="issues" role="status">${day.issues.filter((issue) => !issue.taskId).map((issue) => `<li>${escapeHtml(issue.message)}</li>`).join("")}</ul>
        </section>
        ${solvePanel}
        <details class="member-config">
          <summary>人员配置：可做岗位与当天出勤</summary>
          <p>逐人勾选可做岗位，并选中今天参加排班的人、填写到岗和离岗时间。未勾选不表示全天有空；新一天的出勤不会沿用。修改后请点“确认保存”。</p>
          <div class="member-config-grid">${memberControls || "暂无内部人员名单"}</div>
        </details>
        <section class="role-config extra-occupancy" aria-label="其他占用和走动预留">
          <h2 class="rotation-title">其他占用和走动预留</h2>
          <p>需要核对的其他工作可以填在这里，避免同一时间重复安排。走动预留默认 0 分钟；只有你填了时间才限制不同航班之间的间隔。</p>
          <label>跨航班走动预留（分钟）<input type="number" min="0" max="180" step="1" data-transition-minutes value="${day.transitionMinutes}" /></label>
          ${occupancyControls || "<p>暂无额外占用</p>"}
          <button type="button" id="add-occupancy">添加额外占用</button>
          ${day.remarks.some((remark) => remark.kind === "support") ? `<label class="attendance-toggle"><input type="checkbox" data-remarks-reviewed${day.remarksReviewed ? " checked" : ""} />已核对支援备注；需要占用的人员和时段已录入上方</label>` : ""}
        </section>
        <section class="rotation-settings" aria-label="大件行李拦截轮换设置">
          <h2 class="rotation-title">大件行李拦截轮换时段</h2>
          <div class="rotation-controls">
            <label class="rotation-toggle"><input id="baggage-rotation-enabled" type="checkbox"${baggageRotationSettings.enabled ? " checked" : ""} />启用自动轮换</label>
            <label class="rotation-interval" for="baggage-rotation-minutes">每段时长<input id="baggage-rotation-minutes" type="number" min="1" max="1440" step="1" value="${baggageRotationSettings.intervalMinutes}" />分钟</label>
            <span class="rotation-message" role="status">${escapeHtml(baggageRotationMessage)}</span>
          </div>
          ${rotationSegments}
        </section>
        <section class="handoff-suggestions" aria-label="接机后登机支援候选">
          <h2 class="rotation-title">接机航班结束后的登机支援候选</h2>
          <p class="handoff-note">时间按接机航班结束至目标航班结束计算，不代表岗位准确工作时段。请核对候选人员当时是否有其他任务，以及目标航班是否仍需要登机人手；页面不会自动调人。</p>
          ${handoffSuggestions}
        </section>
        <div class="table-wrap"><table><thead><tr><th>岗位</th>${day.flights.map((flight) => `<th>${escapeHtml(flight.flightNo)}<small>${escapeHtml(flight.windowStart)} - ${escapeHtml(flight.windowEnd)}</small></th>`).join("")}</tr></thead><tbody>${tableRows}</tbody></table></div>
        <section class="remarks"><h2>备注</h2>${day.remarks.length ? `<ul>${day.remarks.map((remark) => `<li>${escapeHtml(remark.rawText)}${remark.kind === "support" ? "（已读入，未设占用时段）" : ""}</li>`).join("")}</ul>` : "<p>无备注</p>"}</section>
      </section>
    </main>`;
  app.querySelectorAll<HTMLInputElement>("[data-import-value]").forEach((input) => {
    input.addEventListener("change", async () => {
      const target = importedDraft.find((cell) => cell.id === input.dataset.importValue);
      if (!target) return;
      target.rawValue = input.value;
      importSaveMessage = "";
      await requestImportedValidation();
      render();
    });
  });
  app.querySelectorAll<HTMLSelectElement>("[data-import-semantics]").forEach((select) => {
    select.addEventListener("change", async () => {
      const target = importedDraft.find((cell) => cell.id === select.dataset.importSemantics);
      if (!target || !select.value) return;
      target.semantics = select.value as ImportedCell["semantics"];
      importSaveMessage = "";
      await requestImportedValidation();
      render();
    });
  });
  app.querySelectorAll<HTMLInputElement>("[data-work-start], [data-work-end]").forEach((input) => {
    input.addEventListener("change", async () => {
      const cellId = input.dataset.workStart ?? input.dataset.workEnd;
      const target = importedDraft.find((cell) => cell.id === cellId);
      if (!target) return;
      if (input.dataset.workStart !== undefined) target.workStart = input.value || null;
      else target.workEnd = input.value || null;
      importSaveMessage = "";
      await requestImportedValidation();
      render();
    });
  });
  app.querySelectorAll<HTMLInputElement>("[data-role-count]").forEach((input) => {
    input.addEventListener("change", async () => {
      if (!importedDay) return;
      if (!input.checkValidity()) {
        input.reportValidity();
        return;
      }
      const name = input.dataset.roleCount ?? "";
      importedDay.roleConfigs = importedDay.roleConfigs.filter((config) => config.roleName !== name);
      importedDay.roleConfigs.push({ roleName: name, requiredCount: input.value === "" ? null : Number(input.value) });
      importSaveMessage = "";
      await requestImportedValidation();
      render();
    });
  });
  app.querySelectorAll<HTMLInputElement>("[data-count-override]").forEach((input) => {
    input.addEventListener("change", async () => {
      const target = importedDraft.find((cell) => cell.id === input.dataset.countOverride);
      if (!target) return;
      if (!input.checkValidity()) {
        input.reportValidity();
        return;
      }
      target.requiredCountOverride = input.value === "" ? null : Number(input.value);
      importSaveMessage = "";
      await requestImportedValidation();
      render();
    });
  });
  app.querySelectorAll<HTMLInputElement>("[data-fixed-member]").forEach((input) => {
    input.addEventListener("change", async () => {
      const target = importedDraft.find((cell) => cell.id === input.dataset.fixedMember);
      if (!target) return;
      const name = input.dataset.fixedName ?? "";
      target.fixedMemberNames = input.checked
        ? [...new Set([...target.fixedMemberNames, name])]
        : target.fixedMemberNames.filter((item) => item !== name);
      importSaveMessage = "";
      await requestImportedValidation();
      render();
    });
  });
  app.querySelectorAll<HTMLInputElement>("[data-occupancy-field], [data-occupancy-member], [data-transition-minutes], [data-remarks-reviewed]").forEach((input) => {
    input.addEventListener("change", async () => {
      if (!importedDay) return;
      if (input.dataset.transitionMinutes !== undefined && !input.checkValidity()) {
        input.reportValidity();
        return;
      }
      const occupancyId = input.closest<HTMLElement>("[data-occupancy]")?.dataset.occupancy;
      if (input.dataset.transitionMinutes !== undefined) importedDay.transitionMinutes = input.value === "" ? 0 : Number(input.value);
      else if (input.dataset.remarksReviewed !== undefined) importedDay.remarksReviewed = input.checked;
      else if (occupancyId) {
        const occupancy = importedDay.extraOccupancies.find((item) => item.id === occupancyId);
        if (!occupancy) return;
        if (input.dataset.occupancyMember !== undefined) {
          const name = input.dataset.memberName ?? "";
          occupancy.memberNames = input.checked ? [...new Set([...occupancy.memberNames, name])] : occupancy.memberNames.filter((item) => item !== name);
        } else if (input.dataset.occupancyField === "label") occupancy.label = input.value;
        else if (input.dataset.occupancyField === "start") occupancy.start = input.value;
        else if (input.dataset.occupancyField === "end") occupancy.end = input.value;
      }
      importSaveMessage = "";
      await requestImportedValidation();
      render();
    });
  });
  app.querySelectorAll<HTMLButtonElement>("[data-remove-occupancy]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!importedDay) return;
      importedDay.extraOccupancies = importedDay.extraOccupancies.filter((item) => item.id !== button.dataset.removeOccupancy);
      await requestImportedValidation();
      render();
    });
  });
  app.querySelector<HTMLButtonElement>("#add-occupancy")?.addEventListener("click", async () => {
    if (!importedDay) return;
    importedDay.extraOccupancies.push({ id: `occupancy-${Date.now()}`, label: "", memberNames: [], start: "", end: "" });
    await requestImportedValidation();
    render();
  });
  app.querySelectorAll<HTMLInputElement>("[data-member-role]").forEach((input) => {
    input.addEventListener("change", async () => {
      if (!importedDay) return;
      const name = input.dataset.memberName ?? "";
      const eligibleRoleNames = Array.from(app.querySelectorAll<HTMLInputElement>("[data-member-role]"))
        .filter((checkbox) => checkbox.dataset.memberName === name && checkbox.checked)
        .map((checkbox) => checkbox.dataset.memberRole ?? "");
      importedDay.memberConfigs = importedDay.memberConfigs.filter((config) => config.memberName !== name);
      importedDay.memberConfigs.push({ memberName: name, eligibleRoleNames });
      importSaveMessage = "";
      await requestImportedValidation();
      render();
      const panel = app.querySelector<HTMLDetailsElement>(".member-config");
      if (panel) panel.open = true;
    });
  });
  app.querySelectorAll<HTMLInputElement>("[data-attendance-member]").forEach((input) => {
    input.addEventListener("change", async () => {
      if (!importedDay) return;
      const name = input.dataset.attendanceMember ?? "";
      const entry = importedDay.memberAvailability.find((item) => item.memberName === name)
        ?? { memberName: name, participating: false, availableFrom: null, availableUntil: null };
      const field = input.dataset.attendanceField;
      if (field === "participating") entry.participating = input.checked;
      else if (field === "availableFrom") entry.availableFrom = input.value || null;
      else if (field === "availableUntil") entry.availableUntil = input.value || null;
      importedDay.memberAvailability = importedDay.memberAvailability.filter((item) => item.memberName !== name);
      importedDay.memberAvailability.push(entry);
      importSaveMessage = "";
      await requestImportedValidation();
      render();
      const panel = app.querySelector<HTMLDetailsElement>(".member-config");
      if (panel) panel.open = true;
    });
  });
  app.querySelector<HTMLInputElement>("#baggage-rotation-enabled")?.addEventListener("change", saveBaggageRotationSettings);
  app.querySelector<HTMLInputElement>("#baggage-rotation-minutes")?.addEventListener("change", saveBaggageRotationSettings);
  app.querySelector<HTMLButtonElement>("#save-import")?.addEventListener("click", saveImportedDay);
  app.querySelector<HTMLButtonElement>("#solve-import")?.addEventListener("click", solveImportedDay);
  app.querySelector<HTMLButtonElement>("#confirm-imported-schedule")?.addEventListener("click", confirmImportedSchedule);
  app.querySelector<HTMLButtonElement>("#back-to-schedule")?.addEventListener("click", () => {
    importedDay = null;
    render();
  });
}

async function importExcel(file: File, serviceDate: string): Promise<void> {
  const response = await fetch(`/api/import-excel?serviceDate=${encodeURIComponent(serviceDate)}`, { method: "POST", body: await file.arrayBuffer() });
  if (!response.ok) throw new Error("Excel 导入失败：请确认首列为岗位、航班列为“航班号 / 起止时间”");
  importedDay = await response.json() as ImportedDay;
  importedSolveResult = null;
  importedDraft = importedDay.cells;
  await refreshBaggageRotationSettings();
  importSaveMessage = "";
  baggageRotationMessage = "";
  render();
}

async function saveImportedDay(): Promise<void> {
  const response = await fetch("/api/imported-day/confirm", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(importedPayload()),
  });
  importedDay = await response.json() as ImportedDay;
  importedDraft = importedDay.cells;
  await refreshBaggageRotationSettings();
  importSaveMessage = response.ok ? "已保存到本机" : "";
  render();
}

async function solveImportedDay(): Promise<void> {
  const response = await fetch("/api/imported-day/solve", { method: "POST" });
  const body = await response.json() as { valid: boolean; status?: string; accepted?: boolean; diagnostics?: string[]; validationIssues?: Issue[]; issues?: Issue[] };
  importedSolveResult = response.ok ? body : { accepted: false, diagnostics: (body.issues ?? []).map((issue) => issue.message) };
  renderImportedDay();
}

async function confirmImportedSchedule(): Promise<void> {
  if (!importedSolveResult?.assignments) return;
  const response = await fetch("/api/imported-day/confirm-schedule", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ day: importedPayload(), assignments: importedSolveResult.assignments }),
  });
  const result = await response.json() as Schedule & { valid: boolean; issues?: Issue[] };
  if (!response.ok || !result.valid) {
    importedSolveResult = { accepted: false, diagnostics: (result.issues ?? []).map((issue) => issue.message) };
    renderImportedDay();
    return;
  }
  schedule = result;
  draft = result.assignments;
  validation = { valid: true, issues: [], memberPaths: result.memberPaths };
  importedDay = null;
  importSaveMessage = "自动排班已保存";
  render();
}

async function start(): Promise<void> {
  const response = await fetch("/api/schedule");
  if (!response.ok) throw new Error("无法读取本地排班数据");
  schedule = await response.json() as Schedule;
  await refreshBaggageRotationSettings();
  draft = schedule.assignments;
  selectedMemberId = schedule.members[0]?.id ?? "";
  validation = { valid: true, issues: [], memberPaths: schedule.memberPaths };
  render();
}

if (new URLSearchParams(location.search).get("legacy") === "1") {
  void import("./styles.css");
  void start().catch((error: unknown) => {
    app.textContent = error instanceof Error ? error.message : "页面启动失败";
  });
} else {
  void import("./workbench");
}
