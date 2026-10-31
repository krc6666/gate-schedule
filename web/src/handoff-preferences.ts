import { html, type State } from "./workbench-model";

export function handoffPreferences(state: State, draft = false): string {
  const flightNo = "*";
  const saved = state.handoffPreferences.filter(p => p.flightNo === flightNo);
  const roles = state.configuration.roleConfigs.map(r => state.newRoleDefaults[r.roleName] ?? r);
  const sources = [...new Set([...roles.filter(r => r.workMode === "arrival").map(r => r.roleName), ...saved.map(p => p.arrivalRole)])];
  const targets = [...new Set([...roles.filter(r => r.workMode === "boarding").map(r => r.roleName), ...saved.flatMap(p => p.boardingRoles)])];
  if (!sources.length || !targets.length) return '<p class="section-note">先在配置页将相应岗位的工作环节设为接机、登机，再勾选通用接续偏好。</p>';
  return `<div class="handoff-preferences" data-handoff-flight="${html(flightNo)}"><div class="section-heading"><h4>接机后岗位偏好</h4><button class="btn btn-sm btn-outline-secondary" data-restore-handoff="${html(flightNo)}">载入历史建议</button></div><p class="section-note">所有航班共用一份偏好，只考虑该航班实际需要的岗位。勾选要优先接续的登机岗，可随时增加或取消。开启“按顺序优先”后用上下移调整；关闭则勾选岗位同等优先。排不开时允许其他合法安排，不因此留空。${draft ? "修改后点击上方保存配置" : "修改即保存"}，重新排班生效，随完整配置迁移。</p>${sources.map(source => {
    const item = saved.find(p => p.arrivalRole === source);
    const orderedTargets = [...(item?.boardingRoles ?? []), ...targets.filter(t => !item?.boardingRoles.includes(t))];
    return `<fieldset data-handoff-source="${html(source)}"><legend>${html(source)}</legend><div class="handoff-switches"><label class="switch-label"><input type="checkbox" data-handoff-enabled ${item?.enabled ? "checked" : ""} ${item ? "" : "disabled"} />启用偏好</label><label class="switch-label"><input type="checkbox" data-handoff-ordered ${item?.ordered ? "checked" : ""} ${item ? "" : "disabled"} />按顺序优先</label><span class="section-note">${item ? item.ordered ? "靠前的岗位优先；不满足时继续考虑后面的岗位" : "已勾选岗位同等优先" : "先勾选下方岗位"}</span></div><div class="handoff-targets">${orderedTargets.map(target => {
      const index = item?.boardingRoles.indexOf(target) ?? -1;
      return `<div class="handoff-target-row"><label><input type="checkbox" data-handoff-target="${html(target)}" ${index >= 0 ? "checked" : ""} /><span class="handoff-rank">${index >= 0 ? item?.ordered ? `第${index + 1}优先` : "优先" : ""}</span>${html(target)}</label>${index >= 0 && item?.ordered ? `<div class="button-row"><button class="btn btn-sm btn-outline-secondary" data-handoff-move="${html(target)}" data-direction="-1" aria-label="上移 ${html(source)} ${html(target)}" ${index === 0 ? "disabled" : ""}>↑ 上移</button><button class="btn btn-sm btn-outline-secondary" data-handoff-move="${html(target)}" data-direction="1" aria-label="下移 ${html(source)} ${html(target)}" ${index === item.boardingRoles.length - 1 ? "disabled" : ""}>↓ 下移</button></div>` : ""}</div>`;
    }).join("")}</div></fieldset>`;
  }).join("")}</div>`;
}

export function bindHandoffPreferences(root: HTMLElement, state: State, save: (action: string, payload: unknown) => Promise<boolean>): void {
  root.querySelectorAll<HTMLElement>("[data-handoff-flight]").forEach(container => {
    container.querySelectorAll<HTMLInputElement>("input").forEach(input => input.onchange = () => {
      const flightNo = container.dataset.handoffFlight!;
      const preferences = state.handoffPreferences.filter(p => p.flightNo !== flightNo);
      container.querySelectorAll<HTMLFieldSetElement>("[data-handoff-source]").forEach(fieldset => {
        const arrivalRole = fieldset.dataset.handoffSource!;
        const boardingRoles = [...fieldset.querySelectorAll<HTMLInputElement>("[data-handoff-target]:checked")].map(i => i.dataset.handoffTarget!);
        if (!boardingRoles.length) return;
        const existing = state.handoffPreferences.find(p => p.flightNo === flightNo && p.arrivalRole === arrivalRole);
        const ordered = fieldset.querySelector<HTMLInputElement>("[data-handoff-ordered]")!.checked;
        preferences.push({ flightNo, arrivalRole, boardingRoles, enabled: existing ? fieldset.querySelector<HTMLInputElement>("[data-handoff-enabled]")!.checked : true, ...(ordered || existing?.ordered !== undefined ? { ordered } : {}) });
      });
      void save("save-handoff-preferences", { preferences });
    });
    container.querySelectorAll<HTMLButtonElement>("[data-handoff-move]").forEach(button => button.onclick = () => {
      const flightNo = container.dataset.handoffFlight!;
      const arrivalRole = button.closest<HTMLElement>("[data-handoff-source]")!.dataset.handoffSource!;
      const preferences = structuredClone(state.handoffPreferences);
      const item = preferences.find(p => p.flightNo === flightNo && p.arrivalRole === arrivalRole);
      if (!item?.ordered) return;
      const index = item.boardingRoles.indexOf(button.dataset.handoffMove!);
      const target = index + Number(button.dataset.direction);
      if (index < 0 || target < 0 || target >= item.boardingRoles.length) return;
      [item.boardingRoles[index], item.boardingRoles[target]] = [item.boardingRoles[target], item.boardingRoles[index]];
      void save("save-handoff-preferences", { preferences });
    });
  });
  root.querySelectorAll<HTMLButtonElement>("[data-restore-handoff]").forEach(button => button.onclick = () => {
    const flightNo = button.dataset.restoreHandoff!;
    if (!confirm(`用合并后的历史建议替换通用岗位接续偏好？初始不设顺序，可继续手动调整。`)) return;
    void save("save-handoff-preferences", { preferences: [...state.handoffPreferences.filter(p => p.flightNo !== flightNo), ...state.suggestedHandoffPreferences.filter(p => p.flightNo === flightNo)] });
  });
}
