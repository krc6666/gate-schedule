import assert from 'node:assert/strict';
import {dedicatedCrossConfiguration} from './dedicated-cross-fixture.mjs';

export async function savedDayContinuationAcceptance({evaluate,click,value,until,save}) {
  const state=()=>evaluate("fetch('/api/groups/A/workspace').then(r=>r.json())");
  const source='入境申报',target='登机核验';
  const mutate=async action=>{const revision=(await state()).revision;await action();await until(async()=>{const error=await evaluate('document.querySelector(".run-error")?.textContent');if(error)throw Error(error);return (await state()).revision>revision&&await evaluate('!document.querySelector(".schedule-progress[data-run-status=running]")')},'整日排班完成')};
  const check=async label=>{
    const s=await state(),p=s.plans[s.selectedPlanId];
    const task=(flight,role)=>p.schedule.tasks.find(t=>p.day.flights.find(f=>f.id===t.flightId)?.flightNo===flight&&t.roleId===role);
    const from=task('CX937',source),to=task('KE0166',target);
    assert.ok(from&&to,label+'：两班任务存在');
    const ids=p.draft.filter(a=>a.taskId===from.id).map(a=>a.memberId);
    assert.ok(p.draft.some(a=>a.taskId===to.id&&ids.includes(a.memberId)),label+'：申报人员实际接KE核验');
    return p;
  };
  await click('[data-view=rules]');
  const index=await evaluate(`[...document.querySelectorAll('[data-flow-common] [data-flow-source]')].find(e=>e.value===${JSON.stringify(source)}).closest('[data-flow-rule]').dataset.flowRule`);
  const row=`[data-flow-rule="${index}"]`;
  await value(row+' [data-flow-target-row="1"] [data-flow-target]',target);
  await mutate(()=>click('#save-rules'));await click('[data-view=schedule]');
  await click('#solve-draft');await mutate(()=>click('#confirm-prepare'));await check('整日重排');await save();
  await click('#archive-next');await value('#prepare-date','2026-08-26');await mutate(()=>click('#confirm-prepare'));
  await check('整日归档后天');await save();
  console.log('Full saved-day cloud UI continuation passed; source person was not fixed.');
}

export async function partTimeDayAcceptance({evaluate,click,value,until,save,ready,login,released}) {
  const state=()=>evaluate("fetch('/api/groups/A/workspace').then(r=>r.json())");
  const initial=await state(),preferences=structuredClone(initial.handoffPreferences);
  const mutate=async action=>{const revision=(await state()).revision;await action();await until(async()=>{const error=await evaluate('document.querySelector(".run-error")?.textContent');if(error)throw Error(error);return (await state()).revision>revision&&await evaluate('!document.querySelector(".schedule-progress[data-run-status=running]")')},'实际整日兼职排班完成')};
  const check=async label=>{
    const s=await state(),p=s.plans[s.selectedPlanId];
    const task=(flight,role)=>p.schedule.tasks.find(t=>p.day.flights.find(f=>f.id===t.flightId)?.flightNo===flight&&t.roleId===role);
    const from=task('CX937','入境申报'),to=task('KE0166','登机人证核对/队伍秩序维护');
    const ids=p.draft.filter(a=>a.taskId===from.id).map(a=>a.memberId);
    assert.ok(p.draft.some(a=>a.taskId===to.id&&ids.includes(a.memberId)),label+'：申报人员实际接KE秩序');
    assert.ok(p.issues.some(i=>i.code==='coverage_shortfall'),label+'：其他缺口仍报警');
    assert.ok(!p.issues.some(i=>i.code!=='coverage_shortfall'&&i.code!=='coverage_order_unmet'),JSON.stringify(p.issues));
    assert.deepEqual(s.handoffPreferences,preferences,'不替换原目标或改规则');
  };
  await click('[data-view=rules]');
  assert.match(await evaluate('document.querySelector("#handoff-preference-rules > summary").textContent'),/接续兼职/);
  await click('[data-view=schedule]');await click('#solve-draft');await mutate(()=>click('#confirm-prepare'));
  await check('26日原配置整日重排');await save();
  await click('#cloud-logout');await until(released,'释放模拟会话');await evaluate('location.reload();true');await login();await ready();
  await evaluate('window.alerts=[];window.alert=m=>window.alerts.push(m);window.confirm=()=>true');await check('云重读');
  await click('[data-view=schedule]');await click('#archive-next');await value('#prepare-date','2026-08-28');await mutate(()=>click('#confirm-prepare'));
  await check('归档后下一班');await save();
  console.log('Actual full-day part-time acceptance passed with unchanged original configuration.');
}

// Runs in the actual cloud browser/worker, against an isolated mock workspace.
export async function continuationAcceptance({evaluate,click,value,until,ready,save,login,released}) {
  const state=()=>evaluate("fetch('/api/groups/A/workspace').then(r=>r.json())");
  const command=async(action,payload={},planId)=>{
    const s=await state();
    const response=await evaluate(`fetch('/api/groups/A/workspace/command',{method:'POST',headers:{'Content-Type':'application/json','X-Gate-Rule-Schema':'flight-role-v1'},body:JSON.stringify(${JSON.stringify({expectedRevision:s.revision,action,payload,planId})})}).then(async r=>({status:r.status,value:await r.json()}))`);
    assert.equal(response.status,200,JSON.stringify(response.value));return response.value;
  };
  const reload=async()=>{await save();await evaluate('window.confirm=()=>true');await click('#cloud-logout');await until(()=>evaluate('document.body.textContent.includes("会话已结束")'),'模拟页面退出');await until(released,'释放模拟会话');await evaluate('location.reload();true');await login();await ready();await evaluate('window.alerts=[];window.alert=m=>window.alerts.push(m);window.confirm=()=>true')};
  const mutate=async(action)=>{
    const before=(await state()).revision;await action();
    await until(async()=>{const error=await evaluate('document.querySelector(".run-error")?.textContent');if(error)throw Error(error);return ((await state()).revision>before)&&await evaluate('!document.querySelector(".schedule-progress[data-run-status=running]")')},'接续操作完成');
  };
  const source='入境申报',normal='登机核验',order='登机人证核对/队伍秩序维护';
  const check=async(target,label)=>{
    const s=await state(),p=s.plans[s.selectedPlanId],worker=s.people.find(p=>p.name==='跨班验证员').id;
    const assigned=role=>p.schedule.tasks.filter(t=>t.roleId===role&&p.draft.some(a=>a.taskId===t.id&&a.memberId===worker));
    assert.equal(assigned(source).length,1,label+'：来源人员存在');
    assert.equal(assigned(target).length,1,label+'：同一人实际接到目标');
    assert.ok(!p.issues.some(i=>i.severity==='error'&&i.code!=='coverage_shortfall'),JSON.stringify(p.issues));
    return p;
  };
  for(const [index,target] of [normal,order,normal].entries()){
    const date=n=>new Date(Date.UTC(2058,7,n)).toISOString().slice(0,10);
    const input=dedicatedCrossConfiguration();
    // Start without any continuation; the complete rule is added using UI controls.
    input.handoffPreferences=[];input.day.flowPreferences=[];
    input.balanceSettings={flightCountGap:null,priorityEnabled:false,priorityRoleNames:[],priorityAdjustments:[],coveragePriorities:[{roleName:order,priority:'leave-empty'},{roleName:normal,priority:'protect'}]};
    input.day.serviceDate=date(24+index*4);
    input.day.roleNames=[source,normal,order];
    input.day.roleConfigs=input.day.roleNames.map(roleName=>({roleName,requiredCount:0,workMode:roleName===source?'arrival':'boarding',exclusiveSameFlight:roleName!==order,concurrent:roleName===order}));
    input.day.memberConfigs[0].eligibleRoleNames=input.day.roleNames;
    input.day.cells=input.day.flights.flatMap(f=>input.day.roleNames.map(roleName=>({id:f.id+roleName,flightId:f.id,flightNo:f.flightNo,roleName,rawValue:'',internalNames:[],externalLabels:[],semantics:'empty',requiredCountOverride:Number(f.id==='A'?roleName===source:roleName===target||(index===2&&roleName===order))})));
    const imported=await command('import-complete-configuration',input);
    for(const role of imported.configuration.roleConfigs)if(!input.day.roleNames.includes(role.roleName))await command('save-role',{...role,requiredCount:0});
    await reload();await click('[data-view=schedule]');await click('#solve-draft');await mutate(()=>click('#confirm-prepare'));
    await assert.rejects(()=>check(target,'未配置路线对照'),/来源人员存在|同一人实际接到目标/);
    await click('[data-view=rules]');await click('#add-flow-rule');
    const row='[data-flow-common] [data-flow-rule]:last-child';
    await value(row+' [data-flow-source]',source);
    const targets=index===2?[order,normal]:[target];
    for(const [n,role] of targets.entries()){
      await click(row+' [data-flow-add-target]');
      await value(row+` [data-flow-target-row="${n}"] [data-flow-target]`,role);
      await value(row+` [data-flow-target-row="${n}"] [data-flow-flight-scope]`,'later-overlap');
    }
    await mutate(()=>click('#save-rules'));
    assert.deepEqual((await state()).handoffPreferences[0].boardingRoles,targets);
    await click('[data-view=schedule]');await click('#solve-draft');
    await until(()=>evaluate('!!document.querySelector("#confirm-prepare")'),'重排确认');
    const expected=target;
    await mutate(()=>click('#confirm-prepare'));await check(expected,'同日重排');
    await save();await reload();await check(expected,'云端保存重读');
    await click('[data-view=schedule]');await click('#archive-next');
    await value('#prepare-date',date(26+index*4));
    await mutate(()=>click('#confirm-prepare'));const next=await check(expected,'归档生成下一班');
    assert.equal(next.day.serviceDate,date(26+index*4));
    if(index===2){const tasks=next.schedule.tasks.filter(t=>t.roleId===order);assert.ok(tasks.length);assert.ok(tasks.every(t=>!next.draft.some(a=>a.taskId===t.id)),'非留空核验覆盖优先，不能为兼职挤掉主要岗位');}
    await save();await reload();await check(expected,'下一班保存重读');
    console.log('Continuation cloud flow passed: '+targets.join(' / '));
  }
}
