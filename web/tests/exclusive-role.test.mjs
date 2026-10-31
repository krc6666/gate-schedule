import assert from 'node:assert/strict';
import {test} from 'node:test';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';

const bundle=await build({stdin:{contents:'export {modeControls} from "./workbench-pages";',resolveDir:fileURLToPath(new URL('../src/',import.meta.url))},bundle:true,write:false,format:'esm',target:'es2022'});
const {modeControls}=await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);
test('每个岗位均有独立专岗开关，不按名称写死且与兼任分开',()=>{
  const state={newRoleDefaults:{},workModes:{boarding:'登机环节'}};
  for(const name of ['自定义主任','登机主验','大件行李拦截']) {
    const markup=modeControls(state,{roleName:name,requiredCount:1,exclusiveSameFlight:true,concurrent:true},'day');
    assert.match(markup,/data-role-exclusive="[^"]+"[^>]*checked/);
    assert.match(markup,/同航班专岗专人/);
    assert.match(markup,/data-scope="day"/);
  }
  const off=modeControls(state,{roleName:'督导',requiredCount:1,concurrent:true},'base');
  assert.match(off,/data-role-concurrent="督导"[^>]*checked/);
  assert.doesNotMatch(off,/data-role-exclusive="督导"[^>]*checked/);
});
