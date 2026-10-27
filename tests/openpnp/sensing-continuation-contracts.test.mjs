// SPDX-License-Identifier: Apache-2.0
import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { OpenPnpRuntime, PINNED_UPSTREAM } from '../../src/openpnp/node/runtime.mjs';
import { TOOL_BY_NAME, publicDefinition } from '../../src/openpnp/node/contracts.mjs';

const method = 'openpnp_request_sensing_reconciliation';
const args = () => ({ session_id: 'lease', request_id: randomUUID(), expected_config_revision: 'cfg-2', recovery_kind: 'continue-faulted-job-replacement', replacement_attempt_id: randomUUID() });
function fixture() {
  const calls = [];
  return { calls, runtime: new OpenPnpRuntime({ client: { async call(name, input, options) {
    if (name === 'openpnp_get_capabilities') return { schema_version: 1, bridge_version: '0.1.0', upstream_commit: PINNED_UPSTREAM, tools: [method], sensing_reconciliation: { profile: 'native-simulator-sensing-reconciliation-v1', available: true, simulation_only: true, hardware_qualified: false } };
    calls.push({ name, input: structuredClone(input), options });
    return { operation_id: randomUUID(), request_id: input.request_id, state: 'accepted', config_revision: input.expected_config_revision };
  } } }) };
}
test('continuation transmits exactly its five fields; the Bridge captures current scope', async () => {
  const { runtime, calls } = fixture(), input = args(), original = structuredClone(input);
  await runtime.call(method, input);
  assert.deepEqual(calls, [{ name: method, input: original, options: { mutating: true } }]);
  assert.deepEqual(input, original);
  assert.equal(TOOL_BY_NAME.get(method).annotations.idempotentHint, false);
  for (const name of ['openpnp_submit_sensing_reconciliation', 'openpnp_continue_replacement_locally']) assert.equal(TOOL_BY_NAME.has(name), false);
});
test('continuation rejects missing fields, foreign scopes, supplied actions and noncanonical IDs before transport', async () => {
  const { runtime, calls } = fixture();
  for (const key of Object.keys(args())) {
    const input = args(); delete input[key]; await assert.rejects(runtime.call(method, input), { code: 'INVALID_ARGUMENT' });
  }
  for (const foreign of [{ job_id: randomUUID() }, { expected_job_revision: 'a'.repeat(64) }, { expected_board_load_revision: 'load-3' }, { expected_material_revision: 'material-4' }, { original_operation_id: randomUUID() }]) {
    await assert.rejects(runtime.call(method, { ...args(), ...foreign }), { code: 'INVALID_ARGUMENT' });
  }
  for (const key of ['task_id', 'continuation_id', 'fault_set_sha256', 'source', 'replacement', 'proposed_effects', 'repair', 'dispose', 'observations', 'local_authority']) {
    await assert.rejects(runtime.call(method, { ...args(), [key]: randomUUID() }), { code: 'INVALID_ARGUMENT' });
  }
  for (const value of ['', null, 'not-a-uuid', 'AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA', `${randomUUID()} `]) {
    await assert.rejects(runtime.call(method, { ...args(), replacement_attempt_id: value }), { code: 'INVALID_ARGUMENT' });
  }
  await assert.rejects(runtime.call(method, { ...args(), expected_config_revision: 'load-2' }), { code: 'INVALID_ARGUMENT' });
  assert.equal(calls.length, 0);
});
test('other recovery kinds cannot carry a continuation attempt and generated catalog retains the exact contract', async () => {
  const { runtime, calls } = fixture();
  await assert.rejects(runtime.call(method, { ...args(), recovery_kind: 'restore-sensing-readiness' }), { code: 'INVALID_ARGUMENT' });
  await assert.rejects(runtime.call(method, { ...args(), recovery_kind: 'replace-faulted-job-attempt', job_id: randomUUID(), expected_job_revision: 'a'.repeat(64), expected_board_load_revision: 'load-3', expected_material_revision: 'material-4', original_operation_id: randomUUID() }), { code: 'INVALID_ARGUMENT' });
  assert.equal(calls.length, 0);
  const definition = TOOL_BY_NAME.get(method), catalog = JSON.parse(await readFile(new URL('../../plugins/openpnp/mcp/tool-inputs.json', import.meta.url), 'utf8'));
  assert.deepEqual(catalog.tools.find(tool => tool.name === method), publicDefinition(definition));
  assert.match(definition.description, /same-process retained candidate/);
  assert.match(definition.description, /new local one-use decision/);
  assert.match(definition.description, /Completed replacement\/load effects are not replayed/);
});
