import {flowRoles, flowRoleOptions as options, flowRoleNotice, flowFlightScopes, html, type HandoffPreference, type State } from "./workbench-model";
import {ruleScopeSections,ruleCardId,revealRule} from './rule-scope-view';
import {handoffRoutes} from './handoff-layout';

const cardId=(row:HandoffPreference)=>ruleCardId('flow-card',row.flightNo,row.arrivalRole,...(row.id?[row.id]:[]));

const flightScopeOptions = flowFlightScopes;

export function handoffPreferences(state: State, draft = false): string {
  const roles = flowRoles(state);
  const flights = [...new Set(state.flightLibrary.map(f => f.flightNo))];
  const routes=handoffRoutes(state.handoffPreferences);
  const targetScope=(row:HandoffPreference,index:number)=>row.targetFlightScopeList?.[index]??row.targetFlightScopes?.[row.boardingRoles[index]]??'same-flight';
  const scopeSuffix=(row:HandoffPreference,index:number)=>targetScope(row,index)==='later-overlap'?'（晚结束重叠航班）':targetScope(row,index)==='earlier-overlap'?'（早结束重叠航班）':'';
  const availableFirst=(indices:number[])=>{
    const source=state.handoffPreferences[indices[0]];
    if([source.arrivalRole,...source.boardingRoles].some(role=>!roles.includes(role)))return undefined;
    const used=indices.flatMap(i=>[state.handoffPreferences[i].arrivalRole,...state.handoffPreferences[i].boardingRoles]);
    return roles.find(role=>!used.includes(role)&&!state.handoffPreferences.some(r=>r.mode!=='chain'&&r.flightNo===source.flightNo&&r.arrivalRole===role));
  };
  const pathSummary=(indices:number[])=>{
    const tail=state.handoffPreferences[indices.at(-1)!];
    const prefix=indices.map((i,position)=>{
      const row=state.handoffPreferences[i];
      const prior=position?state.handoffPreferences[indices[position-1]]:undefined;
      const priorTarget=prior?.boardingRoles.findIndex(role=>role===row.arrivalRole)??-1;
      return html(row.arrivalRole)+(prior&&priorTarget>=0?scopeSuffix(prior,priorTarget):'');
    });
    const ranked=tail.boardingRoles.map((target,order)=>`${order+1}. ${html(target)}${targetScope(tail,order)==='later-overlap'?'（晚结束重叠航班）':targetScope(tail,order)==='earlier-overlap'?'（早结束重叠航班）':''}`).join('；');
    if(indices.length>1){
      if(tail.enabled&&tail.boardingRoles.length===1&&targetScope(tail,0)==='same-flight')return [...prefix,html(tail.boardingRoles[0])].join(' → ');
      return `${prefix.join(' → ')}；${tail.enabled?'优先目标':'后续已停用'}：${ranked||'尚未添加'}`;
    }
    return ranked||'尚未添加目标岗位';
  };
  const card=(indices:number[],position=0):string=>{
    const index=indices[position],row=state.handoffPreferences[index],first=position===0;
    return `<details id="${cardId(row)}${first?'':`/in-${indices[0]}-${position}`}" class="flow-rule rule-card ${first?'':'flow-linked-step '}${row.flightNo==='*'?'is-common':'is-exception'}" data-flow-rule="${index}" ${first?`data-flow-route-root="${index}"`:''}>
    <summary>${first?`<span class="rule-scope-badge">${row.flightNo==='*'?'通用':html(row.flightNo)+' 特例'}</span>`:`<span class="rule-scope-badge">后续</span>`}<strong>${html(row.arrivalRole)}${first?'':' 的后续'}</strong>${!row.enabled&&row.boardingRoles.length?'<span class="rule-status">已停用</span>':''}<span data-rule-path>${first?pathSummary(indices):pathSummary([index])}</span>${first?'':'<span class="flow-expand-hint">展开修改</span>'}<span class="chevron" aria-hidden="true">⌄</span></summary><div class="rule-card-body">
    ${flowRoleNotice(roles,[row.arrivalRole,...row.boardingRoles])}
    ${row.flightNo==='*'?'':`<label class="flow-scope-field">来源航班<input data-flow-scope list="flow-flight-options" value="${html(row.flightNo)}" placeholder="CX937 或 CX*" /></label>`}
    <div class="flow-editor-row"><label class="flow-origin">${first?'来源岗位':'接续岗位'}<select data-flow-source aria-label="${first?'来源岗位':'接续岗位'}">${options(roles,row.arrivalRole)}</select></label><span class="flow-arrow" aria-hidden="true">→</span>
    <ol class="flow-targets">${row.boardingRoles.map((target,order)=>`<li data-flow-target-row="${order}"><label>第${order+1}优先目标岗位<select data-flow-target aria-label="第${order+1}优先目标岗位">${options(roles,target,[])}</select></label><label>目标航班<select data-flow-flight-scope aria-label="第${order+1}优先目标航班">${Object.entries(flightScopeOptions).filter(([key])=>target!==row.arrivalRole||key!=='same-flight').map(([key,label])=>`<option value="${key}" ${targetScope(row,order)===key?'selected':''}>${label}</option>`).join('')}</select></label><div class="button-row"><button type="button" class="btn btn-sm" data-flow-up aria-label="上移第${order+1}优先目标" title="上移" ${order===0?'disabled':''}>↑</button><button type="button" class="btn btn-sm" data-flow-down aria-label="下移第${order+1}优先目标" title="下移" ${order===row.boardingRoles.length-1?'disabled':''}>↓</button><button type="button" class="btn btn-sm flow-remove-target" data-flow-remove-target>删除目标</button></div></li>`).join('')||'<li class="flow-empty-target">请增加目标岗位，添加后自动启用。</li>'}</ol></div>
    <p class="section-note" data-flow-feedback role="status"></p>
    ${!row.enabled&&row.boardingRoles.length?`<p class="section-note flow-inactive-note">此设置此前已停用，原状态保留。<button type="button" class="btn btn-sm" data-flow-resume ${[row.arrivalRole,...row.boardingRoles].some(role=>!roles.includes(role))?'disabled':''}>恢复使用</button></p>`:''}
    <div class="button-row flow-step-actions"><button type="button" class="btn btn-sm btn-outline-primary" data-flow-add-target ${[row.arrivalRole,...row.boardingRoles].some(role=>!roles.includes(role))?'disabled':''}>＋ 增加目标岗位</button>
    ${first?`<button type="button" class="btn btn-sm btn-outline-primary" data-flow-prepend ${!row.enabled||!availableFirst(indices)?'disabled':''}>＋ 在前面加岗位</button>`:''}
    <button type="button" class="btn btn-sm btn-outline-danger" data-flow-remove-rule>${row.flightNo==='*'?'删除此岗位的后续设置':'删除特例，恢复继承'}</button></div>
    ${first?indices.slice(1).map((_,i)=>card(indices,i+1)).join(''):''}</div></details>`;
  };
  return `<section class="flow-settings"><div class="button-row rule-add-actions flow-add-actions"><button type="button" id="add-flow-rule" class="btn btn-outline-primary">＋ 新增接续兼职</button></div><p class="section-note">选择来源岗位 → 增加优先目标 → 选择目标航班。添加目标后默认启用，保存配置后生效；禁止接续请到“禁止衔接”。</p>
    <p class="section-note">同一个人可兼任多个合法岗位。点击“在前面加岗位”，新岗位直接成为来源，原来的来源和后续在同一条里顺排；可在正常“来源岗位”下拉框修改新加的首岗。接机1后续设置自动沿用，不复制。第1、第2优先是目标偏好，不表示必须全部承担。</p>
    <datalist id="flow-flight-options">${flights.map(f=>`<option value="${html(f)}"></option>`).join('')}</datalist>
    ${ruleScopeSections(routes,'flow',route=>card(route.indices))}
    <section class="flow-exception-create" aria-label="集中添加航班特例"><h4>添加航班特例</h4><p class="section-note">没有特例的航班沿用通用设置。选择来源岗位和航班，沿用该岗位的通用设置后再调整。</p><div class="flow-exception-fields"><label>来源岗位<select data-flow-exception-source aria-label="特例来源岗位"><option value="" selected>选择来源岗位</option>${options(roles,'')}</select></label><label>航班号或系列<input data-flow-exception-flight list="flow-flight-options" aria-label="特例航班号或系列" placeholder="如 CX937 或 CX*" /></label><button type="button" id="add-flow-exception" class="btn btn-outline-secondary" ${roles.length?'':'disabled'}>＋ 添加航班特例</button></div><p class="section-note" data-flow-exception-feedback role="status"></p></section>
    ${legacyPreferences(state)}
    <p class="section-note">按顺位比较目标，可沿已有接续继续兼任多个岗位；不能绕过基本约束。${draft?'修改后点击上方保存配置':'修改即保存'}，重新排班生效。</p>
    <details id="flow-help" class="flow-help"><summary>接续与继承说明</summary>
    <p class="section-note">各段目标航班单独设置；共享大件池的较早/较晚在池内重叠航班中比较，不绑定某一架航班。大件按接续灵活交接时，轮换分钟只作人数计算和交接参考，可按接机需要提前交接；固定模式仍按实际时段检查。点击后续摘要展开修改，不会改变前一段范围。“同航班专岗专人”只禁止同一人兼做该航班其他岗位；跨航班仍检查资格、分人、禁止和明确实际占用。先保障非留空岗位；同一保障时段其他非留空岗位仍缺人时，优先留空岗先空着，即使同一个人能顺带兼职也不自动填回。人手充足或独立无缺口时段仍正常安排。</p>
    <p class="section-note">具体航班优先，其次最长系列，最后通用。旧规则未选目标范围时保留原行为，修改该来源后按所选范围保存。大件1/2/3是同池轮次，只有一轮为大件1；单轮规则优先，未配置该轮沿用“大件行李拦截”，明确关闭不回退。接续为偏好，不能绕过分人、禁止、专岗、资格或明确实际占用；较早/较晚比较保障结束，不要求开始更早；结束相等不属于早或晚，不推算交接分钟。本航班的分人和禁止只约束本航班，不阻止明确配置的跨班接续；另设的跨班禁止仍生效。同名来源和目标可按较早或较晚范围接续，但不能接本航班自身。配置、Excel和完整备份保留。</p></details></section>`;
}

function legacyPreferences(state:State):string {
  const rows=state.handoffPreferences.filter(row=>row.mode==='chain');
  if(!rows.length)return '';
  const preview=state.handoffSimplification;
  return `<details class="flow-legacy"><summary>旧版保留设置（${rows.length} 条）</summary><p class="section-note">旧设置和正式历史保留。可按下方预览整理成普通接续，保存配置后生效；整理会沿用各来源已有后续，可能允许更多合法组合，请核对。无法整理时，可先在上方维护替代接续，再删除对应旧设置；正式历史保留。</p><ul>${rows.map(row=>`<li>${html(row.flightNo)} · ${[row.arrivalRole,...row.boardingRoles].map(html).join(' → ')}（${row.enabled?'启用':'停用'}）<button type="button" class="btn btn-sm btn-outline-danger" data-flow-remove-legacy="${state.handoffPreferences.indexOf(row)}">删除此旧设置</button></li>`).join('')}</ul>${preview?.error?`<p class="section-note">${html(preview.error)}</p>`:preview?.preferences?`<p>整理后的全部接续：</p><ul>${preview.preferences.map(row=>`<li>${html(row.flightNo)} · ${html(row.arrivalRole)} → ${row.boardingRoles.map(role=>`${html(role)}（${flightScopeOptions[row.targetFlightScopes?.[role]??'same-flight']}）`).join('；')}（${row.enabled?'启用':'停用'}）</li>`).join('')}</ul><button type="button" class="btn btn-sm" data-flow-simplify>整理为接续兼职</button>`:'<p>请先保存配置并重新打开规则页查看整理预览。</p>'}<p data-flow-legacy-feedback role="status"></p></details>`;
}

export function bindHandoffPreferences(root: HTMLElement, state: State, save: (action: string, payload: unknown) => Promise<boolean>): void {
  let current = structuredClone(state.handoffPreferences);
  const roles = flowRoles(state);
  const commit = (next: HandoffPreference[], reveal?:HandoffPreference, focus=false) => {
    const previous=current;
    current = next;
    void save("save-handoff-preferences", { preferences: next }).then(ok=>{
      if(!ok&&current===next)current=previous;
      if(ok&&reveal){
        const index=next.indexOf(reveal);
        const shown=root.querySelector<HTMLElement>(`[data-flow-rule="${index}"]`);
        revealRule(root,shown?.id||cardId(reveal),focus);
      }
    }).catch(()=>{
      if(current===next)current=previous;
    });
  };
  const modify = (index: number, change: (row: HandoffPreference) => void) => {
    const next = structuredClone(current), row = next[index]; change(row);
    row.targetFlightScopes??={};
    row.targetFlightScopeList??=row.boardingRoles.map(target=>row.targetFlightScopes?.[target]??'same-flight');
    const counts=new Map<string,number>();
    for(const target of row.boardingRoles)counts.set(target,(counts.get(target)??0)+1);
    row.targetFlightScopes={...row.targetFlightScopes};
    for(const target of Object.keys(row.targetFlightScopes))if(!counts.has(target)||counts.get(target)!==1)delete row.targetFlightScopes[target];
    row.boardingRoles.forEach((target,position)=>{if(counts.get(target)===1)row.targetFlightScopes![target]=row.targetFlightScopeList![position];});
    if([...counts.values()].every(count=>count===1))delete row.targetFlightScopeList;
    commit(next,row);
  };
  const unique = (scope: string, source: string, except = -1, identity?:string) => !current.some((r, i) => i !== except && r.flightNo === scope && (identity?r.mode==='chain'&&r.id===identity:r.mode!=='chain'&&r.arrivalRole===source));
  const addException=(source:string,flight:string)=>{
    const feedback=root.querySelector<HTMLElement>('[data-flow-exception-feedback]');
    const reject=(message:string)=>{if(feedback)feedback.textContent=message;};
    if(!roles.includes(source)){reject('请先选择需要添加特例的来源岗位。');return;}
    const shared=current.find(r=>r.flightNo==='*'&&r.arrivalRole===source&&r.mode!=='chain');
    if([source,...(shared?.boardingRoles??[])].some(role=>!roles.includes(role)))return;
    if(!flight||flight==='*'||flight==='通用'||(flight.includes('*')&&!/^[A-Z0-9]+\*$/.test(flight))){reject('请填写具体航班号或以 * 结尾的系列，如 CX937 或 CX*。');return;}
    if(!unique(flight,source)){reject('该航班已有此来源岗位的特例，请展开航班特例修改。');return;}
    if(feedback)feedback.textContent='';
    const next=shared?{...structuredClone(shared),flightNo:flight}:{flightNo:flight,arrivalRole:source,boardingRoles:[],enabled:false,ordered:true};
    const details=root.querySelector<HTMLDetailsElement>('#flow-exceptions');if(details)details.open=true;
    commit([...current,next],next,true);
  };
  root.querySelectorAll<HTMLElement>("[data-flow-rule]").forEach(container => {
    const index = Number(container.dataset.flowRule);
    const source = container.querySelector<HTMLSelectElement>("[data-flow-source]")!;
    const scope = container.querySelector<HTMLInputElement>("[data-flow-scope]");
    if(scope){scope.oninput = () => scope.setCustomValidity("");
    scope.onchange = () => {
      const flight = scope.value.trim().toUpperCase();
      if (!flight || flight==='*'||flight==='通用'||(flight.includes("*") && !/^[A-Z0-9]+\*$/.test(flight)) || !unique(flight, current[index].arrivalRole, index,current[index].mode==='chain'?current[index].id:undefined)) {
        scope.setCustomValidity("填写具体航班或CX*；同航班同来源只能有一条特例"); scope.reportValidity(); return;
      }
      scope.setCustomValidity(""); modify(index, row => { row.flightNo = flight; });
    };}
    const prependButton=container.querySelector<HTMLButtonElement>('[data-flow-prepend]');
    if(prependButton&&(!prependButton.closest||prependButton.closest('[data-flow-rule]')===container))prependButton.onclick=()=>{
      const row=current[index];
      const route=handoffRoutes(current).find(route=>route.indices[0]===index);
      if(!route||!row.enabled||[row.arrivalRole,...row.boardingRoles].some(role=>!roles.includes(role)))return;
      const used=route.indices.flatMap(i=>[current[i].arrivalRole,...current[i].boardingRoles]);
      const role=roles.find(role=>!used.includes(role)&&unique(row.flightNo,role));
      if(!role)return;
      const next=structuredClone(current);
      const prior:HandoffPreference={flightNo:row.flightNo,arrivalRole:role,boardingRoles:[row.arrivalRole],enabled:true,ordered:true,targetFlightScopes:{[row.arrivalRole]:'same-flight'}};
      next.push(prior);
      commit(next,prior,true);
    };
    source.onchange = () => {
      if(!roles.includes(source.value))return;
      if (current[index].mode!=='chain' && !unique(current[index].flightNo, source.value, index)) {
        const attempted=source.value;
        source.value=current[index].arrivalRole;
        source.setCustomValidity('');
        const feedback=container.querySelector<HTMLElement>('[data-flow-feedback]');
        if(feedback)feedback.textContent=`${attempted} 已有该范围的来源规则，当前仍保留 ${current[index].arrivalRole}。要让 ${current[index].arrivalRole} 接续 ${attempted}，请点“增加目标岗位”并选择 ${attempted}；要在已有安排之前加岗，请用“在前面加岗位”。`;
        return;
      }
      source.setCustomValidity(""); modify(index, row => { const scopes=row.targetFlightScopeList??row.boardingRoles.map(role=>row.targetFlightScopes?.[role]??'same-flight'); const kept=row.boardingRoles.map((role,position)=>({role,scope:scopes[position]})).filter(target=>target.role!==source.value||target.scope==='later-overlap'||target.scope==='earlier-overlap'); row.arrivalRole = source.value; row.boardingRoles=kept.map(target=>target.role); row.targetFlightScopeList=kept.map(target=>target.scope); if (!row.boardingRoles.length) row.enabled = false; });
    };
    const resume=container.querySelector<HTMLButtonElement>('[data-flow-resume]');
    if(resume&&(!resume.closest||resume.closest('[data-flow-rule]')===container))resume.onclick=()=>modify(index,row=>{
      row.enabled=row.boardingRoles.length>0&&[row.arrivalRole,...row.boardingRoles].every(role=>roles.includes(role));
    });
    container.querySelector<HTMLButtonElement>("[data-flow-add-target]")!.onclick = () => modify(index, row => {
      if([row.arrivalRole,...row.boardingRoles].some(role=>!roles.includes(role)))return;
      const next = roles.find(r => r !== row.arrivalRole && !row.boardingRoles.includes(r)) ?? row.boardingRoles.at(-1) ?? roles.find(r=>r!==row.arrivalRole) ?? row.arrivalRole;
      const scopeList=row.targetFlightScopeList??=row.boardingRoles.map(target=>row.targetFlightScopes?.[target]??'same-flight');
      scopeList.push(next===row.arrivalRole?'later-overlap':'same-flight');
      row.boardingRoles.push(next); row.enabled = true; row.ordered = true;
    });
    container.querySelector<HTMLButtonElement>("[data-flow-remove-rule]")!.onclick = () => commit(current.filter((_, i) => i !== index));
    container.querySelectorAll<HTMLElement>("[data-flow-target-row]").forEach(target => {
      if(target.closest&&target.closest('[data-flow-rule]')!==container)return;
      const order = Number(target.dataset.flowTargetRow);
      target.querySelector<HTMLSelectElement>("[data-flow-target]")!.onchange = event => modify(index, row => { const next=(event.target as HTMLSelectElement).value; if(!roles.includes(next))return; const targetScopes=row.targetFlightScopeList??=row.boardingRoles.map(role=>row.targetFlightScopes?.[role]??'same-flight'); row.boardingRoles[order]=next; if(next===row.arrivalRole&&targetScopes[order]==='same-flight')targetScopes[order]='later-overlap'; row.ordered = true; });
      target.querySelector<HTMLSelectElement>("[data-flow-flight-scope]")!.onchange=event=>modify(index,row=>{
        const scope=(event.target as HTMLSelectElement).value as NonNullable<HandoffPreference['targetFlightScopes']>[string];
        if(row.boardingRoles[order]===row.arrivalRole&&scope==='same-flight')return;
        (row.targetFlightScopeList??=row.boardingRoles.map(role=>row.targetFlightScopes?.[role]??'same-flight'))[order]=scope;
      });
      target.querySelector<HTMLButtonElement>("[data-flow-remove-target]")!.onclick = () => modify(index, row => { row.boardingRoles.splice(order, 1); row.targetFlightScopeList?.splice(order,1); row.enabled = row.enabled && row.boardingRoles.length > 0; });
      for (const [selector, delta] of [["[data-flow-up]", -1], ["[data-flow-down]", 1]] as const) {
        target.querySelector<HTMLButtonElement>(selector)!.onclick = () => modify(index, row => {
          const other = order + delta;
          if (other >= 0 && other < row.boardingRoles.length) {
            [row.boardingRoles[order], row.boardingRoles[other]] = [row.boardingRoles[other], row.boardingRoles[order]];
            if(row.targetFlightScopeList)[row.targetFlightScopeList[order],row.targetFlightScopeList[other]]=[row.targetFlightScopeList[other],row.targetFlightScopeList[order]];
          }
          row.ordered = true;
        });
      }
    });
  });
  const add = () => {
    const source = roles.find(r => unique('*', r));
    if (source) {
      const row={ flightNo: '*', arrivalRole: source, boardingRoles: [], enabled: false, ordered: true, targetFlightScopes:{} };
      commit([...current,row],row,true);
    }
  };
  const addCommon=root.querySelector<HTMLButtonElement>('#add-flow-rule');
  if(addCommon){addCommon.disabled=!roles.some(r=>unique('*',r));addCommon.addEventListener('click',add);}
  root.querySelectorAll<HTMLButtonElement>('[data-flow-remove-legacy]').forEach(button=>{
    button.onclick=()=>{
      const index=Number(button.dataset.flowRemoveLegacy);
      if(current[index]?.mode==='chain')commit(current.filter((_,i)=>i!==index));
    };
  });
  root.querySelector<HTMLButtonElement>('[data-flow-simplify]')?.addEventListener('click',()=>{
    const preview=state.handoffSimplification;
    if(!preview?.preferences)return;
    if(JSON.stringify(current)!==JSON.stringify(preview.original)){
      const feedback=root.querySelector<HTMLElement>('[data-flow-legacy-feedback]');
      if(feedback)feedback.textContent='请先保存当前接续修改，再重新打开规则页查看整理预览，避免覆盖未保存的设置。';
      return;
    }
    commit(structuredClone(preview.preferences));
  });
  const addSpecial=root.querySelector<HTMLButtonElement>('#add-flow-exception');
  if(addSpecial){addSpecial.disabled=!roles.length;addSpecial.addEventListener('click',()=>{
    const source=root.querySelector<HTMLSelectElement>('[data-flow-exception-source]')?.value??'';
    const flight=root.querySelector<HTMLInputElement>('[data-flow-exception-flight]')?.value.trim().toUpperCase()??'';
    addException(source,flight);
  });}
}
