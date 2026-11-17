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
    <label>每人轮值（分钟）<input data-baggage-window-field="rotationMinutes" type="number" min="1" max="1440" value="${value.rotationMinutes}" ${setting?'':'disabled'}/></label>
    <label>尾段宽限（分钟）<input data-baggage-window-field="remainderToleranceMinutes" type="number" min="0" max="1440" placeholder="旧规则：尾段不增人" value="${value.remainderToleranceMinutes??''}" ${setting?'':'disabled'}/></label></div>
    <p class="section-note">重叠及首尾相接的窗口合并，有空档的分开。整轮之后剩余时间不超过宽限，由最后一人继续；超过宽限就增加一人。每轮60分钟、宽限15分钟时，1小时15分用1人，1小时16分用2人，2小时45分和3小时15分均用3人；整段不足一轮至少1人。宽限0表示有尾段就增人；留空沿用旧规则，尾段不增人。同一合并段每轮必须换人，不同段可复用。未填起飞时间时，截载以当天航班结束时间为基准。各轮按大件1、大件2、大件3编号，可用于接续、禁止及分人规则。关闭本开关沿用“轮换与走动”的旧设置。</p>
    ${setting&&coverage?`<div class="baggage-window-preview"><strong>已保存规则的计算预览 · ${coverage.requiredPeople} 轮值人次</strong>
    ${coverage.groups.map(g=>`<p>${g.flights.map(html).join(' / ')}：${html(g.start)}—${html(g.end)}，${g.requiredPeople} 名不同人员</p><ol>${g.turns.map(t=>`<li>${html(t.role??'')} · ${html(t.start)}—${html(t.end)}</li>`).join('')}</ol>`).join('')||'<p>当前日期没有需要大件保障的航班。</p>'}</div>`:''}
    <p class="section-note">修改后统一保存配置并重新排班；预览按已保存规则计算，未保存的改动尚未计算，当前班表和正式历史不会被预览改写。</p></section>`;
}

export function bindBaggageWindow(container:HTMLElement,state:State,modify:(fn:(r:WorkRules)=>void,repaint?:boolean)=>void):void {
  const enabled=container.querySelector<HTMLInputElement>('[data-baggage-window-enabled]');
  if(!enabled)return;
  enabled.onchange=()=>modify(r=>{r.baggageWindow=enabled.checked?structuredClone(state.baggageWindowDefaults!):null;},true);
  container.querySelectorAll<HTMLInputElement>('[data-baggage-window-field]').forEach(input=>{
    input.onchange=()=>modify(r=>{
      if(!r.baggageWindow)return;
      const field=input.dataset.baggageWindowField as keyof BaggageWindow;
      if(field==='remainderToleranceMinutes'&&input.value==='')delete r.baggageWindow.remainderToleranceMinutes;
      else r.baggageWindow[field]=input.valueAsNumber;
    });
  });
}
