// Actual packaged CLI -> pinned native OpenPnP -> official MCP client.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { randomUUID, createHash } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Client } from '../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { StdioClientTransport } from '../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/stdio.js';
import { readCompleteResponse } from '../scripts/openpnp-read-response.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const flags = {};
for (let i=2;i<process.argv.length;i+=2) {
  const k=process.argv[i],v=process.argv[i+1];
  if (!['--output','--runtime','--java'].includes(k) || !v || k in flags) throw new Error('Use unique --output, --runtime and --java path pairs.');
  flags[k]=v;
}
for (const k of ['--output','--runtime','--java']) if (!path.isAbsolute(flags[k] || '')) throw new Error(`${k} requires an absolute path.`);
const out=flags['--output'],runtime=flags['--runtime'],java=flags['--java'];
await mkdir(out);const state=path.join(out,'native-state');
const report={kind:'actual-packaged-controller-cli-mcp',started_at:new Date().toISOString(),node:process.version,passed:false,physical_qualification:false,checks:[]};
const sha=async p=>createHash('sha256').update(await readFile(p)).digest('hex');
report.bridge_sha256=await sha(path.join(root,'plugins/openpnp/bridge/openpnp-codex-bridge.jar'));
report.mcp_sha256=await sha(path.join(root,'plugins/openpnp/mcp/server.mjs'));
let launcher,client,lease,exit,stdout='',stderr='',ready,forced=false;
const env=Object.fromEntries(Object.entries(process.env).filter(([k])=>!k.startsWith('OPENPNP_')&&!k.startsWith('DYLD_')&&!k.startsWith('LD_')&&!['JAVA_TOOL_OPTIONS','JDK_JAVA_OPTIONS','_JAVA_OPTIONS','CLASSPATH'].includes(k)));
const check=(name,fn)=>{fn();report.checks.push(name);};
try {
  launcher=spawn(process.execPath,[path.join(root,'plugins/openpnp/scripts/openpnp.mjs'),'start-controller-simulator','--state-dir',state,'--openpnp-home',runtime,'--java',java],{env,detached:true,stdio:['ignore','pipe','pipe']});
  const closed=new Promise(resolve=>launcher.once('exit',(code,signal)=>{exit={code,signal};resolve(exit);}));
  launcher.once('error',e=>{report.spawn_error=e.message;});
  launcher.stdout.on('data',b=>{stdout+=b;if(stdout.length>1048576)launcher.kill('SIGTERM');});
  launcher.stderr.on('data',b=>{stderr=(stderr+b).slice(-1048576);});
  let deadline=Date.now()+30000;
  while(!ready && !exit && Date.now()<deadline){for(const line of stdout.split('\n')){try{const x=JSON.parse(line);if(x.connected)ready=x;}catch{}}if(!ready)await delay(20);}
  assert.ok(ready,`Launcher readiness failed ${JSON.stringify(exit)} ${stderr.slice(-3000)}`);report.launch=ready;
  check('fresh authenticated launcher publishes without dispatch',()=>{assert.equal(ready.diagnostic_run_performed,false);assert.equal(ready.hardware_qualified,false);});
  const transport=new StdioClientTransport({command:process.execPath,args:[path.join(root,'plugins/openpnp/mcp/server.mjs'),'--stdio'],cwd:out,env:{PATH:process.env.PATH||'',OPENPNP_STATE_DIR:path.join(out,'mcp-state'),OPENPNP_CONNECTION_FILE:ready.connection_file},stderr:'pipe'});
  client=new Client({name:'openpnp-controller-native-qualification',version:'1.0.0'});await client.connect(transport);
  async function raw(name,args={}){return client.callTool({name:'openpnp_'+name,arguments:args},undefined,{timeout:10000});}
  async function call(name,args={}){const r=await raw(name,args);assert.notEqual(r.isError,true,JSON.stringify(r.structuredContent));return readCompleteResponse(r.structuredContent,p=>call('read_response_page',p));}
  async function rejects(name,args,code){const r=await raw(name,args);assert.equal(r.isError,true);assert.equal(r.structuredContent?.error?.code,code,JSON.stringify(r));report.checks.push('rejects '+code);}
  const catalog=await client.listTools();assert.equal(catalog.tools.length,59);for(const name of ['openpnp_request_board_inspection','openpnp_get_board_inspection'])assert.ok(catalog.tools.some(tool=>tool.name===name));report.checks.push('packaged MCP discovery has 59 tools including local board inspection request and readback');
  const caps=await call('get_capabilities');report.capabilities=caps;
  check('exact pinned bridge/controller profile',()=>{assert.equal(caps.connected,true);assert.equal(caps.bridge.bridge_artifact_sha256,report.bridge_sha256);assert.equal(caps.bridge.controller_diagnostic.controller_instance_id,ready.controller_instance_id);assert.equal(caps.bridge.controller_diagnostic.generation_spent,false);assert.equal(caps.bridge.tools.length,11);});
  const conf=await call('get_configuration');
  lease=(await call('request_control_session',{request_id:randomUUID(),ttl_seconds:120})).session_id;
  const request={session_id:lease,request_id:randomUUID(),expected_config_revision:conf.config_revision,controller_instance_id:ready.controller_instance_id};
  await rejects('run_controller_diagnostic',{...request,host:'127.0.0.1'},'INVALID_ARGUMENT');
  await rejects('run_controller_diagnostic',{...request,controller_instance_id:randomUUID()},'CONTROLLER_INSTANCE_MISMATCH');
  await rejects('run_controller_diagnostic',{...request,expected_config_revision:'cfg-999'},'REVISION_CONFLICT');
  await rejects('home_machine',{session_id:lease,request_id:randomUUID()},'UNSUPPORTED_CAPABILITY');
  assert.equal((await call('get_status')).controller_diagnostic.generation_spent,false);
  const admitted=await call('run_controller_diagnostic',request);report.request={...request,session_id:'<redacted>'};report.admitted=admitted;
  let progress=admitted;deadline=Date.now()+15000;
  while(['accepted','running'].includes(progress.state)&&Date.now()<deadline){await delay(10);progress=await call('get_operation',{operation_id:admitted.operation_id,view:'progress'});}
  report.progress=progress;assert.equal(progress.state,'succeeded',JSON.stringify(progress));
  const operation=await call('get_operation',{operation_id:admitted.operation_id,view:'full'});report.operation=operation;
  check('native wrapper and durable result complete before success',()=>{assert.equal(operation.native_completion.native_wrapper_completed,true);assert.equal(operation.native_completion.native_wrapper_succeeded,true);assert.equal(operation.result.completed_recipe,true);assert.equal(operation.result.steps_attempted,4);});
  const protocol=operation.result.observation.native.protocol;
  check('exact three commands with closed native reader/channel',()=>{assert.equal(protocol.commands_attempted,3);assert.equal(protocol.wire_bytes_written,139);assert.equal(protocol.wire_bytes_read,132);assert.equal(protocol.reader_alive,false);assert.equal(protocol.channel_open,false);assert.equal(protocol.native_connected_flag,false);assert.equal(protocol.physical_standstill_verified,false);});
  report.request_status=await call('get_request_status',{request_id:request.request_id,view:'full'});
  check('original request remains discoverable',()=>{assert.equal(report.request_status.found,true);assert.equal(report.request_status.operation.operation_id,admitted.operation_id);});
  const replay=await call('run_controller_diagnostic',request);assert.equal(replay.operation_id,admitted.operation_id);
  await rejects('run_controller_diagnostic',{...request,request_id:randomUUID()},'CONTROLLER_GENERATION_SPENT');
  report.status=await call('get_status');
  check('spent generation and four ordered journaled steps',()=>{assert.equal(report.status.controller_diagnostic.generation_spent,true);assert.equal(report.status.native_busy,false);assert.deepEqual(report.status.controller_history.steps.map(x=>x.step),['bind','connect','identify','close']);});
  report.configuration=await call('get_configuration');
  check('configuration and status retain latest controller observation',()=>{assert.deepEqual(report.configuration.controller_diagnostic,report.status.controller_diagnostic);assert.deepEqual(report.status.machine.controller_diagnostic,report.status.controller_diagnostic);assert.equal(report.configuration.native_model_read_performed,false);assert.equal(report.configuration.snapshot_at,conf.snapshot_at);assert.equal(report.configuration.controller_diagnostic_source,'latest-cached-diagnostic-observation');});
  const xml=await readFile(path.join(state,'controller-config/machine.xml'),'utf8');assert.match(xml,/OwnedTaggedGcodeDriver/);assert.match(xml,/OwnedBoundedTcp/);
  report.configuration_sha256=await sha(path.join(state,'controller-config/machine.xml'));
  const journalFile=path.join(state,'journal/operations.jsonl');const journal=(await readFile(journalFile,'utf8')).trim().split('\n').map(JSON.parse);
  check('one admission and no replay in forced native journal',()=>{assert.equal(journal.filter(x=>x.type==='controller_diagnostic_admission').length,1);assert.equal(journal.filter(x=>x.type==='controller_diagnostic_step_intent').length,4);assert.equal(journal.filter(x=>x.type==='controller_diagnostic_step_outcome').length,4);});
  report.journal_sha256=await sha(journalFile);
  await call('release_control_session',{session_id:lease});lease=null;await client.close();client=null;
  launcher.kill('SIGTERM');await Promise.race([closed,delay(10000)]);assert.ok(exit,'Launcher child shutdown deadline');assert.equal(exit.code,0,stderr.slice(-3000));
  check('intentional foreground shutdown exits successfully',()=>{assert.equal(exit.signal,null);});
  report.passed=true;
} catch(e){report.failure={message:e.message,stack:e.stack};process.exitCode=1;}
finally {
  if(client)await client.close().catch(()=>{});
  if(launcher&&!exit){launcher.kill('SIGTERM');for(let i=0;i<100&&!exit;i++)await delay(50);if(!exit){forced=true;process.kill(-launcher.pid,'SIGKILL');for(let i=0;i<40&&!exit;i++)await delay(50);}}
  report.launcher_exit=exit;report.forced_cleanup=forced;report.finished_at=new Date().toISOString();
  await writeFile(path.join(out,'stdout.log'),stdout,{flag:'wx'});await writeFile(path.join(out,'stderr.log'),stderr,{flag:'wx'});
  await writeFile(path.join(out,'report.json'),JSON.stringify(report,null,2)+'\n',{flag:'wx'});
  console.log(JSON.stringify({passed:report.passed,checks:report.checks.length,failure:report.failure?.message,launcher_exit:exit,forced_cleanup:forced,report:out}));
}
