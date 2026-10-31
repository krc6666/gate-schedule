import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const javascript = source => ts.transpileModule(source, {compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ES2022}}).outputText;
const url = text => `data:text/javascript;base64,${Buffer.from(text).toString('base64')}`;
const model = url(javascript(await readFile(new URL('../src/workbench-model.ts',import.meta.url),'utf8')));
const source=javascript(await readFile(new URL('../src/schedule-layout.ts',import.meta.url),'utf8')).replace('"./workbench-model"',JSON.stringify(model));
const { sharedBaggageGroups, flightHeader } = await import(url(source));
const segment = (id,start) => ({task_id:id,start,end:start+30,members:['甲']});

test('表头显示提供的航班窗口、保留次日文字并转义，无时间的支援不捏造时间',()=>{
  const flights=[{flightNo:'TW616',windowStart:'次日00:10',windowEnd:'次日01:10'}];
  assert.match(flightHeader('TW616',flights),/次日00:10–次日01:10/);
  assert.match(flightHeader('TW616',flights),/class="flight-number"/);
  assert.doesNotMatch(flightHeader('支援',flights),/flight-time/);
  assert.doesNotMatch(flightHeader('空', [{flightNo:'空',windowStart:'',windowEnd:''}]),/flight-time/);
  assert.match(flightHeader('<航班>',[{flightNo:'<航班>',windowStart:'<10:00>',windowEnd:'11:00'}]),/&lt;航班&gt;/);
  assert.doesNotMatch(flightHeader('<航班>',[]),/<航班>/);
});

test('部分共享的航班合并轮换任务并去重，独立任务即使同人也不合并',()=>{
  const a=segment('a',600),b=segment('b',630),c=segment('c',660),d=segment('d',1000);
  const matrix={CX:[a,b],KE:[a,b,c],FD:[d],TW:[segment('e',1450)]};
  const before=structuredClone(matrix);
  const groups=sharedBaggageGroups(['CX','KE','FD','TW'],matrix);
  assert.deepEqual(groups.map(g=>g.columns),[['CX','KE'],['FD'],['TW']]);
  assert.deepEqual(groups[0].segments.map(s=>s.task_id),['a','b','c']);
  assert.deepEqual(matrix,before);
});

test('传递共享连接成组，保留轮换时间顺序和缺口，不跨越独立列',()=>{
  const a=segment('a',600),b=segment('b',630),c=segment('c',660);
  const groups=sharedBaggageGroups(['A','B','C','空','D'],{A:[b,a],B:[b,c],C:[c],D:[a]});
  assert.deepEqual(groups.map(g=>g.columns),[['A','B','C'],['空'],['D']]);
  assert.deepEqual(groups[0].segments.map(s=>s.task_id),['a','b','c']);
});
