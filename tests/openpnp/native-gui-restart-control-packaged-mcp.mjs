// SPDX-License-Identifier: Apache-2.0
// Control lease companion for an independently owned actual native GUI fixture.
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { openPackagedRecovery } from './native-sensing-restart-packaged-mcp.mjs';
const config=JSON.parse(await readFile(process.argv[2],'utf8'));
let connection,result,failure,cleanup;
try {
  assert.equal(config.action,'control');connection=await openPackagedRecovery(config);
  const lease=await connection.call('openpnp_request_control_session',config.input);
  assert.equal(typeof lease.session_id,'string');assert.ok(lease.session_id.length>10);
  await writeFile(config.private_session_file,JSON.stringify(lease)+'\n',{flag:'wx',mode:0o600});
  result={lease_acquired:true,local_decision_submitted:false};
} catch(error){failure={name:error.name,message:error.message};}
finally{try{cleanup=await connection?.close();}catch(error){failure??={name:error.name,message:error.message};}}
const evidence={passed:!failure,action:'control',result,failure,cleanup,transcript:connection?.transcript,server_sha256:config.server_sha256,driver_sha256:createHash('sha256').update(await readFile(new URL(import.meta.url))).digest('hex'),scope:'Official SDK, actual packaged stdio server and native GUI Bridge HTTP lease; prior local GUI grant remains mandatory',observation_submission_over_mcp:false,physical_qualification:false};
await writeFile(config.output,JSON.stringify(evidence,(key,value)=>key==='session_id'?'<owned-session>':value,2)+'\n',{flag:'wx',mode:0o600});
console.log(JSON.stringify({passed:!failure,action:'control',server_pid:cleanup?.server_pid}));if(failure)process.exitCode=1;
