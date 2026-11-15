import {flowRoles, html, type HandoffPreference, type State } from "./workbench-model";

const flightScopeOptions = {'same-flight':'本航班','later-overlap':'重叠且晚结束的其他航班'};
const options = (roles: string[], selected: string, excluded: string[] = []) => roles.filter(r => r === selected || !excluded.includes(r)).map(r => `<option value="${html(r)}" ${r === selected ? "selected" : ""}>${html(r)}</option>`).join("");

export function handoffPreferences(state: State, draft = false): string {
  const roles = flowRoles(state);
  const flights = [...new Set(state.flightLibrary.map(f => f.flightNo))];
  return `<p class="section-note">选择来源岗位，并按顺序添加目标。大件1/2/3表示同一共享池的先后轮次，只有一轮为大件1；单轮规则优先，未配置该轮时沿用“大件行李拦截”通用来源规则，明确关闭单轮则不回退。第一目标已满、无资格或冲突时，继续匹配下一项。来源航班决定本规则适用于哪班。每个目标航班二选一，新增默认本航班；旧规则未选范围时保留原行为，修改该来源后按所选范围保存。未特别许可的重叠跨班仍冲突。禁止同人集合优先于接续；专人专岗及实际大件块仍检查。允许晚结束重叠航班表示流程许可，不代表现场交接时刻。无需填写岗位分钟。${draft ? "修改后点击上方保存配置" : "修改即保存"}，重新排班生效，完整配置和备份均保留。</p><datalist id="flow-flight-options">${flights.map(f => `<option value="${html(f)}"></option>`).join("")}</datalist><div class="flow-rule-list">${state.handoffPreferences.map((row, index) => `<fieldset class="flow-rule" data-flow-rule="${index}"><legend>接续规则 ${index + 1}</legend><div class="form-grid"><label>来源航班<input data-flow-scope list="flow-flight-options" value="${html(row.flightNo === "*" ? "通用" : row.flightNo)}" placeholder="通用、CX*或CX937" /></label><label>来源岗位<select data-flow-source aria-label="来源岗位">${options(roles, row.arrivalRole)}</select></label><label class="switch-label"><input type="checkbox" data-flow-enabled ${row.enabled ? "checked" : ""} />启用</label></div><ol class="flow-targets">${row.boardingRoles.map((target, order) => `<li data-flow-target-row="${order}"><label>第${order + 1}优先目标岗位 <select data-flow-target aria-label="第${order + 1}优先目标岗位">${options(roles, target, [row.arrivalRole, ...row.boardingRoles])}</select></label><label>目标航班 <select data-flow-flight-scope aria-label="第${order + 1}优先目标航班">${Object.entries(flightScopeOptions).map(([key,label])=>`<option value="${key}" ${(row.targetFlightScopes?.[target]??"same-flight")===key?"selected":""}>${label}</option>`).join("")}</select></label><div class="button-row"><button type="button" class="btn btn-sm" data-flow-up ${order === 0 ? "disabled" : ""}>↑ 上移</button><button type="button" class="btn btn-sm" data-flow-down ${order === row.boardingRoles.length - 1 ? "disabled" : ""}>↓ 下移</button><button type="button" class="btn btn-sm btn-outline-danger" data-flow-remove-target>删除目标</button></div></li>`).join("")}</ol><div class="button-row"><button type="button" class="btn btn-sm btn-outline-primary" data-flow-add-target ${roles.every(r => r === row.arrivalRole || row.boardingRoles.includes(r)) ? "disabled" : ""}>＋ 增加目标岗位</button><button type="button" class="btn btn-sm btn-outline-danger" data-flow-remove-rule>${row.flightNo === "*" ? "删除规则" : "删除特例，恢复继承"}</button></div>${row.flightNo !== "*" ? '<p class="section-note">具体航班优先，其次最长系列，最后通用。关闭此项不回退；删除恢复继承。规则按来源航班选择。</p>' : ""}</fieldset>`).join("") || '<p class="section-note">暂无接续规则，点击下方增加；来源和目标可自由选择。</p>'}</div><div class="button-row"><button type="button" id="add-flow-rule" class="btn btn-outline-primary">＋ 增加来源规则</button><button type="button" id="add-flow-exception" class="btn btn-outline-secondary">＋ 增加航班特例</button></div>`;
}

export function bindHandoffPreferences(root: HTMLElement, state: State, save: (action: string, payload: unknown) => Promise<boolean>): void {
  let current = structuredClone(state.handoffPreferences);
  const roles = flowRoles(state);
  const commit = (next: HandoffPreference[]) => { current = next; void save("save-handoff-preferences", { preferences: next }); };
  const modify = (index: number, change: (row: HandoffPreference) => void) => {
    const next = structuredClone(current), row = next[index]; change(row);
    row.targetFlightScopes??={};
    for(const target of row.boardingRoles)row.targetFlightScopes[target]??='same-flight';
    commit(next);
  };
  const unique = (scope: string, source: string, except = -1) => !current.some((r, i) => i !== except && r.flightNo === scope && r.arrivalRole === source);
  root.querySelectorAll<HTMLElement>("[data-flow-rule]").forEach(container => {
    const index = Number(container.dataset.flowRule);
    const source = container.querySelector<HTMLSelectElement>("[data-flow-source]")!;
    const scope = container.querySelector<HTMLInputElement>("[data-flow-scope]")!;
    scope.oninput = () => scope.setCustomValidity("");
    scope.onchange = () => {
      const value = scope.value.trim().toUpperCase(); const flight = value === "通用" ? "*" : value;
      if (!flight || (flight !== "*" && flight.includes("*") && !/^[A-Z0-9]+\*$/.test(flight)) || !unique(flight, current[index].arrivalRole, index)) {
        scope.setCustomValidity("填写通用、具体航班或CX*；同航班同来源只能有一条规则"); scope.reportValidity(); return;
      }
      scope.setCustomValidity(""); modify(index, row => { row.flightNo = flight; });
    };
    source.onchange = () => {
      if (!unique(current[index].flightNo, source.value, index)) { source.setCustomValidity("该航班已有这个来源岗位的规则"); source.reportValidity(); return; }
      source.setCustomValidity(""); modify(index, row => { row.arrivalRole = source.value; row.boardingRoles = row.boardingRoles.filter(r => r !== source.value); if (!row.boardingRoles.length) row.enabled = false; if(row.targetFlightScopes)for(const r of Object.keys(row.targetFlightScopes))if(!row.boardingRoles.includes(r))delete row.targetFlightScopes[r]; });
    };
    container.querySelector<HTMLInputElement>("[data-flow-enabled]")!.onchange = event => modify(index, row => { row.enabled = (event.target as HTMLInputElement).checked && row.boardingRoles.length > 0; row.ordered = true; });
    container.querySelector<HTMLButtonElement>("[data-flow-add-target]")!.onclick = () => modify(index, row => {
      const next = roles.find(r => r !== row.arrivalRole && !row.boardingRoles.includes(r));
      if (next) { (row.targetFlightScopes??={})[next]='same-flight'; row.boardingRoles.push(next); row.enabled = true; row.ordered = true; }
    });
    container.querySelector<HTMLButtonElement>("[data-flow-remove-rule]")!.onclick = () => commit(current.filter((_, i) => i !== index));
    container.querySelectorAll<HTMLElement>("[data-flow-target-row]").forEach(target => {
      const order = Number(target.dataset.flowTargetRow);
      target.querySelector<HTMLSelectElement>("[data-flow-target]")!.onchange = event => modify(index, row => { const previous=row.boardingRoles[order], next=(event.target as HTMLSelectElement).value; if(previous!==next && row.targetFlightScopes && previous in row.targetFlightScopes){row.targetFlightScopes[next]=row.targetFlightScopes[previous];delete row.targetFlightScopes[previous];} row.boardingRoles[order]=next; row.ordered = true; });
      target.querySelector<HTMLSelectElement>("[data-flow-flight-scope]")!.onchange=event=>modify(index,row=>{
        (row.targetFlightScopes??={})[row.boardingRoles[order]]=(event.target as HTMLSelectElement).value as NonNullable<HandoffPreference['targetFlightScopes']>[string];
      });
      target.querySelector<HTMLButtonElement>("[data-flow-remove-target]")!.onclick = () => modify(index, row => { if(row.targetFlightScopes)delete row.targetFlightScopes[row.boardingRoles[order]]; row.boardingRoles.splice(order, 1); row.enabled = row.enabled && row.boardingRoles.length > 0; });
      for (const [selector, delta] of [["[data-flow-up]", -1], ["[data-flow-down]", 1]] as const) {
        target.querySelector<HTMLButtonElement>(selector)!.onclick = () => modify(index, row => {
          const other = order + delta;
          if (other >= 0 && other < row.boardingRoles.length) [row.boardingRoles[order], row.boardingRoles[other]] = [row.boardingRoles[other], row.boardingRoles[order]];
          row.ordered = true;
        });
      }
    });
  });
  const add = (special: boolean) => {
    const scope = special ? "CX*" : "*";
    const source = roles.find(r => unique(scope, r));
    if (source) commit([...current, { flightNo: scope, arrivalRole: source, boardingRoles: [], enabled: false, ordered: true }]);
  };
  root.querySelector<HTMLButtonElement>("#add-flow-rule")?.addEventListener("click", () => add(false));
  root.querySelector<HTMLButtonElement>("#add-flow-exception")?.addEventListener("click", () => add(true));
}
