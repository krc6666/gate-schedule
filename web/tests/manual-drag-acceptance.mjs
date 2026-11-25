import assert from 'node:assert/strict';
import {dedicatedCrossConfiguration} from './dedicated-cross-fixture.mjs';

// The same worker and UI used by dev.py, with an isolated simulated cloud account.
export async function manualDragAcceptance({evaluate,click,until,ready,save,login,released,call}) {
  const state=()=>evaluate("fetch('/api/groups/A/workspace').then(r=>r.json())");
  const current=async()=>{const s=await state();return s.plans[s.selectedPlanId]};
  const command=async(action,payload={},planId)=>{
    const s=await state();
    const result=await evaluate(`fetch('/api/groups/A/workspace/command',{method:'POST',headers:{'Content-Type':'application/json','X-Gate-Rule-Schema':'flight-role-v2'},body:JSON.stringify(${JSON.stringify({expectedRevision:s.revision,action,payload,planId})})}).then(async r=>({status:r.status,value:await r.json()}))`);
    assert.equal(result.status,200,JSON.stringify(result.value));return result.value;
  };
  const reload=async()=>{
    await save();await click('.cloud-more summary');await click('#cloud-logout');
    await until(released,'释放模拟会话');await evaluate('location.reload();true');await login();await ready();
    await evaluate('window.confirm=()=>true');await click('[data-view=schedule]');
    await until(()=>evaluate('!!document.querySelector(".wb-matrix")'),'云重开班表');
  };
  const mutate=async(action)=>{
    const revision=(await state()).revision;await action();
    await until(async()=>{
      const error=await evaluate('document.querySelector(".run-error")?.textContent');if(error)throw Error(error);
      return (await state()).revision>revision&&await evaluate('!document.querySelector(".schedule-progress[data-run-status=running]") && document.querySelector(".save-state")?.textContent !== "正在保存…"');
    },'手动拖放保存',15000);
  };
  const drag=(from,to)=>evaluate(`(()=>{const a=document.querySelector(${JSON.stringify(from)}),b=document.querySelector(${JSON.stringify(to)}),dt=new DataTransfer();if(!a||!b)throw Error('拖动元素不存在');a.dispatchEvent(new DragEvent('dragstart',{bubbles:true,dataTransfer:dt}));b.dispatchEvent(new DragEvent('dragover',{bubbles:true,dataTransfer:dt,cancelable:true}));b.dispatchEvent(new DragEvent('drop',{bubbles:true,dataTransfer:dt,cancelable:true}));a.dispatchEvent(new DragEvent('dragend',{bubbles:true,dataTransfer:dt}));})()`);
  const input=dedicatedCrossConfiguration(),source='入境申报',temp='特服';
  input.handoffPreferences=[];input.day.flowPreferences=[];
  input.day.flights=input.day.flights.slice(0,1);input.day.roleNames=[source,temp];
  input.day.roster=['甲','乙'];
  input.day.memberConfigs=input.day.roster.map(memberName=>({memberName,eligibleRoleNames:[source,temp]}));
  input.day.memberAvailability=input.day.roster.map(memberName=>({memberName,participating:true}));
  input.day.roleConfigs=[{roleName:source,requiredCount:2,workMode:'arrival'},{roleName:temp,requiredCount:0,workMode:'continuous'}];
  input.day.cells=input.day.roleNames.map((roleName,index)=>({id:index?'temp':'source',flightId:'A',flightNo:'CX937',roleName,rawValue:'',internalNames:[],externalLabels:[],semantics:'empty',requiredCountOverride:index?0:2}));
  const imported=await command('import-complete-configuration',input);
  for(const role of imported.configuration.roleConfigs)if(!input.day.roleNames.includes(role.roleName))await command('save-role',{...role,requiredCount:0});
  await reload();
  await click('#solve-draft');await until(()=>evaluate('!!document.querySelector("#confirm-prepare")'),'准备自动排班');
  await click('#prepare-keep-day-counts');await click('#prepare-keep-day-config');
  await mutate(()=>click('#confirm-prepare'));
  let p=await current();
  assert.equal(p.draft.length,2);
  assert.equal(p.schedule.tasks.some(t=>t.roleId===temp),false,'0需求岗位不能自动派人，即使所有人都有资格');
  const baseline=structuredClone(p),task=p.schedule.tasks[0],member=p.draft[0].memberId,partner=p.draft[1].memberId;
  const name=Object.keys(p.memberIds).find(n=>p.memberIds[n]===member);
  const from=`td[data-cell-id=source] [data-drag-member="${member}"]`,target='td[data-cell-id=temp]';
  await evaluate('document.querySelector(".schedule-progress:not([data-run-status=running])")?.remove();document.querySelector(".wb-matrix").scrollIntoView({block:"center"})');
  const point=selector=>evaluate(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});const r=el.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
  const a=await point(from+' [data-drag-handle]'),b=await point(target+' .schedule-cell');
  await call('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',buttons:1,clickCount:1,...a});
  await call('Input.dispatchMouseEvent',{type:'mouseMoved',buttons:1,x:a.x+15,y:a.y});
  await call('Input.dispatchMouseEvent',{type:'mouseMoved',buttons:1,...b});
  await mutate(()=>call('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',buttons:0,clickCount:1,...b}));
  p=await current();
  assert.equal(p.cellDrafts.temp,name);assert.deepEqual(p.day,baseline.day);
  assert.deepEqual(p.draft,[{taskId:task.id,memberId:partner}]);
  assert.ok(p.issues.some(i=>i.code==='pending_cell'));
  await mutate(()=>click('#undo-move'));assert.deepEqual((await current()).draft,baseline.draft);
  // Exercise the same pointer handling through Chrome touch events as well.
  await evaluate('document.querySelector(".schedule-progress:not([data-run-status=running])")?.remove();document.querySelector(".wb-matrix").scrollIntoView({block:"center"})');
  const touchFrom=await point(from+' [data-drag-handle]'),touchTo=await point(target+' .schedule-cell');
  await call('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{...touchFrom,id:1}]});
  await call('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:touchFrom.x+15,y:touchFrom.y,id:1}]});
  await call('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{...touchTo,id:1}]});
  await mutate(()=>call('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]}));
  assert.equal((await current()).cellDrafts.temp,name,'触屏事件也能拖到0需求格');
  await mutate(()=>click('#undo-move'));
  await mutate(()=>drag(from,target));await reload();
  assert.equal((await current()).cellDrafts.temp,name,'云保存、退出、登录后人名保留');
  await mutate(()=>drag(`${target} [data-drag-member="${member}"]`,'td[data-cell-id=source]'));
  assert.equal((await current()).cellDrafts.temp,undefined);
  assert.equal((await current()).draft.length,2,'可从零需求格拖回，搭档保留');
  await mutate(()=>drag(`[data-palette-member="${member}"]`,target));
  await mutate(()=>drag(`[data-palette-member="${partner}"]`,target));
  assert.equal((await current()).cellDrafts.temp.split(' / ').length,2,'名单追加不覆盖原人名');
  await mutate(()=>click('#toggle-schedule-layout'));
  assert.equal(await evaluate(`document.querySelectorAll('${target} [data-drag-member]').length`),2);
  // Move to a real assignment in the second layout, then restore both names.
  await command('edit-cell',{cellId:'source',text:'乙'},(await state()).selectedPlanId);
  await reload();
  const assigned=(await current()).draft[0].memberId;
  const moving=assigned===member?partner:member;
  await mutate(()=>drag(`${target} [data-drag-member="${moving}"]`,'td[data-cell-id=source]'));
  assert.equal((await current()).cellDrafts.temp.split(' / ').length,1,'原保障表布局也能拖回且保留另一人');
  await mutate(()=>click('#undo-move'));
  await mutate(()=>click(`${target} [data-clear-position]`));
  assert.equal((await current()).cellDrafts.temp,undefined);
  assert.deepEqual((await current()).day,baseline.day,'手动全程没有启用任何自动需求');
  console.log('Manual zero-demand drag passed: pointer, touch, palette, move back, undo, two layouts and cloud reopen; automatic zero demand stays empty.');
}
