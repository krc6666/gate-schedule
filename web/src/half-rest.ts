import {html, type Day, type State} from './workbench-model';

export const halfRestLabel = (mode: string | null | undefined) => mode === 'late-start' ? '上午半休' : mode === 'early-finish' ? '下午半休' : '不半休';

export function halfRestSelector(day: Day, people: State['people']): string {
  const candidates = people.filter(p=>p.active && (p.status??'正常')==='正常' && day.roster.includes(p.name));
  const count=day.memberAvailability.filter(a=>a.participating && a.halfRest).length;
  return `<details id="half-rest-selector" class="half-rest-selector"><summary class="btn btn-sm btn-outline-secondary">半休人员 <span data-half-rest-count>${count||''}</span></summary><div class="half-rest-menu" role="group" aria-label="半休人员多选">${candidates.map(p=>{
    const entry=day.memberAvailability.find(a=>a.memberName===p.name);
    return `<label class="half-rest-option"><input type="checkbox" data-half-rest-person="${html(p.name)}" aria-label="${html(p.name)}设为半休" ${entry?.halfRest&&entry.participating?'checked':''} ${entry?.participating?'':'disabled'} /><span>${html(p.name)}</span><select data-half-rest-mode="${html(p.name)}" aria-label="${html(p.name)}半休时段" ${entry?.participating?'':'disabled'}><option value="early-finish" ${entry?.halfRest!=='late-start'?'selected':''}>下午半休</option><option value="late-start" ${entry?.halfRest==='late-start'?'selected':''}>上午半休</option></select></label>`;
  }).join('')||'<p>请先选择正常启用的当天人员。</p>'}</div><p class="section-note">先勾选当天参加人员，再设置半休。06:00—12:00前开始的岗位算早班；12点和凌晨归另一时段，跨午按开始时间判断。上午休不排早班；下午休只排早班并尽量早点下班。无合适岗位仍保留半休。确认重新排班后生效，取消保留原表。</p></details>`;
}

export function syncHalfRest(root: HTMLElement, day: Day): void {
  root.querySelectorAll<HTMLInputElement>('[data-half-rest-person]').forEach(input=>{
    const entry=day.memberAvailability.find(a=>a.memberName===input.dataset.halfRestPerson);
    input.disabled=!entry?.participating;input.checked=!!(entry?.participating&&entry.halfRest);
    const select=[...root.querySelectorAll<HTMLSelectElement>('[data-half-rest-mode]')].find(s=>s.dataset.halfRestMode===input.dataset.halfRestPerson)!;
    select.disabled=input.disabled;if(entry?.halfRest)select.value=entry.halfRest;
  });
  const count=root.querySelector('[data-half-rest-count]');
  if(count)count.textContent=String(day.memberAvailability.filter(a=>a.participating&&a.halfRest).length||'');
}

export function bindHalfRest(root: HTMLElement, day: Day): void {
  root.querySelectorAll<HTMLInputElement|HTMLSelectElement>('[data-half-rest-person],[data-half-rest-mode]').forEach(input=>input.onchange=()=>{
    const name=input.dataset.halfRestPerson??input.dataset.halfRestMode;
    const entry=day.memberAvailability.find(a=>a.memberName===name);
    if(!entry?.participating)return;
    const checkbox=[...root.querySelectorAll<HTMLInputElement>('[data-half-rest-person]')].find(i=>i.dataset.halfRestPerson===name)!;
    const select=[...root.querySelectorAll<HTMLSelectElement>('[data-half-rest-mode]')].find(i=>i.dataset.halfRestMode===name)!;
    entry.halfRest=checkbox.checked?select.value as 'early-finish'|'late-start':null;
    syncHalfRest(root,day);
  });
}
