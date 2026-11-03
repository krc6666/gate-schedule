import assert from 'node:assert/strict';
import {test} from 'node:test';
import {build} from 'esbuild';
import {spawn} from 'node:child_process';
import {existsSync} from 'node:fs';
import {mkdir,mkdtemp,readFile,rm} from 'node:fs/promises';
import {createServer} from 'node:http';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';

const repo=fileURLToPath(new URL('../../',import.meta.url)),src=join(repo,'web/src');
const bundle=await build({stdin:{contents:'export {roleStatisticsHtml} from "./role-statistics";export {statisticsPage,dutyReportHtml} from "./duty-statistics";',resolveDir:src},bundle:true,write:false,format:'esm',target:'es2022'});
const {roleStatisticsHtml,statisticsPage,dutyReportHtml}=await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);
const data=(month='2026-08',revision=7)=>({month,revision,months:['2026-08','2026-07'],members:[{memberId:'a',memberName:'甲',removed:false},{memberId:'b',memberName:'乙',removed:false}],totals:[{memberId:'a',count:6},{memberId:'b',count:0}],roles:[{roleName:'普通岗',members:[{memberId:'a',memberName:'甲',qualified:true,count:2,dates:[`${month}-02 · F1`,`${month}-04 · F2`]},{memberId:'b',memberName:'乙',qualified:true,count:0,dates:[]}]},{roleName:'旧岗位',members:[{memberId:'a',memberName:'甲',qualified:false,count:4,dates:[`${month}-06 · F3`]}]}]});

test('值班统计参照值机默认收起，模板工具在顶部',()=>{
  const state={people:[]};
  const page=statisticsPage(state,null,'2026-08-02');
  assert.match(page,/id="download-duty-template"/);assert.match(page,/id="import-duty-roster"/);
  assert.ok(page.indexOf('id="download-duty-template"')<page.indexOf('id="duty-report"'));
  assert.ok(page.indexOf('id="import-duty-roster"')<page.indexOf('id="duty-report"'));
  const report=dutyReportHtml({month:'2026-08',people:[],rows:[],dutyDifference:0,standbyDifference:0},state);
  assert.match(report,/<details id="duty-general-details" class="duty-roster-details">/);
  assert.doesNotMatch(report,/<details[^>]*\bopen\b/);
  assert.doesNotMatch(report,/id="(?:download-duty-template|import-duty-roster)"/);
});

test('所有岗位实际次数、按岗合计、零次与姓名筛选及安全文字',()=>{
  const selection={month:'2026-08',roleName:'',query:''};
  const all=roleStatisticsHtml(data(),selection);
  assert.match(all,/所有岗位次数统计/);assert.match(all,/<strong>6<\/strong>/);assert.match(all,/2026-08-02 · F1/);
  assert.match(all,/本月尚无班表记录/);assert.match(all,/当前无此岗资质，保留历史次数/);assert.doesNotMatch(all,/data-priority-adjust/);
  const filtered=roleStatisticsHtml(data(),{...selection,roleName:'普通岗',query:'甲'});
  const table=filtered.split('<table')[1];assert.match(table,/<strong>2<\/strong>/);assert.doesNotMatch(table,/<th>旧岗位<\/th>|data-role-member="b"/);
  assert.match(roleStatisticsHtml(data(),{...selection,query:'不存在'}),/没有符合姓名筛选的人员/);
  const dangerous=data();dangerous.members[0].memberName='<script>alert(1)</script>';
  assert.doesNotMatch(roleStatisticsHtml(dangerous,selection),/<script>/);
  assert.match(roleStatisticsHtml(null,selection,false,'读取失败'),/retry-role-statistics/);
  assert.doesNotMatch(roleStatisticsHtml(data(),selection,true),/<table/);
});

test('所有岗位筛选真实Chrome：切月过期隔离、失败重试、版本保护与中文输入', {timeout:60000},async t=>{
  const temporary=join(repo,'.tmp');await mkdir(temporary,{recursive:true});const directory=await mkdtemp(join(temporary,'role-statistics-ui-'));
  const chrome=[process.env.GATE_TEST_BROWSER,'C:/Program Files/Google/Chrome/Application/chrome.exe','C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'].find(p=>p&&existsSync(p));assert.ok(chrome);
  const app=await build({stdin:{contents:'import {bindRoleStatistics} from "./role-statistics";let dispose=()=>{};const selection={month:"2026-08",roleName:"",query:""};window.mount=(revision=7)=>{dispose();document.querySelector("#app").innerHTML="<div id=role-statistics-root></div>";dispose=bindRoleStatistics(document.querySelector("#app"),{revision,priorityStatistics:{month:"2026-08"}},selection);};window.unmount=()=>{dispose();document.querySelector("#app").innerHTML="离开统计";};window.mount();',resolveDir:src},bundle:true,write:false,format:'iife',target:'es2022'});
  const script=app.outputFiles[0].text,held=[],writes=[];let mode='normal';
  const server=createServer((req,res)=>{
    if(req.method!=='GET')writes.push(req.url);
    if(req.url==='/app.js'){res.setHeader('Content-Type','text/javascript');res.end(script);return;}
    if(req.url.startsWith('/api/role-statistics')){const month=new URL(req.url,'http://test').searchParams.get('month'),currentMode=mode;const reply=()=>{res.setHeader('Content-Type','application/json');if(currentMode==='error'){res.statusCode=503;res.end('{}');}else res.end(JSON.stringify(data(month,currentMode==='revision'?8:7)));};if(mode==='held')held.push(reply);else reply();return;}
    res.end('<!doctype html><meta charset="utf-8"><div id="app"></div><script src="/app.js"></script>');
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const base=`http://127.0.0.1:${server.address().port}`;
  let browser,ws,sessionId,id=0;const pending=new Map(),errors=[];const delay=ms=>new Promise(r=>setTimeout(r,ms));
  const until=async(fn,label)=>{const end=Date.now()+12000;while(Date.now()<end){const v=await fn();if(v)return v;await delay(25);}throw Error(label);};
  const call=(method,params={})=>new Promise((resolve,reject)=>{const key=++id,timer=setTimeout(()=>{pending.delete(key);reject(Error(method));},12000);pending.set(key,{resolve:v=>{clearTimeout(timer);resolve(v);},reject});ws.send(JSON.stringify({id:key,method,params,...(sessionId?{sessionId}:{})}));});
  const evaluate=async expression=>{const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});assert.equal(r.exceptionDetails,undefined,JSON.stringify(r.exceptionDetails));return r.result.value;};
  const month=value=>evaluate(`(()=>{const el=document.querySelector('#role-statistics-month');el.value=${JSON.stringify(value)};el.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  const ready=()=>until(()=>evaluate('!!document.querySelector(".role-statistics-table")'),'次数表加载');
  t.after(async()=>{held.splice(0).forEach(reply=>reply());ws?.close();if(browser&&browser.exitCode===null){const done=new Promise(r=>browser.once('exit',r));browser.kill();await Promise.race([done,delay(3000)]);}await new Promise(resolve=>server.close(resolve));assert.equal(dirname(resolve(directory)),resolve(temporary));await rm(directory,{recursive:true,force:true,maxRetries:8,retryDelay:250});});
  browser=spawn(chrome,['--headless=new','--remote-debugging-port=0',`--user-data-dir=${join(directory,'chrome')}`,'--no-first-run','--no-default-browser-check','about:blank'],{windowsHide:true,stdio:'ignore'});
  const ports=await until(async()=>{try{return await readFile(join(directory,'chrome/DevToolsActivePort'),'utf8');}catch{return false;}},'Chrome启动');const [port,path]=ports.trim().split(/\r?\n/);ws=new WebSocket(`ws://127.0.0.1:${port}${path}`);await new Promise(r=>ws.addEventListener('open',r,{once:true}));
  ws.addEventListener('message',event=>{const m=JSON.parse(event.data),p=pending.get(m.id);if(p){pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result);}if(m.method==='Runtime.exceptionThrown')errors.push(m.params);});
  const target=await call('Target.createTarget',{url:'about:blank'});sessionId=(await call('Target.attachToTarget',{targetId:target.targetId,flatten:true})).sessionId;await call('Page.enable');await call('Runtime.enable');await call('Page.navigate',{url:base});await ready();
  await evaluate('document.querySelector("[data-role-filter=普通岗]").click()');assert.equal(await evaluate('document.querySelector("[data-role-member=a] [data-role-total]").textContent'),'2');
  await evaluate('(()=>{const el=document.querySelector("#role-statistics-name");window.originalNameInput=el;el.value="甲";el.dispatchEvent(new InputEvent("input",{bubbles:true,isComposing:true}));})()');assert.ok(await evaluate('window.originalNameInput===document.querySelector("#role-statistics-name")'));
  await evaluate('document.querySelector("#role-statistics-name").dispatchEvent(new InputEvent("input",{bubbles:true,isComposing:false}))');assert.equal(await evaluate('document.querySelectorAll("tbody tr").length'),1);
  await evaluate('document.querySelector(".priority-count-detail summary").click()');assert.ok(await evaluate('document.querySelector("details").open'));
  mode='held';await month('2026-07');await until(()=>held.length===1,'旧月请求等待');assert.equal(await evaluate('!!document.querySelector("table")'),false);
  mode='normal';await month('2026-08');await ready();held.shift()();await delay(80);assert.match(await evaluate('document.querySelector("table").textContent'),/2026-08-02/);
  mode='error';await month('2026-07');await until(()=>evaluate('!!document.querySelector("#retry-role-statistics")'),'错误重试');assert.equal(await evaluate('!!document.querySelector("table")'),false);
  mode='normal';await evaluate('document.querySelector("#retry-role-statistics").click()');await ready();assert.match(await evaluate('document.querySelector("table").textContent'),/2026-07-02/);
  mode='revision';await month('2026-08');await until(()=>evaluate('document.querySelector("[data-role-statistics-notice]").textContent.includes("数据已改变")'),'版本保护');assert.equal(await evaluate('!!document.querySelector("table")'),false);
  mode='held';await evaluate('document.querySelector("#retry-role-statistics").click()');await until(()=>held.length===1,'离页请求等待');await evaluate('window.unmount()');held.shift()();await delay(80);assert.equal(await evaluate('document.querySelector("#app").textContent'),'离开统计');
  assert.deepEqual(writes,[]);assert.deepEqual(errors,[]);
});
