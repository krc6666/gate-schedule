import { groupFetch, groupUrl } from "./group-api";
import { html, type Candidate, type Plan, type Schedule, type State } from "./workbench-model";

type PaletteCandidate = Candidate & { status?: string; label?: string; revision?: number };
type PaletteHooks = { targetChanged(taskId: string): void; peopleChanged(container: HTMLElement): void };

function vacancyTasks(plan: Plan, schedule: Schedule) {
  return schedule.tasks.filter(task => plan.draft.filter(a => a.taskId === task.id).length < task.requiredCount);
}

function memberButton(member: { id: string; name: string }, selected: string, candidate?: PaletteCandidate): string {
  const assigned = candidate?.status === "assigned";
  const available = !candidate || candidate.available && !assigned;
  const status = candidate?.status ?? "unchecked";
  const label = candidate?.label ?? (candidate ? available ? "可安排" : "不可安排" : "待选择目标岗位检查");
  const reasons = candidate?.reasons.join("；") ?? "";
  return `<button class="palette-person ${selected === member.id ? "selected" : ""} ${candidate ? available ? "palette-available" : "palette-unavailable" : ""}" data-candidate-status="${html(status)}" draggable="${available}" data-drag-member="${html(member.id)}" data-task-ids="[]" data-palette-member="${html(member.id)}" ${available ? "" : "disabled aria-disabled=\"true\""} title="${html([label, reasons].filter(Boolean).join("；"))}"><strong>${html(member.name)}</strong>${candidate ? `<small>${html(label)}${reasons ? ` · ${html(reasons)}` : ""}</small>` : ""}</button>`;
}

export function paletteHtml(plan: Plan, schedule: Schedule | null, selectedTarget = "", selectedMember = ""): string {
  if (!schedule) return "";
  const tasks = vacancyTasks(plan, schedule);
  const target = tasks.find(task => task.id === selectedTarget);
  const options = tasks.map(task => {
    const flight = schedule.flights.find(f => f.id === task.flightId)?.flightNo ?? "共享支援";
    return `<option value="${html(task.id)}" ${task.id === target?.id ? "selected" : ""}>${html(flight)} · ${html(task.roleId)}${task.timingLabel ? ` · ${html(task.timingLabel)}` : ""}</option>`;
  });
  const notice = target ? "正在检查这个岗位的人选…" : tasks.length ? "当天人员名单；选择目标岗位后检查谁可安排。" : "没有待补岗位；当天人员仍可拖动调整或交换，落岗后会校验。";
  return `<aside class="schedule-palette" data-schedule-palette data-selected-member="${html(selectedMember)}"><strong>人员选择</strong><label>待补岗位 <select data-palette-target aria-label="选择需要补人的岗位"><option value="">查看当天人员名单</option>${options.join("")}</select></label><p class="text-muted" data-palette-notice role="status" aria-live="polite">${notice}</p><div class="palette-people">${target ? "" : schedule.members.map(member => memberButton(member, selectedMember)).join("")}</div></aside>`;
}

export function bindPalette(root: HTMLElement, state: State, plan: Plan, hooks: PaletteHooks): () => void {
  const palette = root.querySelector<HTMLElement>("[data-schedule-palette]");
  if (!palette) return () => {};
  const select = palette.querySelector<HTMLSelectElement>("[data-palette-target]")!;
  const notice = palette.querySelector<HTMLElement>("[data-palette-notice]")!;
  const people = palette.querySelector<HTMLElement>(".palette-people")!;
  const controller = new AbortController();
  let generation = 0;
  select.onchange = () => {
    generation += 1;
    controller.abort();
    people.innerHTML = "";
    notice.textContent = "正在检查所选岗位…";
    hooks.targetChanged(select.value);
  };
  const taskId = select.value;
  const task = plan.schedule?.tasks.find(t => t.id === taskId);
  if (!task) return () => { generation += 1; controller.abort(); };
  const expectedRevision = state.revision;
  const requestGeneration = ++generation;
  people.innerHTML = "";
  notice.textContent = "正在检查这个岗位的人选…";
  const slot = plan.draft.filter(a => a.taskId === taskId).length;
  const current = () => !controller.signal.aborted && requestGeneration === generation && palette.isConnected && root.contains(palette) && select.value === taskId && state.revision === expectedRevision;
  void (async () => {
    try {
      const response = await groupFetch(`/api/workspace/candidates/${encodeURIComponent(plan.id)}/${encodeURIComponent(taskId)}?slot=${slot}`, { signal: controller.signal });
      if (!response.ok) throw new Error("检查失败，请重试或重新读取班表。");
      const candidates: PaletteCandidate[] = await response.json();
      if (!current()) return;
      if (!Array.isArray(candidates) || candidates.some(candidate => candidate.revision !== expectedRevision)) throw new Error("班表已改变，请重新读取后再检查人选。");
      const ordered = [...candidates].sort((a, b) => Number(b.available && b.status !== "assigned") - Number(a.available && a.status !== "assigned"));
      people.innerHTML = ordered.map(candidate => memberButton(candidate, palette.dataset.selectedMember ?? "", candidate)).join("");
      const available = candidates.filter(candidate => candidate.available && candidate.status !== "assigned").length;
      notice.textContent = candidates.length ? `${available} 人可接这个岗位；兼任、接续及不可安排原因见姓名下方。` : "暂时无法检查这个岗位的人选，请先核对岗位资料和当天人员。";
      hooks.peopleChanged(people);
    } catch (error) {
      if (!current()) return;
      people.innerHTML = "";
      notice.setAttribute("role", "alert");
      notice.textContent = error instanceof Error ? error.message : "检查失败，请重新读取班表后重试。";
    }
  })();
  return () => { generation += 1; controller.abort(); };
}
