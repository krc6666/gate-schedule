import assert from 'node:assert/strict';

export async function dragSpeedAcceptance({evaluate,click,until}) {
  const state=()=>evaluate("fetch('/api/groups/A/workspace').then(r=>r.json())");
  let initial=await state(),pid=initial.selectedPlanId,p=initial.plans[pid];
  assert.ok(p?.draft.length>1,'需要完整班表计时副本');
  await click('[data-view=schedule]');
  await until(()=>evaluate('!!document.querySelector(".wb-matrix")'),'拖动计时班表');
  const [a,b]=p.draft.flatMap(a=>p.draft.map(b=>[a,b])).find(([a,b])=>a.taskId!==b.taskId&&a.memberId!==b.memberId&&
    Object.values(p.schedule.matrix).some(columns=>Object.values(columns).some(s=>s.length===1&&s[0].task_id===a.taskId))&&
    Object.values(p.schedule.matrix).some(columns=>Object.values(columns).some(s=>s.length===1&&s[0].task_id===b.taskId)));
  const selector=(task,member)=>`[data-task-ids='["${task}"]'][data-drag-member="${member}"]`;
  const measurements=[];
  for(let index=0;index<3;index++) {
    const source=selector(a.taskId,index%2?b.memberId:a.memberId),target=selector(b.taskId,index%2?a.memberId:b.memberId);
    const previous=(await state()).revision;
    await evaluate(`(()=>{const a=document.querySelector(${JSON.stringify(source)}),b=document.querySelector(${JSON.stringify(target)}),dt=new DataTransfer();if(!a||!b)throw Error('计时拖动元素缺失');window.__swapStart=performance.now();a.dispatchEvent(new DragEvent('dragstart',{bubbles:true,dataTransfer:dt}));b.dispatchEvent(new DragEvent('drop',{bubbles:true,dataTransfer:dt,cancelable:true}));a.dispatchEvent(new DragEvent('dragend',{bubbles:true,dataTransfer:dt}));})()`);
    await until(()=>evaluate(`!!document.querySelector(${JSON.stringify(selector(a.taskId,index%2?a.memberId:b.memberId))}) && !document.querySelector('#undo-move').disabled`),'交换后页面可继续操作');
    measurements.push(await evaluate('performance.now()-window.__swapStart'));
    const after=await state();assert.ok(after.revision>previous);
  }
  console.log(JSON.stringify({dragSwapMs:measurements,plans:Object.keys(initial.plans).length,tasks:p.schedule.tasks.length}));
}
