import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {join} from 'node:path';
import {writeFile} from 'node:fs/promises';

// Real dev.py worker and browser controls; isolated simulated cloud documents.
export async function predecessorAcceptance({evaluate,click,value,until,ready,save,login,released,call,repo}){
  const state=()=>evaluate("fetch('/api/groups/A/workspace').then(r=>r.json())");
  const command=async(action,payload={},planId)=>{
    const s=await state();
    const response=await evaluate(`fetch('/api/groups/A/workspace/command',{method:'POST',headers:{'Content-Type':'application/json','X-Gate-Rule-Schema':'flight-role-v2'},body:JSON.stringify(${JSON.stringify({expectedRevision:s.revision,action,payload,planId})})}).then(async r=>({status:r.status,value:await r.json()}))`);
    assert.equal(response.status,200,JSON.stringify(response.value));return response.value;
  };
  const python=process.env.GATE_TEST_PYTHON??join(repo,'.venv-local/Scripts/python.exe');
  const input=JSON.parse(execFileSync(python,['-c','import json;from tests.test_continuation_chains import baggage_chain_configuration;print(json.dumps(baggage_chain_configuration()))'],{cwd:repo,encoding:'utf8'}));
  input.handoffPreferences.push({flightNo:'*',arrivalRole:'秩序维护',boardingRoles:['登机查验'],enabled:true,ordered:true,targetFlightScopes:{'登机查验':'same-flight'}});
  input.day.flowPreferences=structuredClone(input.handoffPreferences);
  let current=await command('import-complete-configuration',input);
  for(const r of current.configuration.roleConfigs)if(!input.day.roleNames.includes(r.roleName))await command('save-role',{...r,requiredCount:0});
  const reload=async()=>{
    await save();await evaluate('window.confirm=()=>true');await click('.cloud-more summary');await click('#cloud-logout');
    await until(()=>evaluate('document.body.textContent.includes("会话已结束")'),'保存后退出');await until(released,'释放会话');
    await evaluate('location.reload();true');await login();await ready();
    await evaluate('window.alerts=[];window.alert=m=>window.alerts.push(m);window.confirm=()=>true');
  };
  const safeClick=async selector=>{await evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e)throw Error('找不到接续控件');for(let p=e.tagName==='SUMMARY'?e.parentElement.parentElement:e.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true;e.click()})()`)};
  await reload();await click('[data-view=rules]');
  assert.equal(await evaluate('document.querySelectorAll("#add-flow-chain,[data-flow-mode]").length'),0);
  assert.equal(await evaluate('document.querySelectorAll(".flow-predecessor,[data-flow-predecessor],[data-flow-predecessor-scope]").length'),0,'没有单独前序表单');
  assert.equal(await evaluate('document.querySelectorAll("[data-flow-enabled],[data-flow-exception]").length'),0,'去掉逐条启用勾选和特例按钮');
  await safeClick('[data-flow-rule="0"] [data-flow-prepend]');
  const card='[data-flow-rule="2"]';
  assert.ok(await evaluate('document.querySelector("[data-flow-route-root=\\"2\\"]").open'),'插入后自动展开整条');
  await value(card+' [data-flow-source]','大件1');
  const assertOrder=async()=>{
    assert.equal(await evaluate('document.querySelectorAll("[data-flow-route-root]").length'),1,'原来源与后续合并在一条里');
    assert.deepEqual(await evaluate(`Array.from(document.querySelector('${card}').querySelectorAll('[data-flow-source]')).map(e=>e.value)`),['大件1','接机1','秩序维护']);
    assert.match(await evaluate(`document.querySelector('${card} > summary [data-rule-path]').textContent`),/大件1 → 接机1 → 秩序维护 → 登机查验/);
    assert.equal(await evaluate(`document.querySelector('${card} [data-flow-source]').getAttribute('aria-label')`),'来源岗位');
  };
  await assertOrder();
  assert.ok(await evaluate(`Array.from(document.querySelector('${card}').querySelectorAll('details')).every(e=>!e.open)`),'后续默认收起');
  for(const scope of ['earlier-overlap','later-overlap','same-flight']){
    await value(card+' [data-flow-flight-scope]',scope);
    assert.equal(await evaluate('document.querySelectorAll("[data-flow-route-root]").length'),1,'切换航班条件不拆卡');
    assert.equal(await evaluate('document.querySelector("[data-flow-rule=\\"0\\"] [data-flow-flight-scope]").value'),'same-flight','后续范围独立保留');
    if(scope!=='same-flight')assert.match(await evaluate(`document.querySelector('${card} > summary [data-rule-path]').textContent`),new RegExp(scope==='earlier-overlap'?'早结束重叠航班':'晚结束重叠航班'));
  }
  await assertOrder();
  await safeClick('[data-flow-rule="0"] > summary');
  await value('[data-flow-rule="0"] [data-flow-flight-scope]','earlier-overlap');
  assert.equal(await evaluate(`document.querySelector('${card} [data-flow-flight-scope]').value`),'same-flight','展开后续可独立选较早');
  await value('[data-flow-rule="0"] [data-flow-flight-scope]','same-flight');
  // Editing a nested target must not run its ancestor's target handler.
  await safeClick('[data-flow-rule="0"] [data-flow-add-target]');
  await safeClick('[data-flow-rule="0"] [data-flow-target-row="1"] [data-flow-remove-target]');
  await assertOrder();
  await safeClick(card+' [data-flow-prepend]');
  assert.equal(await evaluate('document.querySelectorAll("[data-flow-route-root]").length'),1,'可继续向前增加岗位');
  assert.deepEqual(await evaluate('Array.from(document.querySelector("[data-flow-route-root=\\"3\\"]").querySelectorAll("[data-flow-source]")).slice(1).map(e=>e.value)'),['大件1','接机1','秩序维护']);
  await safeClick('[data-flow-rule="3"] [data-flow-remove-rule]');
  await assertOrder();
  await safeClick('#save-rules');
  await until(async()=>((await state()).handoffPreferences.length===3),'保存前序关系');
  current=await state();const prefs=current.handoffPreferences;
  assert.deepEqual(prefs.slice(0,2),input.handoffPreferences,'下游不复制、不改写');
  assert.deepEqual(prefs[2].boardingRoles,['接机1']);
  assert.equal(prefs[2].arrivalRole,'大件1');assert.ok(prefs.every(r=>!r.mode&&!r.id));
  await click('[data-view=rules]');
  for(const width of [1440,390]){
    await call('Emulation.setDeviceMetricsOverride',{width,height:1100,deviceScaleFactor:1,mobile:false});
    await evaluate(`(()=>{const e=document.querySelector('${card}');for(let p=e;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true;for(const step of e.querySelectorAll('details'))step.open=false;})()`);
    assert.ok(await evaluate('document.documentElement.scrollWidth<=innerWidth+1'),'前序编辑不产生横向溢出');
    await evaluate(`document.querySelector('${card}').scrollIntoView({block:'start'});scrollBy(0,-170)`);
    await writeFile(join(repo,`.tmp/handoff-tidy-${width}.png`),Buffer.from((await call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    await safeClick('[data-flow-rule="0"] > summary');
    assert.ok(await evaluate('document.querySelector("[data-flow-rule=\\"0\\"]").open'),'实际展开后续编辑');
    assert.ok(await evaluate('document.documentElement.scrollWidth<=innerWidth+1'),'展开后续也无溢出');
    await writeFile(join(repo,`.tmp/handoff-tidy-expanded-${width}.png`),Buffer.from((await call('Page.captureScreenshot',{format:'png'})).data,'base64'));
  }
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});
  await reload();await click('[data-view=rules]');
  await safeClick(card+' > summary');await assertOrder();
  const pid=current.selectedPlanId;
  const solve=async()=>{
    const s=await state();
    const run=await evaluate(`fetch('/api/groups/A/workspace/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(${JSON.stringify({expectedRevision:s.revision,action:'solve',planId:pid})})}).then(r=>r.json())`);
    await until(async()=>{const r=await evaluate(`fetch('/api/groups/A/workspace/runs/${run.id}').then(r=>r.json())`);return r.status!=='running'},'实际求解');
    const done=await evaluate(`fetch('/api/groups/A/workspace/runs/${run.id}').then(r=>r.json())`);
    assert.equal(done.status,'completed',JSON.stringify(done));return (await state()).plans[pid];
  };
  const worker=current.people.find(m=>m.name==='甲').id;
  let p=await solve();assert.equal(p.valid,true,JSON.stringify(p.issues));
  assert.equal(p.draft.length,5);assert.equal(p.draft.filter(a=>a.memberId===worker).length,4,'大件1与三后续岗位同人');
  assert.equal(p.baggageCoverage.requiredPeople,2,'大件另一轮仍由另一人承担');
  await command('adopt',{},pid);const formal=(await state()).plans[pid].adopted;
  // Edit the middle link only. The preceding baggage link is unchanged.
  const changed=structuredClone(prefs);
  changed[0].boardingRoles=['登机查验'];changed[0].targetFlightScopes={'登机查验':'same-flight'};
  changed[1]={...changed[1],arrivalRole:'登机查验',boardingRoles:['秩序维护'],targetFlightScopes:{'秩序维护':'same-flight'}};
  await command('save-handoff-preferences',{preferences:changed});p=await solve();
  assert.equal(p.valid,true,JSON.stringify(p.issues));assert.equal(p.draft.filter(a=>a.memberId===worker).length,4);
  assert.deepEqual((await state()).handoffPreferences[2],prefs[2]);
  const file=await evaluate(`fetch('/api/groups/A/workspace/configuration/export/${pid}').then(r=>r.arrayBuffer()).then(b=>btoa(String.fromCharCode(...new Uint8Array(b))))`);
  const preview=await evaluate(`fetch('/api/groups/A/workspace/configuration/preview',{method:'POST',body:Uint8Array.from(atob(${JSON.stringify(file)}),c=>c.charCodeAt(0))}).then(r=>r.json())`);
  assert.deepEqual(preview.payload.handoffPreferences,changed);
  await command('import-complete-configuration',preview.payload);
  await reload();assert.deepEqual((await state()).handoffPreferences,changed);
  assert.deepEqual((await state()).plans[pid].adopted,formal,'正式历史不追改');
  const policy=(await state()).workRules;
  await command('save-rules',{configuration:(await state()).configuration,preferences:changed,balanceSettings:(await state()).balanceSettings,
    workRules:{...policy,forbiddenHandoffs:[{flightNo:'*',sourceRole:'大件1',targets:[{role:'秩序维护',flightScope:'same-flight'}],enabled:true}]}});
  p=await solve();assert.ok(!p.issues.some(i=>i.code==='forbidden_handoff'),'自动安排守住禁止');
  assert.ok(!p.issues.some(i=>i.code==='continuation_chain_unmet'),'普通接续没有整组未齐提示');
  const bag=p.schedule.tasks.filter(t=>t.roleId==='大件行李拦截').sort((a,b)=>a.start-b.start)[0];
  const last=p.schedule.tasks.find(t=>t.roleId==='秩序维护');
  assert.ok(bag&&last);const baggagePeople=p.draft.filter(a=>a.taskId===bag.id).map(a=>a.memberId);
  assert.ok(!p.draft.some(a=>a.taskId===last.id&&baggagePeople.includes(a.memberId)));
  assert.deepEqual((await state()).plans[pid].adopted,formal);
  await command('save-handoff-preferences',{preferences:[...changed,{id:'old',mode:'chain',flightNo:'*',arrivalRole:'大件1',boardingRoles:['接机1','登机查验','秩序维护'],enabled:true,ordered:true,targetFlightScopes:{'接机1':'same-flight','登机查验':'same-flight','秩序维护':'same-flight'}}]});
  await reload();await click('[data-view=rules]');
  assert.equal(await evaluate('document.querySelectorAll("[data-flow-rule]").length'),3,'旧组只读保留，未混入日常卡片');
  assert.ok((await state()).handoffSimplification.preferences);
  await safeClick('[data-flow-simplify]');await safeClick('#save-rules');
  await until(async()=>!(await state()).handoffPreferences.some(r=>r.mode==='chain'),'显式整理旧设置');
  assert.deepEqual((await state()).handoffPreferences,changed,'整理复用已有来源，不重复复制目标');
  assert.deepEqual((await state()).plans[pid].adopted,formal);await reload();
  assert.deepEqual((await state()).handoffPreferences,changed);await save();
  await click('[data-view=rules]');
  const original=structuredClone((await state()).handoffPreferences);
  const exceptionFlight=(await state()).flightLibrary[0].flightNo;
  await safeClick('#add-flow-exception');
  assert.match(await evaluate('document.querySelector("[data-flow-exception-feedback]").textContent'),/先选择/);
  const chosen=original.find(r=>r.arrivalRole==='接机1');assert.ok(chosen);
  await value('[data-flow-exception-source]','接机1');
  await value('[data-flow-exception-flight]',` ${exceptionFlight.toLowerCase()} `);await safeClick('#add-flow-exception');
  const exception=`[data-flow-rule="${original.length}"]`;
  assert.ok(await evaluate('document.querySelector("#flow-exceptions").open'),'添加后展开特例');
  assert.equal(await evaluate(`document.querySelector('${exception} [data-flow-target]').value`),chosen.boardingRoles[0],'复制明确选中的来源');
  await safeClick('#save-rules');
  await until(async()=>(await state()).handoffPreferences.length===original.length+1,'集中添加保存');
  assert.deepEqual((await state()).handoffPreferences.at(-1),{...chosen,flightNo:exceptionFlight});
  await reload();await click('[data-view=rules]');
  assert.deepEqual((await state()).handoffPreferences.at(-1),{...chosen,flightNo:exceptionFlight},'保存重开保留特例');
  for(const width of [1440,390]){
    await call('Emulation.setDeviceMetricsOverride',{width,height:1100,deviceScaleFactor:1,mobile:false});
    await evaluate('document.querySelector("#handoff-preference-rules").open=true;document.querySelector(".flow-exception-create").scrollIntoView({block:"center"})');
    assert.ok(await evaluate('document.documentElement.scrollWidth<=innerWidth+1'));
    await writeFile(join(repo,`.tmp/handoff-exception-${width}.png`),Buffer.from((await call('Page.captureScreenshot',{format:'png'})).data,'base64'));
  }
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});
  await safeClick(exception+' [data-flow-remove-rule]');await safeClick('#save-rules');
  await until(async()=>(await state()).handoffPreferences.length===original.length,'删除特例恢复通用');
  assert.deepEqual((await state()).handoffPreferences,original);await save();
  console.log('Ordered insertion passed: new first source, same card with all downstream steps, nested edit isolation, save/reopen, four jobs, Excel/cloud/formal preservation and prohibition.');
}
