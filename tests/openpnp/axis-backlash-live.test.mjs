import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID, createHash } from 'node:crypto';
import { chmod, cp, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { Client } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { StdioClientTransport } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/stdio.js';
import { readCompleteResponse } from '../../scripts/openpnp-read-response.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const connection = process.env.OPENPNP_BACKLASH_E2E_CONNECTION_FILE;
const sha = bytes => createHash('sha256').update(bytes).digest('hex');

test('packaged MCP applies native linear-axis backlash, executes simulated motion, and restores typed settings',
  { skip: !connection, timeout: 180000 }, async t => {
    const state = await mkdtemp(path.join(os.tmpdir(), 'openpnp-backlash-mcp-'));
    t.after(() => rm(state, { recursive: true, force: true }));
    const mcp = path.join(state, 'mcp');
    await cp(path.join(root, 'plugins/openpnp/mcp'), mcp, { recursive: true });
    await chmod(mcp, 0o700);
    const transport = new StdioClientTransport({ command: process.execPath, args: [path.join(mcp, 'server.mjs'), '--stdio'], cwd: state,
      env: { PATH: process.env.PATH || '', OPENPNP_STATE_DIR: state, OPENPNP_CONNECTION_FILE: connection }, stderr: 'pipe' });
    const client = new Client({ name: 'openpnp-backlash-native-e2e', version: '1.0.0' });
    let session;
    t.after(async () => {
      if (session) { try { await client.callTool({ name: 'openpnp_release_control_session', arguments: { session_id: session } }); } catch {} }
      await client.close();
    });
    await client.connect(transport);
    const evidence = { started_at: new Date().toISOString(), passed: false, operations: [], captures: [],
      hardware_qualified: false, independent_image_quality_verified: false, native_placements: 0,
      packaged_server_sha256: sha(await readFile(path.join(mcp, 'server.mjs'))) };
    const output = process.env.OPENPNP_BACKLASH_E2E_EVIDENCE_DIR;
    if (output) await mkdir(output, { recursive: true });
    t.after(async () => {
      if (output) await writeFile(path.join(output, 'mcp-native-axis-backlash.json'), JSON.stringify(evidence,
        (key, value) => key === 'configuration_root' ? '<isolated-native-simulator-configuration>' : value, 2) + '\n');
    });
    async function call(name, args = {}) {
      const response = await client.callTool({ name: `openpnp_${name}`, arguments: args });
      assert.notEqual(response.isError, true, JSON.stringify(response.structuredContent));
      return readCompleteResponse(response.structuredContent, page => call('read_response_page', page));
    }
    async function operation(name, args = {}, expectedState = 'succeeded') {
      const requestId = randomUUID();
      let result = await call(name, { session_id: session, request_id: requestId, ...args });
      const handle = result.operation_id, deadline = Date.now() + 45000;
      assert.equal(typeof handle, 'string');
      while (['accepted', 'running'].includes(result.state)) {
        assert.ok(Date.now() < deadline, 'Backlash operation timed out; it was not replayed.');
        await delay(50); result = await call('get_operation', { operation_id: handle });
      }
      evidence.operations.push({ tool: name, request_id: requestId, operation_id: handle, state: result.state, result: result.result });
      assert.equal(result.state, expectedState, JSON.stringify(result));
      while ((await call('get_status')).native_busy) { assert.ok(Date.now() < deadline); await delay(25); }
      return result.result;
    }
    async function apply(changes) {
      const before = await call('get_configuration');
      const plan = await call('plan_configuration', { session_id: session, expected_config_revision: before.config_revision, changes });
      await operation('apply_configuration', { plan_id: plan.plan_id });
      const after = await call('get_configuration');
      assert.notEqual(after.config_revision, before.config_revision);
      return after;
    }
    const caps=await call('get_capabilities');evidence.bridge=caps.bridge;
    assert.equal(caps.bridge.simulation,true);assert.equal(caps.bridge.hardware_qualified,false);
    assert.ok(caps.bridge.configuration_changes.includes('set_axis_backlash_settings'));
    const initial=await call('get_configuration');assert.equal(initial.enabled,false);
    const axis=initial.settings.axes.find(a=>a.axis_type==='X'&&a.backlash_settings_editable);
    assert.ok(axis);const fields=['method','offset_mm','speed_factor','sneak_up_mm','acceptable_tolerance_mm'];
    session=(await call('request_control_session',{request_id:randomUUID(),ttl_seconds:300})).session_id;
    const selected={type:'set_axis_backlash_settings',axis_id:axis.axis_id,method:'DirectionalSneakUp',offset_mm:0.4,speed_factor:0.25,sneak_up_mm:0.8,acceptable_tolerance_mm:0.025};
    const native=()=>call('get_configuration');let saved;
    for(const method of ['None','OneSidedPositioning','OneSidedOptimizedPositioning','DirectionalCompensation','DirectionalSneakUp']){
      const proposal={...selected,method};const configured=await apply([proposal]);
      const readback=configured.settings.axes.find(a=>a.axis_id===axis.axis_id);
      for(const key of fields)assert.equal(readback[key],proposal[key]);
      assert.equal(configured.homed,false);assert.equal(configured.enabled,false);
      assert.equal(readback.measurement_performed,false);assert.match(readback.soft_limits_bound,/may exceed/);
      if(method==='DirectionalSneakUp')saved=await operation('backup_configuration');
      await operation('set_machine_enabled',{enabled:true});await operation('home_machine');
      const motion=await call('plan_motion',{session_id:session,expected_config_revision:(await native()).config_revision,x:10,y:10,z:-5,units:'mm',speed:0.1});
      assert.ok(Array.isArray(motion.plan.backlash_compensation));
      if(method!=='None')assert.ok(motion.plan.backlash_compensation.some(a=>a.axis_id===axis.axis_id&&a.method===method));
      const complete=await operation('execute_motion',{plan_id:motion.plan_id});assert.equal(complete.completion.controller_barrier,'simulator-standstill');
      await operation('set_machine_enabled',{enabled:false});
    }
    await apply([{...selected,method:'None',offset_mm:0,sneak_up_mm:0}]);
    await operation('restore_configuration',{artifact_id:saved.artifact_id});
    const restored=await native();for(const key of fields)assert.equal(restored.settings.axes.find(a=>a.axis_id===axis.axis_id)[key],selected[key]);
    assert.equal(restored.homed,false);assert.equal(restored.enabled,false);
    // Real server schema rejects untyped/unknown native knobs before RPC application.
    for(const invalid of [{...selected,method:'Bogus'},{...selected,offset_mm:'1'},{...selected,gcode:'G0 X10'}]){
      const response=await client.callTool({name:'openpnp_plan_configuration',arguments:{session_id:session,expected_config_revision:restored.config_revision,changes:[invalid]}});
      assert.equal(response.isError,true);
    }
    evidence.final=restored.settings.axes.find(a=>a.axis_id===axis.axis_id);
    evidence.native_motion_operations=evidence.operations.filter(o=>o.tool==='execute_motion'&&o.state==='succeeded').length;
    assert.equal(evidence.native_motion_operations,5);evidence.passed=true;
  });
