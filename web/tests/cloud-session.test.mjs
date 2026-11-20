import assert from 'node:assert/strict';
import {test} from 'node:test';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
const compiled=await build({entryPoints:[fileURLToPath(new URL('../src/cloud-session.ts',import.meta.url))],bundle:true,write:false,format:'esm',platform:'node'});
const {CloudSession}=await import(`data:text/javascript;base64,${Buffer.from(compiled.outputFiles[0].text).toString('base64')}`);
function fixture(){
  let held=false, request={id:'request-1234567890',status:'pending'};const events=[],calls=[];
  const backend={acquireSession:async()=>{if(!held)throw Error('SESSION_ACTIVE')},requestSession:async()=>request,pollSession:async()=>request,touchSession:async()=>{},releaseSession:async()=>calls.push('release'),cancelRequest:async()=>calls.push('cancel'),decideRequest:async(_s,_r,allow)=>calls.push(allow?'allow':'deny')};
  const session=new CloudSession(backend,'device-1234567890',s=>events.push({...s}));
  return {session,backend,events,calls,hold:()=>held=true,status:value=>request={...request,status:value}};
}
test('waiting never grants permission; refusal and timeout keep applicant outside',async()=>{
 for(const status of ['rejected','expired']){const f=fixture();try{await f.session.connect();assert.equal(f.session.state.phase,'waiting');f.status(status);await f.session.poll();assert.equal(f.session.state.phase,'blocked')}finally{await f.session.stop()}}
});
test('approval reacquires server lease before entering and ignores cancelled late poll',async()=>{
 const f=fixture();await f.session.connect();f.status('approved');f.hold();await f.session.poll();assert.equal(f.session.state.phase,'active');await f.session.stop();
 const g=fixture();await g.session.connect();let resolve;g.backend.pollSession=()=>new Promise(r=>resolve=r);const p=g.session.poll();await g.session.stop();resolve({id:'request-1234567890',status:'approved'});await p;assert.equal(g.session.state.phase,'stopped');
});
test('refusal does not save; failed preparation cannot hand off; success ends old session',async()=>{
 const f=fixture();f.hold();await f.session.connect();await f.session.poll();let saves=0;
 await f.session.decide(false,async()=>{saves++;return false});assert.equal(saves,0);assert.deepEqual(f.calls,['deny']);
 await f.session.poll();await f.session.decide(true,async()=>false);assert.equal(f.session.state.phase,'active');assert.deepEqual(f.calls,['deny']);
 await f.session.decide(true,async()=>true);assert.equal(f.session.state.phase,'transferred');assert.deepEqual(f.calls,['deny','allow']);await f.session.stop();
});
test('heartbeat failure pauses without discarding state, resume validates lease, slow polls do not overlap',async()=>{
 const f=fixture();f.hold();await f.session.connect();f.backend.touchSession=async()=>{throw Error('offline')};await f.session.heartbeat();assert.equal(f.session.state.phase,'paused');await f.session.resume();assert.equal(f.session.state.phase,'active');
 let resolve,count=0;f.backend.pollSession=()=>{count++;return new Promise(r=>resolve=r)};const p=f.session.poll();await f.session.poll();assert.equal(count,1);resolve(null);await p;await f.session.stop();
});
test('regular polling does not postpone the thirty-second heartbeat',async t=>{
 t.mock.timers.enable({apis:['setInterval','setTimeout']});
 const f=fixture();let touches=0;f.backend.touchSession=async()=>{touches++};f.hold();await f.session.connect();
 try{for(let i=0;i<10;i++){t.mock.timers.tick(3000);await new Promise(r=>setImmediate(r))}assert.equal(touches,1)}finally{await f.session.stop()}
});
test('stopping during handoff preparation prevents a late decision',async()=>{
 const f=fixture();f.hold();await f.session.connect();await f.session.poll();let done;const deciding=f.session.decide(true,()=>new Promise(r=>done=r));await f.session.stop();done(true);await deciding;assert.equal(f.session.state.phase,'stopped');assert.deepEqual(f.calls,['release']);
});
