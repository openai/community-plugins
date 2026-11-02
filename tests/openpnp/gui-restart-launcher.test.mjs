// SPDX-License-Identifier: Apache-2.0
// Executable/HTTP/serialized-file fixtures only. No native motion, source or GUI qualification.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdtemp, mkdir, writeFile, readFile, readdir, chmod, symlink, rm, access, stat, realpath } from 'node:fs/promises';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { atomicJson, installBridge, parseArguments } from '../../plugins/openpnp/scripts/openpnp.mjs';
import { prepareRestartGuiSimulator, launchRestartGuiSimulator, validateSavedSimulatorXml } from '../../plugins/openpnp/scripts/gui-restart-launcher.mjs';
const upstream = '5bd404cfc70f34103a3ca0fbb6b50c2b465f407c';
const patch = 'a1de28f8907ef703280615621c87434c1853fd622696f73f38b2fd8477815079';
const sha = data => createHash('sha256').update(data).digest('hex');
const cli = fileURLToPath(new URL('../../plugins/openpnp/scripts/openpnp.mjs', import.meta.url));
const absent = file => assert.rejects(access(file), { code: 'ENOENT' });
const property = (args, key) => args.find(value => value.startsWith(`-D${key}=`))?.slice(key.length + 3);
const model = '<openpnp-machine><machine class="org.openpnp.machine.reference.ReferenceMachine"><drivers><driver class="org.openpnp.machine.reference.driver.NullDriver"/></drivers><motion-planner class="org.openpnp.machine.reference.driver.NullMotionPlanner"/><home-after-enabled>false</home-after-enabled></machine></openpnp-machine>';
async function fixture(t) {
  const root = await realpath(await mkdtemp(path.join(os.tmpdir(), 'openpnp-restart-launcher-'))), releases = []; t.after(async () => { for (const release of releases) await release(); await rm(root, { recursive: true, force: true }); });
  const stateDir = path.join(root, 'state'), sourceRoot = path.join(root, 'plugin'), openpnpHome = path.join(root, 'runtime'), sessionDir = path.join(root, 'original GUI session');
  const config = path.join(sessionDir, 'config'), nativeState = path.join(sessionDir, 'bridge-state');
  for (const dir of [path.join(sourceRoot, 'bridge'), openpnpHome, sessionDir, config, path.join(config, 'scripts'), nativeState, path.join(nativeState, 'journal')]) await mkdir(dir, { recursive: true, mode: 0o700 });
  const bridge = Buffer.from('restart installer test bridge, not Java'), bootstrap = await readFile(new URL('../../plugins/openpnp/bridge/bootstrap.js', import.meta.url));
  await writeFile(path.join(sourceRoot, 'bridge/openpnp-codex-bridge.jar'), bridge); await writeFile(path.join(sourceRoot, 'bridge/bootstrap.js'), bootstrap);
  const entries = [['openpnp-gui.jar','fake retained-graph native JAR'],['gui-preferences.jar','fake isolated preferences'],['lib/a.jar','fake library'],['samples/pnp-test/board.xml','fake sample']];
  for (const [name,data] of entries) { await mkdir(path.dirname(path.join(openpnpHome,name)),{recursive:true});await writeFile(path.join(openpnpHome,name),data); }
  const manifest = { upstream_commit: upstream, gui_jar:'openpnp-gui.jar',gui_launcher_jar:'gui-preferences.jar',libs_directory:'lib',samples_directory:'samples',gui_ownership:{api_version:1,patch_id:'codex-gui-ownership-v1',patch_sha256:patch},native_action_observer:{api_version:1,patch_sha256:'a'.repeat(64)},files:entries.map(([name,data])=>({path:name,sha256:sha(data)})) };
  await atomicJson(path.join(openpnpHome,'codex-build-manifest.json'),manifest);
  const build = { upstream_commit:upstream,bridge_version:'0.1.0',bridge_sha256:sha(bridge),bootstrap_sha256:sha(bootstrap),patched_native_jar_sha256:sha(entries[0][1]),runtime_manifest_sha256:sha(await readFile(path.join(openpnpHome,'codex-build-manifest.json'))),gui_sensing_restart:{schema_version:1,profile:'native-gui-source-absent-restart-v1',startup_mode:'restart',prepared_manifest_profile:'prepared-native-gui-vacuum-fixture-v1',source_installed_at_startup:false} };
  await atomicJson(path.join(sourceRoot,'bridge/build-manifest.json'),build);await installBridge(stateDir,sourceRoot);
  const bootstrapPath = path.join(config,'scripts/codex-bootstrap.js'); await writeFile(bootstrapPath,bootstrap,{mode:0o600});
  await writeFile(path.join(config,'machine.xml'),model,{mode:0o644});
  for(const kind of ['packages','parts','boards','panels','vision-settings','script-state'])await writeFile(path.join(config,`${kind}.xml`),`<openpnp-${kind}/>`,{mode:0o644});
  const machineId = randomUUID(), token = 'A'.repeat(43), journal = Buffer.from('{"fixture_only":true,"operation":"old-unknown","outcome":"outcome_unknown"}\n');
  await writeFile(path.join(nativeState,'bridge.token'),token,{mode:0o600});await writeFile(path.join(nativeState,'journal/machine-id'),machineId+'\n',{mode:0o600});await writeFile(path.join(nativeState,'journal/operations.jsonl'),journal,{mode:0o600});
  const prepared = {schema_version:1,profile:'prepared-native-gui-vacuum-fixture-v1',upstream_commit:upstream,config_directory:config,scenario:'lost-before-place',files:[{path:'machine.xml',bytes:model.length,sha256:sha(model)}],source_authority_created:false,simulation_only:true,hardware_qualified:false};
  const preparedPath = path.join(sessionDir,'sensing-fixture.json');await atomicJson(preparedPath,prepared);
  const original = {gui_session_id:'original',session_root:sessionDir,config_directory:config,native_state_directory:nativeState,bootstrap_script:bootstrapPath,profile:'vacuum-sensing',sensing_preparation:{profile:'vacuum-sensing',scenario:prepared.scenario,manifest:preparedPath,sha256:sha(await readFile(preparedPath))},bridge_sha256:build.bridge_sha256,runtime_manifest_sha256:build.runtime_manifest_sha256,bootstrap_sha256:build.bootstrap_sha256,bridge_on_application_classpath:false,physical_qualification:false};
  await atomicJson(path.join(sessionDir,'launcher.json'),original);
  const marker=path.join(root,'executed.json');
  async function executable(body='process.exit(0);') {const file=path.join(root,'fake-java');await writeFile(file,`#!${process.execPath}\nconst fs=require('node:fs'),path=require('node:path'),args=process.argv.slice(2);fs.writeFileSync(${JSON.stringify(marker)},JSON.stringify({args,env:{JAVA_TOOL_OPTIONS:process.env.JAVA_TOOL_OPTIONS,CLASSPATH:process.env.CLASSPATH}}));if(args.includes('org.openpnp.codex.GuiSensingFixture'))throw Error('Restart invoked source preparer');\n${body}\n`);await chmod(file,0o700);return file;}
  const f={releases,root,stateDir,sourceRoot,openpnpHome,sessionDir,config,nativeState,bootstrapPath,preparedPath,prepared,original,manifest,build,marker,machineId,token,journal,executable};
  f.repin=async()=>{await atomicJson(path.join(openpnpHome,'codex-build-manifest.json'),manifest);build.runtime_manifest_sha256=sha(await readFile(path.join(openpnpHome,'codex-build-manifest.json')));await atomicJson(path.join(stateDir,'bridge/0.1.0/build-manifest.json'),build);};
  f.originalBytes=async()=>Object.fromEntries(await Promise.all(['launcher.json','sensing-fixture.json','config/machine.xml','config/scripts/codex-bootstrap.js','bridge-state/bridge.token','bridge-state/journal/machine-id','bridge-state/journal/operations.jsonl'].map(async name=>[name,sha(await readFile(path.join(sessionDir,name)))])));
  return f;
}
async function prepared(t,f){const result=await prepareRestartGuiSimulator(f);f.releases.push(()=>result.release());return result;}
async function rpc(t,handler){const server=http.createServer(handler);await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});t.after(async()=>{server.closeAllConnections();await new Promise(resolve=>server.close(resolve));});return server.address().port;}
function capability(f){return {schema_version:1,bridge_version:'0.1.0',upstream_commit:upstream,simulator_profile:'gui-simulator',simulation:true,hardware_qualified:false,bridge_artifact_sha256:f.build.bridge_sha256,machine_id:f.machineId,gui_ownership:{local_grant:false,provenance:{sensing_fixture_attested:false,sensing_restart_attested:true,sensing_fixture:{profile:'native-gui-source-absent-restart-v1',source_authority_created:false,execution_authority_restored:false,simulation_only:true,hardware_qualified:false}}},vacuum_sensing:{available:false},sensing_reconciliation:{restart_request_available:true}};}
const ready=port=>`process.stdout.write('OPENPNP_CODEX_GUI_READY port=${port} mode=gui-simulator\\n');setTimeout(()=>process.exit(0),600);`;

test('typed restart CLI accepts only original session and verified runtime inputs',()=>{
 assert.equal(parseArguments(['restart-gui-simulator','--session-dir','/original','--openpnp-home','/runtime']).command,'restart-gui-simulator');
 for(const key of ['--config-dir','--journal','--manifest','--sensing-scenario','--auto-grant','--profile','--java-options','--port'])assert.throws(()=>parseArguments(['restart-gui-simulator',key,'value']),/Unknown/);
 assert.throws(()=>parseArguments(['restart-gui-simulator','--session-dir','/one','--session-dir','/two']),/unique/);
});

test('restart preserves original paths/history and accepts native saved changes under independent current inventory',async t=>{
 const f=await fixture(t);await writeFile(path.join(f.config,'machine.xml'),model.replace('ReferenceMachine"','ReferenceMachine" speed="0.5"'));
 await mkdir(path.join(f.config,'backups'));await writeFile(path.join(f.config,'backups/machine.xml'),model);
 const before=await f.originalBytes(),p=await prepared(t,f);
 assert.equal(property(p.args,'configDir'),f.config);assert.equal(property(p.args,'openpnp.codex.stateDir'),f.nativeState);assert.equal(property(p.args,'openpnp.codex.sensingStartup'),'restart');assert.equal(property(p.args,'openpnp.codex.sensingManifest'),f.preparedPath);
 assert.equal(p.args.at(-1),'org.openpnp.Main');assert.ok(!p.args.includes('org.openpnp.codex.GuiSensingFixture'));assert.ok(!p.args[p.args.indexOf('-cp')+1].includes('openpnp-codex-bridge.jar'));
 assert.ok(property(p.args,'user.home').startsWith(path.join(f.sessionDir,'restart-attempts')));assert.notEqual(p.session_root,f.sessionDir);assert.equal(p.sensing_restart.source_installed_at_startup,false);assert.equal(p.sensing_restart.execution_authority_restored,false);
 assert.equal(p.sensing_restart.journal_prefix.sha256,sha(f.journal));assert.equal(p.sensing_restart.journal_prefix.history_validated,false);assert.notEqual(p.sensing_restart.current_files.find(x=>x.path==='machine.xml').sha256,f.prepared.files[0].sha256);
 await p.beforeLaunch();assert.deepEqual(await f.originalBytes(),before);await absent(f.marker);await absent(path.join(f.stateDir,'connection.json'));assert.equal((await stat(p.session_root)).mode&0o777,0o700);
 await assert.rejects(prepareRestartGuiSimulator(f),/reservation/);await p.release();await absent(path.join(f.sessionDir,'restart-reservation'));
});

test('closed XML preflight accepts recorded native serialized simulator files',async()=>{
 const root=new URL('./fixtures/gui-restart-saved-config/',import.meta.url),p=JSON.parse(await readFile(new URL('provenance.json',root)));
 for(const [name,record]of Object.entries(p.files)){const data=await readFile(new URL(name,root));assert.equal(sha(data),record.sha256);validateSavedSimulatorXml(data,name==='machine.xml');}
});

test('closed XML refuses hardware, script stages, entities, noncanonical class encodings, malformed tags and effectful camera paths',()=>{
 for(const bad of [model.replace('NullDriver','GcodeDriver'),model.replace('NullMotionPlanner','AdvancedMotionPlanner'),model.replace('<drivers>','<drivers><driver class="org.openpnp.machine.reference.driver.NullDriver"/>'),model.replace('ReferenceMachine"','ReferenceMachine" resolves-to="java.lang.Runtime"'),model.replace('NullDriver','Null&#68;river'),'<!DOCTYPE x [<!ENTITY ext SYSTEM "file:///etc/passwd">]>'+model,model.replace('</drivers>','</bad>'),model.replace('<drivers>','<drivers class="java.lang.Runtime">'),model.replace('<drivers>','<drivers><cv-stage class="org.openpnp.vision.pipeline.stages.ScriptRun"/>'),model.replace('<drivers>','<drivers><source-uri>https://example.com/camera.png</source-uri>'),model.replace('<drivers>','<drivers><source-uri>file:///camera.png</source-uri>'),model.replace('<home-after-enabled>false','<home-after-enabled>true'),model.replace('<drivers>','<drivers><?run dangerous?>'),model.replace('<drivers>','<drivers><script>dangerous</script>'),model.replace('ReferenceMachine"','ReferenceMachine" class="org.openpnp.machine.reference.ReferenceMachine"')])assert.throws(()=>validateSavedSimulatorXml(Buffer.from(bad),true));
});

test('unsupported artifacts refuse before executable or restart attempt creation and preserve scope',async t=>{
 for(const kind of ['missing-capability','sourceful-capability','old-native-patch','manifest-tamper','moved-scope','unsafe-profile','private-token','empty-journal','missing-identity','hardware','active-script','symlink-config','writable-config'])await t.test(kind,async child=>{
 const f=await fixture(child);f.java=await f.executable();
 if(kind==='missing-capability'){delete f.build.gui_sensing_restart;await f.repin();}
 if(kind==='sourceful-capability'){f.build.gui_sensing_restart.source_installed_at_startup=true;await f.repin();}
 if(kind==='old-native-patch'){f.manifest.gui_ownership.patch_sha256='b'.repeat(64);await f.repin();}
 if(kind==='manifest-tamper')await writeFile(f.preparedPath,'{}');
 if(kind==='moved-scope'){f.original.config_directory='/other';await atomicJson(path.join(f.sessionDir,'launcher.json'),f.original);}
 if(kind==='unsafe-profile'){f.original.profile='gui-simulator';await atomicJson(path.join(f.sessionDir,'launcher.json'),f.original);}
 if(kind==='private-token')await chmod(path.join(f.nativeState,'bridge.token'),0o644);
 if(kind==='empty-journal')await writeFile(path.join(f.nativeState,'journal/operations.jsonl'),'');
 if(kind==='missing-identity')await rm(path.join(f.nativeState,'journal/machine-id'));
 if(kind==='hardware')await writeFile(path.join(f.config,'machine.xml'),model.replace('NullDriver','GcodeDriver'));
 if(kind==='active-script')await writeFile(path.join(f.config,'scripts/auto.js'),'process.exit(99)');
 if(kind==='symlink-config')await symlink(f.bootstrapPath,path.join(f.config,'alias'));
 if(kind==='writable-config')await chmod(path.join(f.config,'machine.xml'),0o666);
 await assert.rejects(launchRestartGuiSimulator(f));await absent(f.marker);await absent(path.join(f.sessionDir,'restart-attempts'));await absent(path.join(f.sessionDir,'restart-reservation'));
 });
});

test('legacy live native process scope is refused even without PID receipt',async t=>{
 const f=await fixture(t);const child=spawn(process.execPath,['-e','setInterval(()=>{},1000)','--',`-DconfigDir=${f.config}`],{stdio:'ignore'});await once(child,'spawn');t.after(()=>{child.kill('SIGTERM');});
 await assert.rejects(prepareRestartGuiSimulator(f),/Another process/);await absent(path.join(f.sessionDir,'restart-attempts'));child.kill('SIGTERM');await once(child,'exit');
});

test('fresh exclusive reservation rejects concurrent restart and preserves unknown stale owner evidence',async t=>{
 const f=await fixture(t);await mkdir(path.join(f.sessionDir,'restart-reservation'),{mode:0o700});await atomicJson(path.join(f.sessionDir,'restart-reservation/owner.json'),{owner:'prior',launcher_pid:999999});
 await assert.rejects(prepareRestartGuiSimulator(f),/reservation/);assert.equal(JSON.parse(await readFile(path.join(f.sessionDir,'restart-reservation/owner.json'))).owner,'prior');
});

test('pre-spawn guard detects current config, history and runtime drift without launching Java',async t=>{
 for(const kind of ['machine','journal','runtime','token'])await t.test(kind,async child=>{const f=await fixture(child),p=await prepared(child,f);
 if(kind==='machine')await writeFile(path.join(f.config,'machine.xml'),model.replace('ReferenceMachine"','ReferenceMachine" speed="0.5"'));
 if(kind==='journal')await writeFile(path.join(f.nativeState,'journal/operations.jsonl'),'changed');
 if(kind==='runtime'){f.build.gui_sensing_restart.profile='other';await f.repin();}
 if(kind==='token')await writeFile(path.join(f.nativeState,'bridge.token'),'B'.repeat(43));
 await assert.rejects(p.beforeLaunch(),/changed/);await absent(f.marker);
 });
});

test('zero exit and spawn failure preserve original journal and release only owned reservation',async t=>{
 for(const kind of ['zero','spawn'])await t.test(kind,async child=>{const f=await fixture(child),before=await f.originalBytes();f.java=kind==='zero'?await f.executable():path.join(f.root,'no-java');
 if(kind==='zero'){const r=await launchRestartGuiSimulator(f);assert.equal(r.state,'gui-exited-without-attachment');}else await assert.rejects(launchRestartGuiSimulator(f),/ENOENT/);
 assert.deepEqual(await f.originalBytes(),before);await absent(path.join(f.sessionDir,'restart-reservation'));await absent(path.join(f.stateDir,'connection.json'));
 const attempts=await readdir(path.join(f.sessionDir,'restart-attempts'));assert.equal(attempts.length,1);assert.ok(JSON.parse(await readFile(path.join(f.sessionDir,'restart-attempts',attempts[0],'launcher-result.json'))).evidence_preserved);
 if(kind==='zero'){const r=JSON.parse(await readFile(path.join(f.sessionDir,'restart-attempts',attempts[0],'launcher-process.json')));assert.equal(r.config_directory,f.config);assert.ok(r.gui_pid>0);}
 });
});

test('authenticated source-absent attachment uses original machine/token and never issues a recovery/grant request',async t=>{
 const f=await fixture(t),before=await f.originalBytes(),requests=[];const port=await rpc(t,async(req,res)=>{const chunks=[];for await(const chunk of req)chunks.push(chunk);requests.push(JSON.parse(Buffer.concat(chunks)));assert.equal(req.headers.authorization,`Bearer ${f.token}`);res.setHeader('Content-Type','application/json');res.end(JSON.stringify({result:capability(f)}));});
 f.java=await f.executable(ready(port));const r=await launchRestartGuiSimulator(f);assert.equal(r.was_connected,true);assert.deepEqual(requests,[{method:'openpnp_get_capabilities',params:{}}]);assert.deepEqual(await f.originalBytes(),before);const connection=JSON.parse(await readFile(path.join(f.stateDir,'connection.json')));assert.equal(connection.machineId,f.machineId);assert.equal(connection.tokenFile,path.join(f.nativeState,'bridge.token'));assert.ok(!JSON.stringify(r).includes(f.token));await absent(path.join(f.sessionDir,'restart-reservation'));
});

test('wrong machine, restored source or absent restart provenance refuse connection and reap child',async t=>{
 for(const kind of ['machine','source','provenance','authority'])await t.test(kind,async child=>{const f=await fixture(child),before=await f.originalBytes(),cap=capability(f);
 if(kind==='machine')cap.machine_id=randomUUID();if(kind==='source')cap.vacuum_sensing.available=true;if(kind==='provenance')delete cap.gui_ownership.provenance.sensing_restart_attested;if(kind==='authority')cap.gui_ownership.provenance.sensing_fixture.execution_authority_restored=true;
 const port=await rpc(child,(req,res)=>res.end(JSON.stringify({result:cap})));f.java=await f.executable(ready(port));await assert.rejects(launchRestartGuiSimulator(f),/restart attachment/);await absent(path.join(f.stateDir,'connection.json'));await absent(path.join(f.sessionDir,'restart-reservation'));assert.deepEqual(await f.originalBytes(),before);
 });
});

test('CLI cancellation ends owned child, preserves original bytes, releases reservation and records process identity',async t=>{
 const f=await fixture(t),before=await f.originalBytes();f.java=await f.executable("process.stdout.write('FIXTURE_STARTED\\n');setInterval(()=>{},1000);");
 const child=spawn(process.execPath,[cli,'restart-gui-simulator','--session-dir',f.sessionDir,'--state-dir',f.stateDir,'--openpnp-home',f.openpnpHome,'--java',f.java],{stdio:['ignore','pipe','pipe']});let output='',error='';child.stderr.on('data',chunk=>{error+=chunk;});t.after(()=>child.kill('SIGTERM'));
 await new Promise((resolve,reject)=>{child.stdout.on('data',chunk=>{output+=chunk;if(output.includes('waiting-for-local-bootstrap'))resolve();});child.once('exit',code=>reject(new Error(`Early exit ${code}: ${error}`)));});
 child.kill('SIGTERM');const [code]=await once(child,'exit');assert.equal(code,0,error);assert.deepEqual(await f.originalBytes(),before);await absent(path.join(f.sessionDir,'restart-reservation'));
 const attempts=await readdir(path.join(f.sessionDir,'restart-attempts')),result=JSON.parse(await readFile(path.join(f.sessionDir,'restart-attempts',attempts[0],'launcher-result.json')));assert.equal(result.intentional_stop,true);assert.equal(result.forced_stop,false);const proc=JSON.parse(await readFile(path.join(f.sessionDir,'restart-attempts',attempts[0],'launcher-process.json')));assert.throws(()=>process.kill(proc.gui_pid,0),{code:'ESRCH'});
});

test('cancellation escalates only the owned unresponsive child and releases reservation after it exits',async t=>{
 const f=await fixture(t),before=await f.originalBytes();f.java=await f.executable("process.on('SIGTERM',()=>{});process.stderr.write('IGNORE_TERM_READY\\n');setInterval(()=>{},1000);");
 const child=spawn(process.execPath,[cli,'restart-gui-simulator','--session-dir',f.sessionDir,'--state-dir',f.stateDir,'--openpnp-home',f.openpnpHome,'--java',f.java],{stdio:['ignore','pipe','pipe']});let error='';child.stdout.resume();t.after(()=>child.kill('SIGTERM'));
 await new Promise((resolve,reject)=>{child.stderr.on('data',chunk=>{error+=chunk;if(error.includes('IGNORE_TERM_READY'))resolve();});child.once('exit',code=>reject(new Error(`Early exit ${code}: ${error}`)));});
 child.kill('SIGTERM');const [code]=await once(child,'exit');assert.equal(code,0,error);assert.deepEqual(await f.originalBytes(),before);await absent(path.join(f.sessionDir,'restart-reservation'));
 const attempts=await readdir(path.join(f.sessionDir,'restart-attempts')),dir=path.join(f.sessionDir,'restart-attempts',attempts[0]),result=JSON.parse(await readFile(path.join(dir,'launcher-result.json'))),proc=JSON.parse(await readFile(path.join(dir,'launcher-process.json')));
 assert.equal(result.intentional_stop,true);assert.equal(result.forced_stop,true);assert.equal(result.was_connected,false);assert.throws(()=>process.kill(proc.gui_pid,0),{code:'ESRCH'});
});

test('nonempty native board and panel libraries refuse before external XML can be loaded',async t=>{
 for(const kind of ['boards','panels'])await t.test(kind,async child=>{const f=await fixture(child),external=path.join(f.root,'outside-native.xml');await writeFile(external,'<board class="unverified.ExecutableConstructor"/>');
 await writeFile(path.join(f.config,`${kind}.xml`),`<openpnp-${kind}><${kind}><file>${external}</file></${kind}></openpnp-${kind}>`);
 let accepted;try{await assert.rejects(async()=>{accepted=await prepareRestartGuiSimulator(f);},/empty native.*librar/);}finally{await accepted?.release();}
 await absent(f.marker);assert.equal(await readFile(external,'utf8'),'<board class="unverified.ExecutableConstructor"/>');
 });
});

test('missing saved load input refuses automatic defaults and resave before Java startup',async t=>{
 for(const name of ['packages.xml','boards.xml','script-state.xml'])await t.test(name,async child=>{const f=await fixture(child);await rm(path.join(f.config,name));await assert.rejects(prepareRestartGuiSimulator(f),/seven native saved/);await absent(f.marker);await absent(path.join(f.sessionDir,'restart-attempts'));await absent(path.join(f.sessionDir,'restart-reservation'));});
});
