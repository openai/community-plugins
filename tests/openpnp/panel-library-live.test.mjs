import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { spawn } from 'node:child_process';
import { createWriteStream } from 'node:fs';
import { mkdir, readFile, writeFile, access, mkdtemp } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { Client } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { StdioClientTransport } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/stdio.js';
import { readCompleteResponse } from '../../scripts/openpnp-read-response.mjs';
import { digest } from '../../plugins/openpnp/mcp/domain/index.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const connection = process.env.OPENPNP_PANEL_LIBRARY_CONNECTION_FILE;
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const profile = 'saved-board-child-panel-library-v1';
const counterMap = config => Object.fromEntries(config.settings.feeders.map(f => {
  assert.ok(Number.isSafeInteger(f.feed_count) && f.feed_count >= 0); return [f.feeder_id, f.feed_count];
}));
const sum = o => Object.values(o).reduce((a, b) => a + b, 0);

test('actual packaged MCP exports a saved panel library and CLI adopts it with fresh job authority', { skip: !connection, timeout: 300000 }, async t => {
  const out = process.env.OPENPNP_PANEL_LIBRARY_EVIDENCE_DIR;
  const runtime = process.env.OPENPNP_PORTABLE_E2E_RUNTIME, java = process.env.OPENPNP_PORTABLE_E2E_JAVA;
  assert.ok(out && runtime && java, 'Declared owned output, native runtime and Java are required.'); await mkdir(out, { recursive: true });
  const work = await mkdtemp(path.join(out, 'retained-'));
  const evidence = { kind: 'native-panel-library-official-mcp-cli', passed: false, started_at: new Date().toISOString(), node: process.version,
    operations: [], checks: [], children: [], simulation_only: true, physical_qualification: false, independently_inspected: 0,
    prepared_source_job_executed: false, native_shared_panel_identity_proven: false,
    panel_definition_scope: 'Canonical repeated panel occurrences become distinct saved native definitions; exact shared-board and same-byte identity proof is the separate actual native model journey.',
    source_counter_reset: false, material_load_registration_claimed: false,
    material_scope: 'Adopted simulator retains observed source feeder counters. A new target job gets new board loads; this profile does not expose finite material-load registration.',
    bridge_sha256: sha(await readFile(path.join(root, 'plugins/openpnp/bridge/openpnp-codex-bridge.jar'))),
    mcp_sha256: sha(await readFile(path.join(root, 'plugins/openpnp/mcp/server.mjs'))) };
  const peers = [], children = [];
  const mark = label => evidence.checks.push(label);
  function cli(label, args) {
    const stream = createWriteStream(path.join(work, `${label}.log`), { flags: 'wx', mode: 0o600 });
    const child = spawn(process.execPath, [path.join(root, 'plugins/openpnp/scripts/openpnp.mjs'), ...args], { stdio: ['ignore', 'pipe', 'pipe'], shell: false });
    const p = { label, child, pid: child.pid, closed: false }; child.stdout.pipe(stream, { end: false }); child.stderr.pipe(stream, { end: false });
    p.exit = new Promise(resolve => { child.once('error', e => { p.error = e.message; }); child.once('close', (code, signal) => { p.closed = true; p.code = code; p.signal = signal; stream.end(resolve); }); }); children.push(p); return p;
  }
  async function closeChild(p) {
    if (!p.closed) p.child.kill('SIGTERM'); const until = Date.now() + 15000;
    while (!p.closed && Date.now() < until) await delay(50);
    const forced = !p.closed; if (forced) p.child.kill('SIGKILL'); await p.exit;
    evidence.children.push({ label: p.label, pid: p.pid, code: p.code, signal: p.signal, error: p.error, forced_cleanup: forced }); assert.equal(forced, false);
  }
  t.after(async () => {
    const errors = [];
    for (const p of peers.reverse()) { try { if (p.session) await p.call('release_control_session', { session_id: p.session }); } catch (e) { errors.push(e.message); } try { await p.client.close(); } catch (e) { errors.push(e.message); } }
    for (const p of children.reverse()) { try { await closeChild(p); } catch (e) { errors.push(e.message); } }
    evidence.cleanup_errors = errors; if (errors.length) evidence.passed = false; evidence.finished_at = new Date().toISOString();
    await writeFile(path.join(out, 'mcp-native-panel-library.json'), JSON.stringify(evidence, (key, value) => key === 'session_id' ? '<owned-lease>' : value, 2) + '\n'); assert.deepEqual(errors, []);
  });
  async function peer(label, file) {
    const state = path.join(work, `${label}-mcp`); await mkdir(state, { mode: 0o700 });
    const transport = new StdioClientTransport({ command: process.execPath, args: [path.join(root, 'plugins/openpnp/mcp/server.mjs'), '--stdio'], cwd: state,
      env: { PATH: process.env.PATH || '', OPENPNP_STATE_DIR: state, OPENPNP_CONNECTION_FILE: file }, stderr: 'pipe' });
    const client = new Client({ name: `panel-library-${label}`, version: '1' }); const p = { client, state };
    p.call = async (name, args = {}) => {
      const response = await client.callTool({ name: `openpnp_${name}`, arguments: args }); assert.notEqual(response.isError, true, JSON.stringify(response.structuredContent));
      return readCompleteResponse(response.structuredContent, page => p.call('read_response_page', page));
    };
    p.operation = async (name, args = {}) => {
      const request = randomUUID(); let value = await p.call(name, { session_id: p.session, request_id: request, ...args }); const id = value.operation_id; const until = Date.now() + 90000;
      while (['accepted', 'running'].includes(value.state)) { assert.ok(Date.now() < until, 'Pending action retained; no unknown retry.'); await delay(80); value = await p.call('get_operation', { operation_id: id, view: 'progress' }); }
      value = await p.call('get_operation', { operation_id: id }); evidence.operations.push({ peer: label, method: name, request_id: request, operation: value }); assert.equal(value.state, 'succeeded', JSON.stringify(value));
      while ((await p.call('get_status', { view: 'progress' })).native_busy) { assert.ok(Date.now() < until); await delay(30); } return value;
    };
    peers.push(p); await client.connect(transport); p.caps = (await p.call('get_capabilities')).bridge;
    assert.equal(p.caps.bridge_artifact_sha256, evidence.bridge_sha256); assert.equal(p.caps.simulation, true); assert.equal(p.caps.hardware_qualified, false);
    return p;
  }
  async function archive(p, result, label) {
    const value = await p.call('get_native_artifact', { artifact_id: result.artifact.artifact_id }); const bytes = Buffer.from(value.base64, 'base64');
    assert.equal(sha(bytes), result.artifact.sha256); assert.equal(value.sha256, result.artifact.sha256); const file = path.join(work, `${label}.zip`); await writeFile(file, bytes, { flag: 'wx', mode: 0o600 }); return { file, sha256: sha(bytes), bytes: bytes.length };
  }
  async function exported(p, label) {
    const config = await p.call('get_configuration'); assert.equal(config.enabled, false);
    const result = (await p.operation('export_portable_configuration', { expected_config_revision: config.config_revision })).result;
    assert.equal(result.manifest.operational_journal_included, false); assert.equal(result.manifest.physical_state_transferred, false); assert.equal(result.first_launch_only, true);
    return { result, archive: await archive(p, result, label) };
  }
  const source = await peer('source', connection); assert.equal(source.caps.portable_configuration.panel_library.profile, profile); source.session = (await source.call('request_control_session', { request_id: randomUUID(), ttl_seconds: 600 })).session_id;
  const initial = await source.call('get_configuration'); assert.equal(initial.enabled, false); assert.equal(initial.homed, false); const sourceCounts = counterMap(initial);
  const legacy = await exported(source, 'legacy-empty'); assert.equal(legacy.result.manifest.version, 1); mark('actual MCP legacy empty-library export remains v1');
  const part = initial.parts.find(p => p.id === 'R0805-1K'); assert.ok(part?.height_mm > 0);
  const imported = await source.call('import_job', { format: 'reference-csv', units: 'mm', widthMm: 40, heightMm: 30, boardId: 'portable-board',
    content: `Ref,Val,Package,PosX,PosY,Rot,Side,PartId,Height\nR1,1K,${part.package_id},12,12,0,top,${part.id},${part.height_mm}\n` });
  const canonical = (await source.call('get_artifact', { artifact_id: imported.artifact_id })).content;
  const instance = (id, kind, definitionId, x, y) => ({ id, kind, definitionId, x, y, z: 0, rotation: 0, side: 'top', enabled: true });
  canonical.panels = [{ id: 'portable-panel', widthMm: 100, heightMm: 80, children: [instance('A', 'board', canonical.boards[0].id, 0, 0), instance('B', 'board', canonical.boards[0].id, 50, 0)] }];
  canonical.instances = [instance('P1', 'panel', 'portable-panel', 100, 100), instance('P2', 'panel', 'portable-panel', 220, 100)]; const { revision, ...body } = canonical; canonical.revision = digest(body);
  const input = await source.call('import_job', { format: 'canonical-json', content: JSON.stringify(canonical) }); const immutable = await source.call('get_artifact', { artifact_id: input.artifact_id });
  const prepared = (await source.operation('prepare_job', { artifact_id: input.artifact_id })).result; assert.equal(prepared.requested, 4);
  const saved = (await source.operation('save_job')).result; await archive(source, saved, 'source-saved-job');
  await source.operation('load_job', { artifact_id: saved.artifact.artifact_id });
  const sourceLoads = await source.call('get_board_loads'); assert.equal(sourceLoads.roots.length, 2); assert.ok(sourceLoads.loads.every(l => l.placed_history_count === 0));
  const sourceConfig = await source.call('get_configuration'); assert.deepEqual(counterMap(sourceConfig), sourceCounts); assert.equal(sourceConfig.enabled, false); assert.equal(sourceConfig.homed, false);
  const exp = await exported(source, 'panels'); const lib = exp.result.manifest.panel_library;
  assert.equal(exp.result.manifest.version, 3); assert.equal(lib.profile, profile); assert.equal(lib.panel_count, 2); assert.equal(lib.panels.length, 2); assert.equal(lib.child_count, 4);
  assert.equal(lib.placement_count, 0); assert.equal(lib.pseudo_count, 0); assert.ok(exp.result.manifest.board_library.board_count >= 1);
  assert.equal(new Set(lib.panels.map(p => p.id)).size, 2); assert.equal(new Set(lib.panels.map(p => p.entry)).size, 2);
  assert.deepEqual(await source.call('get_artifact', { artifact_id: input.artifact_id }), immutable); assert.deepEqual(await source.call('get_board_loads'), sourceLoads);
  evidence.source = { capabilities: source.caps, imported_artifact_id: input.artifact_id, requested: 4, saved_document: saved, library: lib, board_library: exp.result.manifest.board_library, counts: sourceCounts, loads: sourceLoads };
  mark('native saved-document reload populates two supported saved panels, and MCP export preserves source job/load/artifact/counters');
  const adoption = path.join(work, 'adopted'); const a = cli('adopt', ['adopt-configuration', '--state-dir', path.dirname(connection), '--openpnp-home', runtime, '--java', java, '--bundle', exp.archive.file, '--sha256', exp.archive.sha256, '--destination', adoption]);
  await a.exit; assert.equal(a.code, 0, await readFile(path.join(work, 'adopt.log'), 'utf8'));
  const receipt = JSON.parse(await readFile(path.join(adoption, 'adoption.json'), 'utf8')); assert.equal(receipt.native_xml_roundtrip_verified, true); assert.equal(receipt.enabled, false); assert.equal(receipt.homed, false);
  const generation = path.dirname(path.join(adoption, receipt.configuration_directory)); const manifest = JSON.parse(await readFile(path.join(generation, 'manifest.json'), 'utf8')); assert.deepEqual(manifest.panel_library, lib);
  const files = [...lib.panels, ...manifest.board_library.boards]; const materializedFiles = {};
  for (const row of files) {
    const actual = sha(await readFile(path.join(generation, row.entry))); assert.equal(receipt.generation_files_sha256[row.entry], actual); materializedFiles[row.entry] = actual;
    // Panel child paths are deliberately materialized to exact generation paths before native load.
    if (row.entry.startsWith('library/boards/')) assert.equal(actual, row.sha256);
  }
  const targetState = path.join(work, 'target-state'); const launch = cli('launch', ['start-adopted-simulator', '--state-dir', targetState, '--adoption-dir', adoption, '--openpnp-home', runtime, '--java', java]);
  const targetConnection = path.join(targetState, 'connection.json'), until = Date.now() + 75000;
  for (;;) { try { await access(targetConnection); break; } catch (e) { if (e.code !== 'ENOENT') throw e; } if (launch.closed) { await launch.exit; assert.fail(await readFile(path.join(work, 'launch.log'), 'utf8')); } assert.ok(Date.now() < until); await delay(100); }
  const target = await peer('target', targetConnection); assert.equal(target.caps.simulator_profile, 'adopted-simulator'); assert.notEqual(target.caps.machine_id, source.caps.machine_id); assert.notEqual(target.caps.bridge_instance_id, source.caps.bridge_instance_id);
  const before = await target.call('get_configuration'); assert.equal(before.enabled, false); assert.equal(before.homed, false); assert.deepEqual(counterMap(before), sourceCounts);
  assert.ok(Array.isArray(before.parts) && before.parts.length > 0); assert.deepEqual(before.parts, sourceConfig.parts); const noLoads = await target.call('get_board_loads'); assert.deepEqual(noLoads.loads, []); assert.deepEqual(noLoads.roots, []);
  assert.equal((await target.call('get_status', { view: 'progress' })).job_state, 'absent');
  target.session = (await target.call('request_control_session', { request_id: randomUUID(), ttl_seconds: 600 })).session_id;
  const again = await exported(target, 'target-reexport'); const libAgain = again.result.manifest.panel_library;
  assert.equal(libAgain.profile, profile); for (const key of ['panel_count', 'child_count', 'placement_count', 'pseudo_count', 'expanded_record_count']) assert.equal(libAgain[key], lib[key]);
  assert.deepEqual(libAgain.panels.map(p => [p.id, p.semantic_sha256]), lib.panels.map(p => [p.id, p.semantic_sha256]));
  for (const [entry, hash] of Object.entries(materializedFiles)) assert.equal(sha(await readFile(path.join(generation, entry))), hash);
  evidence.adoption = { archive: exp.archive, receipt, materialized_files_sha256: materializedFiles, panel_byte_scope: 'Manifest panel hashes bind logical archive XML; activation hashes bind native generation filenames after materialization.', reexport_library: libAgain, fresh_capabilities: target.caps, fresh_loads: noLoads, source_counters_retained: true };
  mark('shipped CLI separate validation/one-time first launch preserves panel files and normalized re-export with fresh operational identity');
  // Deliberately prepare a separate small flat top-side job. Exported design data grants no job authority.
  const input2 = await target.call('import_job', { format: 'reference-csv', units: 'mm', widthMm: 40, heightMm: 30,
    content: `Ref,Val,Package,PosX,PosY,Rot,Side,PartId,Height\nT1,1K,${part.package_id},15,15,0,top,${part.id},${part.height_mm}\nT2,1K,${part.package_id},20,15,0,top,${part.id},${part.height_mm}\n` });
  const job2 = (await target.operation('prepare_job', { artifact_id: input2.artifact_id })).result; assert.equal(job2.requested, 2); const freshLoads = await target.call('get_board_loads'); assert.ok(freshLoads.loads.length > 0 && freshLoads.loads.every(l => l.placed_history_count === 0));
  for (const load of freshLoads.loads) assert.equal(sourceLoads.loads.some(s => s.load_id === load.load_id), false);
  await target.operation('set_machine_enabled', { enabled: true }); await target.operation('home_machine'); assert.equal((await target.operation('validate_job')).result.valid, true);
  const completed = await target.operation('start_job', { job_id: job2.job_id }); assert.equal(completed.result.placed, 2); for (const action of ['feed', 'pick', 'align', 'release']) assert.equal(completed.native_action_ledger.outcomes[`${action}:native_hook_returned`], 2);
  await target.operation('set_machine_enabled', { enabled: false }); const after = await target.call('get_configuration'); assert.equal(sum(counterMap(after)) - sum(sourceCounts), 2);
  const done = (await target.operation('save_job')).result; const reloaded = (await target.operation('load_job', { artifact_id: done.artifact.artifact_id })).result; assert.equal(reloaded.job.placed, 2);
  assert.deepEqual(counterMap(await source.call('get_configuration')), sourceCounts); assert.deepEqual(await source.call('get_board_loads'), sourceLoads); assert.deepEqual(await source.call('get_artifact', { artifact_id: input.artifact_id }), immutable);
  evidence.target_job = { prepared: job2, new_loads: freshLoads, completed, saved_document: done, counters_after: counterMap(after) }; evidence.native_placements = 2; evidence.native_action_pairs = 8;
  mark('separate freshly prepared target job completes two native placements with eight matched actions; saved history and source state are retained');
  const journalFile = path.join(targetState, 'journal/operations.jsonl'); const journalHash = sha(await readFile(journalFile)); const repeatState = path.join(work, 'repeat-state');
  const repeat = cli('repeat-launch', ['start-adopted-simulator', '--state-dir', repeatState, '--adoption-dir', adoption, '--openpnp-home', runtime, '--java', java]); await repeat.exit; assert.notEqual(repeat.code, 0); await assert.rejects(access(path.join(repeatState, 'connection.json')), { code: 'ENOENT' }); assert.equal(sha(await readFile(journalFile)), journalHash);
  evidence.repeated_launch_refused = true; evidence.passed = true;
});
