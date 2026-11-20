import {PGlite} from '@electric-sql/pglite';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const admin='11111111-1111-4111-8111-111111111111',a='device-first-123456',b='device-second-12345',c='device-third-123456';
test('gate login approval: isolated server lease, old writes denied, rollback, CAS and data preserved',async t=>{
 const db=new PGlite();t.after(()=>db.close());
 await db.exec(`create role anon;create role authenticated;create schema auth;create table auth.users(id uuid primary key);create function auth.uid() returns uuid language sql stable as $$select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid$$;insert into auth.users values('${admin}');`);
 await db.exec(await readFile(new URL('../../supabase/workspace.sql',import.meta.url),'utf8'));
 await db.exec(`insert into public.gate_admin values(true,'${admin}');`);
 const group={stateVersion:2,timeVersion:2,revision:0,selectedPlanId:null,people:[],flightLibrary:[],handoffPreferences:[],migrationReport:[],plans:{},configuration:{roleConfigs:[]},displayPreferences:{},balanceSettings:{},teamLeaderSettings:{},halfRestSettings:{},relaxedShiftSettings:{},dutyRoster:{}};
 const docs={'group-workspaces-v1':{formatVersion:3,revision:0,activeGroupId:'A',shared:{flightTemplates:[],weeklyFlightPresets:{}},groups:{A:group,B:group}}};
 await db.exec(`set request.jwt.claim.sub='${admin}';`);
 await db.query('select public.gate_save_workspace(0,$1::jsonb)',[JSON.stringify(docs)]);
 const before=(await db.query('select row_to_json(w) as w from public.gate_workspace w')).rows[0].w;
 const upgrade=await readFile(new URL('../../supabase/202611190001_login_approval.sql',import.meta.url),'utf8');
 await assert.rejects(db.exec(upgrade.replace("notify pgrst,'reload schema';","select 1/0;")),/division by zero/);
 await db.exec('rollback');
 assert.equal((await db.query("select to_regclass('public.gate_editor_session') as name")).rows[0].name,null);
 assert.deepEqual((await db.query('select row_to_json(w) as w from public.gate_workspace w')).rows[0].w,before);
 await db.exec(upgrade);await db.exec(upgrade);
 await db.exec(await readFile(new URL('../../supabase/workspace.sql',import.meta.url),'utf8'));
 assert.deepEqual((await db.query('select row_to_json(w) as w from public.gate_workspace w')).rows[0].w,before);
 const rpc=async(name,...args)=>(await db.query(`select public.${name}(${args.map((_,i)=>'$'+(i+1)).join(',')}) as result`,args)).rows[0].result;
 await t.test('anonymous and non-admin cannot acquire; old RPCs and direct tables denied',async()=>{
  await db.exec('set role anon');await assert.rejects(rpc('gate_acquire_session',a),/permission denied/);
  await db.exec("reset role;set role authenticated;set request.jwt.claim.sub='22222222-2222-4222-8222-222222222222'");await assert.rejects(rpc('gate_acquire_session',a),/ADMIN_REQUIRED/);
  await db.exec(`set request.jwt.claim.sub='${admin}'`);
  await assert.rejects(rpc('gate_read_workspace'),/permission denied/);await assert.rejects(rpc('gate_save_workspace',1,JSON.stringify(docs)),/permission denied/);await assert.rejects(db.query('select * from public.gate_login_requests'),/permission denied/);
 });
 await t.test('refusal, duplicate requests, competing applicant, expiry and cancellation do not grant access',async()=>{
  await rpc('gate_acquire_session',a);await assert.rejects(rpc('gate_acquire_session',b),/SESSION_ACTIVE/);
  const id='request-refuse-123456';assert.equal((await rpc('gate_request_login',b,id)).status,'pending');assert.equal((await rpc('gate_request_login',b,id)).id,id);
  await assert.rejects(rpc('gate_request_login',c,'request-other-123456'),/SESSION_REQUEST_BUSY/);
  await assert.rejects(rpc('gate_decide_login',b,id,true),/SESSION_EXPIRED/);
  await rpc('gate_decide_login',a,id,false);assert.equal((await rpc('gate_poll_login',b,id)).status,'rejected');await assert.rejects(rpc('gate_decide_login',a,id,true),/SESSION_REQUEST_CLOSED/);
  await rpc('gate_request_login',b,'request-cancel-12345');await rpc('gate_cancel_login',b,'request-cancel-12345');assert.equal((await rpc('gate_poll_login',b,'request-cancel-12345')).status,'cancelled');
  await rpc('gate_request_login',b,'request-expire-12345');await db.exec("reset role;update public.gate_login_requests set expires_at=now()-interval '1 second' where status='pending';set role authenticated");assert.equal((await rpc('gate_poll_login',b,'request-expire-12345')).status,'expired');await assert.rejects(rpc('gate_read_workspace_session',b),/SESSION_EXPIRED/);
 });
 await t.test('failed save keeps holder; successful handoff rejects every stale write and release',async()=>{
  const id='request-approve-1234';await rpc('gate_request_login',b,id);
  await assert.rejects(rpc('gate_save_workspace_session',a,1,'{}'),/INVALID_WORKSPACE/);await assert.rejects(rpc('gate_acquire_session',b),/SESSION_ACTIVE/);
  assert.equal((await rpc('gate_save_workspace_session',a,1,JSON.stringify(docs))).revision,2);await rpc('gate_decide_login',a,id,true);await rpc('gate_acquire_session',b);
  await assert.rejects(rpc('gate_save_workspace_session',a,2,JSON.stringify(docs)),/SESSION_EXPIRED/);await assert.rejects(rpc('gate_read_workspace_session',a),/SESSION_EXPIRED/);await assert.rejects(rpc('gate_touch_session',a),/SESSION_EXPIRED/);
  await rpc('gate_release_session',a);assert.equal((await rpc('gate_read_workspace_session',b)).revision,2);await assert.rejects(rpc('gate_save_workspace_session',b,1,JSON.stringify(docs)),/WORKSPACE_CONFLICT/);
  assert.deepEqual((await rpc('gate_read_workspace_session',b)).documents,docs);
 });
 await t.test('lease expiry requires explicit reacquire and gate tables never reference check-in tables',async()=>{
  await db.exec("reset role;update public.gate_editor_session set expires_at=now()-interval '1 second';set role authenticated");await assert.rejects(rpc('gate_read_workspace_session',b),/SESSION_EXPIRED/);await rpc('gate_acquire_session',c);assert.equal((await rpc('gate_read_workspace_session',c)).revision,2);
  assert.equal((await db.query("select count(*)::int as n from pg_tables where schemaname='public' and tablename like 'autoschedule_%'")).rows[0].n,0);
 });
});
