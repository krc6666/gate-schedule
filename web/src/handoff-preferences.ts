import {flowRoles, flowRoleOptions as options, flowRoleNotice, html, type HandoffPreference, type State } from "./workbench-model";
import {ruleScopeSections} from './rule-scope-view';

const flightScopeOptions = {'same-flight':'本航班','later-overlap':'重叠且晚结束的其他航班'};

export function handoffPreferences(state: State, draft = false): string {
  const roles = flowRoles(state);
  const flights = [...new Set(state.flightLibrary.map(f => f.flightNo))];
  const card=(row:HandoffPreference,index:number)=>`<fieldset class="flow-rule ${row.flightNo==='*'?'is-common':'is-exception'}" data-flow-rule="${index}">
    <legend><span class="rule-scope-badge">${row.flightNo==='*'?'通用':html(row.flightNo)+' 特例'}</span> ${html(row.arrivalRole)} <span class="rule-status">${row.enabled?'已启用':'已停用'}</span></legend>
    ${flowRoleNotice(roles,[row.arrivalRole,...row.boardingRoles])}
    <div class="form-grid">${row.flightNo==='*'?'':`<label>来源航班<input data-flow-scope list="flow-flight-options" value="${html(row.flightNo)}" placeholder="CX937 或 CX*" /></label>`}
    <label>来源岗位<select data-flow-source aria-label="来源岗位">${options(roles,row.arrivalRole)}</select></label>
    <label class="switch-label"><input type="checkbox" data-flow-enabled ${row.enabled?'checked':''} ${!row.enabled&&[row.arrivalRole,...row.boardingRoles].some(role=>!roles.includes(role))?'disabled':''}/>启用接续</label></div>
    <ol class="flow-targets">${row.boardingRoles.map((target,order)=>`<li data-flow-target-row="${order}"><label>第${order+1}优先目标岗位 <select data-flow-target aria-label="第${order+1}优先目标岗位">${options(roles,target,[row.arrivalRole,...row.boardingRoles])}</select></label><label>目标航班 <select data-flow-flight-scope aria-label="第${order+1}优先目标航班">${Object.entries(flightScopeOptions).map(([key,label])=>`<option value="${key}" ${(row.targetFlightScopes?.[target]??'same-flight')===key?'selected':''}>${label}</option>`).join('')}</select></label><div class="button-row"><button type="button" class="btn btn-sm" data-flow-up ${order===0?'disabled':''}>↑ 上移</button><button type="button" class="btn btn-sm" data-flow-down ${order===row.boardingRoles.length-1?'disabled':''}>↓ 下移</button><button type="button" class="btn btn-sm btn-outline-danger" data-flow-remove-target>删除目标</button></div></li>`).join('')}</ol>
    <div class="button-row"><button type="button" class="btn btn-sm btn-outline-primary" data-flow-add-target ${[row.arrivalRole,...row.boardingRoles].some(role=>!roles.includes(role))||roles.every(r=>r===row.arrivalRole||row.boardingRoles.includes(r))?'disabled':''}>＋ 增加目标岗位</button>
    ${row.flightNo==='*'?`<button type="button" class="btn btn-sm" data-flow-exception ${[row.arrivalRole,...row.boardingRoles].some(role=>!roles.includes(role))?'disabled':''}>＋ 为此岗位添加特例</button>`:''}
    <button type="button" class="btn btn-sm btn-outline-danger" data-flow-remove-rule>${row.flightNo==='*'?'删除通用规则':'删除特例，恢复继承'}</button></div></fieldset>`;
  return `<section class="flow-settings"><p class="flow-inheritance-note">通用设置只需维护一次，没有特例的航班默认沿用通用；有不同安排时再手动添加特例。已设“同航班专岗专人”的岗位不进入流动来源或目标选项；取消专岗并保存后恢复可选。</p>
    <datalist id="flow-flight-options">${flights.map(f=>`<option value="${html(f)}"></option>`).join('')}</datalist>
    ${ruleScopeSections(state.handoffPreferences,'flow',card)}
    <div class="button-row flow-add-actions"><button type="button" id="add-flow-rule" class="btn btn-outline-primary">＋ 添加通用来源岗位</button><button type="button" id="add-flow-exception" class="btn btn-outline-secondary">＋ 增加航班特例</button></div>
    <p class="section-note">按目标顺序尽量接续；无资格、目标已满或冲突时尝试下一项。${draft?'修改后点击上方保存配置':'修改即保存'}，重新排班生效。</p>
    <details id="flow-help" class="flow-help"><summary>接续与继承说明</summary><p class="section-note">具体航班优先，其次最长系列，最后通用。旧规则未选目标范围时保留原行为，修改该来源后按所选范围保存。大件1/2/3是同池轮次，只有一轮为大件1；单轮规则优先，未配置该轮沿用“大件行李拦截”，明确关闭不回退。接续为偏好，不能绕过分人、禁止、专岗、资格或实际时段；晚结束重叠航班表示流程许可，不推算交接分钟。配置、Excel和完整备份保留。</p></details></section>`;
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
  const addException=(source:string)=>{
    const shared=current.find(r=>r.flightNo==='*'&&r.arrivalRole===source);
    if([source,...(shared?.boardingRoles??[])].some(role=>!roles.includes(role)))return;
    const flight=prompt('输入需要不同安排的航班号或系列，如 CX937 或 CX*。未设置特例的航班沿用通用。','')?.trim().toUpperCase();
    if(!flight)return;
    if(flight==='*'||flight==='通用'||(flight.includes('*')&&!/^[A-Z0-9]+\*$/.test(flight))){alert('请填写具体航班号或以 * 结尾的系列；通用设置在上方维护。');return;}
    if(!unique(flight,source)){alert('该航班已有此来源岗位的特例，请展开航班特例修改。');return;}
    const next=shared?{...structuredClone(shared),flightNo:flight}:{flightNo:flight,arrivalRole:source,boardingRoles:[],enabled:false,ordered:true};
    const details=root.querySelector<HTMLDetailsElement>('#flow-exceptions');if(details)details.open=true;
    commit([...current,next]);
  };
  root.querySelectorAll<HTMLElement>("[data-flow-rule]").forEach(container => {
    const index = Number(container.dataset.flowRule);
    const source = container.querySelector<HTMLSelectElement>("[data-flow-source]")!;
    const scope = container.querySelector<HTMLInputElement>("[data-flow-scope]");
    if(scope){scope.oninput = () => scope.setCustomValidity("");
    scope.onchange = () => {
      const flight = scope.value.trim().toUpperCase();
      if (!flight || flight==='*'||flight==='通用'||(flight.includes("*") && !/^[A-Z0-9]+\*$/.test(flight)) || !unique(flight, current[index].arrivalRole, index)) {
        scope.setCustomValidity("填写具体航班或CX*；同航班同来源只能有一条特例"); scope.reportValidity(); return;
      }
      scope.setCustomValidity(""); modify(index, row => { row.flightNo = flight; });
    };}
    container.querySelector<HTMLButtonElement>('[data-flow-exception]')?.addEventListener('click',()=>addException(current[index].arrivalRole));
    source.onchange = () => {
      if(!roles.includes(source.value))return;
      if (!unique(current[index].flightNo, source.value, index)) { source.setCustomValidity("该航班已有这个来源岗位的规则"); source.reportValidity(); return; }
      source.setCustomValidity(""); modify(index, row => { row.arrivalRole = source.value; row.boardingRoles = row.boardingRoles.filter(r => r !== source.value); if (!row.boardingRoles.length) row.enabled = false; if(row.targetFlightScopes)for(const r of Object.keys(row.targetFlightScopes))if(!row.boardingRoles.includes(r))delete row.targetFlightScopes[r]; });
    };
    container.querySelector<HTMLInputElement>("[data-flow-enabled]")!.onchange = event => modify(index, row => { row.enabled = (event.target as HTMLInputElement).checked && row.boardingRoles.length > 0 && [row.arrivalRole,...row.boardingRoles].every(role=>roles.includes(role)); row.ordered = true; });
    container.querySelector<HTMLButtonElement>("[data-flow-add-target]")!.onclick = () => modify(index, row => {
      if([row.arrivalRole,...row.boardingRoles].some(role=>!roles.includes(role)))return;
      const next = roles.find(r => r !== row.arrivalRole && !row.boardingRoles.includes(r));
      if (next) { (row.targetFlightScopes??={})[next]='same-flight'; row.boardingRoles.push(next); row.enabled = true; row.ordered = true; }
    });
    container.querySelector<HTMLButtonElement>("[data-flow-remove-rule]")!.onclick = () => commit(current.filter((_, i) => i !== index));
    container.querySelectorAll<HTMLElement>("[data-flow-target-row]").forEach(target => {
      const order = Number(target.dataset.flowTargetRow);
      target.querySelector<HTMLSelectElement>("[data-flow-target]")!.onchange = event => modify(index, row => { const previous=row.boardingRoles[order], next=(event.target as HTMLSelectElement).value; if(!roles.includes(next))return; if(previous!==next && row.targetFlightScopes && previous in row.targetFlightScopes){row.targetFlightScopes[next]=row.targetFlightScopes[previous];delete row.targetFlightScopes[previous];} row.boardingRoles[order]=next; row.ordered = true; });
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
  const add = () => {
    const source = roles.find(r => unique('*', r));
    if (source) commit([...current, { flightNo: '*', arrivalRole: source, boardingRoles: [], enabled: false, ordered: true }]);
  };
  const addCommon=root.querySelector<HTMLButtonElement>('#add-flow-rule');
  if(addCommon){addCommon.disabled=!roles.some(r=>unique('*',r));addCommon.addEventListener('click',add);}
  const addSpecial=root.querySelector<HTMLButtonElement>('#add-flow-exception');
  if(addSpecial){addSpecial.disabled=!roles.length;addSpecial.addEventListener('click',()=>{const source=current.find(r=>r.flightNo==='*'&&[r.arrivalRole,...r.boardingRoles].every(role=>roles.includes(role)))?.arrivalRole??roles[0];if(source)addException(source);});}
}
