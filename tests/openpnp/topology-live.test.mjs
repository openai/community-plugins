import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { spawn } from 'node:child_process';
import { createWriteStream } from 'node:fs';
import { mkdtemp, mkdir, readFile, writeFile, access } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import os from 'node:os';
import { setTimeout as delay } from 'node:timers/promises';
import { Client } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { StdioClientTransport } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/stdio.js';
import { readCompleteResponse } from '../../scripts/openpnp-read-response.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const connection = process.env.OPENPNP_TOPOLOGY_E2E_CONNECTION_FILE;
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
test('actual MCP creates a complete nozzle assembly, runs a mixed native job, adopts and recovers generations', { skip: !connection, timeout: 420000 }, async t => {
  const evidenceDir = process.env.OPENPNP_TOPOLOGY_E2E_EVIDENCE_DIR || await mkdtemp(path.join(os.tmpdir(), 'openpnp-portable-evidence-'));
  await mkdir(evidenceDir, { recursive: true });
  const work = await mkdtemp(path.join(evidenceDir, 'retained-'));
  const runtime = process.env.OPENPNP_TOPOLOGY_E2E_RUNTIME, java = process.env.OPENPNP_TOPOLOGY_E2E_JAVA;
  assert.ok(runtime && java, 'This test requires the declared verified native runtime and Java executable.');
  const evidence = { schema_version: 1, started_at: new Date().toISOString(), passed: false, simulation_only: true,
    hardware_qualified: false, independently_inspected: 0, operations: [], child_cleanup: [], work,
    packaged_server_sha256: hash(await readFile(path.join(root, 'plugins/openpnp/mcp/server.mjs'))) };
  const clients = [], children = [];
  function cli(label, args) {
    const log = createWriteStream(path.join(work, `${label}.log`), { flags: 'wx', mode: 0o600 });
    const child = spawn(process.execPath, [path.join(root, 'plugins/openpnp/scripts/openpnp.mjs'), ...args], { stdio: ['ignore', 'pipe', 'pipe'], shell: false });
    child.stdout.pipe(log, { end: false }); child.stderr.pipe(log, { end: false });
    const record = { label, pid: child.pid, child, closed: false };
    record.exit = new Promise(resolve => {
      child.once('error', error => { record.error = error.message; });
      child.once('close', (code, signal) => { record.closed = true; record.code = code; record.signal = signal; log.end(resolve); });
    });
    children.push(record); return record;
  }
  async function closeChild(child) {
    if (!child.closed) child.child.kill('SIGTERM');
    const timeout = Date.now() + 15000;
    while (!child.closed && Date.now() < timeout) await delay(50);
    let forced = false;
    if (!child.closed) { forced = true; child.child.kill('SIGKILL'); }
    await child.exit;
    evidence.child_cleanup.push({ label: child.label, code: child.code, signal: child.signal, forced });
    assert.equal(forced, false, 'Owned native child needed forced cleanup.');
  }
  t.after(async () => {
    const errors = [];
    for (const peer of clients.reverse()) {
      try { if (peer.session) await peer.call('release_control_session', { session_id: peer.session }); } catch (e) { errors.push(e.message); }
      try { await peer.client.close(); } catch (e) { errors.push(e.message); }
    }
    for (const child of children.reverse()) { try { await closeChild(child); } catch (e) { errors.push(e.message); } }
    evidence.cleanup_errors = errors; if (errors.length) evidence.passed = false;
    evidence.completed_at = new Date().toISOString();
    await writeFile(path.join(evidenceDir, 'mcp-native-topology.json'), JSON.stringify(evidence, null, 2) + '\n');
    assert.deepEqual(errors, []);
  });
  async function peer(label, connectionFile) {
    const mcpState = path.join(work, `${label}-mcp`); await mkdir(mcpState, { mode: 0o700 });
    const transport = new StdioClientTransport({ command: process.execPath, args: [path.join(root, 'plugins/openpnp/mcp/server.mjs'), '--stdio'], cwd: mcpState,
      env: { PATH: process.env.PATH || '', OPENPNP_STATE_DIR: mcpState, OPENPNP_CONNECTION_FILE: connectionFile }, stderr: 'pipe' });
    const client = new Client({ name: `openpnp-portable-${label}`, version: '1.0.0' });
    const result = { client, session: undefined };
    result.call = async (name, args = {}) => {
      const response = await client.callTool({ name: `openpnp_${name}`, arguments: args });
      assert.notEqual(response.isError, true, JSON.stringify(response.structuredContent));
      try { return await readCompleteResponse(response.structuredContent, page => result.call('read_response_page', page)); }
      catch (error) {
        (evidence.response_failures ||= []).push({ peer: label, method: name, code: error.code, message: error.message,
          known_response: error.known_response, native_action_repeated: false });
        throw error;
      }
    };
    result.operation = async (name, args = {}) => {
      const request = randomUUID(); let value = await result.call(name, { session_id: result.session, request_id: request, ...args });
      const id = value.operation_id, deadline = Date.now() + 120000;
      while (['accepted', 'running'].includes(value.state)) {
        assert.ok(Date.now() < deadline, 'Native operation timed out; preserve the original request.');
        await delay(100); value = await result.call('get_operation', { operation_id: id, view: 'progress' });
      }
      value = await result.call('get_operation', { operation_id: id });
      assert.equal(value.state, 'succeeded', JSON.stringify(value));
      while ((await result.call('get_status', { view: 'progress' })).native_busy) { assert.ok(Date.now() < deadline); await delay(25); }
      evidence.operations.push({ peer: label, method: name, request_id: request, operation_id: id, state: value.state }); return value;
    };
    clients.push(result); await client.connect(transport); return result;
  }
  const source=await peer('source',connection);source.session=(await source.call('request_control_session',{request_id:randomUUID(),ttl_seconds:600})).session_id;
  const caps=await source.call('get_capabilities');assert.ok(caps.bridge.configuration_changes.includes('create_simulator_nozzle_assembly'));assert.equal(caps.bridge.hardware_qualified,false);
  const initial=await source.call('get_configuration'), original=initial.settings.nozzles[0];assert.ok(original.x_axis_id&&original.y_axis_id);assert.equal(initial.enabled,false);
  const change={type:'create_simulator_nozzle_assembly',head_id:original.head_id,driver_id:initial.drivers[0].id,x_axis_id:original.x_axis_id,y_axis_id:original.y_axis_id,
    nozzle_name:'Topology61 nozzle',tip_name:'Topology61 tip',valve_name:'Topology61 valve',head_offsets:{x_mm:20,y_mm:0,z_mm:0},
    z_axis:{home_mm:0,low_mm:-100,high_mm:10,safe_z_mm:0,feedrate_mm_per_s:100,acceleration_mm_per_s2:500,jerk_mm_per_s3:1000},
    rotation_axis:{home_deg:0,low_deg:-360,high_deg:360,feedrate_deg_per_s:500,acceleration_deg_per_s2:1000,jerk_deg_per_s3:10000},
    tip:{min_part_diameter_mm:0,max_part_diameter_mm:20,max_part_height_mm:10,max_pick_tolerance_mm:0.5,pick_dwell_ms:0,place_dwell_ms:0},pick_dwell_ms:0,place_dwell_ms:0,
    exclusive_package_ids:['R0805'],simulated_initial_tool_state:'installed-on-new-nozzle'};
  for(const invalid of [{...change,gcode:'G0 X10'},{...change,simulated_initial_tool_state:'physically-installed'},{...change,rotation_axis:{...change.rotation_axis,high_deg:'360'}}]){
    const response=await source.client.callTool({name:'openpnp_plan_configuration',arguments:{session_id:source.session,expected_config_revision:initial.config_revision,changes:[invalid]}});assert.equal(response.isError,true);
  }
  const plan=await source.call('plan_configuration',{session_id:source.session,expected_config_revision:initial.config_revision,changes:[change]});
  assert.equal((await source.call('get_configuration')).settings.nozzles.length,1);
  const request=randomUUID();let admission=await source.call('apply_configuration',{session_id:source.session,request_id:request,plan_id:plan.plan_id});
  const duplicate=await source.call('apply_configuration',{session_id:source.session,request_id:request,plan_id:plan.plan_id});assert.equal(duplicate.operation_id,admission.operation_id);
  while(['accepted','running'].includes(admission.state)){await delay(50);admission=await source.call('get_operation',{operation_id:admission.operation_id});}
  assert.equal(admission.state,'succeeded',JSON.stringify(admission));evidence.creation=admission;
  while((await source.call('get_status')).native_busy)await delay(25);
  const ids=admission.result.created_assembly,recovery=admission.result.recovery_preimage;assert.ok(ids.nozzle_id&&ids.nozzle_tip_id&&ids.z_axis_id&&ids.rotation_axis_id&&ids.vacuum_actuator_id);assert.notEqual(ids.nozzle_id,original.nozzle_id);
  const configured=await source.call('get_configuration');assert.equal(configured.settings.nozzles.length,2);assert.equal(configured.settings.nozzles[0].nozzle_id,original.nozzle_id);assert.equal(configured.enabled,false);assert.equal(configured.homed,false);
  const created=configured.settings.nozzles.find(n=>n.nozzle_id===ids.nozzle_id);assert.equal(created.installed_nozzle_tip_id,ids.nozzle_tip_id);assert.equal(created.vacuum_actuator_id,ids.vacuum_actuator_id);assert.equal(created.z_axis_id,ids.z_axis_id);assert.equal(created.rotation_axis_id,ids.rotation_axis_id);
  assert.deepEqual(configured.settings.packages.find(p=>p.package_id==='R0805').nozzle_tip_ids,[ids.nozzle_tip_id]);
  const recoveryArtifact=await source.call('get_native_artifact',{artifact_id:recovery.artifact_id});const recoveryBytes=Buffer.from(recoveryArtifact.base64,'base64');assert.equal(hash(recoveryBytes),recovery.sha256);const recoveryBundle=path.join(work,'recovery.zip');await writeFile(recoveryBundle,recoveryBytes,{flag:'wx',mode:0o600});
  async function rawProof(label,stateFile,operation,expected){
    const raw=await readFile(path.join(path.dirname(stateFile),'journal/operations.jsonl'));const records=raw.toString().trim().split('\n').map(JSON.parse);
    const complete=records.filter(r=>r.type==='native_placement_checkpoint'&&r.payload.operation_id===operation.operation_id&&r.payload.state==='native-placement-complete-hook');
    assert.equal(complete.length,expected);const byNozzle={};for(const row of complete){const context=row.payload.context;byNozzle[context.nozzle_id]=(byNozzle[context.nozzle_id]||0)+1;if(context.nozzle_id===ids.nozzle_id)assert.equal(context.nozzle_tip_id,ids.nozzle_tip_id);}
    assert.ok(byNozzle[original.nozzle_id]>0&&byNozzle[ids.nozzle_id]>0,'Both old and new native nozzles must complete placements');
    const actions=records.filter(r=>r.type==='native_action_outcome'&&r.payload.operation_id===operation.operation_id&&r.payload.state==='native_hook_returned');
    for(const nozzle of [original.nozzle_id,ids.nozzle_id])for(const kind of ['pick','release'])assert.ok(actions.some(r=>r.payload.kind===kind&&r.payload.context?.nozzle_id===nozzle));
    return {label,journal:path.join(path.dirname(stateFile),'journal/operations.jsonl'),journal_sha256:hash(raw),native_complete_placements:complete.length,by_nozzle:byNozzle,checkpoints:complete.map(r=>({sequence:r.sequence,payload:r.payload})),action_outcomes:actions.map(r=>({sequence:r.sequence,payload:r.payload}))};
  }
  await source.operation('set_machine_enabled',{enabled:true});await source.operation('home_machine');
  const prepared=(await source.operation('prepare_job',{sample:'pnp-test'})).result;assert.equal(prepared.requested,32);assert.equal((await source.operation('validate_job')).result.valid,true);
  const completed=await source.operation('start_job',{job_id:prepared.job_id});assert.equal(completed.result.placed,32);await source.operation('set_machine_enabled',{enabled:false});
  evidence.source_job={operation:completed,proof:await rawProof('source',connection,completed,32)};
  const exported=(await source.operation('export_portable_configuration',{expected_config_revision:(await source.call('get_configuration')).config_revision})).result.artifact;
  const artifact=await source.call('get_native_artifact',{artifact_id:exported.artifact_id});const bytes=Buffer.from(artifact.base64,'base64');assert.equal(hash(bytes),exported.sha256);const bundle=path.join(work,'configured.zip');await writeFile(bundle,bytes,{flag:'wx',mode:0o600});
  async function adopt(label,bundle,digest){
    const adoption=path.join(work,`${label}-adoption`),state=path.join(work,`${label}-state`);
    const command=cli(`${label}-adopt`,['adopt-configuration','--state-dir',path.dirname(connection),'--openpnp-home',runtime,'--java',java,'--bundle',bundle,'--sha256',digest,'--destination',adoption]);await command.exit;assert.equal(command.code,0,await readFile(path.join(work,`${label}-adopt.log`),'utf8'));
    const launch=cli(`${label}-launch`,['start-adopted-simulator','--state-dir',state,'--adoption-dir',adoption,'--openpnp-home',runtime,'--java',java]);const file=path.join(state,'connection.json');const deadline=Date.now()+75000;
    while(true){try{await access(file);break;}catch(e){if(e.code!=='ENOENT')throw e;}if(launch.closed)assert.fail(await readFile(path.join(work,`${label}-launch.log`),'utf8'));assert.ok(Date.now()<deadline);await delay(100);}
    const target=await peer(label,file);const status=await target.call('get_status'),native=await target.call('get_configuration');assert.equal(status.job_state,'absent');assert.equal(native.enabled,false);assert.equal(native.homed,false);assert.deepEqual((await target.call('get_board_loads')).loads,[]);
    const nextCaps=await target.call('get_capabilities');assert.notEqual(nextCaps.bridge.machine_id,caps.bridge.machine_id);assert.notEqual(nextCaps.bridge.bridge_instance_id,caps.bridge.bridge_instance_id);
    const stale=await target.call('get_request_status',{request_id:request});assert.equal(stale.found,false);target.session=(await target.call('request_control_session',{request_id:randomUUID(),ttl_seconds:600})).session_id;
    return {target,file,native,adoption};
  }
  const adopted=await adopt('created',bundle,exported.sha256);assert.deepEqual(adopted.native.settings.nozzles,(await source.call('get_configuration')).settings.nozzles);
  await adopted.target.operation('set_machine_enabled',{enabled:true});await adopted.target.operation('home_machine');
  const parts=['R0805-1K','R0603-1K'].map(id=>adopted.native.parts.find(p=>p.id===id));const csv='Ref,Val,Package,PosX,PosY,Rot,Side,PartId,Height\n'+parts.map((p,i)=>`R${i+1},1K,${p.package_id},${15+5*i},15,0,top,${p.id},${p.height_mm}`).join('\n')+'\n';
  const imported=await adopted.target.call('import_job',{format:'reference-csv',content:csv,units:'mm',widthMm:40,heightMm:30});const nextJob=(await adopted.target.operation('prepare_job',{artifact_id:imported.artifact_id})).result;assert.equal((await adopted.target.operation('validate_job')).result.valid,true);const nextDone=await adopted.target.operation('start_job',{job_id:nextJob.job_id});assert.equal(nextDone.result.placed,2);await adopted.target.operation('set_machine_enabled',{enabled:false});
  evidence.adopted_job={operation:nextDone,proof:await rawProof('adopted',adopted.file,nextDone,2)};
  const recovered=await adopt('recovered',recoveryBundle,recovery.sha256);assert.equal(recovered.native.settings.nozzles.length,1);assert.equal(recovered.native.settings.nozzles[0].nozzle_id,original.nozzle_id);assert.deepEqual(recovered.native.settings.packages.find(p=>p.package_id==='R0805').nozzle_tip_ids,initial.settings.packages.find(p=>p.package_id==='R0805').nozzle_tip_ids);
  evidence.recovery={artifact:recovery,adoption:recovered.adoption,nozzles:recovered.native.settings.nozzles,execution_authority_transferred:false};evidence.native_placements=34;evidence.passed=true;
});
