import assert from "node:assert/strict";
import {test} from "node:test";
import {createServer} from "node:http";
import {spawn, execFileSync} from "node:child_process";
import {readFile, mkdir, mkdtemp, rm, writeFile, readdir} from "node:fs/promises";
import {join, resolve, extname} from "node:path";
import {fileURLToPath} from "node:url";
import {createHash} from "node:crypto";

const repo=fileURLToPath(new URL("../../",import.meta.url));
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function until(fn,label,ms=90000){const end=Date.now()+ms;while(Date.now()<end){const x=await fn();if(x)return x;await delay(100)}throw Error("等待超时："+label)}

test("静态子路径网页：无后台启动、自动排班、文件迁移与本机保存",{timeout:300000},async t=>{
  const requests=[],errors=[];
  const mime={".html":"text/html",".js":"application/javascript",".wasm":"application/wasm",".css":"text/css",".json":"application/json",".zip":"application/zip"};
  const server=createServer(async(req,res)=>{
    requests.push(req.url);
    try{
      const url=new URL(req.url,"http://localhost");
      assert.ok(url.pathname.startsWith("/gate-schedule/"),"静态资源必须留在项目子路径："+url.pathname);
      const name=decodeURIComponent(url.pathname.slice("/gate-schedule/".length))||"index.html";
      const path=resolve(repo,"build/browser",name);
      assert.ok(path.startsWith(resolve(repo,"build/browser")+(/[\\/]$/.test(resolve(repo,"build/browser"))?"":process.platform==="win32"?"\\":"/")));
      res.setHeader("Content-Type",mime[extname(path)]??"application/octet-stream");
      res.end(await readFile(path));
    }catch(error){res.statusCode=404;res.end(String(error))}
  });
  await new Promise(r=>server.listen(0,"127.0.0.1",r));
  const base=process.env.GATE_TEST_URL??"http://127.0.0.1:"+server.address().port+"/gate-schedule/";
  await mkdir(join(repo,".tmp"),{recursive:true});
  const directory=await mkdtemp(join(repo,".tmp/browser-local-test-"));
  let browser,ws;let next=0;const pending=new Map();
  t.after(async()=>{await writeFile(join(repo,".tmp/browser-local-debug.json"),JSON.stringify({requests,errors},null,2));ws?.close();browser?.kill();await new Promise(r=>server.close(r));await rm(directory,{recursive:true,force:true,maxRetries:10,retryDelay:300})});
  browser=spawn(process.env.GATE_TEST_BROWSER??"C:/Program Files/Google/Chrome/Application/chrome.exe",
    ["--headless=new","--no-first-run","--no-default-browser-check","--disable-background-networking","--remote-debugging-port=0","--user-data-dir="+join(directory,"chrome"),base],
    {windowsHide:true,stdio:"ignore"});
  const port=await until(async()=>{try{return(await readFile(join(directory,"chrome/DevToolsActivePort"),"utf8")).trim().split(/\r?\n/)[0]}catch{return false}},"浏览器端口",15000);
  const target=await until(async()=>{const list=await(await fetch("http://127.0.0.1:"+port+"/json/list")).json();return list.find(x=>x.type==="page"&&x.url.includes("gate-schedule"))},"页面");
  ws=new WebSocket(target.webSocketDebuggerUrl);
  await new Promise(r=>ws.addEventListener("open",r,{once:true}));
  ws.addEventListener("message",event=>{const m=JSON.parse(event.data);if(m.method==="Runtime.exceptionThrown")errors.push(m.params);if(process.env.GATE_TEST_URL&&m.method==="Network.requestWillBeSent")requests.push(m.params.request.url);const p=pending.get(m.id);if(p){pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result)}});
  const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++next;const timer=setTimeout(()=>{pending.delete(id);reject(Error("浏览器调用超时："+method))},120000);pending.set(id,{resolve:value=>{clearTimeout(timer);resolve(value)},reject:error=>{clearTimeout(timer);reject(error)}});ws.send(JSON.stringify({id,method,params}))});
  const evaluate=async expression=>{const r=await call("Runtime.evaluate",{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value};
  await call("Runtime.enable");
  await call("Page.enable");
  await call("Network.enable");
  await call("Browser.setDownloadBehavior",{behavior:"allow",downloadPath:directory});
  const click=selector=>evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);
  const value=(selector,value)=>evaluate(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});el.value=${JSON.stringify(value)};el.dispatchEvent(new Event('change',{bubbles:true}))})()`);
  const ready=async()=>{
    try { await until(()=>evaluate("document.documentElement?.dataset.gateLocal === 'true' && !!document.querySelector('[data-view=\"config\"]')"),"浏览器本机工作台",120000); }
    catch(error) { throw Error(String(error)+"; "+await evaluate("document.body.innerText")); }
  };
  const reload=async()=>{
    await evaluate("window.__reloadPending=true");
    await call("Page.reload");
    await until(()=>evaluate("!window.__reloadPending && document.documentElement?.dataset.gateLocal === 'true' && !!document.querySelector('[data-view=\"config\"]')"),"重新打开工作台",120000);
  };
  await ready();
  assert.equal(await evaluate("crossOriginIsolated"),true);
  assert.deepEqual(await evaluate("[...document.querySelectorAll('.app-nav [data-view]')].map(x=>x.textContent.trim())"),["配置","航班","排班","规则","统计","历史"]);
  const state=group=>evaluate("fetch('/api/groups/"+group+"/workspace').then(r=>r.json())");
  assert.equal((await state("A")).people.length,0);
  const command=async(group,action,payload={},planId)=>{
    const current=await state(group);
    const result=await evaluate("fetch('/api/groups/"+group+"/workspace/command',{method:'POST',headers:{'Content-Type':'application/json','X-Gate-Rule-Schema':'flight-role-v1'},body:JSON.stringify("+JSON.stringify({expectedRevision:current.revision,action,payload,planId})+")}).then(async r=>({status:r.status,value:await r.json()}))");
    assert.equal(result.status,200,JSON.stringify(result.value));
    return result.value;
  };
  await command("A","save-person",{name:"网页甲"});
  assert.equal((await state("B")).people.length,0);
  const python=process.env.GATE_TEST_PYTHON??join(repo,".venv-local/Scripts/python.exe");
  const fixture=JSON.parse(execFileSync(python,["-c",`import json
from tests.test_balance_configuration import configuration
d=configuration()
d['balanceSettings']['priorityFlightRoles']=[{'flightNo':'A100','roleName':'登机主验'}]
d['balanceSettings']['priorityAdjustments']=[]
print(json.dumps(d))`],{cwd:repo,encoding:"utf8"}));
  let imported=await command("A","import-complete-configuration",fixture);
  for(const role of imported.configuration.roleConfigs)if(role.roleName!=="登机主验")await command("A","save-role",{...role,requiredCount:0});
  imported=await state("A");
  const planId=imported.selectedPlanId;
  const run=await evaluate(`fetch('/api/groups/A/workspace/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(${JSON.stringify({expectedRevision:imported.revision,action:"solve",planId})})}).then(r=>r.json())`);
  const done=await until(async()=>{const value=await evaluate(`fetch('/api/groups/A/workspace/runs/${run.id}').then(r=>r.json())`);return value.status!=="running"?value:false},"浏览器自动排班");
  assert.equal(done.status,"completed",JSON.stringify(done));
  imported=await state("A");
  assert.equal(imported.plans[planId].valid,true,JSON.stringify(imported.plans[planId].issues));
  assert.equal(imported.plans[planId].draft.length,2);
  await command("A","adopt",{},planId);
  const correction={month:"2026-08",memberId:"stable-a",flightNo:"A100",roleName:"登机主验",delta:1};
  await command("A","adjust-priority-count",correction);
  await command("B","save-person",{name:"甲",eligibleRoleNames:["登机主验"]});
  assert.notEqual((await state("B")).people[0].id,"stable-a","两组同名人员不得合并");
  const people=(await state("A")).people.map(p=>p.id);
  await command("A","save-duty-settings",{enabled:true,anchorDate:"2026-08-20",dutyMemberIds:people,standbyMemberIds:people,overrides:[]});
  const report=path=>evaluate(`fetch('/api/groups/A/${path}').then(r=>r.json())`);
  const priority=await report("priority-statistics?month=2026-08");
  const duty=await report("duty-statistics?month=2026-08");
  assert.equal(priority.roles[0].flightNo,"A100");
  assert.equal(priority.roles[0].members.find(p=>p.memberId==="stable-a").adjustment,1);
  assert.ok(duty.rows.some(r=>r.dutyMemberId),"浏览器必须真实计算月度轮值");
  await evaluate("location.reload(); true");
  try { await until(()=>evaluate("!!document.querySelector('[data-view=\"config\"]')"),"刷新恢复",20000); }
  catch(error) { throw Error(String(error)+"; "+await evaluate("(async()=>JSON.stringify({text:document.body.innerText,coi:crossOriginIsolated,controller:!!navigator.serviceWorker.controller,header:await fetch(location.href).then(r=>Object.fromEntries(r.headers))}))()")); }
  assert.equal((await state("A")).people[0].name,"网页甲");
  assert.deepEqual((await state("A")).plans[planId].adopted.assignments,imported.plans[planId].draft);
  assert.deepEqual((await report("priority-statistics?month=2026-08")).roles,priority.roles);
  // Export through the actual sidebar, then start with empty browser storage.
  await click("#export-complete-configuration");
  const exported=await until(async()=>{const names=await readdir(directory);return names.find(n=>n.endsWith('.xlsx'))},"实际配置下载");
  const exportedPath=join(directory,exported);
  assert.ok((await readFile(exportedPath)).length>10000);
  const savedA=await state("A"), savedB=await state("B");
  await call("Page.navigate",{url:"about:blank"});
  await call("Storage.clearDataForOrigin",{origin:new URL(base).origin,storageTypes:"indexeddb"});
  await call("Page.navigate",{url:base});
  await ready();
  assert.equal((await state("A")).people.length,0);
  assert.equal((await state("B")).people.length,0);
  const document=await call("DOM.getDocument");
  const input=await call("DOM.querySelector",{nodeId:document.root.nodeId,selector:"#restore-workspace"});
  await call("DOM.setFileInputFiles",{nodeId:input.nodeId,files:[exportedPath]});
  await until(()=>evaluate("!!document.querySelector('#confirm-complete-configuration')"),"真实文件导入预览");
  assert.equal((await state("A")).people.length,0,"预览不得写入");
  await click("#confirm-complete-configuration");
  await until(async()=>!!(await state("A")).plans[planId],"配置与历史落盘");
  await until(()=>evaluate("!document.querySelector('#confirm-complete-configuration')"),"导入界面完成");
  await reload();
  const restoredA=await state("A"),restoredB=await state("B");
  const normalizedPeople=people=>people.map(p=>({teamLeader:false,status:"正常",remark:"",...p}));
  assert.deepEqual(normalizedPeople(restoredA.people),normalizedPeople(savedA.people));
  assert.deepEqual(restoredA.balanceSettings,savedA.balanceSettings);
  assert.deepEqual(restoredA.dutyRoster,savedA.dutyRoster);
  assert.deepEqual(restoredA.plans[planId].adopted,savedA.plans[planId].adopted);
  assert.deepEqual(normalizedPeople(restoredB.people),normalizedPeople(savedB.people));
  for(const [id,plan] of Object.entries(savedB.plans))assert.deepEqual(restoredB.plans[id],plan);
  assert.ok(Object.values(restoredB.plans).every(p=>!p.adopted && !p.draft.length),"B只新增配置维护草稿，不能混入A组班表");
  assert.deepEqual((await report("duty-statistics?month=2026-08")).rows,duty.rows);
  await click('[data-view="statistics"]');
  await until(()=>evaluate("!!document.querySelector('#priority-statistics-flight option[value=A100]')"),"统计页航班筛选");
  await click('#download-duty-template');
  await until(async()=> (await readdir(directory)).some(name=>name.includes('值班备勤')&&name.endsWith('.xlsx')),"网页值班备勤模板实际下载",4000);
  await value("#priority-statistics-flight","A100");
  assert.match(await evaluate("document.querySelector('#priority-statistics-root').innerText"),/A100/);
  await value("#active-group","B");
  await until(()=>evaluate("document.querySelector('#active-group').value==='B' && document.querySelector('.save-state').textContent.includes('已保存')"),"实际切换B组");
  assert.equal((await state("B")).people.length,1);
  await value("#active-group","A");
  await until(()=>evaluate("document.querySelector('#active-group').value==='A' && document.querySelector('.save-state').textContent.includes('已保存')"),"切回A组");
  // A failed durable write must leave the previously saved workspace intact.
  const beforeFailure=await state("A");
  await evaluate("window.__oldTransaction=IDBDatabase.prototype.transaction;IDBDatabase.prototype.transaction=function(...args){const tx=window.__oldTransaction.apply(this,args);if(args[1]==='readwrite')tx.abort();return tx};true");
  const failed=await evaluate(`fetch('/api/groups/A/workspace/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(${JSON.stringify({expectedRevision:beforeFailure.revision,action:"save-person",payload:{name:"不应保存"}})})}).then(async r=>({status:r.status,body:await r.json()}))`);
  assert.equal(failed.status,503,JSON.stringify(failed));
  await evaluate("IDBDatabase.prototype.transaction=window.__oldTransaction;delete window.__oldTransaction;true");
  assert.deepEqual(await state("A"),beforeFailure);
  const bad=await evaluate("fetch('/api/groups/A/workspace/data/preview',{method:'POST',body:new Uint8Array([80,75,1])}).then(r=>r.status)");
  assert.equal(bad,422);
  assert.deepEqual(await state("A"),beforeFailure);
  const cancelled=await evaluate(`(async()=>{const run=await fetch('/api/groups/A/workspace/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(${JSON.stringify({expectedRevision:beforeFailure.revision,action:"solve",planId})})}).then(r=>r.json());await fetch('/api/groups/A/workspace/runs/'+run.id+'/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({keep:false})});return run.id})()`);
  const stopped=await until(async()=>{const r=await evaluate(`fetch('/api/groups/A/workspace/runs/${cancelled}').then(r=>r.json())`);return r.status!=="running"?r:false},"停止排班不采用");
  assert.equal(stopped.status,"cancelled",JSON.stringify(stopped));
  assert.deepEqual(await state("A"),beforeFailure);
  await command("A","save-duty-settings",{...beforeFailure.dutyRoster,enabled:false});
  const migrated=await state("A");
  const rerun=await evaluate(`fetch('/api/groups/A/workspace/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(${JSON.stringify({expectedRevision:migrated.revision,action:"solve",planId:migrated.selectedPlanId})})}).then(r=>r.json())`);
  const rerunResult=await until(async()=>{const r=await evaluate(`fetch('/api/groups/A/workspace/runs/${rerun.id}').then(r=>r.json())`);return r.status!=="running"?r:false},"换设备导入后实际自动排班");
  assert.equal(rerunResult.status,"completed",JSON.stringify(rerunResult));
  const actual=await state("A");
  assert.equal(actual.plans[actual.selectedPlanId].valid,true,JSON.stringify(actual.plans[actual.selectedPlanId].issues));
  const competing=await evaluate(`Promise.all(['竞争甲','竞争乙'].map(name=>fetch('/api/groups/A/workspace/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expectedRevision:${actual.revision},action:'save-person',payload:{name}})}).then(r=>r.status)))`);
  assert.deepEqual(competing.sort(),[200,409],"同一旧版本的并发保存只能采用一次");
  assert.equal((await evaluate("fetch('/api/groups/B/workspace/runs/'+"+JSON.stringify(rerun.id)+").then(r=>r.status)")),404);
  await call('Emulation.setDeviceMetricsOverride',{width:390,height:900,deviceScaleFactor:1,mobile:true});
  assert.equal(await evaluate("getComputedStyle(document.querySelector('#export-complete-configuration')).display"),'flex',"浏览器窄屏仍须能导出配置");
  assert.equal(await evaluate("getComputedStyle(document.querySelector('#restore-workspace').closest('label')).display"),'flex',"浏览器窄屏仍须能导入数据");
  await call('Emulation.clearDeviceMetricsOverride');
  let productionCopy=null;
  if(process.env.GATE_TEST_CONFIGURATION){
    const file=resolve(process.env.GATE_TEST_CONFIGURATION);
    const expected=JSON.parse(execFileSync(python,["-c",`import json,sys
from pathlib import Path
from gate_schedule.excel_io import import_complete_configuration
d=import_complete_configuration(Path(sys.argv[1]).read_bytes())
print(json.dumps({g:c['history']['state'] for g,c in d['groupTransfer']['configurations'].items()}))`,file],{cwd:repo,encoding:"utf8",maxBuffer:32*1024*1024}));
    await call("Page.navigate",{url:"about:blank"});
    await call("Storage.clearDataForOrigin",{origin:new URL(base).origin,storageTypes:"indexeddb"});
    await call("Page.navigate",{url:base});await ready();
    const doc=await call("DOM.getDocument");
    const input=await call("DOM.querySelector",{nodeId:doc.root.nodeId,selector:"#restore-workspace"});
    await call("DOM.setFileInputFiles",{nodeId:input.nodeId,files:[file]});
    await until(()=>evaluate("!!document.querySelector('#confirm-complete-configuration')"),"实际配置副本预览");
    await click("#confirm-complete-configuration");
    await until(()=>evaluate("!document.querySelector('#confirm-complete-configuration')"),"实际配置导入完成");
    await reload();
    const hash=value=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
    for(const g of ['A','B']){
      const current=await state(g);
      for(const [id,plan] of Object.entries(expected[g].plans)){
        assert.equal(hash(current.plans[id].draft),hash(plan.draft),g+'组原草稿必须保持');
        assert.equal(hash(current.plans[id].adopted),hash(plan.adopted),g+'组原正式快照必须保持');
      }
      assert.deepEqual(current.balanceSettings,expected[g].balanceSettings);
      assert.deepEqual(current.dutyRoster,expected[g].dutyRoster);
    }
    const current=await state('A');
    const nativePlan=Object.values(expected.A.plans).find(p=>p.draft.length);
    const started=await evaluate(`fetch('/api/groups/A/workspace/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(${JSON.stringify({expectedRevision:current.revision,action:"solve",planId:nativePlan.id})})}).then(r=>r.json())`);
    const solved=await until(async()=>{const r=await evaluate(`fetch('/api/groups/A/workspace/runs/${started.id}').then(r=>r.json())`);return r.status!=="running"?r:false},"实际配置副本自动排班",120000);
    assert.equal(solved.status,'completed',JSON.stringify(solved));
    const actual=(await state('A')).plans[nativePlan.id];
    assert.ok(actual.draft.length>0);
    productionCopy={historyPlans:Object.keys(expected.A.plans).length,assigned:actual.draft.length,issues:actual.issues.map(i=>i.code)};
    await reload();
    await click('[data-view="schedule"]');
    for(const width of [1440,390]){
      await call('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:width<500});
      await writeFile(join(repo,`.tmp/browser-local-${width}.png`),Buffer.from((await call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    }
  }
  assert.ok(!requests.some(url=>new URL(url,base).pathname.startsWith("/api/")),"不得请求任何Python后台");
  assert.deepEqual(errors,[],"网页不得发生未处理错误");
  await writeFile(join(repo,".tmp/browser-local-result.json"),JSON.stringify({requests,errors,base,productionCopy,passed:true},null,2));
});
