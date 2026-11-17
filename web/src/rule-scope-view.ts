import {html} from './workbench-model';

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
