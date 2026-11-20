import {flowRoles,flowRoleOptions as options,flowRoleNotice,html, type WorkRules, type State, type ForbiddenHandoff} from './workbench-model';
import {ruleScopeSections,ruleCardId,revealRule} from './rule-scope-view';

const scopes={'same-flight':'本航班','later-overlap':'重叠且晚结束的其他航班'};

export function forbiddenHandoffsHtml(state:State,rules:WorkRules,kind='base'):string {
  const roles=flowRoles(state);
  return `<section class="forbidden-handoffs"><div class="button-row rule-add-actions"><button type="button" class="btn btn-outline-primary" data-forbidden-add-rule>＋ 新增禁止衔接</button></div>
    <p class="section-note">明确指定哪些岗位不能由同一人接续承担。禁止优先于接续兼职，即使绕经其他岗位也不能规避；本航班和晚结束重叠航班分别设置。修改后保存配置并重新排班。</p>
    ${ruleScopeSections(rules.forbiddenHandoffs??[],`forbidden-${kind}`, (row,i)=>`<details id="${ruleCardId('forbidden-card',kind,row.flightNo,row.sourceRole)}" class="flow-rule rule-card ${row.flightNo==='*'?'is-common':'is-exception'}" data-forbidden-rule="${i}">
      <summary><span class="rule-scope-badge">${row.flightNo==='*'?'通用':html(row.flightNo)+' 特例'}</span><strong>${html(row.sourceRole)}</strong><span class="rule-status">${row.enabled?'已启用':'已停用'}</span><span data-rule-path>${row.targets.length?'禁止接续：'+row.targets.map(t=>`${html(t.role)}（${scopes[t.flightScope]}）`).join('、'):'尚未添加禁止目标'}</span><span class="chevron" aria-hidden="true">⌄</span></summary><div class="rule-card-body">
      ${flowRoleNotice(roles,[row.sourceRole,...row.targets.map(target=>target.role)])}
      <div class="form-grid">${row.flightNo==='*'?'':`<label>来源航班<input data-forbidden-scope value="${html(row.flightNo)}" placeholder="CX* 或 CX937" /></label>`}
      <label>来源岗位<select data-forbidden-source>${options(roles,row.sourceRole)}</select></label>
      <label class="switch-label"><input type="checkbox" data-forbidden-enabled ${row.enabled?'checked':''} ${row.targets.length?'':'disabled'}/>启用禁止衔接</label></div>
      ${row.targets.map((target,j)=>`<div class="forbidden-target" data-forbidden-target-row="${j}">
        <label>禁止目标岗位<select data-forbidden-target>${options(roles.filter(r=>r!==row.sourceRole),target.role)}</select></label>
        <label>目标航班<select data-forbidden-flight>${Object.entries(scopes).map(([key,name])=>`<option value="${key}" ${key===target.flightScope?'selected':''}>${name}</option>`).join('')}</select></label>
        <button type="button" class="btn btn-sm" data-forbidden-remove-target>删除目标</button></div>`).join('')}
      ${row.targets.length?'':'<p class="section-note">添加目标后自动启用，默认禁止本航班。</p>'}
      <div class="button-row"><button type="button" class="btn btn-sm" data-forbidden-add-target>＋ 添加禁止目标</button>
      <button type="button" class="btn btn-sm" data-forbidden-exception ${[row.sourceRole,...row.targets.map(t=>t.role)].some(role=>!roles.includes(role))?'disabled':''}>＋ 增加航班特例</button>
      <button type="button" class="btn btn-sm btn-outline-danger" data-forbidden-delete>${row.flightNo==='*'?'删除此禁止衔接':'删除特例，恢复继承'}</button></div>
      ${row.flightNo==='*'?'':'<p class="section-note">特例替换此来源的全部通用目标；关闭不回退，删除恢复继承。</p>'}
    </div></details>`)}
    </section>`;
}

/** Owns only editing; domain owns the meaning and final legality. */
export function bindForbiddenHandoffs(container:HTMLElement,state:State,modify:(change:(rules:WorkRules)=>void,repaint?:boolean)=>void,root=container):void {
  const roles=flowRoles(state);
  const kind=container.dataset.workEditor!;
  const show=(row:ForbiddenHandoff,focus=false)=>revealRule(root,ruleCardId('forbidden-card',kind,row.flightNo,row.sourceRole),focus);
  const change=(i:number,fn:(row:ForbiddenHandoff,rules:WorkRules)=>void,repaint=true)=>{
    let edited:ForbiddenHandoff;
    modify(r=>{edited=r.forbiddenHandoffs![i];fn(edited,r);},repaint);
    if(repaint)show(edited!);
  };
  const add=container.querySelector<HTMLButtonElement>('[data-forbidden-add-rule]')!;
  add.disabled=!roles.some(role=>!(state.workRules?.forbiddenHandoffs??[]).some(row=>row.flightNo==='*'&&row.sourceRole===role));
  add.onclick=()=>{
    let added:ForbiddenHandoff|undefined;
    modify(r=>{
    const current=r.forbiddenHandoffs??=[];
    const role=roles.find(role=>!current.some(row=>row.flightNo==='*'&&row.sourceRole===role));
    if(role){added={flightNo:'*',sourceRole:role,targets:[],enabled:false};current.push(added);}
    },true);
    if(added)show(added,true);
  };
  container.querySelectorAll<HTMLElement>('[data-forbidden-rule]').forEach(card=>{
    const i=Number(card.dataset.forbiddenRule);
    const scopeInput=card.querySelector<HTMLInputElement>('[data-forbidden-scope]');
    if(scopeInput)scopeInput.onchange=e=>change(i,row=>{
      const v=(e.target as HTMLInputElement).value.trim().toUpperCase();row.flightNo=v==='通用'?'*':v;
    });
    card.querySelector<HTMLSelectElement>('[data-forbidden-source]')!.onchange=e=>change(i,row=>{
      row.sourceRole=(e.target as HTMLSelectElement).value;row.targets=row.targets.filter(t=>t.role!==row.sourceRole);
      if(!row.targets.length)row.enabled=false;
    });
    card.querySelector<HTMLInputElement>('[data-forbidden-enabled]')!.onchange=e=>change(i,row=>{row.enabled=(e.target as HTMLInputElement).checked;});
    card.querySelector<HTMLButtonElement>('[data-forbidden-add-target]')!.onclick=()=>change(i,row=>{
      for(const flightScope of ['same-flight','later-overlap'] as const){
        const role=roles.find(r=>r!==row.sourceRole&&!row.targets.some(t=>t.role===r&&t.flightScope===flightScope));
        if(role){const empty=!row.targets.length;row.targets.push({role,flightScope});if(empty)row.enabled=true;return;}
      }
    });
    card.querySelector<HTMLButtonElement>('[data-forbidden-delete]')!.onclick=()=>modify(r=>{r.forbiddenHandoffs!.splice(i,1);},true);
    card.querySelector<HTMLButtonElement>('[data-forbidden-exception]')!.onclick=()=>change(i,(row,rules)=>{
      if([row.sourceRole,...row.targets.map(t=>t.role)].some(role=>!roles.includes(role)))return;
      const v=prompt('输入来源航班特例，如 CX* 或 CX937。','CX*')?.trim().toUpperCase();if(!v)return;
      const scope=v==='通用'?'*':v;
      if(rules.forbiddenHandoffs!.some(r=>r.flightNo===scope&&r.sourceRole===row.sourceRole)){alert('该来源已有此航班特例，请直接修改。');return;}
      const details=container.querySelector<HTMLDetailsElement>('.forbidden-handoffs > .rule-exceptions');if(details)details.open=true;
      rules.forbiddenHandoffs!.push({...structuredClone(row),flightNo:scope});
      queueMicrotask(()=>show({...row,flightNo:scope},true));
    });
    card.querySelectorAll<HTMLElement>('[data-forbidden-target-row]').forEach(target=>{
      const j=Number(target.dataset.forbiddenTargetRow);
      target.querySelector<HTMLSelectElement>('[data-forbidden-target]')!.onchange=e=>change(i,row=>{row.targets[j].role=(e.target as HTMLSelectElement).value;});
      target.querySelector<HTMLSelectElement>('[data-forbidden-flight]')!.onchange=e=>change(i,row=>{row.targets[j].flightScope=(e.target as HTMLSelectElement).value as 'same-flight'|'later-overlap';});
      target.querySelector<HTMLButtonElement>('[data-forbidden-remove-target]')!.onclick=()=>change(i,row=>{row.targets.splice(j,1);if(!row.targets.length)row.enabled=false;});
    });
  });
}
