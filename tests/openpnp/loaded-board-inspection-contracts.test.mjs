import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { OpenPnpRuntime, PINNED_UPSTREAM } from '../../src/openpnp/node/runtime.mjs';
import { BridgeClient } from '../../src/openpnp/node/client.mjs';
import { TOOL_BY_NAME } from '../../src/openpnp/node/contracts.mjs';

const request = 'openpnp_request_board_inspection', read = 'openpnp_get_board_inspection';
const capabilities = { schema_version: 1, bridge_version: '0.1.0', upstream_commit: PINNED_UPSTREAM,
  tools: [request, read], loaded_board_inspection: { profile: 'native-loaded-board-inspection-v1', request_available: true } };
const args = () => ({ request_id: randomUUID(), session_id: 'session', expected_config_revision: 'cfg-2',
  job_id: 'job', expected_job_revision: 'a'.repeat(64), expected_board_load_revision: 'load-3', loaded_board_id: 'native-loaded-board' });
function fixture(caps = capabilities) {
  const calls = [];
  const runtime = new OpenPnpRuntime({ client: { async call(name, input, options) {
    calls.push({ name, input, options });
    return name === 'openpnp_get_capabilities' ? caps : name === read ? { found: false, task_id: input.task_id } : { operation_id: 'operation', state: 'accepted' };
  } } });
  return { runtime, calls };
}

test('inspection forwards exact request scope; local observations have no MCP submission route', async () => {
  const { runtime, calls } = fixture(); const input = args(), before = structuredClone(input);
  assert.equal((await runtime.call(request, input)).state, 'accepted');
  assert.deepEqual(calls.at(-1), { name: request, input: before, options: { mutating: true } });
  assert.deepEqual(input, before);
  const task = { task_id: randomUUID() }; await runtime.call(read, task);
  assert.deepEqual(calls.at(-1), { name: read, input: task, options: { mutating: false } });
  assert.equal(TOOL_BY_NAME.get(request).annotations.readOnlyHint, false);
  assert.equal(TOOL_BY_NAME.get(read).annotations.readOnlyHint, true);
  const count = calls.length;
  for (const name of ['openpnp_submit_board_inspection', 'local_native_inspection_submission']) {
    await assert.rejects(runtime.call(name, {}), { code: 'UNKNOWN_TOOL' });
    assert.equal(TOOL_BY_NAME.has(name), false);
  }
  assert.equal(calls.length, count);
});

test('inspection rejects missing scope, noncanonical IDs and injected evidence before native dispatch', async () => {
  const { runtime, calls } = fixture();
  for (const key of Object.keys(args())) { const input = args(); delete input[key]; await assert.rejects(runtime.call(request, input), { code: 'INVALID_ARGUMENT' }); }
  for (const extra of [{ records: [] }, { operator_label: 'remote' }, { production_authority_granted: true },
    { request_id: 'not-uuid' }, { request_id: 'A'.repeat(36) }, { expected_config_revision: 'cfg-x' },
    { expected_board_load_revision: 'job-1' }, { expected_job_revision: 'old' }, { loaded_board_id: '' }, { loaded_board_id: 'x'.repeat(513) }]) {
    await assert.rejects(runtime.call(request, { ...args(), ...extra }), { code: 'INVALID_ARGUMENT' });
  }
  for (const input of [{}, { task_id: 'old' }, { task_id: randomUUID(), resume: true }, { task_id: randomUUID(), records: [] }])
    await assert.rejects(runtime.call(read, input), { code: 'INVALID_ARGUMENT' });
  assert.equal(calls.length, 0);
});

test('headless, older and mismatched profile discovery cannot dispatch a local form', async () => {
  for (const caps of [{ ...capabilities, tools: [read] }, { ...capabilities, loaded_board_inspection: undefined },
    { ...capabilities, loaded_board_inspection: { profile: 'different', request_available: true } },
    { ...capabilities, loaded_board_inspection: { profile: 'native-loaded-board-inspection-v1', request_available: false } }]) {
    const { runtime, calls } = fixture(caps);
    await assert.rejects(runtime.call(request, args()), { code: 'UNSUPPORTED_CAPABILITY' });
    assert.deepEqual(calls.map(call => call.name), ['openpnp_get_capabilities']);
    assert.equal((await runtime.call(read, { task_id: randomUUID() })).found, false, 'Historical read does not need a live form.');
  }
});

test('lost or malformed request receipt preserves its original UUID and never retries (transport fixture)', async t => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'openpnp-inspection-contract-')); t.after(() => rm(dir, { recursive: true, force: true }));
  const tokenFile = path.join(dir, 'token'), connectionFile = path.join(dir, 'connection.json');
  await writeFile(tokenFile, 'T'.repeat(48), { mode: 0o600 });
  await writeFile(connectionFile, JSON.stringify({ url: 'http://127.0.0.1:54321/', tokenFile }), { mode: 0o600 });
  let result, calls = 0;
  const client = new BridgeClient({ connectionFile, fetchImpl: async () => { calls++; return new Response(JSON.stringify({ result })); } });
  for (result of [{ task_id: randomUUID() }, {}, { operation_id: 'op', state: 'invented' }]) {
    const input = args(), before = calls;
    await assert.rejects(client.call(request, input, { mutating: true }), error => error.code === 'OUTCOME_UNKNOWN' && error.details.request_id === input.request_id);
    assert.equal(calls, before + 1);
  }
  result = { operation_id: 'op', state: 'accepted' };
  assert.deepEqual(await client.call(request, args(), { mutating: true }), result);
});
