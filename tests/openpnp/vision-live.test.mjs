import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID, createHash } from 'node:crypto';
import { mkdtemp, mkdir, cp, readFile, writeFile, rm } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { Client } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { StdioClientTransport } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/stdio.js';
import { readCompleteResponse } from '../../scripts/openpnp-read-response.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const connection = process.env.OPENPNP_VISION_E2E_CONNECTION_FILE;
test('packaged MCP configures native inherited vision and executes its actual simulated alignment', { skip: !connection, timeout: 180000 }, async t => {
  const state = await mkdtemp(path.join(os.tmpdir(), 'openpnp-vision-mcp-'));
  t.after(() => rm(state, { recursive: true, force: true }));
  await cp(path.join(root, 'plugins/openpnp/mcp'), path.join(state, 'mcp'), { recursive: true });
  const transport = new StdioClientTransport({ command: process.execPath, args: [path.join(state, 'mcp/server.mjs'), '--stdio'], cwd: state,
    env: { PATH: process.env.PATH || '', OPENPNP_STATE_DIR: state, OPENPNP_CONNECTION_FILE: connection }, stderr: 'pipe' });
  const client = new Client({ name: 'openpnp-vision-native-e2e', version: '1.0.0' });
  let session;
  t.after(async () => { if (session) { try { await client.callTool({ name: 'openpnp_release_control_session', arguments: { session_id: session } }); } catch {} } await client.close(); });
  await client.connect(transport);
  async function call(name, args = {}) {
    const result = await client.callTool({ name: `openpnp_${name}`, arguments: args });
    assert.notEqual(result.isError, true, JSON.stringify(result.structuredContent));
    return readCompleteResponse(result.structuredContent, page => call('read_response_page', page));
  }
  const caps = await call('get_capabilities');
  assert.equal(caps.bridge.simulation, true); assert.equal(caps.bridge.hardware_qualified, false);
  assert.ok(caps.bridge.configuration_changes.includes('set_vision_parameter'));
  assert.equal(caps.bridge.native_action_ledger.available, true);
  assert.equal((await call('get_status')).job_state, 'absent', 'This test requires a fresh native simulator.');
  session = (await call('request_control_session', { request_id: randomUUID(), ttl_seconds: 180 })).session_id;
  const evidence = { started_at: new Date().toISOString(), bridge: caps.bridge, hardware_qualified: false, operations: [],
    server_sha256: createHash('sha256').update(await readFile(path.join(state, 'mcp/server.mjs'))).digest('hex') };
  async function operation(name, args = {}) {
    const requestId = randomUUID(); let result = await call(name, { session_id: session, request_id: requestId, ...args });
    const handle = result.operation_id, deadline = Date.now() + 120000;
    while (['accepted', 'running'].includes(result.state)) {
      assert.ok(Date.now() < deadline, 'Native operation timed out; do not replay it.');
      await delay(75); result = await call('get_operation', { operation_id: handle });
    }
    assert.equal(result.state, 'succeeded', JSON.stringify(result));
    while ((await call('get_status')).native_busy) { assert.ok(Date.now() < deadline); await delay(25); }
    evidence.operations.push({ tool: name, request_id: requestId, operation_id: handle, state: result.state, result: result.result });
    return result;
  }
  const initial = await call('get_configuration');
  const source = initial.settings.vision_settings.profiles.find(p => p.vision_settings_id === 'BVS_Stock');
  assert.ok(source?.stock); assert.equal(initial.settings.vision_settings.bottom_alignment_enabled, true);
  const part = initial.parts.find(p => p.id === 'R0805-1K'); assert.ok(part?.package_id && part.height_mm > 0);
  const backup = (await operation('backup_configuration')).result;
  const id = `BVS_Codex_${randomUUID().replaceAll('-', '')}`;
  const configPlan = await call('plan_configuration', { session_id: session, expected_config_revision: initial.config_revision, changes: [
    { type: 'clone_vision_settings', source_vision_settings_id: source.vision_settings_id, vision_settings_id: id, name: 'Native MCP vision qualification fixture' },
    { type: 'set_vision_parameter', vision_settings_id: id, parameter_name: 'pThreshold', value: 128 },
    { type: 'assign_vision_settings', holder: `part:${part.id}`, kind: 'bottom', vision_settings_id: id },
  ] });
  assert.ok(JSON.stringify(configPlan.plan.effects).includes(`part:${part.id}`));
  await operation('apply_configuration', { plan_id: configPlan.plan_id });
  async function checkAssignment() {
    const current = await call('get_configuration'), vision = current.settings.vision_settings;
    const configured = vision.profiles.find(p => p.vision_settings_id === id);
    assert.equal(configured.pipeline_parameters.find(p => p.parameter_name === 'pThreshold').assigned_value, 128);
    assert.equal(vision.holders.find(h => h.holder === `part:${part.id}`).effective_bottom.vision_settings_id, id);
    return current;
  }
  await checkAssignment();
  const restored = (await operation('restore_configuration', { artifact_id: backup.artifact_id })).result;
  assert.equal(restored.full_configuration_restore, false);
  assert.ok(restored.snapshot_omissions.some(o => o.code === 'NOT_IN_VERSION_ONE_RESTORE'));
  await checkAssignment();
  await operation('set_machine_enabled', { enabled: true }); await operation('home_machine');
  const imported = await call('import_job', { format: 'reference-csv', units: 'mm', widthMm: 40, heightMm: 30,
    content: `Ref,Val,Package,PosX,PosY,Rot,Side,PartId,Height\nR1,1K,${part.package_id},15,15,0,top,${part.id},${part.height_mm}\n` });
  const prepared = (await operation('prepare_job', { artifact_id: imported.artifact_id })).result;
  assert.equal((await operation('validate_job')).result.valid, true);
  const completed = await operation('start_job', { job_id: prepared.job_id });
  assert.equal(completed.result.placed, 1);
  assert.equal(completed.native_action_ledger.outcomes['align:native_hook_returned'], 1, 'Actual native alignment hooks must run for the configured part.');
  assert.equal(completed.native_action_ledger.independently_verified, 0);
  evidence.native_alignment = completed.native_action_ledger;
  await checkAssignment(); await operation('set_machine_enabled', { enabled: false });
  await call('release_control_session', { session_id: session }); session = undefined;
  evidence.completed_at = new Date().toISOString(); evidence.passed = true;
  evidence.scope = 'One actual simulated native placement with inherited stock-derived vision and pThreshold128. Synthetic camera source; no independent placement inspection or physical qualification.';
  if (process.env.OPENPNP_VISION_E2E_EVIDENCE_DIR) {
    await mkdir(process.env.OPENPNP_VISION_E2E_EVIDENCE_DIR, { recursive: true });
    await writeFile(path.join(process.env.OPENPNP_VISION_E2E_EVIDENCE_DIR, 'mcp-native-vision.json'), JSON.stringify(evidence,
      (key, value) => key === 'configuration_root' ? '<isolated-native-simulator-configuration>' : value, 2) + '\n');
  }
});
