// Installer executables/JAR byte fixtures only: no Java, OpenPnP or controller protocol runs.
import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,chmod,rm,access,symlink} from 'node:fs/promises';
import path from 'node:path';import os from 'node:os';import {createHash} from 'node:crypto';import {spawn} from 'node:child_process';
import {parseArguments} from '../../plugins/openpnp/scripts/openpnp.mjs';
import {prepareControllerSimulator,launchControllerSimulator} from '../../plugins/openpnp/scripts/controller-launcher.mjs';
import {CONTROLLER_PROFILE,CONTROLLER_PROTOCOL,CONTROLLER_DRIVER,CONTROLLER_TOOLS} from '../../plugins/openpnp/scripts/controller-profile.mjs';
const pin='5bd404cfc70f34103a3ca0fbb6b50c2b465f407c',uuid='a134762f-2263-491d-a22b-c3cfb7f12145',hash=x=>createHash('sha256').update(x).digest('hex');
const absent=p=>assert.rejects(access(p),{code:'ENOENT'});
async function fixture(t){
 const root=await mkdtemp(path.join(os.tmpdir(),'owned-controller-installer-only-'));t.after(()=>rm(root,{recursive:true,force:true}));
 const sourceRoot=path.join(root,'plugin'),openpnpHome=path.join(root,'runtime'),stateDir=path.join(root,'new-state');await mkdir(path.join(sourceRoot,'bridge'),{recursive:true});
 const bridge=Buffer.from('NOT A NATIVE JAR: installer fixture');await writeFile(path.join(sourceRoot,'bridge/openpnp-codex-bridge.jar'),bridge);
 const entries=[['native.jar','NOT NATIVE'],['lib/a.jar','NOT A LIBRARY'],['samples/fixture.txt','fixture']];
 for(const [p,b]of entries){await mkdir(path.dirname(path.join(openpnpHome,p)),{recursive:true});await writeFile(path.join(openpnpHome,p),b);}
 const manifest=JSON.stringify({upstream_commit:pin,gui_jar:'native.jar',libs_directory:'lib',samples_directory:'samples',files:entries.map(([path,b])=>({path,sha256:hash(b)}))});await writeFile(path.join(openpnpHome,'codex-build-manifest.json'),manifest);
 await writeFile(path.join(sourceRoot,'bridge/build-manifest.json'),JSON.stringify({upstream_commit:pin,bridge_version:'0.1.0',bridge_sha256:hash(bridge),runtime_manifest_sha256:hash(manifest),patched_native_jar_sha256:hash('NOT NATIVE')}));
 const caps=()=>({schema_version:1,bridge_version:'0.1.0',upstream_commit:pin,bridge_artifact_sha256:hash(bridge),bridge_instance_id:uuid,machine_id:'installer-only-machine',simulation:true,simulator_profile:CONTROLLER_PROFILE,native_driver:CONTROLLER_DRIVER,controller_protocol:CONTROLLER_PROTOCOL,hardware_qualified:false,physical_standstill_verified:false,motion_completion_observed:false,configuration_changes:[],tools:[...CONTROLLER_TOOLS],fixed_recipe:['bind','connect:G21,G90','identify:M115','close'],fresh_launch_required:true,controller_diagnostic:{controller_instance_id:uuid,profile:CONTROLLER_PROFILE,generation_spent:false,physical_qualification:false}});
 const executable=async body=>{const file=path.join(root,'installer-only-executable');await writeFile(file,`#!${process.execPath}\n${body}\n`);await chmod(file,0o700);return file;};
 return {root,stateDir,sourceRoot,openpnpHome,caps,executable};
}
const streams=()=>({lines:[],write(x){this.lines.push(String(x));}});
const marker=`OPENPNP_CODEX_READY port=12345 upstream=${pin} mode=${CONTROLLER_PROFILE}\n`;
test('CLI permits only fixed fresh launch flags and rejects endpoint/config selectors',()=>{
 assert.equal(parseArguments(['start-controller-simulator','--state-dir','/new','--openpnp-home','/runtime']).command,'start-controller-simulator');
 for(const flag of ['--host','--port','--command','--profile','--adoption-dir','--config-dir'])assert.throws(()=>parseArguments(['start-controller-simulator',flag,'value']));
});
test('prepare verifies before reservation, refuses existing/nested state and keeps caller history',async t=>{
 const f=await fixture(t);await writeFile(path.join(f.openpnpHome,'native.jar'),'changed');await assert.rejects(prepareControllerSimulator(f),/integrity/);await absent(f.stateDir);
 const g=await fixture(t);await mkdir(g.stateDir);await writeFile(path.join(g.stateDir,'journal'),'original');await assert.rejects(prepareControllerSimulator(g),{code:'EEXIST'});assert.equal(await readFile(path.join(g.stateDir,'journal'),'utf8'),'original');
 await assert.rejects(prepareControllerSimulator({...g,stateDir:path.join(g.openpnpHome,'new')}),{code:'OVERLAPPING_STATE'});
 const alias=path.join(g.root,'alias');await symlink(g.openpnpHome,alias);await assert.rejects(prepareControllerSimulator({...g,stateDir:path.join(alias,'new')}),{code:'OVERLAPPING_STATE'});
});
test('prepared JVM uses fixed entrypoint/ephemeral bridge port/private preferences and no controller endpoint',async t=>{
 const f=await fixture(t),p=await prepareControllerSimulator(f);assert.equal(p.args.includes('org.openpnp.codex.NativeControllerSimulator'),true);assert.equal(p.args.at(-1),'0');assert.equal(p.args.includes('-Djava.util.prefs.PreferencesFactory=org.openpnp.codex.IsolatedPreferencesFactory'),true);assert.equal(p.args.some(a=>/--host|--serial|--command/.test(a)),false);assert.equal((await readFile(p.tokenFile,'utf8')).trim().length,43);await absent(path.join(f.stateDir,'connection.json'));
});
test('actual fixture child readiness publishes only after exact bounded capability binding; no diagnostic call',async t=>{
 const f=await fixture(t),envPath=path.join(f.root,'child-environment.json'),java=await f.executable(`require('node:fs').writeFileSync(${JSON.stringify(envPath)},JSON.stringify(process.env));process.stdout.write(${JSON.stringify(marker)});setTimeout(()=>process.exit(0),350);`),output=streams(),diagnostics=streams();let calls=0;let pid;
 const keys=['JAVA_TOOL_OPTIONS','jdk_java_options','_JAVA_OPTIONS','CLASSPATH','LD_PRELOAD','DYLD_INSERT_LIBRARIES','OPENPNP_FIXTURE_KEEP'];const prior=Object.fromEntries(keys.map(k=>[k,process.env[k]]));for(const k of keys)process.env[k]='installer-only-marker';t.after(()=>{for(const k of keys){if(prior[k]===undefined)delete process.env[k];else process.env[k]=prior[k];}});
 await launchControllerSimulator({...f,java},{output,diagnostics,spawnImpl:(...a)=>{assert.equal(a[2].shell,false);assert.equal(Object.keys(a[2].env).some(k=>/^(JAVA_TOOL_OPTIONS|JDK_JAVA_OPTIONS|_JAVA_OPTIONS|CLASSPATH|LD_|DYLD_)/i.test(k)),false);const c=spawn(...a);pid=c.pid;return c;},fetchImpl:async(url,options)=>{calls++;assert.equal(JSON.parse(options.body).method,'openpnp_get_capabilities');await absent(path.join(f.stateDir,'connection.json'));return new Response(JSON.stringify({result:f.caps()}));}});
 const childEnv=JSON.parse(await readFile(envPath,'utf8'));for(const k of keys.filter(k=>k!=='OPENPNP_FIXTURE_KEEP'))assert.equal(childEnv[k],undefined);assert.equal(childEnv.OPENPNP_FIXTURE_KEEP,'installer-only-marker');for(const k of keys)assert.equal(process.env[k],'installer-only-marker');
 assert.equal(calls,1);const receipt=JSON.parse(output.lines.join(''));assert.equal(receipt.connected,true);assert.equal(receipt.diagnostic_run_performed,false);assert.equal(receipt.controller_instance_id,uuid);assert.equal(receipt.simulator_pid,pid);assert.equal(JSON.parse(await readFile(path.join(f.stateDir,'connection.json'),'utf8')).machineId,'installer-only-machine');assert.throws(()=>process.kill(pid,0),{code:'ESRCH'});
});
test('wrong driver marker/capabilities terminate fixture and preserve state without connection',async t=>{
 const f=await fixture(t),java=await f.executable(`process.stdout.write(${JSON.stringify(marker)});setInterval(()=>{},1000);`);let pid;
 await assert.rejects(launchControllerSimulator({...f,java},{output:streams(),diagnostics:streams(),spawnImpl:(...a)=>{const c=spawn(...a);pid=c.pid;return c;},fetchImpl:async()=>new Response(JSON.stringify({result:{...f.caps(),native_driver:'org.openpnp.machine.reference.driver.NullDriver'}}))}),{code:'INCOMPATIBLE_BRIDGE',state_preserved:true});await absent(path.join(f.stateDir,'connection.json'));await access(path.join(f.stateDir,'bridge.token'));assert.throws(()=>process.kill(pid,0),{code:'ESRCH'});
});
test('spawn failure and clean early exit never claim connected or delete reserved state',async t=>{
 const f=await fixture(t);await assert.rejects(launchControllerSimulator({...f,java:path.join(f.root,'missing-java')},{output:streams(),diagnostics:streams()}),{code:'ENOENT',state_preserved:true});await absent(path.join(f.stateDir,'connection.json'));await access(path.join(f.stateDir,'installation.json'));
 const g=await fixture(t),java=await g.executable('process.exit(0);');await assert.rejects(launchControllerSimulator({...g,java},{output:streams(),diagnostics:streams()}),{code:'EXIT_BEFORE_READINESS'});await absent(path.join(g.stateDir,'connection.json'));
});
test('duplicate readiness and oversized capabilities do not publish connected',async t=>{
 const f=await fixture(t),java=await f.executable(`process.stdout.write(${JSON.stringify(marker+marker)});setInterval(()=>{},1000);`),output=streams();await assert.rejects(launchControllerSimulator({...f,java},{output,diagnostics:streams(),fetchImpl:async()=>{await new Promise(r=>setTimeout(r,50));return new Response(JSON.stringify({result:f.caps()}));}}),{code:'INVALID_READINESS'});assert.equal(output.lines.length,0);await absent(path.join(f.stateDir,'connection.json'));
 const g=await fixture(t),j=await g.executable(`process.stdout.write(${JSON.stringify(marker)});setInterval(()=>{},1000);`);await assert.rejects(launchControllerSimulator({...g,java:j},{output:streams(),diagnostics:streams(),fetchImpl:async()=>new Response('x'.repeat(65537))}),{code:'INVALID_CAPABILITIES'});await absent(path.join(g.stateDir,'connection.json'));
});
test('owned requested SIGTERM permits Java-style143 after readiness, while unrequested143 remains error',async t=>{
 const f=await fixture(t),java=await f.executable(`process.on('SIGTERM',()=>process.exit(143));process.stdout.write(${JSON.stringify(marker)});setInterval(()=>{},1000);`);let pid;
 await launchControllerSimulator({...f,java},{output:{write(){process.emit('SIGTERM');}},diagnostics:streams(),spawnImpl:(...a)=>{const c=spawn(...a);pid=c.pid;return c;},fetchImpl:async()=>new Response(JSON.stringify({result:f.caps()}))});assert.throws(()=>process.kill(pid,0),{code:'ESRCH'});
 const g=await fixture(t),j=await g.executable(`process.stdout.write(${JSON.stringify(marker)});setTimeout(()=>process.exit(143),350);`);await assert.rejects(launchControllerSimulator({...g,java:j},{output:streams(),diagnostics:streams(),fetchImpl:async()=>new Response(JSON.stringify({result:g.caps()}))}),{code:'DIAGNOSTIC_EXIT',state_preserved:true});
});
