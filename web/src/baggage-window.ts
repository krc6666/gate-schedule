import {html,type WorkRules,type State,type BaggageWindow,type BaggageCoverage} from './workbench-model';

export function baggageWindowHtml(state:State,rules:WorkRules,coverage?:BaggageCoverage):string {
  const setting=rules.baggageWindow,defaults=state.baggageWindowDefaults;
  if(!defaults)return '';
  const value=setting??defaults;
  return `<section class="baggage-window"><h3>大件开检与截载</h3>
    <label class="switch-label"><input type="checkbox" data-baggage-window-enabled ${setting?'checked':''}/>按开检至截载计算轮换人数</label>
    <div class="form-grid">
    <label>开检：模板结束时间提前（分钟）<input data-baggage-window-field="startBeforeMinutes" type="number" min="1" max="1440" value="${value.startBeforeMinutes}" ${setting?'':'disabled'}/></label>
    <label>截载：起飞时间提前（分钟）<input data-baggage-window-field="endBeforeMinutes" type="number" min="0" max="1439" value="${value.endBeforeMinutes}" ${setting?'':'disabled'}/></label>
    <label>每人轮值（分钟）<input data-baggage-window-field="rotationMinutes" type="number" min="1" max="1440" value="${value.rotationMinutes}" ${setting?'':'disabled'}/></label></div>
    <p class="section-note">未单独填写起飞时间时，以航班模板的结束时间计算截载。当天修改航班结束后，按当天值计算。重叠及首尾相接的窗口合并，有空档的分开；人数按完整轮值时长向下取整，尾段由最后一人继续覆盖；例如3小时15分钟按3人，整段不足一轮至少1人。同一段合并保障每轮必须换人，不能让同一人重复站多轮；不同保障段可复用人员。各轮按大件1、大件2、大件3依次编号，可在优先接续、禁止衔接及分人集合中选择；只有一轮时为大件1。这里只计算大件，不改变其他岗位的流程。启用后使用本栏轮值规则，关闭后沿用“轮换与走动”的旧设置。</p>
    ${setting&&coverage?`<div class="baggage-window-preview"><strong>已保存规则的计算预览 · ${coverage.requiredPeople} 轮值人次</strong>
    ${coverage.groups.map(g=>`<p>${g.flights.map(html).join(' / ')}：${html(g.start)}—${html(g.end)}，${g.requiredPeople} 名不同人员</p><ol>${g.turns.map(t=>`<li>${html(t.role??'')} · ${html(t.start)}—${html(t.end)}</li>`).join('')}</ol>`).join('')||'<p>当前日期没有需要大件保障的航班。</p>'}</div>`:''}
    <p class="section-note">修改后统一保存配置并重新排班；预览按已保存规则计算，未保存的改动尚未计算，当前班表和正式历史不会被预览改写。</p></section>`;
}

export function bindBaggageWindow(container:HTMLElement,state:State,modify:(fn:(r:WorkRules)=>void,repaint?:boolean)=>void):void {
  const enabled=container.querySelector<HTMLInputElement>('[data-baggage-window-enabled]');
  if(!enabled)return;
  enabled.onchange=()=>modify(r=>{r.baggageWindow=enabled.checked?structuredClone(state.baggageWindowDefaults!):null;},true);
  container.querySelectorAll<HTMLInputElement>('[data-baggage-window-field]').forEach(input=>{
    input.onchange=()=>modify(r=>{if(r.baggageWindow)r.baggageWindow[input.dataset.baggageWindowField as keyof BaggageWindow]=input.valueAsNumber;});
  });
}
