// SPDX-License-Identifier: Apache-2.0
// Fresh owned controller simulator launcher; qualification is recorded per package artifact.
import { spawn } from 'node:child_process';
import { mkdir, realpath, lstat, writeFile, open } from 'node:fs/promises';
import { randomBytes } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { verifiedRuntime, installBridge, atomicJson } from './openpnp.mjs';
import { javaEnvironment } from './java-environment.mjs';
import { CONTROLLER_PROFILE, requireControllerProfile } from './controller-profile.mjs';
const UPSTREAM = '5bd404cfc70f34103a3ca0fbb6b50c2b465f407c';
const pluginRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const err = (code, message) => Object.assign(new Error(message), { code });
const separate = (a,b) => { if (a===b || a.startsWith(b+path.sep) || b.startsWith(a+path.sep)) throw err('OVERLAPPING_STATE', 'State, package and runtime must occupy separate trees.'); };

export async function prepareControllerSimulator({ stateDir, openpnpHome, java = 'java', sourceRoot = pluginRoot }) {
  if (!['darwin','linux'].includes(process.platform)) throw err('UNSUPPORTED_PLATFORM','The diagnostic launcher requires Unix private files.');
  if (!path.isAbsolute(stateDir || '') || !path.isAbsolute(openpnpHome || '') || typeof java !== 'string' || !java || java.includes('\0')) throw err('INVALID_ARGUMENT','Use a new absolute state directory, verified absolute runtime and Java executable.');
  const fresh = path.join(await realpath(path.dirname(stateDir)), path.basename(stateDir));
  const runtimeRoot = await realpath(openpnpHome), packageRoot = await realpath(sourceRoot);
  separate(fresh,runtimeRoot); separate(fresh,packageRoot); separate(packageRoot,runtimeRoot);
  try { await lstat(fresh); throw err('EEXIST','State already exists; preserve its journal, credentials and configuration.'); } catch(e) { if(e.code!=='ENOENT') throw e; }
  await verifiedRuntime({ openpnpHome:runtimeRoot,sourceRoot:packageRoot });
  // Reserve once. Any later failure preserves this owned state; no automatic retry or deletion.
  await mkdir(fresh,{mode:0o700});
  await installBridge(fresh,packageRoot);
  const runtime=await verifiedRuntime({stateDir:fresh,openpnpHome:runtimeRoot});
  const tokenFile=path.join(fresh,'bridge.token'), token=randomBytes(32).toString('base64url');
  const handle=await open(tokenFile,'wx',0o600);try { await handle.writeFile(token+'\n');await handle.sync(); } finally {await handle.close();}
  const args=['-Xmx2g','-XX:+ExitOnOutOfMemoryError','-Dfile.encoding=UTF-8','-Djava.awt.headless=true',
    '-Djava.util.prefs.PreferencesFactory=org.openpnp.codex.IsolatedPreferencesFactory',
    '--add-opens=java.base/java.lang=ALL-UNNAMED','--add-opens=java.desktop/java.awt=ALL-UNNAMED','--add-opens=java.desktop/java.awt.color=ALL-UNNAMED',
    '-cp',[runtime.receipt.bridge_jar,runtime.jar,...runtime.libraries].join(path.delimiter),'org.openpnp.codex.NativeControllerSimulator',
    '--token-file',tokenFile,'--journal-dir',path.join(fresh,'journal'),'--config-dir',path.join(fresh,'controller-config'),'--sample-root',runtime.samples,'--port','0'];
  return {java,args,stateDir:fresh,tokenFile,token,bridgeSha256:runtime.receipt.bridge_sha256};
}
async function observeCapabilities(url, token, fetchImpl) {
  const response=await fetchImpl(new URL('/rpc',url),{method:'POST',redirect:'error',signal:AbortSignal.timeout(10000),headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json'},body:JSON.stringify({method:'openpnp_get_capabilities',params:{}})});
  if(!response.ok) throw err('BRIDGE_UNAVAILABLE','Readiness capability request failed.');
  const reader=response.body.getReader();const chunks=[];let count=0;
  try { for (;;) { const {done,value}=await reader.read();if(done)break;count+=value.byteLength;if(count>65536)throw err('INVALID_CAPABILITIES','Readiness response exceeds 64 KiB.');chunks.push(value); } } finally { await reader.cancel().catch(()=>{}); }
  const data=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(Buffer.concat(chunks)));
  const c=data.result;if(!c || c.schema_version!==1 || c.bridge_version!=='0.1.0' || c.upstream_commit!==UPSTREAM)throw err('INCOMPATIBLE_BRIDGE','Bridge version or native pin differs.');return c;
}

// Dependencies may be replaced by installer-fixture tests only; CLI supplies no overrides.
export async function launchControllerSimulator(options, {spawnImpl=spawn,fetchImpl=fetch,output=process.stdout,diagnostics=process.stderr}={}) {
  const prepared=await prepareControllerSimulator(options);
  const child=spawnImpl(prepared.java,prepared.args,{env:javaEnvironment().environment,stdio:['ignore','pipe','pipe'],shell:false});
  let terminationRequested=false,stopped=false,exited=false,readyStarted=false,ready=false,pending='',bytes=0,error,publicationStarted=false,killTimer,readiness=Promise.resolve();
  const terminate=()=>{if(exited)return;terminationRequested=child.kill('SIGTERM') || terminationRequested;killTimer ||=setTimeout(()=>{if(!exited)child.kill('SIGKILL');},5000);};
  const fail=e=>{error ||=e;terminate();};
  const startupTimer=setTimeout(()=>fail(err('STARTUP_TIMEOUT','Native diagnostic did not become ready within 60 seconds.')),60000);
  const stop=()=>{stopped=true;terminate();};process.once('SIGINT',stop);process.once('SIGTERM',stop);
  const observeLine=line=>{
    if(error || stopped)return;
    const m=/^OPENPNP_CODEX_READY port=([0-9]+) upstream=([a-f0-9]+) mode=([^ ]+)$/.exec(line.trim());
    if(!m){diagnostics.write(line+'\n');return;}
    if(readyStarted || m[2]!==UPSTREAM || m[3]!==CONTROLLER_PROFILE || +m[1]<1 || +m[1]>65535){fail(err('INVALID_READINESS','Diagnostic readiness marker is duplicated or mismatched.'));return;}
    readyStarted=true;
    readiness=(async()=>{
      const url=`http://127.0.0.1:${m[1]}/`,c=await observeCapabilities(url,prepared.token,fetchImpl);
      requireControllerProfile(c,{expectedBridgeSha256:prepared.bridgeSha256,fresh:true});
      if(error)throw error;
      if(stopped)throw err('STARTUP_CANCELLED','Diagnostic readiness was cancelled.');
      if(exited)throw err('EXIT_BEFORE_READINESS','Child exited before readiness was verified.');
      const connectionFile=path.join(prepared.stateDir,'connection.json');publicationStarted=true;
      await atomicJson(connectionFile,{schemaVersion:1,url,tokenFile:prepared.tokenFile,machineId:c.machine_id});
      if(error)throw error;
      if(stopped)throw err('STARTUP_CANCELLED','Connection publication was interrupted; inspect preserved state.');
      if(exited)throw err('EXIT_DURING_PUBLICATION','Child exited while its connection was being published.');
      ready=true;clearTimeout(startupTimer);
      output.write(JSON.stringify({connected:true,connection_file:connectionFile,simulator_pid:child.pid,simulator_profile:CONTROLLER_PROFILE,bridge_instance_id:c.bridge_instance_id,controller_instance_id:c.controller_diagnostic.controller_instance_id,hardware_qualified:false,diagnostic_run_performed:false,lifecycle:'foreground; Ctrl-C requests shutdown; no physical stop claim'})+'\n');
    })().catch(fail);
  };
  child.stdout.setEncoding('utf8');child.stdout.on('data',chunk=>{bytes+=Buffer.byteLength(chunk);if(bytes>1048576){fail(err('OUTPUT_LIMIT','Diagnostic startup output exceeded its bound.'));return;}pending+=chunk;if(pending.length>16384&&!pending.includes('\n')){fail(err('OUTPUT_LIMIT','Diagnostic output line exceeded its bound.'));return;}let n;while((n=pending.indexOf('\n'))>=0){const line=pending.slice(0,n);pending=pending.slice(n+1);if(Buffer.byteLength(line)>16384){fail(err('OUTPUT_LIMIT','Diagnostic output line exceeded its bound.'));return;}observeLine(line);}});
  child.stderr.on('data',chunk=>{bytes+=chunk.length;if(bytes>1048576)fail(err('OUTPUT_LIMIT','Diagnostic output exceeded its bound.'));else diagnostics.write(chunk);});
  try {
    const exit=await new Promise((resolve,reject)=>{child.once('error',reject);child.once('exit',(code,signal)=>{exited=true;resolve({code,signal});});});
    await readiness;if(error)throw error;
    if(!ready)throw err('EXIT_BEFORE_READINESS','Diagnostic exited before verified readiness.');
    if(exit.code!==0 && !(stopped && terminationRequested && (exit.code===143 || exit.signal==='SIGTERM')))throw err('DIAGNOSTIC_EXIT','Diagnostic process exited unexpectedly; retain its original request/journal.');
  } catch(e) {terminate();throw Object.assign(e,{state_preserved:true,...(publicationStarted?{connection_publication:'verify-existing-receipt; possibly-published'}:{})});}
  finally {clearTimeout(startupTimer);clearTimeout(killTimer);process.removeListener('SIGINT',stop);process.removeListener('SIGTERM',stop);}
}
