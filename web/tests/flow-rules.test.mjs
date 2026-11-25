import assert from 'node:assert/strict';
import {test} from 'node:test';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
const bundle=await build({stdin:{contents:'export {handoffPreferences,bindHandoffPreferences} from "./handoff-preferences"; export {templatePositions} from "./template-config"; export {workRulesHtml} from "./work-rules"; export {forbiddenHandoffsHtml} from "./forbidden-handoffs";',resolveDir:fileURLToPath(new URL('../src/',import.meta.url))},bundle:true,write:false,format:'esm',target:'es2022'});
const {handoffPreferences,bindHandoffPreferences,templatePositions,workRulesHtml,forbiddenHandoffsHtml}=await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);
const state=()=>({configuration:{roleConfigs:['大件行李拦截','接机1','入境申报','自定义<&'].map(roleName=>({roleName}))},flightLibrary:[],handoffPreferences:[{flightNo:'CX*',arrivalRole:'大件行李拦截',boardingRoles:['接机1','入境申报'],enabled:true,ordered:true}]});

test('只保留原接续编辑，当前来源可添加前序沿用后续',()=>{
  const markup=handoffPreferences(state(),true);
  assert.doesNotMatch(markup,/id="add-flow-chain"|data-flow-mode|新增同人多岗组合/);
  assert.doesNotMatch(markup,/data-flow-predecessor|flow-predecessor/);
  assert.match(markup,/在前面加岗位/);
  assert.match(markup,/后续.*沿用|沿用.*后续/);
});
test('常规接续不显示启用开关和逐条特例，底部集中明确来源与航班',()=>{
  const s=state(),before=structuredClone(s),markup=handoffPreferences(s,true);
  assert.doesNotMatch(markup,/data-flow-enabled|data-flow-exception\s|启用接续兼职|已启用/);
  assert.equal((markup.match(/id="add-flow-exception"/g)||[]).length,1);
  assert.ok(markup.indexOf('data-flow-exception-source')>markup.indexOf('id="flow-exceptions"'));
  assert.match(markup,/data-flow-exception-flight/);
  assert.deepEqual(s,before);
});

test('插首岗后同一卡从最前来源顺排，后续维持原索引和对象',()=>{
  const s=state();s.flowRuleRoles=['大件1','接机1','秩序维护','登机查验'];
  s.handoffPreferences=[{flightNo:'*',arrivalRole:'接机1',boardingRoles:['秩序维护'],enabled:true,targetFlightScopes:{'秩序维护':'same-flight'}},
    {flightNo:'*',arrivalRole:'秩序维护',boardingRoles:['登机查验'],enabled:true,targetFlightScopes:{'登机查验':'same-flight'}},
    {flightNo:'*',arrivalRole:'大件1',boardingRoles:['接机1'],enabled:true,targetFlightScopes:{'接机1':'same-flight'}}];
  const before=structuredClone(s),markup=handoffPreferences(s,true);
  assert.equal((markup.match(/data-flow-route-root=/g)||[]).length,1);
  assert.match(markup,/data-flow-route-root="2"/);
  assert.match(markup,/大件1 → 接机1 → 秩序维护 → 登机查验/);
  assert.ok(markup.indexOf('data-flow-rule="2"')<markup.indexOf('data-flow-rule="0"'));
  assert.ok(markup.indexOf('data-flow-rule="0"')<markup.indexOf('data-flow-rule="1"'));
  assert.deepEqual(s,before,'展示不能重写后续设置');
});
test('早晚跨班接续同卡维护，标题标清范围，后续默认收起',()=>{
  for(const scope of ['earlier-overlap','later-overlap']){
    const s=state();s.flowRuleRoles=['大件1','接机1','入境申报'];
    s.handoffPreferences=[{flightNo:'*',arrivalRole:'大件1',boardingRoles:['接机1'],enabled:true,targetFlightScopes:{'接机1':scope}},
      {flightNo:'*',arrivalRole:'接机1',boardingRoles:['入境申报'],enabled:true,targetFlightScopes:{'入境申报':'same-flight'}}];
    const before=structuredClone(s),markup=handoffPreferences(s,true);
    assert.equal((markup.match(/data-flow-route-root=/g)||[]).length,1,'切换范围不拆卡');
    const title=markup.match(/<summary>[\s\S]*?<\/summary>/)[0];
    assert.match(title,new RegExp(scope==='earlier-overlap'?'早结束重叠航班':'晚结束重叠航班'));
    assert.match(title,/大件1[\s\S]*接机1[\s\S]*入境申报/);
    const linked=markup.match(/<details[^>]*data-flow-rule="1"[^>]*>/)[0];
    assert.doesNotMatch(linked,/\bopen\b/,'后续按需展开，不自动撑高整条');
    assert.deepEqual(s,before);
  }
});
test('同名目标可同时显示早晚航班范围并保持独立优先顺序',()=>{
  const s=state();s.flowRuleRoles=['大件2','登机查验'];
  s.handoffPreferences=[{flightNo:'*',arrivalRole:'大件2',boardingRoles:['登机查验','登机查验'],enabled:true,ordered:true,
    targetFlightScopeList:['earlier-overlap','later-overlap']}];
  const markup=handoffPreferences(s,true);
  assert.equal((markup.match(/data-flow-target-row=/g)||[]).length,2);
  assert.match(markup,/第1优先目标岗位<select[^>]*>[\s\S]*?value="登机查验" selected/);
  assert.match(markup,/value="earlier-overlap" selected/);
  assert.match(markup,/value="later-overlap" selected/);
  assert.match(markup,/1\. 登机查验（早结束重叠航班）[\s\S]*2\. 登机查验（晚结束重叠航班）/);
});
test('同名目标排序时逐项范围同步移动并保存',()=>{
  const s=state(),saved=[];
  s.flowRuleRoles=['大件2','登机查验'];
  s.handoffPreferences=[{flightNo:'*',arrivalRole:'大件2',boardingRoles:['登机查验','登机查验'],enabled:true,ordered:true,
    targetFlightScopeList:['earlier-overlap','later-overlap']}];
  const targets=[0,1].map(order=>{
    const controls={up:{},down:{},select:{},scope:{},remove:{}};
    return {dataset:{flowTargetRow:String(order)},controls,closest:()=>container,querySelector:selector=>({'[data-flow-up]':controls.up,'[data-flow-down]':controls.down,'[data-flow-target]':controls.select,'[data-flow-flight-scope]':controls.scope,'[data-flow-remove-target]':controls.remove}[selector])};
  });
  const container={dataset:{flowRule:'0'},querySelector:selector=>selector==='[data-flow-source]'?{value:'大件2',setCustomValidity(){}}:['[data-flow-add-target]','[data-flow-remove-rule]'].includes(selector)?{}:null,querySelectorAll:()=>targets};
  bindHandoffPreferences({querySelectorAll:()=>[container],querySelector:()=>null},s,async(_,payload)=>{saved.push(payload.preferences);return true;});
  targets[1].controls.up.onclick();
  assert.deepEqual(saved.at(-1)[0].targetFlightScopeList,['later-overlap','earlier-overlap']);
  assert.deepEqual(saved.at(-1)[0].boardingRoles,['登机查验','登机查验']);
});
test('共享后续卡片定位互不冲突，停用/跨班尾项保留明确说明',()=>{
  const s=state();s.handoffPreferences=[
    {flightNo:'*',arrivalRole:'甲',boardingRoles:['丙'],enabled:true,targetFlightScopes:{'丙':'same-flight'}},
    {flightNo:'*',arrivalRole:'乙',boardingRoles:['丙'],enabled:true,targetFlightScopes:{'丙':'same-flight'}},
    {flightNo:'*',arrivalRole:'丙',boardingRoles:['丁'],enabled:false,targetFlightScopes:{'丁':'later-overlap'}}];
  const markup=handoffPreferences(s,true),ids=Array.from(markup.matchAll(/<details id="([^"]+)"/g),m=>m[1]);
  assert.equal(new Set(ids).size,ids.length);
  assert.equal((markup.match(/data-flow-rule="2"/g)||[]).length,2,'原后续对象在两条里均可维护');
  assert.match(markup,/后续已停用：1\. 丁（晚结束重叠航班）/);
});

function prependHarness(rows,roles=['大件1','接机1','秩序维护','登机查验']){
  const s=state();s.flowRuleRoles=['大件1','接机1','秩序维护','登机查验'];s.handoffPreferences=rows;
  s.flowRuleRoles=roles;
  const saved=[],controls={'[data-flow-source]':{},'[data-flow-enabled]':{},'[data-flow-add-target]':{},'[data-flow-remove-rule]':{},'[data-flow-prepend]':{},'[data-flow-feedback]':{textContent:''}};
  const container={dataset:{flowRule:'0'},querySelector:q=>controls[q],querySelectorAll:()=>[]};
  bindHandoffPreferences({querySelectorAll:()=>[container],querySelector:()=>null},s,async(_,p)=>{saved.push(p.preferences);return true;});
  return {s,saved,controls,add:()=>controls['[data-flow-prepend]'].onclick()};
}
test('点击直接插入新来源，原来源及后续保持，使用正常来源控件修改',()=>{
  const downstream={flightNo:'*',arrivalRole:'接机1',boardingRoles:['秩序维护','登机查验'],enabled:true,targetFlightScopes:{'秩序维护':'same-flight','登机查验':'same-flight'}};
  const h=prependHarness([downstream]);h.add();
  assert.deepEqual(h.saved[0][0],downstream);
  assert.deepEqual(h.saved[0][1].boardingRoles,['接机1'],'不复制后续目标');
  assert.equal(h.s.handoffPreferences.length,1,'原资料不原地修改');
  const markup=handoffPreferences({...h.s,handoffPreferences:h.saved[0]},true);
  assert.match(markup,/data-flow-route-root="1"/);
  assert.match(markup,/<strong>大件1<\/strong>/);
  assert.match(markup,/<select data-flow-source[^>]*><option value="大件1" selected>/);
  assert.doesNotMatch(markup,/data-flow-predecessor/);
});
test('插首岗不覆盖已有来源/顺位/范围，满额或停用时不造重复来源',()=>{
  const downstream={flightNo:'CX937',arrivalRole:'接机1',boardingRoles:['秩序维护'],enabled:true,targetFlightScopes:{'秩序维护':'later-overlap'}};
  const prior={flightNo:'*',arrivalRole:'大件1',boardingRoles:['登机查验'],enabled:true,targetFlightScopes:{'登机查验':'later-overlap'}};
  const h=prependHarness([downstream,prior]);h.add();
  assert.deepEqual(h.saved[0].slice(0,2),[downstream,prior]);
  assert.deepEqual(h.saved[0][2],{flightNo:'CX937',arrivalRole:'大件1',boardingRoles:['接机1'],enabled:true,ordered:true,targetFlightScopes:{'接机1':'same-flight'}});
  const allTaken=prependHarness([{...downstream,flightNo:'*'},prior],['大件1','接机1','秩序维护']);allTaken.add();assert.equal(allTaken.saved.length,0);
  const disabled=prependHarness([{...downstream,enabled:false}]);disabled.add();assert.equal(disabled.saved.length,0);
  const unknown=prependHarness([{...downstream,arrivalRole:'已撤去的旧岗'}]);unknown.add();assert.equal(unknown.saved.length,0,'旧失效引用只读，不能产生新关系');
});
test('旧设置整理使用预览，拒绝覆盖当前未保存修改',()=>{
  const s=state();s.handoffSimplification={original:structuredClone(s.handoffPreferences),preferences:[]};
  const handlers={},feedback={textContent:''},saved=[];
  const root={querySelectorAll:()=>[],querySelector:q=>q==='[data-flow-legacy-feedback]'?feedback:['[data-flow-simplify]','#add-flow-rule'].includes(q)?{addEventListener:(_,fn)=>handlers[q]=fn}:null};
  bindHandoffPreferences(root,s,async(_,p)=>{saved.push(p.preferences);return true;});
  handlers['[data-flow-simplify]']();assert.deepEqual(saved,[[]]);
  handlers['#add-flow-rule']();const count=saved.length;handlers['[data-flow-simplify]']();
  assert.equal(saved.length,count);assert.match(feedback.textContent,/未保存/);
});
test('保存失败回到先前接续草稿，下一次添加不会带上失败内容',async()=>{
  const s=state(),handlers={},saved=[];
  s.flowRuleRoles=['大件1','接机1','秩序维护'];s.handoffPreferences=[];
  const root={querySelectorAll:()=>[],querySelector:q=>q==='#add-flow-rule'?{addEventListener:(_,fn)=>handlers.add=fn}:null};
  bindHandoffPreferences(root,s,async(_,payload)=>{saved.push(payload.preferences);return false;});
  handlers.add();await Promise.resolve();handlers.add();
  assert.equal(saved[1].length,1,'失败的新来源不遗留为隐藏草稿');
  assert.equal(saved[1][0].arrivalRole,'大件1');
});

test('跨航班目标可选择来源同名岗位，目标列表不能全局排除来源',()=>{
  const s=state();s.flowRuleRoles=['接机1','接机2'];
  s.handoffPreferences=[{flightNo:'*',arrivalRole:'接机1',boardingRoles:['接机2'],enabled:true,targetFlightScopes:{'接机2':'later-overlap'}}];
  const markup=handoffPreferences(s,true);
  const targets=markup.match(/<select data-flow-target [\s\S]*?<\/select>/)[0];
  assert.match(targets,/<option value="接机1"/);
});
test('接续及禁止的目标航班都有较早选项，同名跨班可保留较早',()=>{
  const s=state();s.flowRuleRoles=['接机1','接机2'];
  s.handoffPreferences=[{flightNo:'*',arrivalRole:'接机1',boardingRoles:['接机1'],enabled:true,targetFlightScopes:{'接机1':'earlier-overlap'}}];
  const markup=handoffPreferences(s,true);
  assert.match(markup,/<option value="earlier-overlap" selected>重叠时段中较早结束的航班/);
  const select=markup.match(/<select data-flow-flight-scope[\s\S]*?<\/select>/)[0];
  assert.doesNotMatch(select,/value="same-flight"/);assert.match(select,/value="later-overlap"/);
  assert.match(markup,/早结束重叠航班/);
  const policy={pairs:[],baggage:[],forbiddenHandoffs:[{flightNo:'*',sourceRole:'接机1',targets:[{role:'接机1',flightScope:'earlier-overlap'}],enabled:true}]};
  const forbidden=forbiddenHandoffsHtml(s,policy);
  assert.match(forbidden,/<option value="earlier-overlap" selected>/);
  const target=forbidden.match(/<select data-forbidden-target[\s\S]*?<\/select>/)[0];
  assert.match(target,/<option value="接机1" selected>/);
  assert.doesNotMatch(forbidden.match(/<select data-forbidden-flight[\s\S]*?<\/select>/)[0],/value="same-flight"/);
});

test('选重复来源时保留原来源，并指明从增加目标设置接续',()=>{
  const original=state();
  original.flowRuleRoles=['大件1','接机1','接机2'];
  original.handoffPreferences=[{flightNo:'*',arrivalRole:'大件1',boardingRoles:[],enabled:false},
    {flightNo:'*',arrivalRole:'接机1',boardingRoles:['接机2'],enabled:true}];
  const saved=[],feedback={textContent:''};
  const source={value:'大件1',setCustomValidity(){},reportValidity(){}};
  const container={dataset:{flowRule:'0'},querySelector:s=>s==='[data-flow-source]'?source:s==='[data-flow-feedback]'?feedback:s==='[data-flow-scope]'||s==='[data-flow-exception]'||s==='[data-flow-mode]'?null:{},querySelectorAll:()=>[]};
  bindHandoffPreferences({querySelectorAll:()=>[container],querySelector:()=>null},original,async(_,p)=>{saved.push(p);return true;});
  source.value='接机1';source.onchange();
  assert.equal(source.value,'大件1','拒绝后不能显示新来源，标题却仍是大件1');
  assert.equal(saved.length,0);
  assert.match(feedback.textContent,/接机1.*已有.*来源/);
  assert.match(feedback.textContent,/增加目标岗位/);
});

test('配置岗位表没有重复接续编辑区，规则页保留原设置',()=>{
  const s=state(),before=structuredClone(s);
  assert.doesNotMatch(templatePositions(s,()=>''),/data-flow-rule|data-flow-target|接续兼职/);
  assert.match(handoffPreferences(s,true),/data-flow-rule/);
  assert.deepEqual(s,before);
});

test('流动填写只消费可选目录，失效旧引用明确只读，不静默改值或污染其他选择',()=>{
  const s=state();
  s.flowRuleRoles=['接机1','入境申报'];
  s.workRules={pairs:[],baggage:[],separationGroups:[{id:'g',name:'旧分人',flightNo:'*',roles:['大件行李拦截','接机1'],enabled:true}],forbiddenHandoffs:[{flightNo:'*',sourceRole:'大件行李拦截',targets:[{role:'入境申报',flightScope:'same-flight'}],enabled:true}]};
  const before=structuredClone(s);
  for(const markup of [handoffPreferences(s,true),workRulesHtml(s,null,null),forbiddenHandoffsHtml(s,s.workRules)]){
    const options=markup.match(/<option[^>]*value="大件行李拦截"[^>]*>/g)??[];
    assert.ok(options.length>0,'已有引用仍有占位项，防浏览器自动换岗');
    assert.ok(options.every(option=>option.includes('selected')&&option.includes('disabled')),'目录外岗位仅保留禁用原引用');
    assert.match(markup,/不在当前岗位目录/);
  }
  assert.deepEqual(s,before,'渲染不得清除旧规则');
  const blank={...s,handoffPreferences:[{flightNo:'*',arrivalRole:'接机1',boardingRoles:['入境申报'],enabled:true}]};
  assert.doesNotMatch(handoffPreferences(blank,true),/<option[^>]*value="大件行李拦截"/,'没有引用的目录外岗位不出现在填写选项中');
});
test('任意来源与目标下拉，顺序增删和特例，名称转义且没有任务分钟输入',()=>{
  const markup=handoffPreferences(state(),true);
  for(const selector of ['data-flow-source','data-flow-target','data-flow-up','data-flow-down','data-flow-remove-target','data-flow-add-target','data-flow-remove-rule','add-flow-rule','add-flow-exception'])assert.ok(markup.includes(selector));
  assert.match(markup,/自定义&lt;&amp;/);
  assert.match(markup,/第1优先/);assert.match(markup,/第2优先/);
  assert.match(markup,/来源航班/);assert.match(markup,/目标航班/);
  assert.match(markup,/同一个人可兼任多个合法岗位/);
  assert.match(markup,/第1、第2优先是目标偏好，不表示必须全部承担/);
  assert.doesNotMatch(markup.match(/data-rule-path>[\s\S]*?<\/span>/)[0],/ → /,'目标偏好摘要不画成必须全部经过的路线');
  assert.match(markup,/<option value="same-flight" selected>本航班/);
  assert.match(markup,/<option value="later-overlap"/);
  assert.doesNotMatch(markup,/data-relative|durationMinutes|offsetMinutes|type="number"/);
});

test('旧组合只读保留，日常卡片保持原索引，不提供新建组合',()=>{
  const s=state();
  s.handoffPreferences.unshift({mode:'chain',id:'old',flightNo:'*',arrivalRole:'接机1',boardingRoles:['入境申报'],enabled:true});
  const before=structuredClone(s),markup=handoffPreferences(s,true);
  assert.match(markup,/旧版保留设置/);
  assert.match(markup,/data-flow-rule="1"/);
  assert.doesNotMatch(markup,/data-flow-rule="0"|add-flow-chain|data-flow-mode/);
  assert.deepEqual(s,before);
});

test('接续和禁止条目默认折叠，新建入口在列表之前且名称明确区分',()=>{
  const s=state();
  const markup=handoffPreferences(s,true);
  assert.match(markup,/<details[^>]*data-flow-rule="0"/);
  assert.doesNotMatch(markup,/<details[^>]*data-flow-rule="0"[^>]*\bopen\b/);
  assert.ok(markup.indexOf('id="add-flow-rule"')<markup.indexOf('data-flow-rule="0"'));
  assert.match(markup,/新增接续兼职/);
  assert.match(markup,/data-rule-path/);
  const forbidden=forbiddenHandoffsHtml(s,{pairs:[],baggage:[],forbiddenHandoffs:[{flightNo:'*',sourceRole:'接机1',targets:[{role:'入境申报',flightScope:'same-flight'}],enabled:true}]});
  assert.match(forbidden,/<details[^>]*data-forbidden-rule="0"/);
  assert.ok(forbidden.indexOf('data-forbidden-add-rule')<forbidden.indexOf('data-forbidden-rule="0"'));
});
test('通用设置单独突出，已有航班特例默认收起，不按航班模板复制通用规则',()=>{
  const s=state();s.flightLibrary=['CX937','KE0166','TW616'].map(flightNo=>({flightNo}));
  s.handoffPreferences.push({flightNo:'*',arrivalRole:'接机1',boardingRoles:['入境申报'],enabled:true});
  const before=structuredClone(s),markup=handoffPreferences(s,true);
  assert.match(markup,/data-flow-common/);
  assert.match(markup,/<details[^>]*id="flow-exceptions"[^>]*>/);
  assert.doesNotMatch(markup,/<details[^>]*id="flow-exceptions"[^>]*\bopen\b/);
  assert.match(markup,/没有特例的航班.*通用/);
  assert.equal((markup.match(/data-flow-rule=/g)||[]).length,2);
  const common=markup.slice(markup.indexOf('data-flow-common'),markup.indexOf('id="flow-exceptions"'));
  assert.ok(common.includes('data-flow-rule="1"'));assert.ok(!common.includes('data-flow-rule="0"'));
  assert.deepEqual(s,before);
});
test('连续修改和排序使用当前草稿，删除最后目标后禁用，原状态保持',()=>{
  const original=state(),saved=[];
  original.handoffPreferences.push({flightNo:'*',arrivalRole:'自定义<&',boardingRoles:['接机1'],enabled:false});
  const scope={value:'CX*',setCustomValidity(){},reportValidity(){}},source={value:'大件行李拦截',setCustomValidity(){},reportValidity(){}},enabled={};
  const addTarget={},removeRule={};
  const targets=[0,1].map(order=>{const controls={select:{},flightScope:{},up:{},down:{},remove:{}};return {dataset:{flowTargetRow:String(order)},controls,querySelector:s=>({'[data-flow-target]':controls.select,'[data-flow-flight-scope]':controls.flightScope,'[data-flow-up]':controls.up,'[data-flow-down]':controls.down,'[data-flow-remove-target]':controls.remove}[s])};});
  const container={dataset:{flowRule:'0'},querySelector:s=>({'[data-flow-source]':source,'[data-flow-scope]':scope,'[data-flow-enabled]':enabled,'[data-flow-add-target]':addTarget,'[data-flow-remove-rule]':removeRule}[s]),querySelectorAll:()=>targets};
  bindHandoffPreferences({querySelectorAll:()=>[container],querySelector:()=>null},original,async(_,p)=>{saved.push(p.preferences);return true;});
  targets[0].controls.flightScope.onchange({target:{value:'later-overlap'}});
  targets[0].controls.select.onchange({target:{value:'接机1'}});
  targets[1].controls.up.onclick();
  scope.value='cx937';scope.onchange();
  assert.deepEqual(saved.at(-1)[0].boardingRoles,['入境申报','接机1']);
  assert.equal(saved.at(-1)[0].flightNo,'CX937');
  assert.deepEqual(saved.at(-1)[0].targetFlightScopes,{'接机1':'later-overlap','入境申报':'same-flight'});
  assert.deepEqual(saved.at(-1)[1],original.handoffPreferences[1]);
  targets[0].controls.remove.onclick();targets[0].controls.remove.onclick();
  assert.deepEqual(saved.at(-1)[0].boardingRoles,[]);assert.equal(saved.at(-1)[0].enabled,false);
  assert.deepEqual(saved.at(-1)[0].targetFlightScopes,{});
  assert.deepEqual(original.handoffPreferences[0].boardingRoles,['接机1','入境申报']);
  source.value='接机1';source.onchange();addTarget.onclick();
  targets[0].controls.select.onchange({target:{value:'接机1'}});
  assert.equal(saved.at(-1)[0].targetFlightScopes['接机1'],'later-overlap','选来源同名目标自动限定跨班');
  source.onchange();
  assert.deepEqual(saved.at(-1)[0].boardingRoles,['接机1'],'改来源不删除已有合法跨班同名目标');
});
function exceptionHarness(s){
  const saved=[],handlers={},details={open:false},source={value:''},flight={value:''},feedback={textContent:''};
  const controls={'#flow-exceptions':details,'[data-flow-exception-source]':source,'[data-flow-exception-flight]':flight,'[data-flow-exception-feedback]':feedback};
  const root={querySelectorAll:()=>[],querySelector:q=>controls[q]??(q==='#add-flow-exception'?{addEventListener:(_,fn)=>handlers.add=fn}:null)};
  bindHandoffPreferences(root,s,async(_,p)=>{saved.push(p.preferences);return true;});
  return {saved,details,source,flight,feedback,add:()=>handlers.add()};
}
test('底部集中添加须明确岗位及航班，复制所选来源，重复/无效不覆盖',()=>{
  const original=state();
  original.handoffPreferences=[{flightNo:'*',arrivalRole:'大件行李拦截',boardingRoles:['接机1'],enabled:true},
    {flightNo:'*',arrivalRole:'接机1',boardingRoles:['入境申报'],enabled:true,ordered:false,targetFlightScopes:{'入境申报':'earlier-overlap'}}];
  const h=exceptionHarness(original);
  h.flight.value='CX937';h.add();assert.equal(h.saved.length,0);assert.match(h.feedback.textContent,/先选择/);
  h.source.value='接机1';
  for(const flight of ['', '通用', '*', 'CX*937']){h.flight.value=flight;h.add();assert.equal(h.saved.length,0);}
  h.flight.value=' cx937 ';h.add();assert.equal(h.details.open,true);
  assert.deepEqual(h.saved[0],[...original.handoffPreferences,{...original.handoffPreferences[1],flightNo:'CX937'}]);
  h.add();assert.equal(h.saved.length,1);assert.match(h.feedback.textContent,/已有/);
  assert.equal(original.handoffPreferences.length,2);
});

test('旧组合不被复制为新特例，新增特例使用普通来源',()=>{
  const s=state();
  s.handoffPreferences=[{id:'group-a',mode:'chain',flightNo:'*',arrivalRole:'接机1',boardingRoles:['入境申报'],enabled:true,targetFlightScopes:{'入境申报':'same-flight'}},
    {id:'group-b',mode:'chain',flightNo:'*',arrivalRole:'接机1',boardingRoles:['自定义<&'],enabled:true,targetFlightScopes:{'自定义<&':'same-flight'}}];
  const h=exceptionHarness(s);h.source.value='大件行李拦截';h.flight.value='CX937';h.add();
  assert.deepEqual(h.saved.at(-1)[2],{flightNo:'CX937',arrivalRole:'大件行李拦截',boardingRoles:[],enabled:false,ordered:true});
  assert.deepEqual(h.saved.at(-1)[1],s.handoffPreferences[1]);
});
test('旧停用设置只读保留，恢复按钮只开启所属来源，新增目标默认开启',()=>{
  const s=state();s.handoffPreferences[0].enabled=false;
  const before=structuredClone(s),markup=handoffPreferences(s,true);
  assert.match(markup,/data-flow-resume/);assert.doesNotMatch(markup,/data-flow-enabled/);assert.deepEqual(s,before);
  const saved=[],resume={},add={};
  const container={dataset:{flowRule:'0'},querySelector:q=>q==='[data-flow-resume]'?resume:q==='[data-flow-add-target]'?add:{},querySelectorAll:()=>[]};
  bindHandoffPreferences({querySelectorAll:()=>[container],querySelector:()=>null},s,async(_,p)=>{saved.push(p.preferences);return true;});
  resume.onclick();assert.equal(saved.at(-1)[0].enabled,true);assert.equal(s.handoffPreferences[0].enabled,false);
  add.onclick();assert.equal(saved.at(-1)[0].enabled,true);
});
