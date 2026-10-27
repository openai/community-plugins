// SPDX-License-Identifier: Apache-2.0
// Generic official-SDK client for one exact private packaged native test connection.
// Local GUI decisions and native assertions remain outside this process.
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
const config=JSON.parse(await readFile(process.argv[2],'utf8'));
const {openPackagedRecovery}=await import(pathToFileURL(config.recovery_driver).href);
let connection,result,failure,cleanup;const credentials=[];
try {
  try {const saved=JSON.parse(await readFile(config.private_session_file,'utf8'));if(typeof saved.session_id==='string')credentials.push(saved.session_id);}catch(error){if(error.code!=='ENOENT')throw error;}
  assert.equal(config.action,'tool');assert.ok(config.input.name.startsWith('openpnp_'));
  connection=await openPackagedRecovery(config);
  let value=await connection.call(config.input.name,config.input.arguments);
  if(config.input.name==='openpnp_request_control_session'){
    assert.equal(typeof value.session_id,'string');credentials.push(value.session_id);
    await writeFile(config.private_session_file,JSON.stringify(value)+'\n',{flag:'wx',mode:0o600});
  }
  const initialOperationId=value.operation_id;
  if(config.input.wait_for_terminal){
    assert.equal(typeof initialOperationId,'string');const until=Date.now()+25000;
    while(['accepted','running','paused'].includes(value.state)){
      assert.ok(Date.now()<until,'Bounded operation polling; no mutation retry');
      assert.notEqual(value.state,'paused','Unexpected pause requires independent local recovery');
      await delay(30);value=await connection.call('openpnp_get_operation',{operation_id:initialOperationId});
    }
    assert.equal(value.operation_id,initialOperationId);
    if(value.state==='succeeded'){
      assert.equal(value.native_completion.native_wrapper_completed,true);
      assert.equal(value.native_completion.native_wrapper_succeeded,true);
    }
  }
  result={tool_result:value,local_decision_submitted:false,mutation_repeated:false};
}catch(error){failure={name:error.name,message:error.message};}
finally{try{cleanup=await connection?.close();}catch(error){failure??={name:error.name,message:error.message};}}
const evidence={passed:!failure,action:'tool',tool:config.input.name,result,failure,cleanup,transcript:connection?.transcript,server_sha256:config.server_sha256,driver_sha256:createHash('sha256').update(await readFile(new URL(import.meta.url))).digest('hex'),scope:'Scripted official SDK / exact generated MCP bundle / native GUI Bridge HTTP workflow. Local decisions are separate actual GUI gestures, not evidence of fresh skill actor competence.',observation_submission_over_mcp:false,physical_qualification:false};
let encoded=JSON.stringify(evidence,(key,value)=>key==='session_id'?'<owned-session>':value,2)+'\n';
for(const credential of credentials)encoded=encoded.replaceAll(credential,'<owned-session>');
await writeFile(config.output,encoded,{flag:'wx',mode:0o600});
console.log(JSON.stringify({passed:!failure,tool:config.input.name,server_pid:cleanup?.server_pid}));if(failure)process.exitCode=1;
