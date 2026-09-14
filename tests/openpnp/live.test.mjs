import test from 'node:test';
import { readCompleteResponse } from '../../scripts/openpnp-read-response.mjs';
import assert from 'node:assert/strict';
import { randomUUID, createHash } from 'node:crypto';
import { mkdtemp, rm, mkdir, writeFile, readFile, cp } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { Client } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { StdioClientTransport } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/stdio.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const connectionFile = process.env.OPENPNP_E2E_CONNECTION_FILE;

test('official MCP client operates the pinned live native OpenPnP simulator end to end', { skip: !connectionFile, timeout: 600000 }, async t => {
  const state = await mkdtemp(path.join(os.tmpdir(), 'openpnp-live-mcp-')); t.after(() => rm(state, { recursive: true, force: true }));
  const frozenMcp = path.join(state, 'mcp');
  await cp(path.join(root, 'plugins/openpnp/mcp'), frozenMcp, { recursive: true });
  const transport = new StdioClientTransport({ command: process.execPath, args: [path.join(frozenMcp, 'server.mjs'), '--stdio'], cwd: state,
    env: { PATH: process.env.PATH || '', OPENPNP_STATE_DIR: state, OPENPNP_CONNECTION_FILE: connectionFile }, stderr: 'pipe' });
  const client = new Client({ name: 'openpnp-native-e2e', version: '1.0.0' });
  let session;
  t.after(async () => {
    if (session) { try { await client.callTool({ name: 'openpnp_release_control_session', arguments: { session_id: session } }); } catch {} }
    await client.close();
  });
  await client.connect(transport);
  const evidence = { started_at: new Date().toISOString(), host_node: process.versions.node, checks: [], hardware_qualified: false,
    packaged_server_sha256: createHash('sha256').update(await readFile(path.join(frozenMcp, 'server.mjs'))).digest('hex'),
    sanitization: 'Native configuration_root values replaced with an explicit portable placeholder; operation outcomes and hashes are unchanged.' };
  async function call(name, args = {}) {
    const response = await client.callTool({ name: `openpnp_${name}`, arguments: args });
    assert.notEqual(response.isError, true, JSON.stringify(response.structuredContent));
    return readCompleteResponse(response.structuredContent, pageArgs => call('read_response_page', pageArgs));
  }
  const capabilities = await call('get_capabilities');
  assert.equal(capabilities.connected, true); assert.equal(capabilities.bridge.upstream_commit, '5bd404cfc70f34103a3ca0fbb6b50c2b465f407c');
  assert.equal(capabilities.bridge.simulation, true); assert.equal(capabilities.bridge.hardware_qualified, false);
  assert.equal(capabilities.bridge.bridge_artifact_hash_available, true);
  assert.match(capabilities.bridge.bridge_artifact_sha256, /^[a-f0-9]{64}$/);
  for (const name of ['save_job', 'load_job', 'restore_configuration', 'prepare_job', 'start_job'])
    assert.ok(capabilities.tools.includes(`openpnp_${name}`), `The fresh native build must support ${name} before this test consumes material.`);
  evidence.bridge = capabilities.bridge;
  const initialStatus = await call('get_status');
  assert.equal(typeof initialStatus.native_busy, 'boolean');
  assert.equal(initialStatus.job_state, 'absent', 'Use a fresh simulator instance; this test consumes finite native feeder stock and never resets counts.');
  assert.ok(initialStatus.active_operation_id == null, 'The simulator must have no active operation.');
  session = (await call('request_control_session', { request_id: randomUUID(), ttl_seconds: 600 })).session_id;
  assert.equal(typeof session, 'string');
  let lastRenewal = Date.now();
  async function awaitNativeIdle() {
    const deadline = Date.now() + 10000;
    while ((await call('get_status')).native_busy) {
      assert.ok(Date.now() < deadline, 'Native executor remained busy after its operation receipt; no command was replayed.');
      await delay(50);
    }
  }
  async function observe(handle, timeout = 240000, desired = 'succeeded', awaitingResume = false) {
    const start = Date.now(); let result = handle;
    while (['accepted', 'running'].includes(result.state) || (awaitingResume && result.state === 'paused')) {
      if (Date.now() - start > timeout) assert.fail(`Native operation ${result.operation_id} did not reach ${desired}; no retry was made.`);
      if (Date.now() - lastRenewal > 60000) { await call('renew_control_session', { session_id: session, ttl_seconds: 600 }); lastRenewal = Date.now(); }
      await delay(100); result = await call('get_operation', { operation_id: handle.operation_id });
    }
    assert.equal(result.state, desired, JSON.stringify(result));
    await awaitNativeIdle();
    return result;
  }
  async function operation(name, args = {}, options = {}) {
    const requestId = randomUUID();
    const handle = await call(name, { request_id: requestId, session_id: session, ...args });
    const result = await observe(handle, options.timeout, options.desired);
    evidence.checks.push({ tool: `openpnp_${name}`, operation_id: handle.operation_id, request_id: requestId, state: result.state, result: result.result });
    return result.result;
  }
  assert.equal((await operation('set_machine_enabled', { enabled: true })).enabled, true);
  assert.equal((await operation('home_machine')).homed, true);
  const config = await call('get_configuration'); assert.ok(config.drivers.length > 0);
  const motion = await call('plan_motion', { session_id: session, x: 10, y: 10, z: 0, rotation: 0, units: 'mm', speed: 0.1 });
  const moved = await operation('execute_motion', { plan_id: motion.plan_id });
  assert.equal(moved.pose.x, 10); assert.equal(moved.pose.y, 10); assert.equal(moved.completion.position_source, 'native-simulation');
  const camera = await operation('capture_camera', { mode: 'settled' });
  const image = await client.callTool({ name: 'openpnp_get_native_artifact', arguments: { artifact_id: camera.artifact_id } });
  assert.notEqual(image.isError, true); assert.ok(image.content.some(content => content.type === 'image' && content.mimeType === 'image/png'));
  assert.equal(image.structuredContent.sha256, camera.sha256);
  const frame = image.content.find(content => content.type === 'image' && content.mimeType === 'image/png');
  assert.equal(createHash('sha256').update(Buffer.from(frame.data, 'base64')).digest('hex'), camera.sha256);
  const backup = await operation('backup_configuration');
  const toInvalidate = await call('plan_motion', { session_id: session, x: 11, y: 10, z: 0, rotation: 0, units: 'mm', speed: 0.1 });
  const plan = await call('plan_configuration', { session_id: session, expected_config_revision: config.config_revision, changes: [{ type: 'set_machine_speed', speed: 0.25 }] });
  await operation('apply_configuration', { plan_id: plan.plan_id });
  assert.equal((await call('get_configuration')).speed, 0.25);
  const beforeStale = await call('get_configuration');
  const stale = await operation('execute_motion', { plan_id: toInvalidate.plan_id }, { desired: 'failed' });
  assert.ok(['PLAN_STALE', 'NOT_FOUND'].includes(stale.code));
  assert.deepEqual((await call('get_configuration')).nozzles, beforeStale.nozzles);
  const restored = await operation('restore_configuration', { artifact_id: backup.artifact_id });
  assert.equal(restored.full_configuration_restore, false); assert.equal((await call('get_configuration')).speed, config.speed);
  if (!(await call('get_configuration')).homed) await operation('home_machine');

  const prepared = await operation('prepare_job', { sample: 'pnp-test' }); assert.equal(prepared.requested, 32);
  const validation = await operation('validate_job'); assert.equal(validation.valid, true);
  const feederCounts = configuration => Object.fromEntries(configuration.settings.feeders.map(feeder => {
    assert.ok(typeof feeder.feeder_id === 'string' && feeder.feeder_id.length > 0);
    assert.ok(Number.isSafeInteger(feeder.feed_count), `Expected native sample feeder count for ${feeder.feeder_id}`);
    return [feeder.feeder_id, feeder.feed_count];
  }));
  const sampleCountsBefore = feederCounts(await call('get_configuration'));
  assert.equal(Object.keys(sampleCountsBefore).length, (await call('get_configuration')).settings.feeders.length);
  const requestId = randomUUID();
  const started = await call('start_job', { request_id: requestId, session_id: session, job_id: prepared.job_id });
  const duplicate = await call('start_job', { request_id: requestId, session_id: session, job_id: prepared.job_id });
  assert.equal(duplicate.operation_id, started.operation_id);
  const pausedRequest = await call('pause_job', { request_id: randomUUID(), session_id: session, operation_id: started.operation_id });
  const paused = await observe(pausedRequest, 120000, 'paused'); assert.equal(paused.operation_id, started.operation_id);
  const resumed = await call('resume_job', { request_id: randomUUID(), session_id: session, operation_id: started.operation_id });
  const completed = await observe(resumed, 240000, 'succeeded', true); assert.equal(completed.result.placed, 32); assert.equal(completed.result.independently_inspected, 0);
  const completedStatus = await call('get_status');
  assert.equal(completedStatus.job_progress.placed, 32); assert.equal(completedStatus.job_progress.requested, 32);
  assert.ok(Number.isFinite(Date.parse(completedStatus.job_progress.observed_at)));
  assert.ok(completedStatus.job_progress.through_sequence <= completedStatus.through_sequence);
  const sampleCountsAfter = feederCounts(await call('get_configuration'));
  assert.deepEqual(Object.keys(sampleCountsAfter).sort(), Object.keys(sampleCountsBefore).sort());
  const sampleFeedDelta = Object.entries(sampleCountsAfter).reduce((total, [id, count]) => {
    assert.ok(count >= sampleCountsBefore[id], 'Native feed counts cannot decrease during this job.');
    return total + count - sampleCountsBefore[id];
  }, 0);
  assert.equal(sampleFeedDelta, 32, 'The duplicate start request must not duplicate native feeds.');
  evidence.checks.push({ tool: 'openpnp_start_job', operation_id: started.operation_id, request_id: requestId, state: completed.state, result: completed.result, pause_resume_verified: true, dedup_verified: true, native_feed_delta: sampleFeedDelta });
  const status = await call('get_request_status', { request_id: requestId }); assert.ok(JSON.stringify(status).includes(started.operation_id));

  const part = (await call('get_configuration')).parts.find(part => part.id === 'R0805-1K');
  assert.ok(part && part.height_mm > 0 && part.package_id);
  const imported = await call('import_job', { format: 'reference-csv', content: `Ref,Val,Package,PosX,PosY,Rot,Side,PartId,Height\nR1,1K,${part.package_id},15,15,0,top,${part.id},${part.height_mm}\n`, units: 'mm', widthMm: 40, heightMm: 30 });
  const custom = await operation('prepare_job', { artifact_id: imported.artifact_id }); assert.equal(custom.requested, 1);
  assert.equal((await operation('validate_job')).valid, true);
  const customCompleted = await operation('start_job', { job_id: custom.job_id }); assert.equal(customCompleted.placed, 1);
  const saved = await operation('save_job');
  assert.equal(saved.job.placed, 1); assert.equal(saved.transient_registration_preserved, false);
  const nativeDocument = await call('get_native_artifact', { artifact_id: saved.artifact.artifact_id });
  assert.equal(nativeDocument.sha256, saved.artifact.sha256);
  assert.equal(createHash('sha256').update(Buffer.from(nativeDocument.base64, 'base64')).digest('hex'), saved.artifact.sha256);
  assert.equal(Buffer.from(nativeDocument.base64, 'base64').subarray(0, 2).toString(), 'PK');
  await operation('prepare_job', { sample: 'pnp-test' });
  const reloaded = await operation('load_job', { artifact_id: saved.artifact.artifact_id });
  assert.equal(reloaded.job.placed, 1); assert.equal(reloaded.job.requested, 1);
  assert.equal(reloaded.placed_history_restored, true); assert.equal(reloaded.registration, 'invalidated');
  assert.equal(reloaded.requires_validation, true);
  const beforeRejectedStart = feederCounts(await call('get_configuration'));
  const rejectedStart = await operation('start_job', { job_id: reloaded.job.job_id }, { desired: 'failed' });
  assert.equal(rejectedStart.code, 'JOB_NOT_VALIDATED');
  assert.deepEqual(feederCounts(await call('get_configuration')), beforeRejectedStart);
  const issues = await operation('list_issues'); assert.equal(issues.automatic_issue_application, false);
  const report = await operation('export_run_report');
  const artifact = await call('get_native_artifact', { artifact_id: report.artifact_id });
  const reportContent = JSON.parse(Buffer.from(artifact.base64, 'base64').toString('utf8')); assert.equal(reportContent.hardware_qualified, false);
  const events = await call('get_events', { after_sequence: 0, limit: 500 }); assert.ok(events.events.length > 0);
  assert.equal((await operation('set_machine_enabled', { enabled: false })).enabled, false);
  await call('release_control_session', { session_id: session });
  evidence.completed_at = new Date().toISOString(); evidence.passed = true;
  if (process.env.OPENPNP_E2E_EVIDENCE_DIR) {
    await mkdir(process.env.OPENPNP_E2E_EVIDENCE_DIR, { recursive: true });
    await writeFile(path.join(process.env.OPENPNP_E2E_EVIDENCE_DIR, 'mcp-native-e2e.json'), JSON.stringify(evidence,
      (key, value) => key === 'configuration_root' ? '<isolated-native-simulator-configuration>' : value, 2) + '\n');
  }
});
