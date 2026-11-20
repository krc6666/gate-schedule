import {html} from './workbench-model';

export function ruleCardId(prefix:string,...parts:string[]):string {
  return `${prefix}-${parts.map(part=>encodeURIComponent(part)).join('/')}`;
}

/** Reveal an edited item after the caller has redrawn its draft. */
export function revealRule(root:HTMLElement,id:string,focus=false):void {
  const card=root.querySelector<HTMLElement>(`[id="${id}"]`);
  if(!card)return;
  for(let node:HTMLElement|null=card;node&&root.contains(node);node=node.parentElement){
    if(node instanceof HTMLDetailsElement)node.open=true;
  }
  if(focus){
    card.scrollIntoView({block:'nearest'});
    card.querySelector<HTMLElement>('input:not([type=checkbox]),select,button')?.focus({preventScroll:true});
  }
}

export function ruleCategory(id:string,title:string,summary:string,body:string,day=false):string {
  return `<details id="${html(id)}" class="${day?'rule-subgroup':'workspace-section rule-group'} rule-category"><summary><strong>${html(title)}</strong><span data-rule-summary>${html(summary)}</span><span class="chevron" aria-hidden="true">⌄</span></summary><div class="rule-group-body">${body}</div></details>`;
}

/** Presentation only: retain saved order/indices and never resolve inheritance. */
export function ruleScopeSections<T extends {flightNo:string}>(rows:T[],prefix:string,card:(row:T,index:number)=>string):string {
  const common=rows.map((row,i)=>row.flightNo==='*'?card(row,i):'').join('');
  const exceptions=rows.map((row,i)=>row.flightNo!=='*'?card(row,i):'').join('');
  const count=rows.filter(row=>row.flightNo!=='*').length;
  return `<section class="rule-common" data-${html(prefix)}-common><h4>通用设置</h4>
    ${common||'<p class="section-note">尚未添加通用设置。</p>'}</section>
    <details id="${html(prefix)}-exceptions" class="rule-exceptions"><summary>航班特例 <span>${count} 项</span></summary>
    <div class="rule-exceptions-body"><p class="section-note">特例覆盖对应的通用设置；删除特例后恢复继承。停用特例表示明确不执行，不会回退通用。</p>
    ${exceptions||'<p class="section-note">尚未添加特例，所有航班沿用通用设置。</p>'}</div></details>`;
}
