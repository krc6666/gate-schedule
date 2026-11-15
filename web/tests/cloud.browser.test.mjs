import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createServer} from 'node:http';
import {spawn,execFileSync} from 'node:child_process';
import {readFile,mkdir,mkdtemp,writeFile} from 'node:fs/promises';
import {join,resolve,extname} from 'node:path';
import {fileURLToPath} from 'node:url';

const repo=fileURLToPath(new URL('../../',import.meta.url));
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function until(fn,label,ms=90000){const end=Date.now()+ms;while(Date.now()<end){if(await fn())return;await delay(80)}throw Error('等待超时：'+label)}
test('cloud UI with simulated Supabase: actual save entry, recovery, failures, conflict, auth, exit and responsive layout',{timeout:300000},async t=>{
  console.log('Preparing cloud mock build');
  execFileSync(process.platform==='win32'?'npm.cmd':'npm',['run','build:cloud','--','--outDir','../build/cloud-mock'],{cwd:join(repo,'web'),env:{...process.env,VITE_SUPABASE_URL:'https://gate-cloud-test.invalid',VITE_SUPABASE_PUBLISHABLE_KEY:'sb_publishable_test'},shell:process.platform==='win32',stdio:'pipe'});
  let stored=null,revision=0,saves=0,mode='',release;
  const errors=[],dialogs=[];
  const token='e30.'+Buffer.from(JSON.stringify({sub:'11111111-1111-4111-8111-111111111111',role:'authenticated',exp:Math.floor(Date.now()/1000)+3600})).toString('base64url')+'.test';
  const server=createServer(async(req,res)=>{
    try{
      const url=new URL(req.url,'http://localhost');
      if(url.pathname.startsWith('/auth/')||url.pathname.startsWith('/rest/')){
        const chunks=[];for await(const chunk of req)chunks.push(chunk);const body=JSON.parse(Buffer.concat(chunks).toString()||'{}');
        res.setHeader('Content-Type','application/json');
        if(url.pathname==='/auth/v1/token')return res.end(JSON.stringify({access_token:token,refresh_token:'test-refresh',expires_in:3600,token_type:'bearer',user:{id:'11111111-1111-4111-8111-111111111111',email:body.email,aud:'authenticated'}}));
        if(url.pathname==='/auth/v1/logout')return res.end('{}');
        if(mode==='permission'){res.statusCode=403;return res.end(JSON.stringify({code:'42501',message:'ADMIN_REQUIRED'}))}
        if(url.pathname.endsWith('gate_read_workspace'))return res.end(JSON.stringify({revision,documents:mode==='corrupt'?{}:stored}));
        if(url.pathname.endsWith('gate_save_workspace')){
          saves++;
          if(mode==='delay')await new Promise(r=>{release=r});
          if(mode==='network'){res.statusCode=503;return res.end(JSON.stringify({message:'unavailable'}))}
          if(body.expected_revision!==revision){res.statusCode=409;return res.end(JSON.stringify({code:'40001',message:'WORKSPACE_CONFLICT'}))}
          stored=body.new_documents;revision++;return res.end(JSON.stringify({revision}));
        }
        res.statusCode=404;return res.end('{}');
      }
      assert.ok(url.pathname.startsWith('/gate-schedule/'));
      const name=decodeURIComponent(url.pathname.slice('/gate-schedule/'.length))||'index.html';
      const path=resolve(repo,'build/cloud-mock',name);
      assert.ok(path.startsWith(resolve(repo,'build/cloud-mock')+ (process.platform==='win32'?'\\':'/')));
      let data=await readFile(path);
      if(extname(path)==='.js')data=Buffer.from(data.toString().replaceAll('https://gate-cloud-test.invalid',`http://127.0.0.1:${server.address().port}`));
      res.setHeader('Content-Type',({'.html':'text/html','.js':'application/javascript','.wasm':'application/wasm','.css':'text/css','.json':'application/json','.zip':'application/zip'})[extname(path)]??'application/octet-stream');
      res.end(data);
    }catch(error){res.statusCode=500;res.end(String(error))}
  });
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  await mkdir(join(repo,'.tmp'),{recursive:true});const directory=await mkdtemp(join(repo,'.tmp/cloud-browser-'));
  let browser,ws;let next=0;const pending=new Map();
  t.after(async()=>{ws?.close();browser?.kill();server.closeAllConnections();await new Promise(r=>server.close(r));await writeFile(join(directory,'result.json'),JSON.stringify({errors,dialogs,saves,revision},null,2))});
  let browserError='';
  browser=spawn(process.env.GATE_TEST_BROWSER??'C:/Program Files/Google/Chrome/Application/chrome.exe',['--headless=new','--no-first-run','--no-default-browser-check','--disable-background-networking','--remote-debugging-port=0','--user-data-dir='+join(directory,'chrome'),'about:blank'],{windowsHide:true,stdio:['ignore','ignore','pipe']});
  browser.stderr.on('data',chunk=>{browserError=(browserError+chunk.toString()).slice(-8000)});
  browser.on('error',error=>{browserError=String(error)});
  let port;
  try{await until(async()=>{
    if(browser.exitCode!==null||browser.signalCode!==null)throw Error(`Chrome exited: ${browser.exitCode??browser.signalCode}; ${browserError}`);
    try{port=(await readFile(join(directory,'chrome/DevToolsActivePort'),'utf8')).trim().split(/\r?\n/)[0];return port}catch{return false}
  },'Chrome端口',15000)}catch(error){throw Error(`${error}; Chrome startup: ${browserError}`)}
  const targets=await(await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  ws=new WebSocket(targets.find(x=>x.type==='page').webSocketDebuggerUrl);await new Promise(r=>ws.addEventListener('open',r,{once:true}));
  ws.addEventListener('message',event=>{const m=JSON.parse(event.data);if(m.method==='Runtime.exceptionThrown')errors.push(m.params);if(m.method==='Page.javascriptDialogOpening')dialogs.push(m.params);const p=pending.get(m.id);if(p){pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result)}});
  const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++next;const timer=setTimeout(()=>reject(Error('CDP timeout: '+method)),25000);pending.set(id,{resolve:value=>{clearTimeout(timer);resolve(value)},reject:error=>{clearTimeout(timer);reject(error)}});ws.send(JSON.stringify({id,method,params}))});
  const evaluate=async expression=>{const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value};
  await call('Runtime.enable');await call('Page.enable');
  await call('Page.navigate',{url:`http://127.0.0.1:${server.address().port}/gate-schedule/`});
  const click=selector=>evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);
  const value=(selector,value)=>evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});e.value=${JSON.stringify(value)};e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}))})()`);
  const ready=()=>until(()=>evaluate('!!document.querySelector("[data-view=config]")'),'工作台');
  const login=async()=>{await until(()=>evaluate('crossOriginIsolated && !!document.querySelector("#cloud-login-form")'),'隔离后的登录');await value('#cloud-email','admin@example.test');await value('#cloud-password','test-only');await click('#cloud-login-form button')};
  // Capture alerts while allowing tests to control application confirms.
  // The COI service worker reloads the first non-isolated document. Wait for its
  // replacement before interacting with the form or installing dialog hooks.
  await until(()=>evaluate('crossOriginIsolated && !!document.querySelector("#cloud-login-form")'),'隔离后的登录表单');
  await evaluate('window.alerts=[];window.alert=m=>window.alerts.push(m);window.confirm=()=>true');
  await login();await until(()=>evaluate('!!document.querySelector("#cloud-empty")'),'首次选择');await click('#cloud-empty');await ready();
  const addPerson=async name=>{await click('[data-view=config]');await click('#add-person');await value('#person-name',name);await click('#save-person');await until(()=>evaluate('!document.querySelector("#person-form")'),'保存人员')};
  const save=async()=>{await click('#cloud-save');await until(()=>evaluate('!document.querySelector("#cloud-save").disabled'),'云端回执')};
  const warning=()=>evaluate('(()=>{const e=new Event("beforeunload",{cancelable:true});window.dispatchEvent(e);return e.defaultPrevented})()');
  const label=()=>evaluate('document.querySelector("#cloud-status").textContent');
  await addPerson('云端甲');assert.match(await label(),/有修改未上传/);assert.equal(await warning(),true);
  mode='delay';await click('#cloud-save');await until(()=>!!release,'保存请求');assert.match(await label(),/正在保存到云端/);assert.equal(await warning(),true);const count=saves;await click('#cloud-save');assert.equal(saves,count);release();mode='';await until(async()=>!(await evaluate('document.querySelector("#cloud-save").disabled')),'保存完成');
  assert.match(await label(),/已保存 \d{2}:\d{2}:\d{2}/);assert.equal(await warning(),false);assert.equal(stored['group-workspaces-v1'].groups.A.people[0].name,'云端甲');
  const title=await evaluate('document.querySelector("#cloud-status").title');assert.ok(title.includes(':'));
  await delay(5100);assert.equal(await label(),'✓ 已保存');
  await addPerson('云端乙');assert.match(await label(),/有修改未上传/);assert.equal(await warning(),true);
  // A failed request cannot alter the server revision and must produce an obvious alert.
  mode='network';const before=revision;await save();assert.equal(revision,before);assert.match(await label(),/保存失败，点击重试/);assert.match(await evaluate('window.alerts.at(-1)'),/网络或云端服务异常/);
  mode='';await click('#cloud-status');await until(()=>evaluate('!document.querySelector("#cloud-save").disabled'),'重试');assert.equal(await warning(),false);
  // A second page/device saved first: the stale UI must never silently overwrite.
  await addPerson('冲突保留');revision++;await save();assert.match(await evaluate('window.alerts.at(-1)'),/版本冲突/);assert.equal(stored['group-workspaces-v1'].groups.A.people.length,2);
  await evaluate('window.confirm=()=>false');await click('.cloud-more summary');await click('#cloud-logout');assert.ok(await evaluate('!!document.querySelector("#cloud-save")'));assert.equal((await evaluate('fetch("/api/groups/A/workspace").then(r=>r.json())')).people.length,3);
  await evaluate('window.confirm=()=>true');await click('#cloud-logout');await until(()=>evaluate('document.body.textContent.includes("会话已结束")'),'退出');
  await evaluate('location.reload();true');await login();await ready();assert.match(await label(),/已读取/);assert.ok(!(await label()).includes(':'));assert.equal((await evaluate('fetch("/api/groups/A/workspace").then(r=>r.json())')).people.length,2);assert.equal(await warning(),false);
  // Saved state and menus at desktop and mobile widths.
  for(const width of [1440,390]){
    await call('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:false});
    assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'),true,`overflow at ${width}`);
    assert.equal(await evaluate('document.querySelector(".cloud-more").open'),false);
    const shot=await call('Page.captureScreenshot',{format:'png'});await writeFile(join(directory,`cloud-${width}.png`),Buffer.from(shot.data,'base64'));
    await click('.cloud-more summary');assert.equal(await evaluate('getComputedStyle(document.querySelector("#cloud-logout")).display')!=='none',true);await click('.cloud-more summary');
  }
  // A read permission error and malformed remote data must not create a working default workspace.
  mode='permission';await evaluate('location.reload();true');await login();await until(()=>evaluate('document.body.textContent.includes("权限不足")'),'权限拒绝');assert.equal(await evaluate('!!document.querySelector("#cloud-save")'),false);
  mode='corrupt';await click('button');await until(()=>evaluate('document.body.textContent.includes("数据校验")'),'损坏拒绝');assert.equal(await evaluate('!!document.querySelector("[data-view=config]")'),false);
  assert.equal(errors.length,0,JSON.stringify(errors));
  console.log('Cloud browser evidence: '+directory);
});
