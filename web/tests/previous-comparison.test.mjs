import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdir, mkdtemp, readFile, writeFile, rm } from 'node:fs/promises';
import { createServer } from 'node:http';
import { join, dirname, resolve } from 'node:path';

const bundle=await build({entryPoints:[fileURLToPath(new URL('../src/schedule-extras.ts',import.meta.url))],bundle:true,write:false,format:'esm',target:'es2022'});
const {scheduleExtras}=await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);

function fixture() {
  const flights=[{id:'f',flightNo:'CX937',windowStart:'10:10',windowEnd:'11:30'},{id:'g',flightNo:'TW616',windowStart:'次日00:10',windowEnd:'次日01:10'}];
  const segment=(task_id,members,start=610)=>({task_id,members,start,end:start+30,timing_mode:'phase'});
  const formal={flights,tasks:[{id:'arrival',flightId:'f',roleId:'接机',requiredCount:2},{id:'shared1',flightId:null,roleId:'大件行李拦截',requiredCount:1},{id:'shared2',flightId:null,roleId:'大件行李拦截',requiredCount:1},{id:'late',flightId:'g',roleId:'登机',requiredCount:1}],members:[{id:'a',name:'甲'},{id:'b',name:'乙'}],assignments:[],memberFlights:[],memberPaths:{},matrix:{接机:{CX937:[segment('arrival',['甲'])]},大件行李拦截:{CX937:[segment('shared1',['甲']),segment('shared2',['乙'],640)],TW616:[segment('shared1',['甲']),segment('shared2',['乙'],640)]},登机:{TW616:[segment('late',['乙'])]}}};
  const cells=flights.flatMap(f=>['接机','大件行李拦截','登机'].map(roleName=>({id:`${f.id}-${roleName}`,flightId:f.id,flightNo:f.flightNo,roleName,rawValue:'',requiredCountOverride:0})));
  const previous={id:'previous',day:{serviceDate:'2026-11-05',roleNames:['草稿新岗位'],cells:[]},formalDay:{serviceDate:'2026-11-05',roleNames:['接机','大件行李拦截','登机'],flights,cells,roleConfigs:[]},formalSchedule:formal,formalIssues:[{code:'coverage_shortfall',message:'接机缺1人',taskId:'arrival'}],formalCellDrafts:{}};
  const current={id:'current',day:{serviceDate:'2026-11-07'},schedule:{...formal,members:[{id:'z',name:'当前草稿姓名'}],matrix:{}}};
  const state={plans:{previous,current,older:{...previous,id:'older',day:{serviceDate:'2026-11-03'},formalDay:{...previous.formalDay,serviceDate:'2026-11-03'},formalSchedule:{...formal,flights:[{...flights[0],flightNo:'不应选旧日期'}]}},future:{...previous,id:'future',day:{serviceDate:'2026-11-09'}},unadopted:{id:'unadopted',day:{serviceDate:'2026-11-06'},formalSchedule:null}}};
  return {state,current,previous};
}

function comparison({state,current}) {return scheduleExtras(state,current,current.schedule).split('<section id="previous-comparison"')[1];}

test('上班对照按上一正式航班完整展开岗位/人员双列，显示航班时间和轮换姓名',()=>{
  const f=fixture(),before=structuredClone(f),markup=comparison(f);
  assert.match(markup,/上一班班表/);
  assert.match(markup,/2026-11-05/);
  assert.match(markup,/<table[^>]*class="[^"]*previous-schedule-grid/);
  assert.match(markup,/colspan="2"[^>]*>[\s\S]*CX937/);
  assert.match(markup,/次日00:10–次日01:10/);
  assert.match(markup,/>岗位<\/th><th[^>]*>人员<\/th>/);
  assert.match(markup,/甲[\s\S]*乙/);
  assert.match(markup,/待安排/);
  assert.match(markup,/previous-cell-vacancy/);
  assert.doesNotMatch(markup,/>支援<\/strong>/,'分摊到实际航班的共享岗位不另造空支援列');
  assert.doesNotMatch(markup,/上一归档安排|当前安排|草稿新岗位|当前草稿姓名|不应选旧日期/);
  assert.doesNotMatch(markup,/contenteditable|draggable|data-(?:cell|pick|drop|drag)|wb-matrix|data-zoom-sheet/);
  assert.deepEqual(f,before,'看对照不修改归档和当前资料');
});

test('问题归档未形成任务也保留正式原文、待完善姓名及冲突提示，零需求留空',()=>{
  const f=fixture(),p=f.previous;
  p.formalSchedule={...p.formalSchedule,matrix:{},tasks:[],members:[]};
  p.formalDay.cells[0].rawValue='<外援>';p.formalDay.cells[0].requiredCountOverride=1;
  const cell=p.formalDay.cells.find(c=>c.flightId==='g'&&c.roleName==='登机');
  p.formalCellDrafts[cell.id]='待核名字';
  p.formalIssues=[{code:'unknown_member',taskId:cell.id,message:'未知姓名待完善'}];
  const markup=comparison(f);
  assert.match(markup,/&lt;外援&gt;/);assert.doesNotMatch(markup,/<外援>/);
  assert.match(markup,/待核名字（待完善）/);
  assert.match(markup,/previous-cell-conflict/);
  assert.match(markup,/未知姓名待完善/);
  assert.match(markup,/带问题归档/);
  assert.doesNotMatch(markup,/待安排/,'零需求无任务不误标缺人');
});

test('无上一正式归档时给出空态，不拿当前同日/未来或未采用草稿作对照',()=>{
  const f=fixture();delete f.state.plans.previous;delete f.state.plans.older;
  f.state.plans.current.formalSchedule=f.current.schedule;
  const markup=comparison(f);
  assert.match(markup,/当前日期之前还没有已采用的归档班表/);
  assert.doesNotMatch(markup,/<table/);
});

test('归档有独立支援安排时保留支援列及原人员，不冒作航班窗口',()=>{
  const f=fixture();f.previous.formalSchedule.matrix.支援岗={支援:[{task_id:'shared1',members:['甲'],start:600,end:660}]};
  const markup=comparison(f);
  assert.match(markup,/>支援<\/strong>/);assert.match(markup,/>支援岗<\/th>/);
  assert.match(markup,/>甲<\/td>/);
});

test('上班班表真实Chrome：宽窄屏按航班双列、只读、横向滚动且不串入主表', {timeout:60000}, async t=>{
  const repo=fileURLToPath(new URL('../../',import.meta.url)),temporary=join(repo,'.tmp');
  await mkdir(temporary,{recursive:true});const directory=await mkdtemp(join(temporary,'previous-board-ui-'));
  const chrome=[process.env.GATE_TEST_BROWSER,'C:/Program Files/Google/Chrome/Application/chrome.exe','C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'].find(p=>p&&existsSync(p));assert.ok(chrome);
  const css=await readFile(join(repo,'web/src/workbench.css'),'utf8'),f=fixture();
  const markup=scheduleExtras(f.state,f.current,f.current.schedule);
  const server=createServer((req,res)=>{res.setHeader('Content-Type','text/html; charset=utf-8');res.end(`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>${css}body{margin:0}.test-frame{max-width:1200px;margin:auto;padding:20px}</style><main class="wb-main test-frame"><h2>航班排班</h2>${markup}</main>`);});
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  let browser,ws,sessionId,id=0;const pending=new Map(),errors=[],delay=ms=>new Promise(r=>setTimeout(r,ms));
  const until=async(fn,label)=>{const end=Date.now()+12000;while(Date.now()<end){const value=await fn();if(value)return value;await delay(25);}throw Error(label);};
  const call=(method,params={})=>new Promise((resolveCall,reject)=>{const key=++id,timer=setTimeout(()=>{pending.delete(key);reject(Error(method));},12000);pending.set(key,{resolve:v=>{clearTimeout(timer);resolveCall(v);},reject:e=>{clearTimeout(timer);reject(e);}});ws.send(JSON.stringify({id:key,method,params,...(sessionId?{sessionId}:{})}));});
  const evaluate=async expression=>{const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});assert.equal(r.exceptionDetails,undefined,JSON.stringify(r.exceptionDetails));return r.result.value;};
  t.after(async()=>{ws?.close();if(browser&&browser.exitCode===null){const done=new Promise(r=>browser.once('exit',r));browser.kill();await Promise.race([done,delay(3000)]);}await new Promise(r=>server.close(r));assert.equal(dirname(resolve(directory)),resolve(temporary));await rm(directory,{recursive:true,force:true,maxRetries:8,retryDelay:250});});
  browser=spawn(chrome,['--headless=new','--remote-debugging-port=0',`--user-data-dir=${join(directory,'chrome')}`,'--no-first-run','--no-default-browser-check','about:blank'],{windowsHide:true,stdio:'ignore',env:{...process.env,TEMP:temporary,TMP:temporary}});
  const ports=await until(async()=>{try{return await readFile(join(directory,'chrome/DevToolsActivePort'),'utf8');}catch{return false;}},'Chrome启动');const [port,path]=ports.trim().split(/\r?\n/);ws=new WebSocket(`ws://127.0.0.1:${port}${path}`);await new Promise(r=>ws.addEventListener('open',r,{once:true}));
  ws.addEventListener('message',event=>{const m=JSON.parse(event.data),p=pending.get(m.id);if(p){pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result);}if(m.method==='Runtime.exceptionThrown')errors.push(m.params);});
  const target=await call('Target.createTarget',{url:'about:blank'});sessionId=(await call('Target.attachToTarget',{targetId:target.targetId,flatten:true})).sessionId;
  await call('Page.enable');await call('Runtime.enable');await call('Page.navigate',{url:`http://127.0.0.1:${server.address().port}`});
  await until(()=>evaluate('!!document.querySelector(".previous-schedule-grid")'),'上一班班表加载');
  for(const width of [1440,390]) {
    await call('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:false});
    assert.deepEqual(await evaluate('[...document.querySelectorAll(".previous-schedule-grid thead tr:first-child th")].map(el=>el.colSpan)'),[2,2]);
    assert.equal(await evaluate('document.querySelectorAll(".previous-schedule-grid tbody tr:first-child>*").length'),4);
    assert.ok(await evaluate('document.documentElement.scrollWidth<=window.innerWidth+1'),'页面不被班表撑宽');
    assert.equal(await evaluate('document.querySelectorAll("#previous-comparison button,#previous-comparison [contenteditable],#previous-comparison [draggable],#previous-comparison .wb-matrix").length'),0);
    if(width===390) {
      assert.ok(await evaluate('document.querySelector(".previous-board").scrollWidth>document.querySelector(".previous-board").clientWidth'),'窄屏表内滚动');
      await evaluate('document.querySelector(".previous-board").scrollLeft=10000');
      assert.ok(await evaluate('document.querySelector(".previous-board").scrollLeft>0'));
      await evaluate('document.querySelector(".previous-board").scrollLeft=0');
    }
    await evaluate('document.querySelector("#previous-comparison").scrollIntoView({block:"center"})');
    const shot=await call('Page.captureScreenshot',{format:'png'});await writeFile(join(temporary,`previous-comparison-${width}.png`),Buffer.from(shot.data,'base64'));
  }
  assert.deepEqual(errors,[]);
});
