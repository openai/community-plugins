import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID, createHash } from 'node:crypto';
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { Client } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { StdioClientTransport } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/stdio.js';
import { readCompleteResponse } from '../../scripts/openpnp-read-response.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const connectionFile = process.env.OPENPNP_PART_BINDINGS_CONNECTION_FILE;
const out = process.env.OPENPNP_PART_BINDINGS_EVIDENCE_DIR;
const server = process.env.OPENPNP_PART_BINDINGS_MCP_SERVER ?? path.join(root, 'plugins/openpnp/mcp/server.mjs');
const sha = bytes => createHash('sha256').update(bytes).digest('hex');

test('official SDK resolves import aliases to existing native parts and preserves source, library and job history', { skip: !connectionFile, timeout: 280000 }, async () => {
  assert.ok(out, 'An owned evidence directory is required');
  await mkdir(out, { recursive: true });
  const state = path.join(out, 'mcp-state'); await mkdir(state, { mode: 0o700 });
  const evidence = { started_at: new Date().toISOString(), kind: 'native-part-bindings-official-mcp', node: process.version,
    simulation_only: true, physical_equivalence_verified: false, new_library_entries_allowed: false,
    checks: [], requests: [], operations: [], refusals: [], completed_jobs: [] };
  const files = [server, path.join(root, 'plugins/openpnp/bridge/openpnp-codex-bridge.jar'), fileURLToPath(import.meta.url)];
  const hashes = async () => Object.fromEntries(await Promise.all(files.map(async f => [path.relative(root, f), sha(await readFile(f))])));
  const inputs = await hashes(); evidence.inputs = inputs;
  const client = new Client({ name: 'native-part-bindings-journey', version: '1' });
  const transport = new StdioClientTransport({ command: process.execPath, args: [server, '--stdio'], cwd: state,
    env: { PATH: process.env.PATH ?? '', OPENPNP_STATE_DIR: state, OPENPNP_CONNECTION_FILE: connectionFile }, stderr: 'pipe' });
  let session, unresolved = false, stderr = '';
  const check = (label, fn) => { fn(); evidence.checks.push(label); };
  async function call(name, args = {}) {
    const response = await client.callTool({ name: `openpnp_${name}`, arguments: args });
    const result = await readCompleteResponse(response.structuredContent, p => call('read_response_page', p));
    if (response.isError) throw Object.assign(new Error(result.error.message), result.error);
    return result;
  }
  const status = () => call('get_status', { view: 'progress' });
  async function bound(name, args = {}) {
    const envelope = { session_id: session, request_id: randomUUID(), expected_config_revision: (await status()).config_revision, ...args };
    evidence.requests.push({ method: name, ...envelope, session_id: '<owned-lease>' });
    return { envelope, handle: await call(name, envelope) };
  }
  async function observe(handle) {
    const deadline = Date.now() + 150000; let op = handle;
    while (['accepted', 'running'].includes(op.state)) {
      assert.ok(Date.now() < deadline, 'Observation deadline exceeded; original request is not replayed');
      await delay(25); op = await call('get_operation', { operation_id: handle.operation_id, view: 'progress' });
    }
    op = await call('get_operation', { operation_id: handle.operation_id, view: 'full' });
    if (op.state === 'outcome_unknown') unresolved = true;
    evidence.operations.push(op);
    while ((await status()).native_busy) { assert.ok(Date.now() < deadline, 'Native wrapper still owns task'); await delay(10); }
    return op;
  }
  async function operation(name, args = {}, expected = 'succeeded') {
    const request = await bound(name, args); const op = await observe(request.handle);
    assert.equal(op.state, expected, JSON.stringify(op)); return op.result;
  }
  async function refuse(name, args, code) {
    let got;
    try {
      const request = await bound(name, args); const op = await observe(request.handle);
      assert.equal(op.state, 'failed', JSON.stringify(op)); got = op.result;
    } catch (error) { if (!error.code) throw error; got = error; }
    assert.equal(got.code, code, JSON.stringify(got));
    evidence.refusals.push({ method: name, code, message: got.message });
  }
  async function scope() {
    const s = await status(); return { job_id: s.job_id, expected_config_revision: s.config_revision, expected_job_revision: s.job_revision, expected_board_load_revision: s.board_load_revision };
  }
  const inspect = () => scope().then(s => operation('inspect_job', { ...s, offset: 0, limit: 200 }));
  const counts = async () => Object.fromEntries((await call('get_configuration')).settings.feeders.map(f => [f.feeder_id, f.feed_count]));
  async function imported(rows, boardId) {
    return call('import_job', { format: 'reference-csv', content: 'Ref,Val,Package,PosX,PosY,Rot,Side,PartId,Height\n' + rows.map((r, i) => `${r.ref},supplier-label,${r.package_id},${12 + i * 4},12,0,top,${r.id},${r.height_mm}`).join('\n') + '\n', units: 'mm', widthMm: 50, heightMm: 30, boardId });
  }
  async function fetchJsonArtifact(meta) {
    const value = await call('get_native_artifact', { artifact_id: meta.artifact_id });
    assert.equal(value.mime_type, 'application/json'); const bytes = Buffer.from(value.base64, 'base64');
    assert.equal(sha(bytes), value.sha256); assert.equal(value.sha256, meta.sha256);
    await writeFile(path.join(out, `${meta.artifact_id}.part-resolution.json`), bytes);
    return JSON.parse(bytes.toString('utf8'));
  }
  async function confirmLoads(action) {
    for (const load of (await call('get_board_loads')).roots) {
      const s = await scope(); delete s.expected_job_revision;
      await operation('register_board_load', { ...s, root_instance_id: load.root_instance_id, side: load.side, action, expected_load_id: load.load_id });
    }
  }
  try {
    await client.connect(transport); transport.stderr?.on('data', b => { stderr += b.toString(); });
    const caps = await call('get_capabilities'); evidence.bridge = caps.bridge;
    check('discover native existing-parts capability and exact launched artifact', () => {
      assert.equal(caps.connected, true); assert.equal(caps.bridge.simulation, true); assert.equal(caps.bridge.hardware_qualified, false);
      assert.equal(caps.bridge.canonical_part_bindings.profile, 'existing-parts-v1');
      assert.equal(caps.bridge.canonical_part_bindings.library_mutation, false);
      assert.equal(caps.bridge.canonical_part_bindings.physical_equivalence_verified, false);
      assert.equal(caps.bridge.bridge_artifact_sha256, inputs['plugins/openpnp/bridge/openpnp-codex-bridge.jar']);
    });
    const catalog = await client.listTools(); const prepareSchema = catalog.tools.find(t => t.name === 'openpnp_prepare_job').inputSchema;
    assert.ok(prepareSchema.properties.part_bindings); evidence.catalog_tool_count = catalog.tools.length;
    const initial = await status(); assert.equal(initial.job_state, 'absent'); assert.equal(initial.machine.enabled, false);
    session = (await call('request_control_session', { request_id: randomUUID(), ttl_seconds: 600 })).session_id;
    const config = await call('get_configuration');
    const a = config.parts.find(p => p.id === 'R0805-1K'), b = config.parts.find(p => p.id === 'R0603-1K');
    assert.ok(a?.height_mm > 0 && b?.height_mm > 0 && a.package_id !== b.package_id);
    const feederByPart = Object.fromEntries([a, b].map(p => { const feeders = config.settings.feeders.filter(f => f.part_id === p.id && f.enabled); assert.ok(feeders.length > 0); return [p.id, feeders.map(f => f.feeder_id)]; }));
    const initialCounts = await counts(); evidence.starting_feed_counts = initialCounts; evidence.native_feeder_by_part = feederByPart;
    const configParent = path.join(path.dirname(connectionFile), 'simulator-configs');
    const dirs = (await readdir(configParent, { withFileTypes: true })).filter(d => d.isDirectory()); assert.equal(dirs.length, 1);
    const configRoot = path.join(configParent, dirs[0].name);
    const libraryFiles = ['parts.xml', 'packages.xml'];
    const libraryHashes = async () => Object.fromEntries(await Promise.all(libraryFiles.map(async f => [f, sha(await readFile(path.join(configRoot, f)))])));
    const baselineLibrary = await libraryHashes(); evidence.library_before = baselineLibrary;
    const aliases = [{ ...a, id: 'SUPPLIER-1K-A', ref: 'R1' }, { ...a, id: 'SUPPLIER-1K-B', ref: 'R2' }, { ...b, id: 'SUPPLIER-0603', ref: 'R3' }, { ...a, ref: 'R4' }];
    const bindings = aliases.slice(0, 3).map((row, i) => ({ source_part_id: i === 0 ? row.id.toLowerCase() : row.id, native_part_id: i === 2 ? b.id : i === 0 ? a.id.toLowerCase() : a.id }));
    const source = await imported(aliases, 'alias-board'); const sourceContent = await call('get_artifact', { artifact_id: source.artifact_id });
    const sourceFile = path.join(state, 'artifacts', `${source.artifact_id}.json`); const sourceBytes = await readFile(sourceFile);
    assert.equal(sha(sourceBytes), source.artifact_id);
    evidence.original_artifact = { artifact_id: source.artifact_id, sha256: sha(sourceBytes), bytes: sourceBytes.length };
    // Legacy branches retain their previous behavior, with no machine action.
    const legacySample = await operation('prepare_job', { sample: 'pnp-test' }); assert.equal(legacySample.requested, 32);
    const nativeOnly = await imported([{ ...a, ref: 'L1' }], 'legacy-board');
    const legacy = await operation('prepare_job', { artifact_id: nativeOnly.artifact_id }); assert.equal(legacy.requested, 1);
    check('legacy sample and direct native-ID artifact prepare work without bindings', () => assert.deepEqual(Object.keys(legacy).includes('part_resolution'), false));
    const oldScope = await scope(); const unmoved = await inspect();
    const currentRevision = oldScope.expected_config_revision;
    const validArgs = { artifact_id: source.artifact_id, part_bindings: bindings };
    await refuse('prepare_job', { ...validArgs, expected_config_revision: undefined }, 'INVALID_ARGUMENT');
    await refuse('prepare_job', { ...validArgs, expected_config_revision: 'cfg-0' }, 'REVISION_CONFLICT');
    await refuse('prepare_job', { ...validArgs, request_id: '1-1-1-1-1' }, 'INVALID_ARGUMENT');
    await refuse('prepare_job', { sample: 'pnp-test', part_bindings: bindings }, 'INVALID_ARGUMENT');
    await refuse('prepare_job', { ...validArgs, part_bindings: [{ ...bindings[0], native_part_id: 'MISSING-NATIVE-TARGET' }, ...bindings.slice(1)] }, 'PART_UNMAPPED');
    await refuse('prepare_job', { ...validArgs, part_bindings: bindings.slice(0, 1) }, 'PART_UNMAPPED');
    await refuse('prepare_job', { ...validArgs, part_bindings: [...bindings, { source_part_id: 'UNUSED-ABSENT-SOURCE', native_part_id: a.id }] }, 'UNKNOWN_PART_BINDING');
    await refuse('prepare_job', { ...validArgs, part_bindings: [...bindings, { source_part_id: bindings[0].source_part_id.toUpperCase(), native_part_id: a.id }] }, 'DUPLICATE_PART_BINDING');
    const wrongHeight = await imported(aliases.map((p, i) => i === 0 ? { ...p, height_mm: p.height_mm + 0.1 } : p), 'bad-height');
    await refuse('prepare_job', { ...validArgs, artifact_id: wrongHeight.artifact_id }, 'PART_CONFLICT');
    const wrongPackage = await imported(aliases.map((p, i) => i === 0 ? { ...p, package_id: b.package_id } : p), 'bad-package');
    await refuse('prepare_job', { ...validArgs, artifact_id: wrongPackage.artifact_id }, 'PART_CONFLICT');
    const afterRefusals = await scope();
    check('all argument/resolution refusals preserve current revision', () => assert.equal(afterRefusals.expected_config_revision, currentRevision));
    assert.equal((await scope()).job_id, oldScope.job_id); assert.deepEqual((await inspect()).records, unmoved.records);
    assert.deepEqual(await libraryHashes(), baselineLibrary); assert.deepEqual(await counts(), initialCounts);
    const request = await bound('prepare_job', validArgs); const completed = await observe(request.handle); assert.equal(completed.state, 'succeeded', JSON.stringify(completed));
    const prepared = completed.result; assert.equal(prepared.requested, 4); assert.equal(prepared.part_binding_profile, 'existing-parts-v1');
    assert.equal(prepared.source_artifact_id, source.artifact_id); assert.equal(prepared.part_library_changed, false);
    const resolution = await fetchJsonArtifact(prepared.part_resolution); evidence.part_resolution = resolution;
    check('native JSON binds original import, request, configured IDs, explicit and implicit resolution', () => {
      assert.equal(resolution.source_artifact_id, source.artifact_id); assert.equal(resolution.request_id, request.envelope.request_id);
      assert.equal(resolution.operation_id, completed.operation_id); assert.equal(resolution.request_digest, prepared.request_digest);
      assert.equal(resolution.publication_authority, false); assert.equal(resolution.part_library_changed, false);
      assert.equal(resolution.physical_equivalence_verified, false); assert.equal(resolution.parts.length, 4);
      for (const row of resolution.parts) { assert.equal(row.definition_placement_count, 1); assert.ok([a.id, b.id].includes(row.native_part_id)); assert.equal(row.canonical_height_mm, row.native_height_mm); }
      assert.equal(resolution.parts.filter(row => row.explicit_binding).length, 3);
      assert.equal(resolution.parts.find(row => row.source_part_id === 'SUPPLIER-1K-A').native_part_id, a.id);
    });
    const replay = await call('prepare_job', request.envelope); assert.equal(replay.operation_id, completed.operation_id);
    await refuse('prepare_job', { ...request.envelope, part_bindings: [{ ...bindings[0], native_part_id: b.id }, ...bindings.slice(1)] }, 'REQUEST_ID_CONFLICT');
    const ready = await inspect(); const desired = { R1: a.id, R2: a.id, R3: b.id, R4: a.id };
    for (const row of ready.records) { assert.equal(row.part_id, desired[row.placement_id]); assert.equal(row.placed, false); }
    assert.deepEqual(await libraryHashes(), baselineLibrary); assert.deepEqual((await call('get_configuration')).parts, config.parts);
    assert.deepEqual(await readFile(sourceFile), sourceBytes); assert.deepEqual(await call('get_artifact', { artifact_id: source.artifact_id }), sourceContent);
    check('many-to-one import and idempotent request preserve immutable source and exact native library files', () => assert.deepEqual(prepared.part_library_changed, false));
    await operation('set_machine_enabled', { enabled: true }); await operation('home_machine');
    assert.equal((await operation('validate_job')).valid, true);
    const first = await operation('start_job', { job_id: prepared.job_id }); assert.equal(first.placed, 4);
    evidence.completed_jobs.push({ job_id: prepared.job_id, operation_id: evidence.operations.at(-1).operation_id, expected_parts: desired, placements: 4 });
    const placed = await inspect(); assert.equal(placed.records.filter(p => p.placed).length, 4);
    const saved = await operation('save_job'); const archive = await call('get_native_artifact', { artifact_id: saved.artifact.artifact_id });
    assert.equal(sha(Buffer.from(archive.base64, 'base64')), saved.artifact.sha256);
    const reloaded = await operation('load_job', { artifact_id: saved.artifact.artifact_id }); assert.equal(reloaded.job.placed, 4);
    assert.deepEqual((await inspect()).records, placed.records);
    assert.equal((await operation('validate_job', {}, 'failed')).code, 'BOARD_LOAD_REQUIRED');
    await confirmLoads('same-load'); assert.equal((await operation('validate_job')).valid, true);
    // No start call is made for the completed reloaded job.
    check('saved native resolved references and placed history survive reload without replay', () => assert.equal(reloaded.registration, 'invalidated'));
    const placedR1 = placed.records.find(row => row.placement_id === 'R1');
    const rejectPlaced = await operation('plan_placement_edits', { ...await scope(), changes: [{ scope: 'job_instance', holder_instance_id: placedR1.holder_instance_id, placement_id: 'R1', set: { part_id: b.id } }] }, 'failed');
    assert.equal(rejectPlaced.code, 'PLACED_HISTORY_CONFLICT');
    // Separate existing-tool substitution proof on a new two-placement job.
    await operation('set_machine_enabled', { enabled: false });
    const next = await imported([{ ...a, ref: 'S1' }, { ...a, ref: 'S2' }], 'substitution-board');
    const second = await operation('prepare_job', { artifact_id: next.artifact_id }); const beforeEdit = await inspect();
    const s1 = beforeEdit.records.find(p => p.placement_id === 'S1'); const editScope = await scope();
    const plan = await operation('plan_placement_edits', { ...editScope, changes: [{ scope: 'job_instance', holder_instance_id: s1.holder_instance_id, placement_id: 'S1', set: { part_id: b.id } }] });
    const applied = await operation('apply_placement_edits', { ...editScope, plan_id: plan.plan_id }); assert.equal(applied.registration_invalidated, true);
    const afterEdit = await inspect(); assert.equal(afterEdit.records.find(p => p.placement_id === 'S1').part_id, b.id); assert.equal(afterEdit.records.find(p => p.placement_id === 'S2').part_id, a.id);
    await confirmLoads('replace'); await operation('set_machine_enabled', { enabled: true }); await operation('home_machine');
    assert.equal((await operation('validate_job')).valid, true); assert.equal((await operation('start_job', { job_id: second.job_id })).placed, 2);
    evidence.completed_jobs.push({ job_id: second.job_id, operation_id: evidence.operations.at(-1).operation_id, expected_parts: { S1: b.id, S2: a.id }, placements: 2 });
    await operation('set_machine_enabled', { enabled: false });
    const finalCounts = await counts();
    for (const [part, expected] of [[a.id, 4], [b.id, 2]]) assert.equal(feederByPart[part].reduce((sum, id) => sum + finalCounts[id] - initialCounts[id], 0), expected, `Resolved part feeder delta ${part}`);
    assert.deepEqual((await call('get_configuration')).parts, config.parts); assert.deepEqual(await readFile(sourceFile), sourceBytes);
    // Only the connection's fresh runner-owned journal is read; no external configuration.
    const journalBytes = await readFile(path.join(path.dirname(connectionFile), 'journal/operations.jsonl'));
    const journal = journalBytes.toString('utf8').trim().split('\n').map(JSON.parse);
    const prepareFacts = journal.filter(row => row.payload?.operation_id === completed.operation_id);
    const publicationIntent = prepareFacts.filter(row => row.type === 'native_effect_intent' && row.payload.kind === 'canonical-part-binding-publication');
    const publicationOutcome = prepareFacts.filter(row => row.type === 'native_effect_outcome' && row.payload.kind === 'canonical-part-binding-publication');
    check('outer import publication retains matching forced intent/outcome before terminal success', () => {
      assert.equal(publicationIntent.length, 1); assert.equal(publicationOutcome.length, 1);
      const terminal = prepareFacts.filter(row => row.type === 'operation' && row.payload.state === 'succeeded');
      assert.equal(terminal.length, 1);
      assert.ok(prepareFacts.indexOf(publicationIntent[0]) < prepareFacts.indexOf(publicationOutcome[0]));
      assert.ok(prepareFacts.indexOf(publicationOutcome[0]) < prepareFacts.indexOf(terminal[0]));
    });
    evidence.prepare_publication_records = [...publicationIntent, ...publicationOutcome];
    const hooks = journal.filter(row => ['native_action_intent', 'native_action_outcome', 'native_placement_checkpoint'].includes(row.type));
    for (const job of evidence.completed_jobs) {
      const rows = hooks.filter(row => row.payload.operation_id === job.operation_id);
      const completes = rows.filter(row => row.type === 'native_placement_checkpoint' && row.payload.hook === 'Job.Placement.Complete');
      assert.equal(completes.length, job.placements); assert.equal(new Set(completes.map(row => row.payload.context.placement_id)).size, job.placements);
      for (const row of completes) { assert.equal(row.payload.context.part_id, job.expected_parts[row.payload.context.placement_id]); assert.equal(row.payload.context.native_placed_status, true); }
      for (const kind of ['feed', 'pick', 'release']) {
        const intents = rows.filter(row => row.type === 'native_action_intent' && row.payload.kind === kind);
        const outcomes = rows.filter(row => row.type === 'native_action_outcome' && row.payload.kind === kind);
        assert.equal(intents.length, job.placements); assert.equal(outcomes.length, job.placements);
        for (const intent of intents) {
          const partId = job.expected_parts[intent.payload.context.placement_id]; assert.equal(intent.payload.context.part_id, partId);
          if (kind === 'feed') assert.ok(feederByPart[partId].includes(intent.payload.context.feeder_id), `Observed feeder must supply the resolved native part ${partId}`);
          const outcome = outcomes.find(row => row.payload.action_id === intent.payload.action_id); assert.ok(outcome); assert.equal(outcome.payload.state, 'native_hook_returned');
        }
      }
    }
    const feedsById = {};
    for (const row of hooks.filter(row => row.type === 'native_action_intent' && row.payload.kind === 'feed')) feedsById[row.payload.context.feeder_id] = (feedsById[row.payload.context.feeder_id] ?? 0) + 1;
    for (const [id, count] of Object.entries(initialCounts)) assert.equal(finalCounts[id] - count, feedsById[id] ?? 0, `Exact observed feeder counter delta ${id}`);
    evidence.native_hook_records = hooks; evidence.native_journal_sha256 = sha(journalBytes); evidence.final_feed_counts = finalCounts;
    evidence.completed_native_placements = 6; evidence.native_feed_delta = 6;
    check('two real jobs retain six matching native feed/pick/release and Complete identities', () => assert.equal(evidence.completed_jobs.length, 2));
    assert.deepEqual(await hashes(), inputs); evidence.passed = true;
  } catch (error) {
    evidence.passed = false; evidence.failure = { code: error.code, message: error.message, stack: error.stack, details: error.details };
    try { evidence.failure_status = await status(); if (evidence.failure_status?.active_operation_id) unresolved = true; } catch (failure) { evidence.status_failure = { code: failure.code, message: failure.message }; }
    throw error;
  } finally {
    if (session && !unresolved) try { await call('release_control_session', { session_id: session }); } catch (error) { evidence.release_error = { code: error.code, message: error.message }; }
    await client.close(); evidence.finished_at = new Date().toISOString(); evidence.unknown_action_replayed = false;
    await writeFile(path.join(out, 'mcp-server.stderr.log'), stderr);
    await writeFile(path.join(out, 'mcp-native-part-bindings.json'), JSON.stringify(evidence, (key, value) => key === 'configuration_root' ? '<owned-native-config>' : value, 2) + '\n');
  }
});
