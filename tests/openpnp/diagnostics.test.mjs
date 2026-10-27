import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, rm, rmdir, symlink } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import os from 'node:os';
import path from 'node:path';
import { appendFileSync } from 'node:fs';
import { inspectJournal } from '../../plugins/openpnp/scripts/diagnostics.mjs';
import { main } from '../../plugins/openpnp/scripts/openpnp.mjs';

const id = '11111111-1111-4111-8111-111111111111';
function event(sequence, state, extra = {}) { return JSON.stringify({ sequence, type: 'operation', payload: {
  operation_id: id, method: 'openpnp_start_job', state, ...extra,
} }) + '\n'; }
async function fixture(t, data) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'openpnp-offline-diagnostics-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const state = path.join(root, 'state'); const file = path.join(state, 'journal/operations.jsonl');
  await mkdir(path.dirname(file), { recursive: true }); await writeFile(file, data);
  return { root, state, file };
}
test('offline journal diagnostics retain final operation state without copying credentials, free text or raw designs', async t => {
  const secret = 'private-bearer-credential-never-export';
  const source = event(1, 'accepted', { session_id: secret }) + event(2, 'running') + event(3, 'outcome_unknown', {
    result: { message: secret, part: 'private-board-name', session_id: secret }, native_steps_started: 7,
  });
  const f = await fixture(t, source);
  const result = await inspectJournal(f.state);
  assert.equal(result.structurally_complete, true);
  assert.equal(result.records, 3); assert.equal(result.operation_count, 1);
  assert.deepEqual({ ...result.operation_state_counts }, { outcome_unknown: 1 });
  assert.equal(result.unresolved_operation_count, 1); assert.equal(result.recent_operations[0].native_steps_started, 7);
  assert.equal(result.source.sha256, createHash('sha256').update(source).digest('hex'));
  assert.equal(result.source.changed_during_read, false);
  assert.equal(result.action_replay_performed, false); assert.equal(result.may_authorize_action, false);
  assert.ok(!JSON.stringify(result).includes(secret)); assert.ok(!JSON.stringify(result).includes('private-board-name'));
  assert.equal(await readFile(f.file, 'utf8'), source);
});
test('partial writes and sequence corruption remain explicit and never become inferred successful actions', async t => {
  const source = event(1, 'accepted') + event(3, 'running') + event(2, 'failed') + '{broken}\n' + event(4, 'succeeded').trimEnd();
  const f = await fixture(t, source);
  const result = await inspectJournal(f.state);
  assert.equal(result.structurally_complete, false);
  assert.deepEqual(result.anomalies.map(item => item.kind), ['sequence-gap', 'non-increasing-sequence-not-applied', 'malformed-json-or-encoding', 'unterminated-tail-not-applied']);
  assert.equal(result.recent_operations[0].state, 'running');
  assert.equal(result.physical_state, 'unknown');
  assert.equal(await readFile(f.file, 'utf8'), source);
});
test('missing prefixes and stale successes cannot remove unresolved evidence; unknown method text is omitted', async t => {
  const f = await fixture(t, event(5, 'outcome_unknown') + event(2, 'succeeded') + event(6, 'failed', { method: 'openpnp_private_customer_design' }));
  const result = await inspectJournal(f.state);
  assert.equal(result.structurally_complete, false);
  assert.equal(result.unresolved_operation_count, 1);
  assert.equal(result.recent_operations[0].state, 'outcome_unknown');
  assert.equal(result.recent_operations[0].last_sequence, 5);
  assert.deepEqual(result.anomalies.map(item => item.kind), ['missing-prefix', 'non-increasing-sequence-not-applied', 'invalid-operation-envelope']);
  assert.ok(!JSON.stringify(result).includes('private_customer_design'));
});
test('diagnostic reads reject escaped journals and output cannot replace state or an existing report', async t => {
  const f = await fixture(t, event(1, 'succeeded'));
  const report = path.join(f.root, 'report.json');
  const result = await main(['diagnostics', '--state-dir', f.state, '--output', report]);
  assert.equal(result.may_authorize_action, false);
  const before = await readFile(report);
  await assert.rejects(main(['diagnostics', '--state-dir', f.state, '--output', report]), { code: 'EEXIST' });
  assert.deepEqual(await readFile(report), before);
  await assert.rejects(main(['diagnostics', '--state-dir', f.state, '--output', f.file]), /outside the machine state/);
  await rm(f.file); await symlink(report, f.file);
  await assert.rejects(inspectJournal(f.state));
  await rm(f.file); await rmdir(path.dirname(f.file)); await symlink(f.root, path.dirname(f.file));
  await assert.rejects(inspectJournal(f.state), /escapes/);
});

test('record observers see the original prefix and concurrent append remains explicit', async t => {
  const f = await fixture(t, event(1, 'accepted'));
  const seen = [];
  const result = await inspectJournal(f.state, { onRecord(value, receipt) {
    seen.push({ value, receipt }); appendFileSync(f.file, event(2, 'succeeded'));
  } });
  assert.equal(seen.length, 1); assert.equal(seen[0].value.payload.state, 'accepted');
  assert.equal(seen[0].receipt.source_line_sha256, createHash('sha256').update(event(1, 'accepted').trimEnd()).digest('hex'));
  assert.equal(result.source.grew_during_read, true); assert.equal(result.source.changed_during_read, true);
  assert.equal(result.recent_operations[0].state, 'accepted'); assert.equal(result.records, 1);
});
