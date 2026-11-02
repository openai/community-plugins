import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, rm, lstat, symlink, link, truncate, readdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import os from 'node:os';
import path from 'node:path';
import { exportSupportBundle, SUPPORT_LIMITS } from '../../plugins/openpnp/scripts/support-export.mjs';

// Synthetic installer/journal fixtures. These tests never start Java or contact a bridge.
const op = '11111111-1111-4111-8111-111111111111';
const bridge = '22222222-2222-4222-8222-222222222222';
const request = '33333333-3333-4333-8333-333333333333';
const imageId = '44444444-4444-4444-8444-444444444444';
const zipId = '55555555-5555-4555-8555-555555555555';
const hash = b => createHash('sha256').update(b).digest('hex');
const event = (sequence, type, payload) => JSON.stringify({ sequence, type, payload, bridge_instance_id: bridge, occurred_at: '2026-09-11T00:00:00Z' }) + '\n';
const operation = (sequence, state, extra = {}) => event(sequence, 'operation', { operation_id: op, request_id: request,
  method: 'openpnp_start_job', state, config_revision: 'cfg-12', ...extra });
async function fixture(t, journal = operation(1, 'accepted')) {
  const raw = await mkdtemp(path.join(os.tmpdir(), 'openpnp-support-test-')); // Canonicalize /var on macOS like installed launchers.
  const { realpath } = await import('node:fs/promises'); const root = await realpath(raw);
  t.after(() => rm(root, { recursive: true, force: true }));
  const state = path.join(root, 'state'), jar = path.join(state, 'bridge/0.1.0/openpnp-codex-bridge.jar');
  await mkdir(path.dirname(jar), { recursive: true }); await mkdir(path.join(state, 'journal'));
  const fakeJar = Buffer.from('synthetic installer fixture, not executable native qualification');
  await writeFile(jar, fakeJar);
  const manifest = { upstream_commit: '5bd404cfc70f34103a3ca0fbb6b50c2b465f407c', bridge_version: '0.1.0', bridge_sha256: hash(fakeJar),
    runtime_manifest_sha256: 'a'.repeat(64), patched_native_jar_sha256: 'b'.repeat(64), bootstrap_sha256: 'c'.repeat(64),
    free_text: 'PRIVATE_MANIFEST_VALUE', production_source_sha256: { '/Users/private/name.java': 'd'.repeat(64) } };
  const installation = { schema_version: 1, version: '0.1.0', bridge_jar: jar, upstream_commit: manifest.upstream_commit, bridge_sha256: manifest.bridge_sha256 };
  await writeFile(path.join(path.dirname(jar), 'build-manifest.json'), JSON.stringify(manifest));
  await writeFile(path.join(state, 'installation.json'), JSON.stringify(installation));
  await writeFile(path.join(state, 'journal/machine-id'), bridge + '\n');
  await writeFile(path.join(state, 'journal/operations.jsonl'), journal);
  return { root, state, jar, installation, manifest, journal, output: path.join(root, 'support.tar') };
}
// Independent minimal USTAR reader checks header checksum, paths, padding and exact payload sizes.
function unpack(data) {
  const entries = new Map(); let cursor = 0;
  while (cursor < data.length - 1024) {
    const header = data.subarray(cursor, cursor + 512); assert.equal(header.toString('ascii', 257, 262), 'ustar');
    const stored = parseInt(header.toString('ascii', 148, 154), 8); const copy = Buffer.from(header); copy.fill(32, 148, 156);
    assert.equal(copy.reduce((a, b) => a + b, 0), stored);
    const name = header.subarray(0, 100).toString('utf8').replace(/\0.*$/s, '');
    assert.ok(!name.startsWith('/') && !name.split('/').includes('..')); assert.ok(!entries.has(name));
    assert.equal(header[156], 48); assert.equal(parseInt(header.toString('ascii', 100, 107), 8), 0o600);
    const size = parseInt(header.toString('ascii', 124, 135), 8); cursor += 512;
    entries.set(name, data.subarray(cursor, cursor + size));
    const padding = (512 - size % 512) % 512; assert.ok(data.subarray(cursor + size, cursor + size + padding).every(v => v === 0));
    cursor += size + padding;
  }
  assert.equal(cursor, data.length - 1024); assert.ok(data.subarray(cursor).every(v => v === 0)); return entries;
}
function records(entries) { const data = entries.get('operations.jsonl').toString(); return data.trim() ? data.trimEnd().split('\n').map(JSON.parse) : []; }
async function bundle(f, options = {}) {
  const receipt = await exportSupportBundle({ stateDir: f.state, output: f.output, operationIds: [op], ...options });
  const data = await readFile(f.output); assert.equal(receipt.sha256, hash(data)); assert.equal(receipt.size, data.length);
  const entries = unpack(data), manifest = JSON.parse(entries.get('manifest.json'));
  for (const entry of manifest.entries) { assert.equal(hash(entries.get(entry.name)), entry.sha256); assert.equal(entries.get(entry.name).length, entry.bytes); }
  return { receipt, data, entries, manifest, selection: JSON.parse(entries.get('selection.json')) };
}

test('selective export verifies bytes, keeps complete transitions, excludes secrets and never opens unselected paths', async t => {
  const secret = 'Bearer PRIVATE_SECRET_DO_NOT_EXPORT';
  const journal = operation(1, 'accepted', { session_id: secret, request_digest: hash(secret), [secret]: secret }) +
    operation(2, 'running') + operation(3, 'outcome_unknown', { native_steps_started: 7, native_effect_pending: true,
      result: { physical_outcome: 'unknown', repeat_action_performed: false, code: 'NATIVE_ACTION_OUTCOME_UNKNOWN',
        completion: { standstill_confirmed: false, barrier_error: secret }, error: { code: 'OUTCOME_UNKNOWN', message: secret },
        context: { part_id: 'private-customer-part', board_instance_id: 'private-board', session_id: secret },
        message: secret, base64: Buffer.from(secret).toString('base64'), configuration: { password: secret } } });
  const f = await fixture(t, journal);
  // Broken links would fail any attempt to read credential/config/script/unselected artifact paths.
  for (const name of ['connection.json', 'bridge.token', 'scripts', 'simulator-configs', 'responses']) await symlink('/unselected-do-not-open', path.join(f.state, name));
  await symlink('/unselected-do-not-open', path.join(f.state, `journal/${zipId}.artifact`));
  const result = await bundle(f); const text = result.data.toString('utf8');
  for (const excluded of [secret, 'private-customer-part', 'private-board', 'PRIVATE_MANIFEST_VALUE', '/Users/private/name.java', f.state]) assert.ok(!text.includes(excluded), excluded);
  const rows = records(result.entries); assert.equal(rows.length, 3); assert.equal(rows[2].facts.state, 'outcome_unknown');
  assert.equal(rows[2].facts.native_effect_pending, true); assert.equal(rows[2].facts.result.repeat_action_performed, false);
  assert.equal(rows[2].facts.result.context.part_id_sha256, hash('private-customer-part'));
  assert.equal(rows[2].facts.result.error.code, 'OUTCOME_UNKNOWN'); assert.ok(rows[2].redaction.omitted_fields > 0);
  assert.equal(rows[2].facts.result.code, 'NATIVE_ACTION_OUTCOME_UNKNOWN'); assert.equal(rows[2].facts.result.completion.standstill_confirmed, false);
  assert.deepEqual(rows.map(row => row.source_line_sha256), journal.trimEnd().split('\n').map(hash));
  assert.equal(result.selection.selected_records_omitted, 0); assert.equal(result.selection.raw_selected_records_complete, false);
  assert.equal(result.selection.unresolved_operation_inventory[0].state, 'outcome_unknown');
  assert.equal(result.receipt.native_state_modified, false); assert.equal(result.receipt.network_used, false);
  assert.equal(JSON.parse(result.entries.get('provenance.json')).native_runtime_files_verified, false);
  assert.equal(await readFile(path.join(f.state, 'journal/operations.jsonl'), 'utf8'), journal);
  if (process.platform !== 'win32') assert.equal((await lstat(f.output)).mode & 0o777, 0o600);
});

test('over 100 selected hook records and all unresolved arrays survive without row truncation', async t => {
  const lines = [operation(1, 'accepted')];
  for (let i = 1; i <= 1200; i++) lines.push(event(i + 1, 'native_action_intent', { operation_id: op, action_id: `${op}/native-action-${i}`,
    event_id: `${op}/native-ledger-${i}`, kind: 'pick', state: 'intent', native_call: 'processor-next', physical_outcome: 'unknown', context: { placement_id: `R${i}` } }));
  lines.push(operation(1202, 'outcome_unknown', { native_action_recovery: { native_hook_outcomes: 0, requires_reconciliation: true,
    unresolved_actions: Array.from({ length: 1200 }, (_, i) => ({ action_id: `${op}/native-action-${i + 1}`, state: 'outcome_unknown', reason: 'journal-ended-before-matching-durable-after-hook' })) } }));
  const f = await fixture(t, lines.join('')); const result = await bundle(f); const rows = records(result.entries);
  assert.equal(rows.length, 1202); assert.equal(rows.at(-1).facts.native_action_recovery.unresolved_actions.length, 1200);
  assert.equal(rows[1].facts.native_call, 'processor-next');
  assert.equal(rows.at(-1).facts.native_action_recovery.unresolved_actions.at(-1).action_id, `${op}/native-action-1200`);
  assert.equal(result.selection.selected_records_omitted, 0); assert.equal(result.selection.event_counts.native_action_intent, 1200);
});

test('corrupt/partial/unknown history stays explicit, in source order, with every anomaly', async t => {
  const journal = operation(1, 'accepted') + event(3, 'private-secret-event-type', { operation_id: op, state: 'outcome_unknown', private_secret: 'never-copy' }) +
    operation(2, 'succeeded') + Array.from({ length: 130 }, () => '{bad}\n').join('') + operation(4, 'succeeded').trimEnd();
  const f = await fixture(t, journal); const result = await bundle(f); const rows = records(result.entries);
  assert.equal(result.receipt.source_structurally_complete, false); assert.equal(rows.length, 3);
  assert.deepEqual(rows.map(r => r.sequence), [1, 3, 2]); assert.equal(rows[1].type, 'unrecognized-event-type');
  assert.equal(rows[1].facts.state, 'outcome_unknown'); assert.equal(result.selection.unrecognized_records, 1);
  assert.equal(result.entries.get('anomalies.jsonl').toString().trim().split('\n').length, 133);
  assert.ok(!result.data.includes(Buffer.from('private-secret'))); assert.ok(!result.data.includes(Buffer.from('never-copy')));
  assert.equal(JSON.parse(result.entries.get('diagnostics.json')).source.sha256, hash(journal));
});

test('unknown operation states remain unresolved and unselected/unlinked facts are not attributed', async t => {
  const journal = operation(1, 'vendor-private-state') + event(2, 'feed_intent', { feeder_id: 'customer-secret' }) +
    event(3, 'operation', { operation_id: zipId, method: 'openpnp_pick', state: 'paused' });
  const f = await fixture(t, journal); const result = await bundle(f);
  assert.equal(result.selection.unresolved_operation_inventory.length, 2); assert.equal(records(result.entries).length, 1);
  assert.equal(result.selection.unresolved_operation_inventory[0].state, 'unrecognized');
  assert.ok(!result.data.toString().includes('customer-secret')); assert.ok(!result.data.toString().includes('vendor-private-state'));
});

async function addArtifact(f, id, data, mime, metadata) {
  await writeFile(path.join(f.state, `journal/${id}.artifact`), data);
  await writeFile(path.join(f.state, `journal/${id}.metadata.json`), JSON.stringify({ artifact_id: id, sha256: hash(data), size: data.length, mime_type: mime, metadata }));
}
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jg5cAAAAASUVORK5CYII=', 'base64');
test('raw image and board-document selection requires exact IDs/hashes and preserves exact opaque bytes', async t => {
  const f = await fixture(t); const zip = Buffer.from([80,75,3,4,1,2,3,4]); // Opaque signature fixture, not native archive qualification.
  await addArtifact(f, imageId, png, 'image/png', { camera_id: 'Camera-private-name', credential: 'secret-artifact-metadata' });
  await addArtifact(f, zipId, zip, 'application/zip', { kind: 'native-job-document', private_name: 'private-design-name' });
  const result = await bundle(f, { artifactSelections: [{ artifact_id: imageId, sha256: hash(png), kind: 'camera' }, { artifact_id: zipId, sha256: hash(zip), kind: 'job-document' }] });
  assert.deepEqual(result.entries.get(`artifacts/${imageId}.png`), png); assert.deepEqual(result.entries.get(`artifacts/${zipId}.zip`), zip);
  assert.equal(result.manifest.artifacts.length, 2); assert.equal(result.manifest.artifacts[1].content_redacted, false);
  assert.ok(!result.data.toString().includes('secret-artifact-metadata')); assert.ok(!result.data.toString().includes('private-design-name'));
});

test('artifact scope, tampering and expected hash failures never publish partial archives', async t => {
  const f = await fixture(t); const selection = { artifact_id: imageId, sha256: hash(png), kind: 'camera' };
  await addArtifact(f, imageId, png, 'application/json', { kind: 'configuration-backup' });
  await assert.rejects(exportSupportBundle({ stateDir: f.state, output: f.output, artifactSelections: [selection] }), { code: 'SUPPORT_ARTIFACT_SCOPE' });
  await addArtifact(f, imageId, png, 'image/png', { camera_id: 'C1' });
  await writeFile(path.join(f.state, `journal/${imageId}.artifact`), Buffer.from('tampered'));
  await assert.rejects(exportSupportBundle({ stateDir: f.state, output: f.output, artifactSelections: [selection] }), { code: 'SUPPORT_ARTIFACT_HASH' });
  await assert.rejects(exportSupportBundle({ stateDir: f.state, output: f.output, artifactSelections: [{ ...selection, sha256: 'a'.repeat(64) }] }), { code: 'SUPPORT_ARTIFACT' });
  await assert.rejects(lstat(f.output), { code: 'ENOENT' });
});

test('selection rejects unknown fields, path-like identifiers, duplicates, missing operations and limits', async t => {
  const f = await fixture(t); const base = { stateDir: f.state, output: f.output };
  for (const changes of [{ operationIds: [op, op] }, { operationIds: ['../../bridge.token'] }, { operationIds: Array(101).fill(op) },
    { artifactSelections: [{ artifact_id: imageId, sha256: 'a'.repeat(64), kind: 'configuration' }] }, { tokenFile: '/secret' },
    { artifactSelections: [{ artifact_id: imageId, sha256: 'a'.repeat(64), kind: 'camera', path: '/secret' }] }])
    await assert.rejects(exportSupportBundle({ ...base, ...changes }), { code: 'SUPPORT_SELECTION' });
  await assert.rejects(exportSupportBundle({ ...base, operationIds: [zipId] }), { code: 'SUPPORT_OPERATION_NOT_FOUND' });
  await assert.rejects(lstat(f.output), { code: 'ENOENT' });
});

test('installed provenance rejects mismatched JAR bytes and arbitrary receipt paths', async t => {
  const f = await fixture(t); await writeFile(f.jar, 'tampered-jar');
  await assert.rejects(exportSupportBundle({ stateDir: f.state, output: f.output }), { code: 'SUPPORT_INSTALLATION' });
  await writeFile(path.join(f.state, 'installation.json'), JSON.stringify({ ...f.installation, bridge_jar: '/arbitrary/external.jar' }));
  await assert.rejects(exportSupportBundle({ stateDir: f.state, output: f.output }), { code: 'SUPPORT_INSTALLATION' });
  await assert.rejects(lstat(f.output), { code: 'ENOENT' });
});

test('journal root/intermediate/file symlinks and hardlinks fail before publication', async t => {
  const f = await fixture(t); const alias = path.join(f.root, 'state-link'); await symlink(f.state, alias);
  await assert.rejects(exportSupportBundle({ stateDir: alias, output: f.output }), { code: 'SUPPORT_PATH' });
  const journal = path.join(f.state, 'journal/operations.jsonl'); const external = path.join(f.root, 'external'); await writeFile(external, f.journal);
  await rm(journal); await symlink(external, journal);
  await assert.rejects(exportSupportBundle({ stateDir: f.state, output: f.output }), { code: 'SUPPORT_INPUT' });
  await rm(journal); await link(external, journal);
  await assert.rejects(exportSupportBundle({ stateDir: f.state, output: f.output }), { code: 'SUPPORT_INPUT' });
  await rm(path.join(f.state, 'journal'), { recursive: true }); await symlink(f.root, path.join(f.state, 'journal'));
  await assert.rejects(exportSupportBundle({ stateDir: f.state, output: f.output }), { code: 'SUPPORT_PATH' });
  await assert.rejects(lstat(f.output), { code: 'ENOENT' });
});

test('output inside state, aliases and existing output cannot replace history or prior evidence', async t => {
  const f = await fixture(t); const alias = path.join(f.root, 'journal-link'); await symlink(path.join(f.state, 'journal'), alias);
  for (const output of [path.join(f.state, 'report.tar'), path.join(alias, 'report.tar')])
    await assert.rejects(exportSupportBundle({ stateDir: f.state, output }), { code: 'SUPPORT_PATH' });
  const first = await bundle(f); await assert.rejects(exportSupportBundle({ stateDir: f.state, output: f.output }), { code: 'SUPPORT_OUTPUT_EXISTS' });
  assert.deepEqual(await readFile(f.output), first.data); assert.deepEqual((await readdir(f.root)).filter(v => v.endsWith('.tmp')), []);
});

test('bounded source records and sparse oversized artifacts reject without partial output', async t => {
  const f = await fixture(t, operation(1, 'accepted') + JSON.stringify({ value: 'a'.repeat(16 * 1024 * 1024) }) + '\n');
  await assert.rejects(exportSupportBundle({ stateDir: f.state, output: f.output }), /16 MiB/);
  await writeFile(path.join(f.state, 'journal/operations.jsonl'), operation(1, 'accepted'));
  await addArtifact(f, imageId, png, 'image/png', { camera_id: 'C1' });
  await truncate(path.join(f.state, `journal/${imageId}.artifact`), SUPPORT_LIMITS.artifact_bytes + 1);
  await assert.rejects(exportSupportBundle({ stateDir: f.state, output: f.output, artifactSelections: [{ artifact_id: imageId, sha256: hash(png), kind: 'camera' }] }), { code: 'SUPPORT_INPUT' });
  await assert.rejects(lstat(f.output), { code: 'ENOENT' });
});

test('aggregate archive capacity never truncates selected artifacts or leaves output', async t => {
  const f = await fixture(t); const payload = Buffer.alloc(SUPPORT_LIMITS.artifact_bytes); png.copy(payload); const selections = [];
  for (let i = 0; i < 8; i++) {
    const id = `66666666-6666-4666-8666-${String(i).padStart(12, '0')}`;
    await addArtifact(f, id, payload, 'image/png', { camera_id: 'C1' }); selections.push({ artifact_id: id, sha256: hash(payload), kind: 'camera' });
  }
  await assert.rejects(exportSupportBundle({ stateDir: f.state, output: f.output, artifactSelections: selections }), { code: 'SUPPORT_CAPACITY' });
  await assert.rejects(lstat(f.output), { code: 'ENOENT' }); assert.deepEqual((await readdir(f.root)).filter(v => v.endsWith('.tmp')), []);
});
