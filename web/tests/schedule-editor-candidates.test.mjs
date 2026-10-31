import assert from 'node:assert/strict';
import { test } from 'node:test';
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { readFile, mkdtemp, mkdir, rm } from 'node:fs/promises';
import { createServer } from 'node:http';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

const repo=fileURLToPath(new URL('../../',import.meta.url));
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));

test('所有岗位格统一查询候选：零需求试排、未知阻断、任务冲突、版本与旧请求保护', {timeout:60000}, async t=>{
  const temporaryRoot=join(repo,'.tmp');await mkdir(temporaryRoot,{recursive:true});
  const directory=await mkdtemp(join(temporaryRoot,'editor-candidates-'));
  const chrome=[process.env.GATE_TEST_BROWSER,'C:/Program Files/Google/Chrome/Application/chrome.exe','C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'].find(path=>path&&existsSync(path));assert.ok(chrome);
  const bundle=await build({entryPoints:[join(repo,'web/src/schedule-editor.ts')],bundle:true,write:false,format:'iife',globalName:'editorModule',target:'es2022'});
  const requests=[],held=[];
  let mode='normal';
  const candidates=[
    {id:'a',name:'甲',available:false,manualAllowed:true,status:'blocked',label:'不可安排',reasons:['与已有航班接机冲突'],issues:[],revision:31},
    {id:'b',name:'乙',available:true,status:'available',label:'可安排，待确认临时需求',requiresConfiguration:true,reasons:[],issues:[],revision:31},
  ];
  const server=createServer((request,response)=>{
    if(request.url==='/bundle.js'){response.setHeader('Content-Type','text/javascript');response.end(bundle.outputFiles[0].text);return;}
    if(request.url?.includes('/api/workspace/')){
      requests.push(request.url);
      const requestMode=mode;
      const reply=()=>{
        response.setHeader('Content-Type','application/json');
        if(requestMode==='error'||requestMode==='preview-error'&&request.url.includes('preview-edit')){response.statusCode=503;response.end('{}');return;}
        if(request.url.includes('preview-edit')){response.end(JSON.stringify({valid:true,revision:31,issues:[],suggestions:[]}));return;}
        const result=request.url.includes('cell-unknown')?candidates.map(c=>({...c,status:'unverified',available:false,manualAllowed:false,label:'资料未核对',reasons:['请先确认岗位环节和需求']})):candidates;
        response.end(JSON.stringify(requestMode==='revision'?result.map(c=>({...c,revision:32})):result));
      };
      if(requestMode==='held')held.push(reply);else reply();return;
    }
    response.setHeader('Content-Type','text/html');response.end('<!doctype html><meta charset="utf-8"><div id="root"></div><script src="/bundle.js"></script>');
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const base=`http://127.0.0.1:${server.address().port}`;
  let browser,ws,sessionId,id=0;
  const pending=new Map();
  const until=async(fn,label)=>{const end=Date.now()+10000;while(Date.now()<end){const value=await fn();if(value)return value;await delay(25);}throw Error(`等待超时：${label}`);};
  const call=(method,params={})=>new Promise((resolve,reject)=>{const request=++id,timer=setTimeout(()=>{pending.delete(request);reject(Error(`Chrome超时${method}`));},10000);pending.set(request,{resolve:value=>{clearTimeout(timer);resolve(value);},reject});ws.send(JSON.stringify({id:request,method,params,...(sessionId?{sessionId}:{})}));});
  const evaluate=async expression=>{const result=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(result.exceptionDetails)throw Error(JSON.stringify(result.exceptionDetails));return result.result.value;};
  t.after(async()=>{
    for(const release of held.splice(0))release();ws?.close();
    if(browser&&browser.exitCode===null){const done=new Promise(resolve=>browser.once('exit',resolve));browser.kill();await Promise.race([done,delay(3000)]);}
    server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
    assert.equal(dirname(directory),temporaryRoot);await rm(directory,{recursive:true,force:true,maxRetries:8,retryDelay:250});
  });
  const profile=join(directory,'chrome');browser=spawn(chrome,['--headless=new','--remote-debugging-port=0',`--user-data-dir=${profile}`,'--no-first-run','--disable-background-networking','about:blank'],{windowsHide:true,stdio:'ignore',env:{...process.env,TEMP:temporaryRoot,TMP:temporaryRoot}});
  const portFile=await until(async()=>{try{return await readFile(join(profile,'DevToolsActivePort'),'utf8');}catch{return false;}},'Chrome启动');
  const [port,path]=portFile.trim().split(/\r?\n/);ws=new WebSocket(`ws://127.0.0.1:${port}${path}`);await new Promise(resolve=>ws.addEventListener('open',resolve,{once:true}));
  ws.addEventListener('message',event=>{const message=JSON.parse(event.data),request=pending.get(message.id);if(request){pending.delete(message.id);message.error?request.reject(Error(JSON.stringify(message.error))):request.resolve(message.result);}});
  const target=await call('Target.createTarget',{url:'about:blank'});sessionId=(await call('Target.attachToTarget',{targetId:target.targetId,flatten:true})).sessionId;
  await call('Page.enable');await call('Page.navigate',{url:base});await until(()=>evaluate('!!window.editorModule'),'模块载入');
  const gridMarkup=`<table><tbody><tr><td data-drop-ids="[]" data-cell-id="cell-empty"><div id="empty-text" data-cell-text="cell-empty" contenteditable="true"></div><button id="pick-empty" data-pick-cell>+</button></td><td data-drop-ids="[]" data-cell-id="cell-unknown"><button id="pick-unknown" data-pick-cell>+</button></td><td data-drop-ids='["existing"]' data-cell-id="cell-task"><button id="pick-task" data-pick-cell>+</button></td></tr></tbody></table>`;
  await evaluate(`window.context={state:{revision:31},disabled:false,plan:{id:'p',day:{roster:['甲','乙']},memberIds:{甲:'a',乙:'b'},draft:[],schedule:{tasks:[{id:'existing',requiredCount:1,roleId:'核验',timingLabel:'登机环节'}],members:[{id:'a',name:'甲'},{id:'b',name:'乙'}]}}};window.changes=[];window.saves=[];window.slots=[];window.mount=()=>{window.editor?.dispose();const root=document.getElementById('root');root.innerHTML=${JSON.stringify(gridMarkup)};window.editor=new editorModule.ScheduleEditor(root,{context:()=>context,changed:(id,text)=>changes.push([id,text]),save:async id=>{saves.push(id);return true;},cancel:()=>{},slot:async(...args)=>{slots.push(args);return true;}});editor.bind();};mount();`);
  const click=selector=>evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);
  const waitOptions=()=>until(()=>evaluate("document.querySelectorAll('[data-candidate-id]').length>0"),'候选列表');
  await click('#pick-empty');await waitOptions();
  assert.equal(requests[0],'/api/workspace/cell-candidates/p/cell-empty?slot=0','无任务的通用单元格也必须由后端试排，不能把roster全部标为可用');
  assert.equal(await evaluate("document.querySelector('[data-candidate-id=a]').disabled"),false,'有冲突仍可手工填入，不能禁用');
  assert.match(await evaluate("document.querySelector('[data-candidate-id=a]').textContent"),/冲突/);
  assert.match(await evaluate("document.querySelector('[data-candidate-id=b]').textContent"),/待确认临时需求/);
  assert.doesNotMatch(await evaluate("document.getElementById('schedule-candidate-menu').textContent"),/名单匹配，补齐岗位后校验/);
  await click('[data-candidate-id=b]');assert.deepEqual(await evaluate('window.changes'),[['cell-empty','乙']]);assert.deepEqual(await evaluate('window.saves'),['cell-empty']);assert.deepEqual(await evaluate('window.slots'),[],'临时需求仍走原格子保存流程');
  await click('#pick-task');await waitOptions();
  assert.match(await evaluate("document.querySelector('[data-candidate-id=a]').textContent"),/填入后报警/);
  await evaluate("(()=>{const el=document.querySelector('.candidate-search');el.value='甲';el.dispatchEvent(new Event('input'));el.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));})()");
  await until(()=>evaluate('window.slots.length===1'),'精确姓名回车保留冲突安排');
  assert.deepEqual(await evaluate('window.slots'),[['existing',0,'a']]);
  await click('#pick-unknown');await waitOptions();
  assert.equal(requests.at(-1),'/api/workspace/cell-candidates/p/cell-unknown?slot=0');
  assert.equal(await evaluate("document.querySelectorAll('[data-candidate-id]:not(:disabled)').length"),0);
  assert.match(await evaluate("document.getElementById('schedule-candidate-menu').textContent"),/资料未核对.*确认岗位环节/);
  await click('#pick-task');await waitOptions();assert.equal(requests.at(-1),'/api/workspace/candidates/p/existing?slot=0');
  mode='revision';await click('#pick-empty');await until(()=>evaluate("document.querySelector('.candidate-notice')?.textContent.includes('班表已改变')"),'拒绝不同版本');
  assert.equal(await evaluate("document.querySelectorAll('[data-candidate-id]:not(:disabled)').length"),0);
  mode='held';const prior=requests.length;await click('#pick-empty');await until(()=>requests.length>prior,'挂起旧请求');
  assert.equal(await evaluate("document.querySelectorAll('[data-candidate-id]:not(:disabled)').length"),0,'请求中无残留可选');
  mode='normal';await click('#pick-unknown');await waitOptions();const latest=await evaluate("document.getElementById('schedule-candidate-menu').innerHTML");held.shift()();await delay(100);assert.equal(await evaluate("document.getElementById('schedule-candidate-menu').innerHTML"),latest,'旧请求不覆盖当前格');
  mode='error';await click('#pick-empty');await until(()=>evaluate("document.querySelector('.candidate-notice')?.textContent.includes('查询失败')"),'请求失败');assert.equal(await evaluate("document.querySelectorAll('[data-candidate-id]:not(:disabled)').length"),0);
  mode='preview-error';await evaluate("const el=document.getElementById('empty-text');el.textContent='乙';el.dispatchEvent(new InputEvent('input',{bubbles:true}));");
  await until(()=>evaluate("document.querySelector('.candidate-notice')?.textContent.includes('检查未完成')"),'预览失败');
  assert.equal(await evaluate("document.querySelectorAll('[data-candidate-id]:not(:disabled)').length"),0,'预览检查失败后也不能残留可选人');
  mode='held';const detachedCount=requests.length;await click('#pick-empty');await until(()=>requests.length>detachedCount,'待移除旧请求');await evaluate("document.getElementById('root').innerHTML='<p>已切换</p>'");held.shift()();await delay(100);assert.equal(await evaluate("document.querySelectorAll('[data-candidate-id]').length"),0,'旧格子移除后不展示迟到名单');
});
