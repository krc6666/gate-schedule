import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';

const bundle = await build({entryPoints:[fileURLToPath(new URL('../src/workbench-edits.ts', import.meta.url))], bundle:true, write:false, format:'esm', target:'es2022'});
const { WorkbenchEdits } = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);

function fixture() {
  const state = {
    configuration:{rotationEnabled:true,rotationMinutes:60}, balanceSettings:{priorityEnabled:false},
    teamLeaderSettings:{lastUsed:true}, halfRestSettings:{exemptFlightCount:true},
    relaxedShiftSettings:{lateFlightNos:[]}, handoffPreferences:[], dutyRoster:{enabled:true},
  };
  const plan = {id:'day',day:{serviceDate:'2026-11-05',title:'original'},draft:[],rotationEnabled:true,rotationMinutes:60};
  const commands = [];
  let rejected = '', valid = true, confirmations = 0;
  const edits = new WorkbenchEdits({
    context:()=>({state,plan}), changed:()=>{}, validate:()=>valid,
    confirmDayChange:()=>{confirmations++;return true;},
    execute:async(action,payload)=>{
      commands.push({action,payload:structuredClone(payload)});
      if(action===rejected)return false;
      if(action==='save-rules')Object.assign(state,{...payload,handoffPreferences:payload.preferences});
      if(action==='save-duty-settings')state.dutyRoster=payload;
      edits.committed(action,plan);
      return true;
    },
    saveCells:async()=>{for(const id of edits.cells.keys()){if(rejected==='edit-cell')return false;commands.push({action:'edit-cell',id});edits.cells.delete(id);}return true;},
  });
  edits.resetDay(plan);
  return {edits,state,plan,commands,reject:action=>{rejected=action;},validate:value=>{valid=value;},confirmations:()=>confirmations};
}

test('每种设置草稿都阻止离开，继续编辑保留、放弃后才继续', async()=>{
  const {edits,state,commands}=fixture();
  for(const key of Object.keys(state)) {
    edits.settings[key]=structuredClone(state[key]);
    assert.equal(edits.hasChanges,true,key);
    const leaving=edits.beforeContinue();
    assert.equal(edits.confirming,true);
    assert.equal(await edits.beforeContinue(),false,'已有确认时不启动第二次操作');
    await edits.resolveLeave('stay');assert.equal(await leaving,false);
    assert.equal(edits.hasChanges,true);
    const retry=edits.beforeContinue();await edits.resolveLeave('discard');
    assert.equal(await retry,true);assert.equal(edits.hasChanges,false);
  }
  assert.deepEqual(commands,[]);
});

test('规则、当天、轮值和姓名混合修改必须全部保存才继续，失败仅保留未成功部分', async()=>{
  const {edits,commands,reject}=fixture();
  edits.settings.halfRestSettings={exemptFlightCount:false};
  edits.settings.dutyRoster={enabled:false};
  edits.day.title='changed';edits.dayDirty=true;edits.cells.set('cell','甲');
  reject('save-duty-settings');
  let resumed=0;
  const leaving=edits.beforeContinue().then(ok=>{if(ok)resumed++;return ok;});
  await edits.resolveLeave('save');
  assert.equal(resumed,0);assert.equal(edits.confirming,true);
  assert.equal(edits.settings.halfRestSettings,undefined,'已成功部分不重复保存');
  assert.equal(edits.settings.dutyRoster.enabled,false);
  assert.equal(edits.cells.get('cell'),'甲');
  assert.equal(commands[0].payload.input.day.title,'changed');
  reject('');await edits.resolveLeave('save');assert.equal(await leaving,true);
  assert.equal(resumed,1);assert.equal(edits.hasChanges,false);assert.equal(edits.confirming,false);
  assert.deepEqual(commands.map(c=>c.action),['save-rules','save-duty-settings','save-duty-settings','edit-cell']);
});

test('校验失败和请求失败均保留原草稿与待继续操作', async()=>{
  const {edits,commands,reject,validate}=fixture();
  edits.settings.handoffPreferences=[{enabled:false}];
  const leaving=edits.beforeContinue();validate(false);await edits.resolveLeave('save');
  assert.equal(commands.length,0);assert.equal(edits.hasChanges,true);
  validate(true);reject('save-rules');await edits.resolveLeave('save');
  assert.equal(edits.settings.handoffPreferences[0].enabled,false);assert.equal(edits.confirming,true);
  await edits.resolveLeave('stay');assert.equal(await leaving,false);
});

test('仅有姓名编辑时导航自动保存，失败不继续；有其他修改则询问', async()=>{
  const {edits,reject}=fixture();edits.cells.set('cell','甲');reject('edit-cell');
  assert.equal(await edits.beforeContinue(true),false);assert.equal(edits.cells.size,1);
  reject('');assert.equal(await edits.beforeContinue(true),true);assert.equal(edits.hasChanges,false);
  edits.cells.set('cell','乙');edits.settings.relaxedShiftSettings={lateFlightNos:['A100']};
  const leaving=edits.beforeContinue(true);assert.equal(edits.confirming,true);
  await edits.resolveLeave('discard');assert.equal(await leaving,true);assert.equal(edits.cells.size,0);
});

test('命令成功集中清理对应草稿，换计划清理全部，其他写入保留编辑', ()=>{
  const {edits,plan}=fixture();
  edits.settings.configuration={rotationEnabled:false};edits.settings.dutyRoster={enabled:false};
  edits.committed('save-config',plan);assert.equal(edits.settings.configuration,undefined);
  assert.equal(edits.settings.dutyRoster.enabled,false);
  edits.settings.handoffPreferences=[{enabled:false}];edits.dayDirty=true;edits.cells.set('cell','甲');
  edits.committed('adjust-priority-count',plan);assert.equal(edits.hasChanges,true);
  const effect=edits.committed('select-plan',{...plan,day:{title:'other'}});
  assert.equal(effect.followPlan,true);assert.equal(edits.hasChanges,false);assert.equal(edits.day.title,'other');
});

test('保存中重复点击或关闭确认不能重复写入或提前继续', async()=>{
  let finishRequest, edits, writes=0, resumed=0;
  const pendingRequest=new Promise(resolve=>{finishRequest=resolve;});
  edits=new WorkbenchEdits({
    context:()=>({state:{dutyRoster:{enabled:true}},plan:null}),changed:()=>{},validate:()=>true,
    confirmDayChange:()=>true,saveCells:async()=>true,
    execute:async()=>{writes++;await pendingRequest;edits.committed('save-duty-settings',null);return true;},
  });
  edits.settings.dutyRoster={enabled:false};
  const leaving=edits.beforeContinue().then(ok=>{if(ok)resumed++;return ok;});
  const saving=edits.resolveLeave('save');
  await edits.resolveLeave('save');await edits.resolveLeave('discard');await edits.resolveLeave('stay');
  assert.equal(writes,1);assert.equal(resumed,0);assert.equal(edits.confirming,true);
  finishRequest();await saving;assert.equal(await leaving,true);assert.equal(resumed,1);
});

test('查看无计划日期时保存规则不能载入其他日期的旧计划', ()=>{
  const oldPlan={id:'old',day:{title:'old date'}};
  const edits=new WorkbenchEdits({context:()=>({state:{},plan:null}),changed:()=>{},
    execute:async()=>true,validate:()=>true,confirmDayChange:()=>true,saveCells:async()=>true});
  edits.settings.halfRestSettings={exemptFlightCount:false};
  edits.committed('save-rules',oldPlan);
  assert.equal(edits.day,null);assert.equal(edits.hasChanges,false);
  edits.committed('select-plan',oldPlan);assert.equal(edits.day.title,'old date');
});
