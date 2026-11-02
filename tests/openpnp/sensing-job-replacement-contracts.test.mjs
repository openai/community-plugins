// SPDX-License-Identifier: Apache-2.0
import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { OpenPnpRuntime, PINNED_UPSTREAM } from '../../src/openpnp/node/runtime.mjs';

const method = 'openpnp_request_sensing_reconciliation';
const jobFields = ['job_id', 'expected_job_revision', 'expected_board_load_revision', 'expected_material_revision', 'original_operation_id'];
const args = () => ({ request_id: randomUUID(), session_id: 'lease', expected_config_revision: 'cfg-1', recovery_kind: 'replace-faulted-job-attempt', job_id: randomUUID(), expected_job_revision: 'a'.repeat(64), expected_board_load_revision: 'load-3', expected_material_revision: 'material-4', original_operation_id: randomUUID() });
function fixture() {
  const effects = [];
  return { effects, runtime: new OpenPnpRuntime({ client: { async call(name, input) {
    if (name === 'openpnp_get_capabilities') return { schema_version: 1, bridge_version: '0.1.0', upstream_commit: PINNED_UPSTREAM, tools: [method], sensing_reconciliation: { profile: 'native-simulator-sensing-reconciliation-v1', available: true, simulation_only: true, hardware_qualified: false } };
    effects.push(structuredClone(input)); return { operation_id: randomUUID(), request_id: input.request_id, state: 'accepted', config_revision: input.expected_config_revision };
  } } }) };
}
test('replacement request binds every job and load revision without accepting remote disposition evidence', async () => {
  const { runtime, effects } = fixture(), input = args();
  await runtime.call(method, input); assert.deepEqual(effects, [input]);
  for (const field of jobFields) { const missing = args(); delete missing[field]; await assert.rejects(runtime.call(method, missing), { code: 'INVALID_ARGUMENT' }); }
  for (const invalid of [{ expected_material_revision: 'load-1' }, { expected_job_revision: 'cfg-1' }, { original_operation_id: 'unknown' }, { replacement_job: {} }, { disposed: true }, { feeder_loads: [] }, { observations: [] }]) await assert.rejects(runtime.call(method, { ...args(), ...invalid }), { code: 'INVALID_ARGUMENT' });
  assert.equal(effects.length, 1);
});
test('standalone recovery cannot smuggle a job dependency or erase one from a replacement request', async () => {
  const { runtime, effects } = fixture(), source = args();
  const standalone = Object.fromEntries(Object.entries({ ...source, recovery_kind: 'restore-sensing-readiness' }).filter(([key]) => !jobFields.includes(key)));
  await runtime.call(method, standalone);
  for (const field of jobFields) await assert.rejects(runtime.call(method, { ...standalone, [field]: source[field] }), { code: 'INVALID_ARGUMENT' });
  await assert.rejects(runtime.call(method, { ...standalone, recovery_kind: 'replace-faulted-job-attempt' }), { code: 'INVALID_ARGUMENT' });
  assert.deepEqual(effects, [standalone]);
});
