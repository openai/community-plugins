import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { cp, chmod, mkdir, mkdtemp, readFile, readdir, rename, writeFile } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { Client } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { StdioClientTransport } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/stdio.js';
import { readCompleteResponse } from '../../scripts/openpnp-read-response.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const connection = process.env.OPENPNP_MAPPED_E2E_CONNECTION_FILE;
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
async function inventory(directory) {
  const result = {};
  async function walk(dir) { for (const entry of await readdir(dir, { withFileTypes: true })) {
    const file = path.join(dir, entry.name);
    if (entry.isDirectory()) await walk(file);
    else { assert.ok(entry.isFile(), 'MCP copy contains only regular files.'); result[path.relative(directory, file)] = sha(await readFile(file)); }
  } }
  await walk(directory); return Object.fromEntries(Object.entries(result).sort(([a], [b]) => a.localeCompare(b)));
}

test('actual MCP edits an existing native mapped axis, revokes dependencies and refuses stale/portable requests',
  { skip: !connection, timeout: 180000 }, async t => {
    const fixtureDir = process.env.OPENPNP_MAPPED_E2E_FIXTURE_DIR;
    assert.ok(fixtureDir && path.isAbsolute(fixtureDir), 'The explicit owned test fixture directory is required.');
    const output = process.env.OPENPNP_MAPPED_E2E_EVIDENCE_DIR || await mkdtemp(path.join(os.tmpdir(), 'openpnp-mapped-evidence-'));
    await mkdir(output, { recursive: true, mode: 0o700 });
    const work = await mkdtemp(path.join(output, 'retained-')), mcp = path.join(work, 'mcp'), mcpState = path.join(work, 'state');
    await mkdir(mcpState, { mode: 0o700 }); await cp(path.join(root, 'plugins/openpnp/mcp'), mcp, { recursive: true }); await chmod(mcp, 0o700);
    const mcpInventory = await inventory(mcp), fixtureBytes = await readFile(path.join(fixtureDir, 'manifest.json'));
    const fixture = JSON.parse(fixtureBytes);
    assert.equal(fixture.test_fixture, true); assert.equal(fixture.public_axis_creation_api, false); assert.equal(fixture.physical_qualification, false);
    const evidence = { schema_version: 1, started_at: new Date().toISOString(), passed: false,
      fixture: 'test-only fresh native defaults with existing mapped X and alternate X; closed filesystem controls',
      synthetic_validation_prerequisites: true, native_validation_or_calibration_performed: false,
      shipped_start_simulator_fixture_qualified: false, hardware_qualified: false, native_placements: 0,
      operations: [], fixture_controls: [], fixture_manifest_sha256: sha(fixtureBytes), copied_mcp_inventory: mcpInventory,
      copied_mcp_server_sha256: mcpInventory['server.mjs'] };
    const client = new Client({ name: 'openpnp-existing-mapped-native-e2e', version: '1.0.0' }); let session;
    t.after(async () => {
      const cleanup = [];
      if (session) try { await client.callTool({ name: 'openpnp_release_control_session', arguments: { session_id: session } }); } catch (e) { cleanup.push(e.message); }
      try { await client.close(); } catch (e) { cleanup.push(e.message); }
      evidence.cleanup_errors = cleanup; if (cleanup.length) evidence.passed = false;
      evidence.completed_at = new Date().toISOString();
      await writeFile(path.join(output, 'mcp-native-mapped-axis.json'), JSON.stringify(evidence,
        (key, value) => key === 'configuration_root' ? '<owned-test-native-configuration>' : value, 2) + '\n', { flag: 'wx', mode: 0o600 });
      assert.deepEqual(cleanup, []);
    });
    const transport = new StdioClientTransport({ command: process.execPath, args: [path.join(mcp, 'server.mjs'), '--stdio'], cwd: mcpState,
      env: { PATH: process.env.PATH || '', OPENPNP_STATE_DIR: mcpState, OPENPNP_CONNECTION_FILE: connection }, stderr: 'pipe' });
    await client.connect(transport);
    async function call(name, args = {}) {
      const reply = await client.callTool({ name: `openpnp_${name}`, arguments: args });
      assert.notEqual(reply.isError, true, JSON.stringify(reply.structuredContent));
      try { return await readCompleteResponse(reply.structuredContent, page => call('read_response_page', page)); }
      catch (error) { (evidence.response_failures ||= []).push({ tool: name, code: error.code, known_response: error.known_response, native_action_repeated: false }); throw error; }
    }
    async function idle() { const until = Date.now() + 30000; while (true) { const value = await call('get_status', { view: 'progress' }); if (!value.native_busy && !value.active_operation_id) return; assert.ok(Date.now() < until, 'Native executor did not become idle.'); await delay(25); } }
    async function operation(name, args, expected = 'succeeded') {
      const requestId = randomUUID(); let value = await call(name, { session_id: session, request_id: requestId, ...args });
      const id = value.operation_id, until = Date.now() + 45000; assert.equal(typeof id, 'string');
      while (['accepted', 'running'].includes(value.state)) { assert.ok(Date.now() < until, 'Native deadline exceeded; original request was not replayed.'); await delay(50); value = await call('get_operation', { operation_id: id, view: 'progress' }); }
      value = await call('get_operation', { operation_id: id });
      evidence.operations.push({ tool: name, request_id: requestId, operation_id: id, state: value.state,
        code: value.result?.code, native_effect_pending: value.native_effect_pending, native_effect_kind: value.native_effect_kind });
      assert.equal(value.state, expected, JSON.stringify(value)); await idle(); return value;
    }
    async function control(action) {
      await idle(); const id = randomUUID(), dir = path.join(fixtureDir, 'controls');
      const tmp = path.join(dir, `${id}.tmp`), request = path.join(dir, `${id}.request.json`), receipt = path.join(dir, `${id}.receipt.json`);
      await writeFile(tmp, JSON.stringify({ request_id: id, action }) + '\n', { flag: 'wx', mode: 0o600 }); await rename(tmp, request);
      const until = Date.now() + 45000; let bytes;
      while (!bytes) { try { bytes = await readFile(receipt); } catch (error) { if (error.code !== 'ENOENT') throw error; assert.ok(Date.now() < until, 'Test-only fixture command timed out; it was not replayed.'); await delay(20); } }
      const value = JSON.parse(bytes); assert.equal(value.request_id, id); assert.equal(value.action, action); assert.equal(value.state, 'succeeded', JSON.stringify(value));
      evidence.fixture_controls.push({ action, request_id: id, receipt_sha256: sha(bytes), state: value.state, result: value.result }); return value.result;
    }
    function counts(config) { return Object.fromEntries(config.settings.feeders.map(row => { assert.ok(Number.isSafeInteger(row.feed_count) && row.feed_count >= 0); return [row.feeder_id, row.feed_count]; })); }
    const caps = await call('get_capabilities'); evidence.bridge = caps.bridge;
    assert.equal(caps.connected, true); assert.equal(caps.bridge.simulation, true); assert.equal(caps.bridge.hardware_qualified, false);
    assert.equal(caps.bridge.simulator_profile, 'native-simulator'); assert.equal(caps.bridge.bridge_artifact_hash_available, true);
    assert.equal(caps.bridge.bridge_artifact_sha256, fixture.bridge_artifact_sha256);
    assert.ok(caps.bridge.configuration_changes.includes('set_mapped_axis_geometry'));
    const initial = await call('get_configuration'), initialCounts = counts(initial);
    assert.equal(initial.enabled, false); assert.equal(initial.homed, false); assert.equal((await call('get_status', { view: 'progress' })).job_state, 'absent');
    const mapped = initial.settings.axes.find(row => row.axis_id === fixture.mapped_axis_id);
    assert.equal(mapped.native_class, 'org.openpnp.machine.reference.axis.ReferenceMappedAxis'); assert.equal(mapped.axis_type, 'X');
    assert.equal(mapped.input_axis_id, fixture.initial_source_axis_id); assert.equal(mapped.mapped_geometry_editable, true);
    session = (await call('request_control_session', { request_id: randomUUID(), ttl_seconds: 300 })).session_id;
    // Run before sample preparation adds native board libraries, isolating the actual mapped-class refusal.
    const portable = await operation('export_portable_configuration', { expected_config_revision: initial.config_revision }, 'failed');
    assert.equal(portable.result.code, 'CLASS_REJECTED'); assert.match(portable.result.message, /ReferenceMappedAxis/);
    evidence.portable_existing_mapped_class_refused = true;
    await operation('prepare_job', { sample: 'pnp-test' });
    const seeded = await control('seed_dependencies');
    assert.equal(seeded.synthetic_validation_prerequisites, true); assert.equal(seeded.native_validation_or_calibration_performed, false);
    assert.equal(seeded.job_state, 'validated'); assert.ok(seeded.registered_native_roots > 0 && seeded.calibrated_nozzle_tip_caches > 0 && seeded.valid_advanced_camera_caches > 0);
    const baseline = await call('get_status'), before = await call('get_configuration');
    const change = { type: 'set_mapped_axis_geometry', axis_id: fixture.mapped_axis_id, input_axis_id: fixture.alternate_source_axis_id,
      input_0_mm: 5, output_0_mm: -20, input_1_mm: 15, output_1_mm: 0 };
    const plan = await call('plan_configuration', { session_id: session, expected_config_revision: before.config_revision, changes: [change] });
    assert.equal(plan.plan.retained_native_guard, true);
    const impact = plan.plan.effects[0]; assert.equal(impact.derived_scale, 2); assert.equal(impact.derived_offset_mm, -30);
    assert.equal(impact.physical_calibration_valid, false); assert.equal(impact.input_axis_id, fixture.alternate_source_axis_id);
    assert.equal((await call('get_configuration')).config_revision, before.config_revision, 'Planning does not mutate revision.');
    const applied = await operation('apply_configuration', { plan_id: plan.plan_id, expected_config_revision: before.config_revision });
    assert.equal(applied.result.persisted, true); assert.equal(applied.native_effect_pending, false);
    const configured = await call('get_configuration'), after = await call('get_status');
    assert.notEqual(configured.config_revision, before.config_revision); assert.equal(after.job_state, 'prepared');
    assert.equal(after.board_load_revision, baseline.board_load_revision);
    assert.deepEqual(after.board_loads.loads.map(row => row.load_id), baseline.board_loads.loads.map(row => row.load_id));
    assert.deepEqual(after.board_loads.loads.map(row => row.placed_history), baseline.board_loads.loads.map(row => row.placed_history));
    assert.ok(after.board_loads.loads.every(row => row.registration.state === 'invalidated' && row.registration.reason === 'mapped-axis-geometry'));
    const actual = configured.settings.axes.find(row => row.axis_id === fixture.mapped_axis_id);
    for (const key of ['input_axis_id', 'input_0_mm', 'output_0_mm', 'input_1_mm', 'output_1_mm']) assert.equal(actual[key], change[key]);
    const observed = await control('inspect'); assert.equal(observed.job_state, 'prepared'); assert.equal(observed.validated_board_load_revision ?? null, null);
    assert.equal(observed.registered_native_roots, 0); assert.equal(observed.calibrated_nozzle_tip_caches, 0); assert.equal(observed.valid_advanced_camera_caches, 0);
    assert.equal(observed.persisted_mapped.input_axis_id, fixture.alternate_source_axis_id);
    for (const [key, value] of [['map-input-0', 5], ['map-output-0', -20], ['map-input-1', 15], ['map-output-1', 0]]) assert.deepEqual(observed.persisted_mapped[key], { value, units: 'Millimeters' });
    assert.equal(observed.mapped_source_setter_events, 1); evidence.preview = impact; evidence.persisted_native_geometry = observed.persisted_mapped;
    const backup = (await operation('backup_configuration', {})).result;
    const artifact = await call('get_native_artifact', { artifact_id: backup.artifact_id });
    const bytes = Buffer.from(artifact.base64, 'base64'); assert.equal(sha(bytes), artifact.sha256);
    const snapshot = JSON.parse(bytes).typed_snapshot;
    assert.ok(snapshot.omissions.some(row => row.target_id === fixture.mapped_axis_id && row.code === 'MAPPED_GEOMETRY_NOT_IN_VERSION_ONE_RESTORE'));
    assert.ok(snapshot.typed_changes.every(row => row.type !== 'set_mapped_axis_geometry'));
    evidence.typed_snapshot_omissions = snapshot.omissions.filter(row => row.type === 'set_mapped_axis_geometry' || row.code === 'MAPPED_SOURCE_LIMITS_UNSUPPORTED');
    const stalePlan = await call('plan_configuration', { session_id: session, expected_config_revision: configured.config_revision,
      changes: [{ ...change, input_axis_id: fixture.initial_source_axis_id }] });
    const drift = await control('change_source'); assert.equal(drift.out_of_band_source_changes, 1); assert.equal(drift.native_mapped.input_axis_id, fixture.initial_source_axis_id);
    assert.equal((await call('get_status', { view: 'progress' })).config_revision, configured.config_revision);
    const refused = await operation('apply_configuration', { plan_id: stalePlan.plan_id, expected_config_revision: configured.config_revision }, 'failed');
    assert.equal(refused.result.code, 'STALE_AXIS_PLAN'); assert.notEqual(refused.native_effect_pending, true);
    const final = await call('get_configuration'), finalStatus = await call('get_status'), finalFixture = await control('inspect');
    assert.equal(final.config_revision, configured.config_revision); assert.equal(finalStatus.configuration_fault, false);
    assert.deepEqual(counts(final), initialCounts); assert.equal(finalStatus.job_progress.placed, 0);
    assert.equal(final.enabled, false); assert.equal(final.homed, false);
    for (const key of ['enable_events', 'true_home_events', 'head_activity_events']) assert.equal(finalFixture[key], 0);
    assert.equal(finalFixture.mapped_source_setter_events, 2); assert.equal(finalFixture.out_of_band_source_changes, 1);
    assert.equal(finalFixture.persisted_mapped.input_axis_id, fixture.alternate_source_axis_id, 'Refused stale plan did not save external drift.');
    assert.deepEqual(await inventory(mcp), mcpInventory);
    evidence.initial_feed_counts = initialCounts; evidence.final_feed_counts = counts(final); evidence.native_feed_delta = 0;
    evidence.stale_native_source_refused = true; evidence.dependencies_revoked = true; evidence.zero_motion_events = true;
    evidence.copied_mcp_unchanged = true; evidence.passed = true;
  });
