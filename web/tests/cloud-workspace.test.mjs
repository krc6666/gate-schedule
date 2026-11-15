import assert from 'node:assert/strict';
import {test} from 'node:test';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
const compiled = await build({entryPoints:[fileURLToPath(new URL('../src/cloud-workspace.ts', import.meta.url))],bundle:true,write:false,format:'esm',platform:'node'});
const {CloudWorkspace} = await import(`data:text/javascript;base64,${Buffer.from(compiled.outputFiles[0].text).toString('base64')}`);
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {resolve,reject,promise}};
function fixture(){
  const pending=deferred();let writes=0;const alerts=[];
  const cloud=new CloudWorkspace(4,async()=>({root:'full'}),async(expected,documents)=>{writes++;assert.equal(expected,4);assert.deepEqual(documents,{root:'full'});return pending.promise},()=>{},message=>alerts.push(message));
  return {cloud,pending,writes:()=>writes,alerts};
}
test('only server acknowledgement advances cloud version and success time',async()=>{
  const {cloud,pending,writes}=fixture();cloud.changed();
  const saving=cloud.save(async()=>true);assert.equal(cloud.saving,true);assert.equal(cloud.label,'正在保存到云端...');
  assert.equal(await cloud.save(async()=>true),false);await new Promise(r=>setImmediate(r));assert.equal(writes(),1);
  assert.equal(cloud.savedAt,null);assert.equal(cloud.version,4);
  pending.resolve(5);assert.equal(await saving,true);assert.equal(cloud.version,5);assert.ok(cloud.savedAt instanceof Date);assert.equal(cloud.needsLeaveWarning,false);
});
test('edits after snapshot survive late successful receipt and dominate success label',async()=>{
  const {cloud,pending}=fixture();cloud.changed();const saving=cloud.save(async()=>true);await new Promise(r=>setImmediate(r));cloud.changed();pending.resolve(5);await saving;
  assert.equal(cloud.label,'有修改未上传');assert.equal(cloud.needsLeaveWarning,true);
});
test('failure keeps edits/version, alerts and retry remains available',async()=>{
  const {cloud,pending,alerts}=fixture();cloud.changed();const saving=cloud.save(async()=>true);pending.reject(Error('版本冲突'));assert.equal(await saving,false);
  assert.equal(cloud.version,4);assert.equal(cloud.savedAt,null);assert.equal(cloud.label,'保存失败，点击重试');assert.equal(cloud.needsLeaveWarning,true);assert.equal(alerts.length,1);
});
test('session disposal suppresses late receipts and failure alerts',async()=>{
  const {cloud,pending,alerts}=fixture();cloud.changed();const saving=cloud.save(async()=>true);await new Promise(r=>setImmediate(r));cloud.dispose();pending.resolve(5);assert.equal(await saving,false);assert.equal(cloud.version,4);assert.equal(cloud.savedAt,null);assert.equal(alerts.length,0);
});
test('validation failure never uploads or marks saved; read never fabricates save time',async()=>{
  const {cloud,writes}=fixture();assert.equal(cloud.savedAt,null);assert.equal(cloud.label,'已读取');cloud.changed();assert.equal(await cloud.save(async()=>false),false);assert.equal(writes(),0);assert.equal(cloud.needsLeaveWarning,true);
});
