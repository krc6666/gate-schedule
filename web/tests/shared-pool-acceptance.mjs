import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {join} from 'node:path';
import {writeFile,readFile} from 'node:fs/promises';

export async function sharedPoolAcceptance({evaluate,click,value,until,ready,save,login,released,call,repo}){
  const state=()=>evaluate("fetch('/api/groups/A/workspace').then(r=>r.json())");
  const command=async(action,payload={},planId)=>{
    const s=await state();
    const result=await evaluate(`fetch('/api/groups/A/workspace/command',{method:'POST',headers:{'Content-Type':'application/json','X-Gate-Rule-Schema':'flight-role-v2'},body:JSON.stringify(${JSON.stringify({expectedRevision:s.revision,action,payload,planId})})}).then(async r=>({status:r.status,value:await r.json()}))`);
    assert.equal(result.status,200,JSON.stringify(result.value));return result.value;
  };
  const reload=async()=>{
    await save();await evaluate('window.confirm=()=>true');await click('.cloud-more summary');await click('#cloud-logout');
    await until(()=>evaluate('document.body.textContent.includes("会话已结束")'),'共享池保存退出');await until(released,'会话释放');
    await evaluate('location.reload();true');await login();await ready();
    await evaluate('window.alerts=[];window.alert=m=>window.alerts.push(m);window.confirm=()=>true');
  };
  const python=process.env.GATE_TEST_PYTHON??join(repo,'.venv-local/Scripts/python.exe');
  const fixture=JSON.parse(execFileSync(python,['-X','utf8','-B','-c','import json;from tests.test_shared_baggage_scopes import flexible_configuration;print(json.dumps(flexible_configuration()))'],{cwd:repo,encoding:'utf8'}));
  let current=await command('import-complete-configuration',fixture);
  for(const r of current.configuration.roleConfigs)if(!fixture.day.roleNames.includes(r.roleName))await command('save-role',{...r,requiredCount:0});
  const solve=async()=>{
    const s=await state(),pid=s.selectedPlanId;
    const run=await evaluate(`fetch('/api/groups/A/workspace/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(${JSON.stringify({expectedRevision:s.revision,action:'solve',planId:pid})})}).then(r=>r.json())`);
    await until(async()=>{const r=await evaluate(`fetch('/api/groups/A/workspace/runs/${run.id}').then(r=>r.json())`);return r.status!=='running'},'共享大件池实际求解');
    const result=await evaluate(`fetch('/api/groups/A/workspace/runs/${run.id}').then(r=>r.json())`);
    assert.equal(result.status,'completed',JSON.stringify(result));return (await state()).plans[pid];
  };
  let plan=await solve();assert.equal(plan.valid,true,JSON.stringify(plan.issues));assert.equal(plan.draft.length,4);
  await reload();await click('[data-view=rules]');
  assert.match(await evaluate('document.querySelector("[data-flow-rule=\\"0\\"] [data-flow-flight-scope]").selectedOptions[0].textContent'),/^重叠时段中较早结束的航班$/);
  assert.equal(await evaluate('document.querySelector("[data-baggage-handoff-mode]").value'),'flexible');
  // The same saved scope must select the other member of the pool when changed to later.
  const later=structuredClone(fixture);
  for(const c of later.day.cells)if(c.roleName!=='大件行李拦截')c.requiredCountOverride=c.flightId==='B'?1:0;
  await command('import-complete-configuration',later);await reload();await click('[data-view=rules]');
  await value('[data-flow-rule="0"] [data-flow-flight-scope]','later-overlap');await click('#save-rules');
  await until(async()=>(await state()).handoffPreferences[0].targetFlightScopes['接机1']==='later-overlap','保存共享池较晚范围');
  plan=await solve();assert.equal(plan.valid,true,JSON.stringify(plan.issues));
  const late=plan.schedule.tasks.find(t=>t.roleId==='接机1');assert.equal(plan.day.flights.find(f=>f.id===late.flightId).flightNo,'KE0166');
  const bag=plan.schedule.tasks.find(t=>t.timingLabel.includes('大件2'));
  assert.ok(plan.draft.some(a=>a.taskId===bag.id&&plan.draft.some(b=>b.taskId===late.id&&a.memberId===b.memberId)),'较晚范围实际接KE0166');
  const source=process.env.GATE_TEST_SHARED_POOL_CONFIG;
  if(source){
    const bytes=(await readFile(source)).toString('base64');
    const imported=await evaluate(`fetch('/api/groups/A/workspace/configuration/preview',{method:'POST',body:Uint8Array.from(atob(${JSON.stringify(bytes)}),c=>c.charCodeAt(0))}).then(r=>r.json())`);
    current=await command('import-complete-configuration',imported.payload);
    const groupB=await evaluate("fetch('/api/groups/B/workspace/backup').then(r=>r.json()).then(b=>b.groups.B)");
    const prefs=structuredClone(current.handoffPreferences),formal=Object.fromEntries(Object.entries(current.plans).filter(([,p])=>p.adopted).map(([id,p])=>[id,p.adopted]));
    plan=await solve();assert.equal(plan.day.serviceDate,'2026-08-24');assert.equal(plan.schedule.tasks.length,50);
    assert.equal(plan.draft.length,58,'原配置缺口从13人岗降为3人岗');
    assert.ok(plan.issues.every(i=>i.code==='coverage_shortfall'&&i.message.includes('登机人证核对/队伍秩序维护')),JSON.stringify(plan.issues));
    assert.deepEqual((await state()).handoffPreferences,prefs,'原早/晚配置无需改为本班');
    assert.deepEqual(await evaluate("fetch('/api/groups/B/workspace/backup').then(r=>r.json()).then(b=>b.groups.B)"),groupB,'A组重排不改B组');
    for(const [id,adopted] of Object.entries(formal))assert.deepEqual((await state()).plans[id].adopted,adopted);
    for(const [turn,role] of [['大件1','接机1'],['大件2','接机2']]){
      const t=plan.schedule.tasks.find(t=>t.timingLabel.startsWith(turn)&&t.start==='10:10');
      const target=plan.schedule.tasks.find(t=>t.roleId===role&&plan.day.flights.find(f=>f.id===t.flightId).flightNo==='CX937');
      assert.ok(t&&target);assert.ok(t.timingLabel.includes('参考'));
      assert.ok(plan.draft.some(a=>a.taskId===t.id&&plan.draft.some(b=>b.taskId===target.id&&a.memberId===b.memberId)),turn+'实际接池内较早结束的CX937');
    }
    const exported=await evaluate(`fetch('/api/groups/A/workspace/configuration/export/${current.selectedPlanId}').then(r=>r.arrayBuffer()).then(b=>{const bytes=new Uint8Array(b);let text='';for(let i=0;i<bytes.length;i+=8192)text+=String.fromCharCode(...bytes.subarray(i,i+8192));return btoa(text)})`);
    const preview=await evaluate(`fetch('/api/groups/A/workspace/configuration/preview',{method:'POST',body:Uint8Array.from(atob(${JSON.stringify(exported)}),c=>c.charCodeAt(0))}).then(r=>r.json())`);
    assert.equal(preview.payload.workRules.baggageWindow.handoffMode,'flexible');assert.deepEqual(preview.payload.handoffPreferences,prefs);
    await writeFile(join(repo,'.tmp/shared-pool-browser-input-result.json'),JSON.stringify({preferences:prefs,issues:plan.issues,draftCount:plan.draft.length,mode:preview.payload.workRules.baggageWindow.handoffMode},null,2));
  }
  await reload();await click('[data-view=rules]');
  for(const width of [1440,390]){
    await call('Emulation.setDeviceMetricsOverride',{width,height:1100,deviceScaleFactor:1,mobile:false});
    await evaluate('(()=>{document.querySelector("#handoff-preference-rules").open=true;const e=document.querySelector("[data-flow-route-root]");e.open=true;e.scrollIntoView({block:"start"});scrollBy(0,-160)})()');
    assert.ok(await evaluate('document.documentElement.scrollWidth<=innerWidth+1'));
    await writeFile(join(repo,`.tmp/shared-pool-${width}.png`),Buffer.from((await call('Page.captureScreenshot',{format:'png'})).data,'base64'));
  }
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});
  await save();const before=await state();await evaluate('window.confirm=()=>true');await click('.cloud-more summary');await click('#cloud-logout');
  await until(()=>evaluate('document.body.textContent.includes("会话已结束")'),'共享池保存退出');await until(released,'会话释放');
  await evaluate('location.reload();true');await login();await ready();
  const after=await state();assert.deepEqual(after.handoffPreferences,before.handoffPreferences);assert.equal(after.workRules.baggageWindow.handoffMode,'flexible');
  assert.equal(after.plans[after.selectedPlanId].draft.length,before.plans[before.selectedPlanId].draft.length);
  console.log('Shared baggage pool passed: actual earlier/later selection, flexible handoff, downstream inheritance, saved configuration, cloud reopen and original formal preservation.');
}
