/** Anonymous capability check; never signs in or reads workspace contents. */
import {loadEnv} from '../web/node_modules/vite/dist/node/index.js';
import {build} from '../web/node_modules/esbuild/lib/main.js';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../',import.meta.url));
try{
  const source=await build({entryPoints:[root+'web/src/cloud-config.ts'],bundle:true,write:false,format:'esm',platform:'node'});
  const {cloudConfiguration}=await import('data:text/javascript;base64,'+Buffer.from(source.outputFiles[0].text).toString('base64'));
  const config=cloudConfiguration({...loadEnv('cloud',root+'web','VITE_'),...Object.fromEntries(Object.entries(process.env).filter(([k])=>k.startsWith('VITE_')))});
  if(!config)throw Error('请先在 web/.env.local 配置登机口云端公开连接。');
  for(const [name,body] of [
    ['gate_acquire_session',{p_session:'gate-launch-schema-check'}],
    ['gate_read_workspace_session',{p_session:'gate-launch-schema-check'}],
    ['gate_save_workspace_session',{p_session:'gate-launch-schema-check',expected_revision:0,new_documents:{}}],
  ]){
    const response=await fetch(config.url+'/rest/v1/rpc/'+name,{method:'POST',headers:{apikey:config.key,'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(15000)});
    const value=await response.json();
    if(value.code==='PGRST202'){console.log('云端尚未安装登录审批更新，准备启动登机口数据库升级工具。');process.exitCode=2;break}
    if(![401,403].includes(response.status)||value.code!=='42501')throw Error('云端权限或接口检查未通过，已停止启动。');
  }
}catch(error){
  // URL/network stacks may include connection settings. Print only our own
  // configuration validation message or a generic connection failure.
  console.error(error instanceof TypeError?'无法连接登机口云端，请检查网络后重试。':error.message);
  process.exitCode=1;
}
