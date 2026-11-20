import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';

const bundle = await build({entryPoints:[fileURLToPath(new URL('../src/day-preparation.ts', import.meta.url))], bundle:true, write:false, format:'esm', target:'es2022'});
const { startPreparation, loadPreparationConfiguration, setPreparationRequiredCounts, applyWeeklyFlights, copyPreparationAttendance, confirmPreparation } = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);

test('配置页人数由0改1后普通重排必须生成需求；只有明确选择才沿用当天0',()=>{
  const {day,state}=fixture();
  day.cells[0].requiredCountOverride=0;
  state.flightLibrary[0].roles[0].requiredCount=1;
  const before=structuredClone({day,state});
  const p=startPreparation(day,state,'reschedule');
  assert.equal(confirmPreparation(p,day.serviceDate,false).cells[0].requiredCountOverride,1);
  assert.equal(p.requiredCounts,'latest');
  assert.equal(p.day.cells[0].workStart,'10:10','人数同步不能改变当天环节');
  assert.deepEqual(p.day.memberConfigs,day.memberConfigs,'不能恢复当天已取消资格');
  setPreparationRequiredCounts(p,state,day,'day');
  assert.equal(confirmPreparation(p,day.serviceDate,false).cells[0].requiredCountOverride,0);
  assert.equal(p.requiredCounts,'day');
  setPreparationRequiredCounts(p,state,day,'latest');
  assert.equal(p.day.cells[0].requiredCountOverride,1);
  assert.deepEqual({day,state},before,'反复切换或取消不写原数据');
});

test('人数0、空白继承基础人数、增减人数及无长期模板的当天航班分别处理',()=>{
  for (const count of [0,1,4,null]) {
    const {day,state}=fixture();
    state.flightLibrary[0].roles[0].requiredCount=count;
    const p=startPreparation(day,state,'reschedule');
    assert.equal(p.day.cells[0].requiredCountOverride,count);
    assert.equal(p.day.roleConfigs[0].requiredCount,2);
  }
  const {day,state}=fixture();
  state.flightLibrary=[];day.cells[0].requiredCountOverride=null;
  const p=startPreparation(day,state,'reschedule');
  assert.equal(p.day.cells[0].requiredCountOverride,1,'临时航班原来继承当天1，不能被新基础2改写');
  setPreparationRequiredCounts(p,state,day,'day');
  assert.equal(p.day.cells[0].requiredCountOverride,null);
  assert.equal(p.day.roleConfigs[0].requiredCount,1);
});

function fixture() {
  const day = {
    serviceDate:'2026-11-05',title:'原班表',roster:['甲'],roleNames:['接机'],
    roleConfigs:[{roleName:'接机',requiredCount:1,workMode:'continuous',concurrent:false}],
    memberConfigs:[{memberName:'甲',eligibleRoleNames:[]}],
    memberAvailability:[{memberName:'甲',participating:true,availableFrom:'09:00',availableUntil:'12:00',halfRest:'early-finish'}],
    flights:[{id:'f',flightNo:'CX937',windowStart:'10:00',windowEnd:'11:00'}],
    cells:[{id:'c',flightId:'f',flightNo:'CX937',roleName:'接机',rawValue:'甲',internalNames:['甲'],externalLabels:['外援'],semantics:'internal',fixedMemberNames:['甲'],requiredCountOverride:1,workModeOverride:'timed',workStart:'10:10',workEnd:'10:30'}],
    phaseHandoffs:[['f','f']],extraOccupancies:[{id:'o',label:'支援',memberNames:['甲'],start:'09:00',end:'10:00'}],remarks:[{rawText:'旧备注',kind:'note'}],remarksReviewed:false,transitionMinutes:5,
  };
  const state = {
    people:[{id:'a',name:'甲',active:true,eligibleRoleNames:['接机']}],
    configuration:{roleConfigs:[{roleName:'接机',requiredCount:2,workMode:'arrival',concurrent:false}],transitionMinutes:0},
    newRoleDefaults:{},flightTemplates:[{flightNo:'CX937',windowStart:'10:10',windowEnd:'11:30'},{flightNo:'A100',windowStart:'12:00',windowEnd:'13:00'}],
    flightLibrary:[{flightNo:'CX937',windowStart:'10:10',windowEnd:'11:30',roleNames:['接机'],roles:[{roleName:'接机',requiredCount:3,workModeOverride:'timed',workStart:'10:15',workEnd:'10:35'}]}],weeklyFlightPresets:{},
  };
  return {day,state};
}

test('普通重排同步最新人数，在副本保留当天资格/环节/出勤及默认选中',()=>{
  const {day,state}=fixture(),saved=structuredClone({day,state});
  const p=startPreparation(day,state,'reschedule');
  assert.deepEqual([...p.selected],['f']);
  assert.equal(p.day.flights.length,2);
  assert.deepEqual(p.day.cells[0],{...day.cells[0],requiredCountOverride:3});
  assert.deepEqual(p.day.roleConfigs,[{...day.roleConfigs[0],requiredCount:2}]);
  assert.deepEqual(p.day.memberConfigs,day.memberConfigs);
  assert.deepEqual(p.day.memberAvailability,day.memberAvailability);
  assert.equal(p.day.serviceDate,day.serviceDate);
  assert.deepEqual({day,state},saved,'取消准备不改已存资料或配置');
});

test('明确载入配置只更新准备副本；未移除的固定人与当天事实留到新日确认清理',()=>{
  const {day,state}=fixture(),saved=structuredClone(day),p=startPreparation(day,state,'reschedule');
  loadPreparationConfiguration(p.day,state);
  assert.equal(p.day.cells[0].requiredCountOverride,3);
  assert.equal(p.day.cells[0].workStart,'10:15');
  assert.deepEqual(p.day.memberConfigs[0].eligibleRoleNames,['接机']);
  assert.deepEqual(p.day.cells[0].fixedMemberNames,['甲']);
  assert.deepEqual(day,saved);
});

test('新日默认加两天、跨月保留航班选择；星期预设不自动覆盖；清半休及特殊出勤限制',()=>{
  const {day,state}=fixture();state.weeklyFlightPresets['6']=[state.flightTemplates[1]];
  const p=startPreparation(day,state,'archive');
  assert.equal(p.day.serviceDate,'2026-11-07');
  assert.deepEqual(p.day.flights.filter(f=>p.selected.has(f.id)).map(f=>f.flightNo),['CX937']);
  assert.deepEqual(p.day.memberAvailability,[{memberName:'甲',participating:true,availableFrom:null,availableUntil:null,halfRest:null}]);
  assert.equal(p.day.cells[0].requiredCountOverride,3);
  state.weeklyFlightPresets['6']=[];
  assert.equal(startPreparation(day,state,'archive').selected.size,1,'预设只有主动应用才改变航班');
  assert.equal(startPreparation({...day,serviceDate:'2026-12-31'},state,'archive').day.serviceDate,'2027-01-02');
});

test('休假状态保持；主动恢复正常后下次准备默认参加，手工不参加仍沿用',()=>{
  const {day,state}=fixture();day.memberAvailability[0].participating=false;
  state.people[0].status='休假';
  assert.equal(startPreparation(day,state,'archive',{a:'休假'}).day.memberAvailability[0].participating,false);
  state.people[0].status='正常';
  assert.equal(startPreparation(day,state,'archive',{a:'休假'}).day.memberAvailability[0].participating,true);
  assert.equal(startPreparation(day,state,'archive').day.memberAvailability[0].participating,false);
});

test('确认新日按选择裁剪，清姓名/固定/额外占用/备注，特殊时段与衔接须显式保留',()=>{
  const {day,state}=fixture(),p=startPreparation(day,state,'archive');
  const untouched=structuredClone(p.day);
  const fresh=confirmPreparation(p,'2026-11-09',false);
  assert.equal(fresh.flights.length,1);assert.equal(fresh.cells.length,1);
  assert.equal(fresh.title,'2026-11-09 保障计划');
  assert.deepEqual([fresh.cells[0].rawValue,fresh.cells[0].internalNames,fresh.cells[0].externalLabels,fresh.cells[0].semantics,fresh.cells[0].fixedMemberNames],['',[],[],'empty',[]]);
  assert.deepEqual([fresh.cells[0].workStart,fresh.cells[0].workEnd,fresh.phaseHandoffs],[null,null,[]]);
  assert.deepEqual([fresh.extraOccupancies,fresh.remarks,fresh.remarksReviewed],[[],[],true]);
  const confirmed=confirmPreparation(p,'2026-11-09',true);
  assert.deepEqual([confirmed.cells[0].workStart,confirmed.cells[0].workEnd,confirmed.phaseHandoffs],['10:15','10:35',[['f','f']]]);
  assert.deepEqual(p.day,untouched,'成功确认也不改准备副本，可在计算失败后重试');
  assert.equal(fresh.cells[0].requiredCountOverride,3);
});

test('准备检查失败保留选项；普通重排不能空航班/无人，新日允许待处理空草稿',()=>{
  const {day,state}=fixture(),p=startPreparation(day,state,'reschedule');
  const before=structuredClone(p.day);
  assert.throws(()=>confirmPreparation(p,'',false),/请填写排班日期/);
  p.selected.clear();
  assert.throws(()=>confirmPreparation(p,day.serviceDate,false),/请至少选择一个航班/);
  assert.deepEqual(p.day,before);
  p.selected.add('f');p.day.memberAvailability[0].participating=false;
  assert.throws(()=>confirmPreparation(p,day.serviceDate,false),/请选择当天参加人员/);
  const next=startPreparation(day,state,'archive');next.selected.clear();next.day.memberAvailability=[];
  assert.equal(confirmPreparation(next,next.day.serviceDate,false).flights.length,0);
});

test('周计划用于当天只保留所选航班及其格子/衔接，并明确套用模板而不改长期配置',()=>{
  const {day,state}=fixture(),saved=structuredClone(state);
  const p=startPreparation(day,state,'reschedule');
  p.day.phaseHandoffs.push(['f',p.day.flights[1].id]);
  applyWeeklyFlights(p.day,state,[state.flightTemplates[1]]);
  assert.deepEqual(p.day.flights.map(f=>f.flightNo),['A100']);
  assert.equal(p.day.cells.length,1);assert.equal(p.day.cells[0].flightId,p.day.flights[0].id);
  assert.deepEqual(p.day.phaseHandoffs,[]);
  assert.deepEqual(p.day.memberConfigs[0].eligibleRoleNames,['接机']);
  assert.deepEqual(state,saved);
});

test('复制原出勤保留原来的时段；跨日半休清除，重排半休保留，来源不变',()=>{
  const {day,state}=fixture(),saved=structuredClone(day);
  const archive=startPreparation(day,state,'archive');copyPreparationAttendance(archive,day);
  assert.deepEqual(archive.day.memberAvailability,[{...day.memberAvailability[0],halfRest:null}]);
  const same=startPreparation(day,state,'reschedule');copyPreparationAttendance(same,day);
  assert.deepEqual(same.day.memberAvailability,day.memberAvailability);
  archive.day.memberAvailability[0].participating=false;
  assert.deepEqual(day,saved);
});

test('新人与停用/非正常人员保留原预选政策，新增岗位尊重模板显式移除',()=>{
  const {day,state}=fixture();
  state.people[0].status='请假';
  state.people.push({id:'b',name:'乙',active:true,eligibleRoleNames:['接机']});
  state.configuration.roleConfigs.push({roleName:'督导',requiredCount:1});
  const p=startPreparation(day,state,'reschedule');
  assert.equal(p.day.memberAvailability[0].participating,false);
  assert.equal(p.day.memberAvailability.find(a=>a.memberName==='乙').participating,true);
  assert.deepEqual(p.day.memberConfigs.find(m=>m.memberName==='乙').eligibleRoleNames,['接机']);
  assert.equal(p.day.cells.find(c=>c.flightId==='f'&&c.roleName==='督导').requiredCountOverride,0);
  state.people[0].active=false;
  assert.equal(startPreparation(day,state,'archive').day.memberAvailability[0].participating,false);
});
