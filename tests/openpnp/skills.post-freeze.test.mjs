// Post-freeze schema/CLI regressions. Authored walkthroughs are unblinded and do not prove skill competence.
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile, mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import os from 'node:os';
import path from 'node:path';
import Ajv from '../../src/openpnp/node/node_modules/ajv/dist/ajv.js';
import { TOOL_BY_NAME } from '../../src/openpnp/node/contracts.mjs';
import { main } from '../../plugins/openpnp/scripts/openpnp.mjs';

const walkthroughs = JSON.parse(await readFile(new URL('./evidence/skill-document-restore-contract-walkthroughs.json', import.meta.url), 'utf8'));
const ajv = new Ajv({ strict: true, allErrors: true, coerceTypes: false, useDefaults: false });
const validate = name => ajv.compile(TOOL_BY_NAME.get(name).inputSchema);

test('unblinded native-document/restore call proposals conform to current authority and input contracts', () => {
  assert.equal(walkthroughs.evidence_class, 'unblinded-authored-contract-walkthrough');
  assert.equal(walkthroughs.behavioral_qualification, false);
  assert.equal(walkthroughs.native_qualification, false);
  assert.equal(walkthroughs.hardware_qualification, false);
  for (const scenario of walkthroughs.scenarios) for (const call of scenario.proposed_calls) {
    assert.ok(TOOL_BY_NAME.has(call.name), `${scenario.id}: unavailable tool`);
    const check = validate(call.name);
    assert.ok(check(call.arguments), `${scenario.id}: ${JSON.stringify(check.errors)}`);
  }
  // Validate the current schemas, not a matching phrase in the authored response.
  for (const name of ['openpnp_save_job', 'openpnp_load_job', 'openpnp_restore_configuration']) {
    const check = validate(name);
    const valid = { request_id: 'request', session_id: 'session', ...(name === 'openpnp_save_job' ? {} : { artifact_id: 'native-receipt' }) };
    assert.equal(check(valid), true);
    for (const field of ['request_id', 'session_id']) {
      const missing = { ...valid }; delete missing[field]; assert.equal(check(missing), false);
    }
    assert.equal(check({ ...valid, path: '/tmp/arbitrary.job.xml' }), false);
  }
});

test('local importer schemas cannot accept a returned native document ID as canonical content', () => {
  const nativeId = 'observed-native-document';
  assert.equal(validate('openpnp_get_artifact')({ artifact_id: nativeId }), false);
  assert.equal(validate('openpnp_prepare_job')({ request_id: 'request', session_id: 'session', artifact_id: nativeId }), false);
  assert.equal(validate('openpnp_get_native_artifact')({ artifact_id: nativeId }), true);
  // The native ID schema does not prove existence or same-instance ownership: the bridge performs those checks.
});

test('the walkthrough offline diagnostic command retains uncertainty and does not alter its journal', async t => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'openpnp-skill-contract-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const state = path.join(root, 'state');
  await mkdir(path.join(state, 'journal'), { recursive: true });
  const operationId = '99999999-9999-4999-8999-999999999999';
  const source = [[5, 'outcome_unknown'], [2, 'succeeded']].map(([sequence, state]) => JSON.stringify({
    sequence, type: 'operation', payload: { operation_id: operationId, method: 'openpnp_start_job', state },
  })).join('\n') + '\n';
  const journal = path.join(state, 'journal/operations.jsonl');
  const output = path.join(root, 'report.json');
  await writeFile(journal, source);
  const receipt = await main(['diagnostics', '--state-dir', state, '--output', output]);
  assert.deepEqual(receipt, { written: output, action_replay_performed: false, may_authorize_action: false });
  const result = JSON.parse(await readFile(output, 'utf8'));
  assert.equal(result.physical_state, 'unknown');
  assert.equal(result.may_authorize_action, false);
  assert.equal(result.action_replay_performed, false);
  assert.equal(result.structurally_complete, false);
  assert.equal(result.recent_operations[0].state, 'outcome_unknown');
  assert.equal(result.unresolved_operation_count, 1);
  assert.deepEqual(result.anomalies.map(item => item.kind), ['missing-prefix', 'non-increasing-sequence-not-applied']);
  assert.equal(result.source.sha256, createHash('sha256').update(source).digest('hex'));
  assert.equal(await readFile(journal, 'utf8'), source);
});
