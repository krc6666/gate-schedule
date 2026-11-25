import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {join} from 'node:path';
import {writeFile} from 'node:fs/promises';

export async function earlierAcceptance({evaluate,click,value,until,ready,save,login,released,call,repo}){
  const state=()=>evaluate("fetch('/api/groups/A/workspace').then(r=>r.json())");
  const command=async(action,payload={},planId)=>{
    const s=await state();
    const response=await evaluate(`fetch('/api/groups/A/workspace/command',{method:'POST',headers:{'Content-Type':'application/json','X-Gate-Rule-Schema':'flight-role-v2'},body:JSON.stringify(${JSON.stringify({expectedRevision:s.revision,action,payload,planId})})}).then(async r=>({status:r.status,value:await r.json()}))`);
    assert.equal(response.status,200,JSON.stringify(response.value));return response.value;
  };
  const input=JSON.parse(execFileSync(process.env.GATE_TEST_PYTHON??join(repo,'.venv-local/Scripts/python.exe'),['-c','import json;from tests.test_arrival_cross_flight import arrival_configuration;print(json.dumps(arrival_configuration()))'],{cwd:repo,encoding:'utf8'}));
  let s=await command('import-complete-configuration',input);
  for(const r of s.configuration.roleConfigs)if(!input.day.roleNames.includes(r.roleName))s=await command('save-role',{...r,requiredCount:0});
  const pid=s.selectedPlanId;
  const solve=async()=>{
    const current=await state();
    const run=await evaluate(`fetch('/api/groups/A/workspace/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(${JSON.stringify({expectedRevision:current.revision,action:'solve',planId:pid})})}).then(r=>r.json())`);
    await until(async()=>{const r=await evaluate(`fetch('/api/groups/A/workspace/runs/${run.id}').then(r=>r.json())`);return r.status!=='running'},'较早范围实际求解');
    const done=await evaluate(`fetch('/api/groups/A/workspace/runs/${run.id}').then(r=>r.json())`);
    assert.equal(done.status,'completed',JSON.stringify(done));return (await state()).plans[pid];
  };
  let p=await solve();assert.equal(p.valid,true,JSON.stringify(p.issues));await command('adopt',{},pid);
  const formal=(await state()).plans[pid].adopted;
  const reload=async()=>{
    await save();await evaluate('window.confirm=()=>true');await click('.cloud-more summary');await click('#cloud-logout');
    await until(()=>evaluate('document.body.textContent.includes("会话已结束")'),'较早配置保存退出');await until(released,'会话释放');
    await evaluate('location.reload();true');await login();await ready();await evaluate('window.alerts=[];window.alert=m=>window.alerts.push(m);window.confirm=()=>true');
  };
  const safeClick=async selector=>evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e)throw Error('找不到范围控件');for(let p=e.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true;e.click()})()`);
  await reload();await click('[data-view=rules]');
  for(const index of [0,1]){
    for(const target of [0,1]){
      const selector=`[data-flow-rule="${index}"] [data-flow-target-row="${target}"] [data-flow-flight-scope]`;
      assert.ok(await evaluate(`Array.from(document.querySelector(${JSON.stringify(selector)}).options).some(o=>o.value==='earlier-overlap')`));
      await value(selector,'earlier-overlap');
    }
    const source=`[data-flow-rule="${index}"] [data-flow-source]`;
    await value(source,index===0?'接机1':'接机2');
    assert.equal(await evaluate(`document.querySelector('[data-flow-rule="${index}"] [data-flow-target-row="${index}"] [data-flow-flight-scope]').value`),'earlier-overlap','同名来源修改不能重置为较晚');
  }
  const forbidden='[data-work-editor="base"] [data-forbidden-rule="0"]';
  await safeClick(forbidden+' [data-forbidden-add-target]');
  const own=forbidden+' [data-forbidden-target-row="1"]';
  await value(own+' [data-forbidden-target]','接机1');await value(own+' [data-forbidden-flight]','earlier-overlap');
  assert.equal(await evaluate(`document.querySelector('${own} [data-forbidden-flight]').value`),'earlier-overlap');
  await safeClick('#save-rules');
  await until(async()=>Object.values((await state()).handoffPreferences[0].targetFlightScopes??{}).every(s=>s==='earlier-overlap'),'保存较早范围');
  s=await state();const prefs=s.handoffPreferences,policy=s.workRules;
  assert.equal(policy.forbiddenHandoffs[0].targets[1].flightScope,'earlier-overlap');
  for(const width of [1440,390]){
    await call('Emulation.setDeviceMetricsOverride',{width,height:1100,deviceScaleFactor:1,mobile:false});
    await evaluate('document.querySelector("#handoff-preference-rules").open=true;document.querySelector("[data-flow-rule=\\"0\\"]").open=true;document.querySelector("[data-flow-rule=\\"0\\"]").scrollIntoView({block:"start"});scrollBy(0,-180)');
    assert.ok(await evaluate('document.documentElement.scrollWidth<=innerWidth+1'));
    await writeFile(join(repo,`.tmp/earlier-overlap-${width}.png`),Buffer.from((await call('Page.captureScreenshot',{format:'png'})).data,'base64'));
  }
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});
  p=await solve();assert.equal(p.valid,true,JSON.stringify(p.issues));assert.equal(p.draft.length,4,'两个接机岗两班共四个任务全部排齐');
  const byRole=(flight,role)=>p.schedule.tasks.find(t=>p.day.flights.find(f=>f.id===t.flightId)?.flightNo===flight&&t.roleId===role);
  const from=byRole('B200','接机1'),to=byRole('A100','接机2'),blocked=byRole('A100','接机1');assert.ok(from&&to&&blocked);
  const worker=p.draft.find(a=>a.taskId===from.id).memberId;
  assert.ok(p.draft.some(a=>a.taskId===to.id&&a.memberId===worker),'晚结束班接机1按新方向兼任早结束班接机2');
  assert.ok(!p.draft.some(a=>a.taskId===blocked.id&&a.memberId===worker),'同名较早禁止真实生效');
  const file=await evaluate(`fetch('/api/groups/A/workspace/configuration/export/${pid}').then(r=>r.arrayBuffer()).then(b=>btoa(String.fromCharCode(...new Uint8Array(b))))`);
  const preview=await evaluate(`fetch('/api/groups/A/workspace/configuration/preview',{method:'POST',body:Uint8Array.from(atob(${JSON.stringify(file)}),c=>c.charCodeAt(0))}).then(r=>r.json())`);
  assert.deepEqual(preview.payload.handoffPreferences,prefs);assert.deepEqual(preview.payload.workRules,policy);
  await command('import-complete-configuration',preview.payload);await reload();
  s=await state();assert.deepEqual(s.handoffPreferences,prefs);assert.deepEqual(s.workRules,policy);assert.deepEqual(s.plans[pid].adopted,formal,'旧较晚范围正式不追改');
  p=await solve();assert.equal(p.valid,true,JSON.stringify(p.issues));assert.equal(p.draft.length,4);await save();
  console.log('Earlier overlap passed: both editors, same-role scope preservation, actual reverse direction, prohibition, Excel/cloud/reopen and old formal preservation.');
}
