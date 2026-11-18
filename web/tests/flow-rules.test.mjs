import assert from 'node:assert/strict';
import {test} from 'node:test';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
const bundle=await build({stdin:{contents:'export {handoffPreferences,bindHandoffPreferences} from "./handoff-preferences"; export {workRulesHtml} from "./work-rules"; export {forbiddenHandoffsHtml} from "./forbidden-handoffs";',resolveDir:fileURLToPath(new URL('../src/',import.meta.url))},bundle:true,write:false,format:'esm',target:'es2022'});
const {handoffPreferences,bindHandoffPreferences,workRulesHtml,forbiddenHandoffsHtml}=await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);
const state=()=>({configuration:{roleConfigs:['大件行李拦截','接机1','入境申报','自定义<&'].map(roleName=>({roleName}))},flightLibrary:[],handoffPreferences:[{flightNo:'CX*',arrivalRole:'大件行李拦截',boardingRoles:['接机1','入境申报'],enabled:true,ordered:true}]});

test('流动填写只消费可选目录，专岗旧引用明确只读，不静默改值或污染其他选择',()=>{
  const s=state();
  s.flowRuleRoles=['接机1','入境申报'];
  s.workRules={pairs:[],baggage:[],separationGroups:[{id:'g',name:'旧分人',flightNo:'*',roles:['大件行李拦截','接机1'],enabled:true}],forbiddenHandoffs:[{flightNo:'*',sourceRole:'大件行李拦截',targets:[{role:'入境申报',flightScope:'same-flight'}],enabled:true}]};
  const before=structuredClone(s);
  for(const markup of [handoffPreferences(s,true),workRulesHtml(s,null,null),forbiddenHandoffsHtml(s,s.workRules)]){
    const options=markup.match(/<option[^>]*value="大件行李拦截"[^>]*>/g)??[];
    assert.ok(options.length>0,'已有引用仍有占位项，防浏览器自动换岗');
    assert.ok(options.every(option=>option.includes('selected')&&option.includes('disabled')),'专岗只能作为已选禁用原值，不可新选');
    assert.match(markup,/专岗/);
  }
  assert.deepEqual(s,before,'渲染不得清除旧规则');
  const blank={...s,handoffPreferences:[{flightNo:'*',arrivalRole:'接机1',boardingRoles:['入境申报'],enabled:true}]};
  assert.doesNotMatch(handoffPreferences(blank,true),/<option[^>]*value="大件行李拦截"/,'没有引用的专岗完全不出现在填写选项中');
});
test('任意来源与目标下拉，顺序增删和特例，名称转义且没有任务分钟输入',()=>{
  const markup=handoffPreferences(state(),true);
  for(const selector of ['data-flow-source','data-flow-target','data-flow-up','data-flow-down','data-flow-remove-target','data-flow-add-target','data-flow-remove-rule','add-flow-rule','add-flow-exception'])assert.ok(markup.includes(selector));
  assert.match(markup,/自定义&lt;&amp;/);
  assert.match(markup,/第1优先/);assert.match(markup,/第2优先/);
  assert.match(markup,/来源航班/);assert.match(markup,/目标航班/);
  assert.match(markup,/<option value="same-flight" selected>本航班/);
  assert.match(markup,/<option value="later-overlap"/);
  assert.doesNotMatch(markup,/data-relative|durationMinutes|offsetMinutes|type="number"/);
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
  const common=markup.slice(markup.indexOf('data-flow-common'),markup.indexOf('<details'));
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
});
test('新增特例需明确航班，取消不修改；按来源复制通用，重复不覆盖旧特例',()=>{
  const original=state(),saved=[],handlers={},details={open:false},alerts=[];
  original.handoffPreferences=[{flightNo:'*',arrivalRole:'接机1',boardingRoles:['入境申报'],enabled:true,ordered:false,targetFlightScopes:{'入境申报':'later-overlap'}}];
  const root={querySelectorAll:()=>[],querySelector:s=>s==='#flow-exceptions'?details:({addEventListener:(event,fn)=>handlers[s]=fn})};
  const previousPrompt=globalThis.prompt,previousAlert=globalThis.alert;let reply=null;
  globalThis.prompt=()=>reply;globalThis.alert=message=>alerts.push(message);
  try{
    bindHandoffPreferences(root,original,async(_,p)=>{saved.push(p.preferences);return true;});
    handlers['#add-flow-exception']();assert.equal(saved.length,0,'取消不创建默认CX*');
    reply='';handlers['#add-flow-exception']();assert.equal(saved.length,0);
    reply='通用';handlers['#add-flow-exception']();assert.equal(saved.length,0);
    reply='cx937';handlers['#add-flow-exception']();
    assert.equal(details.open,true);assert.equal(saved.length,1);
    assert.deepEqual(saved[0],[original.handoffPreferences[0],{...original.handoffPreferences[0],flightNo:'CX937'}]);
    handlers['#add-flow-exception']();assert.equal(saved.length,1);assert.equal(alerts.length,2);
    assert.equal(original.handoffPreferences.length,1);
  }finally{globalThis.prompt=previousPrompt;globalThis.alert=previousAlert;}
});
