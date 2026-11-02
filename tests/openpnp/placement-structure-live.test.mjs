import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID, createHash } from 'node:crypto';
import { mkdtemp, rm, readFile, writeFile, mkdir } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { Client } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { StdioClientTransport } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/stdio.js';
import { readCompleteResponse } from '../../scripts/openpnp-read-response.mjs';
import { digest } from '../../plugins/openpnp/mcp/domain/index.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const connectionFile = process.env.OPENPNP_STRUCTURE_E2E_CONNECTION_FILE;
const serverPath = path.join(root, 'plugins/openpnp/mcp/server.mjs');
const sha = bytes => createHash('sha256').update(bytes).digest('hex');

test('actual packaged MCP adds/removes shared native placements and preserves unused lineage across documents', { skip: !connectionFile, timeout: 300000 }, async t => {
  const state = await mkdtemp(path.join(os.tmpdir(), 'openpnp-structure-mcp-'));
  t.after(() => rm(state, { recursive: true, force: true }));
  const inputs = ['plugins/openpnp/mcp/server.mjs', 'plugins/openpnp/mcp/tool-inputs.json', 'plugins/openpnp/bridge/openpnp-codex-bridge.jar', 'tests/openpnp/placement-structure-live.test.mjs'];
  const hashes = async () => Object.fromEntries(await Promise.all(inputs.map(async name => [name, sha(await readFile(path.join(root, name)))])));
  const before = await hashes();
  const evidence = { started_at: new Date().toISOString(), inputs: before, checks: [], physical_qualification: false, independently_inspected: 0,
    scope: 'Official MCP SDK -> packaged server -> real pinned native simulator. Structure, document and lineage guards; one native step then explicit abort. No physical qualification.' };
  const client = new Client({ name: 'native-structure-lineage-e2e', version: '1' });
  const transport = new StdioClientTransport({ command: process.execPath, args: [serverPath, '--stdio'], cwd: state,
    env: { PATH: process.env.PATH ?? '', OPENPNP_STATE_DIR: state, OPENPNP_CONNECTION_FILE: connectionFile }, stderr: 'pipe' });
  let session;
  t.after(async () => { if (session) try { await client.callTool({ name: 'openpnp_release_control_session', arguments: { session_id: session } }); } catch {} await client.close(); });
  await client.connect(transport);
  async function call(name, args = {}) {
    const response = await client.callTool({ name: `openpnp_${name}`, arguments: args });
    const value = await readCompleteResponse(response.structuredContent, page => call('read_response_page', page));
    if (response.isError) throw Object.assign(new Error(value.error.message), value.error); return value;
  }
  async function operation(name, args = {}, expected = 'succeeded') {
    const request_id = args.request_id ?? randomUUID(), handle = await call(name, { session_id: session, request_id, ...args });
    const deadline = Date.now() + 120000; let latest = handle;
    while (['accepted', 'running'].includes(latest.state)) {
      assert.ok(Date.now() < deadline, 'Timed out observing original operation; no retry.');
      await delay(25); latest = await call('get_operation', { operation_id: handle.operation_id, view: 'progress' });
    }
    while ((await call('get_status', { view: 'progress' })).native_busy) { assert.ok(Date.now() < deadline, 'Native executor did not release.'); await delay(25); }
    const done = await call('get_operation', { operation_id: handle.operation_id, view: 'full' });
    evidence.checks.push({ method: name, request_id, operation_id: done.operation_id, state: done.state, result: done.result });
    assert.equal(done.state, expected, JSON.stringify(done)); return done;
  }
  async function scope() {
    const s = await call('get_status'); return { job_id: s.job_id, expected_config_revision: s.config_revision, expected_job_revision: s.job_revision, expected_board_load_revision: s.board_load_revision };
  }
  async function inspect() {
    const bound = await scope(); let offset = 0, fingerprint; const records = [];
    while (true) {
      const page = (await operation('inspect_job', { ...bound, offset, limit: 2, ...(fingerprint ? { expected_source_fingerprint: fingerprint } : {}) })).result;
      fingerprint ??= page.source_fingerprint; assert.equal(page.source_fingerprint, fingerprint);
      records.push(...page.records); if (page.eof) { assert.equal(records.length, page.total_records); return records; } offset = page.next_offset;
    }
  }
  const feeds = async () => Object.fromEntries((await call('get_configuration')).settings.feeders.map(f => [f.feeder_id, f.feed_count]));
  const select = (records, holder, reference) => records.filter(r => r.holder_instance_id === holder && r.placement_id === reference);
  const added = () => ({ action: 'add', scope: 'job_shared_definition', holder_instance_id: 'P1⇒A', placement_id: 'R3', placement: {
    location: { frame: 'holder', units: 'mm', x: 8, y: 9, z: 0, rotation: 30 }, side: 'Top', type: 'Placement', part_id: 'R0805-1K', enabled: false, error_handling: 'Default', comments: 'explicit shared addition', rank: 0,
  } });
  async function edit(changes) {
    const bound = await scope(); const staged = (await operation('plan_placement_structure', { ...bound, changes })).result;
    const request_id = randomUUID(), applied = await operation('apply_placement_structure', { ...bound, plan_id: staged.plan_id, request_id });
    const duplicate = await call('apply_placement_structure', { ...bound, session_id: session, plan_id: staged.plan_id, request_id });
    assert.equal(duplicate.operation_id, applied.operation_id); return { staged, applied };
  }
  try {
    const caps = await call('get_capabilities'); assert.equal(caps.connected, true); assert.equal(caps.bridge.simulation, true);
    for (const name of ['plan_placement_structure', 'apply_placement_structure', 'step_job']) assert.ok(caps.tools.includes(`openpnp_${name}`));
    assert.equal(caps.bridge.upstream_commit, '5bd404cfc70f34103a3ca0fbb6b50c2b465f407c'); evidence.bridge = caps.bridge;
    const initial = await call('get_status'); assert.equal(initial.job_state, 'absent'); assert.equal((await call('get_configuration')).enabled, false);
    session = (await call('request_control_session', { request_id: randomUUID(), ttl_seconds: 600 })).session_id;
    const startingFeeds = await feeds(); evidence.starting_feeds = startingFeeds;
    const part = (await call('get_configuration')).parts.find(p => p.id === 'R0805-1K'); assert.ok(part?.height_mm > 0);
    const imported = await call('import_job', { format: 'reference-csv', content: `Ref,Val,Package,PosX,PosY,Rot,Side,PartId,Height\nR1,1K,${part.package_id},15,15,0,top,${part.id},${part.height_mm}\nR2,1K,${part.package_id},20,15,0,bottom,${part.id},${part.height_mm}\n`, units: 'mm', widthMm: 40, heightMm: 30 });
    const canonical = (await call('get_artifact', { artifact_id: imported.artifact_id })).content, board = canonical.boards[0].id;
    const instance = (id, kind, definitionId, x, y, side = 'top', enabled = true) => ({ id, kind, definitionId, x, y, z: 0, rotation: 0, side, enabled });
    canonical.panels = [{ id: 'panel', widthMm: 100, heightMm: 40, children: [instance('A', 'board', board, 0, 0), instance('X', 'board', board, 50, 0, 'top', false)] }];
    canonical.instances = [instance('P1', 'panel', 'panel', 100, 100), instance('B', 'board', board, 100, 150, 'bottom')];
    const { revision, ...body } = canonical; canonical.revision = digest(body);
    const nativeInput = await call('import_job', { format: 'canonical-json', content: JSON.stringify(canonical) });
    await operation('prepare_job', { artifact_id: nativeInput.artifact_id }); const original = await inspect(); assert.equal(original.length, 6);
    const oldDocument = (await operation('save_job')).result;
    const addition = await edit([added()]); assert.equal(addition.staged.plan.effects.changes[0].affected.length, 3);
    const expanded = await inspect(); assert.equal(expanded.length, 9);
    for (const holder of ['P1⇒A', 'P1⇒X', 'B']) {
      const rows = select(expanded, holder, 'R3'); assert.equal(rows.length, 1); assert.equal(rows[0].location.x, 8); assert.equal(rows[0].enabled, false);
    }
    await edit([{ action: 'remove', scope: 'job_instance', holder_instance_id: 'P1⇒X', placement_id: 'R1' }]);
    const edited = await inspect(); assert.equal(edited.length, 8); assert.equal(select(edited, 'P1⇒X', 'R1').length, 0); assert.equal(select(edited, 'P1⇒A', 'R1').length, 1);
    const reused = added(); reused.scope = 'job_instance'; reused.holder_instance_id = 'P1⇒X'; reused.placement_id = 'R1';
    assert.equal((await operation('plan_placement_structure', { ...await scope(), changes: [reused] }, 'failed')).result.code, 'STRUCTURE_ID_REUSED');
    const currentDocument = (await operation('save_job')).result;
    assert.notEqual(currentDocument.artifact.sha256, oldDocument.artifact.sha256);
    await operation('load_job', { artifact_id: oldDocument.artifact.artifact_id });
    assert.equal((await operation('plan_placement_structure', { ...await scope(), changes: [added()] }, 'failed')).result.code, 'LINEAGE_DOCUMENT_STALE');
    await operation('load_job', { artifact_id: currentDocument.artifact.artifact_id }); assert.deepEqual(await inspect(), edited);
    const rebound = await call('get_board_loads');
    for (const item of rebound.roots) {
      const bound = await scope(); delete bound.expected_job_revision;
      await operation('register_board_load', { ...bound, root_instance_id: item.root_instance_id, action: 'replace', side: item.side, expected_load_id: item.load_id });
    }
    assert.deepEqual(await feeds(), startingFeeds, 'Model/document/binding actions consume no native feed.');
    const beforeRunDocument = (await operation('save_job')).result;
    await operation('set_machine_enabled', { enabled: true }); await operation('home_machine');
    assert.equal((await operation('validate_job')).result.valid, true);
    const bound = await scope(); const stepped = await operation('step_job', { job_id: bound.job_id, expected_config_revision: bound.expected_config_revision }, 'paused');
    assert.equal(stepped.result.placed, 0, 'First native next call must have no placement in this pinned fixture.');
    await operation('abort_job', { operation_id: stepped.operation_id, expected_config_revision: bound.expected_config_revision }, 'aborted');
    await operation('set_machine_enabled', { enabled: false });
    await operation('load_job', { artifact_id: beforeRunDocument.artifact.artifact_id });
    const afterAdmission = await operation('plan_placement_structure', { ...await scope(), changes: [{ action: 'remove', scope: 'job_instance', holder_instance_id: 'P1⇒A', placement_id: 'R1' }] }, 'failed');
    assert.equal(afterAdmission.result.code, 'LINEAGE_EXECUTED');
    assert.deepEqual(await feeds(), startingFeeds); evidence.native_feed_delta = 0; evidence.native_placements = 0;
    assert.deepEqual(await hashes(), before); evidence.passed = true;
  } catch (error) { evidence.passed = false; evidence.failure = { code: error.code, message: error.message }; throw error; }
  finally {
    evidence.finished_at = new Date().toISOString();
    if (process.env.OPENPNP_STRUCTURE_E2E_EVIDENCE_DIR) {
      await mkdir(process.env.OPENPNP_STRUCTURE_E2E_EVIDENCE_DIR, { recursive: true });
      await writeFile(path.join(process.env.OPENPNP_STRUCTURE_E2E_EVIDENCE_DIR, 'mcp-native-placement-structure.json'), JSON.stringify(evidence, null, 2) + '\n');
    }
  }
});
