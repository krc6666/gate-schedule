import assert from 'node:assert/strict';
import { test } from 'node:test';
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { readFile, mkdtemp, mkdir, rm } from 'node:fs/promises';
import { createServer } from 'node:http';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import ts from 'typescript';

const repo = fileURLToPath(new URL('../../', import.meta.url));
const javascript = source => ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const url = text => `data:text/javascript;base64,${Buffer.from(text).toString('base64')}`;
const model = url(javascript(await readFile(new URL('../src/workbench-model.ts', import.meta.url), 'utf8')));
const source = javascript(await readFile(new URL('../src/schedule-palette.ts', import.meta.url), 'utf8')).replace('"./workbench-model"', JSON.stringify(model));
const { paletteHtml } = await import(url(source));
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const fixture = () => {
  const tasks = [
    { id:'arrive/1', flightId:'f1', roleId:'接机1', timingLabel:'接机环节', requiredCount:2 },
    { id:'board2', flightId:'f2', roleId:'登机主验', timingLabel:'登机环节', requiredCount:1 },
    { id:'rotation3', flightId:'f1', roleId:'大件行李拦截', timingLabel:'11:00—12:00', requiredCount:1 },
    { id:'none', flightId:'f1', roleId:'特服', requiredCount:0 },
  ];
  const members = ['甲','乙','丙'].map((name,index)=>({id:`m${index}`,name}));
  const schedule = {tasks,members,flights:[{id:'f1',flightNo:'CX937'},{id:'f2',flightNo:'KE0166'}]};
  return {state:{revision:41},plan:{id:'plan/1',draft:[{taskId:'arrive/1',memberId:'m0'},{taskId:'rotation3',memberId:'m1'}],schedule},schedule};
};

test('人员名单不声称全局空闲，目标只列真正缺人的任务并保留轮换身份',()=>{
  const {plan,schedule}=fixture();
  const markup=paletteHtml(plan,schedule);
  assert.match(markup,/当天人员名单；选择目标岗位后检查谁可安排/);
  assert.doesNotMatch(markup,/空闲/);
  assert.match(markup,/value="arrive\/1"/);
  assert.match(markup,/value="board2"/);
  assert.doesNotMatch(markup,/value="rotation3"|value="none"/);
  assert.equal((markup.match(/data-palette-member=/g)??[]).length,3);
  assert.equal((markup.match(/draggable="true"/g)??[]).length,3);
  plan.draft=plan.draft.filter(a=>a.taskId!=='rotation3');
  const rotated=paletteHtml(plan,schedule,'rotation3');
  assert.match(rotated,/value="rotation3" selected/);
  assert.match(rotated,/11:00—12:00/);
  assert.doesNotMatch(rotated,/data-palette-member=/,'选中目标后未检查前不能显示旧的可安排人选');
});

test('没有缺口时仍保留全员手调入口，已失效目标退回名单',()=>{
  const {plan,schedule}=fixture();
  plan.draft=[...plan.draft,{taskId:'arrive/1',memberId:'m2'},{taskId:'board2',memberId:'m0'}];
  const markup=paletteHtml(plan,schedule,'arrive/1');
  assert.match(markup,/没有待补岗位/);
  assert.equal((markup.match(/<option/g)??[]).length,1);
  assert.equal((markup.match(/data-palette-member=/g)??[]).length,3);
  assert.equal(paletteHtml(plan,null),'');
});

test('目标岗位名单真实请求：状态分组、禁用冲突、旧回复隔离与失败不显示可安排', {timeout:60000}, async t=>{
  const temporaryRoot=join(repo,'.tmp');await mkdir(temporaryRoot,{recursive:true});
  const directory=await mkdtemp(join(temporaryRoot,'palette-browser-'));
  const chrome=[process.env.GATE_TEST_BROWSER,'C:/Program Files/Google/Chrome/Application/chrome.exe','C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'].find(path=>path&&existsSync(path));
  assert.ok(chrome,'需要Chrome/Edge验收人员选择');
  const bundle=await build({stdin:{contents:'export { paletteHtml, bindPalette } from "./schedule-palette"; export { ScheduleDrag } from "./schedule-drag";',resolveDir:join(repo,'web/src')},bundle:true,write:false,format:'iife',globalName:'paletteModule',target:'es2022'});
  const workbenchCss=await readFile(join(repo,'web/src/workbench.css'),'utf8');
  const candidates=[
    {id:'m0',name:'甲',available:false,status:'assigned',label:'已在本岗',reasons:[],issues:[],revision:41},
    {id:'m1',name:'乙',available:false,status:'blocked',label:'不可安排',reasons:['与CX937接机任务冲突'],issues:[],revision:41},
    {id:'m2',name:'丙',available:true,status:'continuation',label:'可接续',reasons:[],issues:[],revision:41},
    {id:'m3',name:'丁',available:true,status:'concurrent',label:'可兼任',reasons:[],issues:[],revision:41},
    {id:'m4',name:'戊',available:true,status:'available',label:'可安排',reasons:[],issues:[],revision:41},
  ];
  const requests=[],held=[];
  let mode='normal';
  const server=createServer((request,response)=>{
    if(request.url==='/bundle.js'){response.setHeader('Content-Type','text/javascript');response.end(bundle.outputFiles[0].text);return;}
    if(request.url==='/workbench.css'){response.setHeader('Content-Type','text/css');response.end(workbenchCss);return;}
    if(request.url?.startsWith('/api/workspace/candidates/')){
      requests.push(request.url);
      const reply=()=>{response.setHeader('Content-Type','application/json');response.end(JSON.stringify(mode==='revision'?candidates.map(c=>({...c,revision:42})):candidates));};
      if(mode==='held'){held.push(reply);return;}
      if(mode==='error'){response.statusCode=503;response.end('{}');return;}
      reply();return;
    }
    response.setHeader('Content-Type','text/html');response.end('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="/workbench.css"><style>body{min-height:1600px}.schedule-sheet{margin-top:32px}.drop-fixture{width:300px;height:120px;border:1px solid #aaa}</style><main id="root" class="wb-main"></main><script src="/bundle.js"></script>');
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const base=`http://127.0.0.1:${server.address().port}`;
  let browser,ws,sessionId,id=0;
  const pending=new Map();
  const until=async(fn,label)=>{const end=Date.now()+12000;while(Date.now()<end){const value=await fn();if(value)return value;await delay(25);}throw Error(`等待超时：${label}`);};
  const call=(method,params={})=>new Promise((resolve,reject)=>{const request=++id,timer=setTimeout(()=>{pending.delete(request);reject(Error(`Chrome超时${method}`));},12000);pending.set(request,{resolve:value=>{clearTimeout(timer);resolve(value);},reject});ws.send(JSON.stringify({id:request,method,params,...(sessionId?{sessionId}:{})}));});
  const evaluate=async expression=>{const result=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(result.exceptionDetails)throw Error(JSON.stringify(result.exceptionDetails));return result.result.value;};
  t.after(async()=>{
    for(const release of held.splice(0))release();
    ws?.close();
    if(browser&&browser.exitCode===null){const done=new Promise(resolve=>browser.once('exit',resolve));browser.kill();await Promise.race([done,delay(3000)]);}
    server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
    assert.equal(dirname(directory),temporaryRoot);await rm(directory,{recursive:true,force:true,maxRetries:8,retryDelay:250});
  });
  const profile=join(directory,'chrome');
  browser=spawn(chrome,['--headless=new','--remote-debugging-port=0',`--user-data-dir=${profile}`,'--no-first-run','--disable-background-networking','about:blank'],{windowsHide:true,stdio:'ignore',env:{...process.env,TEMP:temporaryRoot,TMP:temporaryRoot}});
  const portFile=await until(async()=>{try{return await readFile(join(profile,'DevToolsActivePort'),'utf8');}catch{return false;}},'Chrome启动');
  const [port,path]=portFile.trim().split(/\r?\n/);ws=new WebSocket(`ws://127.0.0.1:${port}${path}`);await new Promise(resolve=>ws.addEventListener('open',resolve,{once:true}));
  ws.addEventListener('message',event=>{const message=JSON.parse(event.data),request=pending.get(message.id);if(request){pending.delete(message.id);message.error?request.reject(Error(JSON.stringify(message.error))):request.resolve(message.result);}});
  const target=await call('Target.createTarget',{url:'about:blank'});sessionId=(await call('Target.attachToTarget',{targetId:target.targetId,flatten:true})).sessionId;
  await call('Page.enable');await call('Page.navigate',{url:base});await until(()=>evaluate('!!window.paletteModule'),'模块载入');
  const targetMarkup=`<section class="schedule-sheet"><table><tbody><tr><td class="drop-fixture" data-drop-ids='["drop-task"]' data-cell-id="drop-cell">待安排</td></tr></tbody></table></section>`;
  await evaluate(`window.testData=${JSON.stringify(fixture())};window.changed=[];window.ready=0;window.drops=[];window.starts=0;window.drag=new paletteModule.ScheduleDrag(document.getElementById('root'),{source:el=>({memberId:el.dataset.dragMember,taskIds:JSON.parse(el.dataset.taskIds)}),started:()=>starts++,drop:(source,target)=>drops.push({source,target})});window.mount=(target='')=>{window.dispose?.();drag.dispose();const root=document.getElementById('root');root.innerHTML=paletteModule.paletteHtml(testData.plan,testData.schedule,target)+${JSON.stringify(targetMarkup)};drag.bind();window.dispose=paletteModule.bindPalette(root,testData.state,testData.plan,{targetChanged:id=>{changed.push(id);mount(id);},peopleChanged:scope=>{drag.bind(scope);ready++;}});};mount();`);
  assert.equal(await evaluate("document.querySelectorAll('[data-palette-member]').length"),3);
  await evaluate("(()=>{const select=document.querySelector('[data-palette-target]');select.value='arrive/1';select.dispatchEvent(new Event('change'));})()");
  await until(()=>evaluate('window.ready===1'),'可安排回复');
  assert.equal(requests[0],'/api/workspace/candidates/plan%2F1/arrive%2F1?slot=1');
  assert.equal(await evaluate("document.querySelectorAll('.palette-available').length"),3);
  assert.equal(await evaluate("document.querySelectorAll('[data-palette-member][disabled]').length"),2);
  assert.equal(await evaluate("document.querySelector('[data-palette-member=m0]').draggable"),false);
  assert.match(await evaluate("document.querySelector('[data-palette-member=m1]').textContent"),/冲突/);
  assert.match(await evaluate("document.querySelector('[data-palette-member=m2]').textContent"),/可接续/);
  assert.match(await evaluate("document.querySelector('[data-palette-member=m3]').textContent"),/可兼任/);
  assert.doesNotMatch(await evaluate("document.querySelector('[data-schedule-palette]').textContent"),/空闲/);
  // Use real touch events and product CSS: synthetic pointer events miss native pan cancellation.
  await call('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:1});
  const frame=()=>evaluate('new Promise(resolve=>requestAnimationFrame(()=>resolve(true)))');
  const touchDrop=async memberId=>{
    await evaluate('window.scrollTo(0,0)');await frame();
    const points=await evaluate(`(()=>{const center=el=>{const box=el.getBoundingClientRect();return{x:box.x+box.width/2,y:box.y+box.height/2};};return{from:center(document.querySelector('[data-palette-member=${memberId}]')),to:center(document.querySelector('.drop-fixture'))};})()`);
    await call('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{...points.from,id:1}]});await frame();
    await call('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:points.from.x+15,y:points.from.y,id:1}]});await frame();
    await call('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{...points.to,id:1}]});await frame();
    await call('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await frame();
  };
  assert.equal(await evaluate("getComputedStyle(document.querySelector('[data-palette-member=m2]')).touchAction"),'none','可拖候选必须阻止浏览器把拖动接管为滚动');
  await touchDrop('m2');
  assert.deepEqual(await evaluate('window.drops'),[{source:{memberId:'m2',taskIds:[]},target:{ids:['drop-task'],cellId:'drop-cell'}}],'异步生成的人选应通过动态scope绑定触屏落岗');
  const starts=await evaluate('window.starts');
  await touchDrop('m1');
  assert.equal(await evaluate('window.starts'),starts,'冲突禁用人员不能开始触屏拖动');
  assert.equal(await evaluate('window.drops.length'),1,'冲突禁用人员不能触屏落岗');
  mode='held';const requestCount=requests.length;await evaluate("mount('arrive/1')");await until(()=>requests.length>requestCount,'挂起候选请求');
  assert.equal(await evaluate("document.querySelectorAll('.palette-available').length"),0,'等待期间清除旧绿人选');
  mode='normal';await evaluate("(()=>{const select=document.querySelector('[data-palette-target]');select.value='board2';select.dispatchEvent(new Event('change'));})()");
  await until(()=>evaluate('window.ready===2'),'新目标回复');
  assert.equal(await evaluate("getComputedStyle(document.querySelector('[data-palette-member=m4]')).touchAction"),'none');
  await touchDrop('m4');
  assert.equal(await evaluate('window.drops.at(-1).source.memberId'),'m4','切目标重绘后新生成的人选仍能触屏落岗');
  assert.equal(await evaluate('window.drops.length'),2,'重绘后动态绑定不应遗漏或重复触发');
  await call('Emulation.setTouchEmulationEnabled',{enabled:false});
  const latest=await evaluate("document.querySelector('[data-schedule-palette]').innerHTML");
  held.shift()();await delay(100);assert.equal(await evaluate("document.querySelector('[data-schedule-palette]').innerHTML"),latest,'旧目标回复不能覆盖新目标');
  mode='revision';await evaluate("mount('arrive/1')");await until(()=>evaluate("document.querySelector('[data-palette-notice]').getAttribute('role')==='alert'"),'版本不符拒用');
  assert.match(await evaluate("document.querySelector('[data-palette-notice]').textContent"),/班表已改变/);
  assert.equal(await evaluate("document.querySelectorAll('[data-palette-member]').length"),0);
  mode='error';await evaluate("mount('arrive/1')");await until(()=>evaluate("document.querySelector('[data-palette-notice]').getAttribute('role')==='alert'"),'请求失败');
  assert.match(await evaluate("document.querySelector('[data-palette-notice]').textContent"),/检查失败/);
  assert.equal(await evaluate("document.querySelectorAll('.palette-available').length"),0);
  mode='held';const disconnectedCount=requests.length;await evaluate("mount('arrive/1')");await until(()=>requests.length>disconnectedCount,'待移除请求');
  await evaluate("document.getElementById('root').innerHTML='<p id=changed>已切换日期</p>'");mode='normal';held.shift()();await delay(100);
  assert.equal(await evaluate("document.getElementById('root').textContent"),'已切换日期','离开的页面不接收旧回复');
});
