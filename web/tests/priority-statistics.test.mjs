import assert from 'node:assert/strict';
import {test} from 'node:test';
import {build} from 'esbuild';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';

const repo=fileURLToPath(new URL('../../',import.meta.url)),src=join(repo,'web/src');
const bundle=await build({stdin:{contents:'export {priorityStatisticsHtml} from "./priority-statistics";',resolveDir:src},bundle:true,write:false,format:'esm',target:'es2022'});
const {priorityStatisticsHtml}=await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);

test('重点岗位次数展开后显示班表日期、航班和次数修正',()=>{
  const month='2026-09';
  const data={revision:4,month,months:[month],totals:[{memberId:'member-1',count:2}],roles:[{roleName:'督导',members:[{
    memberId:'member-1',memberName:'甲',qualified:true,historyCount:2,adjustment:0,effectiveCount:2,
    dates:['2026-09-03 · KE0166'],
  }]}]};
  const html=priorityStatisticsHtml(data,{month,roleName:''});

  assert.match(html,/<details id="priority-count-[^"]+" class="priority-count-detail"><summary title="展开班表日期和航班"[^>]*>2<\/summary>/);
  assert.match(html,/班表记录：2026-09-03 · KE0166/);
  assert.match(html,/班表 2 · 修正 \+0/);
  assert.match(html,/班表记录：/);
});

test('航班下拉筛选仅显示该航班重点列，合计消费服务端范围投影',()=>{
  const member={memberId:'a',memberName:'甲',qualified:true,historyCount:2,adjustment:0,effectiveCount:2,dates:['2026-11-03 · A100']};
  const data={revision:1,month:'2026-11',months:[],totals:[{memberId:'a',count:99}],
    scopedTotals:[{flightNo:'A100',roleName:'',totals:[{memberId:'a',count:7}]}],
    roles:[{flightNo:'A100',roleName:'督导',members:[member]},{flightNo:'B200',roleName:'督导',members:[member]}]};
  const html=priorityStatisticsHtml(data,{month:'2026-11',roleName:'',flightNo:'A100'});
  assert.match(html,/id="priority-statistics-flight"/);
  assert.match(html,/<option value="B200"/);
  assert.match(html,/<th>A100 · 督导<\/th>/);
  assert.doesNotMatch(html,/<th>B200 · 督导<\/th>/);
  assert.match(html,/<strong>7<\/strong>/,'不从前端重算或误用全航班99');
  assert.match(html,/data-flight-no="A100"/);
});

test('重点统计沿用值机的右侧工具栏、岗位按钮和人员合计表，不显示整段说明',()=>{
  const month='2026-11';
  const member={memberId:'a',memberName:'甲',qualified:true,historyCount:2,adjustment:0,effectiveCount:2,dates:[]};
  const data={revision:1,month,months:[month],totals:[{memberId:'a',count:2}],roles:[{flightNo:'A100',roleName:'督导',members:[member]}]};
  const html=priorityStatisticsHtml(data,{month,flightNo:'A100',roleName:'督导'});
  assert.match(html,/class="priority-statistics-toolbar"/);
  assert.match(html,/role="group" aria-label="重点岗位统计岗位"/);
  assert.match(html,/data-priority-role="督导"[^>]*aria-pressed="true"/);
  assert.match(html,/<th>合计<\/th>/);
  assert.match(html,/<td data-label="人员"><strong>甲<\/strong><\/td>/);
  assert.match(html,/data-label="A100 · 督导"/);
  assert.match(html,/class="priority-adjustment-row"/);
  assert.match(html,/class="priority-adjustments"/);
  assert.doesNotMatch(html,/只统计已选的航班＋岗位；当前所选已保存草稿/);
});
