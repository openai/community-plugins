// SPDX-License-Identifier: Apache-2.0
// Actual Node CLI and installed Bridge reducer; original native journals remain read-only.
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync } from 'node:fs';
import { chmod, cp, mkdir, mkdtemp, readFile, readdir, stat, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const plugin = path.join(repository, 'plugins/openpnp'), cli = path.join(plugin, 'scripts/openpnp.mjs');
const retained = process.env.OPENPNP_HISTORY_REAL_FIXTURES || path.join(repository, 'validation/explicit-retained-history-required');
const runtime = process.env.OPENPNP_HISTORY_RUNTIME || path.join(repository, 'validation/explicit-history-runtime-required');
const java = process.env.OPENPNP_HISTORY_JAVA || '';
const crash = path.join(retained, 'native-regression-corrected03/tmp/openpnp-controller-crash-8872944327462146736');
const fixtures = [
  { name: 'official-success', file: path.join(retained, 'controller-official-03/native-state/journal/operations.jsonl'), bytes: 17724, sha256: '31c9d7acf61f298d3f52c00d4f845c60edf70d646ffe0d545db5ec809f2fe81d', state: 'succeeded' },
  { name: 'connect-halt', file: path.join(crash, 'connect-intent-force/producer/journal/operations.jsonl'), bytes: 6551, sha256: 'adf89c45a84f01e8a305ace453e04624f4d835e09f6cb497b3cb0e2a725dac0d', state: 'running' },
  { name: 'connect-recovered', file: path.join(crash, 'connect-intent-force/recovery-state/journal/operations.jsonl'), bytes: 10612, sha256: 'bac22043d3f96663fd3e0fb7c9b07fcfbdcc3f8b3af2c31058c7116261f456a2', state: 'outcome_unknown' },
];
const available = [java, path.join(runtime, 'codex-build-manifest.json'), ...fixtures.map(f => f.file)].every(existsSync);
const skip = available ? false : 'Set OPENPNP_HISTORY_JAVA, OPENPNP_HISTORY_RUNTIME and OPENPNP_HISTORY_REAL_FIXTURES to frozen recovery56/retained native inputs.';
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
// Bind the inspector to actual selected package bytes; retained journal fixture hashes stay pinned.
const BRIDGE = sha(await readFile(path.join(plugin, 'bridge/openpnp-codex-bridge.jar')));
const REDUCER = '8a4955256b3ae7f72e6e9aaa1629b3460b68cb4ca8b49fc06c5fdf81c3d40418';
const DTO = 'f421e9162db4bdb979ac3d24141ec8fb4b0cb170a8b644b7f0d746efc4a36a23';
const sourceFiles = [
  fileURLToPath(import.meta.url), cli, path.join(plugin, 'scripts/controller-history.mjs'),
  path.join(plugin, 'controller-history/build-manifest.json'), path.join(plugin, 'controller-history/openpnp-controller-history.jar'),
  path.join(plugin, 'controller-history/source/org/openpnp/codex/ControllerHistoryMain.java'), path.join(plugin, 'controller-history/corresponding-source.zip'),
  path.join(plugin, 'bridge/build-manifest.json'), path.join(plugin, 'bridge/openpnp-codex-bridge.jar'),
  path.join(repository, 'src/openpnp/controller-history/org/openpnp/codex/ControllerHistoryMain.java'),
  path.join(repository, 'src/openpnp/java/org/openpnp/codex/NativeControllerJournal.java'), path.join(repository, 'src/openpnp/java/org/openpnp/codex/NativeJournalJson.java'),
  path.join(runtime, 'codex-build-manifest.json'), path.join(runtime, 'lib/gson-2.2.3.jar'), java,
];
async function fingerprint(file) {
  const before = await stat(file), bytes = await readFile(file), after = await stat(file);
  assert.equal(before.size, after.size); assert.equal(before.mtimeMs, after.mtimeMs);
  return { bytes: bytes.length, sha256: sha(bytes), mtime_ms: after.mtimeMs, inode: after.ino };
}
async function fingerprints(files) { return Object.fromEntries(await Promise.all(files.map(async f => [f, await fingerprint(f)]))); }
async function tree(root) {
  const result = {};
  async function walk(dir) {
    for (const entry of await readdir(dir, { withFileTypes: true })) {
      const p = path.join(dir, entry.name);
      if (entry.isDirectory()) await walk(p);
      else { assert.ok(entry.isFile(), 'Owned installed state must contain regular files'); result[path.relative(root, p)] = await fingerprint(p); }
    }
  }
  await walk(root); return result;
}
async function execute(root, label, args, entrypoint = cli) {
  const started = Date.now(), child = spawn(process.execPath, [entrypoint, ...args], { shell: false, stdio: ['ignore', 'pipe', 'pipe'] });
  let out = Buffer.alloc(0), err = Buffer.alloc(0), exceeded = false, spawnError;
  const timer = setTimeout(() => child.kill('SIGTERM'), 30000), lastResort = setTimeout(() => child.kill('SIGKILL'), 32000);
  child.on('error', e => { spawnError = e.code || 'SPAWN_ERROR'; });
  child.stdout.on('data', b => { if (out.length + b.length <= 1048576) out = Buffer.concat([out, b]); else { exceeded = true; child.kill('SIGTERM'); } });
  child.stderr.on('data', b => { if (err.length + b.length <= 65536) err = Buffer.concat([err, b]); else { exceeded = true; child.kill('SIGTERM'); } });
  const ended = await new Promise(resolve => child.once('close', (code, signal) => resolve({ code, signal })));
  clearTimeout(timer); clearTimeout(lastResort);
  const receipt = { label, ...ended, pid: child.pid, elapsed_ms: Date.now() - started, stdout: out.toString('utf8'), stderr: err.toString('utf8'), output_limit: exceeded, spawn_error: spawnError };
  await writeFile(path.join(root, `${label}.json`), JSON.stringify(receipt, null, 2) + '\n', { flag: 'wx' });
  assert.equal(exceeded, false); assert.equal(spawnError, undefined); assert.equal(ended.signal, null);
  return { ...receipt, result: out.length ? JSON.parse(out.toString('utf8')) : undefined };
}
const argsFor = (state, journal, selectedJava = java) => ['inspect-controller-history', '--state-dir', state, '--openpnp-home', runtime, '--java', selectedJava, '--journal', journal];
function offline(run, valid) {
  assert.equal(run.code, valid ? 0 : 2); assert.equal(run.stderr, ''); assert.equal(run.result.valid, valid);
  assert.equal(run.result.schema_version, 1); assert.equal(run.result.mode, 'offline-recorded-controller-history');
  for (const key of ['native_authority_restored', 'native_machine_opened', 'controller_connection_opened', 'journal_appended', 'physical_qualification']) assert.equal(run.result[key], false, key);
  assert.equal(run.stdout.includes('PRIVATE_HISTORY_SENTINEL'), false);
  if (run.result.inspector_process) {
    assert.equal(run.result.inspector_process.reaped, true); assert.ok(run.result.inspector_process.pid > 0);
    assert.throws(() => process.kill(run.result.inspector_process.pid, 0), { code: 'ESRCH' });
  }
  if (valid) {
    assert.equal(run.result.provenance.bridge_sha256, BRIDGE); assert.equal(run.result.provenance.reducer_class_sha256, REDUCER);
    assert.equal(run.result.provenance.journal_json_class_sha256, DTO); assert.equal(run.result.input_unchanged, true);
    assert.equal(run.result.shared_read_lock_held_during_scan, true); assert.equal(run.result.recorded_bytes_prove_force_or_power_loss_survival, false);
  }
}
const serialize = rows => rows.map(r => JSON.stringify(r)).join('\n') + '\n';
const clone = value => structuredClone(value);
const renumber = rows => { rows.forEach((r, i) => { r.sequence = i + 1; }); return rows; };
const byType = (rows, type) => { const found = rows.find(r => r.type === type); assert.ok(found, `missing ${type}`); return found.payload; };
const terminal = rows => rows.findLast(r => r.type === 'operation').payload;
function inject(rows, select, literal) {
  select(rows, 'PRIVATE_NUMBER_LITERAL');
  const raw = serialize(rows); assert.equal(raw.split('"PRIVATE_NUMBER_LITERAL"').length, 2);
  return raw.replace('"PRIVATE_NUMBER_LITERAL"', literal);
}
function numericForms(rows, mode) {
  let count = 0;
  const text = rows.map(row => JSON.stringify(row, (_key, value) => {
    if (typeof value !== 'number') return value;
    assert.ok(Number.isSafeInteger(value)); return `EXACT_PRIVATE_INTEGER_${value}_${count++}`;
  })).join('\n') + '\n';
  assert.ok(count > 40, 'Transform covers envelope, passive receipt and nested controller integers');
  return text.replace(/"EXACT_PRIVATE_INTEGER_(-?\d+)_(\d+)"/g, (_match, n, i) => n + (mode === 'decimal' ? '.0' : mode === 'exponent' ? 'e0' : ['', '.0', 'e0'][Number(i) % 3]));
}
function abandonment(rows) {
  const last = clone(rows.findLast(r => r.type === 'operation'));
  assert.equal(last.payload.state, 'outcome_unknown'); last.payload.state = 'cancelled';
  last.payload.updated_at = '2026-09-11T14:00:00Z';
  last.payload.result = { resolution: 'abandoned-after-simulator-reset', previous_physical_outcome: 'unknown', repeat_action_performed: false };
  rows.push(last); return renumber(rows);
}

test('actual recovery56 history CLI preserves exact values, operation order and offline class dependencies', { skip, timeout: 180000 }, async t => {
  const parent = process.env.OPENPNP_HISTORY_TEST_OUTPUT || path.join(repository, 'validation'); await mkdir(parent, { recursive: true });
  const root = await mkdtemp(path.join(parent, 'controller-history-recovery56-')); t.diagnostic(`Retained test evidence: ${root}`);
  const sourceBefore = await fingerprints(sourceFiles), originalBefore = await fingerprints(fixtures.map(f => f.file));
  for (const f of fixtures) { assert.equal(originalBefore[f.file].bytes, f.bytes); assert.equal(originalBefore[f.file].sha256, f.sha256); }
  await writeFile(path.join(root, 'inputs-before.json'), JSON.stringify({ sources: sourceBefore, originals: originalBefore }, null, 2) + '\n', { flag: 'wx' });
  const state = path.join(root, 'installed'), installation = await execute(root, 'install', ['install-bridge', '--state-dir', state]);
  assert.equal(installation.code, 0); assert.equal(installation.result.installed, true); const stateBefore = await tree(state);
  const success = (await readFile(fixtures[0].file, 'utf8')).trimEnd().split('\n').map(JSON.parse);
  const recovered = (await readFile(fixtures[2].file, 'utf8')).trimEnd().split('\n').map(JSON.parse);
  let cases = 0;
  async function derived(label, raw, valid, verify = () => {}, source = fixtures[0]) {
    const journal = path.join(root, `${label}.jsonl`); await writeFile(journal, raw, { flag: 'wx' }); const before = await fingerprint(journal);
    await writeFile(path.join(root, `${label}-derivation.json`), JSON.stringify({ kind: 'owned-derivative-of-retained-native-journal', source: source.file, source_sha256: source.sha256, derivative_sha256: before.sha256 }, null, 2) + '\n', { flag: 'wx' });
    const run = await execute(root, label, argsFor(state, journal)); offline(run, valid); verify(run.result);
    assert.deepEqual(await fingerprint(journal), before); cases++;
  }
  try {
    for (const f of fixtures) await t.test(`exact-original-${f.name}`, async () => {
      const run = await execute(root, f.name, argsFor(state, f.file)); offline(run, true);
      assert.equal(run.result.history.recorded_operation_state, f.state); assert.equal(run.result.journal_sha256, f.sha256); cases++;
    });
    for (const mode of ['decimal', 'exponent', 'mixed']) await t.test(`exact-integral-${mode}`, () => derived(`exact-${mode}`, numericForms(success, mode), true, r => {
      assert.equal(r.history.recorded_operation_state, 'succeeded'); assert.equal(r.history.wrapper_success_recorded, true);
    }));
    await t.test('mixed-receipt-grant-equality', () => derived('mixed-receipt-grant', inject(clone(success), (r, v) => { byType(r, 'session_granted').ownership_epoch = v; }, String(byType(success, 'session_granted').ownership_epoch) + '.000'), true));
    const numbers = [
      ['near-sequence', (r, v) => { r[0].sequence = v; }, '1.00000000000000001'],
      ['near-schema', (r, v) => { byType(r, 'controller_diagnostic_admission').schema_version = v; }, '1.00000000000000001'],
      ['near-owner-epoch', (r, v) => { byType(r, 'controller_diagnostic_admission').ownership_epoch = v; }, String(byType(success, 'controller_diagnostic_admission').ownership_epoch) + '.00000000000000001'],
      ['near-observation-sequence', (r, v) => { byType(r, 'controller_diagnostic_step_intent').before.observation_sequence = v; }, String(byType(success, 'controller_diagnostic_step_intent').before.observation_sequence) + '.00000000000000001'],
      ['near-result-step-count', (r, v) => { terminal(r).result.steps_attempted = v; }, '4.00000000000000001'],
      ['negative-near-zero', (r, v) => { byType(r, 'controller_diagnostic_step_intent').step_index = v; }, '-0.00000000000000001'],
      ['unsafe-owner-integer', (r, v) => { byType(r, 'controller_diagnostic_admission').ownership_epoch = v; }, '9007199254740992'],
      ['unsafe-rounded-fraction', (r, v) => { byType(r, 'controller_diagnostic_admission').ownership_epoch = v; }, '9007199254740991.000000000000001'],
      ['overflow-exponent', (r, v) => { byType(r, 'controller_diagnostic_admission').schema_version = v; }, '1e309'],
      ['underflow-exponent', (r, v) => { byType(r, 'controller_diagnostic_admission').schema_version = v; }, '1e-10001'],
      ['string-integer', (r, v) => { byType(r, 'controller_diagnostic_admission').schema_version = v; }, '"1"'],
      ['boolean-integer', (r, v) => { byType(r, 'controller_diagnostic_admission').schema_version = v; }, 'true'],
    ];
    for (const [name, select, literal] of numbers) await t.test(name, () => derived(name, inject(clone(success), select, literal), false));
    for (const wanted of ['accepted', 'running', 'succeeded']) await t.test(`identical-duplicate-${wanted}`, async () => {
      const rows = clone(success), at = rows.findIndex(r => r.type === 'operation' && r.payload.state === wanted); assert.ok(at >= 0);
      rows.splice(at + 1, 0, clone(rows[at])); await derived(`duplicate-${wanted}`, serialize(renumber(rows)), false);
    });
    await t.test('conservative-abandonment', () => derived('abandoned', serialize(abandonment(clone(recovered))), true, r => {
      assert.equal(r.history.recorded_operation_state, 'cancelled'); assert.equal(r.history.offline_disposition, 'recorded-cancelled');
      assert.equal(r.history.requires_reconciliation, true); assert.equal(r.history.wrapper_success_recorded, false); assert.equal(r.history.pending_step, 'connect');
    }, fixtures[2]));
    const invalidAbandonments = [
      ['known-physical-outcome', r => { terminal(r).result.previous_physical_outcome = 'known'; }],
      ['repeat-action', r => { terminal(r).result.repeat_action_performed = true; }],
      ['missing-unknown', r => { delete terminal(r).result.previous_physical_outcome; }],
      ['extra-abandonment-field', r => { terminal(r).result.extra = true; }],
      ['abandonment-resume', r => { const next = clone(r.at(-1)); next.payload.state = 'running'; r.push(next); }],
      ['abandonment-terminal-repeat', r => { r.push(clone(r.at(-1))); }],
      ['abandonment-effect', r => { r.push(clone(r.find(e => e.type === 'controller_diagnostic_step_intent'))); }],
    ];
    for (const [name, edit] of invalidAbandonments) await t.test(name, async () => {
      const rows = abandonment(clone(recovered)); edit(rows); await derived(name, serialize(renumber(rows)), false, undefined, fixtures[2]);
    });
    await t.test('parser-dependency-guard-before-journal-parse', async () => {
      const copyRoot = path.join(root, 'copied-plugin'); await cp(plugin, copyRoot, { recursive: true });
      const manifestFile = path.join(copyRoot, 'controller-history/build-manifest.json'), manifestBytes = await readFile(manifestFile), manifest = JSON.parse(manifestBytes);
      const privateJournal = path.join(root, 'dependency-sentinel.jsonl'); await writeFile(privateJournal, 'PRIVATE_HISTORY_SENTINEL\n', { flag: 'wx' }); const before = await fingerprint(privateJournal);
      try {
        for (const [key, code] of [['journal_json_class_sha256', 'INCOMPATIBLE_JOURNAL_JSON'], ['journal_json_source_sha256', 'OFFLINE_HISTORY_INPUT_OR_ARTIFACT_FAILURE']]) {
          await writeFile(manifestFile, JSON.stringify({ ...manifest, [key]: '0'.repeat(64) }));
          const run = await execute(root, key, argsFor(state, privateJournal), path.join(copyRoot, 'scripts/openpnp.mjs')); offline(run, false);
          assert.equal(run.result.error_code, code); if (key.includes('class')) assert.equal(run.result.inspector_process.reaped, true); cases++;
        }
      } finally { await writeFile(manifestFile, manifestBytes); assert.deepEqual(await fingerprint(privateJournal), before); }
    });
    await t.test('actual-cli-loads-only-helper-reducer-and-exact-json-dependency', { skip: process.platform === 'win32' }, async () => {
      const log = path.join(root, 'java-class-load.log'), wrapper = path.join(root, 'trace-java');
      const quote = value => "'" + value.replaceAll("'", "'\\''") + "'";
      await writeFile(wrapper, `#!/bin/sh\nexec ${quote(java)} ${quote('-Xlog:class+load=info:file=' + log)} "$@"\n`, { flag: 'wx' }); await chmod(wrapper, 0o700);
      const run = await execute(root, 'class-trace', argsFor(state, fixtures[0].file, wrapper)); offline(run, true);
      const text = await readFile(log, 'utf8'); const openpnp = text.split('\n').filter(line => /\[class,load\] org\.openpnp\./.test(line));
      for (const name of ['ControllerHistoryMain', 'NativeControllerJournal', 'NativeJournalJson']) assert.ok(openpnp.some(line => line.includes(`org.openpnp.codex.${name} source:`)), name);
      for (const line of openpnp) assert.match(line, /org\.openpnp\.codex\.(ControllerHistoryMain|NativeControllerJournal|NativeJournalJson)(?:\$[^ ]+)? source:/, 'Only the three exact owners and their nested/generated classes may load');
      for (const name of ['NativeControllerJournal', 'NativeJournalJson']) assert.ok(openpnp.find(line => line.includes(`org.openpnp.codex.${name} source:`)).includes('openpnp-codex-bridge.jar'));
      await writeFile(path.join(root, 'class-load-proof.json'), JSON.stringify({ class_log_sha256: sha(await readFile(log)), loaded_openpnp_classes: openpnp, native_application_classes_loaded: false, machine_opened: false, physical_qualification: false }, null, 2) + '\n', { flag: 'wx' }); cases++;
    });
  } finally {
    const sourceAfter = await fingerprints(sourceFiles), originalAfter = await fingerprints(fixtures.map(f => f.file)), stateAfter = await tree(state);
    const unchanged = JSON.stringify(sourceAfter) === JSON.stringify(sourceBefore) && JSON.stringify(originalAfter) === JSON.stringify(originalBefore) && JSON.stringify(stateAfter) === JSON.stringify(stateBefore);
    await writeFile(path.join(root, 'preservation.json'), JSON.stringify({ cases_completed: cases, sources: sourceAfter, originals: originalAfter, installed_state: stateAfter, all_inputs_unchanged: unchanged, machine_or_controller_started: false }, null, 2) + '\n', { flag: 'wx' });
    assert.deepEqual(sourceAfter, sourceBefore); assert.deepEqual(originalAfter, originalBefore); assert.deepEqual(stateAfter, stateBefore);
  }
});
