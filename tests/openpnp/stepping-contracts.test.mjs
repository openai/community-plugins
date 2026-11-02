import test from 'node:test';
import assert from 'node:assert/strict';
import Ajv from '../../src/openpnp/node/node_modules/ajv/dist/ajv.js';
import { TOOL_BY_NAME } from '../../src/openpnp/node/contracts.mjs';
import { OpenPnpRuntime, PINNED_UPSTREAM } from '../../src/openpnp/node/runtime.mjs';

const tool = TOOL_BY_NAME.get('openpnp_step_job');
const validate = new Ajv({ strict: true, allErrors: true, coerceTypes: false }).compile(tool.inputSchema);
const base = { session_id: 'lease', request_id: 'request', expected_config_revision: 'cfg-1' };
test('single-step requires a revision and exactly one validated-job or paused-operation identity', () => {
  assert.equal(tool.mutating, true); assert.notEqual(tool.offline, true);
  assert.equal(validate({ ...base, job_id: 'job' }), true);
  assert.equal(validate({ ...base, operation_id: 'operation' }), true);
  for (const invalid of [base, { ...base, job_id: 'job', operation_id: 'operation' },
    { ...base, job_id: '' }, { ...base, job_id: 1 }, { ...base, job_id: 'job', native_steps: 100 },
    { ...base, job_id: 'job', raw_command: 'next()' }, { ...base, job_id: 'job', expected_config_revision: 'stale-format' }])
    assert.equal(validate(invalid), false, JSON.stringify(invalid));
  for (const key of ['session_id', 'request_id', 'expected_config_revision']) {
    const missing = { ...base, job_id: 'job' }; delete missing[key]; assert.equal(validate(missing), false, key);
  }
});
test('runtime refuses step before bridge advertisement and forwards the exact revision-bound command once', async () => {
  const calls = []; let available = false;
  const client = { async call(name, args, options) {
    calls.push({ name, args, options });
    if (name === 'openpnp_get_capabilities') return { schema_version: 1, bridge_version: '0.1.0', upstream_commit: PINNED_UPSTREAM,
      tools: available ? ['openpnp_step_job'] : [] };
    return { operation_id: 'original-operation', state: 'accepted' };
  } };
  const runtime = new OpenPnpRuntime({ client }); const args = { ...base, operation_id: 'original-operation' };
  await assert.rejects(runtime.call('openpnp_step_job', args), error => error.code === 'UNSUPPORTED_CAPABILITY');
  assert.equal(calls.filter(call => call.name === 'openpnp_step_job').length, 0);
  available = true;
  assert.deepEqual(await runtime.call('openpnp_step_job', args), { operation_id: 'original-operation', state: 'accepted' });
  const forwarded = calls.filter(call => call.name === 'openpnp_step_job');
  assert.deepEqual(forwarded, [{ name: 'openpnp_step_job', args, options: { mutating: true } }]);
  const before = calls.length;
  await assert.rejects(runtime.call('openpnp_step_job', { ...args, job_id: 'second-target' }), error => error.code === 'INVALID_ARGUMENT');
  assert.equal(calls.length, before, 'Conflicting identity must be refused before bridge discovery or dispatch.');
});
