import assert from 'node:assert/strict';

export async function immediateDragAcceptance({evaluate,click,until,call}) {
  const state=()=>evaluate("fetch('/api/groups/A/workspace').then(r=>r.json())");
  const initial=await state(),pid=initial.selectedPlanId,p=initial.plans[pid];
  await click('[data-view=schedule]');
  const singleton=id=>Object.values(p.schedule.matrix).some(cols=>Object.values(cols).some(s=>s.length===1&&s[0].task_id===id));
  const [a,b]=p.draft.flatMap(a=>p.draft.map(b=>[a,b])).find(([a,b])=>a.taskId!==b.taskId&&a.memberId!==b.memberId&&singleton(a.taskId)&&singleton(b.taskId)&&!p.draft.some(x=>x.taskId===a.taskId&&x.memberId===b.memberId)&&!p.draft.some(x=>x.taskId===b.taskId&&x.memberId===a.memberId));
  const chip=(task,member)=>`[data-task-ids='["${task}"]'][data-drag-member="${member}"]`;
  const drag=(from,to)=>evaluate(`(()=>{const a=document.querySelector(${JSON.stringify(from)}),b=document.querySelector(${JSON.stringify(to)}),dt=new DataTransfer();if(!a||!b)throw Error('找不到拖动姓名');const started=performance.now();a.dispatchEvent(new DragEvent('dragstart',{bubbles:true,dataTransfer:dt}));b.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:dt}));a.dispatchEvent(new DragEvent('dragend',{bubbles:true,dataTransfer:dt}));return performance.now()-started})()`);
  await evaluate(`(()=>{window.__moveReleases=[];window.__moveActions=[];const original=window.fetch;window.fetch=async(input,init)=>{const url=String(input);let command;try{command=JSON.parse(init?.body??'{}')}catch{}if(url.includes('/workspace/command')&&command.action==='move-assignment'){window.__moveActions.push(command.action);if(window.__failMove){window.__failMove=false;return Response.json({detail:'模拟保存失败'},{status:409})}await new Promise(resolve=>window.__moveReleases.push(resolve))}if(url.includes('/workspace/runs')&&init?.method==='POST')throw Error('手动拖动不得启动自动排班');return original(input,init)}})()`);
  const immediateMs=await drag(chip(a.taskId,a.memberId),chip(b.taskId,b.memberId));
  assert.ok(await evaluate(`!!document.querySelector(${JSON.stringify(chip(a.taskId,b.memberId))})`),'保存请求尚未返回时，姓名必须立即互换');
  assert.equal((await state()).revision,initial.revision,'显示预览不能假装后端已经保存');
  assert.ok(await evaluate('document.querySelector("#archive-only").disabled'),'检查未完成不能归档');
  assert.ok(await evaluate('document.querySelector("#schedule-issues").textContent.includes("正在保存")'),'预览不能显示已经检查通过');
  assert.ok(await evaluate('(()=>{const event=new Event("beforeunload",{cancelable:true});window.dispatchEvent(event);return event.defaultPrevented})()'),'待保存队列必须触发离开保护');
  await drag(chip(a.taskId,b.memberId),chip(b.taskId,a.memberId));
  assert.ok(await evaluate(`!!document.querySelector(${JSON.stringify(chip(a.taskId,a.memberId))})`),'保存中仍可继续第二次拖换');
  assert.equal(await evaluate('window.__moveActions.length'),1,'请求按顺序保存，不并发覆盖');
  await evaluate('window.__moveReleases.shift()()');
  await until(()=>evaluate('window.__moveReleases.length===1'),'第二次互换开始保存');
  await evaluate('window.__moveReleases.shift()()');
  await until(()=>evaluate('!document.querySelector("#undo-move").disabled'),'连续互换保存及检查完成');
  let saved=await state();
  assert.equal(saved.revision,initial.revision+2);
  const sorted=values=>values.map(x=>x.taskId+':'+x.memberId).sort();
  assert.deepEqual(sorted(saved.plans[pid].draft),sorted(p.draft),'只交换指定人员，其他安排保留');
  assert.deepEqual(saved.plans[pid].adopted,p.adopted);
  assert.deepEqual(saved.plans[pid].issues,(await state()).plans[pid].issues);
  assert.ok(await evaluate('!document.querySelector("#schedule-issues").textContent.includes("正在保存")'),'保存完成恢复正式检查结果');
  // A prior response arriving during a pointer gesture must not remove the
  // handle, cancel the gesture, or make its locally displayed revision stale.
  await drag(chip(a.taskId,a.memberId),chip(b.taskId,b.memberId));
  await evaluate('document.querySelector(".manual-feedback")?.remove();document.querySelector(".wb-matrix").scrollIntoView({block:"center"})');
  const point=selector=>evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)}),r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
  const from=await point(chip(a.taskId,b.memberId)+' [data-drag-handle]'),to=await point(chip(b.taskId,a.memberId));
  await call('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',buttons:1,clickCount:1,...from});
  await call('Input.dispatchMouseEvent',{type:'mouseMoved',buttons:1,x:from.x+12,y:from.y});
  assert.ok(await evaluate('!!document.querySelector(".schedule-drag-preview")'));
  const beforeReply=saved.revision;
  await evaluate('window.__moveReleases.shift()()');
  await until(async()=>((await state()).revision>beforeReply),'前一互换保存回执');
  assert.ok(await evaluate('!!document.querySelector(".schedule-drag-preview")'),'保存回执不能打断正在拖动的下一人');
  await call('Input.dispatchMouseEvent',{type:'mouseMoved',buttons:1,...to});
  await call('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',buttons:0,clickCount:1,...to});
  assert.ok(await evaluate(`!!document.querySelector(${JSON.stringify(chip(a.taskId,a.memberId))})`));
  await until(()=>evaluate('window.__moveReleases.length===1'),'后一次手柄交换进入保存');
  await evaluate('window.__moveReleases.shift()()');
  await until(()=>evaluate('!document.querySelector("#undo-move").disabled'),'手柄连续交换完成');
  saved=await state();assert.deepEqual(sorted(saved.plans[pid].draft),sorted(p.draft));
  await evaluate('window.__failMove=true');
  await drag(chip(a.taskId,a.memberId),chip(b.taskId,b.memberId));
  await until(()=>evaluate('document.querySelector(".app-message")?.textContent.includes("模拟保存失败")'),'失败提示');
  assert.ok(await evaluate(`!!document.querySelector(${JSON.stringify(chip(a.taskId,a.memberId))})`),'保存失败恢复最后已保存的位置');
  assert.equal((await state()).revision,saved.revision);
  assert.ok(await evaluate('window.__moveActions.every(action=>action==="move-assignment")'));
  console.log(`Immediate drag passed: display ${Math.round(immediateMs)} ms; held response, consecutive swaps, serial revisions, rollback, preserved assignments and zero automatic runs.`);
}
