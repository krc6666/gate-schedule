import assert from "node:assert/strict";
import {test} from "node:test";
import {createServer} from "node:http";
import {spawn, execFileSync} from "node:child_process";
import {readFile, mkdir, mkdtemp, rm, writeFile, readdir} from "node:fs/promises";
import {join, resolve, extname} from "node:path";
import {fileURLToPath} from "node:url";
import {createHash} from "node:crypto";
import {dedicatedCrossConfiguration} from './dedicated-cross-fixture.mjs';

const repo=fileURLToPath(new URL("../../",import.meta.url));
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function until(fn,label,ms=90000){const end=Date.now()+ms;while(Date.now()<end){const x=await fn();if(x)return x;await delay(100)}throw Error("等待超时："+label)}

test("静态子路径网页：运行文件失败后自动重试并完成排班和本机保存",{timeout:300000},async t=>{
  const requests=[],errors=[];let failedRuntimeOnce=false;
  const mime={".html":"text/html",".js":"application/javascript",".wasm":"application/wasm",".css":"text/css",".json":"application/json",".zip":"application/zip"};
  const server=createServer(async(req,res)=>{
    requests.push(req.url);
    try{
      const url=new URL(req.url,"http://localhost");
      assert.ok(url.pathname.startsWith("/gate-schedule/"),"静态资源必须留在项目子路径："+url.pathname);
      const name=decodeURIComponent(url.pathname.slice("/gate-schedule/".length))||"index.html";
      if(!process.env.GATE_TEST_URL&&!failedRuntimeOnce&&name.endsWith("runtime/pyodide/openpyxl-3.1.5-py2.py3-none-any.whl")){failedRuntimeOnce=true;res.statusCode=503;res.end("temporary runtime download failure");return}
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
    ["--headless=new","--no-first-run","--no-default-browser-check","--disable-background-networking","--remote-debugging-port=0","--user-data-dir="+join(directory,"chrome"),"about:blank"],
    {windowsHide:true,stdio:"ignore"});
  const port=await until(async()=>{try{return(await readFile(join(directory,"chrome/DevToolsActivePort"),"utf8")).trim().split(/\r?\n/)[0]}catch{return false}},"浏览器端口",15000);
  const target=await until(async()=>{const list=await(await fetch("http://127.0.0.1:"+port+"/json/list")).json();return list.find(x=>x.type==="page")},"页面");
  ws=new WebSocket(target.webSocketDebuggerUrl);
  await new Promise(r=>ws.addEventListener("open",r,{once:true}));
  ws.addEventListener("message",event=>{const m=JSON.parse(event.data);if(m.method==="Runtime.exceptionThrown")errors.push(m.params);if(process.env.GATE_TEST_URL&&m.method==="Network.requestWillBeSent")requests.push(m.params.request.url);const p=pending.get(m.id);if(p){pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result)}});
  const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++next;const timer=setTimeout(()=>{pending.delete(id);reject(Error("浏览器调用超时："+method))},120000);pending.set(id,{resolve:value=>{clearTimeout(timer);resolve(value)},reject:error=>{clearTimeout(timer);reject(error)}});ws.send(JSON.stringify({id,method,params}))});
  const evaluate=async expression=>{const r=await call("Runtime.evaluate",{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value};
  await call("Runtime.enable");
  await call("Page.enable");
  await call("Network.enable");
  // Simulate the library missing its first reload notification. The application
  // must wait for worker control and reload into isolation before it starts.
  await call("Page.addScriptToEvaluateOnNewDocument", {source: "window.coi={doReload:()=>{}}"});
  await call("Browser.setDownloadBehavior",{behavior:"allow",downloadPath:directory});
  await call("Page.navigate", {url: base});
  const click=selector=>evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);
  const value=(selector,value)=>evaluate(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});el.value=${JSON.stringify(value)};el.dispatchEvent(new Event('change',{bubbles:true}))})()`);
  const ready=async()=>{
    try { await until(()=>evaluate("document.documentElement?.dataset.gateLocal === 'true' && !!document.querySelector('[data-view=\"config\"]')"),"浏览器本机工作台",120000); }
    catch(error) { throw Error(String(error)+"; "+await evaluate("document.body.innerText")); }
  };
  const reload=async(ms=120000)=>{
    await evaluate("window.__reloadPending=true; location.reload(); true");
    try { await until(()=>evaluate("!window.__reloadPending && document.documentElement?.dataset.gateLocal === 'true' && !!document.querySelector('[data-view=\"config\"]')"),"重新打开工作台",ms); }
    catch(error) { throw Error(String(error)+'; '+await evaluate("JSON.stringify({pending:window.__reloadPending,local:document.documentElement?.dataset.gateLocal,isolated:crossOriginIsolated,text:document.body.innerText})")); }
  };
  await ready();
  if(!process.env.GATE_TEST_URL){assert.equal(failedRuntimeOnce,true,"测试应注入一次运行文件下载失败");assert.ok(requests.filter(path=>path.endsWith("runtime/pyodide/openpyxl-3.1.5-py2.py3-none-any.whl")).length>=2,"运行文件失败后应自动重新下载")}
  assert.equal(await evaluate("document.body.innerText.includes('Python')"),false,"用户无需安装 Python");
  assert.equal(await evaluate("crossOriginIsolated"),true);
  assert.deepEqual(await evaluate("[...document.querySelectorAll('.app-nav [data-view]')].map(x=>x.textContent.trim())"),["配置","航班","排班","规则","统计","历史"]);
  const state=group=>evaluate("fetch('/api/groups/"+group+"/workspace').then(r=>r.json())");
  assert.equal((await state("A")).people.length,0);
  const command=async(group,action,payload={},planId)=>{
    const current=await state(group);
    const result=await evaluate("fetch('/api/groups/"+group+"/workspace/command',{method:'POST',headers:{'Content-Type':'application/json','X-Gate-Rule-Schema':'flight-role-v2'},body:JSON.stringify("+JSON.stringify({expectedRevision:current.revision,action,payload,planId})+")}).then(async r=>({status:r.status,value:await r.json()}))");
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
  // Old controls may survive until navigation commits. Wait for the replacement
  // document and its fetch runtime, keeping this check's original 20s bound.
  await reload(20000);
  assert.equal((await state("A")).people[0].name,"网页甲");
  assert.deepEqual((await state("A")).plans[planId].adopted.assignments,imported.plans[planId].draft);
  assert.deepEqual((await report("priority-statistics?month=2026-08")).roles,priority.roles);
  await click('[data-view="rules"]');
  assert.equal(await evaluate('document.querySelectorAll("[data-coverage-role]:checked").length'),0,'新设置不得替用户预选岗位');
  await evaluate('document.querySelector("#coverage-priority-rules").open=true');
  await click('[data-coverage-order-tier="normal"][data-coverage-order-action="enable"]');
  const orderBefore=await evaluate('[...document.querySelectorAll("[data-coverage-order-group=normal] tbody th")].map(el=>el.textContent)');
  assert.ok(orderBefore.length>1);
  await click(`[data-coverage-order-role="${orderBefore[1]}"][data-coverage-order-action="up"]`);
  await click('[data-coverage-role="登机主验"][data-coverage-priority="protect"]');
  await evaluate('document.querySelector("#relaxed-shift-rules").open=true');
  await click('[data-relaxed-enabled="earlyDeparture"]');
  await value('[data-relaxed-gap="earlyDeparture"]','3');
  await value('[data-relaxed-gap="afternoonRest"]','0');
  await click('#save-rules');
  await until(async()=> (await state('A')).balanceSettings.coveragePriorities?.some(i=>i.roleName==='登机主验'&&i.priority==='protect'),'保岗勾选真实保存');
  await until(()=>evaluate('!document.querySelector("#save-rules").disabled'),'保存完成');
  const savedOrder=(await state('A')).balanceSettings.coverageRoleOrder;
  assert.deepEqual(savedOrder,[orderBefore[1],orderBefore[0],...orderBefore.slice(2)],'同档顺序真实保存，不自动重排旧正式');
  const bBalance=(await state('B')).balanceSettings;
  await command('B','update-balance-settings',{balanceSettings:{...bBalance,coveragePriorities:[{roleName:'登机主验',priority:'leave-empty'}],coverageRoleOrder:[...savedOrder].reverse()}});
  await reload();
  await click('[data-view="rules"]');
  assert.equal(await evaluate('document.querySelector("[data-coverage-role=登机主验][data-coverage-priority=protect]").checked'),true,'刷新后保岗勾选保持');
  await evaluate('document.querySelector("#coverage-priority-rules").open=true');
  for(const width of [1440,390]){
    await call('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:width<500});
    assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth'),true,'优先级勾选不撑宽页面');
    assert.equal(await evaluate('[...document.querySelectorAll("[data-coverage-role]")].every(el=>el.getBoundingClientRect().right<=innerWidth)'),true,'小屏同时看得到两列勾选，无需横向找控件');
    await writeFile(join(repo,`.tmp/coverage-rules-${width}.png`),Buffer.from((await call('Page.captureScreenshot',{format:'png'})).data,'base64'));
  }
  await call('Emulation.clearDeviceMetricsOverride');
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
  assert.deepEqual(restoredA.relaxedShiftSettings,savedA.relaxedShiftSettings);
  assert.deepEqual(restoredB.relaxedShiftSettings,savedB.relaxedShiftSettings);
  assert.equal(restoredA.relaxedShiftSettings.earlyDepartureBalanceEnabled,false);
  assert.equal(restoredA.relaxedShiftSettings.earlyDepartureMaxDifference,3);
  assert.equal(restoredA.relaxedShiftSettings.afternoonRestMaxDifference,0);
  const normalizedPeople=people=>people.map(p=>({teamLeader:false,status:"正常",remark:"",...p}));
  assert.deepEqual(normalizedPeople(restoredA.people),normalizedPeople(savedA.people));
  assert.deepEqual(restoredA.balanceSettings,savedA.balanceSettings);
  assert.deepEqual(restoredB.balanceSettings.coveragePriorities,[{roleName:'登机主验',priority:'leave-empty'}],'另一组相反选择随文件迁移');
  assert.deepEqual(restoredA.balanceSettings.coverageRoleOrder,savedOrder,'A组顺序随Excel导入空浏览器再刷新保留');
  assert.deepEqual(restoredB.balanceSettings.coverageRoleOrder,[...savedOrder].reverse(),'B组独立顺序保留');
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
  // Real WASM solve after two unfair manual days: neither draft nor the target
  // date may be counted as earlier history, and the lower-count person rests.
  const restFixture=JSON.parse(execFileSync(python,['-c',`import json
from tests.test_balance_workflow import small_configuration
from gate_schedule.domain import default_relaxed_shift_settings
d=small_configuration(None)
d['relaxedShiftSettings']=default_relaxed_shift_settings()
d['day']['serviceDate']='2044-03-02'
d['day']['flights'][0].update(windowStart='09:00',windowEnd='10:00')
d['day']['flights'][1].update(windowStart='14:00',windowEnd='15:00')
d['day']['cells'][0]['requiredCountOverride']=2
print(json.dumps(d))`],{cwd:repo,encoding:'utf8'}));
  let restState=await command('B','import-complete-configuration',restFixture);
  await command('B','save-duty-settings',{...restState.dutyRoster,enabled:false,overrides:[]});
  for(const role of restState.configuration.roleConfigs)if(role.roleName!=='接机1')await command('B','save-role',{...role,requiredCount:0});
  let restId=restState.selectedPlanId;
  for(const day of ['2044-03-04','2044-03-06']){
    await command('B','edit-cell',{cellId:'c0',text:'甲 / 乙'},restId);
    await command('B','edit-cell',{cellId:'c1',text:'乙'},restId);
    const nextDay=structuredClone((await state('B')).plans[restId].day);nextDay.serviceDate=day;
    restState=await command('B','archive-next',{day:nextDay},restId);restId=restState.selectedPlanId;
  }
  assert.equal(restState.plans[restId].valid,true,JSON.stringify(restState.plans[restId].issues));
  for(const key of ['earlyDepartures','afternoonRest'])assert.deepEqual(restState.relaxedShiftSummary[key].map(p=>p.memberName),['乙']);
  // The same date/clear workflow must also run entirely inside the browser.
  const preservedA=await state('A');
  const carried=await command('B','new-plan',{serviceDate:'2044-04-01'});
  const carriedPlan=carried.plans[carried.selectedPlanId];
  assert.deepEqual(carriedPlan.day.flights.map(f=>f.flightNo),['F0','F1']);
  assert.deepEqual(carriedPlan.day.memberAvailability.map(a=>a.participating),[true,true]);
  const repeated=await command('B','new-plan',{serviceDate:'2044-04-01'});
  assert.equal(repeated.selectedPlanId,carried.selectedPlanId);
  const cleared=await command('B','clear-history');
  assert.deepEqual(cleared.historyPlanIds,[]);
  assert.deepEqual(cleared.plans[cleared.selectedPlanId].day,carriedPlan.day);
  assert.deepEqual(await state('A'),preservedA);
  await reload();assert.deepEqual((await state('B')).historyPlanIds,[]);
  // Flight exceptions must survive browser-only transfer and affect the WASM solve.
  const handoffConfig=JSON.parse(execFileSync(python,['-c',`import json
from tests.test_handoff_exceptions import configuration
from tests.test_handoff_preferences import preference
print(json.dumps(configuration([preference(targets=['登机核验']),preference(flight='TW616',targets=['登机主验']),dict(preference(flight='TW616',source='接机1'),enabled=False,boardingRoles=[])])))`],{cwd:repo,encoding:'utf8'}));
  let handoffState=await command('B','import-complete-configuration',handoffConfig);
  await command('B','save-duty-settings',{...handoffState.dutyRoster,enabled:false,overrides:[]});
  for(const role of handoffState.configuration.roleConfigs)if(!handoffConfig.day.roleNames.includes(role.roleName))await command('B','save-role',{...role,requiredCount:0});
  const solveHandoff=async role=>{
    const s=await state('B'),pid=s.selectedPlanId;
    const run=await evaluate(`fetch('/api/groups/B/workspace/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(${JSON.stringify({expectedRevision:s.revision,action:'solve',planId:pid})})}).then(r=>r.json())`);
    const result=await until(async()=>{const r=await evaluate(`fetch('/api/groups/B/workspace/runs/${run.id}').then(r=>r.json())`);return r.status!=='running'?r:false},'WASM航班偏好实际求解');
    assert.equal(result.status,'completed',JSON.stringify(result));
    const after=await state('B'),p=after.plans[pid];assert.equal(p.valid,true,JSON.stringify(p.issues));assert.equal(p.draft.length,5);
    const task=p.schedule.tasks.find(t=>t.roleId===role),member=after.people.find(p=>p.name==='乙');
    assert.ok(p.draft.some(a=>a.taskId===task.id&&a.memberId===member.id),role+'实际按偏好接续');
  };
  await solveHandoff('登机主验');
  await reload();
  const handoffA=await state('A');
  handoffState=await state('B');
  const handoffFile=await evaluate(`fetch('/api/groups/B/workspace/configuration/export/${handoffState.selectedPlanId}').then(r=>r.arrayBuffer()).then(b=>btoa(String.fromCharCode(...new Uint8Array(b))))`);
  const emptyHandoff={formatVersion:2,state:{stateVersion:2,timeVersion:2,revision:0,people:[],plans:{},selectedPlanId:null,migrationReport:[]}};
  assert.equal(await evaluate(`fetch('/api/groups/B/workspace/restore?expectedRevision=${handoffState.revision}',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(${JSON.stringify(emptyHandoff)})}).then(r=>r.status)`),200);
  const handoffPreview=await evaluate(`fetch('/api/groups/B/workspace/configuration/preview',{method:'POST',body:Uint8Array.from(atob(${JSON.stringify(handoffFile)}),c=>c.charCodeAt(0))}).then(r=>r.json())`);
  await command('B','import-complete-configuration',handoffPreview.payload);
  await reload();
  assert.deepEqual((await state('B')).handoffPreferences,handoffConfig.handoffPreferences);
  await solveHandoff('登机主验');
  await command('B','save-handoff-preferences',{preferences:handoffConfig.handoffPreferences.filter(p=>p.flightNo==='*')});
  await solveHandoff('登机核验');
  const handoffPreservedA=await state('A');
  for(const key of ['people','configuration','handoffPreferences','balanceSettings','dutyRoster','relaxedShiftSettings'])assert.deepEqual(handoffPreservedA[key],handoffA[key],'B偏好迁移保留A组'+key);
  for(const [id,plan] of Object.entries(handoffA.plans))assert.deepEqual(handoffPreservedA.plans[id],plan,'A原计划和归档保持');
  const coverageFixture=JSON.parse(execFileSync(python,['-c',"import json;from tests.test_early_flight_coverage import coverage_configuration;print(json.dumps(coverage_configuration()))"],{cwd:repo,encoding:'utf8'}));
  let coverageState=await command('B','import-complete-configuration',coverageFixture);
  for(const role of coverageState.configuration.roleConfigs)if(!coverageFixture.day.roleNames.includes(role.roleName))await command('B','save-role',{...role,requiredCount:0});
  const solveCoverage=async(action='solve',payload={})=>{
    const s=await state('B'),pid=s.selectedPlanId;
    const started=await evaluate(`fetch('/api/groups/B/workspace/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(${JSON.stringify({expectedRevision:s.revision,action,payload,planId:pid})})}).then(r=>r.json())`);
    const done=await until(async()=>{const r=await evaluate(`fetch('/api/groups/B/workspace/runs/${started.id}').then(r=>r.json())`);return r.status!=='running'?r:false},'WASM专岗与早结束航班优先');
    assert.equal(done.status,'completed',JSON.stringify(done));
    const after=await state('B');return after.plans[after.selectedPlanId];
  };
  let coveragePlan=await solveCoverage();
  const coverageTasks=coveragePlan.schedule.tasks.filter(t=>coveragePlan.draft.some(a=>a.taskId===t.id));
  assert.equal(coverageTasks.filter(t=>t.exclusiveSameFlight).length,2);
  assert.deepEqual(coverageTasks.filter(t=>t.roleId==='普通岗').map(t=>t.flightId),['A']);
  const shortageDay=structuredClone(coveragePlan.day);shortageDay.roleConfigs.find(r=>r.roleName==='登机主验').requiredCount=2;
  await command('B','save-input',{day:shortageDay},coveragePlan.id);
  coveragePlan=await solveCoverage();assert.equal(coveragePlan.draft.length,3);assert.match(coveragePlan.message,/专岗未配齐/);assert.equal(coveragePlan.valid,false);
  await command('B','adopt',{},coveragePlan.id);
  await reload();await value('#active-group','B');await click('[data-view="schedule"]');
  await until(()=>evaluate('document.querySelectorAll(".wb-matrix .schedule-cell-conflict").length>0'),'浏览器专岗缺口标红');
  await click('[data-view="config"]');assert.equal(await evaluate('document.querySelectorAll("[data-role-concurrent]").length'),0);
  await click('[data-view="history"]');assert.ok(await evaluate('document.querySelectorAll(".history-matrix .schedule-cell-conflict").length>0'));
  const withholdingFixture=JSON.parse(execFileSync(python,['-c',"import json;from tests.test_coverage_priorities import shortage_handoff_configuration;print(json.dumps(shortage_handoff_configuration()))"],{cwd:repo,encoding:'utf8'}));
  const withholdingState=await command('B','import-complete-configuration',withholdingFixture);
  for(const role of withholdingState.configuration.roleConfigs)if(!withholdingFixture.day.roleNames.includes(role.roleName))await command('B','save-role',{...role,requiredCount:0});
  const assertWithheld=plan=>{
    assert.ok(plan.draft.length>0,'接机仍正常安排');
    const jobs=plan.schedule.tasks;
    const source=jobs.find(t=>t.roleId==='接机1'&&t.flightId==='A');
    const target=jobs.find(t=>t.roleId==='登机查验'&&t.flightId==='A');
    const owner=plan.draft.find(a=>a.taskId===source.id)?.memberId;
    assert.ok(owner,'接机来源人员仍保留');
    assert.ok(!plan.draft.some(a=>a.taskId===target.id),'缺人时优先留空高于合法同人兼职');
    assert.ok(plan.issues.some(i=>i.code==='coverage_shortfall'),'所有实际缺口仍显示');
  };
  let withheld=await solveCoverage();assertWithheld(withheld);
  const followingDay=structuredClone(withheld.day);followingDay.serviceDate='2026-08-22';
  withheld=await solveCoverage('archive-next',{day:followingDay});assertWithheld(withheld);
  assert.equal(withheld.day.serviceDate,'2026-08-22','真实WASM归档排后天仍按留空优先执行');
  const relationsFixture=JSON.parse(execFileSync(python,['-c',"import json;from tests.test_work_relations import configuration;print(json.dumps(configuration()))"],{cwd:repo,encoding:'utf8'}));
  let relationsState=await command('B','import-complete-configuration',relationsFixture);
  for(const role of relationsState.configuration.roleConfigs)if(!relationsFixture.day.roleNames.includes(role.roleName))await command('B','save-role',{...role,requiredCount:0});
  relationsState=await state('B');let relationsPlan=relationsState.plans[relationsState.selectedPlanId];
  await command('B','save-handoff-preferences',{preferences:[{flightNo:'*',arrivalRole:'大件行李拦截',boardingRoles:['入境申报','登机1','登机2'],enabled:true,ordered:true}]});
  const checkFlowDraft=plan=>{
    assert.equal(plan.valid,true,JSON.stringify(plan.issues));
    assert.equal(plan.draft.length,6);
    const sourceIds=new Set(plan.schedule.tasks.filter(t=>t.flightId==='A').map(t=>t.id));
    const otherIds=new Set(plan.schedule.tasks.filter(t=>t.flightId==='B').map(t=>t.id));
    const sourcePeople=new Set(plan.draft.filter(a=>sourceIds.has(a.taskId)).map(a=>a.memberId));
    assert.ok(plan.draft.filter(a=>otherIds.has(a.taskId)).every(a=>!sourcePeople.has(a.memberId)),'同班接续补齐，不能借给重叠航班');
    assert.deepEqual(plan.taskHandoffOptions,[],'流程不要求逐项接岗确认');
  };
  relationsPlan=await solveCoverage();checkFlowDraft(relationsPlan);
  await command('B','adopt',{},relationsPlan.id);
  const relationsFile=await evaluate(`fetch('/api/groups/B/workspace/configuration/export/${relationsPlan.id}').then(r=>r.arrayBuffer()).then(b=>btoa(String.fromCharCode(...new Uint8Array(b))))`);
  const relationsPreview=await evaluate(`fetch('/api/groups/B/workspace/configuration/preview',{method:'POST',body:Uint8Array.from(atob(${JSON.stringify(relationsFile)}),c=>c.charCodeAt(0))}).then(r=>r.json())`);
  assert.ok(relationsPreview.payload,JSON.stringify(relationsPreview));
  await command('B','import-complete-configuration',relationsPreview.payload);await reload();
  relationsPlan=await solveCoverage();checkFlowDraft(relationsPlan);
  await value('#active-group','B');await click('[data-view="rules"]');
  assert.ok(await evaluate('!!document.querySelector("#baggage-rules-base")'));
  const wasmDraft=structuredClone(relationsPlan.draft);
  const bagTaskIds=new Set(relationsPlan.schedule.tasks.filter(t=>t.roleId==='大件行李拦截').map(t=>t.id));
  const bagCells=relationsPlan.day.cells.filter(c=>c.roleName==='大件行李拦截').map(c=>c.id);
  let clearedWasm=await command('B','clear-position',{cellIds:bagCells},relationsPlan.id);
  assert.deepEqual(clearedWasm.plans[relationsPlan.id].draft,wasmDraft.filter(a=>!bagTaskIds.has(a.taskId)),'WASM岗位清空仅移除共享轮换实际任务');
  const flightCounts=clearedWasm.plans[relationsPlan.id].schedule.memberFlights;
  await reload();
  clearedWasm=await state('B');assert.deepEqual(clearedWasm.plans[relationsPlan.id].schedule.memberFlights,flightCounts,'WASM清空后统计重载保持');
  assert.equal(clearedWasm.plans[relationsPlan.id].undoAvailable,true);
  const undoWasm=await command('B','undo-edit',{},relationsPlan.id);
  assert.deepEqual(undoWasm.plans[relationsPlan.id].draft,wasmDraft,'WASM撤销恢复全部大件轮换和后续岗位');
  // Phase-one core rules must run with the same Bool/Int model in WASM.
  const relativeFixtures=JSON.parse(execFileSync(python,['-c',`import json
from tests.test_relative_scheduling import relative_day, shared_pool_day
print(json.dumps([relative_day(),shared_pool_day()]))`],{cwd:repo,encoding:'utf8'}));
  for(const [index,day] of relativeFixtures.entries()){
    day.serviceDate='2056-08-0'+(index+2);
    if(index===1){
      day.workRules.pairs[0].flightNo='B*';
      // First-flight baggage can serve its own declaration; it cannot borrow
      // the overlapping second flight's ordinary positions.
      for(const cell of day.cells)if(cell.roleName==='入境申报')cell.requiredCountOverride=cell.flightId==='A'?1:0;
    }
    let current=await command('B','import-complete-configuration',{day,rotationEnabled:true,rotationMinutes:60});
    const pid=current.selectedPlanId;
    const preferences=index===0 ? [] : [{flightNo:'*',arrivalRole:'大件行李拦截',boardingRoles:['入境申报','登机1','登机2'],enabled:true,ordered:true}];
    current=await command('B','save-handoff-preferences',{preferences});
    if(index===0){
      for(const role of current.configuration.roleConfigs)if(!day.roleNames.includes(role.roleName))await command('B','save-role',{...role,requiredCount:0});
      await reload();await value('#active-group','B');await click('[data-view="rules"]');
      assert.equal(await evaluate('document.querySelector("#relative-time-rules")'),null);
      await click('#add-flow-rule');await value('[data-flow-rule="0"] [data-flow-source]','大件行李拦截');
      await click('[data-flow-rule="0"] [data-flow-add-target]');await value('[data-flow-rule="0"] [data-flow-target]','登机核验');
      const revision=(await state('B')).revision;await click('#save-rules');
      await until(async()=>(await state('B')).revision>revision,'WASM保存自由流程');
      await until(()=>evaluate('!document.querySelector("#save-rules").disabled'),'WASM保存完成');
      current=await state('B');
      assert.equal(current.handoffPreferences[0].boardingRoles[0],'登机核验');
    }
    const run=await evaluate(`fetch('/api/groups/B/workspace/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(${JSON.stringify({expectedRevision:current.revision,action:'solve',planId:pid})})}).then(r=>r.json())`);
    const done=await until(async()=>{const r=await evaluate(`fetch('/api/groups/B/workspace/runs/${run.id}').then(r=>r.json())`);return r.status!=='running'?r:false},'WASM相对时间与大件释放');
    assert.equal(done.status,'completed',JSON.stringify(done));
    const plan=(await state('B')).plans[pid];
    assert.equal(plan.valid,true,JSON.stringify(plan.issues));
    assert.equal(plan.day.dedicatedFlightProtection,true);
    if(index===0){
      assert.equal(plan.schedule.tasks[0].start,'前日23:30');
      assert.equal(plan.schedule.tasks[0].end,'00:20');
    }else{
      const blocks=plan.schedule.tasks.filter(t=>t.roleId==='大件行李拦截').sort((a,b)=>a.start.localeCompare(b.start));
      assert.equal(blocks.length,3);
      const boarding=plan.schedule.tasks.filter(t=>['登机1','登机2'].includes(t.roleId)).map(t=>t.id);
      const owners=plan.draft.filter(a=>boarding.includes(a.taskId));
      assert.equal(new Set(owners.map(a=>a.memberId)).size,2,'B*系列规则在WASM仍要求两登机岗分人');
      await command('B','edit-slot',{taskId:boarding[1],slot:0,memberId:owners.find(a=>a.taskId===boarding[0]).memberId},pid);
      assert.ok((await state('B')).plans[pid].issues.some(i=>i.code==='role_separation'),'后置违反分人仍被独立校验识别');

    }
  }
  // The new scope is solved by the browser's CP-SAT adapter as well.
  const flowDay=structuredClone(relativeFixtures[0]);
  flowDay.serviceDate='2056-08-07';flowDay.roster=['甲','乙'];flowDay.roleNames=['入境申报','登机1','登机2'];
  flowDay.flights=[{id:'A',flightNo:'CX937',windowStart:'08:00',windowEnd:'11:00'},{id:'B',flightNo:'CX931',windowStart:'09:00',windowEnd:'12:00'}];
  flowDay.roleConfigs=flowDay.roleNames.map(roleName=>({roleName,requiredCount:0,workMode:roleName==='入境申报'?'arrival':'boarding',concurrent:true}));
  flowDay.memberConfigs=flowDay.roster.map(memberName=>({memberName,eligibleRoleNames:flowDay.roleNames}));
  flowDay.memberAvailability=flowDay.roster.map(memberName=>({memberName,participating:true}));
  const needed=new Set(['A入境申报','A登机1','B登机1']);
  flowDay.cells=flowDay.flights.flatMap(f=>flowDay.roleNames.map(roleName=>({id:f.id+roleName,flightId:f.id,flightNo:f.flightNo,roleName,rawValue:'',internalNames:[],externalLabels:[],semantics:'empty',requiredCountOverride:needed.has(f.id+roleName)?1:0,fixedMemberNames:f.id==='A'&&roleName==='入境申报'?['甲']:f.id==='A'&&roleName==='登机1'?['乙']:[]})));
  const flowRules={pairs:[],baggage:[],separationGroups:[{id:'declaration',name:'同班申报登机分人',flightNo:'*',roles:flowDay.roleNames,enabled:true}]};
  const flowPrefs=[{flightNo:'*',arrivalRole:'入境申报',boardingRoles:['登机1','登机2'],enabled:true,ordered:true,targetFlightScopes:{'登机1':'later-overlap','登机2':'later-overlap'}}];
  let flowState=await command('B','import-complete-configuration',{day:flowDay,rotationEnabled:true,rotationMinutes:60,workRules:flowRules,handoffPreferences:flowPrefs});
  const flowPid=flowState.selectedPlanId;
  for(const role of flowState.configuration.roleConfigs)if(!flowDay.roleNames.includes(role.roleName))await command('B','save-role',{...role,requiredCount:0});
  flowState=await state('B');
  const flowRun=await evaluate(`fetch('/api/groups/B/workspace/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(${JSON.stringify({expectedRevision:flowState.revision,action:'solve',planId:flowPid})})}).then(r=>r.json())`);
  const flowDone=await until(async()=>{const r=await evaluate(`fetch('/api/groups/B/workspace/runs/${flowRun.id}').then(r=>r.json())`);return r.status!=='running'?r:false},'WASM同班分人及晚结束跨班接续');
  assert.equal(flowDone.status,'completed',JSON.stringify(flowDone));
  const flowPlan=(await state('B')).plans[flowPid];assert.equal(flowPlan.valid,true,JSON.stringify(flowPlan.issues));
  const flowOwner=flowPlan.draft.find(a=>a.taskId==='imported-A入境申报').memberId;
  assert.ok(flowPlan.draft.some(a=>a.taskId==='imported-B登机1'&&a.memberId===flowOwner));
  await command('B','edit-slot',{taskId:'imported-A登机1',slot:0,memberId:flowOwner},flowPid);
  assert.ok((await state('B')).plans[flowPid].issues.some(i=>i.code==='role_separation'));
  await command('B','undo-edit',{},flowPid);await reload();
  assert.deepEqual((await state('B')).workRules,flowRules);assert.deepEqual((await state('B')).handoffPreferences,flowPrefs);
  // An explicit cross-flight prohibition must beat the permitted preference
  // in WASM, and survive export/import, reload and manual reverse validation.
  const forbiddenRules={...flowRules,forbiddenHandoffs:[{flightNo:'*',sourceRole:'入境申报',targets:[{role:'登机1',flightScope:'later-overlap'}],enabled:true}]};
  flowState=await state('B');
  await command('B','save-rules',{configuration:flowState.configuration,balanceSettings:flowState.balanceSettings,preferences:flowPrefs,workRules:forbiddenRules});
  const forbiddenPlan=await solveCoverage();
  assert.ok(!forbiddenPlan.draft.some(a=>a.taskId==='imported-B登机1'&&a.memberId===flowOwner),'禁止优先于跨航班接续，宁留缺口');
  assert.ok(forbiddenPlan.issues.some(i=>i.code==='coverage_shortfall'));
  await command('B','edit-slot',{taskId:'imported-B登机1',slot:0,memberId:flowOwner},flowPid);
  assert.ok((await state('B')).plans[flowPid].issues.some(i=>i.code==='forbidden_handoff'),'WASM人工违规标红');
  await command('B','undo-edit',{},flowPid);
  const forbiddenFile=await evaluate(`fetch('/api/groups/B/workspace/configuration/export/${flowPid}').then(r=>r.arrayBuffer()).then(b=>btoa(String.fromCharCode(...new Uint8Array(b))))`);
  const forbiddenPreview=await evaluate(`fetch('/api/groups/B/workspace/configuration/preview',{method:'POST',body:Uint8Array.from(atob(${JSON.stringify(forbiddenFile)}),c=>c.charCodeAt(0))}).then(r=>r.json())`);
  assert.deepEqual(forbiddenPreview.payload.workRules,forbiddenRules);
  await command('B','import-complete-configuration',forbiddenPreview.payload);await reload();
  assert.deepEqual((await state('B')).workRules,forbiddenRules);
  assert.ok((await solveCoverage()).issues.some(i=>i.code==='coverage_shortfall'));
  // Indexed baggage rules use physical baggage qualifications in WASM too.
  const baggageDay=JSON.parse(execFileSync(python,['-c','import json;from tests.test_baggage_flow_roles import turn_day;print(json.dumps(turn_day()))'],{cwd:repo,encoding:'utf8'}));
  baggageDay.serviceDate='2057-08-12';
  const baggageRules={...baggageDay.workRules,forbiddenHandoffs:[{flightNo:'*',sourceRole:'大件1',targets:[{role:'登机1',flightScope:'same-flight'}],enabled:true}]};
  baggageRules.baggageWindow.remainderToleranceMinutes=15;
  baggageRules.separationGroups.push({id:'second',name:'第二轮与申报分人',flightNo:'*',roles:['大件2','入境申报'],enabled:true});
  const baggageState=await command('B','import-complete-configuration',{day:baggageDay,workRules:baggageRules,handoffPreferences:baggageDay.flowPreferences,rotationEnabled:false,rotationMinutes:15});
  for(const role of baggageState.configuration.roleConfigs)if(!baggageDay.roleNames.includes(role.roleName))await command('B','save-role',{...role,requiredCount:0});
  let baggagePlan=await solveCoverage();assert.equal(baggagePlan.valid,true,JSON.stringify(baggagePlan.issues));
  const baggageBlocks=baggagePlan.schedule.tasks.filter(t=>t.roleId==='大件行李拦截');assert.equal(baggageBlocks.length,3);
  assert.equal(baggageBlocks[2].end,'11:45');assert.equal(baggagePlan.baggageCoverage.requiredPeople,3);
  const baggageOwner=id=>baggagePlan.draft.find(a=>a.taskId===id).memberId;
  assert.equal(new Set(baggageBlocks.map(t=>baggageOwner(t.id))).size,3);
  for(const [index,role] of ['入境申报','登机1','登机2'].entries())assert.equal(baggageOwner(baggageBlocks[index].id),baggageOwner(baggagePlan.schedule.tasks.find(t=>t.roleId===role).id));
  await command('B','edit-slot',{taskId:baggageBlocks[1].id,slot:0,memberId:baggageOwner(baggageBlocks[0].id)},baggagePlan.id);
  assert.ok((await state('B')).plans[baggagePlan.id].issues.some(i=>i.code==='baggage_rotation_person'));
  await command('B','undo-edit',{},baggagePlan.id);
  for(const [minutes,count] of [[120,2],[60,3]]){
    const current=await state('B');
    await command('B','save-rules',{configuration:current.configuration,balanceSettings:current.balanceSettings,preferences:current.handoffPreferences,workRules:{...baggageRules,baggageWindow:{...baggageRules.baggageWindow,rotationMinutes:minutes}}});
    baggagePlan=await solveCoverage();assert.equal(baggagePlan.valid,true,JSON.stringify(baggagePlan.issues));assert.equal(baggagePlan.baggageCoverage.requiredPeople,count);
  }
  const baggageFile=await evaluate(`fetch('/api/groups/B/workspace/configuration/export/${baggagePlan.id}').then(r=>r.arrayBuffer()).then(b=>btoa(Array.from(new Uint8Array(b),x=>String.fromCharCode(x)).join('')))`);
  const baggagePreview=await evaluate(`fetch('/api/groups/B/workspace/configuration/preview',{method:'POST',body:Uint8Array.from(atob(${JSON.stringify(baggageFile)}),c=>c.charCodeAt(0))}).then(r=>r.json())`);
  assert.deepEqual(baggagePreview.payload.workRules,baggageRules);
  await command('B','import-complete-configuration',baggagePreview.payload);await reload();
  assert.deepEqual((await state('B')).workRules,baggageRules);
  baggagePlan=await solveCoverage();assert.equal(baggagePlan.valid,true,JSON.stringify(baggagePlan.issues));assert.equal(baggagePlan.baggageCoverage.requiredPeople,3);
  const shortBaggageDay=structuredClone(baggagePlan.day);shortBaggageDay.flights[0].departureTime='10:15';
  await command('B','save-input',{day:shortBaggageDay},baggagePlan.id);
  baggagePlan=await solveCoverage();assert.equal(baggagePlan.baggageCoverage.requiredPeople,1);
  assert.deepEqual(baggagePlan.baggageCoverage.groups[0].turns,[{start:'08:30',end:'09:15',role:'大件1'}]);
  const cxKeDay=JSON.parse(execFileSync(python,['-c','import json;from tests.test_baggage_window import baggage_day;print(json.dumps(baggage_day()))'],{cwd:repo,encoding:'utf8'}));
  cxKeDay.serviceDate='2057-08-13';cxKeDay.workRules.baggageWindow.remainderToleranceMinutes=15;
  const cxKeState=await command('B','import-complete-configuration',{day:cxKeDay,workRules:cxKeDay.workRules,handoffPreferences:[],rotationEnabled:true,rotationMinutes:60});
  for(const role of cxKeState.configuration.roleConfigs)if(role.roleName!=='大件行李拦截')await command('B','save-role',{...role,requiredCount:0});
  baggagePlan=await solveCoverage();assert.equal(baggagePlan.valid,true,JSON.stringify(baggagePlan.issues));
  assert.equal(baggagePlan.baggageCoverage.requiredPeople,3,'CX937/KE0166 08:30–11:15，宽限15分钟需要3人');
  assert.equal(new Set(baggagePlan.draft.map(a=>a.memberId)).size,3);
  assert.ok(!requests.some(url=>new URL(url,base).pathname.startsWith("/api/")),"不得请求任何Python后台");
  // The real WASM solver consumes the same soft order after shortage, not just
  // the page or native solver. Reverse it, then retain a manual counterexample.
  const orderFixture=JSON.parse(execFileSync(python,['-c',`import json
from tests.test_coverage_order import configured
from gate_schedule.domain import empty_work_rules
d=configured()
d['day']['serviceDate']='2061-03-02'
d['workRules']=empty_work_rules()
d['handoffPreferences']=[]
print(json.dumps(d))`],{cwd:repo,encoding:'utf8'}));
  let orderState=await command('B','import-complete-configuration',orderFixture);
  for(const role of orderState.configuration.roleConfigs)if(!orderFixture.day.roleNames.includes(role.roleName))await command('B','save-role',{...role,requiredCount:0});
  let orderedPlan=await solveCoverage();
  const assignedRoles=plan=>new Set(plan.schedule.tasks.filter(t=>plan.draft.some(a=>a.taskId===t.id)).map(t=>t.roleId));
  assert.deepEqual(assignedRoles(orderedPlan),new Set(['登机主验']),'WASM缺人时先保同档前岗');
  orderState=await state('B');
  await command('B','update-balance-settings',{balanceSettings:{...orderState.balanceSettings,coverageRoleOrder:['秩序维护','登机主验']}});
  orderedPlan=await solveCoverage();
  assert.deepEqual(assignedRoles(orderedPlan),new Set(['秩序维护']),'WASM反转顺序真正改变保岗');
  const manualOrder=orderedPlan.schedule.tasks.filter(t=>t.roleId==='登机主验').map(t=>({taskId:t.id,memberId:orderedPlan.draft[0].memberId}));
  await command('B','assign',{assignments:manualOrder},orderedPlan.id);
  let orderHints=(await state('B')).plans[orderedPlan.id].issues.filter(i=>i.code==='coverage_order_unmet');
  assert.ok(orderHints.length&&orderHints.every(i=>i.severity==='warning'),'WASM人工反转显示软提示');
  await command('B','adopt',{},orderedPlan.id);
  await reload();
  assert.deepEqual((await state('B')).plans[orderedPlan.id].formalIssues.filter(i=>i.code==='coverage_order_unmet'),orderHints,'偏好未满足可归档并在刷新后保留');
  // Dedicated roles remain editable for cross-flight rules. Same-flight
  // restrictions are enforced by the domain, not by removing role choices.
  assert.ok((await state('B')).flowRuleRoles.includes('登机主验'));
  const oldFlow=[{flightNo:'*',arrivalRole:'登机主验',boardingRoles:['秩序维护'],enabled:true}];
  await command('B','save-handoff-preferences',{preferences:oldFlow});
  await reload();await value('#active-group','B');await click('[data-view="rules"]');
  assert.ok(await evaluate('[...document.querySelectorAll("[data-flow-source] option")].some(o=>o.value==="登机主验"&&o.selected&&!o.disabled)'));
  assert.deepEqual((await state('B')).handoffPreferences,oldFlow);
  await command('B','save-role',{roleName:'秩序维护',exclusiveSameFlight:false});
  await reload();await value('#active-group','B');await click('[data-view="rules"]');
  assert.ok(await evaluate('[...document.querySelectorAll("[data-flow-target] option")].some(o=>o.value==="秩序维护"&&!o.disabled)'));
  assert.ok((await state('B')).flowRuleRoles.includes('登机主验'));
  assert.deepEqual((await state('B')).plans[orderedPlan.id].formalIssues.filter(i=>i.code==='coverage_order_unmet'),orderHints);
  // Run the corrected dedicated contract in the shipped WASM solver too.
  const dedicatedInput=dedicatedCrossConfiguration();
  const dedicatedImported=await command('B','import-complete-configuration',dedicatedInput);
  const dedicatedId=dedicatedImported.selectedPlanId;
  const dedicatedStart=await evaluate(`fetch('/api/groups/B/workspace/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(${JSON.stringify({expectedRevision:dedicatedImported.revision,action:'solve',planId:dedicatedId})})}).then(r=>r.json())`);
  const dedicatedDone=await until(async()=>{const r=await evaluate(`fetch('/api/groups/B/workspace/runs/${dedicatedStart.id}').then(r=>r.json())`);return r.status!=='running'?r:false},'WASM专岗跨班接续');
  assert.equal(dedicatedDone.status,'completed',JSON.stringify(dedicatedDone));
  const dedicatedState=await state('B'),dedicatedPlan=dedicatedState.plans[dedicatedId];
  assert.equal(dedicatedPlan.valid,true,JSON.stringify(dedicatedPlan.issues));
  const dedicatedWorker=dedicatedState.people.find(p=>p.name==='跨班验证员').id;
  assert.equal(dedicatedPlan.draft.filter(a=>a.memberId===dedicatedWorker).length,2);
  assert.ok(dedicatedPlan.schedule.tasks.some(t=>t.roleId==='入境申报'&&t.exclusiveSameFlight));
  await reload();
  assert.equal((await state('B')).plans[dedicatedId].draft.filter(a=>a.memberId===dedicatedWorker).length,2);
  assert.deepEqual(errors,[],"网页不得发生未处理错误");
  await writeFile(join(repo,".tmp/browser-local-result.json"),JSON.stringify({requests,errors,base,productionCopy,passed:true},null,2));
});
