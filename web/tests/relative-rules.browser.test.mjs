import assert from "node:assert/strict";
import { spawn, execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdtemp, readFile, writeFile, mkdir, rm, readdir } from "node:fs/promises";
import { join, resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { createServer } from "node:net";

const repo = fileURLToPath(new URL("../../", import.meta.url));
const python = process.env.GATE_TEST_PYTHON ?? join(repo, ".venv-local/Scripts/python.exe");
const chrome = [process.env.GATE_TEST_BROWSER, "C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"].find(p => p && existsSync(p));
const delay = ms => new Promise(r => setTimeout(r, ms));
async function until(fn, label) {
  const end = Date.now() + 15000;
  while (Date.now() < end) { const value = await fn(); if (value) return value; await delay(30); }
  throw new Error(`等待超时：${label}`);
}

// This flow includes all configuration transfers, server restarts and fourteen
// browser subtests. Keep each wait bounded while allowing the complete suite to finish.
test("真实后端 Chrome：自由流程排序、系列保存、实际求解和重启", { timeout: 180000 }, async t => {
  assert.ok(chrome, "需要 Chrome/Edge");
  assert.ok(existsSync(python), "先按 README 创建 .venv-local，或设置 GATE_TEST_PYTHON");
  const temporaryRoot = join(repo, ".tmp");
  await mkdir(temporaryRoot, { recursive: true });
  const directory = await mkdtemp(join(temporaryRoot, "gate-workbench-real-"));
  let server, browser, ws, baseUrl, sessionId;
  let nextId = 0;
  const pending = new Map();
  const runtimeErrors = [];
  let interceptedDrag = null;
  let acceptDialogs = true;
  let promptReply;
  const call = (method, params = {}) => new Promise((resolveCall, reject) => {
    const id = ++nextId;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`Chrome 调用超时：${method}`)); }, 15000);
    pending.set(id, { resolveCall: value => { clearTimeout(timer); resolveCall(value); }, reject: error => { clearTimeout(timer); reject(error); } });
    ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
  const evaluate = async expression => {
    const result = await call("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const click = async (selector, saveOnLeave = true) => { await evaluate(`(() => { const el=document.querySelector(${JSON.stringify(selector)}); if(!el)throw new Error('找不到控件'); const ancestors=[]; for(let p=el.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS'&&!p.open)ancestors.unshift(p); for(const p of ancestors)p.querySelector(':scope > summary').click(); el.click(); })()`);
    if (saveOnLeave && selector.includes('data-view') && await evaluate('!!document.querySelector("#leave-save")')) {
      await evaluate('document.querySelector("#leave-save").click()');
      await until(()=>evaluate('!document.querySelector("#leave-save")'),'保存后离开');
    }
  };
  const value = (selector, inputValue, checked = false) => evaluate(`(() => { const el=document.querySelector(${JSON.stringify(selector)}); el.${checked ? "checked" : "value"}=${JSON.stringify(inputValue)}; el.dispatchEvent(new Event('change',{bubbles:true})); })()`);
  const state = async () => (await fetch(`${baseUrl}/api/workspace`)).json();
  const current = async () => { const s = await state(); return s.plans[s.selectedPlanId]; };
  const waitElement = async selector => {
    try { return await until(() => evaluate(`!!document.querySelector(${JSON.stringify(selector)})`), selector); }
    catch (error) { throw new Error(`${error.message}; 页面提示：${await evaluate("document.querySelector('.app-message')?.textContent ?? document.body.textContent.slice(0,1500)")}`); }
  };
  const mutate = async action => {
    const revision = (await state()).revision;
    await action();
    try { await until(async () => (await state()).revision > revision, "后端事务提交"); }
    catch(error) { throw new Error(`${error.message}; ${await evaluate("document.querySelector('.run-error')?.textContent || document.querySelector('.dialog-error')?.textContent || document.querySelector('.app-message')?.textContent || document.body.textContent.slice(-2000)")}`); }
    await until(() => evaluate(`!document.querySelector('.schedule-progress[data-run-status="running"]') && ![...document.querySelectorAll('#app button')].every(b=>b.disabled)`), "页面完成更新");
  };
  const rerun = async () => { await until(()=>evaluate("!!document.querySelector('#solve-draft') && !document.querySelector('#solve-draft').disabled"),'排班入口就绪'); await click('#solve-draft'); await waitElement('#confirm-prepare'); await mutate(() => click('#confirm-prepare')); };
  const reloadPage = async () => { await evaluate('window.__reloadMarker=true'); await call('Page.reload'); await until(()=>evaluate('!window.__reloadMarker && !!document.querySelector(".wb-matrix")'),'新页面矩阵加载'); };
  const typeCell = (selector,text) => evaluate(`(() => {const el=document.querySelector(${JSON.stringify(selector)});el.focus();el.textContent=${JSON.stringify(text)};el.dispatchEvent(new InputEvent('input',{bubbles:true}));el.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));})()`);
  const drag = (source,target) => evaluate(`(() => {const from=document.querySelector(${JSON.stringify(source)}),to=document.querySelector(${JSON.stringify(target)});if(!from||!to)throw Error('拖动元素不存在');const dt=new DataTransfer();from.dispatchEvent(new DragEvent('dragstart',{bubbles:true,dataTransfer:dt}));to.dispatchEvent(new DragEvent('dragover',{bubbles:true,dataTransfer:dt,cancelable:true}));to.dispatchEvent(new DragEvent('drop',{bubbles:true,dataTransfer:dt,cancelable:true}));from.dispatchEvent(new DragEvent('dragend',{bubbles:true,dataTransfer:dt}));})()`);
  const launchServer = async () => {
    let output = "", errors = "";
    const environment = { ...process.env, PYTHONIOENCODING: "utf-8", TEMP: temporaryRoot, TMP: temporaryRoot };
    if (process.env.GATE_TEST_EXECUTABLE) {
      const listener = createServer();
      await new Promise((resolvePort,reject)=>{listener.once('error',reject);listener.listen(0,'127.0.0.1',resolvePort);});
      const port = listener.address().port;
      await new Promise(resolvePort=>listener.close(resolvePort));
      baseUrl = `http://127.0.0.1:${port}`;
      environment.GATE_SCHEDULE_DATA = directory;
      server = spawn(process.env.GATE_TEST_EXECUTABLE, ['--no-browser','--port',String(port)], {cwd:directory,windowsHide:true,env:environment});
    } else {
      server = spawn(python, ["-B", join(repo, "tests/serve_workbench.py"), join(directory, "state.sqlite3")], { cwd: repo, windowsHide: true, env: environment });
    }
    server.stdout.on("data", b => { output += b; }); server.stderr.on("data", b => { errors += b; });
    if (!process.env.GATE_TEST_EXECUTABLE) baseUrl = await until(() => output.match(/http:\/\/127\.0\.0\.1:\d+/)?.[0], "后端监听端口");
    await until(async () => { try { return (await fetch(`${baseUrl}/api/workspace`)).ok; } catch { if (server.exitCode !== null) throw new Error(errors); return false; } }, "后端启动");
  };
  const stopServer = async () => { if (!server || server.exitCode !== null || server.signalCode !== null) return; const done = new Promise(r => server.once("exit", r)); server.kill(); await Promise.race([done,delay(5000)]); };
  t.after(async () => {
    ws?.close();
    if (browser && browser.exitCode === null && browser.signalCode === null) { const done = new Promise(r => browser.once("exit", r)); browser.kill(); await Promise.race([done, delay(3000)]); }
    if (server && server.exitCode === null && server.signalCode === null) await stopServer();
    assert.equal(dirname(resolve(directory)), resolve(temporaryRoot));
    await rm(directory, { recursive: true, force: true, maxRetries: 8, retryDelay: 250 });
  });
  await launchServer();
  const profile = join(directory, "chrome");
  browser = spawn(chrome, ["--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "--no-first-run", "--no-default-browser-check", "--disable-background-networking", "about:blank"], { windowsHide: true, stdio: "ignore" });
  const portFile = await until(async () => { try { return await readFile(join(profile, "DevToolsActivePort"), "utf8"); } catch { return false; } }, "Chrome 调试端口");
  const [port, path] = portFile.trim().split(/\r?\n/);
  ws = new WebSocket(`ws://127.0.0.1:${port}${path}`);
  await new Promise(r => ws.addEventListener("open", r, { once: true }));
  ws.addEventListener("message", event => {
    const m = JSON.parse(event.data);
    if (m.method === 'Input.dragIntercepted') interceptedDrag = m.params.data;
    if (m.method === "Page.javascriptDialogOpening") void call("Page.handleJavaScriptDialog", { accept: acceptDialogs, ...(m.params.type==='prompt'?{promptText:promptReply??(m.params.defaultPrompt||'CX*')}:{}) });
    if (m.method === "Runtime.exceptionThrown") runtimeErrors.push(m.params.exceptionDetails);
    const p = pending.get(m.id); if (!p) return;
    pending.delete(m.id); m.error ? p.reject(new Error(JSON.stringify(m.error))) : p.resolveCall(m.result);
  });
  await call("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: join(directory, "downloads") });
  const target = await call("Target.createTarget", { url: "about:blank" });
  sessionId = (await call("Target.attachToTarget", { targetId: target.targetId, flatten: true })).sessionId;
  await call("Page.enable"); await call("Runtime.enable");
  await call("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await call("Page.navigate", { url: baseUrl });
  await waitElement('[data-view="config"]');
  const command=async(action,payload={},planId=null)=>{
    const s=await state();
    const response=await fetch(`${baseUrl}/api/workspace/command`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expectedRevision:s.revision,action,payload,planId})});
    const result=await response.json();assert.equal(response.ok,true,JSON.stringify(result));return result;
  };
  const fixture=JSON.parse(execFileSync(python,['-c',`import json
from tests.test_relative_scheduling import relative_day
day=relative_day()
day['roleConfigs'][0].pop('relativeTiming')
print(json.dumps(day))`],{cwd:repo,encoding:'utf8'}));
  fixture.flights.forEach(f=>{f.flightNo='CX937';f.departureTime=null});
  fixture.cells.forEach(c=>c.flightNo='CX937');
  let s=await command('import-complete-configuration',{day:fixture,rotationMinutes:60,rotationEnabled:true});
  const pid=s.selectedPlanId;
  for(const role of s.configuration.roleConfigs)if(!fixture.roleNames.includes(role.roleName))await command('save-role',{...role,requiredCount:0});
  await command('apply-config',{},pid);
  await evaluate('window.__phase2Reload=true');await call('Page.reload');await until(()=>evaluate('!window.__phase2Reload && !!document.querySelector("[data-view=rules]")'),'新页面规则入口');
  await click('[data-view="rules"]');
  assert.equal(await evaluate('document.querySelector("#relative-time-rules")'),null);
  assert.match(await evaluate('document.querySelector("#handoff-preference-rules > summary").textContent'),/流动岗位管理/);
  await click('#add-flow-exception');
  await value('#flow-exceptions [data-flow-rule]:last-of-type [data-flow-source]','大件行李拦截');
  while(await evaluate('!!document.querySelector("#flow-exceptions [data-flow-rule]:last-of-type [data-flow-remove-target]")'))await click('#flow-exceptions [data-flow-rule]:last-of-type [data-flow-remove-target]');
  await click('#flow-exceptions [data-flow-rule]:last-of-type [data-flow-add-target]');
  await value('#flow-exceptions [data-flow-rule]:last-of-type [data-flow-target]','登机核验');
  assert.deepEqual(await evaluate('[...document.querySelector("#flow-exceptions [data-flow-rule]:last-of-type [data-flow-flight-scope]").options].map(o=>o.value)'),['same-flight','later-overlap']);
  assert.equal(await evaluate('document.querySelector("#flow-exceptions [data-flow-rule]:last-of-type [data-flow-flight-scope]").value'),'same-flight');
  await value('#flow-exceptions [data-flow-rule]:last-of-type [data-flow-flight-scope]','later-overlap');
  await click('#flow-exceptions [data-flow-rule]:last-of-type [data-flow-add-target]');
  await value('#flow-exceptions [data-flow-rule]:last-of-type [data-flow-target-row="1"] [data-flow-target]','接机1');
  await click('#flow-exceptions [data-flow-rule]:last-of-type [data-flow-target-row="1"] [data-flow-up]');
  await value('#flow-exceptions [data-flow-rule]:last-of-type [data-flow-scope]','CX*');
  await value('#flow-exceptions [data-flow-rule]:last-of-type [data-flow-enabled]',true,true);
  await mutate(()=>click('#save-rules'));
  s=await state();
  assert.ok(s.handoffPreferences.some(p=>p.flightNo==='CX*'&&p.arrivalRole==='大件行李拦截'&&p.boardingRoles.join(',')==='接机1,登机核验'));
  assert.ok(s.handoffPreferences.some(p=>p.flightNo==='CX*'&&p.targetFlightScopes?.['登机核验']==='later-overlap'&&p.targetFlightScopes?.['接机1']==='same-flight'));
  await click('[data-view="flights"]');
  await value('[data-flight][data-field="departureTime"]','00:20');
  await value('[data-flight][data-field="arrivalTime"]','前日23:10');
  await mutate(()=>click('#save-preparation'));
  await click('[data-view="schedule"]');
  await click('#solve-draft');await waitElement('#confirm-prepare');
  await click('#prepare-latest-config');
  await mutate(()=>click('#confirm-prepare'));
  let p=await current();
  assert.equal(p.valid,true,JSON.stringify(p.issues));
  assert.equal(p.schedule.tasks[0].start,'前日23:10');
  assert.equal(p.schedule.tasks[0].end,'00:20');
  assert.equal(p.day.flights[0].arrivalTime,'前日23:10');
  await stopServer();await launchServer();await evaluate('window.__phase2Reload=true');await call('Page.navigate',{url:baseUrl});await until(()=>evaluate('!window.__phase2Reload && !!document.querySelector("[data-view=rules]")'),'重启后规则入口');
  await click('[data-view="rules"]');
  assert.ok((await state()).handoffPreferences.some(p=>p.flightNo==='CX*'&&p.boardingRoles[0]==='接机1'));
  assert.equal((await current()).schedule.tasks[0].start,'前日23:10');
  await t.test('岗位分人组：自由岗位、启停、特例、连续草稿、保存与重启',async()=>{
    const base='[data-work-editor="base"]';
    const card=`${base} [data-separation-group="0"]`;
    await click(`${base} [data-group-add]`);
    assert.equal(await evaluate(`document.querySelector('${card} [data-group-enabled]').disabled`),true);
    await value(`${card} [data-group-name]`,'接机登机分人测试');
    await click(`${card} [data-group-add-role]`);
    await value(`${card} [data-group-role]`,'接机1');
    await click(`${card} [data-group-add-role]`);
    await value(`${card} [data-group-role-row="1"] [data-group-role]`,'登机核验');
    assert.equal(await evaluate(`document.querySelector('${card} [data-group-enabled]').checked`),true,'选满两岗默认启用，不能要求用户补一个隐藏步骤');
    // Prompt accepts its default CX*, preserving the group's stable identity.
    await click(`${card} [data-group-exception]`);
    const exception=`${base} [data-separation-group="1"]`;
    await waitElement(exception);
    await value(`${exception} [data-group-enabled]`,false,true);
    await value(`${exception} [data-group-scope]`,'CX937');
    await click(`${exception} [data-group-remove-role="1"]`);
    await click(`${exception} [data-group-add-role]`);
    await value(`${exception} [data-group-role-row="1"] [data-group-role]`,'登机核验');
    await value(`${exception} [data-group-enabled]`,false,true);
    await mutate(()=>click('#save-rules'));
    let saved=(await state()).workRules.separationGroups;
    assert.equal(saved.length,2);
    assert.equal(saved[0].name,'接机登机分人测试');
    assert.deepEqual(saved[0].roles,['接机1','登机核验']);
    assert.equal(saved[0].id,saved[1].id);
    assert.equal(saved[0].enabled,true);assert.equal(saved[1].enabled,false);
    assert.equal(saved[1].flightNo,'CX937');
    const expected=structuredClone(saved);
    await stopServer();await launchServer();await evaluate('window.__groupReload=true');await call('Page.navigate',{url:baseUrl});
    await until(()=>evaluate('!window.__groupReload && !!document.querySelector("[data-view=rules]")'),'分人组重启');
    await click('[data-view="rules"]');
    assert.deepEqual((await state()).workRules.separationGroups,expected);
    await click(`${exception} [data-group-delete]`);
    await mutate(()=>click('#save-rules'));
    saved=(await state()).workRules.separationGroups;
    assert.equal(saved.length,1);assert.equal(saved[0].id,expected[0].id);
    await click('#day-work-override');
    await click('[data-work-editor="day"] [data-group-add]');
    await value('[data-work-editor="day"] [data-separation-group="1"] [data-group-name]','当天独立草稿');
    await mutate(()=>click('#save-rules'));
    assert.equal((await current()).day.workRules.separationGroups[1].name,'当天独立草稿');
    assert.equal((await state()).workRules.separationGroups.length,1);
    await click('#day-work-override');await mutate(()=>click('#save-rules'));
    for(const width of [1440,390]){
      await call('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:false});
      await evaluate(`document.querySelector('${card}').scrollIntoView({block:'center'})`);
      assert.ok(await evaluate('document.documentElement.scrollWidth<=innerWidth+1'));
      assert.ok(await evaluate(`[...document.querySelectorAll('${card} input,${card} select,${card} button')].every(el=>{const r=el.getBoundingClientRect();return r.width>0&&r.left>=0&&r.right<=innerWidth+1})`));
      await writeFile(join(temporaryRoot,`separation-groups-${width}.png`),Buffer.from((await call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    }
    // Conversion is explicit and must never overwrite a newer unsaved draft.
    await click(`${base} [data-work-add-pair]`);
    await value(`${base} [data-work-pair="0"] [data-work-field="firstRole"]`,'接机1');
    await value(`${base} [data-work-pair="0"] [data-work-field="secondRole"]`,'接机2');
    await mutate(()=>click('#save-rules'));
    await value(`${card} [data-group-name]`,'已改名的分人组');
    assert.equal(await evaluate('document.querySelector("#convert-work-groups").disabled'),true);
    await mutate(()=>click('#save-rules'));
    await click('#convert-work-groups');await mutate(()=>click('#save-rules'));
    const converted=(await state()).workRules;
    assert.equal(converted.pairs.length,1);assert.equal(converted.pairs[0].mode,'sequence');assert.equal(converted.separationGroups.length,2);
    assert.equal(converted.separationGroups[0].name,'已改名的分人组');
  });
  await t.test('分人集合完整操作：不补勾选→保存→重新排班，禁止衔接硬限制与手工反向',async()=>{
    const day=structuredClone(fixture);day.serviceDate='2057-08-09';
    day.flights=[{id:'F',flightNo:'CX937',windowStart:'08:00',windowEnd:'11:00'}];
    day.roleNames=['接机1','接机2','登机1','登机2'];
    day.roleConfigs=day.roleNames.map(roleName=>({roleName,requiredCount:1,workMode:roleName.startsWith('接机')?'arrival':'boarding',concurrent:true}));
    day.memberConfigs=day.roster.map(memberName=>({memberName,eligibleRoleNames:day.roleNames}));
    day.cells=day.roleNames.map(roleName=>({id:roleName,roleName,flightId:'F',flightNo:'CX937',rawValue:'',internalNames:[],externalLabels:[],semantics:'empty',fixedMemberNames:[]}));
    const empty={pairs:[],baggage:[],separationGroups:[],forbiddenHandoffs:[]};
    const prefs=day.roleNames.slice(0,-1).map((arrivalRole,i)=>({flightNo:'*',arrivalRole,boardingRoles:day.roleNames.slice(i+1),enabled:true,ordered:true}));
    let currentState=await command('import-complete-configuration',{day,workRules:empty,handoffPreferences:prefs,rotationEnabled:true,rotationMinutes:60});
    const testPid=currentState.selectedPlanId;
    for(const r of currentState.configuration.roleConfigs)if(!day.roleNames.includes(r.roleName))await command('save-role',{...r,requiredCount:0});
    await evaluate('window.__guardReload=true');await call('Page.reload');await until(()=>evaluate('!window.__guardReload&&!!document.querySelector("[data-view=rules]")'),'新场景');
    await click('[data-view="rules"]');
    const base='[data-work-editor="base"]';
    for(const [i,roles] of [['接机1','接机2'],['登机1','登机2']].entries()){
      await click(`${base} [data-group-add]`);
      const card=`${base} [data-separation-group="${i}"]`;
      for(const [j,role] of roles.entries()){
        await click(`${card} [data-group-add-role]`);await value(`${card} [data-group-role-row="${j}"] [data-group-role]`,role);
      }
      assert.equal(await evaluate(`document.querySelector('${card} [data-group-enabled]').checked`),true);
    }
    // Explicit off stays off when adding a third role; the activation button
    // repairs the screenshot's existing stopped group without guessing intent.
    const first=`${base} [data-separation-group="0"]`;
    await value(`${first} [data-group-enabled]`,false,true);
    assert.match(await evaluate(`document.querySelector('${first} [data-group-state]').textContent`),/已停用/);
    await click(`${first} [data-group-add-role]`);
    assert.equal(await evaluate(`document.querySelector('${first} [data-group-enabled]').checked`),false);
    await click(`${first} [data-group-remove-role="2"]`);await click(`${first} [data-group-activate]`);
    await click(`${base} [data-forbidden-add-rule]`);
    const blocked=`${base} [data-forbidden-rule="0"]`;
    await value(`${blocked} [data-forbidden-source]`,'接机1');await click(`${blocked} [data-forbidden-add-target]`);
    await value(`${blocked} [data-forbidden-target]`,'登机1');
    assert.equal(await evaluate(`document.querySelector('${blocked} [data-forbidden-flight]').value`),'same-flight');
    assert.equal(await evaluate(`document.querySelector('${blocked} [data-forbidden-enabled]').checked`),true);
    await click(`${blocked} [data-forbidden-exception]`);const exception=`${base} [data-forbidden-rule="1"]`;
    await value(`${exception} [data-forbidden-enabled]`,false,true);
    await mutate(()=>click('#save-rules'));
    assert.equal((await state()).workRules.forbiddenHandoffs.length,2);
    await click(`${exception} [data-forbidden-delete]`);await mutate(()=>click('#save-rules'));
    await click('[data-view="schedule"]');await rerun();
    let result=(await state()).plans[testPid];assert.equal(result.valid,true,JSON.stringify(result.issues));assert.equal(result.draft.length,4);
    const owners=Object.fromEntries(result.draft.map(a=>[a.taskId,a.memberId]));
    assert.notEqual(owners['imported-接机1'],owners['imported-接机2']);assert.notEqual(owners['imported-登机1'],owners['imported-登机2']);
    assert.notEqual(owners['imported-接机1'],owners['imported-登机1']);
    result=(await command('edit-slot',{taskId:'imported-登机1',slot:0,memberId:owners['imported-接机1']},testPid)).plans[testPid];
    assert.ok(result.issues.some(i=>i.code==='forbidden_handoff'));
    await reloadPage();await click('[data-view="schedule"]');
    assert.ok(await evaluate('document.querySelectorAll(".wb-matrix .schedule-cell-conflict[title*=禁止衔接]").length>=2'),'来源和目标格均标红说明禁止衔接');
    await command('undo-edit',{},testPid);
    // A separate same-group violation must still be caught after manual edits.
    result=(await command('edit-slot',{taskId:'imported-接机2',slot:0,memberId:owners['imported-接机1']},testPid)).plans[testPid];
    assert.ok(result.issues.some(i=>i.code==='role_separation'));await command('undo-edit',{},testPid);
    await evaluate('window.__guardReload=true');await call('Page.reload');await until(()=>evaluate('!window.__guardReload&&!!document.querySelector("[data-view=rules]")'),'恢复规则页');
    await click('[data-view="rules"]');
    await click('#day-work-override');
    assert.match(await evaluate('document.querySelector("#work-relation-rules").textContent'),/当天独立规则已开启/);
    await click('[data-work-editor="day"] [data-forbidden-rule="0"] [data-forbidden-remove-target]');
    await mutate(()=>click('#save-rules'));
    assert.equal((await current()).day.workRules.forbiddenHandoffs[0].enabled,false);
    assert.equal((await state()).workRules.forbiddenHandoffs[0].enabled,true);
    await click('#day-work-override');await mutate(()=>click('#save-rules'));
    for(const width of [1440,390]){
      await call('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:false});
      await evaluate(`document.querySelector('${blocked}').scrollIntoView({block:'center'})`);
      assert.ok(await evaluate('document.documentElement.scrollWidth<=innerWidth+1'));
      assert.ok(await evaluate(`[...document.querySelectorAll('${blocked} input,${blocked} select,${blocked} button')].every(el=>{const r=el.getBoundingClientRect();return r.width>0&&r.left>=0&&r.right<=innerWidth+1})`));
      await writeFile(join(temporaryRoot,`forbidden-handoffs-${width}.png`),Buffer.from((await call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    }
  });
  await t.test('大件窗口与轮次：界面配置、三类规则、实际接续、手工反向和重启',async()=>{
    const day=JSON.parse(execFileSync(python,['-c','import json;from tests.test_baggage_flow_roles import turn_day;print(json.dumps(turn_day()))'],{cwd:repo,encoding:'utf8'}));
    day.serviceDate='2057-08-11';
    const empty={pairs:[],baggage:[],baggageWindow:null,separationGroups:[],forbiddenHandoffs:[]};
    let source=await command('import-complete-configuration',{day,workRules:empty,handoffPreferences:[],rotationEnabled:false,rotationMinutes:15});
    for(const role of source.configuration.roleConfigs)if(!day.roleNames.includes(role.roleName))await command('save-role',{...role,requiredCount:0});
    await reloadPage();await click('[data-view="rules"]');
    const base='[data-work-editor="base"]';
    await click(`${base} [data-baggage-window-enabled]`);
    assert.equal(await evaluate(`document.querySelector('${base} [data-baggage-window-field="startBeforeMinutes"]').value`),'180');
    assert.equal(await evaluate(`document.querySelector('${base} [data-baggage-window-field="remainderToleranceMinutes"]').value`),'15');
    await value(`${base} [data-baggage-window-field="rotationMinutes"]`,'90');
    await value(`${base} [data-baggage-window-field="rotationMinutes"]`,'60');
    for(const [index,target] of ['入境申报','登机1','登机2'].entries()){
      await click('#add-flow-rule');
      const row=`[data-flow-rule="${index}"]`;
      const choices=await evaluate(`[...document.querySelector('${row} [data-flow-source]').options].map(o=>o.value)`);
      assert.ok(['大件1','大件2','大件3'].every(r=>choices.includes(r)));
      await value(`${row} [data-flow-source]`,`大件${index+1}`);
      await click(`${row} [data-flow-add-target]`);await value(`${row} [data-flow-target]`,target);
      assert.ok((await evaluate(`[...document.querySelector('${row} [data-flow-target]').options].map(o=>o.value)`)).includes(`大件${index===0?2:1}`));
    }
    for(const [index,roles] of [['入境申报','登机1','登机2'],['大件2','入境申报']].entries()){
      await click(`${base} [data-group-add]`);
      const card=`${base} [data-separation-group="${index}"]`;
      for(const [position,role] of roles.entries()){
        await click(`${card} [data-group-add-role]`);await value(`${card} [data-group-role-row="${position}"] [data-group-role]`,role);
      }
    }
    await click(`${base} [data-forbidden-add-rule]`);
    await value(`${base} [data-forbidden-rule="0"] [data-forbidden-source]`,'大件1');
    await click(`${base} [data-forbidden-rule="0"] [data-forbidden-add-target]`);
    await value(`${base} [data-forbidden-rule="0"] [data-forbidden-target]`,'登机1');
    await mutate(()=>click('#save-rules'));
    source=await state();assert.deepEqual(source.workRules.baggageWindow,{startBeforeMinutes:180,endBeforeMinutes:60,rotationMinutes:60,remainderToleranceMinutes:15});
    assert.match(await evaluate(`document.querySelector('${base} .baggage-window-preview').textContent`),/3 名不同人员/);
    assert.match(await evaluate(`document.querySelector('${base} .baggage-window-preview').textContent`),/大件3/);
    await click('[data-view="schedule"]');await rerun();
    const plan=await current();assert.equal(plan.valid,true,JSON.stringify(plan.issues));
    const baggage=plan.schedule.tasks.filter(t=>t.roleId==='大件行李拦截');
    assert.equal(baggage.length,3);assert.deepEqual(baggage.map(t=>[t.start,t.end]),[['08:30','09:30'],['09:30','10:30'],['10:30','11:45']]);
    const owner=id=>plan.draft.find(a=>a.taskId===id)?.memberId;
    assert.equal(new Set(baggage.map(t=>owner(t.id))).size,3);
    for(const [index,role] of ['入境申报','登机1','登机2'].entries())assert.equal(owner(baggage[index].id),owner(plan.schedule.tasks.find(t=>t.roleId===role).id));
    await command('edit-slot',{taskId:baggage[1].id,slot:0,memberId:owner(baggage[0].id)},plan.id);
    assert.ok((await current()).issues.some(i=>i.code==='baggage_rotation_person'));
    await command('undo-edit',{},plan.id);
    await reloadPage();
    // Recalculate a populated draft with fewer turns, then restore the rule.
    for(const [minutes,count] of [[120,2],[60,3]]){
      await click('[data-view="rules"]');await value(`${base} [data-baggage-window-field="rotationMinutes"]`,String(minutes));
      await mutate(()=>click('#save-rules'));await click('[data-view="schedule"]');await rerun();
      const updated=await current();assert.equal(updated.valid,true,JSON.stringify(updated.issues));
      assert.equal(updated.baggageCoverage.requiredPeople,count,'已有草稿减少轮次仍能保存');
    }
    await stopServer();await launchServer();await call('Page.navigate',{url:baseUrl});await until(()=>evaluate('!!document.querySelector("[data-view=rules]")'),'大件规则重启');
    assert.deepEqual((await state()).workRules,source.workRules);
    assert.equal((await current()).baggageCoverage.requiredPeople,3);
    await click('[data-view="rules"]');await evaluate('document.querySelector("#handoff-preference-rules").open=true;document.querySelector("#work-relation-rules").open=true');
    await value(`${base} [data-baggage-window-field="remainderToleranceMinutes"]`,'0');
    await mutate(()=>click('#save-rules'));
    assert.match(await evaluate(`document.querySelector('${base} .baggage-window-preview').textContent`),/4 名不同人员/);
    await value(`${base} [data-baggage-window-field="remainderToleranceMinutes"]`,'15');
    await mutate(()=>click('#save-rules'));
    assert.match(await evaluate(`document.querySelector('${base} .baggage-window-preview').textContent`),/3 名不同人员/);
    for(const width of [1440,390]){
      await call('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:false});
      await evaluate(`document.querySelector('${base} .baggage-window').scrollIntoView({block:'center'})`);
      assert.ok(await evaluate('document.documentElement.scrollWidth<=innerWidth+1'));
      assert.ok(await evaluate(`[...document.querySelectorAll('${base} .baggage-window input')].every(el=>{const r=el.getBoundingClientRect();return r.width>0&&r.left>=0&&r.right<=innerWidth+1})`));
      await writeFile(join(temporaryRoot,`baggage-window-${width}.png`),Buffer.from((await call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    }
  });
  await t.test('通用接续与手动特例：未配置航班继承、覆盖删除、原历史与迁移、四色宽窄屏',async()=>{
    const day=structuredClone(fixture);day.serviceDate='2057-08-18';
    day.roleNames=['接机1','接机2','登机主验','登机核验','登机人证核对/队伍秩序维护'];
    day.flights=[{id:'F',flightNo:'CX937',windowStart:'10:00',windowEnd:'11:20'}];
    day.roleConfigs=day.roleNames.map(roleName=>({roleName,requiredCount:1,workMode:roleName.startsWith('接机')?'arrival':'boarding',concurrent:false}));
    day.roster=['甲','乙','丙'];day.memberConfigs=day.roster.map(memberName=>({memberName,eligibleRoleNames:day.roleNames}));
    day.memberAvailability=day.roster.map(memberName=>({memberName,participating:true}));
    day.cells=day.roleNames.map((roleName,i)=>({id:'flow'+i,flightId:'F',flightNo:'CX937',roleName,rawValue:'',internalNames:[],externalLabels:[],semantics:'empty',fixedMemberNames:i<2?[day.roster[i]]:[]}));
    const shared={flightNo:'*',arrivalRole:'接机2',boardingRoles:['登机核验','登机主验'],enabled:true,ordered:true,targetFlightScopes:{'登机核验':'same-flight','登机主验':'same-flight'}};
    const empty={pairs:[],baggage:[],baggageWindow:null,separationGroups:[],forbiddenHandoffs:[]};
    // These explicit fixture requirements make the preferred target observable:
    // without them a concurrent worker may legally serve both boarding roles.
    empty.separationGroups.push({id:'boarding',name:'登机岗位分人',flightNo:'*',roles:day.roleNames.slice(2),enabled:true});
    let source=await command('import-complete-configuration',{day,workRules:empty,handoffPreferences:[shared],rotationEnabled:false,rotationMinutes:60});
    const planId=source.selectedPlanId;
    const history=Object.fromEntries(Object.entries(source.plans).filter(([id])=>id!==planId).map(([id,p])=>[id,structuredClone(p.adopted)]));
    for(const role of source.configuration.roleConfigs)if(!day.roleNames.includes(role.roleName))await command('save-role',{...role,requiredCount:0});
    await reloadPage();await click('[data-view="rules"]');
    await evaluate('document.querySelector("#handoff-preference-rules").open=true;document.querySelector("#work-relation-rules").open=true');
    assert.equal(await evaluate('document.querySelector("#flow-exceptions").open'),false);
    assert.equal(await evaluate('document.querySelectorAll("[data-flow-common] [data-flow-rule]").length'),1);
    assert.equal(await evaluate('document.querySelectorAll("[data-flow-common] [data-flow-scope]").length'),0);
    const before=(await state()).handoffPreferences;acceptDialogs=false;
    await click('[data-flow-common] [data-flow-exception]');acceptDialogs=true;
    assert.deepEqual((await state()).handoffPreferences,before);
    assert.equal(await evaluate('document.querySelectorAll("#flow-exceptions [data-flow-rule]").length'),0);
    const solveFlight=async(flightNo)=>{
      const raw=structuredClone((await state()).plans[planId].day);
      raw.flights[0].flightNo=flightNo;raw.cells.forEach(c=>c.flightNo=flightNo);
      await command('save-input',{day:raw},planId);await reloadPage();await click('[data-view="schedule"]');await rerun();
      const s=await state(),p=s.plans[planId];assert.equal(p.valid,true,JSON.stringify(p.issues));
      const owner=s.people.find(p=>p.name==='乙').id;
      return p.schedule.tasks.find(t=>['登机核验','登机主验'].includes(t.roleId)&&p.draft.some(a=>a.taskId===t.id&&a.memberId===owner)).roleId;
    };
    assert.equal(await solveFlight('KE0166'),'登机核验','无特例沿用通用');
    await click('[data-view="rules"]');promptReply='CX937';await click('[data-flow-common] [data-flow-exception]');promptReply=undefined;
    assert.equal(await evaluate('document.querySelector("#flow-exceptions").open'),true);
    const exception='#flow-exceptions [data-flow-rule]';
    assert.equal(await evaluate(`document.querySelector('${exception} [data-flow-target]').value`),'登机核验','新特例从此来源通用设置复制');
    await click(`${exception} [data-flow-target-row="1"] [data-flow-up]`);await mutate(()=>click('#save-rules'));
    assert.deepEqual((await state()).handoffPreferences[0],shared);
    assert.equal(await solveFlight('CX937'),'登机主验','手动特例覆盖通用');
    assert.equal(await solveFlight('TW616'),'登机核验','其他未配置航班仍用通用，不自动生成逐班设置');
    const prefs=(await state()).handoffPreferences;
    assert.equal(prefs.length,2);
    const file=await fetch(`${baseUrl}/api/workspace/configuration/export/${planId}`).then(r=>r.arrayBuffer());
    const preview=await fetch(`${baseUrl}/api/workspace/configuration/preview`,{method:'POST',body:file}).then(r=>r.json());
    assert.deepEqual(preview.payload.handoffPreferences,prefs);
    await stopServer();await launchServer();await call('Page.navigate',{url:baseUrl});await until(()=>evaluate('!!document.querySelector("[data-view=rules]")'),'通用特例重启');
    await click('[data-view="rules"]');await evaluate('document.querySelector("#handoff-preference-rules").open=true;document.querySelector("#work-relation-rules").open=true');
    assert.equal(await evaluate('document.querySelector("#flow-exceptions").open'),false);
    assert.deepEqual((await state()).handoffPreferences,prefs);
    const accents=await evaluate('[".flow-settings",".baggage-window",".separation-settings",".forbidden-handoffs"].map(s=>getComputedStyle(document.querySelector(s)).getPropertyValue("--rule-accent")||getComputedStyle(document.querySelector(s)).borderLeftColor)');
    assert.equal(new Set(accents).size,4);
    for(const width of [1440,390]){
      await call('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:false});
      await evaluate('document.querySelector(".flow-settings").scrollIntoView({block:"start"})');
      assert.ok(await evaluate('document.documentElement.scrollWidth<=innerWidth+1'));
      await writeFile(join(temporaryRoot,`flow-common-${width}.png`),Buffer.from((await call('Page.captureScreenshot',{format:'png'})).data,'base64'));
      await evaluate('document.querySelector("#flow-exceptions").open=true;document.querySelector("#flow-exceptions").scrollIntoView({block:"start"})');
      assert.ok(await evaluate('[...document.querySelectorAll("#flow-exceptions input,#flow-exceptions select,#flow-exceptions button")].every(e=>{const r=e.getBoundingClientRect();return r.width>0&&r.left>=0&&r.right<=innerWidth+1})'));
      await writeFile(join(temporaryRoot,`flow-exception-${width}.png`),Buffer.from((await call('Page.captureScreenshot',{format:'png'})).data,'base64'));
      await evaluate('document.querySelector("#flow-exceptions").open=false');
    }
    await click(`${exception} [data-flow-remove-rule]`);await mutate(()=>click('#save-rules'));
    assert.deepEqual((await state()).handoffPreferences,[shared]);
    assert.equal(await solveFlight('CX937'),'登机核验','删除特例恢复通用');
    for(const [id,formal] of Object.entries(history))assert.deepEqual((await state()).plans[id].adopted,formal,'既有正式历史保持');
    await click('[data-view="rules"]');
  });
  await t.test('专岗不进入流动选项：旧引用保留、新增排除、取消恢复及迁移重启',async()=>{
    const original=await state(),prefs=structuredClone(original.handoffPreferences);
    const formal=Object.fromEntries(Object.entries(original.plans).map(([id,p])=>[id,p.adopted]));
    for(const roleName of ['登机主验','登机核验'])await command('save-role',{roleName,exclusiveSameFlight:true});
    let source=await state();
    assert.deepEqual(source.handoffPreferences,prefs,'设置专岗不清除既有引用');
    await reloadPage();await click('[data-view="rules"]');
    await evaluate('document.querySelector("#handoff-preference-rules").open=true;document.querySelector("#work-relation-rules").open=true');
    assert.ok(await evaluate('[...document.querySelectorAll("[data-flow-target] option")].some(o=>o.value==="登机核验"&&o.selected&&o.disabled)'), '原引用明确显示且禁选');
    assert.match(await evaluate('document.querySelector(".flow-settings").textContent'),/已设专岗/);
    assert.ok(await evaluate('[...document.querySelectorAll("[data-flow-source] option,[data-flow-target] option,[data-group-role] option,[data-forbidden-source] option,[data-forbidden-target] option,[data-work-field=firstRole] option,[data-work-field=secondRole] option")].every(o=>!["登机主验","登机核验"].includes(o.value)||(o.selected&&o.disabled))'));
    await click('#add-flow-rule');
    assert.ok(await evaluate('!["登机主验","登机核验"].includes([...document.querySelectorAll("[data-flow-common] [data-flow-source]")].at(-1).value)'));
    await mutate(()=>click('#save-rules'));
    source=await state();assert.deepEqual(source.handoffPreferences.slice(0,prefs.length),prefs);
    const file=await fetch(`${baseUrl}/api/workspace/configuration/export/${source.selectedPlanId}`).then(r=>r.arrayBuffer());
    const preview=await fetch(`${baseUrl}/api/workspace/configuration/preview`,{method:'POST',body:file}).then(r=>r.json());
    assert.deepEqual(preview.payload.handoffPreferences,source.handoffPreferences.map(row=>({...row,targetFlightScopes:row.targetFlightScopes??{}})),'Excel会为新建空目标规则显式补空范围表，原引用保持');
    assert.equal(preview.payload.day.roleConfigs.find(r=>r.roleName==='登机核验').exclusiveSameFlight,true);
    await stopServer();await launchServer();await call('Page.navigate',{url:baseUrl});
    await until(()=>evaluate('!!document.querySelector("[data-view=rules]")'),'专岗过滤重启');
    assert.ok(!(await state()).flowRuleRoles.includes('登机核验'));
    await command('save-role',{roleName:'登机核验',exclusiveSameFlight:false});
    await reloadPage();await click('[data-view="rules"]');
    assert.ok(await evaluate('[...document.querySelectorAll("[data-flow-target] option")].some(o=>o.value==="登机核验"&&!o.disabled)'), '取消专岗并保存后恢复');
    assert.ok(!(await state()).flowRuleRoles.includes('登机主验'),'其他专岗仍排除');
    for(const [id,adopted] of Object.entries(formal))assert.deepEqual((await state()).plans[id].adopted,adopted,'不自动改写正式');
  });
  await evaluate('document.querySelector("#handoff-preference-rules").open=true');
  for(const width of [1440,390]){
    await call('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:false});
    await evaluate('document.querySelector("[data-flow-rule]:last-child").scrollIntoView({block:"center"})');
    assert.ok(await evaluate('document.documentElement.scrollWidth<=innerWidth+1'));
    assert.ok(await evaluate('[...document.querySelectorAll("[data-flow-rule]:last-child select,[data-flow-rule]:last-child button")].every(el=>{const r=el.getBoundingClientRect();return r.width>0&&r.left>=0&&r.right<=innerWidth+1})'));
    await writeFile(join(temporaryRoot,`flow-rules-${width}.png`),Buffer.from((await call('Page.captureScreenshot',{format:'png'})).data,'base64'));
  }
  assert.deepEqual(runtimeErrors, []);
});
