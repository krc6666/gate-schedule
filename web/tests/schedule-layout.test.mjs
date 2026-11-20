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
const flight = (flightNo,windowStart,windowEnd) => ({id:flightNo,flightNo,windowStart,windowEnd});

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
  const groups=sharedBaggageGroups(['CX','KE','FD','TW'],matrix,[flight('CX','10:00','11:00'),flight('KE','10:30','11:30'),flight('FD','16:00','17:00'),flight('TW','次日00:10','次日01:10')]);
  assert.deepEqual(groups.map(g=>g.columns),[['CX','KE'],['FD'],['TW']]);
  assert.deepEqual(groups[0].segments.map(s=>s.task_id),['a','b','c']);
  assert.deepEqual(matrix,before);
});

test('传递共享连接成组，保留轮换时间顺序和缺口，不跨越独立列',()=>{
  const a=segment('a',600),b=segment('b',630),c=segment('c',660);
  const groups=sharedBaggageGroups(['A','B','C','空','D'],{A:[b,a],B:[b,c],C:[c],D:[a]},['A','B','C','D'].map(no=>flight(no,'10:00','11:00')));
  assert.deepEqual(groups.map(g=>g.columns),[['A','B','C'],['空'],['D']]);
  assert.deepEqual(groups[0].segments.map(s=>s.task_id),['a','b','c']);
});

test('MF8683与FD573即使共享大件轮换也按不重叠的航班窗口分格',()=>{
  const shared=segment('actual-shared-turn',900);
  const flights=[flight('MF8683','15:40','16:40'),flight('FD573','17:25','18:25')];
  const matrix={MF8683:[shared],FD573:[shared]};
  assert.deepEqual(sharedBaggageGroups(['MF8683','FD573'],matrix,flights).map(g=>g.columns),[['MF8683'],['FD573']]);
});

test('任意航班采用共同重叠，首尾相接与传递链不能绕过分格',()=>{
  const shared=segment('shared',500),columns=['A','B','C','D'];
  const matrix=Object.fromEntries(columns.map(c=>[c,[shared]]));
  const flights=[flight('A','10:00','11:00'),flight('B','10:30','11:30'),flight('C','11:10','12:00'),flight('D','12:00','13:00')];
  assert.deepEqual(sharedBaggageGroups(columns,matrix,flights).map(g=>g.columns),[['A','B'],['C'],['D']]);
  assert.deepEqual(sharedBaggageGroups(['C','B','A'],matrix,flights).map(g=>g.columns),[['C','B'],['A']]);
  assert.equal(sharedBaggageGroups(['A','B'],{A:[shared],B:[segment('independent',500)]},flights).length,2,'重叠且同人但无共享任务仍分格');
});

test('显式跨日、前日、多日时段按日期比较，未知或无效窗口不猜测合并',()=>{
  const shared=segment('shared',1400);
  for(const [left,right,merge] of [
    [['23:05','次日00:05'],['23:30','次日00:55'],true],
    [['23:05','次日00:05'],['次日00:05','次日01:00'],false],
    [['23:05','次日00:05'],['00:01','01:00'],false],
    [['前日23:10','00:30'],['00:01','01:00'],true],
    [['第2日23:10','第3日00:30'],['第3日00:01','第3日01:00'],true],
    [['前第2日23:10','前日00:30'],['前日00:01','前日01:00'],true],
    [['',''],['10:00','11:00'],false],
    [[undefined,null],['10:00','11:00'],false],
    [['25:00','26:00'],['10:00','11:00'],false],
    [['10:00','09:00'],['10:00','11:00'],false],
  ]) {
    const groups=sharedBaggageGroups(['A','B'],{A:[shared],B:[shared]},[flight('A',...left),flight('B',...right)]);
    assert.equal(groups.length,merge?1:2,JSON.stringify([left,right]));
  }
  assert.equal(sharedBaggageGroups(['A','B'],{A:[shared],B:[shared]},[]).length,2);
});
