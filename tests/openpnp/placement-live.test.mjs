import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID, createHash } from 'node:crypto';
import { mkdtemp, rm, readFile, writeFile, mkdir } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { Client } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { StdioClientTransport } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/stdio.js';
import { readCompleteResponse } from '../../scripts/openpnp-read-response.mjs';
import { digest } from '../../plugins/openpnp/mcp/domain/index.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const connectionFile = process.env.OPENPNP_JOB_EDIT_CONNECTION_FILE;
const serverPath = process.env.OPENPNP_JOB_EDIT_MCP_SERVER ?? path.join(root, 'src/openpnp/node/server.mjs');
const sha = value => createHash('sha256').update(value).digest('hex');

test('official MCP client edits, inspects and reloads a shared native panel with explicit board-load identities', { skip: !connectionFile, timeout: 300000 }, async t => {
  const state = await mkdtemp(path.join(os.tmpdir(), 'openpnp-job-edit-mcp-'));
  t.after(() => rm(state, { recursive: true, force: true }));
  const sourceFiles = [serverPath, ...['contracts', 'client', 'runtime', 'settings-contracts', 'responses'].map(name => path.join(root, `src/openpnp/node/${name}.mjs`))];
  const sourceHashes = async () => Object.fromEntries(await Promise.all(sourceFiles.map(async file => [path.relative(root, file), sha(await readFile(file))])));
  const beforeHashes = await sourceHashes();
  const evidence = { started_at: new Date().toISOString(), node: process.version, scope: 'Actual official MCP SDK -> source/bundled MCP server -> pinned native OpenPnP simulator. Two actual native placements; model edits/inspection/board-load actions do not move or feed.', source_hashes: beforeHashes, checks: [], hardware_qualified: false, physical_load_verified: false };
  const client = new Client({ name: 'native-placement-load-e2e', version: '1' });
  const transport = new StdioClientTransport({ command: process.execPath, args: [serverPath, '--stdio'], cwd: state,
    env: { PATH: process.env.PATH ?? '', OPENPNP_STATE_DIR: state, OPENPNP_CONNECTION_FILE: connectionFile }, stderr: 'pipe' });
  let session; let lastRenewal = Date.now();
  t.after(async () => { if (session) try { await client.callTool({ name: 'openpnp_release_control_session', arguments: { session_id: session } }); } catch {} await client.close(); });
  await client.connect(transport);
  async function call(name, args = {}) {
    const response = await client.callTool({ name: `openpnp_${name}`, arguments: args });
    const value = await readCompleteResponse(response.structuredContent, page => call('read_response_page', page));
    if (response.isError) throw Object.assign(new Error(value.error.message), value.error);
    return value;
  }
  async function observe(handle) {
    const deadline = Date.now() + 180000; let result = handle;
    while (['accepted', 'running'].includes(result.state)) {
      assert.ok(Date.now() < deadline, 'Operation exceeded deadline; no action replayed.');
      if (Date.now() - lastRenewal > 60000) { await call('renew_control_session', { session_id: session, ttl_seconds: 600 }); lastRenewal = Date.now(); }
      await delay(50); result = await call('get_operation', { operation_id: handle.operation_id });
    }
    while ((await call('get_status')).native_busy) { assert.ok(Date.now() < deadline, 'Executor did not release; no action replayed.'); await delay(25); }
    return result;
  }
  async function operation(name, args = {}, expected = 'succeeded') {
    const request_id = args.request_id ?? randomUUID(); const handle = await call(name, { session_id: session, request_id, ...args });
    const done = await observe(handle); evidence.checks.push({ method: name, request_id, operation_id: done.operation_id, state: done.state, result: done.result });
    assert.equal(done.state, expected, JSON.stringify(done)); return done.result;
  }
  async function scope() {
    const status = await call('get_status'); return { job_id: status.job_id, expected_config_revision: status.config_revision, expected_job_revision: status.job_revision, expected_board_load_revision: status.board_load_revision };
  }
  async function inspect() {
    const bound = await scope(); let offset = 0; let fingerprint; const records = [];
    do {
      const page = await operation('inspect_job', { ...bound, offset, limit: 2, ...(fingerprint ? { expected_source_fingerprint: fingerprint } : {}) });
      if (!fingerprint) fingerprint = page.source_fingerprint;
      assert.equal(page.source_fingerprint, fingerprint); assert.equal(page.offset, offset); assert.equal(page.count, page.records.length);
      records.push(...page.records); offset = page.next_offset;
      if (page.eof) { assert.equal(records.length, page.total_records); return { records, fingerprint, bound }; }
    } while (true);
  }
  const select = (records, holder, ref) => { const found = records.filter(row => row.holder_instance_id === holder && row.placement_id === ref); assert.equal(found.length, 1); return found[0]; };
  const feederCounts = async () => Object.fromEntries((await call('get_configuration')).settings.feeders.map(feeder => {
    assert.ok(Number.isSafeInteger(feeder.feed_count)); return [feeder.feeder_id, feeder.feed_count];
  }));
  async function loadAction(rootId, action, side, expectedLoad) {
    const current = await scope(); delete current.expected_job_revision;
    return operation('register_board_load', { ...current, root_instance_id: rootId, action, side, ...(expectedLoad ? { expected_load_id: expectedLoad } : {}) });
  }
  try {
    const caps = await call('get_capabilities'); assert.equal(caps.connected, true); assert.equal(caps.bridge.simulation, true); assert.equal(caps.bridge.hardware_qualified, false);
    assert.equal(caps.bridge.upstream_commit, '5bd404cfc70f34103a3ca0fbb6b50c2b465f407c'); assert.match(caps.bridge.bridge_artifact_sha256, /^[a-f0-9]{64}$/);
    for (const name of ['get_board_loads', 'register_board_load', 'inspect_job', 'plan_placement_edits', 'apply_placement_edits']) assert.ok(caps.tools.includes(`openpnp_${name}`), `Native capability ${name} required before any action.`);
    evidence.bridge = caps.bridge;
    const initial = await call('get_status'); assert.equal(initial.job_state, 'absent', 'Dedicated fresh simulator required.'); assert.ok(initial.active_operation_id == null, 'Native nullable active operation must be absent.');
    session = (await call('request_control_session', { request_id: randomUUID(), ttl_seconds: 600 })).session_id;
    const config = await call('get_configuration'); const part = config.parts.find(item => item.id === 'R0805-1K'); assert.ok(part?.height_mm > 0 && part.package_id);
    const startingFeeds = await feederCounts(); evidence.starting_feeds = startingFeeds;
    const imported = await call('import_job', { format: 'reference-csv', content: `Ref,Val,Package,PosX,PosY,Rot,Side,PartId,Height\nR1,1K,${part.package_id},15,15,0,top,${part.id},${part.height_mm}\nR2,1K,${part.package_id},20,15,0,bottom,${part.id},${part.height_mm}\nR3,1K,${part.package_id},10,10,0,top,${part.id},${part.height_mm}\n`, units: 'mm', widthMm: 40, heightMm: 30 });
    const canonical = (await call('get_artifact', { artifact_id: imported.artifact_id })).content;
    canonical.boards[0].placements.find(item => item.ref === 'R3').enabled = false;
    const boardId = canonical.boards[0].id;
    const instance = (id, kind, definitionId, x, y, side = 'top', enabled = true) => ({ id, kind, definitionId, x, y, z: 0, rotation: 0, side, enabled });
    canonical.panels = [{ id: 'panel', widthMm: 100, heightMm: 40, children: [instance('A', 'board', boardId, 0, 0), instance('X', 'board', boardId, 50, 0, 'top', false)] }];
    canonical.instances = [instance('P1', 'panel', 'panel', 100, 100), instance('B', 'board', boardId, 100, 150, 'bottom')];
    const { revision: oldRevision, ...body } = canonical; canonical.revision = digest(body);
    const artifact = await call('import_job', { format: 'canonical-json', content: JSON.stringify(canonical) });
    const prepared = await operation('prepare_job', { artifact_id: artifact.artifact_id }); assert.equal(prepared.requested, 2);
    const first = await inspect(); assert.equal(first.records.length, 9);
    assert.equal(select(first.records, 'P1⇒X', 'R1').holder_effectively_enabled, false);
    assert.equal(select(first.records, 'B', 'R1').ordinary_active_side_placement, false);
    assert.equal(select(first.records, 'P1⇒A', 'R3').enabled, false);
    const beforeInvalid = await feederCounts();
    const edit = (holder, ref, set, scope = 'job_instance') => ({ scope, holder_instance_id: holder, placement_id: ref, set });
    const rejected = await operation('plan_placement_edits', { ...await scope(), changes: [edit('P1⇒A', 'R1', { comments: 'must not apply' }), edit('B', 'R2', { part_id: 'missing-configured-part' })] }, 'failed');
    assert.equal(rejected.code, 'PART_NOT_FOUND'); assert.deepEqual((await inspect()).records, first.records); assert.deepEqual(await feederCounts(), beforeInvalid);
    const pending = await operation('plan_placement_edits', { ...await scope(), changes: [edit('P1⇒A', 'R1', { comments: 'stale plan' })] });
    const staleScope = await scope(); const oldLoads = await call('get_board_loads'); const oldB = oldLoads.roots.find(item => item.root_instance_id === 'B');
    await loadAction('B', 'flip', 'top', oldB.load_id);
    await assert.rejects(operation('apply_placement_edits', { ...staleScope, plan_id: pending.plan_id }), { code: 'BOARD_LOAD_REVISION_CONFLICT' });
    await loadAction('B', 'flip', 'bottom', oldB.load_id); assert.deepEqual(await feederCounts(), beforeInvalid);
    const plannedScope = await scope();
    const reviewed = await operation('plan_placement_edits', { ...plannedScope, changes: [edit('P1⇒A', 'R1', { location: { frame: 'holder', units: 'in', x: 1, y: 0.5, z: 0, rotation: 0 } }), edit('B', 'R3', { comments: 'Reviewed disabled record' }, 'job_shared_definition')] });
    assert.equal(reviewed.plan.effects.changes[1].affected.length, 3, 'Shared board definition affects all three loads including X-out.');
    const applyRequest = randomUUID(); const applied = await operation('apply_placement_edits', { ...plannedScope, request_id: applyRequest, plan_id: reviewed.plan_id });
    assert.equal(applied.changed, true); assert.equal(applied.job_identity_preserved, true); assert.equal(applied.registration_invalidated, true); assert.equal(applied.opaque_placed_history_preserved, true);
    const priorOperation = evidence.checks.at(-1).operation_id;
    const duplicate = await call('apply_placement_edits', { ...plannedScope, request_id: applyRequest, session_id: session, plan_id: reviewed.plan_id });
    assert.equal(duplicate.operation_id, priorOperation, 'Same durable request observes prior apply instead of applying twice.');
    const edited = await inspect(); assert.equal(select(edited.records, 'P1⇒A', 'R1').location.x, 25.4); assert.equal(select(edited.records, 'B', 'R1').location.x, 15);
    for (const holder of ['P1⇒A', 'P1⇒X', 'B']) assert.equal(select(edited.records, holder, 'R3').comments, 'Reviewed disabled record');
    const afterEditLoads = await call('get_board_loads');
    const sameLoad = await operation('register_board_load', { session_id: session, ...Object.fromEntries(Object.entries(await scope()).filter(([key]) => key !== 'expected_job_revision')), root_instance_id: 'B', action: 'same-load', side: 'bottom', expected_load_id: oldB.load_id }, 'failed');
    assert.equal(sameLoad.code, 'BOARD_LOAD_JOB_MISMATCH');
    for (const item of afterEditLoads.roots) await loadAction(item.root_instance_id, 'replace', item.side, item.load_id);
    const noopScope = await scope(); const noop = await operation('plan_placement_edits', { ...noopScope, changes: [edit('P1⇒A', 'R1', { enabled: true })] });
    assert.equal((await operation('apply_placement_edits', { ...noopScope, plan_id: noop.plan_id })).changed, false); assert.equal((await scope()).expected_board_load_revision, noopScope.expected_board_load_revision);
    assert.deepEqual(await feederCounts(), startingFeeds, 'All preparation, failed plans, flips, edits and inspection consume no native material.');
    await operation('set_machine_enabled', { enabled: true }); await operation('home_machine'); assert.equal((await operation('validate_job')).valid, true);
    const complete = await operation('start_job', { job_id: prepared.job_id }); assert.equal(complete.placed, 2);
    const finishedFeeds = await feederCounts(); const feedDelta = Object.keys(startingFeeds).reduce((sum, id) => { assert.ok(finishedFeeds[id] >= startingFeeds[id]); return sum + finishedFeeds[id] - startingFeeds[id]; }, 0); assert.equal(feedDelta, 2);
    evidence.completed_native_placements = 2; evidence.native_feed_delta = feedDelta;
    const placedInspection = await inspect(); assert.equal(select(placedInspection.records, 'P1⇒A', 'R1').placed, true); assert.equal(select(placedInspection.records, 'B', 'R2').placed, true);
    const placedFailure = await operation('plan_placement_edits', { ...await scope(), changes: [edit('P1⇒A', 'R1', { enabled: false })] }, 'failed'); assert.equal(placedFailure.code, 'PLACED_HISTORY_CONFLICT');
    const saved = await operation('save_job'); const archive = await call('get_native_artifact', { artifact_id: saved.artifact.artifact_id }); assert.equal(sha(Buffer.from(archive.base64, 'base64')), saved.artifact.sha256);
    const reloaded = await operation('load_job', { artifact_id: saved.artifact.artifact_id }); assert.equal(reloaded.job.placed, 2); assert.equal(reloaded.registration, 'invalidated');
    assert.deepEqual((await inspect()).records, placedInspection.records, 'Native save/reload preserves all edited/shared/disabled/opposite-side records and actual history.');
    const unbound = await operation('validate_job', {}, 'failed'); assert.equal(unbound.code, 'BOARD_LOAD_REQUIRED');
    for (const item of (await call('get_board_loads')).roots) await loadAction(item.root_instance_id, 'same-load', item.side, item.load_id);
    assert.equal((await operation('validate_job')).valid, true);
    const b = (await call('get_board_loads')).roots.find(item => item.root_instance_id === 'B'); const beforeFlipRevision = (await scope()).expected_job_revision;
    await loadAction('B', 'flip', 'top', b.load_id); const flipped = await inspect();
    assert.equal(select(flipped.records, 'B', 'R2').placed, true); assert.equal(select(flipped.records, 'B', 'R1').placed, false); assert.equal(select(flipped.records, 'B', 'R1').ordinary_active_side_placement, true);
    assert.equal((await scope()).expected_job_revision, beforeFlipRevision, 'Facing-side changes use board_load_revision.');
    await loadAction('B', 'flip', 'bottom', b.load_id); assert.deepEqual(await feederCounts(), finishedFeeds, 'Rejected edits, save/reload, presence confirmation and flips cannot replay feeds.');
    await operation('set_machine_enabled', { enabled: false });
    assert.deepEqual(await sourceHashes(), beforeHashes, 'MCP source bytes must remain fixed for this result.');
    evidence.passed = true;
  } catch (error) { evidence.passed = false; evidence.failure = { code: error.code, message: error.message }; throw error; }
  finally {
    evidence.finished_at = new Date().toISOString();
    if (process.env.OPENPNP_JOB_EDIT_EVIDENCE_DIR) {
      await mkdir(process.env.OPENPNP_JOB_EDIT_EVIDENCE_DIR, { recursive: true });
      await writeFile(path.join(process.env.OPENPNP_JOB_EDIT_EVIDENCE_DIR, 'mcp-placement-loads.json'), JSON.stringify(evidence, (key, value) => key === 'configuration_root' ? '<isolated-native-simulator-configuration>' : value, 2) + '\n');
    }
  }
});
