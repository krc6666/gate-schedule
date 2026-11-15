import {PGlite} from '@electric-sql/pglite';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const repo=fileURLToPath(new URL('../../',import.meta.url));
const python=process.env.GATE_TEST_PYTHON??repo+'.venv-local/Scripts/python.exe';
const docs=JSON.parse(execFileSync(python,['-c',`import json
from pathlib import Path
from gate_schedule.browser_runtime import BrowserStore
from gate_schedule.group_workspaces import GroupWorkspaces
from tests.test_balance_configuration import configuration
s=BrowserStore(); groups=GroupWorkspaces(Path('unused'),store=s); groups.initialize()
for g in ('A','B'):
 w=groups.workbench(g); state=w.execute(w.snapshot()['revision'],'import-complete-configuration',configuration()); pid=state['selectedPlanId']
 state=w.execute(state['revision'],'solve',{},pid); w.execute(state['revision'],'adopt',{},pid)
print(json.dumps(s.documents))`],{cwd:repo,encoding:'utf8'}));
test('PostgreSQL SQL: admin-only read, direct writes denied, CAS including first-save conflict, corruption rejected',async()=>{
  const db=new PGlite();
  try{
    await db.exec(`create role anon; create role authenticated;
      create schema auth; create table auth.users(id uuid primary key);
      create function auth.uid() returns uuid language sql stable as $$ select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid $$;
      insert into auth.users values('11111111-1111-4111-8111-111111111111'),('22222222-2222-4222-8222-222222222222');`);
    const sql=await readFile(new URL('../../supabase/workspace.sql',import.meta.url),'utf8');
    await db.exec(sql);await db.exec(sql); // Idempotent setup preserves existing data and admin.
    await db.exec(`insert into public.gate_admin values(true,'11111111-1111-4111-8111-111111111111');set role anon;`);
    await assert.rejects(db.query('select public.gate_read_workspace()'),/permission denied/);
    await assert.rejects(db.query('select * from public.gate_workspace'),/permission denied/);
    await db.exec(`reset role; set role authenticated; set request.jwt.claim.sub='22222222-2222-4222-8222-222222222222';`);
    await assert.rejects(db.query('select public.gate_read_workspace()'),/ADMIN_REQUIRED/);
    await assert.rejects(db.query('select public.gate_save_workspace(0,$1::jsonb)',[JSON.stringify(docs)]),/ADMIN_REQUIRED/);
    await db.exec(`set request.jwt.claim.sub='11111111-1111-4111-8111-111111111111';`);
    assert.deepEqual((await db.query('select public.gate_read_workspace() as w')).rows[0].w,{revision:0,documents:null});
    await assert.rejects(db.query('insert into public.gate_workspace values(true,1,$1::jsonb,now())',[JSON.stringify(docs)]),/permission denied/);
    const save=revision=>db.query('select public.gate_save_workspace($1,$2::jsonb) as w',[revision,JSON.stringify(docs)]);
    assert.equal((await save(0)).rows[0].w.revision,1);
    await assert.rejects(save(0),/WORKSPACE_CONFLICT/);
    assert.equal((await save(1)).rows[0].w.revision,2);
    const bad=structuredClone(docs);delete bad['group-workspaces-v1'].groups.B.configuration;
    await assert.rejects(db.query('select public.gate_save_workspace(2,$1::jsonb)',[JSON.stringify(bad)]),/INVALID_WORKSPACE/);
    const final=(await db.query('select public.gate_read_workspace() as w')).rows[0].w;
    assert.equal(final.revision,2);assert.deepEqual(final.documents,docs);
    // Defense in depth: even with a mistaken SELECT grant the RLS hides all data from non-admins.
    await db.exec(`reset role; grant select on public.gate_workspace to authenticated; set role authenticated; set request.jwt.claim.sub='22222222-2222-4222-8222-222222222222';`);
    assert.equal((await db.query('select * from public.gate_workspace')).rows.length,0);
    await assert.rejects(db.query('select * from public.gate_admin'),/permission denied/);
  }finally{await db.close()}
});
