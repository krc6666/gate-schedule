import assert from 'node:assert/strict';
import {test} from 'node:test';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
const compiled=await build({entryPoints:[fileURLToPath(new URL('../src/cloud-backend.ts',import.meta.url))],bundle:true,write:false,format:'esm',platform:'browser'});
const {cloudConfiguration}=await import(`data:text/javascript;base64,${Buffer.from(compiled.outputFiles[0].text).toString('base64')}`);
test('only a public Supabase configuration is accepted without echoing rejected secrets',()=>{
  assert.equal(cloudConfiguration({}),null);
  const config=key=>({VITE_SUPABASE_URL:'https://example.supabase.co',VITE_SUPABASE_PUBLISHABLE_KEY:key});
  assert.equal(cloudConfiguration(config('sb_publishable_example')).url,'https://example.supabase.co');
  const jwt=role=>'e30.'+Buffer.from(JSON.stringify({role})).toString('base64url')+'.test';
  assert.ok(cloudConfiguration(config(jwt('anon'))));
  for(const secret of ['sb_secret_private',jwt('service_role'),'database-password'])assert.throws(()=>cloudConfiguration(config(secret)),e=>!e.message.includes(secret));
  for(const url of ['http://example.com','https://user:password@example.com','https://example.com/path','https://example.com?key=x'])assert.throws(()=>cloudConfiguration({...config('sb_publishable_example'),VITE_SUPABASE_URL:url}));
});
