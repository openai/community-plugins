import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID, createHash } from 'node:crypto';
import { chmod, cp, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import Ajv from '../../src/openpnp/node/node_modules/ajv/dist/ajv.js';
import { Client } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { StdioClientTransport } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/stdio.js';
import { readCompleteResponse } from '../../scripts/openpnp-read-response.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const connection = process.env.OPENPNP_CAMERA_SCALE_E2E_CONNECTION_FILE;
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const target = { x: 5.994319218624397, y: 6.413460468770919, z: 0, rotation: 0 };
const geometry = camera => Object.fromEntries(['units_per_pixel_x_mm', 'units_per_pixel_y_mm', 'working_plane_z_mm', 'head_offsets'].map(key => [key, camera[key]]));

test('packaged MCP measures stock ImageCamera scale, retrieves eight exact frames, and separately applies the typed proposal',
  { skip: !connection, timeout: 210000 }, async () => {
    const state = await mkdtemp(path.join(os.tmpdir(), 'openpnp-camera-scale-mcp-'));
    const output = process.env.OPENPNP_CAMERA_SCALE_E2E_EVIDENCE_DIR;
    if (output) await mkdir(output, { recursive: true });
    const mcp = path.join(state, 'mcp');
    await cp(path.join(root, 'plugins/openpnp/mcp'), mcp, { recursive: true }); await chmod(mcp, 0o700);
    const client = new Client({ name: 'openpnp-camera-scale-native-e2e', version: '1.0.0' });
    const transport = new StdioClientTransport({ command: process.execPath, args: [path.join(mcp, 'server.mjs'), '--stdio'], cwd: state,
      env: { PATH: process.env.PATH || '', OPENPNP_STATE_DIR: state, OPENPNP_CONNECTION_FILE: connection }, stderr: 'pipe' });
    const evidence = { started_at: new Date().toISOString(), passed: false, operations: [], captures: [], refusals: [],
      physical_qualification: false, hardware_qualified: false, native_placements: 0, automatic_configuration_apply: false,
      packaged_server_sha256: sha(await readFile(path.join(mcp, 'server.mjs'))) };
    let session;
    async function call(name, args = {}) {
      const response = await client.callTool({ name: `openpnp_${name}`, arguments: args });
      if (response.isError) { const error = response.structuredContent?.error; throw Object.assign(new Error(error?.message || JSON.stringify(response)), error); }
      return readCompleteResponse(response.structuredContent, page => call('read_response_page', page));
    }
    async function operation(name, args = {}) {
      const bound = { session_id: session, request_id: randomUUID(), ...args };
      let result = await call(name, bound); const operationId = result.operation_id, deadline = Date.now() + 60000;
      assert.equal(typeof operationId, 'string');
      while (['accepted', 'running'].includes(result.state)) {
        assert.ok(Date.now() < deadline, 'Operation timed out; no mutation was replayed.');
        await delay(50); result = await call('get_operation', { operation_id: operationId });
      }
      evidence.operations.push({ tool: name, request_id: bound.request_id, operation_id: operationId, state: result.state, result: result.result });
      assert.equal(result.state, 'succeeded', JSON.stringify(result));
      while ((await call('get_status')).native_busy) { assert.ok(Date.now() < deadline); await delay(25); }
      return { bound, operation: result, result: result.result };
    }
    async function apply(changes) {
      const before = await call('get_configuration');
      const plan = await call('plan_configuration', { session_id: session, expected_config_revision: before.config_revision, changes });
      const applied = await operation('apply_configuration', { plan_id: plan.plan_id });
      const after = await call('get_configuration'); assert.notEqual(after.config_revision, before.config_revision);
      return { before, after, plan, applied: applied.result };
    }
    async function refuse(args, code = 'INVALID_ARGUMENT') {
      await assert.rejects(call('run_calibration', args), error => error.code === code);
      evidence.refusals.push({ arguments: { ...args, session_id: '<current-owned-session>' }, code });
    }
    const counts = config => Object.fromEntries(config.settings.feeders.map(feeder => {
      assert.ok(Number.isSafeInteger(feeder.feed_count)); return [feeder.feeder_id, feeder.feed_count];
    }));
    try {
      await client.connect(transport);
      const capabilities = await call('get_capabilities'); evidence.bridge = capabilities.bridge;
      assert.equal(capabilities.bridge.simulation, true); assert.equal(capabilities.bridge.hardware_qualified, false);
      const scaleCapability = capabilities.bridge.camera_planar_scale;
      assert.equal(scaleCapability.runtime.available, true); assert.equal(scaleCapability.measurement_only, true);
      assert.equal(scaleCapability.automatic_application, false); assert.equal(scaleCapability.physical_qualification, false);
      const initial = await call('get_configuration'); evidence.initial_config_revision = initial.config_revision;
      assert.equal(initial.enabled, false); assert.equal(initial.homed, false); assert.equal((await call('get_status')).job_state, 'absent');
      const camera = initial.settings.cameras.find(camera => camera.native_class === 'org.openpnp.machine.reference.camera.ImageCamera');
      assert.ok(camera); const cameraId = camera.camera_id, initialCounts = counts(initial);
      evidence.initial_camera = camera;
      session = (await call('request_control_session', { request_id: randomUUID(), ttl_seconds: 300 })).session_id;
      await operation('set_machine_enabled', { enabled: true }); await operation('home_machine');
      // Initialize the stock renderer before changing its reported scale; no image source replacement or private model injection.
      const warm = await operation('capture_camera', { camera_id: cameraId, mode: 'raw' }); evidence.renderer_initialization_capture = warm.result;
      await apply([{ type: 'set_camera_settling', camera_id: cameraId, settle_time_ms: 600 }]);
      const motion = await call('plan_motion', { session_id: session, expected_config_revision: (await call('get_configuration')).config_revision,
        tool_id: cameraId, ...target, units: 'mm', speed: 0.2 });
      evidence.target_motion = (await operation('execute_motion', { plan_id: motion.plan_id })).result;
      assert.equal(evidence.target_motion.completion.position_source, 'native-simulation');
      evidence.target_capture = (await operation('capture_camera', { camera_id: cameraId, mode: 'raw' })).result;
      await apply([{ type: 'set_camera_geometry', camera_id: cameraId, ...geometry(camera),
        units_per_pixel_x_mm: camera.units_per_pixel_x_mm * 1.08, units_per_pixel_y_mm: camera.units_per_pixel_y_mm * 1.07 }]);
      const before = await call('get_configuration'); const cameraBefore = before.settings.cameras.find(camera => camera.camera_id === cameraId);
      const args = { session_id: session, request_id: randomUUID(), expected_config_revision: before.config_revision,
        recipe_id: 'camera-planar-scale', camera_id: cameraId, displacement_mm: 1, expected_feature_diameter_px: 24 };
      const catalog = await client.listTools(); const advertised = catalog.tools.find(tool => tool.name === 'openpnp_run_calibration'); assert.ok(advertised);
      const validate = new Ajv({ strict: true, allErrors: true, coerceTypes: false }).compile(advertised.inputSchema);
      assert.equal(validate(args), true, JSON.stringify(validate.errors));
      const invalid = [
        { ...args, request_id: '1-1-1-1-1' }, { ...args, request_id: 'AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA' },
        { ...args, displacement_mm: 0.49 }, { ...args, displacement_mm: 2.01 }, { ...args, displacement_mm: '1' },
        { ...args, expected_feature_diameter_px: 24.5 }, { ...args, expected_feature_diameter_px: 97 },
        { ...args, nozzle_id: 'N1' }, { ...args, enable: true }, { ...args, apply: true }, { ...args, pipeline: 'arbitrary' },
      ];
      for (const key of ['request_id', 'expected_config_revision', 'camera_id', 'displacement_mm', 'expected_feature_diameter_px']) {
        const missing = { ...args }; delete missing[key]; invalid.push(missing);
      }
      const preRefusalStatus = await call('get_status', { view: 'full' });
      for (const bad of invalid) { assert.equal(validate(bad), false); await refuse(bad); }
      await refuse({ ...args, request_id: randomUUID(), expected_config_revision: initial.config_revision }, 'REVISION_CONFLICT');
      assert.equal((await call('get_status', { view: 'full' })).metrics.operation_count, preRefusalStatus.metrics.operation_count);
      assert.deepEqual((await call('get_configuration')).settings.cameras, before.settings.cameras);
      const measured = await operation('run_calibration', args); const result = measured.result; evidence.measurement = result;
      assert.equal(result.measurement_status, 'accepted'); assert.equal(result.capture_calls, 8); assert.equal(result.recipe_move_calls, 7);
      assert.equal(result.calibration_applied, false); assert.equal(result.physical_scale_verified, false); assert.equal(result.physical_qualification, false);
      assert.equal(result.native_io_hard_deadline, false); assert.equal(Object.hasOwn(result, 'independent_physical_residuals'), false, 'The native JSON receipt must not invent physical residual measurements.');
      assert.equal(result.config_revision, before.config_revision); assert.equal(result.configuration_proposal_status, 'available');
      assert.equal(result.capture_api, 'native-settleAndCapture-without-lighting');
      assert.deepEqual(result.last_native_pose, result.origin);
      assert.equal(result.observations.length, 8); assert.equal(new Set(result.observations.map(frame => frame.artifact_id)).size, 8);
      assert.equal(result.heldout_simulator_image_prediction_errors.length, 2);
      for (const holdout of result.heldout_simulator_image_prediction_errors) assert.ok(holdout.error_px <= 1);
      assert.ok(result.final_baseline_error_px <= 1);
      const observed = await call('get_configuration'); assert.equal(observed.config_revision, before.config_revision);
      assert.deepEqual(observed.settings.cameras, before.settings.cameras); assert.deepEqual(counts(observed), initialCounts);
      for (const frame of result.observations) {
        const response = await client.callTool({ name: 'openpnp_get_native_artifact', arguments: { artifact_id: frame.artifact_id } });
        assert.notEqual(response.isError, true, JSON.stringify(response.structuredContent));
        const image = response.content.find(item => item.type === 'image' && item.mimeType === 'image/png'); assert.ok(image);
        const bytes = Buffer.from(image.data, 'base64'); const artifact = response.structuredContent;
        assert.equal(sha(bytes), frame.png_sha256); assert.equal(artifact.sha256, frame.png_sha256); assert.equal(bytes.length, frame.bytes);
        assert.deepEqual(bytes.subarray(0, 8), Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
        assert.equal(bytes.toString('ascii', 12, 16), 'IHDR'); assert.equal(bytes.readUInt32BE(16), 640); assert.equal(bytes.readUInt32BE(20), 480);
        assert.equal(artifact.metadata.operation_id, measured.operation.operation_id); assert.equal(artifact.metadata.config_revision, before.config_revision);
        assert.equal(artifact.metadata.observation.png_sha256, frame.png_sha256); assert.equal(artifact.metadata.physical_qualification, false);
        assert.equal(frame.capture_return_proves_stability, false); assert.equal(frame.loaded_source_pixel_sha256, result.loaded_source_pixel_sha256);
        if (output) await writeFile(path.join(output, `${frame.index}-${frame.label}.png`), bytes, { flag: 'wx' });
        evidence.captures.push({ artifact_id: frame.artifact_id, png_sha256: sha(bytes), bytes: bytes.length, label: frame.label });
      }
      const beforeDuplicate = await call('get_status', { view: 'full' });
      const duplicate = await call('run_calibration', measured.bound);
      assert.equal(duplicate.operation_id, measured.operation.operation_id); assert.deepEqual(duplicate.result, result);
      await refuse({ ...measured.bound, displacement_mm: 0.5 }, 'REQUEST_ID_CONFLICT');
      assert.equal((await call('get_status', { view: 'full' })).metrics.operation_count, beforeDuplicate.metrics.operation_count);
      const proposal = result.proposed_configuration_change;
      assert.equal(proposal.type, 'set_camera_geometry'); assert.equal(proposal.camera_id, cameraId);
      assert.equal(proposal.working_plane_z_mm, cameraBefore.working_plane_z_mm); assert.deepEqual(proposal.head_offsets, cameraBefore.head_offsets);
      for (const axis of ['x', 'y']) {
        const key = `units_per_pixel_${axis}_mm`;
        assert.ok(Number.isFinite(proposal[key]) && proposal[key] > 0);
        assert.ok(Math.abs(proposal[key] / cameraBefore[key] - 1) <= 0.2, 'Proposal must satisfy its bounded approximate-scale correction policy.');
      }
      evidence.separate_proposal_apply = await apply([proposal]);
      const appliedCamera = evidence.separate_proposal_apply.after.settings.cameras.find(camera => camera.camera_id === cameraId);
      assert.deepEqual(geometry(appliedCamera), geometry(proposal));
      await refuse({ ...args, request_id: randomUUID() }, 'REVISION_CONFLICT');
      assert.deepEqual(counts(await call('get_configuration')), initialCounts); assert.equal((await call('get_status')).job_state, 'absent');
      await operation('set_machine_enabled', { enabled: false });
      await call('release_control_session', { session_id: session }); session = undefined;
      // The runner owns this fresh state. Reading its durable journal provides phase evidence without an alternate execution channel.
      const journalBytes = await readFile(path.join(path.dirname(connection), 'journal/operations.jsonl'));
      const journal = journalBytes.toString('utf8').trim().split('\n').map(JSON.parse);
      const rows = journal.filter(row => row.payload?.operation_id === measured.operation.operation_id);
      for (const type of ['camera_scale_effect_intent', 'camera_scale_effect_outcome']) {
        assert.equal(rows.filter(row => row.type === type && row.payload.kind === 'camera-scale-capture').length, 8);
        assert.equal(rows.filter(row => row.type === type && row.payload.kind === 'camera-scale-move').length, 7);
      }
      assert.equal(rows.filter(row => row.type === 'camera_scale_observation').length, 8);
      assert.equal(rows.filter(row => row.type === 'camera_scale_measurement_completed').length, 1);
      assert.equal(journal.filter(row => row.type === 'native_action_intent' && ['feed', 'pick', 'place'].includes(row.payload.kind)).length, 0);
      evidence.measurement_journal_records = rows; evidence.journal_sha256 = sha(journalBytes); evidence.native_feed_delta = 0;
      evidence.passed = true;
      evidence.scope = 'Actual packaged stdio MCP and shipped fresh stock native simulator; public settings and motion select a stock-image target. Eight recipe PNGs plus two setup captures; measurement stays unapplied until a separate typed plan/apply. Simulator image prediction is not physical calibration.';
    } catch (error) {
      evidence.failure = { code: error.code, message: error.message, stack: error.stack };
      try { evidence.failure_status = await call('get_status'); } catch {}
      throw error;
    } finally {
      if (session) { try { await call('release_control_session', { session_id: session }); } catch {} }
      await client.close(); evidence.completed_at = new Date().toISOString();
      if (output) await writeFile(path.join(output, 'mcp-native-camera-scale.json'), JSON.stringify(evidence,
        (key, value) => key === 'configuration_root' ? '<isolated-native-simulator-configuration>' : value, 2) + '\n');
      await rm(state, { recursive: true, force: true });
    }
  });
