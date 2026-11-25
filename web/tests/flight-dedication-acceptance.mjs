import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {join} from 'node:path';
import {writeFile} from 'node:fs/promises';

export async function flightDedicationAcceptance({evaluate,click,value,until,ready,save,login,released,call,repo}){
  const state=()=>evaluate("fetch('/api/groups/A/workspace').then(r=>r.json())");
  const command=async(action,payload={},planId)=>{
    const s=await state();
    const response=await evaluate(`fetch('/api/groups/A/workspace/command',{method:'POST',headers:{'Content-Type':'application/json','X-Gate-Rule-Schema':'flight-role-v2'},body:JSON.stringify(${JSON.stringify({expectedRevision:s.revision,action,payload,planId})})}).then(async r=>({status:r.status,value:await r.json()}))`);
    assert.equal(response.status,200,JSON.stringify(response.value));return response.value;
  };
  const reload=async()=>{
    await save();await evaluate('window.confirm=()=>true');await click('.cloud-more summary');await click('#cloud-logout');
    await until(()=>evaluate('document.body.textContent.includes("会话已结束")'),'专岗保存后退出');await until(released,'释放会话');
    await evaluate('location.reload();true');await login();await ready();
    await evaluate('window.alerts=[];window.alert=m=>window.alerts.push(m);window.confirm=()=>true');
  };
  const python=process.env.GATE_TEST_PYTHON??join(repo,'.venv-local/Scripts/python.exe');
  const fixture=JSON.parse(execFileSync(python,['-X','utf8','-B','-c','import json;from tests.test_flight_specific_dedication import complete_payload;print(json.dumps(complete_payload(dedicated_cx=False)))'],{cwd:repo,encoding:'utf8'}));
  await command('import-complete-configuration',fixture);
  const other=await evaluate("fetch('/api/groups/B/workspace/backup').then(r=>r.json()).then(b=>b.groups.B)");
  await reload();await click('[data-view=config]');
  const cx='[data-template-exclusive="CX937"][data-template-role="登机主验"]';
  const ke='[data-template-exclusive="KE0166"][data-template-role="登机主验"]';
  assert.equal(await evaluate('document.querySelectorAll("#base-positions,[data-role-exclusive],[data-role-mode]").length'),0);
  await evaluate(`(()=>{const input=document.querySelector(${JSON.stringify(cx)});input.checked=true;input.dispatchEvent(new Event('change',{bubbles:true}))})()`);
  await until(async()=>((await state()).flightLibrary.find(f=>f.flightNo==='CX937').roles.find(r=>r.roleName==='登机主验').exclusiveSameFlight),'保存CX937专岗');
  assert.equal(await evaluate(`document.querySelector(${JSON.stringify(ke)}).checked`),false,'其他航班没有联动勾选');
  const mode='[data-template-mode="CX937"][data-template-role="登机主验"]';
  await value(mode,'continuous');
  await until(async()=>((await state()).flightLibrary.find(f=>f.flightNo==='CX937').roles.find(r=>r.roleName==='登机主验').workModeOverride==='continuous'),'保存单航班工作环节');
  assert.equal(await evaluate('document.querySelector("[data-template-mode=KE0166][data-template-role=登机主验]").value'),'boarding');
  await value(mode,'boarding');
  await until(async()=>((await state()).flightLibrary.find(f=>f.flightNo==='CX937').roles.find(r=>r.roleName==='登机主验').workModeOverride==='boarding'),'恢复主验环节');
  await click('[data-view=schedule]');await click('#solve-draft');
  await until(()=>evaluate('!!document.querySelector("#confirm-prepare")'),'重新排班准备');
  await click('#confirm-prepare');
  await until(async()=>{const s=await state();return !!s.plans[s.selectedPlanId].draft.length},'实际WASM排班');
  await until(()=>evaluate('!document.querySelector("#confirm-prepare")'),'排班完成');
  let s=await state(),pid=s.selectedPlanId,p=s.plans[pid];
  assert.equal(p.valid,true,JSON.stringify(p.issues));assert.equal(p.draft.length,4);
  const flights=new Map(p.day.flights.map(f=>[f.id,f.flightNo]));
  const task=(flight,role)=>p.schedule.tasks.find(t=>flights.get(t.flightId)===flight&&t.roleId===role);
  const member=t=>p.draft.find(a=>a.taskId===t.id).memberId;
  assert.equal(task('CX937','登机主验').exclusiveSameFlight,true);
  assert.notEqual(member(task('CX937','接机2')),member(task('CX937','登机主验')));
  assert.equal(member(task('KE0166','接机2')),member(task('KE0166','登机主验')));
  const valid=structuredClone(p.draft),cxMain=task('CX937','登机主验');
  const broken=valid.map(a=>a.taskId===cxMain.id?{...a,memberId:member(task('CX937','接机2'))}:a);
  s=await command('assign',{assignments:broken},pid);
  assert.ok(s.plans[pid].issues.some(i=>i.code==='exclusive_same_flight'),'人工破坏独立校验拒绝合法性');
  await command('assign',{assignments:valid},pid);
  const exported=await evaluate(`fetch('/api/groups/A/workspace/configuration/export/${pid}').then(r=>r.arrayBuffer()).then(b=>{const a=new Uint8Array(b);let s='';for(let i=0;i<a.length;i+=8192)s+=String.fromCharCode(...a.subarray(i,i+8192));return btoa(s)})`);
  const preview=await evaluate(`fetch('/api/groups/A/workspace/configuration/preview',{method:'POST',body:Uint8Array.from(atob(${JSON.stringify(exported)}),c=>c.charCodeAt(0))}).then(r=>r.json())`);
  const flags=Object.fromEntries(preview.payload.flightLibrary.map(f=>[f.flightNo,f.roles.find(r=>r.roleName==='登机主验').exclusiveSameFlight]));
  assert.deepEqual(flags,{CX937:true,KE0166:false});
  await reload();await click('[data-view=config]');
  assert.equal(await evaluate(`document.querySelector(${JSON.stringify(cx)}).checked`),true);
  assert.equal(await evaluate(`document.querySelector(${JSON.stringify(ke)}).checked`),false);
  for(const width of [1440,390]){
    await call('Emulation.setDeviceMetricsOverride',{width,height:1100,deviceScaleFactor:1,mobile:false});
    await evaluate('(()=>{const d=document.querySelector("#template-CX937");d.open=true;d.scrollIntoView({block:"start"});scrollBy(0,-160)})()');
    assert.ok(await evaluate('document.documentElement.scrollWidth<=innerWidth+1'));
    await writeFile(join(repo,`.tmp/flight-dedication-${width}.png`),Buffer.from((await call('Page.captureScreenshot',{format:'png'})).data,'base64'));
  }
  assert.deepEqual(await evaluate("fetch('/api/groups/B/workspace/backup').then(r=>r.json()).then(b=>b.groups.B)"),other);
  console.log('Per-flight dedication passed: real controls, independent modes, automatic scheduling, manual conflict, Excel, cloud reopen and B-group isolation.');
}
