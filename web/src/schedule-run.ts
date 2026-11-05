import { groupUrl } from "./group-api";
import { html, type State } from "./workbench-model";

type Run = { id: string; status: "running" | "completed" | "cancelled" | "failed"; stage: string; solutionCount: number; canKeep: boolean; message?: string; percent: number; completedSteps: number; totalSteps: number; steps: { label: string; status: string }[] };

async function read<T>(response: Response): Promise<T> {
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail ?? "计算请求失败，原班表保留");
  return data as T;
}

export async function runSchedule(command: unknown, group?: "A" | "B"): Promise<State> {
  const scopedFetch = (path: string, init?: RequestInit) => fetch(groupUrl(path, group), init);
  document.querySelector(".schedule-progress")?.remove();
  document.querySelector<HTMLDialogElement>("#app-dialog")?.close();
  const panel = document.createElement("section");
  panel.className = "schedule-progress";
  panel.setAttribute("aria-label", "排班进度");
  document.body.append(panel);
  let run: Run | null = null;
  let stopping = false;
  let stopRequested = false;
  let errorMessage = "";
  let elapsed = 0;
  let lastPaint = "";
  const startedAt = performance.now();
  const paint = () => {
    const key = JSON.stringify([run, stopping, errorMessage, elapsed]);
    if (key === lastPaint) return;
    lastPaint = key;
    const active = !run || run.status === "running";
    panel.dataset.runStatus = active ? "running" : run!.status;
    const title = active ? stopping ? "正在停止排班" : "正在计算排班" : run!.status === "completed" ? "排班已完成" : run!.status === "cancelled" ? "排班已停止" : "排班未完成";
    panel.innerHTML = `<header class="schedule-progress-heading"><strong role="status">${title}</strong><span>${run?.percent ?? 0}%</span>${!active ? '<button data-close-progress aria-label="关闭排班进度">×</button>' : ""}</header><div class="run-progress-track" role="progressbar" aria-label="排班流程完成比例" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${run?.percent ?? 0}"><div class="run-progress-bar" style="width:${run?.percent ?? 0}%"></div></div><p class="run-summary">${run ? `${run.completedSteps} / ${run.totalSteps} 步已完成 · ${run.solutionCount} 份安全候选` : "正在提交排班请求…"} · ${elapsed}秒</p><ol class="run-stages">${run?.steps.map(step => `<li class="is-${step.status}"><span aria-hidden="true">${step.status === "completed" ? "✓" : step.status === "active" ? "◉" : step.status === "failed" || step.status === "stopped" ? "!" : "○"}</span>${html(step.label)}</li>`).join("") ?? '<li class="is-active">◉ 准备排班资料</li>'}</ol><p class="run-note">${active ? "进度按实际处理步骤显示；人员计算期间会继续搜索更合适的安排。" : html(run!.message ?? "草稿已保存，请核对人员；缺人处仍需补齐。")}</p>${active ? '<div class="schedule-progress-actions"><button data-stop-run="discard" class="btn btn-outline-danger">停止，不采用</button><button data-stop-run="keep" class="btn btn-primary">停止并保留当前安排</button></div>' : ""}<p class="run-error" role="alert">${html(errorMessage)}</p>`;
    panel.querySelector<HTMLButtonElement>("[data-close-progress]")?.addEventListener("click", () => panel.remove());
    panel.querySelectorAll<HTMLButtonElement>("[data-stop-run]").forEach(button => {
      button.disabled = stopping || (button.dataset.stopRun === "keep" && !run?.canKeep);
      button.onclick = async () => {
        stopping = true; stopRequested = true; paint();
        if (!run) return;
        try {
          const result = await read<Run>(await scopedFetch(`/api/workspace/runs/${run.id}/stop`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ keep: button.dataset.stopRun === "keep" }) }));
          if (run.status === "running") run = result;
          paint();
        } catch (error) { stopping = false; stopRequested = false; errorMessage = String(error); paint(); }
      };
    });
  };
  paint();
  const timer = window.setInterval(() => { elapsed = Math.floor((performance.now() - startedAt) / 1000); paint(); }, 1000);
  try {
    run = await read<Run>(await scopedFetch("/api/workspace/runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(command) }));
    if (stopRequested) run = await read<Run>(await scopedFetch(`/api/workspace/runs/${run.id}/stop`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ keep: false }) }));
    paint();
    while (run.status === "running") {
      await new Promise(resolve => setTimeout(resolve, 180));
      const result: Run = await read<Run>(await scopedFetch(`/api/workspace/runs/${run!.id}`));
      if (run.status === "running") run = result;
      paint();
    }
    if (run.status !== "completed") throw new Error(run.message ?? "计算未保存，原班表保留");
    return read<State>(await scopedFetch("/api/workspace"));
  } catch (error) {
    if (run?.status === "running") {
      try { run = await read<Run>(await scopedFetch(`/api/workspace/runs/${run.id}/stop`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ keep: false }) })); } catch { /* Reload shows the persisted outcome. */ }
    }
    errorMessage = error instanceof Error ? error.message : "计算失败，请重新读取班表";
    if (!run) run = { id: "", status: "failed", stage: "", solutionCount: 0, canKeep: false, percent: 0, completedSteps: 0, totalSteps: 0, steps: [] };
    else if (run.status === "running") run = { ...run, status: "failed" };
    paint(); throw error;
  } finally { clearInterval(timer); }
}
