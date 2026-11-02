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
const connection = process.env.OPENPNP_CAMERA_E2E_CONNECTION_FILE;
const sha = bytes => createHash('sha256').update(bytes).digest('hex');

test('packaged MCP applies native dynamic camera settling, retains truthful frames, and restores its settings',
  { skip: !connection, timeout: 180000 }, async t => {
    const state = await mkdtemp(path.join(os.tmpdir(), 'openpnp-camera-mcp-'));
    t.after(() => rm(state, { recursive: true, force: true }));
    const mcp = path.join(state, 'mcp');
    await cp(path.join(root, 'plugins/openpnp/mcp'), mcp, { recursive: true });
    await chmod(mcp, 0o700);
    const transport = new StdioClientTransport({ command: process.execPath, args: [path.join(mcp, 'server.mjs'), '--stdio'], cwd: state,
      env: { PATH: process.env.PATH || '', OPENPNP_STATE_DIR: state, OPENPNP_CONNECTION_FILE: connection }, stderr: 'pipe' });
    const client = new Client({ name: 'openpnp-camera-native-e2e', version: '1.0.0' });
    let session;
    t.after(async () => {
      if (session) { try { await client.callTool({ name: 'openpnp_release_control_session', arguments: { session_id: session } }); } catch {} }
      await client.close();
    });
    await client.connect(transport);
    const evidence = { started_at: new Date().toISOString(), passed: false, operations: [], captures: [],
      hardware_qualified: false, independent_image_quality_verified: false, native_placements: 0,
      packaged_server_sha256: sha(await readFile(path.join(mcp, 'server.mjs'))) };
    const output = process.env.OPENPNP_CAMERA_E2E_EVIDENCE_DIR;
    if (output) await mkdir(output, { recursive: true });
    t.after(async () => {
      if (output) await writeFile(path.join(output, 'mcp-native-camera-settling.json'), JSON.stringify(evidence,
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
        assert.ok(Date.now() < deadline, 'Camera operation timed out; it was not replayed.');
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
    function counters(config) {
      return Object.fromEntries(config.settings.feeders.map(feeder => {
        assert.ok(Number.isSafeInteger(feeder.feed_count)); return [feeder.feeder_id, feeder.feed_count];
      }));
    }
    function settings(camera) {
      return Object.fromEntries(['settle_method', 'settle_time_ms', 'settle_timeout_ms', 'settle_debounce', 'settle_threshold_percent', 'settle_full_color']
        .map(key => { assert.ok(Object.hasOwn(camera, key), key); return [key, camera[key]]; }));
    }
    const caps = await call('get_capabilities'); evidence.bridge = caps.bridge;
    assert.equal(caps.bridge.simulation, true); assert.equal(caps.bridge.hardware_qualified, false);
    assert.ok(caps.bridge.configuration_changes.includes('set_camera_dynamic_settling'));
    const initial = await call('get_configuration');
    assert.equal(initial.enabled, false); assert.equal(initial.homed, false);
    assert.equal((await call('get_status')).job_state, 'absent');
    const cameras = initial.settings.cameras;
    assert.equal(cameras.length, 2, 'Fresh native simulator must expose both stock cameras.');
    assert.deepEqual(cameras.map(camera => camera.native_class).sort(),
      ['org.openpnp.machine.reference.camera.ImageCamera', 'org.openpnp.machine.reference.camera.SimulatedUpCamera']);
    assert.ok(cameras.every(camera => camera.dynamic_settling_editable === true));
    evidence.initial_camera_settings = cameras.map(camera => ({ camera_id: camera.camera_id, ...settings(camera) }));
    const initialCounts = counters(initial);
    session = (await call('request_control_session', { request_id: randomUUID(), ttl_seconds: 300 })).session_id;
    const methods = ['Maximum', 'Mean', 'Euclidean', 'Square'];
    let baseline;
    for (const [index, method] of methods.entries()) {
      const changes = cameras.map((camera, n) => ({ type: 'set_camera_dynamic_settling', camera_id: camera.camera_id, method,
        timeout_ms: 30 + n * 10, debounce: index % 2, threshold_percent: 0.5 + n, full_color: n === 1 }));
      const configured = await apply(changes);
      assert.equal(configured.enabled, false);
      if (index === 0) assert.equal(configured.homed, false, 'Configuration must not home the machine.');
      for (const change of changes) {
        const actual = configured.settings.cameras.find(camera => camera.camera_id === change.camera_id);
        assert.equal(actual.settle_method, change.method); assert.equal(actual.settle_timeout_ms, change.timeout_ms);
        assert.equal(actual.settle_debounce, change.debounce); assert.equal(actual.settle_threshold_percent, change.threshold_percent);
        assert.equal(actual.settle_full_color, change.full_color);
      }
      if (index === 0) baseline = { artifact: await operation('backup_configuration'),
        settings: configured.settings.cameras.map(camera => ({ camera_id: camera.camera_id, ...settings(camera) })) };
      await operation('set_machine_enabled', { enabled: true });
      if (!(await call('get_configuration')).homed) await operation('home_machine');
      for (const camera of cameras) for (const mode of ['raw', 'settled']) {
        const artifact = await operation('capture_camera', { camera_id: camera.camera_id, mode });
        assert.equal(artifact.mime_type, 'image/png');
        const metadata = artifact.metadata;
        assert.equal(metadata.camera_id, camera.camera_id); assert.equal(metadata.capture_mode, mode);
        assert.equal(metadata.capture_status, 'capture-returned');
        assert.equal(metadata.settling_outcome, mode === 'raw' ? 'not-requested' : 'not-reported-by-native-api');
        assert.equal(metadata.image_validity, 'not-established-by-capture-return');
        assert.equal(metadata.physical_calibration_validity, 'not-assessed');
        assert.equal(metadata.native_timeout_is_hard_io_deadline, false);
        assert.equal(metadata.hardware_qualified, false);
        const requested = changes.find(change => change.camera_id === camera.camera_id);
        for (const key of ['method', 'timeout_ms', 'debounce', 'threshold_percent', 'full_color'])
          assert.equal(metadata.admission.settling_settings[key], requested[key], `Capture admission must bind ${key}`);
        assert.ok(Number.isFinite(metadata.elapsed_ms) && metadata.elapsed_ms >= 0);
        assert.ok(Number.isFinite(Date.parse(metadata.captured_at)));
        assert.equal(Object.hasOwn(metadata, 'native_recorded_settle_ms'), mode === 'settled');
        if (mode === 'settled') assert.ok(Number.isFinite(metadata.native_recorded_settle_ms) && metadata.native_recorded_settle_ms >= 0);
        const response = await client.callTool({ name: 'openpnp_get_native_artifact', arguments: { artifact_id: artifact.artifact_id } });
        assert.notEqual(response.isError, true, JSON.stringify(response.structuredContent));
        const image = response.content.find(content => content.type === 'image' && content.mimeType === 'image/png'); assert.ok(image);
        const bytes = Buffer.from(image.data, 'base64');
        assert.equal(sha(bytes), artifact.sha256); assert.equal(response.structuredContent.sha256, artifact.sha256);
        assert.deepEqual(bytes.subarray(0, 8), Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
        assert.equal(bytes.toString('ascii', 12, 16), 'IHDR');
        assert.equal(bytes.readUInt32BE(16), metadata.width); assert.equal(bytes.readUInt32BE(20), metadata.height);
        assert.ok(metadata.width > 0 && metadata.width <= 2048 && metadata.height > 0 && metadata.height <= 2048);
        if (output) await writeFile(path.join(output, `${index}-${camera.camera_id}-${mode}.png`), bytes, { flag: 'wx' });
        evidence.captures.push({ method, camera_id: camera.camera_id, mode, artifact });
      }
      await operation('set_machine_enabled', { enabled: false });
    }
    const beforeRefusal = await call('get_configuration');
    const refusal = await operation('export_portable_configuration', { expected_config_revision: beforeRefusal.config_revision }, 'failed');
    assert.equal(refusal.code, 'PORTABLE_PROFILE_LIMIT'); evidence.portable_refusal = refusal;
    assert.deepEqual((await call('get_configuration')).settings.cameras, beforeRefusal.settings.cameras);
    const restored = await operation('restore_configuration', { artifact_id: baseline.artifact.artifact_id });
    assert.equal(restored.full_configuration_restore, false);
    assert.deepEqual((await call('get_configuration')).settings.cameras.map(camera => ({ camera_id: camera.camera_id, ...settings(camera) })), baseline.settings);
    const fixed = await apply(cameras.map(camera => ({ type: 'set_camera_settling', camera_id: camera.camera_id, settle_time_ms: camera.settle_time_ms })));
    assert.ok(fixed.settings.cameras.every(camera => camera.settle_method === 'FixedTime'));
    assert.deepEqual(counters(fixed), initialCounts);
    assert.equal((await call('get_status')).job_state, 'absent'); assert.equal(fixed.enabled, false);
    await call('release_control_session', { session_id: session }); session = undefined;
    evidence.native_feed_delta = 0; evidence.completed_at = new Date().toISOString(); evidence.passed = true;
    evidence.scope = 'Actual packaged MCP, both native simulator cameras and all four dynamic methods; 16 hash-verified PNG captures, typed setting restoration and portable refusal. No image-quality, physical calibration or hard I/O deadline claim.';
  });
