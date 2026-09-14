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
const connection = process.env.OPENPNP_PORTABLE_E2E_CONNECTION_FILE;
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
test('actual MCP preserves typed backlash and a used Board library through fresh adoption and native jobs', { skip: !connection, timeout: 240000 }, async t => {
  const evidenceDir = process.env.OPENPNP_PORTABLE_E2E_EVIDENCE_DIR || await mkdtemp(path.join(os.tmpdir(), 'openpnp-portable-evidence-'));
  await mkdir(evidenceDir, { recursive: true });
  const work = await mkdtemp(path.join(evidenceDir, 'retained-'));
  const runtime = process.env.OPENPNP_PORTABLE_E2E_RUNTIME, java = process.env.OPENPNP_PORTABLE_E2E_JAVA;
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
    await writeFile(path.join(evidenceDir, 'mcp-native-portable.json'), JSON.stringify(evidence, null, 2) + '\n');
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
  const counts = c => Object.fromEntries(c.settings.feeders.map(f => {
    assert.ok(typeof f.feeder_id === 'string' && f.feeder_id.length > 0);
    assert.ok(Number.isSafeInteger(f.feed_count) && f.feed_count >= 0, `Expected native feed count for ${f.feeder_id}`);
    return [f.feeder_id, f.feed_count];
  }));
  const backlashFields = ['method', 'offset_mm', 'speed_factor', 'sneak_up_mm', 'acceptable_tolerance_mm'];
  const backlashSettings = configuration => ['X', 'Y'].map(axisType => {
    const matches = configuration.settings.axes.filter(axis => axis.axis_type === axisType && axis.native_class === 'org.openpnp.machine.reference.axis.ReferenceControllerAxis');
    assert.equal(matches.length, 1, `Expected one exact native ${axisType} controller axis.`);
    const axis = matches[0]; assert.equal(axis.backlash_settings_editable, true);
    assert.equal(axis.physical_calibration_valid, false); assert.equal(axis.measurement_performed, false);
    return Object.fromEntries(['axis_id', 'axis_type', ...backlashFields].map(field => [field, axis[field]]));
  });
  const source = await peer('source', connection), sourceCaps = await source.call('get_capabilities');
  assert.equal(sourceCaps.bridge.simulation, true); assert.equal(sourceCaps.bridge.hardware_qualified, false);
  assert.ok(sourceCaps.bridge.tools.includes('openpnp_export_portable_configuration'));
  source.session = (await source.call('request_control_session', { request_id: randomUUID(), ttl_seconds: 600 })).session_id;
  const initial = await source.call('get_configuration'); assert.equal(initial.enabled, false); assert.equal(initial.homed, false);
  const nativeAxes = backlashSettings(initial);
  const backlashChanges = [
    { type: 'set_axis_backlash_settings', axis_id: nativeAxes[0].axis_id, method: 'DirectionalSneakUp', offset_mm: 0.2, speed_factor: 0.15, sneak_up_mm: 0.05, acceptable_tolerance_mm: 0.017 },
    { type: 'set_axis_backlash_settings', axis_id: nativeAxes[1].axis_id, method: 'OneSidedPositioning', offset_mm: -0.1, speed_factor: 0.35, sneak_up_mm: 0, acceptable_tolerance_mm: 0.023 },
  ];
  const expectedBacklash = backlashChanges.map((change, index) => ({ axis_id: change.axis_id, axis_type: nativeAxes[index].axis_type,
    ...Object.fromEntries(backlashFields.map(field => [field, change[field]])) }));
  const visionId = `BVS_Portable_${randomUUID().replaceAll('-', '')}`;
  const speedPlan = await source.call('plan_configuration', { session_id: source.session, expected_config_revision: initial.config_revision,
    changes: [{ type: 'set_machine_speed', speed: 0.4 }, ...backlashChanges] });
  await source.operation('apply_configuration', { plan_id: speedPlan.plan_id });
  const visionBase = await source.call('get_configuration');
  assert.deepEqual(backlashSettings(visionBase), expectedBacklash, 'Typed source apply must retain every nondefault backlash field.');
  assert.equal(visionBase.enabled, false); assert.equal(visionBase.homed, false);
  evidence.axis_backlash = { typed_changes: backlashChanges, source_before_job: backlashSettings(visionBase), measurement_performed: false, physical_calibration_valid: false };
  const plan = await source.call('plan_configuration', { session_id: source.session, expected_config_revision: visionBase.config_revision, changes: [
    { type: 'clone_vision_settings', source_vision_settings_id: 'BVS_Stock', vision_settings_id: visionId, name: 'Portable native vision' },
    { type: 'set_vision_parameter', vision_settings_id: visionId, parameter_name: 'pThreshold', value: 128 },
    { type: 'assign_vision_settings', holder: 'part:R0805-1K', kind: 'bottom', vision_settings_id: visionId },
  ] });
  await source.operation('apply_configuration', { plan_id: plan.plan_id });
  // Running the native sample creates the saved Board library that previously blocked export.
  const sourceBeforeCounts = counts(await source.call('get_configuration'));
  await source.operation('set_machine_enabled', { enabled: true }); await source.operation('home_machine');
  const sourcePrepared = (await source.operation('prepare_job', { sample: 'pnp-test' })).result;
  assert.equal(sourcePrepared.requested, 32);
  assert.equal((await source.operation('validate_job')).result.valid, true);
  const sourceCompleted = await source.operation('start_job', { job_id: sourcePrepared.job_id });
  assert.equal(sourceCompleted.result.placed, 32);
  for (const action of ['feed', 'pick', 'align', 'release'])
    assert.equal(sourceCompleted.native_action_ledger.outcomes[`${action}:native_hook_returned`], 32);
  await source.operation('set_machine_enabled', { enabled: false });
  const configured = await source.call('get_configuration'), sourceAfterCounts = counts(configured);
  assert.deepEqual(backlashSettings(configured), expectedBacklash, 'Source sample cannot silently reset configured backlash.');
  evidence.axis_backlash.source_after_job = backlashSettings(configured);
  assert.deepEqual(Object.keys(sourceAfterCounts).sort(), Object.keys(sourceBeforeCounts).sort());
  const sourceFeedDelta = Object.entries(sourceAfterCounts).reduce((sum, [id, value]) => {
    assert.ok(value >= sourceBeforeCounts[id]); return sum + value - sourceBeforeCounts[id];
  }, 0);
  assert.equal(sourceFeedDelta, 32);
  const sourceLoads = await source.call('get_board_loads');
  assert.ok(sourceLoads.loads.length > 0);
  assert.equal(sourceLoads.loads.reduce((sum, load) => sum + load.placed_history_count, 0), 32);
  evidence.source_completed_job = { operation_id: sourceCompleted.operation_id, result: sourceCompleted.result,
    native_action_ledger: sourceCompleted.native_action_ledger, native_feed_delta: sourceFeedDelta };
  const exportResult = (await source.operation('export_portable_configuration', { expected_config_revision: configured.config_revision })).result;
  assert.equal(exportResult.first_launch_only, true); assert.equal(exportResult.requires_separate_validation_jvm, true);
  assert.equal(exportResult.manifest.version, 2);
  const library = exportResult.manifest.board_library;
  assert.equal(library.profile, 'saved-flat-board-library-v1'); assert.equal(library.board_count, 1);
  // One shared native Board has 30 definition records; the sample job enables 32 instance placements.
  assert.equal(library.placement_count, 30); assert.equal(library.boards.length, 1);
  assert.equal(library.paste_pad_count, 48, 'Saved inert paste geometry must survive with the board definition.');
  assert.equal(exportResult.manifest.operational_journal_included, false);
  assert.deepEqual(await source.call('get_board_loads'), sourceLoads, 'Export preserves source load/history identity.');
  const exported = exportResult.artifact;
  const artifact = await source.call('get_native_artifact', { artifact_id: exported.artifact_id });
  const archive = Buffer.from(artifact.base64, 'base64'); assert.equal(hash(archive), exported.sha256);
  const bundle = path.join(work, 'portable.zip'); await writeFile(bundle, archive, { flag: 'wx', mode: 0o600 });
  const adoption = path.join(work, 'adopted'), newState = path.join(work, 'new-state');
  const adopted = cli('adopt', ['adopt-configuration', '--state-dir', path.dirname(connection), '--openpnp-home', runtime, '--java', java,
    '--bundle', bundle, '--sha256', exported.sha256, '--destination', adoption]);
  await adopted.exit; assert.equal(adopted.code, 0, await readFile(path.join(work, 'adopt.log'), 'utf8'));
  const adoptionReceipt = JSON.parse(await readFile(path.join(adoption, 'adoption.json'), 'utf8'));
  const generation = path.dirname(path.join(adoption, adoptionReceipt.configuration_directory));
  const adoptedManifest = JSON.parse(await readFile(path.join(generation, 'manifest.json'), 'utf8'));
  assert.deepEqual(adoptedManifest.board_library, library);
  const boardFiles = {};
  for (const board of library.boards) {
    assert.equal(hash(await readFile(path.join(generation, board.entry))), board.sha256);
    assert.equal(adoptionReceipt.generation_files_sha256[board.entry], board.sha256);
    boardFiles[board.entry] = board.sha256;
  }
  const launch = cli('launch', ['start-adopted-simulator', '--state-dir', newState, '--adoption-dir', adoption, '--openpnp-home', runtime, '--java', java]);
  const deadline = Date.now() + 75000, newConnection = path.join(newState, 'connection.json');
  while (true) {
    try { await access(newConnection); break; } catch (error) { if (error.code !== 'ENOENT') throw error; }
    if (launch.closed) { await launch.exit; assert.fail(await readFile(path.join(work, 'launch.log'), 'utf8')); }
    assert.ok(Date.now() < deadline, 'Adopted simulator did not become ready.'); await delay(100);
  }
  const target = await peer('adopted', newConnection), targetCaps = await target.call('get_capabilities');
  assert.equal(targetCaps.bridge.simulator_profile, 'adopted-simulator'); assert.equal(targetCaps.bridge.hardware_qualified, false);
  assert.notEqual(targetCaps.bridge.machine_id, sourceCaps.bridge.machine_id);
  assert.notEqual(targetCaps.bridge.bridge_instance_id, sourceCaps.bridge.bridge_instance_id);
  const before = await target.call('get_configuration'); assert.equal(before.enabled, false); assert.equal(before.homed, false); assert.equal(before.speed, 0.4);
  // Read every retained value before requesting a target lease or making any target model/motion change.
  assert.deepEqual(backlashSettings(before), expectedBacklash, 'Fresh native adoption must retain method, signed offset, speed, sneak-up and tolerance.');
  evidence.axis_backlash.adopted_before_any_target_mutation = backlashSettings(before);
  assert.deepEqual(before.settings, configured.settings, 'Adopted settings must preserve native source values.');
  assert.deepEqual(before.feeders, configured.feeders, 'Source counters remain source simulator data without reset.');
  assert.equal((await target.call('get_status', { view: 'progress' })).job_state, 'absent');
  const adoptedLoads = await target.call('get_board_loads');
  assert.deepEqual(adoptedLoads.loads, []); assert.deepEqual(adoptedLoads.roots, []);
  const startupJournal = await readFile(path.join(newState, 'journal/operations.jsonl'), 'utf8');
  assert.equal(startupJournal.includes(sourceCompleted.operation_id), false, 'Source operation IDs cannot be adopted.');
  for (const [entry, digest] of Object.entries(boardFiles))
    assert.equal(hash(await readFile(path.join(generation, entry))), digest);
  evidence.board_library = { manifest: library, saved_board_files_sha256: boardFiles,
    native_validation_jvm_roundtrip_verified: adoptionReceipt.native_xml_roundtrip_verified,
    first_launch_succeeded: true, fresh_empty_board_loads: true, source_operation_id_absent: true };
  target.session = (await target.call('request_control_session', { request_id: randomUUID(), ttl_seconds: 600 })).session_id;
  await target.operation('set_machine_enabled', { enabled: true }); await target.operation('home_machine');
  const part = before.parts.find(p => p.id === 'R0805-1K');
  const imported = await target.call('import_job', { format: 'reference-csv', units: 'mm', widthMm: 40, heightMm: 30,
    content: `Ref,Val,Package,PosX,PosY,Rot,Side,PartId,Height\nR1,1K,${part.package_id},15,15,0,top,${part.id},${part.height_mm}\nR2,1K,${part.package_id},20,15,0,top,${part.id},${part.height_mm}\n` });
  const prepared = (await target.operation('prepare_job', { artifact_id: imported.artifact_id })).result;
  assert.equal((await target.operation('validate_job')).result.valid, true);
  const completed = await target.operation('start_job', { job_id: prepared.job_id });
  assert.equal(completed.result.placed, 2); assert.equal(completed.native_action_ledger.outcomes['feed:native_hook_returned'], 2);
  assert.equal(completed.native_action_ledger.outcomes['align:native_hook_returned'], 2);
  await target.operation('set_machine_enabled', { enabled: false });
  const final = await target.call('get_configuration');
  assert.deepEqual(backlashSettings(final), expectedBacklash, 'Target native job must retain adopted backlash values.');
  evidence.axis_backlash.target_after_job = backlashSettings(final);
  const beforeCounts = counts(before), finalCounts = counts(final), sum = c => Object.values(c).reduce((a, b) => a + b, 0);
  assert.deepEqual(Object.keys(finalCounts).sort(), Object.keys(beforeCounts).sort());
  for (const key of Object.keys(finalCounts)) assert.ok(finalCounts[key] >= beforeCounts[key]);
  assert.equal(sum(finalCounts) - sum(beforeCounts), 2);
  const sourceAfterTarget = await source.call('get_configuration');
  assert.deepEqual(counts(sourceAfterTarget), counts(configured));
  assert.deepEqual(backlashSettings(sourceAfterTarget), expectedBacklash, 'Target work cannot change source axis settings.');
  evidence.axis_backlash.source_after_target_job = backlashSettings(sourceAfterTarget);
  evidence.axis_backlash.all_five_fields_preserved_before_target_mutation = true;
  evidence.feeder_counts_before = beforeCounts; evidence.feeder_counts_after = finalCounts;
  const oldJournal = path.join(newState, 'journal/operations.jsonl'), journalHash = hash(await readFile(oldJournal));
  const repeatState = path.join(work, 'repeat-state');
  const repeat = cli('repeat-launch', ['start-adopted-simulator', '--state-dir', repeatState, '--adoption-dir', adoption, '--openpnp-home', runtime, '--java', java]);
  await repeat.exit; assert.notEqual(repeat.code, 0);
  await assert.rejects(access(path.join(repeatState, 'connection.json')), { code: 'ENOENT' });
  assert.equal(hash(await readFile(oldJournal)), journalHash, 'Rejected repeat launch must preserve the live original journal.');
  evidence.source = sourceCaps.bridge; evidence.adopted = targetCaps.bridge; evidence.archive_sha256 = exported.sha256;
  assert.deepEqual(await source.call('get_board_loads'), sourceLoads, 'Target work preserves source placed history.');
  const sourceStillCompleted = await source.call('get_operation', { operation_id: sourceCompleted.operation_id });
  assert.deepEqual(sourceStillCompleted.result, sourceCompleted.result);
  assert.deepEqual(sourceStillCompleted.native_action_ledger, sourceCompleted.native_action_ledger);
  evidence.adopted_native_placements = 2; evidence.source_native_placements = 32;
  evidence.native_placements = 34; evidence.native_feed_delta = 34; evidence.repeated_launch_refused = true;
  evidence.exact_settings_preserved = true; evidence.source_material_unchanged = true; evidence.passed = true;
});
