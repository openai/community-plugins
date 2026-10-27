// SPDX-License-Identifier: Apache-2.0
// Actual shipped CLI / actual Java history inspection. Never starts a machine or controller.
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync } from 'node:fs';
import { chmod, cp, mkdir, mkdtemp, readFile, readdir, stat, symlink, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const plugin = path.join(repository, 'plugins/openpnp');
const cli = path.join(plugin, 'scripts/openpnp.mjs');
const retained = process.env.OPENPNP_HISTORY_REAL_FIXTURES || path.join(repository, 'validation/explicit-retained-history-required');
const runtime = process.env.OPENPNP_HISTORY_RUNTIME || path.join(repository, 'validation/explicit-history-runtime-required');
const java = process.env.OPENPNP_HISTORY_JAVA || '';
const crash = path.join(retained, 'native-regression-corrected03/tmp/openpnp-controller-crash-8872944327462146736');
const sha = value => createHash('sha256').update(value).digest('hex');
// Inspector provenance identifies the selected installed Bridge, independently of the old input journals.
const selectedBridgeSha256 = sha(await readFile(path.join(plugin, 'bridge/openpnp-codex-bridge.jar')));
const fixture = (name, file, bytes, hash, state, outcomes, pending, retired, records) => ({ name, file, bytes, hash, state, outcomes, pending, retired, records });
const fixtures = [
  fixture('official-success', path.join(retained, 'controller-official-03/native-state/journal/operations.jsonl'), 17724, '31c9d7acf61f298d3f52c00d4f845c60edf70d646ffe0d545db5ec809f2fe81d', 'succeeded', ['bind', 'connect', 'identify', 'close'], null, true, 17),
  fixture('connect-halt', path.join(crash, 'connect-intent-force/producer/journal/operations.jsonl'), 6551, 'adf89c45a84f01e8a305ace453e04624f4d835e09f6cb497b3cb0e2a725dac0d', 'running', ['bind'], 'connect', false, 9),
  fixture('connect-recovered', path.join(crash, 'connect-intent-force/recovery-state/journal/operations.jsonl'), 10612, 'bac22043d3f96663fd3e0fb7c9b07fcfbdcc3f8b3af2c31058c7116261f456a2', 'outcome_unknown', ['bind'], 'connect', false, 10),
  fixture('identify-halt', path.join(crash, 'identify-before-ack/producer/journal/operations.jsonl'), 9463, '3895ca7eca28b8a51aba6f6e59bc9d92bf04783d28a548f4b058b5e3ab7f7b07', 'running', ['bind', 'connect'], 'identify', false, 11),
  fixture('identify-recovered', path.join(crash, 'identify-before-ack/recovery-state/journal/operations.jsonl'), 14852, 'a6c898f93b4ddae7ac18e04b3fd9749c0ba70743f588caf82f809fe1a2ff135a', 'outcome_unknown', ['bind', 'connect'], 'identify', false, 12),
  fixture('success-force-halt', path.join(crash, 'succeeded-force/producer/journal/operations.jsonl'), 17544, 'a0593dce32e71f9254a462f277a235a640415d3547c6394f7314a1606e0cbafd', 'succeeded', ['bind', 'connect', 'identify', 'close'], null, true, 16),
  fixture('success-force-recovered', path.join(crash, 'succeeded-force/recovery-state/journal/operations.jsonl'), 17544, 'a0593dce32e71f9254a462f277a235a640415d3547c6394f7314a1606e0cbafd', 'succeeded', ['bind', 'connect', 'identify', 'close'], null, true, 16),
];
const available = existsSync(java) && existsSync(path.join(runtime, 'codex-build-manifest.json')) && fixtures.every(f => existsSync(f.file));
const skip = available ? false : 'Set OPENPNP_HISTORY_JAVA, OPENPNP_HISTORY_RUNTIME and OPENPNP_HISTORY_REAL_FIXTURES to retained corrected build03 inputs.';
const checks = ['native_authority_restored', 'native_machine_opened', 'controller_connection_opened', 'journal_appended', 'physical_qualification'];

function offline(result) {
  assert.equal(result.schema_version, 1);
  assert.equal(result.mode, 'offline-recorded-controller-history');
  for (const key of checks) assert.equal(result[key], false, key);
}
function reaped(processReceipt) {
  assert.equal(processReceipt.reaped, true);
  assert.ok(Number.isInteger(processReceipt.pid) && processReceipt.pid > 0);
  assert.throws(() => process.kill(processReceipt.pid, 0), { code: 'ESRCH' });
}
async function fingerprint(file) {
  const before = await stat(file), bytes = await readFile(file), after = await stat(file);
  assert.equal(before.size, after.size); assert.equal(before.mtimeMs, after.mtimeMs);
  return { bytes: bytes.length, sha256: sha(bytes), mtime_ms: after.mtimeMs, inode: after.ino };
}
async function treeFiles(root) {
  const result = {};
  async function walk(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const file = path.join(directory, entry.name);
      if (entry.isDirectory()) await walk(file);
      else { assert.ok(entry.isFile(), 'Owned state contains only regular files'); result[path.relative(root, file)] = await fingerprint(file); }
    }
  }
  await walk(root); return result;
}
async function evidenceRoot(t) {
  const parent = process.env.OPENPNP_HISTORY_TEST_OUTPUT || path.join(repository, 'validation');
  await mkdir(parent, { recursive: true });
  const root = await mkdtemp(path.join(parent, 'controller-history-cli-'));
  t.diagnostic(`Retained test evidence: ${root}`); return root;
}
async function execute(root, label, args, entrypoint = cli) {
  const started = Date.now();
  const child = spawn(process.execPath, [entrypoint, ...args], { shell: false, stdio: ['ignore', 'pipe', 'pipe'], env: { ...process.env } });
  let out = Buffer.alloc(0), err = Buffer.alloc(0), exceeded = false, failure;
  const timer = setTimeout(() => child.kill('SIGTERM'), 30000);
  const lastResort = setTimeout(() => child.kill('SIGKILL'), 32000);
  child.on('error', e => { failure = e.code || 'SPAWN_ERROR'; });
  child.stdout.on('data', b => { if (out.length + b.length <= 1048576) out = Buffer.concat([out, b]); else { exceeded = true; child.kill('SIGTERM'); } });
  child.stderr.on('data', b => { if (err.length + b.length <= 65536) err = Buffer.concat([err, b]); else { exceeded = true; child.kill('SIGTERM'); } });
  const status = await new Promise(resolve => child.once('close', (code, signal) => resolve({ code, signal })));
  clearTimeout(timer); clearTimeout(lastResort);
  const receipt = { label, ...status, pid: child.pid, elapsed_ms: Date.now() - started, stdout: out.toString('utf8'), stderr: err.toString('utf8'), output_limit: exceeded, spawn_error: failure };
  await writeFile(path.join(root, `${label}.json`), JSON.stringify(receipt, null, 2) + '\n', { flag: 'wx' });
  assert.equal(exceeded, false); assert.equal(failure, undefined); assert.equal(status.signal, null);
  return { ...receipt, result: receipt.stdout.trim() ? JSON.parse(receipt.stdout) : undefined };
}
const argsFor = (state, journal, selectedJava = java) => ['inspect-controller-history', '--state-dir', state, '--openpnp-home', runtime, '--java', selectedJava, '--journal', journal];
async function installed(root) {
  const state = path.join(root, 'installed');
  const run = await execute(root, 'install', ['install-bridge', '--state-dir', state]);
  assert.equal(run.code, 0); assert.equal(run.result.installed, true);
  return state;
}
function serialized(events) { return events.map(e => JSON.stringify(e)).join('\n') + '\n'; }

test('actual history CLI rejects unsupported and ambiguous arguments before opening state', async t => {
  const root = await evidenceRoot(t);
  const cases = [ ['--host', '127.0.0.1'], ['--journal', '/one', '--journal', '/two'], ['--journal'], ['--command', 'M115'], ['--profile', 'physical'] ];
  for (const [index, args] of cases.entries()) {
    const run = await execute(root, `arguments-${index}`, ['inspect-controller-history', ...args]);
    assert.equal(run.code, 1); assert.equal(run.result, undefined);
    assert.equal(typeof JSON.parse(run.stderr).error, 'string');
  }
});

test('actual shipped CLI reads seven retained native journals with exact provenance and preserves every input', { skip }, async t => {
  const root = await evidenceRoot(t), state = await installed(root), stateBefore = await treeFiles(state);
  const before = new Map();
  for (const f of fixtures) { const fact = await fingerprint(f.file); assert.equal(fact.bytes, f.bytes); assert.equal(fact.sha256, f.hash); before.set(f.file, fact); }
  try {
    for (const f of fixtures) await t.test(f.name, async () => {
      const run = await execute(root, f.name, argsFor(state, f.file));
      assert.equal(run.code, 0); assert.equal(run.stderr, ''); const r = run.result; offline(r); assert.equal(r.valid, true);
      assert.equal(r.journal_sha256, f.hash); assert.equal(r.journal_bytes, f.bytes); assert.equal(r.records, f.records);
      assert.equal(r.input_unchanged, true); assert.equal(r.shared_read_lock_held_during_scan, true);
      assert.equal(r.recorded_bytes_prove_force_or_power_loss_survival, false);
      assert.equal(r.history.recorded_operation_state, f.state);
      assert.equal(r.history.offline_disposition, f.state === 'succeeded' ? 'recorded-success' : 'outcome-unknown');
      assert.equal(r.history.requires_reconciliation, f.state !== 'succeeded');
      assert.deepEqual(r.history.steps.map(s => s.step), f.outcomes);
      assert.equal(r.history.pending_step ?? null, f.pending); assert.equal(r.history.retirement_recorded, f.retired);
      assert.equal(r.history.complete_recipe_recorded, f.state === 'succeeded');
      assert.equal(r.history.wrapper_success_recorded, f.state === 'succeeded');
      assert.equal(r.provenance.bridge_sha256, selectedBridgeSha256);
      assert.equal(r.cli_provenance.runtime_manifest_sha256, sha(await readFile(path.join(runtime, 'codex-build-manifest.json'))));
      reaped(r.inspector_process);
    });
  } finally {
    for (const f of fixtures) assert.deepEqual(await fingerprint(f.file), before.get(f.file), f.name);
    assert.deepEqual(await treeFiles(state), stateBefore);
  }
});

test('actual shipped CLI refuses malformed and contradictory private copies without exposing journal text', { skip }, async t => {
  const root = await evidenceRoot(t), state = await installed(root), original = await readFile(fixtures[0].file), originalBefore = await fingerprint(fixtures[0].file);
  const base = original.toString('utf8').trimEnd().split('\n').map(JSON.parse);
  const success = events => events.find(e => e.type === 'operation' && e.payload.state === 'succeeded').payload;
  const cases = [
    ['partial-tail', () => original.subarray(0, original.length - 1)],
    ['invalid-utf8', () => Buffer.from([255, 10])],
    ['blank-record', () => Buffer.concat([original, Buffer.from('\n')])],
    ['duplicate-key', () => original.toString('utf8').replace(/"sequence":1(?=[,}])/, '"sequence":1,"sequence":1')],
    ['sequence-gap', e => { e[3].sequence++; return serialized(e); }],
    ['fractional-controller-integer', e => serialized(e).replace(/"schema_version":1(?=[,}])/, '"schema_version":1.0000000000000000001')],
    ['unknown-event', e => { e[6].type = 'PRIVATE_HISTORY_SENTINEL'; return serialized(e); }],
    ['success-before-execution', e => { const index = e.findIndex(x => x.type === 'operation' && x.payload.state === 'succeeded'); const [terminal] = e.splice(index, 1); e.splice(6, 0, terminal); e.forEach((x, i) => { x.sequence = i + 1; }); return serialized(e); }],
    ['missing-wrapper-id', e => { delete success(e).native_completion.submission_id; return serialized(e); }],
    ['wrapper-not-completed', e => { success(e).native_completion.native_wrapper_completed = false; return serialized(e); }],
    ['contradictory-result', e => { success(e).result.outcome_unknown = true; return serialized(e); }],
    ['contradictory-retirement', e => { e.find(x => x.type === 'controller_diagnostic_retired').payload.completed_recipe = false; return serialized(e); }],
    ['changed-terminal-repeat', e => { const terminal = structuredClone(e.find(x => x.type === 'operation' && x.payload.state === 'succeeded')); terminal.payload.updated_at = '2026-09-11T13:00:00Z'; e.push(terminal); e.forEach((x, i) => { x.sequence = i + 1; }); return serialized(e); }],
  ];
  try {
    for (const [name, mutate] of cases) await t.test(name, async () => {
      const file = path.join(root, `${name}.jsonl`); await writeFile(file, mutate(structuredClone(base)), { flag: 'wx' }); const before = await fingerprint(file);
      const run = await execute(root, name, argsFor(state, file));
      assert.equal(run.code, 2); assert.equal(run.stderr, ''); offline(run.result); assert.equal(run.result.valid, false);
      assert.equal(typeof run.result.error_code, 'string'); assert.equal(run.stdout.includes('PRIVATE_HISTORY_SENTINEL'), false);
      if (run.result.inspector_process) reaped(run.result.inspector_process);
      assert.deepEqual(await fingerprint(file), before);
    });
    const alias = path.join(root, 'journal-alias'); await symlink(path.join(root, 'partial-tail.jsonl'), alias);
    const refused = await execute(root, 'symlink-journal', argsFor(state, alias)); assert.equal(refused.code, 2); offline(refused.result); assert.equal(refused.result.valid, false);
  } finally { assert.deepEqual(await fingerprint(fixtures[0].file), originalBefore); }
});

test('actual CLI validates copied helper manifests and loaded reducer compatibility', { skip }, async t => {
  const root = await evidenceRoot(t), state = await installed(root), stateBefore = await treeFiles(state);
  const copyRoot = path.join(root, 'owned-plugin'); await cp(plugin, copyRoot, { recursive: true });
  const copiedCli = path.join(copyRoot, 'scripts/openpnp.mjs'), manifestFile = path.join(copyRoot, 'controller-history/build-manifest.json');
  const original = await readFile(manifestFile), manifest = JSON.parse(original);
  // The compatibility guard must win before parsing this deliberately invalid owned journal.
  const preScanJournal = path.join(root, 'pre-scan-invalid.jsonl');
  await writeFile(preScanJournal, 'PRIVATE_PRE_SCAN_SENTINEL\n', { flag: 'wx' });
  const preScanBefore = await fingerprint(preScanJournal);
  const cases = [
    ['manifest-array', '[]', 'OFFLINE_HISTORY_INPUT_OR_ARTIFACT_FAILURE'],
    ['manifest-null', 'null', 'OFFLINE_HISTORY_INPUT_OR_ARTIFACT_FAILURE'],
    ['manifest-malformed', '{', 'OFFLINE_HISTORY_INPUT_OR_ARTIFACT_FAILURE'],
    ['manifest-oversize', ' '.repeat(65537), 'OFFLINE_HISTORY_INPUT_OR_ARTIFACT_FAILURE'],
    ['helper-digest', JSON.stringify({ ...manifest, helper_sha256: '0'.repeat(64) }), 'OFFLINE_HISTORY_INPUT_OR_ARTIFACT_FAILURE'],
    ['reducer-digest', JSON.stringify({ ...manifest, reducer_class_sha256: '0'.repeat(64) }), 'INCOMPATIBLE_REDUCER'],
    ['journal-json-digest', JSON.stringify({ ...manifest, journal_json_class_sha256: '0'.repeat(64) }), 'INCOMPATIBLE_JOURNAL_JSON'],
    ['journal-json-source-digest', JSON.stringify({ ...manifest, journal_json_source_sha256: '0'.repeat(64) }), 'OFFLINE_HISTORY_INPUT_OR_ARTIFACT_FAILURE'],
    ['reducer-source-digest', JSON.stringify({ ...manifest, reducer_source_sha256: '0'.repeat(64) }), 'OFFLINE_HISTORY_INPUT_OR_ARTIFACT_FAILURE'],
    ['gson-digest', JSON.stringify({ ...manifest, gson_sha256: '0'.repeat(64) }), 'OFFLINE_HISTORY_INPUT_OR_ARTIFACT_FAILURE'],
  ];
  try {
    for (const [name, bytes, code] of cases) await t.test(name, async () => {
      await writeFile(manifestFile, bytes);
      const run = await execute(root, name, argsFor(state, ['reducer-digest','journal-json-digest'].includes(name) ? preScanJournal : fixtures[0].file), copiedCli);
      assert.equal(run.code, 2); assert.equal(run.stderr, ''); offline(run.result); assert.equal(run.result.valid, false); assert.equal(run.result.error_code, code);
      if (['reducer-digest','journal-json-digest'].includes(name)) { reaped(run.result.inspector_process); assert.equal(run.stdout.includes('PRIVATE_PRE_SCAN_SENTINEL'), false); assert.deepEqual(await fingerprint(preScanJournal), preScanBefore); }
    });
  } finally { await writeFile(manifestFile, original); assert.deepEqual(await treeFiles(state), stateBefore); }
  const recovered = await execute(root, 'restored-helper-manifest', argsFor(state, fixtures[0].file), copiedCli);
  assert.equal(recovered.code, 0); assert.equal(recovered.result.valid, true); reaped(recovered.result.inspector_process);
});

test('actual Java exclusive journal lock rejects inspection until owned holder releases it', { skip }, async t => {
  const root = await evidenceRoot(t), state = await installed(root), journal = path.join(root, 'locked.jsonl');
  await cp(fixtures[0].file, journal); const before = await fingerprint(journal);
  const source = path.join(root, 'OwnedHistoryLock.java');
  await writeFile(source, 'import java.nio.channels.*; import java.nio.file.*; public class OwnedHistoryLock { public static void main(String[] a)throws Exception { try(FileChannel c=FileChannel.open(Path.of(a[0]),StandardOpenOption.READ,StandardOpenOption.WRITE); FileLock l=c.lock()){ System.out.println("OWNED_HISTORY_LOCK_READY"); System.out.flush(); System.in.read(); } } }\n', { flag: 'wx' });
  const holder = spawn(java, ['--source', '11', source, journal], { env: { PATH: '/usr/bin:/bin', LANG: 'C', LC_ALL: 'C' }, stdio: ['pipe', 'pipe', 'pipe'], shell: false });
  let out = '', err = ''; holder.stdout.on('data', b => { out += b; }); holder.stderr.on('data', b => { err += b; });
  const closed = new Promise(resolve => holder.once('close', (code, signal) => resolve({ code, signal })));
  try {
    const deadline = Date.now() + 10000;
    while (!out.includes('OWNED_HISTORY_LOCK_READY') && holder.exitCode === null && Date.now() < deadline) await delay(20);
    assert.match(out, /OWNED_HISTORY_LOCK_READY/, err); assert.equal(err, '');
    const refused = await execute(root, 'exclusive-lock-refused', argsFor(state, journal));
    assert.equal(refused.code, 2); offline(refused.result); assert.equal(refused.result.error_code, 'JOURNAL_IN_USE'); reaped(refused.result.inspector_process);
  } finally {
    holder.stdin.end('x'); const stop = setTimeout(() => holder.kill('SIGKILL'), 3000);
    const exit = await closed; clearTimeout(stop);
    await writeFile(path.join(root, 'lock-holder.json'), JSON.stringify({ pid: holder.pid, ...exit, stdout: out, stderr: err }) + '\n', { flag: 'wx' });
    assert.equal(exit.code, 0); assert.equal(exit.signal, null); assert.throws(() => process.kill(holder.pid, 0), { code: 'ESRCH' });
  }
  const allowed = await execute(root, 'lock-released-success', argsFor(state, journal));
  assert.equal(allowed.code, 0); assert.equal(allowed.result.valid, true); reaped(allowed.result.inspector_process);
  assert.deepEqual(await fingerprint(journal), before);
});

test('owned executable fixtures prove CLI output/deadline cleanup independently of native Java', { skip: skip || process.platform === 'win32' }, async t => {
  const root = await evidenceRoot(t), state = await installed(root);
  await t.test('spawn-failure-has-no-reaped-child', async () => {
    const file = path.join(root, 'owned-non-executable');
    await writeFile(file, 'This owned regular file is intentionally not executable.\n', { flag: 'wx', mode: 0o600 });
    const before = await fingerprint(file), journalBefore = await fingerprint(fixtures[0].file);
    const run = await execute(root, 'spawn-failure', argsFor(state, fixtures[0].file, file));
    assert.equal(run.code, 2); assert.equal(run.stderr, ''); offline(run.result);
    assert.equal(run.result.error_code, 'INSPECTOR_START_FAILED');
    assert.equal(run.result.inspector_process.pid, null); assert.equal(run.result.inspector_process.reaped, false);
    assert.deepEqual(await fingerprint(file), before); assert.deepEqual(await fingerprint(fixtures[0].file), journalBefore);
  });
  for (const [name, body, code] of [
    ['output-limit', "process.stdout.write('x'.repeat(70000));", 'INSPECTION_OUTPUT_LIMIT'],
    ['deadline', '', 'INSPECTION_DEADLINE'],
  ]) await t.test(name, async () => {
    const executable = path.join(root, `${name}-owned-executable.cjs`), pidFile = path.join(root, `${name}.pid`);
    await writeFile(executable, `#!${process.execPath}\nrequire('node:fs').writeFileSync(${JSON.stringify(pidFile)}, String(process.pid));process.on('SIGTERM',()=>{});${body}setInterval(()=>{},1000);\n`, { flag: 'wx', mode: 0o700 }); await chmod(executable, 0o700);
    const result = await execute(root, name, argsFor(state, fixtures[0].file, executable));
    assert.equal(result.code, 2); assert.equal(result.stderr, ''); offline(result.result); assert.equal(result.result.error_code, code);
    const receipt = result.result.inspector_process; reaped(receipt); assert.equal(receipt.signal, 'SIGKILL');
    assert.equal(receipt.pid, Number(await readFile(pidFile, 'utf8')));
    if (name === 'deadline') assert.ok(receipt.elapsed_ms >= 15000 && receipt.elapsed_ms < 20000);
    else assert.ok(receipt.elapsed_ms < 5000);
  });
});
