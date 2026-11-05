import { test } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';

async function loadModule(path) {
  const bundle=await build({entryPoints:[fileURLToPath(new URL(path,import.meta.url))],bundle:true,write:false,format:'esm',target:'es2022'});
  return import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);
}
const { startPreparation, loadPreparationConfiguration } = await loadModule('../src/day-preparation.ts');
const { rulesPage } = await loadModule('../src/workbench-pages.ts');

test('规则页无需当前日计划即可维护已导入的航班接续偏好', () => {
  const state = {
    relaxedShiftSettings:{earlyDepartureCutoffTime:'12:00',afternoonRestStartTime:'12:00',afternoonRestEndTime:'18:00',lateShiftEndTime:'23:00',lateFlightNos:[]},
    teamLeaderSettings:{lastUsed:true,exemptFlightCount:true},
    configuration: {roleConfigs:[{roleName:'接机2',workMode:'arrival'},{roleName:'登机核验',workMode:'boarding'}],rotationEnabled:false,rotationMinutes:60,transitionMinutes:0},
    newRoleDefaults:{}, flightLibrary:[], plans:{},
    balanceSettings:{flightCountGap:null,priorityEnabled:false,priorityRoleNames:[],priorityFlightRoles:[],priorityAdjustments:[]},
    handoffPreferences:[{flightNo:'*',arrivalRole:'接机2',boardingRoles:['登机核验'],enabled:true}]
  };
  const markup=rulesPage(state,null,null);
  assert.match(markup,/data-handoff-flight="\*"/);
  assert.equal((markup.match(/data-handoff-flight=/g) ?? []).length,1);
  assert.match(markup,/data-handoff-target="登机核验" checked/);
  assert.match(markup,/data-handoff-enabled checked/);
});

const fixtures = () => ({
  state: { people: [{id:'a',name:'甲',active:true,eligibleRoleNames:['接机']}], configuration:{roleConfigs:[{roleName:'接机',requiredCount:2}]},newRoleDefaults:{接机:{roleName:'接机',requiredCount:2,workMode:'arrival',concurrent:false}},flightLibrary:[{flightNo:'CX937',windowStart:'10:10',windowEnd:'11:30',roles:[{roleName:'接机',requiredCount:3}]}],flightTemplates:[],weeklyFlightPresets:{} },
  day: {roster:['甲'],roleNames:['接机'],roleConfigs:[{roleName:'接机',requiredCount:1,workMode:'continuous',concurrent:false}],memberConfigs:[{memberName:'甲',eligibleRoleNames:[]}],memberAvailability:[{memberName:'甲',participating:true,availableFrom:null,availableUntil:null}],flights:[{id:'f',flightNo:'CX937',windowStart:'10:00',windowEnd:'11:00'}],cells:[{id:'c',flightId:'f',flightNo:'CX937',roleName:'接机',requiredCountOverride:1,workModeOverride:'continuous',workStart:null,workEnd:null}]}
});

test('重新排班保留当天人工取消的资质、人数及环节，不被长期配置静默替换',()=>{
  const {state,day}=fixtures(),before=structuredClone(day);
  assert.deepEqual(startPreparation(day,state,'reschedule').day,before);
  assert.deepEqual(day,before);
});

test('明确载入长期配置或准备下一班时更新需求和资质，确认前只改准备副本',()=>{
  const {state,day}=fixtures(),saved=structuredClone(day),prepared=structuredClone(day);
  loadPreparationConfiguration(prepared,state);
  assert.deepEqual(day,saved);
  assert.equal(prepared.cells[0].requiredCountOverride,3);
  assert.equal(prepared.roleConfigs[0].workMode,'arrival');
  assert.deepEqual(prepared.memberConfigs[0].eligibleRoleNames,['接机']);
  assert.equal(prepared.transitionMinutes,state.configuration.transitionMinutes);
});

test('航班岗位移除后载入最新配置和新日均为零需求，普通重排保留当天例外',()=>{
  const {state,day}=fixtures();
  state.flightLibrary[0].roleNames=[];
  const saved=structuredClone(day),prepared=structuredClone(day);
  loadPreparationConfiguration(prepared,state);
  assert.equal(prepared.cells[0].requiredCountOverride,0);
  assert.deepEqual(day,saved);
  assert.equal(startPreparation(day,state,'reschedule').day.cells[0].requiredCountOverride,1,'旧日需求没有静默重写');
  const newDay=structuredClone(day);newDay.cells=[];
  assert.equal(startPreparation(newDay,state,'reschedule').day.cells[0].requiredCountOverride,0,'为新航班补格也尊重已移除岗位');
});
