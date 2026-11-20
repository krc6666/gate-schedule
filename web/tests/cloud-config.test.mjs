import assert from 'node:assert/strict';
import {test} from 'node:test';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
const compiled=await build({entryPoints:[fileURLToPath(new URL('../src/cloud-backend.ts',import.meta.url))],bundle:true,write:false,format:'esm',platform:'browser'});
const {cloudConfiguration,createCloudBackend}=await import(`data:text/javascript;base64,${Buffer.from(compiled.outputFiles[0].text).toString('base64')}`);

test('missing cloud login upgrade is explained and never falls back to old workspace RPCs',async t=>{
  const requests=[];
  t.mock.method(globalThis,'fetch',async(url)=>{
    requests.push(String(url));
    return new Response(JSON.stringify({code:'PGRST202',message:'Function not found'}),{status:404,headers:{'Content-Type':'application/json'}});
  });
  const backend=createCloudBackend({url:'https://example.supabase.co',key:'sb_publishable_example'},'dev-launch-test-session');
  t.after(()=>backend.client.auth.stopAutoRefresh());
  await assert.rejects(backend.acquireSession('dev-launch-test-session'),/云端尚未安装登录审批更新/);
  assert.equal(requests.length,1);
  assert.match(requests[0],/gate_acquire_session$/);
});
test('only a public Supabase configuration is accepted without echoing rejected secrets',()=>{
  assert.equal(cloudConfiguration({}),null);
  const config=key=>({VITE_SUPABASE_URL:'https://example.supabase.co',VITE_SUPABASE_PUBLISHABLE_KEY:key});
  assert.equal(cloudConfiguration(config('sb_publishable_example')).url,'https://example.supabase.co');
  const jwt=role=>'e30.'+Buffer.from(JSON.stringify({role})).toString('base64url')+'.test';
  assert.ok(cloudConfiguration(config(jwt('anon'))));
  for(const secret of ['sb_secret_private',jwt('service_role'),'database-password'])assert.throws(()=>cloudConfiguration(config(secret)),e=>!e.message.includes(secret));
  for(const url of ['http://example.com','https://user:password@example.com','https://example.com/path','https://example.com?key=x'])assert.throws(()=>cloudConfiguration({...config('sb_publishable_example'),VITE_SUPABASE_URL:url}));
});
