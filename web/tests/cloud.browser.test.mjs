import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createServer} from 'node:http';
import {spawn,execFileSync} from 'node:child_process';
import {readFile,mkdir,mkdtemp,writeFile} from 'node:fs/promises';
import {join,resolve,extname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer as createTcpServer} from 'node:net';
import {continuationAcceptance,savedDayContinuationAcceptance,partTimeDayAcceptance} from './continuation-acceptance.mjs';

const repo=fileURLToPath(new URL('../../',import.meta.url));
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function until(fn,label,ms=90000){const end=Date.now()+ms;while(Date.now()<end){if(await fn())return;await delay(80)}throw Error('等待超时：'+label)}
test('cloud UI with simulated Supabase: actual save entry, recovery, failures, conflict, auth, exit and responsive layout',{timeout:300000},async t=>{
  const development=process.env.GATE_TEST_CLOUD_DEV==='1';
  if(!development){
    console.log('Preparing cloud mock build');
    execFileSync(process.platform==='win32'?'npm.cmd':'npm',['run','build:cloud','--','--outDir','../build/cloud-mock'],{cwd:join(repo,'web'),env:{...process.env,VITE_SUPABASE_URL:'https://gate-cloud-test.invalid',VITE_SUPABASE_PUBLISHABLE_KEY:'sb_publishable_test'},shell:process.platform==='win32',stdio:'pipe'});
  }
  let stored=null,revision=0,saves=0,mode='',release;
  if(process.env.GATE_TEST_CONTINUATION_SNAPSHOT){const snapshot=JSON.parse(await readFile(process.env.GATE_TEST_CONTINUATION_SNAPSHOT,'utf8'));stored=snapshot.documents;revision=snapshot.revision;}
  let launcherOutput='';
  let holder='',request=null; let reads=0;
  const errors=[],dialogs=[];
  const token='e30.'+Buffer.from(JSON.stringify({sub:'11111111-1111-4111-8111-111111111111',role:'authenticated',exp:Math.floor(Date.now()/1000)+3600})).toString('base64url')+'.test';
  const server=createServer(async(req,res)=>{
    try{
      const url=new URL(req.url,'http://localhost');
      if(development){
        res.setHeader('Access-Control-Allow-Origin','*');
        res.setHeader('Access-Control-Allow-Headers',req.headers['access-control-request-headers']??'authorization,apikey,content-type,x-client-info');
        res.setHeader('Access-Control-Allow-Methods','GET,POST,OPTIONS');
        if(req.method==='OPTIONS'){res.statusCode=204;return res.end()}
      }
      if(url.pathname.startsWith('/auth/')||url.pathname.startsWith('/rest/')){
        const chunks=[];for await(const chunk of req)chunks.push(chunk);const body=JSON.parse(Buffer.concat(chunks).toString()||'{}');
        res.setHeader('Content-Type','application/json');
        if(url.pathname.startsWith('/rest/')&&!req.headers.authorization){res.statusCode=403;return res.end(JSON.stringify({code:'42501',message:'permission denied'}))}
        if(url.pathname==='/auth/v1/token')return res.end(JSON.stringify({access_token:token,refresh_token:'test-refresh',expires_in:3600,token_type:'bearer',user:{id:'11111111-1111-4111-8111-111111111111',email:body.email,aud:'authenticated'}}));
        if(url.pathname==='/auth/v1/logout')return res.end('{}');
        if(mode==='permission'){res.statusCode=403;return res.end(JSON.stringify({code:'42501',message:'ADMIN_REQUIRED'}))}
        const fail=message=>{res.statusCode=400;res.end(JSON.stringify({message}))};
        if(url.pathname.endsWith('gate_acquire_session')){if(holder&&holder!==body.p_session)return fail('SESSION_ACTIVE');holder=body.p_session;res.statusCode=204;return res.end()}
        if(url.pathname.endsWith('gate_request_login')){request={id:body.p_request,status:'pending',requester:body.p_session};return res.end(JSON.stringify(request))}
        if(url.pathname.endsWith('gate_poll_login'))return res.end(JSON.stringify(request));
        if(url.pathname.endsWith('gate_touch_session')){if(holder!==body.p_session)return fail('SESSION_EXPIRED');res.statusCode=204;return res.end()}
        if(url.pathname.endsWith('gate_release_session')){if(holder===body.p_session)holder='';res.statusCode=204;return res.end()}
        if(url.pathname.endsWith('gate_cancel_login')){if(request?.requester===body.p_session&&request.status==='pending')request.status='cancelled';res.statusCode=204;return res.end()}
        if(url.pathname.endsWith('gate_decide_login')){if(holder!==body.p_session)return fail('SESSION_EXPIRED');if(body.p_allow)holder=request.requester;request.status=body.p_allow?'approved':'rejected';res.statusCode=204;return res.end()}
        if(body.p_session!==holder)return fail('SESSION_EXPIRED');
        if(url.pathname.endsWith('gate_read_workspace_session')){reads++;return res.end(JSON.stringify({revision,documents:mode==='corrupt'?{}:stored}))}
        if(url.pathname.endsWith('gate_save_workspace_session')){
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
  let pageUrl=`http://127.0.0.1:${server.address().port}/gate-schedule/`;
  if(development){
    const socket=createTcpServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));
    const port=socket.address().port;await new Promise(r=>socket.close(r));
    const launcher=spawn(process.env.GATE_TEST_PYTHON??(process.platform==='win32'?join(repo,'.venv-local/Scripts/python.exe'):'python3'),
      ['-B',join(repo,'dev.py'),'--no-browser','--port',String(port)],
      {cwd:repo,windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8',TEMP:join(repo,'.tmp'),TMP:join(repo,'.tmp'),VITE_SUPABASE_URL:`http://127.0.0.1:${server.address().port}`,VITE_SUPABASE_PUBLISHABLE_KEY:'sb_publishable_test'},stdio:['ignore','pipe','pipe']});
    for(const stream of [launcher.stdout,launcher.stderr])stream.on('data',chunk=>{launcherOutput=(launcherOutput+chunk.toString()).slice(-16000)});
    t.after(()=>{if(launcher.exitCode===null){if(process.platform==='win32')execFileSync('taskkill',['/PID',String(launcher.pid),'/T','/F'],{stdio:'ignore'});else launcher.kill('SIGINT')}});
    pageUrl=`http://127.0.0.1:${port}/`;
    await until(async()=>{
      if(launcher.exitCode!==null)throw Error('dev.py exited: '+launcherOutput);
      try{return (await fetch(pageUrl)).ok}catch{return false}
    },'dev.py云端入口');
    assert.match(launcherOutput,/dev:cloud/);
    console.log('Testing dev.py cloud entry');
  }
  await mkdir(join(repo,'.tmp'),{recursive:true});const directory=await mkdtemp(join(repo,'.tmp/cloud-browser-'));
  let browser,ws;let next=0;const pending=new Map();
  t.after(async()=>{await writeFile(join(directory,'result.json'),JSON.stringify({errors,dialogs,saves,revision,launcherOutput},null,2));ws?.close();browser?.kill();await new Promise(r=>{server.close(r);server.closeAllConnections()})});
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
  await call('Page.navigate',{url:pageUrl});
  const click=selector=>evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);
  const value=(selector,value)=>evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});e.value=${JSON.stringify(value)};e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}))})()`);
  const ready=()=>until(()=>evaluate('!!document.querySelector("[data-view=config]")'),'工作台');
  const login=async()=>{await until(()=>evaluate('crossOriginIsolated && !!document.querySelector("#cloud-login-form")'),'隔离后的登录');await value('#cloud-email','admin@example.test');await value('#cloud-password','test-only');await click('#cloud-login-form button')};
  // Capture alerts while allowing tests to control application confirms.
  // The COI service worker reloads the first non-isolated document. Wait for its
  // replacement before interacting with the form or installing dialog hooks.
  await until(()=>evaluate('crossOriginIsolated && !!document.querySelector("#cloud-login-form")'),'隔离后的登录表单');
  await evaluate('window.alerts=[];window.alert=m=>window.alerts.push(m);window.confirm=()=>true');
  await login();if(!stored){await until(()=>evaluate('!!document.querySelector("#cloud-empty")'),'首次选择');await click('#cloud-empty');}await ready();
  const addPerson=async name=>{await click('[data-view=config]');await click('#add-person');await value('#person-name',name);await click('#save-person');await until(()=>evaluate('!document.querySelector("#person-form")'),'保存人员')};
  const save=async()=>{await click('#cloud-save');await until(()=>evaluate('!document.querySelector("#cloud-save").disabled'),'云端回执')};
  const warning=()=>evaluate('(()=>{const e=new Event("beforeunload",{cancelable:true});window.dispatchEvent(e);return e.defaultPrevented})()');
  const label=()=>evaluate('document.querySelector("#cloud-status").textContent');
  if(process.env.GATE_TEST_CONTINUATION_ONLY==='1'){
    if(process.env.GATE_TEST_PARTTIME==='1')await partTimeDayAcceptance({evaluate,click,value,until,save,ready,login,released:()=>holder===''});
    else if(process.env.GATE_TEST_CONTINUATION_SNAPSHOT)await savedDayContinuationAcceptance({evaluate,click,value,until,save});
    else await continuationAcceptance({evaluate,click,value,until,ready,save,login,released:()=>holder===''});
    await writeFile(join(directory,'acceptance.json'),JSON.stringify({errors,dialogs,saves,revision},null,2));
    assert.equal(errors.length,0,JSON.stringify(errors));
    console.log('Continuation browser evidence: '+directory);return;
  }
  await addPerson('云端甲');assert.match(await label(),/有修改未上传/);assert.equal(await warning(),true);
  mode='delay';await click('#cloud-save');await until(()=>!!release,'保存请求');assert.match(await label(),/正在保存到云端/);assert.equal(await warning(),true);const count=saves;await click('#cloud-save');assert.equal(saves,count);release();mode='';await until(async()=>!(await evaluate('document.querySelector("#cloud-save").disabled')),'保存完成');
  assert.match(await label(),/已保存 \d{2}:\d{2}:\d{2}/);assert.equal(await warning(),false);assert.equal(stored['group-workspaces-v1'].groups.A.people[0].name,'云端甲');
  const title=await evaluate('document.querySelector("#cloud-status").title');assert.ok(title.includes(':'));
  await delay(5100);assert.equal(await label(),'✓ 已保存');
  await addPerson('云端乙');assert.match(await label(),/有修改未上传/);assert.equal(await warning(),true);
  // A failed request cannot alter the server revision and must produce an obvious alert.
  mode='network';const before=revision;await save();assert.equal(revision,before);assert.match(await label(),/保存失败，点击重试/);assert.match(await evaluate('window.alerts.at(-1)'),/网络或云端服务异常/);
  mode='';await click('#cloud-status');await until(()=>evaluate('!document.querySelector("#cloud-save").disabled'),'重试');assert.equal(await warning(),false);
  // Older saved workspaces omit the now-optional baggage time basis.
  const legacy=structuredClone(stored);
  for(const group of ['A','B'])legacy['group-workspaces-v1'].groups[group].workRules={pairs:[],baggage:[],baggageWindow:{startBeforeMinutes:180,endBeforeMinutes:60,rotationMinutes:60,remainderToleranceMinutes:15}};
  stored=legacy;
  const legacyRevision=revision,legacySaves=saves;
  await click('.cloud-more summary');await click('#cloud-logout');await until(()=>evaluate('document.body.textContent.includes("会话已结束")'),'旧云快照验收退出');
  await until(()=>holder==='','旧云快照验收释放会话');
  await evaluate('location.reload();true');await login();await ready();
  assert.deepEqual(stored,legacy);assert.equal(revision,legacyRevision);assert.equal(saves,legacySaves);
  const loadedLegacy=await evaluate('fetch("/api/groups/A/workspace").then(r=>r.json())');
  assert.equal(loadedLegacy.workRules.baggageWindow.timeBasis,'flight-window');
  assert.equal(loadedLegacy.people.length,2);
  await evaluate('window.alerts=[];window.alert=m=>window.alerts.push(m);window.confirm=()=>true');
  await save();
  assert.equal(revision,legacyRevision+1);
  assert.deepEqual(stored['group-workspaces-v1'].groups.A.people,legacy['group-workspaces-v1'].groups.A.people);
  assert.deepEqual(stored['group-workspaces-v1'].groups.A.plans,legacy['group-workspaces-v1'].groups.A.plans);
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
  // A second actual page requests access. Refusal preserves the original input,
  // and approval cannot run through an unfinished modal or failed upload.
  await evaluate('window.alerts=[];window.alert=m=>window.alerts.push(m);window.confirm=()=>true');
  const target=await(await fetch(`http://127.0.0.1:${port}/json/new?about:blank`,{method:'PUT'})).json();
  const peerSocket=new WebSocket(target.webSocketDebuggerUrl);await new Promise(r=>peerSocket.addEventListener('open',r,{once:true}));
  t.after(()=>peerSocket.close());let peerNext=0;const peerPending=new Map();
  peerSocket.addEventListener('message',event=>{const m=JSON.parse(event.data);if(m.method==='Runtime.exceptionThrown')errors.push(m.params);const p=peerPending.get(m.id);if(p){peerPending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result)}});
  const peerCall=(method,params={})=>new Promise((resolve,reject)=>{const id=++peerNext;const timer=setTimeout(()=>reject(Error('Peer CDP timeout: '+method)),25000);peerPending.set(id,{resolve:v=>{clearTimeout(timer);resolve(v)},reject:e=>{clearTimeout(timer);reject(e)}});peerSocket.send(JSON.stringify({id,method,params}))});
  const peer=async expression=>{const result=await peerCall('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(result.exceptionDetails)throw Error(JSON.stringify(result.exceptionDetails));return result.result.value};
  await peerCall('Runtime.enable');await peerCall('Page.enable');await peerCall('Page.navigate',{url:pageUrl});
  await until(()=>peer('crossOriginIsolated && !!document.querySelector("#cloud-login-form")'),'第二页面登录');
  await click('[data-view=config]');await click('#add-person');await value('#person-name','审批拒绝后输入保留');await evaluate('window.originalPersonInput=document.querySelector("#person-name")');
  const readsBefore=reads,saveBefore=saves;
  await peer(`document.querySelector('#cloud-email').value='admin@example.test';document.querySelector('#cloud-password').value='test-only';document.querySelector('#cloud-login-form button').click()`);
  await until(()=>peer('document.body.textContent.includes("等待原设备确认")'),'申请等待');
  assert.equal(reads,readsBefore);assert.equal(await peer('!!document.querySelector("[data-view=config]")'),false);
  await until(()=>evaluate('!!document.querySelector("#cloud-deny-login")'),'原设备申请通知');
  assert.equal(await evaluate(`(()=>{const b=document.querySelector('#cloud-deny-login');b.scrollIntoView({block:'center'});const r=b.getBoundingClientRect();return document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)===b})()`),true,'refusal remains reachable above the existing modal');
  await click('#cloud-deny-login');await until(()=>peer('document.body.textContent.includes("原设备已拒绝")'),'新设备拒绝提示');
  assert.equal(saves,saveBefore);assert.equal(await evaluate('window.originalPersonInput===document.querySelector("#person-name")'),true);assert.equal(await evaluate('document.querySelector("#person-name").value'),'审批拒绝后输入保留');
  await peer(`[...document.querySelectorAll('button')].find(b=>b.textContent==='重新申请登录').click()`);
  await until(()=>evaluate('!!document.querySelector("#cloud-allow-login")'),'再次申请');await click('#cloud-allow-login');
  await until(()=>evaluate('document.querySelector(".cloud-session-notice").textContent.includes("未交接")'),'未完成表单禁止交接');assert.equal(saves,saveBefore);
  await click('#save-person');await until(()=>evaluate('!document.querySelector("#person-form")'),'完成原输入');
  mode='network';await click('#cloud-allow-login');await until(()=>evaluate('window.alerts.at(-1)?.includes("网络或云端服务异常")'),'保存失败保留');assert.equal(await peer('!!document.querySelector("#cloud-save")'),false);assert.equal(await evaluate('!!document.querySelector("#cloud-save")'),true);
  mode='';await click('#cloud-allow-login');await until(()=>evaluate('document.body.textContent.includes("本设备已结束编辑")'),'原设备结束');
  await until(()=>peer('!!document.querySelector("[data-view=config]")'),'新设备读取最新资料');
  assert.equal(stored['group-workspaces-v1'].groups.A.people.at(-1).name,'审批拒绝后输入保留');assert.equal(await peer('document.querySelector("#cloud-status").textContent.includes("已读取")'),true);
  // Heartbeat/poll failures keep the same editor and inputs, then revalidate.
  await peer(`document.querySelector('[data-view=config]').click();document.querySelector('#add-person').click();document.querySelector('#person-name').value='断线输入';window.keptInput=document.querySelector('#person-name')`);
  const activeHolder=holder;holder='different-device-123456';request=null;
  await until(()=>peer('!!document.querySelector("#cloud-resume-session")'),'会话中断保留');assert.equal(await peer('window.keptInput===document.querySelector("#person-name") && window.keptInput.value==="断线输入"'),true);
  holder=activeHolder;await peer('document.querySelector("#cloud-resume-session").click()');await until(()=>peer('!document.querySelector("#app").inert'),'验证恢复');
  await peer(`document.querySelector('#app-dialog [data-close-dialog]').click();window.confirm=()=>true;document.querySelector('#cloud-logout').click()`);await until(()=>peer('document.body.textContent.includes("会话已结束")'),'第二页面退出');
  // Cross-origin development requests can still be completing their preflight.
  // Finish releasing the mock lease before injecting a separate permission error.
  await until(()=>holder==='','第二页面释放使用权');
  // A read permission error and malformed remote data must not create a working default workspace.
  mode='permission';await evaluate('location.reload();true');await login();await until(()=>evaluate('document.body.textContent.includes("权限不足")'),'权限拒绝');assert.equal(await evaluate('!!document.querySelector("#cloud-save")'),false);
  mode='corrupt';await click('button');
  try{await until(()=>evaluate('document.body.textContent.includes("数据校验")'),'损坏拒绝')}
  catch(error){await writeFile(join(directory,'corrupt-failure.json'),JSON.stringify({page:await evaluate('document.body.innerText'),reads,holder,request},null,2));throw error}
  assert.equal(await evaluate('!!document.querySelector("[data-view=config]")'),false);
  assert.equal(errors.length,0,JSON.stringify(errors));
  console.log('Cloud browser evidence: '+directory);
});
