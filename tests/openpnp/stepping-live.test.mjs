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
const connection = process.env.OPENPNP_STEP_E2E_CONNECTION_FILE;

test('packaged MCP single-steps the native processor and resumes the same paused job without duplicate feeds',
  { skip: !connection, timeout: 180000 }, async t => {
    const state = await mkdtemp(path.join(os.tmpdir(), 'openpnp-step-mcp-')); t.after(() => rm(state, { recursive: true, force: true }));
    const mcp = path.join(state, 'mcp'); await cp(path.join(root, 'plugins/openpnp/mcp'), mcp, { recursive: true }); await chmod(mcp, 0o700);
    const transport = new StdioClientTransport({ command: process.execPath, args: [path.join(mcp, 'server.mjs'), '--stdio'], cwd: state,
      env: { PATH: process.env.PATH || '', OPENPNP_STATE_DIR: state, OPENPNP_CONNECTION_FILE: connection }, stderr: 'pipe' });
    const client = new Client({ name: 'openpnp-step-native-e2e', version: '1.0.0' }); let session;
    t.after(async () => { if (session) { try { await client.callTool({ name: 'openpnp_release_control_session', arguments: { session_id: session } }); } catch {} } await client.close(); });
    await client.connect(transport);
    const evidence = { started_at: new Date().toISOString(), passed: false, operations: [], step_requests: [], native_placements: 0,
      hardware_qualified: false, independently_inspected: 0,
      packaged_server_sha256: createHash('sha256').update(await readFile(path.join(mcp, 'server.mjs'))).digest('hex') };
    const output = process.env.OPENPNP_STEP_E2E_EVIDENCE_DIR;
    if (output) await mkdir(output, { recursive: true });
    t.after(async () => { if (output) await writeFile(path.join(output, 'mcp-native-stepping.json'), JSON.stringify(evidence,
      (key, value) => key === 'configuration_root' ? '<isolated-native-simulator-configuration>' : value, 2) + '\n'); });
    async function call(name, args = {}) {
      const response = await client.callTool({ name: `openpnp_${name}`, arguments: args });
      assert.notEqual(response.isError, true, JSON.stringify(response.structuredContent));
      return readCompleteResponse(response.structuredContent, page => call('read_response_page', page));
    }
    async function observe(handle) {
      let result = handle; const deadline = Date.now() + 60000;
      while (['accepted', 'running'].includes(result.state)) {
        assert.ok(Date.now() < deadline, 'The original native operation did not stop; no action was replayed.');
        await delay(30); result = await call('get_operation', { operation_id: handle.operation_id });
      }
      assert.ok(['paused', 'succeeded'].includes(result.state), JSON.stringify(result));
      while ((await call('get_status')).native_busy) { assert.ok(Date.now() < deadline); await delay(20); }
      return result;
    }
    async function args(extra = {}) { return { session_id: session, request_id: randomUUID(), expected_config_revision: (await call('get_configuration')).config_revision, ...extra }; }
    async function operation(name, extra = {}) {
      const request = await args(extra), result = await observe(await call(name, request));
      assert.equal(result.state, 'succeeded', JSON.stringify(result));
      evidence.operations.push({ tool: name, request, operation_id: result.operation_id, result: result.result }); return result.result;
    }
    function counters(config) { return Object.fromEntries(config.settings.feeders.map(feeder => {
      assert.ok(Number.isSafeInteger(feeder.feed_count), 'Native feeder count must be reported.'); return [feeder.feeder_id, feeder.feed_count];
    })); }
    const caps = await call('get_capabilities'); evidence.bridge = caps.bridge;
    assert.equal(caps.bridge.simulation, true); assert.equal(caps.bridge.hardware_qualified, false);
    assert.ok(caps.tools.includes('openpnp_step_job'));
    const initial = await call('get_configuration'), beforeCounts = counters(initial);
    assert.equal((await call('get_status')).job_state, 'absent'); assert.equal(initial.enabled, false);
    session = (await call('request_control_session', { request_id: randomUUID(), ttl_seconds: 300 })).session_id;
    const part = initial.parts.find(part => part.id === 'R0805-1K'); assert.ok(part?.package_id && part.height_mm > 0);
    await operation('set_machine_enabled', { enabled: true }); await operation('home_machine');
    const imported = await call('import_job', { format: 'reference-csv', units: 'mm', widthMm: 40, heightMm: 30,
      content: `Ref,Val,Package,PosX,PosY,Rot,Side,PartId,Height\nR1,1K,${part.package_id},15,15,0,top,${part.id},${part.height_mm}\n` });
    async function prepare() {
      const job = await operation('prepare_job', { artifact_id: imported.artifact_id });
      assert.equal((await operation('validate_job')).valid, true); return job.job_id;
    }
    const job = await prepare();
    const first = await args({ job_id: job }); const handle = await call('step_job', first);
    assert.equal((await call('step_job', first)).operation_id, handle.operation_id);
    let current = await observe(handle), previous = 1;
    assert.equal(current.state, 'paused'); assert.equal(current.native_steps_started, 1);
    evidence.step_requests.push({ request: first, operation_id: handle.operation_id, native_steps_started: 1 });
    assert.equal((await call('step_job', first)).native_steps_started, 1, 'Lost initial reply must not execute another next() call.');
    // Transfer only the supervision lease; the native job and operation stay paused.
    await call('release_control_session', { session_id: session }); session = undefined;
    assert.equal((await call('get_operation', { operation_id: handle.operation_id })).state, 'paused');
    session = (await call('request_control_session', { request_id: randomUUID(), ttl_seconds: 300 })).session_id;
    while (current.state === 'paused') {
      assert.equal(current.result.pause_reason, 'single-native-step-completed');
      assert.equal(current.result.native_step_boundary.standstill_confirmed, true);
      assert.equal(current.result.native_step_boundary.boundary_semantics, 'one-native-processor-next-call');
      assert.ok(previous < 100, 'One-part fixture exceeded its bounded number of observed native calls.');
      const request = await args({ operation_id: handle.operation_id }); current = await observe(await call('step_job', request));
      assert.equal(current.operation_id, handle.operation_id); assert.equal(current.native_steps_started, previous + 1);
      assert.equal((await call('step_job', request)).native_steps_started, current.native_steps_started);
      evidence.step_requests.push({ request, operation_id: current.operation_id, native_steps_started: current.native_steps_started, state: current.state });
      previous = current.native_steps_started;
    }
    assert.equal(current.state, 'succeeded'); assert.equal(current.result.placed, 1); assert.equal(current.result.independently_inspected, 0);
    assert.equal(current.native_action_ledger.outcomes['feed:native_hook_returned'], 1);
    evidence.single_stepped_job = current; evidence.native_placements++;
    const resumedJob = await prepare(), second = await observe(await call('step_job', await args({ job_id: resumedJob })));
    assert.equal(second.state, 'paused'); assert.equal(second.native_steps_started, 1);
    const resumed = await observe(await call('resume_job', await args({ operation_id: second.operation_id })));
    assert.equal(resumed.operation_id, second.operation_id); assert.equal(resumed.state, 'succeeded'); assert.equal(resumed.result.placed, 1);
    assert.ok(resumed.native_steps_started > 1); assert.equal(resumed.native_action_ledger.outcomes['feed:native_hook_returned'], 1);
    evidence.continuously_resumed_job = resumed; evidence.native_placements++;
    const afterCounts = counters(await call('get_configuration'));
    assert.deepEqual(Object.keys(afterCounts).sort(), Object.keys(beforeCounts).sort());
    const delta = Object.entries(afterCounts).reduce((sum, [id, value]) => { assert.ok(value >= beforeCounts[id]); return sum + value - beforeCounts[id]; }, 0);
    assert.equal(delta, 2); evidence.native_feed_delta = delta;
    await operation('set_machine_enabled', { enabled: false }); await call('release_control_session', { session_id: session }); session = undefined;
    evidence.passed = true; evidence.completed_at = new Date().toISOString();
    evidence.scope = 'Actual packaged MCP and pinned native simulator: one job completed through single next() calls, another switched from a single step to continuous resume; exact two feeds and two native placements, no independent inspection.';
  });
