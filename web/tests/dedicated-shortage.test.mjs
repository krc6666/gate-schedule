import assert from 'node:assert/strict';
import {test} from 'node:test';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';

const bundle = await build({stdin:{contents:'export {readonlyMatrix} from "./workbench-pages"; export {scheduleExtras} from "./schedule-extras"; export {needsRedWarning} from "./workbench-model";',resolveDir:fileURLToPath(new URL('../src/',import.meta.url))},bundle:true,write:false,format:'esm',target:'es2022'});
const {readonlyMatrix,scheduleExtras,needsRedWarning} = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);

test('专岗缺口在历史和上一班标红，普通缺口保留原提示',()=>{
  const issue={code:'coverage_shortfall',message:'缺1人',taskId:'t'};
  const schedule={flights:[{id:'f',flightNo:'A100',windowStart:'10:00',windowEnd:'11:00'}],tasks:[{id:'t',flightId:'f',roleId:'主验',requiredCount:1,exclusiveSameFlight:true}],members:[],assignments:[],memberFlights:[],matrix:{主验:{A100:[{task_id:'t',members:[],start:600,end:660}]}},memberPaths:{}};
  assert.equal(needsRedWarning(issue,schedule),true);
  assert.equal(needsRedWarning({...issue,code:'coverage_order_unmet',severity:'warning'},schedule),false,'顺序偏好即使关联专岗也不能标作硬错误');
  assert.match(readonlyMatrix(schedule,null,{},[issue]),/schedule-cell-conflict/);
  const preference={...issue,code:'coverage_order_unmet',severity:'warning',message:'顺序偏好未满足'};
  assert.match(readonlyMatrix(schedule,null,{},[preference]),/schedule-cell-vacancy/);
  assert.doesNotMatch(readonlyMatrix(schedule,null,{},[preference]),/schedule-cell-conflict/);
  const previous={day:{serviceDate:'2026-11-10'},formalSchedule:schedule,formalIssues:[issue]};
  assert.match(scheduleExtras({plans:{previous}},{day:{serviceDate:'2026-11-12'}},schedule),/previous-cell-conflict/);
  assert.match(scheduleExtras({plans:{previous:{...previous,formalIssues:[preference]}}},{day:{serviceDate:'2026-11-12'}},schedule),/previous-cell-vacancy/);
  schedule.tasks[0].exclusiveSameFlight=false;
  assert.equal(needsRedWarning(issue,schedule),false);
  assert.match(readonlyMatrix(schedule,null,{},[issue]),/schedule-cell-vacancy/);
  assert.match(scheduleExtras({plans:{previous}},{day:{serviceDate:'2026-11-12'}},schedule),/previous-cell-vacancy/);
});
