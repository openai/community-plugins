// SPDX-License-Identifier: Apache-2.0
import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { OpenPnpRuntime, PINNED_UPSTREAM } from '../../src/openpnp/node/runtime.mjs';
import { TOOL_BY_NAME, publicDefinition } from '../../src/openpnp/node/contracts.mjs';

const method = 'openpnp_request_sensing_reconciliation';
const args = () => ({ session_id: 'lease', request_id: randomUUID(), expected_config_revision: 'cfg-2', recovery_kind: 'restart-faulted-job-replacement', replacement_attempt_id: randomUUID() });
function fixture(recoveryOverrides = {}, capabilitiesOverrides = {}) {
  const calls = [];
  const capabilities = { schema_version: 1, bridge_version: '0.1.0', upstream_commit: PINNED_UPSTREAM, tools: [method], vacuum_sensing: { available: false }, sensing_reconciliation: { profile: 'native-simulator-sensing-reconciliation-v1', available: true, restart_request_available: true, simulation_only: true, hardware_qualified: false, ...recoveryOverrides }, ...capabilitiesOverrides };
  return { calls, capabilities, runtime: new OpenPnpRuntime({ client: { async call(name, input, options) {
    calls.push({ name, input: structuredClone(input), options });
    if (name === 'openpnp_get_capabilities') return capabilities;
    return { operation_id: randomUUID(), request_id: input.request_id, state: 'accepted', config_revision: input.expected_config_revision };
  } } }) };
}
test('source-absent explicit restart transmits exactly five caller fields without submitting local observations', async () => {
  const { runtime, calls, capabilities } = fixture(), input = args(), original = structuredClone(input);
  const result = await runtime.call(method, input);
  assert.equal(capabilities.vacuum_sensing.available, false);
  assert.deepEqual(calls, [{ name: 'openpnp_get_capabilities', input: {}, options: undefined }, { name: method, input: original, options: { mutating: true } }]);
  assert.equal(result.state, 'accepted');
  assert.deepEqual(input, original);
  assert.equal(TOOL_BY_NAME.get(method).annotations.idempotentHint, false);
  for (const name of ['openpnp_submit_restart_observations', 'openpnp_complete_restart', 'openpnp_submit_sensing_reconciliation']) assert.equal(TOOL_BY_NAME.has(name), false);
});
test('restart refuses missing IDs and foreign old-job/effect fields before capability lookup or native dispatch', async () => {
  const { runtime, calls } = fixture();
  for (const key of Object.keys(args())) { const input = args(); delete input[key]; await assert.rejects(runtime.call(method, input), { code: 'INVALID_ARGUMENT' }); }
  for (const foreign of [{ job_id: randomUUID() }, { expected_job_revision: 'a'.repeat(64) }, { expected_board_load_revision: 'load-3' }, { expected_material_revision: 'material-4' }, { original_operation_id: randomUUID() }]) await assert.rejects(runtime.call(method, { ...args(), ...foreign }), { code: 'INVALID_ARGUMENT' });
  for (const key of ['task_id', 'reattachment_id', 'continuation_id', 'fault_set_sha256', 'source', 'native_graph', 'observations', 'prior_process_disposition', 'proposed_effects', 'repair', 'dispose', 'local_authority']) await assert.rejects(runtime.call(method, { ...args(), [key]: randomUUID() }), { code: 'INVALID_ARGUMENT' });
  for (const value of ['', null, 'bad-id', 'AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA', `${randomUUID()} `]) await assert.rejects(runtime.call(method, { ...args(), replacement_attempt_id: value }), { code: 'INVALID_ARGUMENT' });
  await assert.rejects(runtime.call(method, { ...args(), expected_config_revision: 'load-3' }), { code: 'INVALID_ARGUMENT' });
  assert.equal(calls.length, 0);
});
test('restart requires its explicit Boolean capability in addition to the qualified simulator recovery profile', async () => {
  for (const value of [undefined, null, false, 0, 1, 'true', {}]) {
    const { runtime, calls } = fixture({ restart_request_available: value });
    await assert.rejects(runtime.call(method, args()), { code: 'UNSUPPORTED_CAPABILITY' });
    assert.deepEqual(calls.map(call => call.name), ['openpnp_get_capabilities']);
  }
  for (const recovery of [{ available: false }, { profile: 'foreign' }, { simulation_only: false }, { hardware_qualified: true }]) {
    const { runtime, calls } = fixture(recovery);
    await assert.rejects(runtime.call(method, args()), { code: 'UNSUPPORTED_CAPABILITY' });
    assert.equal(calls.length, 1);
  }
  const { runtime, calls } = fixture({}, { tools: [] });
  await assert.rejects(runtime.call(method, args()), { code: 'UNSUPPORTED_CAPABILITY' });
  assert.equal(calls.length, 1);
});
test('existing recovery branches keep their scopes and do not require restart availability', async () => {
  const { runtime, calls } = fixture({ restart_request_available: false });
  const base = args(); delete base.replacement_attempt_id;
  const standalone = { ...base, recovery_kind: 'restore-sensing-readiness' };
  const replace = { ...base, recovery_kind: 'replace-faulted-job-attempt', job_id: randomUUID(), expected_job_revision: 'a'.repeat(64), expected_board_load_revision: 'load-3', expected_material_revision: 'material-4', original_operation_id: randomUUID() };
  const continuation = { ...args(), recovery_kind: 'continue-faulted-job-replacement' };
  for (const input of [standalone, replace, continuation]) await runtime.call(method, input);
  assert.deepEqual(calls.filter(call => call.name === method).map(call => call.input), [standalone, replace, continuation]);
  for (const input of [{ ...standalone, replacement_attempt_id: randomUUID() }, { ...replace, replacement_attempt_id: randomUUID() }, { ...continuation, job_id: randomUUID() }]) await assert.rejects(runtime.call(method, input), { code: 'INVALID_ARGUMENT' });
  assert.equal(calls.filter(call => call.name === method).length, 3);
});
test('source contract describes observation-only restart completion and the separate continuation decision', () => {
  const definition = publicDefinition(TOOL_BY_NAME.get(method));
  assert.match(definition.description, /explicit GUI restart mode with no live sensing source/);
  assert.match(definition.description, /completion preserves sensing faults and grants no execution readiness/);
  assert.match(definition.description, /A separate continuation decision/);
  assert.equal(definition.inputSchema.additionalProperties, false);
});
