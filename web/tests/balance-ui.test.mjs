import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { readFile, writeFile, mkdtemp, mkdir, rm } from 'node:fs/promises';
import { createServer } from 'node:http';
import { dirname, join } from 'node:path';

const repo=fileURLToPath(new URL('../../',import.meta.url));
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));

const bundle=await build({stdin:{contents:'export {rulesPage} from "./workbench-pages";export {scheduleExtras} from "./schedule-extras";',resolveDir:fileURLToPath(new URL('../src/',import.meta.url))},bundle:true,write:false,format:'esm',target:'es2022'});
const {rulesPage,scheduleExtras}=await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);

test('规则页可选重点岗位和硬差距，次数修正不混在设置表里',()=>{
  const state={relaxedShiftSettings:{earlyDepartureCutoffTime:'12:00',afternoonRestStartTime:'12:00',afternoonRestEndTime:'18:00',lateShiftEndTime:'23:00',lateFlightNos:[]},halfRestSettings:{exemptFlightCount:true},personnelStatuses:["正常","病假","休假"],teamLeaderSettings:{lastUsed:true,exemptFlightCount:true},balanceSettings:{priorityEnabled:true,priorityFlightRoles:[{flightNo:'F0',roleName:'登机主验'}],priorityRoleNames:[],flightCountGap:1,priorityAdjustments:[]},configuration:{roleConfigs:[{roleName:'接机1'},{roleName:'登机主验'}],rotationEnabled:true,rotationMinutes:60,transitionMinutes:0},newRoleDefaults:{},flightLibrary:[{flightNo:'F0'}],plans:{},handoffPreferences:[]};
  const markup=rulesPage(state,null,null);
  assert.match(markup,/id="priority-enabled"[^>]*checked/);
  assert.match(markup,/data-priority-role="登机主验"[^>]*checked/);
  assert.match(markup,/data-priority-role="接机1"/);
  assert.match(markup,/id="flight-count-gap"[^>]*value="1"/);
  assert.match(markup,/id="save-rules"/);
  assert.match(markup,/本月.*次数|本自然月/);
  assert.match(markup,/重新排班/);
  assert.match(markup,/稀缺岗位资质轮换（自动）/);
  assert.match(markup,/正常参加排班且具备该岗资质/);
  assert.match(markup,/人数相同的岗位同等优先，无需手动勾选/);
  assert.match(markup,/只有1个合格人或无法安全换人时允许继续同人/);
  assert.doesNotMatch(markup,/data-priority-adjust/);
});

test('概览只消费memberFlights，不从matrix或任务明细重算航班',()=>{
  const schedule={members:[{id:'a',name:'甲'},{id:'b',name:'乙'}],memberFlights:[{memberId:'a',memberName:'甲',flightIds:['f1','f2'],flightNos:['CX937','KE0166'],count:2},{memberId:'b',memberName:'乙',flightIds:[],flightNos:[],count:0}],memberPaths:{a:[{task_id:'t',role_id:'不应出现在概览的岗位'}]},tasks:[{id:'t',flightId:'f1'}],flights:[{id:'f1',flightNo:'错误旧投影'}],matrix:{}};
  const markup=scheduleExtras({plans:{}},{day:{serviceDate:'2026-08-02'}},schedule);
  assert.doesNotMatch(markup, /<details|<summary/);
  const overview=markup.split('<section id="people-overview"')[1].split('<section id="previous-comparison"')[0];
  assert.match(overview,/参与航班/);
  assert.match(overview,/CX937、KE0166/);
  assert.match(overview,/<td>2<\/td>/);
  assert.match(overview,/尚无安排/);
  assert.doesNotMatch(overview,/岗位安排|不应出现在概览的岗位|错误旧投影/);
});

test('规则保存与重点次数：真实页面、累计修正、切月隔离和窄屏', {timeout:60000}, async t=>{
  const temporaryRoot=join(repo,'.tmp');await mkdir(temporaryRoot,{recursive:true});
  const directory=await mkdtemp(join(temporaryRoot,'balance-ui-'));
  const chrome=[process.env.GATE_TEST_BROWSER,'C:/Program Files/Google/Chrome/Application/chrome.exe','C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'].find(path=>path&&existsSync(path));assert.ok(chrome);
  const app=await build({entryPoints:[join(repo,'web/src/workbench.ts')],bundle:true,write:false,outdir:directory,format:'iife',target:'es2022'});
  const script=app.outputFiles.find(file=>file.path.endsWith('.js')).text,css=app.outputFiles.find(file=>file.path.endsWith('.css')).text;
  const settings={flightCountGap:null,priorityEnabled:false,priorityFlightRoles:[{flightNo:'F0',roleName:'\u4e3b\u9a8c'}],priorityRoleNames:['主验'],priorityAdjustments:[{month:'2026-08',memberId:'a',flightNo:'F0',roleName:'主验',delta:2}]};
  const stats=month=>({revision:state.revision,month,months:['2026-08','2026-07','2026-06'],totals:[{memberId:'a',count:23},{memberId:'b',count:1}],scopedTotals:[{flightNo:'',roleName:'核验',totals:[{memberId:'a',count:0}]},{flightNo:'F1',roleName:'',totals:[{memberId:'a',count:7}]}],roles:[{flightNo:'F0',roleName:'主验',members:[{memberId:'a',memberName:'甲',qualified:true,historyCount:3,adjustment:state.balanceSettings.priorityAdjustments.filter(item=>item.month===month).reduce((sum,item)=>sum+item.delta,0),effectiveCount:3+state.balanceSettings.priorityAdjustments.filter(item=>item.month===month).reduce((sum,item)=>sum+item.delta,0),dates:[`${month}-02`]},{memberId:'b',memberName:'乙',qualified:false,historyCount:1,adjustment:0,effectiveCount:1,dates:[`${month}-04`]}]},{flightNo:'F0',roleName:'核验',members:[{memberId:'a',memberName:'甲',qualified:true,historyCount:0,adjustment:0,effectiveCount:0,dates:[]}]},{flightNo:'F1',roleName:'主验',members:[{memberId:'a',memberName:'甲',qualified:true,historyCount:7,adjustment:0,effectiveCount:7,dates:[]}]}]});
  const state={relaxedShiftSettings:{earlyDepartureCutoffTime:'12:00',afternoonRestStartTime:'12:00',afternoonRestEndTime:'18:00',lateShiftEndTime:'23:00',lateFlightNos:[]},halfRestSettings:{exemptFlightCount:true},personnelStatuses:["正常","病假","休假"],teamLeaderSettings:{lastUsed:true,exemptFlightCount:true},revision:51,selectedPlanId:null,plans:{},balanceSettings:settings,people:[{id:'a',name:'甲',active:true,eligibleRoleNames:['主验','核验']}],configuration:{roleConfigs:[{roleName:'接机1',requiredCount:1,workMode:'arrival'},{roleName:'主验',requiredCount:1,workMode:'boarding'},{roleName:'核验',requiredCount:1,workMode:'boarding'}],rotationEnabled:false,rotationMinutes:60,transitionMinutes:0},displayPreferences:{layout:'flights',zoom:100},flightLibrary:[{flightNo:'F0'},{flightNo:'F1'}],flightTemplates:[],weeklyFlightPresets:{},newRoleDefaults:{},workModes:{boarding:'登机环节'},handoffPreferences:[],suggestedHandoffPreferences:[],migrationReport:[]};
  const snapshot=()=>({...state,priorityStatistics:stats('2026-08')});
  let rejectSettings=false,queryMode='normal';const commands=[],held=[];
  const server=createServer((request,response)=>{
    if(request.url==='/app.js'){response.setHeader('Content-Type','text/javascript');response.end(script);return;}
    if(request.url==='/app.css'){response.setHeader('Content-Type','text/css');response.end(css);return;}
    if(request.url==='/api/workspace'){response.setHeader('Content-Type','application/json');response.end(JSON.stringify(snapshot()));return;}
    if(request.url==='/api/workspace/command'){
      let body='';request.on('data',chunk=>body+=chunk);request.on('end',()=>{
        const command=JSON.parse(body);commands.push(command);response.setHeader('Content-Type','application/json');
        if(command.action==='save-rules'&&rejectSettings){response.statusCode=409;response.end(JSON.stringify({detail:'模拟保存失败，原数据保留'}));return;}
        assert.equal(command.expectedRevision,state.revision);
        if(command.action==='save-rules') {
          assert.equal(request.headers['x-gate-rule-schema'],'flight-role-v1');
          state.balanceSettings=command.payload.balanceSettings;
          state.teamLeaderSettings=command.payload.teamLeaderSettings;state.halfRestSettings=command.payload.halfRestSettings;
          state.configuration=command.payload.configuration;
          state.handoffPreferences=command.payload.preferences;
        }
        if(command.action==='adjust-priority-count'){
          const p=command.payload;
          let item=state.balanceSettings.priorityAdjustments.find(item=>item.month===p.month&&item.memberId===p.memberId&&item.flightNo===p.flightNo&&item.roleName===p.roleName);
          if(!item){item={month:p.month,memberId:p.memberId,flightNo:p.flightNo,roleName:p.roleName,delta:0};state.balanceSettings.priorityAdjustments.push(item);}item.delta+=p.delta;
        }
        state.revision++;response.end(JSON.stringify(snapshot()));
      });return;
    }
    if(request.url?.startsWith('/api/priority-statistics?')){
      const month=new URL(request.url,'http://test').searchParams.get('month'),mode=queryMode;
      const reply=()=>{response.setHeader('Content-Type','application/json');if(mode==='error'){response.statusCode=503;response.end('{}');}else response.end(JSON.stringify({...stats(month),...(mode==='revision'?{revision:state.revision+1}:{})}));};
      if(mode==='held')held.push(reply);else reply();return;
    }
    response.setHeader('Content-Type','text/html');response.end('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/app.css"><div id="app"></div><script src="/app.js"></script>');
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const base=`http://127.0.0.1:${server.address().port}`;
  let browser,ws,sessionId,id=0;const pending=new Map();
  const until=async(fn,label)=>{const end=Date.now()+10000;while(Date.now()<end){const result=await fn();if(result)return result;await delay(25);}throw Error(`等待超时：${label}`);};
  const call=(method,params={})=>new Promise((resolve,reject)=>{const key=++id,timer=setTimeout(()=>{pending.delete(key);reject(Error(`Chrome超时${method}`));},10000);pending.set(key,{resolve:value=>{clearTimeout(timer);resolve(value);},reject});ws.send(JSON.stringify({id:key,method,params,...(sessionId?{sessionId}:{})}));});
  const evaluate=async expression=>{const result=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(result.exceptionDetails)throw Error(JSON.stringify(result.exceptionDetails));return result.result.value;};
  const click=selector=>evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);
  const change=(selector,value,checked=false)=>evaluate(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});el.${checked?'checked':'value'}=${JSON.stringify(value)};el.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  const screenshot=async name=>{
    if(!process.env.GATE_UI_SCREENSHOTS)return;
    const destination=join(temporaryRoot,'balance-ui-verification');await mkdir(destination,{recursive:true});
    await writeFile(join(destination,name),Buffer.from((await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:true})).data,'base64'));
  };
  t.after(async()=>{for(const release of held.splice(0))release();ws?.close();if(browser&&browser.exitCode===null){const done=new Promise(resolve=>browser.once('exit',resolve));browser.kill();await Promise.race([done,delay(3000)]);}server.closeAllConnections();await new Promise(resolve=>server.close(resolve));assert.equal(dirname(directory),temporaryRoot);await rm(directory,{recursive:true,force:true,maxRetries:8,retryDelay:250});});
  const profile=join(directory,'chrome');browser=spawn(chrome,['--headless=new','--remote-debugging-port=0',`--user-data-dir=${profile}`,'--no-first-run','--disable-background-networking','about:blank'],{windowsHide:true,stdio:'ignore',env:{...process.env,TEMP:temporaryRoot,TMP:temporaryRoot}});
  const portFile=await until(async()=>{try{return await readFile(join(profile,'DevToolsActivePort'),'utf8');}catch{return false;}},'Chrome启动');const [port,path]=portFile.trim().split(/\r?\n/);ws=new WebSocket(`ws://127.0.0.1:${port}${path}`);await new Promise(resolve=>ws.addEventListener('open',resolve,{once:true}));
  ws.addEventListener('message',event=>{const message=JSON.parse(event.data),request=pending.get(message.id);if(request){pending.delete(message.id);message.error?request.reject(Error(JSON.stringify(message.error))):request.resolve(message.result);}});
  const target=await call('Target.createTarget',{url:'about:blank'});sessionId=(await call('Target.attachToTarget',{targetId:target.targetId,flatten:true})).sessionId;await call('Page.enable');await call('Page.navigate',{url:base});await until(()=>evaluate('!!document.querySelector("[data-view=rules]")'),'工作台启动');
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await click('[data-view=rules]');
  assert.equal(await evaluate('document.querySelectorAll("#save-rules").length'),1);
  assert.equal(await evaluate('document.querySelectorAll(".rule-group").length'),8);
  await evaluate('(()=>{const el=document.querySelector("#rules-search");el.value="稀缺";el.dispatchEvent(new Event("input",{bubbles:true}));})()');
  assert.equal(await evaluate('document.querySelector("#base-rule-settings").hidden'),false);
  assert.equal(await evaluate('document.querySelector("#base-rule-settings").open'),true);
  assert.match(await evaluate('document.querySelector("#base-rule-settings").textContent'),/甲岗2人、乙岗8人/);
  assert.equal(commands.length,0,'查看自动稀缺规则不写配置');
  await evaluate('(()=>{const el=document.querySelector("#rules-search");el.value="";el.dispatchEvent(new Event("input",{bubbles:true}));})()');
  await evaluate('document.querySelector("#priority-role-rules").open=true');
  await evaluate('(()=>{const el=document.querySelector("#rules-search");el.value="差距";el.dispatchEvent(new Event("input",{bubbles:true}));})()');
  assert.equal(await evaluate('document.querySelector("#flight-balance-rules").open'),true);
  assert.ok(await evaluate('document.querySelectorAll("mark.rule-match").length>0'));
  assert.equal(await evaluate('document.querySelector("#priority-role-rules").hidden'),true);
  await evaluate('(()=>{const el=document.querySelector("#rules-search");el.value="";el.dispatchEvent(new Event("input",{bubbles:true}));})()');
  assert.equal(await evaluate('document.querySelector("#priority-role-rules").open'),true);
  assert.equal(await evaluate('document.querySelector("#flight-balance-rules").open'),false);
  assert.equal(commands.length,0,'搜索折叠不写库');
  await change('#priority-enabled' ,true,true);await change('[data-priority-role="核验"]',true,true);await change('#flight-count-gap-enabled',true,true);await change('#flight-count-gap','1');
  await change('#priority-rule-flight','F1');await change('[data-priority-flight="F1"][data-priority-role="主验"]',true,true);
  await change('#priority-rule-flight','F0');assert.equal(await evaluate('document.querySelector("[data-priority-flight=F0][data-priority-role=核验]").checked'),true,'切换航班保留各自勾选');
  await change('[data-handoff-source=接机1] [data-handoff-target=主验]',true,true);assert.equal(commands.length,0,'接续勾选只改草稿');
  await change('#flight-count-gap','');await click('[data-view=statistics]');assert.equal(await evaluate('document.querySelector("#app-dialog").open'),true);await click('#leave-stay');
  assert.equal(await evaluate('document.querySelector("#flight-count-gap").value'),'','未完成的差距输入切页后仍为空，不能静默变为硬规则0');
  await click('#save-rules');await delay(40);assert.equal(commands.length,0,'空值保存被阻止，不向后端提交规则');assert.equal(state.revision,51);
  await change('#flight-count-gap','1');
  assert.equal(await evaluate('(()=>{const event=new Event("beforeunload",{cancelable:true});window.dispatchEvent(event);return event.defaultPrevented;})()'),true,'未保存时关闭提醒生效');
  rejectSettings=true;await click('#save-rules');await until(()=>evaluate('document.querySelector(".app-message")?.textContent.includes("模拟保存失败")'),'失败保留');
  assert.equal(await evaluate('document.querySelector("#priority-enabled").checked'),true);assert.equal(await evaluate('document.querySelector("#flight-count-gap").value'),'1');
  await click('[data-view=statistics]');await click('#leave-save');
  await until(()=>evaluate('document.querySelector("#app-dialog")?.textContent.includes("模拟保存失败")'),'保存并继续失败就近提示');
  assert.equal(await evaluate('document.querySelector("[data-view=rules]").getAttribute("aria-current")'),'page','保存失败不能离开');
  await click('#leave-stay');
  await click('[data-view=statistics]');await click('#leave-stay');assert.equal(await evaluate('document.querySelector("[data-priority-role=核验]").checked'),true,'未保存草稿切页保留');
  rejectSettings=false;await click('[data-view=statistics]');await click('#leave-save');await until(()=>state.revision===52,'规则保存');await until(()=>evaluate('!!document.querySelector("#priority-statistics-root")'),'保存后继续');await click('[data-view=rules]');
  assert.deepEqual(commands.at(-1).payload.balanceSettings,{...settings,priorityEnabled:true,priorityRoleNames:[],priorityFlightRoles:[{flightNo:'F0',roleName:'主验'},{flightNo:'F0',roleName:'核验'},{flightNo:'F1',roleName:'主验'}],flightCountGap:1},'规则保存保留原人工修正及跨航班勾选');
  await evaluate('document.querySelectorAll("#balance-rules details").forEach(detail=>detail.open=true)');await screenshot('rules-1440.png');
  await change('#base-transition-minutes','9');
  await change('#service-date','2026-08-05');
  assert.equal(await evaluate('document.querySelector("#app-dialog").open'),true);
  await click('#leave-stay');assert.equal(await evaluate('document.querySelector("#base-transition-minutes").value'),'9');
  await click('[data-view=statistics]');await click('#leave-discard');await click('[data-view=rules]');
  assert.equal(await evaluate('document.querySelector("#base-transition-minutes").value'),'0');
  assert.equal(state.revision,52,'放弃未写库');

  await click('[data-view=statistics]');await until(()=>evaluate('!!document.querySelector(".priority-count-detail")'),'重点次数展示');
  await change('#priority-statistics-month','');
  assert.equal(await evaluate('document.querySelector("#priority-statistics-month").value'),'2026-08','清空月份时恢复当前统计月，避免空月份下误改旧月');
  assert.equal(await evaluate('document.querySelectorAll(".nav-item[data-view]").length'),6);
  assert.match(await evaluate('document.querySelector("#priority-statistics-root").textContent'),/班表 3.*修正 \+2/);
  assert.equal(await evaluate('document.querySelector(".priority-statistics-toolbar").contains(document.querySelector("#priority-statistics-month"))'),true,'月份和筛选集中到值机式右侧工具栏');
  assert.equal(await evaluate('getComputedStyle(document.querySelector(".priority-count-detail summary")).borderBottomStyle'),'dotted','次数使用值机的深色虚线入口');
  assert.equal(await evaluate('getComputedStyle(document.querySelector(".priority-count-detail summary")).color'),'rgb(32, 36, 42)','次数不用原红色链接');
  assert.equal(await evaluate('document.querySelector("#priority-statistics-help").open'),false,'长计次说明默认收起');
  await change('#priority-statistics-flight','F1');assert.equal(await evaluate('document.querySelectorAll(".priority-statistics-table thead th").length'),3,'航班筛选仅显示本航班重点列');
  assert.equal(await evaluate('document.querySelector(".priority-statistics-table tbody tr [data-label=合计] strong").textContent'),'7','航班合计消费后端对应范围');
  assert.match(await evaluate('document.querySelector(".priority-statistics-table thead").textContent'),/F1 · 主验/);
  await change('#priority-statistics-flight','');await click('#priority-statistics-role [data-priority-role="核验"]');assert.equal(await evaluate('document.querySelectorAll(".priority-statistics-table thead th").length'),3,'岗位按钮筛选仅显示选中岗位列');
  assert.equal(await evaluate('document.querySelector("#priority-statistics-role [data-priority-role=核验]").getAttribute("aria-pressed")'),'true');
  assert.equal(await evaluate('document.querySelectorAll(".priority-statistics-table tbody tr").length'),1,'筛选只保留该岗有资质或次数的人员');
  assert.equal(await evaluate('document.querySelector(".priority-statistics-table tbody tr [data-label=合计] strong").textContent'),'0','岗位合计只消费后端对应范围');
  assert.equal(await evaluate(`document.querySelector(${JSON.stringify('[data-member-id=a][data-role-name=核验][data-priority-adjust="-1"]')}).disabled`),true,'零次禁止再减');
  await click('#priority-statistics-role [data-priority-role="主验"]');
  await click('.priority-count-detail summary');
  await click('[data-member-id=a][data-priority-adjust="1"]');await until(()=>state.revision===53,'次数累加');await until(()=>evaluate('document.querySelector(".priority-count-detail summary")?.textContent==="6"'),'次数更新');
  assert.equal(await evaluate('document.querySelector(".priority-count-detail").open'),true,'修正后保留展开状态');
  assert.deepEqual(commands.at(-1).payload,{month:'2026-08',memberId:'a',flightNo:'F0',roleName:'主验',delta:1});
  await click('[data-member-id=a][data-priority-adjust="-1"]');await until(()=>state.revision===54,'次数扣减');await until(()=>evaluate('document.querySelector(".priority-count-detail summary")?.textContent==="5"'),'扣减更新');
  assert.equal(state.balanceSettings.priorityAdjustments[0].delta,2,'正负修正累计保存在同一条记录');
  await screenshot('statistics-1440.png');
  queryMode='held';await change('#priority-statistics-month','2026-07');await until(()=>held.length===1,'挂起旧月份');
  queryMode='normal';await change('#priority-statistics-month','2026-06');await until(()=>evaluate('document.querySelector(".priority-count-detail")?.textContent.includes("2026-06-02")'),'新月份');held.shift()();await delay(80);assert.equal(await evaluate('document.querySelector("#priority-statistics-month").value'),'2026-06');assert.doesNotMatch(await evaluate('document.querySelector("#priority-statistics-root").textContent'),/2026-07-02/);
  queryMode='error';await change('#priority-statistics-month','2026-07');await until(()=>evaluate('document.querySelector("[data-priority-statistics-notice]")?.getAttribute("role")==="alert"'),'查询错误');assert.equal(await evaluate('document.querySelectorAll("[data-priority-adjust]").length'),0);
  queryMode='revision';await click('#retry-priority-statistics');await until(()=>evaluate('document.querySelector("[data-priority-statistics-notice]")?.textContent.includes("数据已改变")'),'拒绝旧版本');
  queryMode='normal';await click('#retry-priority-statistics');await until(()=>evaluate('!!document.querySelector(".priority-count-detail")'),'重试成功');
  await call('Emulation.setDeviceMetricsOverride',{width:390,height:850,deviceScaleFactor:1,mobile:false});assert.ok(await evaluate('document.documentElement.scrollWidth<=innerWidth+1'),'390宽页面不横向溢出');
  assert.equal(await evaluate('getComputedStyle(document.querySelector(".priority-statistics-table tbody tr")).display'),'grid','窄屏采用值机式人员卡片');
  await click('.priority-count-detail summary');
  assert.ok(await evaluate('document.documentElement.scrollWidth<=innerWidth+1'),'窄屏展开明细也不溢出');
  await screenshot('statistics-390.png');
  queryMode='held';await change('#priority-statistics-month','2026-06');await until(()=>held.length===1,'切页前请求');await click('[data-view=rules]');held.shift()();await delay(80);assert.equal(await evaluate('!!document.querySelector("#balance-rules")'),true,'已离开统计页不接收迟到回复');
  await evaluate('document.querySelectorAll("#balance-rules details").forEach(detail=>detail.open=true)');await screenshot('rules-390.png');
});
