import {baggageWindowHtml,bindBaggageWindow} from './baggage-window';
import {html,flowRoles, type WorkRules, type SeparationGroup, type State, type Day, type Plan} from './workbench-model';
import {forbiddenHandoffsHtml,bindForbiddenHandoffs} from './forbidden-handoffs';
const empty = (): WorkRules => ({pairs: [], baggage: []});
const options = (roles: string[], selected: string) => roles.map(r => `<option value="${html(r)}" ${r === selected ? 'selected' : ''}>${html(r)}</option>`).join('');
function legacyEditor(state: State, rules: WorkRules): string {
  const roles = state.configuration.roleConfigs.map(r => r.roleName);
  return `<details class="legacy-work-rules"><summary>高级：旧岗位关系（${rules.pairs.length}项，仍然生效）</summary><div class="table-responsive"><table class="data-table"><thead><tr><th>适用航班</th><th>岗位一</th><th>岗位二</th><th>范围</th><th>是否分人</th><th>操作</th></tr></thead><tbody>${rules.pairs.map((r, i) => `<tr data-work-pair="${i}"><td><input data-work-field="flightNo" value="${html(r.flightNo === '*' ? '通用' : r.flightNo)}" placeholder="通用或CX*" /></td><td><select data-work-field="firstRole">${options(roles, r.firstRole)}</select></td><td><select data-work-field="secondRole">${options(roles, r.secondRole)}</select></td><td><select data-work-field="scope">${Object.entries({'same-flight':'同航班','later-flight':'晚结束航班','after-baggage':'大件完成后'}).map(([k,v])=>`<option value="${k}" ${r.scope===k?'selected':''}>${v}</option>`).join('')}</select></td><td><select data-work-field="mode"><option value="separate" ${r.mode==='separate'?'selected':''}>必须分人</option><option value="sequence" ${r.mode!=='separate'?'selected':''}>按接续优先级安排</option></select></td><td><button type="button" class="btn btn-sm" data-work-delete-pair="${i}">删除</button></td></tr>`).join('') || '<tr><td colspan="6">未设置额外分人要求。</td></tr>'}</tbody></table></div><button type="button" class="btn btn-sm" data-work-add-pair>＋ 增加岗位关系</button></details>`;
}
function editor(state: State, rules: WorkRules, kind: string, plan:Plan|null): string {
  const roles = flowRoles(state);
  const groups = rules.separationGroups ?? [];
  return `<div data-work-editor="${kind}">${baggageWindowHtml(state,rules,(kind==='day'||!plan?.day.workRulesOverride)?plan?.baggageRulePreview:undefined)}${groups.map((g,i) => `
    <fieldset class="separation-group" data-separation-group="${i}">
      <legend>${html(g.name)} · ${html(g.flightNo==='*'?'通用':g.flightNo)} · <span data-group-state>${g.enabled?'已启用':'已停用，不执行分人'}</span></legend>
      <div class="separation-fields">
        <label>组名称 <input data-group-name value="${html(g.name)}" /></label>
        <label>适用航班 <input data-group-scope value="${html(g.flightNo==='*'?'通用':g.flightNo)}" placeholder="通用 / CX* / CX937" /></label>
        <label><input type="checkbox" data-group-enabled ${g.enabled?'checked':''} ${g.roles.length<2?'disabled':''} />启用同航班分人</label>
      </div>
      <p class="section-note ${g.enabled?'':'inactive-rule-note'}">${g.roles.length<2?'请至少选两个岗位，选满两岗自动启用。':g.enabled?'此集合已启用：组内任意两个岗位均不能同人；接机与登机放在同一组也会禁止相互接续。':'此集合已停用，当前不会禁止人员重复。点击“启用此集合”，保存配置后重新排班。'}${g.flightNo!=='*'?' 此项覆盖同组通用/系列设置；关闭不回退，删除后恢复继承。':''}</p>
      <div class="separation-roles">${g.roles.map((role,j)=>`<div data-group-role-row="${j}"><label>组内岗位 <select data-group-role>${options(roles.filter(r=>r===role||!g.roles.includes(r)),role)}</select></label><button type="button" class="btn btn-sm" data-group-remove-role="${j}">移除岗位</button></div>`).join('')}</div>
      <div class="button-row">
        ${!g.enabled&&g.roles.length>=2?'<button type="button" class="btn btn-sm btn-primary" data-group-activate>启用此集合</button>':''}
        <button type="button" class="btn btn-sm" data-group-add-role ${g.roles.length>=roles.length?'disabled':''}>＋ 添加岗位</button>
        <button type="button" class="btn btn-sm" data-group-exception>＋ 增加航班特例</button>
        <button type="button" class="btn btn-sm" data-group-delete>${g.flightNo==='*'?'删除通用设置':'删除此特例（恢复继承）'}</button>
        ${groups.filter(r=>r.id===g.id).length>1?'<button type="button" class="btn btn-sm" data-group-delete-all>删除整组（含特例）</button>':''}
      </div>
    </fieldset>`).join('') || '<p class="section-note">尚未设置分人组。可添加“接机组”“登机组”，自由选择组内岗位。</p>'}
    <button type="button" class="btn btn-sm" data-group-add>＋ 新增分人组</button>
    ${kind==='base' && state.groupedWorkRules && rules.pairs.some(r=>r.scope==='same-flight'&&r.mode==='separate')?'<button type="button" class="btn btn-sm" id="convert-work-groups">旧同航班分人规则转为分人组</button><p class="section-note">转换使用已保存配置；有未保存改动时，请先保存。跨航班及兼任许可仍保留在高级区。</p>':''}
    ${forbiddenHandoffsHtml(state,rules)}${legacyEditor(state,rules)}</div>`;
}
export function workRulesHtml(state: State, day: Day | null, plan: Plan | null): string {
  const current = state.workRules ?? empty();
  return `<details id="work-relation-rules" class="workspace-section rule-group"><summary><strong>大件保障、禁止同人集合与禁止衔接</strong><span data-rule-summary>集合 ${current.separationGroups?.filter(g=>g.enabled).length??0} 项启用 / ${current.separationGroups?.length??0} 项设置 · 禁止 ${current.forbiddenHandoffs?.filter(g=>g.enabled).length??0} 项启用</span></summary><div class="rule-group-body"><p class="section-note">同组同航班必须不同人，优先级不能绕过；缺人留缺口，手工违规标红。多组共同生效。接续去向与航班范围在本页上方配置。修改后统一保存配置并重新排班；保存规则不会立即改写当前班表。</p>${day?.workRulesOverride?'<p class="inactive-rule-note" role="status">当天独立规则已开启：下方长期集合和禁止衔接不会用于当天排班，请编辑“当天临时例外”或关闭当天独立规则。</p>':''}<button id="load-confirmed-work-rules" class="btn btn-sm btn-outline-secondary">载入已确认规则</button>${editor(state,current,'base',plan)}${day ? `<details id="day-work-relations"><summary>当天临时例外${day.workRulesOverride?' · 已开启，覆盖长期规则':''}</summary><label><input id="day-work-override" type="checkbox" ${day.workRulesOverride?'checked':''} />仅当天使用独立大件、分人及禁止衔接规则</label>${day.workRulesOverride?editor(state,day.workRules??current,'day',plan):''}</details>`:''}</div></details>`;
}
export function bindWorkRules(root: HTMLElement, state: State, day: Day | null, onRules: (rules: WorkRules) => void, onDay: () => void, render: () => void, savedRules = state.workRules): void {
  let base = structuredClone(state.workRules ?? empty());
  const modify = (kind: string, fn: (r: WorkRules) => void, repaint = false) => {
    const next = structuredClone(kind==='day' ? day?.workRules ?? base : base); fn(next);
    if(kind==='day' && day){day.workRules=next;onDay();}else{base=next;onRules(next);}
    const convert = root.querySelector<HTMLButtonElement>('#convert-work-groups');
    if(convert)convert.disabled=JSON.stringify(base)!==JSON.stringify(savedRules);
    if(repaint)render();
  };
  const convert = root.querySelector<HTMLButtonElement>('#convert-work-groups');
  if(convert){
    convert.disabled=JSON.stringify(base)!==JSON.stringify(savedRules);
    convert.onclick=()=>{
      if(!state.groupedWorkRules || JSON.stringify(base)!==JSON.stringify(savedRules))return;
      onRules(structuredClone(state.groupedWorkRules));render();
    };
  }
  root.querySelector<HTMLButtonElement>('#load-confirmed-work-rules')?.addEventListener('click', () => {
    if(!confirm('载入已确认的岗位分人规则？保存并重新排班后生效，原正式保留。'))return;
    const next=structuredClone(state.suggestedWorkRules??empty());
    if('baggageWindow' in base)next.baggageWindow=structuredClone(base.baggageWindow);
    onRules(next);render();
  });
  root.querySelector<HTMLInputElement>('#day-work-override')?.addEventListener('change', e => {
    if(!day)return;day.workRulesOverride=(e.target as HTMLInputElement).checked;day.workRules=structuredClone(base);onDay();render();
  });
  root.querySelectorAll<HTMLElement>('[data-work-editor]').forEach(container => {
    const kind=container.dataset.workEditor!;
    bindBaggageWindow(container,state,(fn,repaint)=>modify(kind,fn,repaint));
    bindForbiddenHandoffs(container,state,(fn,repaint)=>modify(kind,fn,repaint));
    const changeGroup=(index:number, fn:(g:SeparationGroup,r:WorkRules)=>void, repaint=false)=>modify(kind,r=>fn(r.separationGroups![index],r),repaint);
    container.querySelector<HTMLButtonElement>('[data-group-add]')!.onclick=()=>modify(kind,r=>{
      (r.separationGroups??=[]).push({id:crypto.randomUUID(),name:'新分人组',flightNo:'*',roles:[],enabled:false});
    },true);
    container.querySelectorAll<HTMLElement>('[data-separation-group]').forEach(card=>{
      const i=Number(card.dataset.separationGroup);
      card.querySelector<HTMLInputElement>('[data-group-name]')!.onchange=e=>changeGroup(i,g=>{g.name=(e.target as HTMLInputElement).value.trim();});
      card.querySelector<HTMLInputElement>('[data-group-scope]')!.onchange=e=>changeGroup(i,g=>{const v=(e.target as HTMLInputElement).value.trim();g.flightNo=v==='通用'?'*':v;});
      card.querySelector<HTMLInputElement>('[data-group-enabled]')!.onchange=e=>changeGroup(i,g=>{g.enabled=(e.target as HTMLInputElement).checked;},true);
      card.querySelector<HTMLButtonElement>('[data-group-activate]')?.addEventListener('click',()=>changeGroup(i,g=>{g.enabled=true;},true));
      card.querySelectorAll<HTMLSelectElement>('[data-group-role]').forEach((select,j)=>select.onchange=()=>changeGroup(i,g=>{g.roles[j]=select.value;},true));
      card.querySelectorAll<HTMLButtonElement>('[data-group-remove-role]').forEach(b=>b.onclick=()=>changeGroup(i,g=>{
        g.roles.splice(Number(b.dataset.groupRemoveRole),1);if(g.roles.length<2)g.enabled=false;
      },true));
      card.querySelector<HTMLButtonElement>('[data-group-add-role]')!.onclick=()=>changeGroup(i,g=>{
        const next=flowRoles(state).find(r=>!g.roles.includes(r));
        if(next){const count=g.roles.length;g.roles.push(next);if(count<2&&g.roles.length>=2)g.enabled=true;}
      },true);
      card.querySelector<HTMLButtonElement>('[data-group-exception]')!.onclick=()=>changeGroup(i,(g,r)=>{
        const flight=prompt('输入航班特例：如 CX* 或 CX937。同组具体航班优先，其次最长系列，最后通用。','CX*')?.trim();
        if(!flight)return;
        const scope=flight==='通用'?'*':flight;
        if(r.separationGroups!.some(row=>row.id===g.id&&row.flightNo===scope)){alert('同组已存在此航班范围，请直接修改对应设置。');return;}
        r.separationGroups!.push({...structuredClone(g),flightNo:scope});
      },true);
      card.querySelector<HTMLButtonElement>('[data-group-delete]')!.onclick=()=>modify(kind,r=>{r.separationGroups!.splice(i,1);},true);
      card.querySelector<HTMLButtonElement>('[data-group-delete-all]')?.addEventListener('click',()=>{
        if(confirm('删除整组及其所有航班特例？保存并重新排班后生效。'))changeGroup(i,(g,r)=>{r.separationGroups=r.separationGroups!.filter(row=>row.id!==g.id);},true);
      });
    });
    container.querySelectorAll<HTMLElement>('[data-work-pair]').forEach(row => {
      const index=Number(row.dataset.workPair);
      row.querySelectorAll<HTMLInputElement|HTMLSelectElement>('[data-work-field]').forEach(input=>input.onchange=()=>modify(kind, rules=>{
        const key=input.dataset.workField!;Object.assign(rules.pairs[index],{[key]:key==='flightNo'&&input.value==='通用'?'*':input.value});
      }));
    });
    container.querySelector<HTMLButtonElement>('[data-work-add-pair]')!.onclick=()=>modify(kind, rules=>{
      const roles=state.configuration.roleConfigs.map(r=>r.roleName);if(roles.length<2)return;
      rules.pairs.push({flightNo:'*',firstRole:roles[0],secondRole:roles[1],scope:'same-flight',mode:'separate'});
    },true);
    container.querySelectorAll<HTMLButtonElement>('[data-work-delete-pair]').forEach(b=>b.onclick=()=>modify(kind,r=>{r.pairs.splice(Number(b.dataset.workDeletePair),1);},true));
  });
}
