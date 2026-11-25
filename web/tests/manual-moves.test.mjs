import assert from 'node:assert/strict';
import {test} from 'node:test';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';

const bundle=await build({entryPoints:[fileURLToPath(new URL('../src/manual-moves.ts',import.meta.url))],bundle:true,write:false,format:'esm',target:'es2022'});
const {ManualMoves}=await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}};
const move={memberId:'a',sourceTaskId:'one',targetTaskId:'two',targetMemberId:'b'};
function fixture(){
  const initial={revision:7,plans:{p:{draft:[{taskId:'one',memberId:'a'},{taskId:'one',memberId:'partner'},{taskId:'two',memberId:'b'}],memberIds:{甲:'a',乙:'b'},day:{same:true},adopted:{same:true},issues:[{code:'old'}]},history:{unchanged:true}}};
  let view=initial;const calls=[],events=[];
  const queue=new ManualMoves({current:()=>view,save:(planId,move,revision)=>{const wait=deferred();calls.push({planId,move,revision,...wait});return wait.promise},changed:(state,pending,error)=>{view=state;events.push({pending,error})}});
  return {queue,initial,calls,events,view:()=>view};
}
test('松手立即投影互换，保存尚未返回时保留原正式、搭档和问题，连续操作顺序落盘',async()=>{
  const f=fixture();const first=f.queue.add('p',move);
  assert.equal(f.queue.pending,true);
  assert.ok(f.view().plans.p.draft.some(a=>a.taskId==='two'&&a.memberId==='a'));
  assert.equal(f.view().plans.p.adopted,f.initial.plans.p.adopted);
  assert.equal(f.view().plans.p.day,f.initial.plans.p.day);
  assert.deepEqual(f.initial.plans.p.draft[0],{taskId:'one',memberId:'a'});
  assert.ok(f.view().plans.p.draft.some(a=>a.memberId==='partner'));
  const confirmed={...f.view(),revision:8};
  const second=f.queue.add('p',{...move,memberId:'b',targetMemberId:'a'});
  assert.equal(f.calls.length,1);
  assert.ok(f.view().plans.p.draft.some(a=>a.taskId==='one'&&a.memberId==='a'));
  f.calls[0].resolve(confirmed);assert.equal(await first,true);
  assert.equal(f.calls[1].revision,8);
  const last={...f.view(),revision:9};f.calls[1].resolve(last);
  assert.equal(await second,true);assert.equal(f.queue.pending,false);
  assert.equal(f.view(),last);
  assert.equal(f.view().plans.history,f.initial.plans.history);
});
test('后续保存失败只撤回未保存操作，之前成功的互换和冲突提示保留',async()=>{
  const f=fixture();const first=f.queue.add('p',move);const saved={...f.view(),revision:8,plans:{...f.view().plans,p:{...f.view().plans.p,issues:[{code:'ineligible'}]}}};
  f.calls[0].resolve(saved);await first;
  const second=f.queue.add('p',{...move,memberId:'b',targetMemberId:'a'});
  const third=f.queue.add('p',move);
  f.calls[1].reject(Error('disk full'));
  assert.equal(await second,false);assert.equal(await third,false);
  assert.equal(f.view(),saved);assert.equal(f.events.at(-1).error,'disk full');
  assert.equal(f.calls.length,2);assert.equal(f.queue.pending,false);
});
test('零需求临时格的立即显示、来源移除和名单追加，不改岗位人数',async()=>{
  const f=fixture();f.initial.plans.p.cellDrafts={temp:'乙'};
  const saved=f.queue.add('p',{memberId:'a',sourceTaskId:'one',targetCellId:'temp'});
  assert.equal(f.view().plans.p.cellDrafts.temp,'乙 / 甲');
  assert.equal(f.view().plans.p.draft.some(a=>a.taskId==='one'&&a.memberId==='a'),false);
  f.calls[0].reject(Error('reject'));await saved;
  assert.equal(f.view(),f.initial);
});
test('临时格只交换选中姓名，其余搭档顺序在预览与保存之间一致',async()=>{
  const f=fixture();f.initial.plans.p.cellDrafts={temp:'乙 / 同事'};
  const pending=f.queue.add('p',{memberId:'a',sourceTaskId:'one',targetCellId:'temp',targetMemberId:'b'});
  assert.equal(f.view().plans.p.cellDrafts.temp,'甲 / 同事');
  assert.ok(f.view().plans.p.draft.some(a=>a.taskId==='one'&&a.memberId==='b'));
  f.calls[0].resolve({...f.view(),revision:8});assert.equal(await pending,true);
});
