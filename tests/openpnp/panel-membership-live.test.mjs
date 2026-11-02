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
import { digest } from '../../plugins/openpnp/mcp/domain/index.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const connectionFile = process.env.OPENPNP_PANEL_MEMBERSHIP_CONNECTION_FILE;
const out = process.env.OPENPNP_PANEL_MEMBERSHIP_EVIDENCE_DIR;
const server = path.join(root, 'plugins/openpnp/mcp/server.mjs');
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const holdersBefore = ['P1', 'P1⇒A', 'P1⇒B', 'P2', 'P2⇒A', 'P2⇒B'];
const holdersCloned = ['P1', 'P1⇒A', 'P1⇒B', 'P1⇒C', 'P2', 'P2⇒A', 'P2⇒B'];
const holdersAfter = ['P1', 'P1⇒A', 'P1⇒C', 'P2', 'P2⇒A', 'P2⇒B'];
const boardPaths = ['P1⇒A', 'P1⇒C', 'P2⇒A', 'P2⇒B'];
const desiredKeys = boardPaths.flatMap(holder => ['R1', 'R2'].map(ref => `${holder}⇒${ref}`)).sort();
const clone = (newId = 'C') => ({ action: 'clone_board_child', scope: 'job_instance', parent_instance_id: 'P1', source_child_id: 'A', new_child_id: newId,
  location: { frame: 'holder', units: 'mm', x: 0, y: 40, z: 0, rotation: 0 }, side: 'Top', enabled: true, check_fiducials: false });
const remove = (child = 'B', parent = 'P1') => ({ action: 'remove_board_child', scope: 'job_instance', parent_instance_id: parent, child_id: child });

// Requires a separately built panel73 Bridge. An older Bridge must not be used as a live fixture.
test('packaged MCP isolates repeated canonical panel definitions and executes its exact eight native placement identities', { skip: !connectionFile, timeout: 330000 }, async () => {
  assert.ok(out, 'Fresh owned evidence directory is required'); await mkdir(out, { recursive: true });
  const state = path.join(out, 'mcp-state'); await mkdir(state, { mode: 0o700 });
  const evidence = { kind: 'native-panel-board-membership-official-mcp', started_at: new Date().toISOString(), node: process.version,
    checks: [], requests: [], operations: [], refusals: [], documents: [], load_confirmations: [], simulation_only: true,
    native_shared_panel_identity_proven: false, panel_definition_scope: 'The public canonical importer expands repeated panel definition IDs into separate native Panel definitions. This journey proves repeated-definition semantics and P2 isolation; genuine shared native definition identity is covered separately by native model tests.', independently_inspected: 0, physical_qualification: false, physical_clearance_verified: false, unknown_action_replayed: false };
  const files = [server, path.join(root, 'plugins/openpnp/mcp/tool-inputs.json'), path.join(root, 'plugins/openpnp/bridge/openpnp-codex-bridge.jar'), fileURLToPath(import.meta.url)];
  const hashes = async () => Object.fromEntries(await Promise.all(files.map(async f => [path.relative(root, f), sha(await readFile(f))])));
  const inputs = await hashes(); evidence.inputs = inputs;
  const client = new Client({ name: 'panel-membership-native-journey', version: '1' });
  const transport = new StdioClientTransport({ command: process.execPath, args: [server, '--stdio'], cwd: state,
    env: { PATH: process.env.PATH ?? '', OPENPNP_STATE_DIR: state, OPENPNP_CONNECTION_FILE: connectionFile }, stderr: 'pipe' });
  let session, unresolved = false, stderr = '';
  const mark = label => evidence.checks.push(label);
  async function call(name, args = {}) {
    try {
      const response = await client.callTool({ name: `openpnp_${name}`, arguments: args });
      const value = await readCompleteResponse(response.structuredContent, p => call('read_response_page', p));
      if (response.isError) throw Object.assign(new Error(value.error.message), value.error);
      return value;
    } catch (error) { if (error.code === 'OUTCOME_UNKNOWN') unresolved = true; throw error; }
  }
  const status = () => call('get_status', { view: 'progress' });
  async function scope() { const s = await status(); return { job_id: s.job_id, expected_config_revision: s.config_revision, expected_job_revision: s.job_revision, expected_board_load_revision: s.board_load_revision }; }
  async function observe(handle) {
    const deadline = Date.now() + 150000; let op = handle;
    while (['accepted', 'running'].includes(op.state)) {
      assert.ok(Date.now() < deadline, 'Original operation observation timed out; no retry'); await delay(25);
      op = await call('get_operation', { operation_id: handle.operation_id, view: 'progress' });
    }
    op = await call('get_operation', { operation_id: handle.operation_id, view: 'full' }); evidence.operations.push(op);
    if (['outcome_unknown', 'unknown'].includes(op.state)) unresolved = true;
    while ((await status()).native_busy) { assert.ok(Date.now() < deadline, 'Native wrapper did not release ownership'); await delay(10); }
    return op;
  }
  async function request(name, args = {}) {
    const envelope = { session_id: session, request_id: randomUUID(), expected_config_revision: (await status()).config_revision, ...args };
    evidence.requests.push({ method: name, ...envelope, session_id: '<owned-lease>' });
    return { envelope, operation: await observe(await call(name, envelope)) };
  }
  async function operation(name, args = {}, expected = 'succeeded') { const r = await request(name, args); assert.equal(r.operation.state, expected, JSON.stringify(r.operation)); return r.operation.result; }
  async function refuse(name, args, code) {
    let value;
    try { const r = await request(name, args); assert.equal(r.operation.state, 'failed', JSON.stringify(r.operation)); value = r.operation.result; }
    catch (error) { if (!error.code) throw error; value = error; }
    assert.equal(value.code, code, JSON.stringify(value)); evidence.refusals.push({ method: name, code, message: value.message });
  }
  async function inspect() {
    const bound = await scope(); let offset = 0, fingerprint; const records = [];
    for (;;) {
      const page = await operation('inspect_job', { ...bound, offset, limit: 3, ...(fingerprint ? { expected_source_fingerprint: fingerprint } : {}) });
      fingerprint ??= page.source_fingerprint; assert.equal(page.source_fingerprint, fingerprint); records.push(...page.records);
      if (page.eof) { assert.equal(records.length, page.total_records); return records; } offset = page.next_offset;
    }
  }
  const feedCounts = async () => Object.fromEntries((await call('get_configuration')).settings.feeders.map(f => [f.feeder_id, f.feed_count]));
  const journal = async () => {
    const bytes = await readFile(path.join(path.dirname(connectionFile), 'journal/operations.jsonl'));
    assert.equal(bytes.at(-1), 10, 'Complete retained journal tail'); const rows = bytes.toString('utf8').trimEnd().split('\n').map(JSON.parse);
    assert.deepEqual(rows.map(x => x.sequence), rows.map((_, i) => i + 1)); return { bytes, rows };
  };
  const reservations = rows => rows.filter(x => x.type === 'job_lineage_reservation');
  const inventory = (rows, expected) => {
    assert.deepEqual(rows.map(x => x.holder_instance_id), expected);
    for (const row of rows) { assert.equal(row.kind, row.holder_instance_id.includes('⇒') ? 'board' : 'panel'); assert.deepEqual(row.placement_ids, row.kind === 'board' ? ['R1', 'R2'] : []); }
  };
  const recordKeys = rows => rows.map(r => `${r.holder_instance_id}⇒${r.placement_id}`).sort();
  async function save(label) {
    const result = await operation('save_job'); const artifact = await call('get_native_artifact', { artifact_id: result.artifact.artifact_id });
    assert.equal(artifact.mime_type, 'application/zip'); const bytes = Buffer.from(artifact.base64, 'base64');
    assert.equal(sha(bytes), artifact.sha256); assert.equal(artifact.sha256, result.artifact.sha256); assert.equal(bytes.length, result.artifact.size);
    await writeFile(path.join(out, `${label}.native-job.zip`), bytes, { flag: 'wx' }); evidence.documents.push({ label, result, sha256: sha(bytes), bytes: bytes.length }); return result;
  }
  async function edit(change, beforeIds, afterIds) {
    const bound = await scope(), beforeJournal = await journal(), beforeInspect = await inspect();
    const planned = await request('plan_placement_structure', { ...bound, changes: [change] });
    assert.equal(planned.operation.state, 'succeeded', JSON.stringify(planned.operation)); const stage = planned.operation.result, effects = stage.plan.effects;
    assert.equal(effects.profile, 'panel-board-membership-v1'); assert.equal(effects.scope, 'job_instance');
    assert.equal(effects.current_bridge_lineage_support, true); assert.equal(effects.source_files_modified, false); assert.equal(effects.material_history_modified, false);
    inventory(effects.before_inventory, beforeIds); inventory(effects.result_inventory, afterIds); assert.deepEqual(effects.affected_root_ids, ['P1']);
    assert.deepEqual(await inspect(), beforeInspect); assert.equal(reservations((await journal()).rows).length, reservations(beforeJournal.rows).length, 'Planning reserves no IDs');
    const plannedReplay = await call('plan_placement_structure', planned.envelope); assert.equal(plannedReplay.operation_id, planned.operation.operation_id);
    await refuse('plan_placement_structure', { ...planned.envelope, changes: [clone('DIFFERENT')] }, 'REQUEST_ID_CONFLICT');
    const applied = await request('apply_placement_structure', { ...bound, plan_id: stage.plan_id });
    assert.equal(applied.operation.state, 'succeeded', JSON.stringify(applied.operation)); const result = applied.operation.result;
    assert.equal(result.profile, 'panel-board-membership-v1'); assert.equal(result.job_identity_preserved, true); assert.equal(result.registration_invalidated, true);
    assert.equal(result.all_root_confirmation_invalidated, true); assert.equal(result.requires_explicit_load_rebinding, true); assert.deepEqual(result.result_inventory, effects.result_inventory);
    assert.equal(result.source_files_modified, false); assert.equal(result.material_history_modified, false);
    const after = await scope(); assert.equal(after.job_id, bound.job_id); assert.equal(after.expected_config_revision, bound.expected_config_revision); assert.notEqual(after.expected_job_revision, bound.expected_job_revision);
    const replayBefore = await journal(); const replay = await call('apply_placement_structure', applied.envelope); assert.equal(replay.operation_id, applied.operation.operation_id);
    await refuse('apply_placement_structure', { ...applied.envelope, plan_id: 'different-plan' }, 'REQUEST_ID_CONFLICT');
    assert.equal(reservations((await journal()).rows).length, reservations(replayBefore.rows).length);
    return { planned, applied };
  }
  try {
    await client.connect(transport); transport.stderr?.on('data', b => { if (stderr.length < 1024 * 1024) stderr += b.toString(); });
    const caps = await call('get_capabilities'); evidence.bridge = caps.bridge;
    assert.equal(caps.connected, true); assert.equal(caps.bridge.simulation, true); assert.equal(caps.bridge.hardware_qualified, false);
    assert.equal(caps.bridge.panel_board_membership.profile, 'panel-board-membership-v1');
    assert.equal(caps.bridge.bridge_artifact_sha256, inputs['plugins/openpnp/bridge/openpnp-codex-bridge.jar']);
    assert.equal(caps.bridge.upstream_commit, '5bd404cfc70f34103a3ca0fbb6b50c2b465f407c');
    const catalog = await client.listTools(); evidence.catalog_tool_count = catalog.tools.length;
    assert.ok(catalog.tools.find(x => x.name === 'openpnp_plan_placement_structure').inputSchema.properties.changes.oneOf);
    const first = await status(); assert.equal(first.job_state, 'absent'); assert.equal(first.machine.enabled, false);
    session = (await call('request_control_session', { request_id: randomUUID(), ttl_seconds: 600 })).session_id;
    const config = await call('get_configuration'); const part = config.parts.find(p => p.id === 'R0805-1K'); assert.ok(part?.height_mm > 0);
    const partFeeders = config.settings.feeders.filter(f => f.enabled && f.part_id === part.id).map(f => f.feeder_id); assert.ok(partFeeders.length > 0);
    const startingFeeds = await feedCounts(); evidence.starting_feed_counts = startingFeeds; evidence.selected_part = part; evidence.eligible_native_feeders = partFeeders;
    const configParent = path.join(path.dirname(connectionFile), 'simulator-configs');
    const dirs = (await readdir(configParent, { withFileTypes: true })).filter(x => x.isDirectory()); assert.equal(dirs.length, 1);
    const configRoot = path.join(configParent, dirs[0].name); const modelFiles = ['machine.xml', 'parts.xml', 'packages.xml'];
    const modelHashes = async () => Object.fromEntries(await Promise.all(modelFiles.map(async name => [name, sha(await readFile(path.join(configRoot, name)))])));
    const beforePreparation = await modelHashes(); evidence.model_files_before_prepare = beforePreparation;
    for (const name of modelFiles) await writeFile(path.join(out, `before-prepare-${name}`), await readFile(path.join(configRoot, name)), { flag: 'wx' });
    const base = await call('import_job', { format: 'reference-csv', content: `Ref,Val,Package,PosX,PosY,Rot,Side,PartId,Height\nR1,1K,${part.package_id},12,12,0,top,${part.id},${part.height_mm}\nR2,1K,${part.package_id},20,12,0,top,${part.id},${part.height_mm}\n`, units: 'mm', widthMm: 40, heightMm: 30, boardId: 'panel-board' });
    const canonical = (await call('get_artifact', { artifact_id: base.artifact_id })).content;
    const instance = (id, kind, definitionId, x, y) => ({ id, kind, definitionId, x, y, z: 0, rotation: 0, side: 'top', enabled: true });
    canonical.panels = [{ id: 'shared-panel', widthMm: 100, heightMm: 100, children: [instance('A', 'board', canonical.boards[0].id, 0, 0), instance('B', 'board', canonical.boards[0].id, 50, 0)] }];
    canonical.instances = [instance('P1', 'panel', 'shared-panel', 100, 100), instance('P2', 'panel', 'shared-panel', 200, 100)];
    const { revision, ...body } = canonical; canonical.revision = digest(body);
    const imported = await call('import_job', { format: 'canonical-json', content: JSON.stringify(canonical) });
    const original = await call('get_artifact', { artifact_id: imported.artifact_id }); const sourceFile = path.join(state, 'artifacts', `${imported.artifact_id}.json`); const sourceBytes = await readFile(sourceFile);
    assert.equal(sha(sourceBytes), imported.artifact_id); await writeFile(path.join(out, 'canonical-source.json'), sourceBytes, { flag: 'wx' });
    evidence.original_artifact = { artifact_id: imported.artifact_id, sha256: sha(sourceBytes), bytes: sourceBytes.length };
    const prepared = await operation('prepare_job', { artifact_id: imported.artifact_id }); assert.equal(prepared.requested, 8);
    const modelBefore = await modelHashes(); evidence.model_files_before_membership = modelBefore;
    for (const name of ['parts.xml', 'packages.xml']) assert.equal(modelBefore[name], beforePreparation[name], `Canonical preparation preserves library ${name}`);
    for (const name of modelFiles) await writeFile(path.join(out, `before-membership-${name}`), await readFile(path.join(configRoot, name)), { flag: 'wx' });
    evidence.preparation_scope = 'Legacy canonical prepare_job saves configuration and may serialize pinned native startup migrations. Exact machine/library preservation for panel membership is measured after preparation and before any panel request; before/after preparation files are retained separately.';
    const initialRows = await inspect(); assert.equal(initialRows.length, 8); assert.equal(new Set(recordKeys(initialRows)).size, 8);
    assert.ok(initialRows.every(r => r.part_id === part.id && r.ordinary_active_side_placement && !r.placed && !r.derived)); evidence.initial_records = initialRows;
    const untouchedP2 = initialRows.filter(r => r.root_instance_id === 'P2'), loadsBefore = await call('get_board_loads'); evidence.loads_before = loadsBefore;
    assert.deepEqual(await modelHashes(), modelBefore); const oldDocument = await save('before-membership');
    const unchangedBefore = await scope();
    await refuse('plan_placement_structure', { ...await scope(), changes: [clone(), remove()] }, 'INVALID_ARGUMENT');
    await refuse('plan_placement_structure', { ...await scope(), changes: [{ ...clone(), clear_history: true }] }, 'INVALID_ARGUMENT');
    await refuse('plan_placement_structure', { ...await scope(), changes: [clone('B')] }, 'STRUCTURE_ID_REUSED');
    await refuse('plan_placement_structure', { ...await scope(), changes: [{ ...clone(), parent_instance_id: 'P1⇒A' }] }, 'PANEL_NOT_FOUND');
    await refuse('plan_placement_structure', { ...await scope(), changes: [{ ...clone(), source_child_id: 'ABSENT' }] }, 'HOLDER_NOT_FOUND');
    assert.deepEqual(await scope(), unchangedBefore); assert.deepEqual(await inspect(), initialRows); assert.deepEqual(await modelHashes(), modelBefore); assert.deepEqual(await feedCounts(), startingFeeds);
    mark('closed and native selection refusals preserve exact job scope, model files and feed counts');
    const cloned = await edit(clone(), holdersBefore, holdersCloned); const clonedRows = await inspect(); assert.equal(clonedRows.length, 10);
    assert.deepEqual(clonedRows.filter(r => r.root_instance_id === 'P2'), untouchedP2);
    assert.deepEqual(clonedRows.filter(r => r.holder_instance_id === 'P1⇒C'), initialRows.filter(r => r.holder_instance_id === 'P1⇒A').map(r => ({ ...r, holder_instance_id: 'P1⇒C' })));
    const c = cloned.applied.operation.result.result_inventory.find(x => x.holder_instance_id === 'P1⇒C');
    assert.equal(c.source_holder_instance_id, 'P1⇒A'); assert.deepEqual(c.location, { frame: 'holder', units: 'Millimeters', x: 0, y: 40, z: 0, rotation: 0 }); assert.equal(c.side, 'Top'); assert.equal(c.enabled, true); assert.equal(c.check_fiducials, false);
    const removed = await edit(remove(), holdersCloned, holdersAfter); const editedRows = await inspect(); evidence.edited_records = editedRows;
    assert.deepEqual(recordKeys(editedRows), desiredKeys); assert.deepEqual(editedRows.filter(r => r.root_instance_id === 'P2'), untouchedP2);
    assert.deepEqual(removed.applied.operation.result.result_inventory.filter(r => r.holder_instance_id.startsWith('P2')), cloned.applied.operation.result.result_inventory.filter(r => r.holder_instance_id.startsWith('P2')));
    evidence.edits = [{ plan: cloned.planned.operation.result, apply: cloned.applied.operation.result }, { plan: removed.planned.operation.result, apply: removed.applied.operation.result }];
    await refuse('plan_placement_structure', { ...await scope(), changes: [clone('B')] }, 'STRUCTURE_ID_REUSED');
    const currentDocument = await save('after-membership'); assert.notEqual(currentDocument.artifact.sha256, oldDocument.artifact.sha256);
    await operation('load_job', { artifact_id: oldDocument.artifact.artifact_id });
    await refuse('plan_placement_structure', { ...await scope(), changes: [clone('D')] }, 'LINEAGE_DOCUMENT_STALE');
    await operation('load_job', { artifact_id: currentDocument.artifact.artifact_id }); assert.deepEqual(await inspect(), editedRows);
    await refuse('plan_placement_structure', { ...await scope(), changes: [clone('B')] }, 'STRUCTURE_ID_REUSED');
    // A non-applied plan exposes complete panel/board membership after native document reload.
    const census = await operation('plan_placement_structure', { ...await scope(), changes: [remove('B', 'P2')] }); inventory(census.plan.effects.before_inventory, holdersAfter);
    evidence.reloaded_holder_inventory = census.plan.effects.before_inventory; assert.deepEqual(await inspect(), editedRows);
    const modelJournal = await journal(), reserved = reservations(modelJournal.rows); assert.equal(reserved.length, 2);
    for (const [index, id] of ['C', 'B'].entries()) {
      const expected = [`holder-v1:${Buffer.from(`P1⇒${id}`, 'utf8').toString('base64url')}`, `P1⇒${id}⇒R1`, `P1⇒${id}⇒R2`].sort();
      assert.deepEqual([...reserved[index].payload.logical_ids].sort(), expected);
    }
    assert.equal(modelJournal.rows.filter(x => x.type === 'native_action_intent').length, 0);
    assert.deepEqual(await modelHashes(), modelBefore); assert.deepEqual(await feedCounts(), startingFeeds); assert.deepEqual(await readFile(sourceFile), sourceBytes);
    assert.deepEqual(await call('get_artifact', { artifact_id: imported.artifact_id }), original);
    evidence.model_files_after_membership = await modelHashes(); evidence.reservation_records = reserved;
    mark('P1 specializes independently, exact holder tombstones survive document aliasing, and old documents remain structurally stale');
    const pendingLoads = await call('get_board_loads'); evidence.loads_before_explicit_confirmation = pendingLoads;
    assert.deepEqual(pendingLoads.roots.map(r => r.root_instance_id), ['P1', 'P2']);
    for (const id of ['P1', 'P2']) {
      const load = (await call('get_board_loads')).roots.find(r => r.root_instance_id === id); const old = loadsBefore.roots.find(r => r.root_instance_id === id);
      assert.equal(load.load_id, old.load_id); assert.equal(load.definition_matches, id === 'P2'); assert.equal(load.load_state, 'job_binding_unconfirmed');
      const bound = await scope(); delete bound.expected_job_revision;
      const action = id === 'P1' ? 'replace' : 'same-load';
      const result = await operation('register_board_load', { ...bound, root_instance_id: id, action, side: load.side, expected_load_id: load.load_id });
      evidence.load_confirmations.push({ root_instance_id: id, action, observed_load_id: load.load_id, definition_matches: load.definition_matches, result });
    }
    const confirmed = await call('get_board_loads'); evidence.loads_after_explicit_confirmation = confirmed;
    const activeBoards = confirmed.roots.flatMap(r => r.boards.map(b => b.board_instance_id)).sort(); assert.deepEqual(activeBoards, boardPaths.toSorted());
    assert.ok(confirmed.roots.every(r => r.definition_matches && r.load_state === 'loaded'));
    assert.notEqual(confirmed.roots.find(r => r.root_instance_id === 'P1').load_id, loadsBefore.roots.find(r => r.root_instance_id === 'P1').load_id);
    assert.equal(confirmed.roots.find(r => r.root_instance_id === 'P2').load_id, loadsBefore.roots.find(r => r.root_instance_id === 'P2').load_id);
    assert.deepEqual(await modelHashes(), modelBefore); assert.deepEqual(await feedCounts(), startingFeeds);
    mark('explicit P1 replacement and unchanged P2 same-load confirmation use exact observed identities before production');
    await operation('set_machine_enabled', { enabled: true }); await operation('home_machine'); assert.equal((await operation('validate_job')).valid, true);
    const run = await request('start_job', { job_id: (await scope()).job_id }); assert.equal(run.operation.state, 'succeeded', JSON.stringify(run.operation)); assert.equal(run.operation.result.placed, 8);
    evidence.run_operation_id = run.operation.operation_id; const placedRows = await inspect(); assert.deepEqual(recordKeys(placedRows), desiredKeys); assert.ok(placedRows.every(r => r.placed));
    await operation('set_machine_enabled', { enabled: false }); const placedDocument = await save('placed-history');
    const loadedPlaced = await operation('load_job', { artifact_id: placedDocument.artifact.artifact_id }); assert.equal(loadedPlaced.job.placed, 8); assert.deepEqual(await inspect(), placedRows);
    await refuse('plan_placement_structure', { ...await scope(), changes: [remove('C')] }, 'STRUCTURE_HISTORY_PRESENT');
    const priorReplay = await journal(), priorFeeds = await feedCounts();
    const lateReplay = await call('apply_placement_structure', removed.applied.envelope); assert.equal(lateReplay.operation_id, removed.applied.operation.operation_id);
    assert.equal(reservations((await journal()).rows).length, reservations(priorReplay.rows).length); assert.deepEqual(await inspect(), placedRows); assert.deepEqual(await feedCounts(), priorFeeds);
    evidence.placed_records_after_reload = placedRows; evidence.late_replay_operation_id = lateReplay.operation_id;
    mark('native placed history survives save/load; new structural work is refused and original request replay remains receipt-only');
    const finalJournal = await journal(), runRows = finalJournal.rows.filter(r => r.payload?.operation_id === run.operation.operation_id);
    const completes = runRows.filter(r => r.type === 'native_placement_checkpoint' && r.payload.state === 'native-placement-complete-hook');
    const nativeKey = p => `${p.context.board_instance_id}⇒${p.context.placement_id}`;
    assert.equal(completes.length, 8); assert.equal(new Set(completes.map(r => r.payload.event_id)).size, 8); assert.deepEqual(completes.map(r => nativeKey(r.payload)).sort(), desiredKeys);
    for (const row of completes) { assert.equal(row.payload.context.part_id, part.id); assert.equal(row.payload.context.native_placed_status, true); }
    const intents = runRows.filter(r => r.type === 'native_action_intent'), outcomes = runRows.filter(r => r.type === 'native_action_outcome');
    assert.equal(intents.length, 32); assert.equal(outcomes.length, 32);
    const byFeeder = {};
    for (const kind of ['feed', 'pick', 'align', 'release']) {
      const before = intents.filter(r => r.payload.kind === kind), after = outcomes.filter(r => r.payload.kind === kind); assert.equal(before.length, 8); assert.equal(after.length, 8);
      assert.deepEqual(before.map(r => nativeKey(r.payload)).sort(), desiredKeys);
      for (const row of before) {
        assert.equal(row.payload.context.part_id, part.id); assert.equal(row.payload.context.board_instance_id.includes('P1⇒B'), false);
        const end = after.find(r => r.payload.action_id === row.payload.action_id); assert.ok(end); assert.equal(end.payload.state, 'native_hook_returned'); assert.ok(row.sequence < end.sequence); assert.equal(nativeKey(end.payload), nativeKey(row.payload));
        if (kind === 'feed') { assert.ok(partFeeders.includes(row.payload.context.feeder_id)); byFeeder[row.payload.context.feeder_id] = (byFeeder[row.payload.context.feeder_id] ?? 0) + 1; }
      }
    }
    assert.equal(finalJournal.rows.filter(r => r.type === 'native_placement_checkpoint' && r.payload.state === 'native-placement-complete-hook').length, 8);
    assert.equal(finalJournal.rows.filter(r => r.type === 'native_action_intent').length, 32); assert.equal(reservations(finalJournal.rows).length, 2);
    const finalFeeds = await feedCounts(); for (const [id, count] of Object.entries(startingFeeds)) assert.equal(finalFeeds[id] - count, byFeeder[id] ?? 0, `Exact feed counter delta ${id}`);
    evidence.native_hook_records = [...intents, ...outcomes, ...completes].sort((a, b) => a.sequence - b.sequence); evidence.native_journal_sha256 = sha(finalJournal.bytes);
    evidence.final_feed_counts = finalFeeds; evidence.completed_native_placements = 8; evidence.native_feed_delta = 8; evidence.native_action_pairs = 32;
    const finalModel = await modelHashes(); evidence.model_files_after_jobs = finalModel;
    assert.equal(finalModel['parts.xml'], modelBefore['parts.xml']); assert.deepEqual((await call('get_configuration')).parts, config.parts);
    evidence.library_after_job_scope = 'parts.xml and native part DTOs must remain exact. packages.xml may acquire pinned native lazy vision-compositing defaults during real alignment; before/after hashes are retained for independent diff review, not asserted globally unchanged.';
    assert.deepEqual(await readFile(sourceFile), sourceBytes); assert.deepEqual(await hashes(), inputs); evidence.passed = true;
    mark('all eight exact board/reference identities include P1/C and exclude removed P1/B, with32 matched feed/pick/align/release pairs');
  } catch (error) {
    evidence.passed = false; evidence.failure = { code: error.code, message: error.message, stack: error.stack, details: error.details };
    try { evidence.failure_status = await status(); if (evidence.failure_status?.active_operation_id || evidence.failure_status?.native_busy) unresolved = true; } catch (failure) { evidence.status_failure = { code: failure.code, message: failure.message }; }
    throw error;
  } finally {
    if (session && !unresolved) try { await call('release_control_session', { session_id: session }); } catch (error) { evidence.release_error = { code: error.code, message: error.message }; }
    await client.close(); evidence.finished_at = new Date().toISOString();
    await writeFile(path.join(out, 'mcp-server.stderr.log'), stderr);
    await writeFile(path.join(out, 'mcp-native-panel-membership.json'), JSON.stringify(evidence, (key, value) => key === 'session_id' ? '<owned-lease>' : key === 'configuration_root' ? '<owned-native-config>' : value, 2) + '\n');
  }
});
