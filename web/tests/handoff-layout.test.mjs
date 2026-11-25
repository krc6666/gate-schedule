import assert from 'node:assert/strict';
import {test} from 'node:test';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
const bundle=await build({entryPoints:[fileURLToPath(new URL('../src/handoff-layout.ts',import.meta.url))],bundle:true,write:false,format:'esm'});
const {handoffRoutes}=await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);
const row=(source,targets,overrides={})=>({flightNo:'*',arrivalRole:source,boardingRoles:targets,enabled:true,targetFlightScopes:Object.fromEntries(targets.map(t=>[t,'same-flight'])),...overrides});
test('按实际来源连接顺排，不依赖保存数组先后，读取不变更资料',()=>{
  const rows=[row('接机1',['秩序维护']),row('秩序维护',['登机查验']),row('大件1',['接机1'])],before=structuredClone(rows);
  assert.deepEqual(handoffRoutes(rows),[{indices:[2,0,1],flightNo:'*'}]);assert.deepEqual(rows,before);
});
test('同范围两个前岗共享后续，两条均能看到原后续，未复制保存对象',()=>{
  const rows=[row('甲',['丙']),row('乙',['丙']),row('丙',['丁']),row('别岗',['末岗'])];
  assert.deepEqual(handoffRoutes(rows).map(r=>r.indices),[[0,2],[1,2],[3]]);
});
test('明确跨班单目标切换范围后保留同一张接续卡，不改保存对象',()=>{
  for(const scope of ['same-flight','later-overlap','earlier-overlap']){
    const rows=[row('大件2',['接机2'],{targetFlightScopes:{'接机2':scope}}),row('接机2',['查验','秩序'])],before=structuredClone(rows);
    assert.deepEqual(handoffRoutes(rows).map(r=>r.indices),[[0,1]]);
    assert.deepEqual(rows,before);
  }
});
test('不同航班特例、旧未明确范围、候选分支与停用不冒充顺序连接',()=>{
  for(const first of [row('甲',['乙'],{flightNo:'CX*'}),row('甲',['乙'],{targetFlightScopes:{}}),row('甲',['乙','丙']),row('甲',['乙'],{enabled:false})]){
    const rows=[first,row('乙',['丁'])];assert.deepEqual(handoffRoutes(rows).map(r=>r.indices),[[0],[1]]);
  }
});
test('循环和同岗自接不无限展开，不丢失任何普通来源；旧组仍不进入日常顺序',()=>{
  const rows=[row('甲',['乙']),row('乙',['甲']),row('同岗',['同岗']),row('旧组',['甲'],{mode:'chain',id:'old'})];
  assert.deepEqual(handoffRoutes(rows).map(r=>r.indices).flat().sort(),[0,1,2]);
});
