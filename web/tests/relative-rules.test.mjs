import assert from 'node:assert/strict';
import {test} from 'node:test';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';

const bundle=await build({stdin:{contents:'export {flightsPage} from "./workbench-pages"; export {time} from "./workbench-model"; export {startPreparation} from "./day-preparation";',resolveDir:fileURLToPath(new URL('../src/',import.meta.url))},bundle:true,write:false,format:'esm',target:'es2022'});
const {flightsPage,time,startPreparation}=await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);

test('航班页可直接维护当天起降，保障窗口仍独立',()=>{
  const day={serviceDate:'2026-11-13',flights:[{id:'f',flightNo:'CX937',windowStart:'前日23:00',windowEnd:'00:30',arrivalTime:'前日23:10',departureTime:'00:20'}],roster:[],memberConfigs:[],memberAvailability:[]};
  const markup=flightsPage({},day,day.serviceDate,false);
  assert.match(markup,/data-field="arrivalTime"[^>]*value="前日23:10"/);
  assert.match(markup,/data-field="departureTime"[^>]*value="00:20"/);
  assert.match(markup,/data-field="windowStart"[^>]*value="前日23:00"/);
});

test('任务负分钟显示前日而不是负数钟点，支持前多日',()=>{
  assert.equal(time(-20),'前日23:40');
  assert.equal(time(-1460),'前第2日23:40');
  assert.equal(time(20),'00:20');
  assert.equal(time(1460),'次日00:20');
});

test('新日期不偷沿用上班实际起降，同日重排保留',()=>{
  const day={serviceDate:'2026-11-13',flights:[{id:'f',flightNo:'CX937',windowStart:'10:00',windowEnd:'11:30',arrivalTime:'10:10',departureTime:'11:20'}],roleNames:[],roleConfigs:[],roster:[],memberConfigs:[],memberAvailability:[],cells:[]};
  const state={people:[],flightTemplates:[],flightLibrary:[],configuration:{roleConfigs:[],transitionMinutes:0},newRoleDefaults:{}};
  assert.equal(startPreparation(day,state,'reschedule').day.flights[0].departureTime,'11:20');
  const next=startPreparation(day,state,'archive').day.flights[0];
  assert.equal(next.departureTime,null);
  assert.equal(next.arrivalTime,null);
  assert.equal(day.flights[0].departureTime,'11:20');
});
