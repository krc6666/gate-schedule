import assert from 'node:assert/strict';
import {test} from 'node:test';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';

const bundle=await build({stdin:{contents:'export {templatePositions,bindTemplateConfig} from "./template-config";',resolveDir:fileURLToPath(new URL('../src/',import.meta.url))},bundle:true,write:false,format:'esm',target:'es2022'});
const {templatePositions,bindTemplateConfig}=await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);
function fixture() {
  return {people:[],newRoleDefaults:{},workModes:{arrival:'接机环节',boarding:'登机环节'},
    configuration:{roleConfigs:[{roleName:'登机主验',requiredCount:1,workMode:'boarding',exclusiveSameFlight:false}]},
    flightLibrary:['CX937','KE0166'].map(flightNo=>({flightNo,roleNames:['登机主验'],windowStart:'10:00',windowEnd:'11:00',
      roles:[{roleName:'登机主验',requiredCount:1,workModeOverride:'boarding',exclusiveSameFlight:flightNo==='CX937'}]}))};
}

test('专人专岗与工作环节控件按航班岗位显示，不再绑定同名通用设置',()=>{
  const markup=templatePositions(fixture());
  assert.match(markup,/data-template-exclusive="CX937"[^>]*checked/);
  assert.doesNotMatch(markup,/data-template-exclusive="KE0166"[^>]*checked/);
  assert.match(markup,/data-template-mode="CX937"/);
  assert.match(markup,/data-template-mode="KE0166"/);
  assert.doesNotMatch(markup,/data-role-exclusive|data-role-mode|通用岗位配置/);
});

test('一个航班修改专岗或工作环节只保存该航班副本，其他航班和值源保持',()=>{
  for(const [selector,dataset,value] of [
    ['[data-template-exclusive]',{templateExclusive:'CX937',templateRole:'登机主验'},false],
    ['[data-template-mode]',{templateMode:'CX937',templateRole:'登机主验'},'arrival'],
  ]) {
    const state=fixture(),before=structuredClone(state),saved=[];
    const control={dataset,checked:value,value};
    const root={querySelectorAll:s=>s===selector?[control]:[]};
    bindTemplateConfig(root,state,async(action,payload)=>{saved.push({action,payload});return true;},()=>{});
    control.onchange();
    assert.equal(saved[0].action,'save-flight-template');
    assert.equal(saved[0].payload.flightNo,'CX937');
    const role=saved[0].payload.roles[0];
    if(selector==='[data-template-exclusive]') assert.equal(role.exclusiveSameFlight,false);
    else assert.equal(role.workModeOverride,'arrival');
    assert.deepEqual(state,before);
  }
});
