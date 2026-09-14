// SPDX-License-Identifier: Apache-2.0
// Protocol fixtures; integrated native recovery is tested separately.
import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { OpenPnpRuntime, PINNED_UPSTREAM } from '../../src/openpnp/node/runtime.mjs';
import { TOOL_BY_NAME } from '../../src/openpnp/node/contracts.mjs';
import { BridgeClient } from '../../src/openpnp/node/client.mjs';
import { createServer } from '../../src/openpnp/node/server.mjs';
import { Client } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { InMemoryTransport } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/inMemory.js';

const request = 'openpnp_request_sensing_reconciliation', read = 'openpnp_get_sensing_reconciliation';
const profile = { profile: 'native-simulator-sensing-reconciliation-v1', available: true, simulation_only: true, hardware_qualified: false };
const args = () => ({ request_id: randomUUID(), session_id: 'lease', expected_config_revision: 'cfg-1', recovery_kind: 'restore-sensing-readiness' });
function fixture(sensing_reconciliation = profile) {
  const calls = [], capabilities = { schema_version: 1, bridge_version: '0.1.0', upstream_commit: PINNED_UPSTREAM, tools: [request, read], sensing_reconciliation };
  const runtime = new OpenPnpRuntime({ client: { async call(name, input, options) {
    calls.push({ name, input, options });
    return name === 'openpnp_get_capabilities' ? capabilities : name === read ? { task_id: input.task_id, historical: true } : { operation_id: randomUUID(), request_id: input.request_id, config_revision: input.expected_config_revision, state: 'accepted' };
  } } });
  return { calls, runtime };
}

test('recovery request binds its whole scope; the local decision has no MCP submission tool', async () => {
  const { calls, runtime } = fixture(), input = args(), original = structuredClone(input);
  await runtime.call(request, input);
  assert.deepEqual(calls.at(-1), { name: request, input: original, options: { mutating: true } });
  assert.deepEqual(input, original);
  const query = { task_id: randomUUID() }; await runtime.call(read, query);
  assert.deepEqual(calls.at(-1), { name: read, input: query, options: { mutating: false } });
  assert.equal(TOOL_BY_NAME.get(request).annotations.idempotentHint, false);
  assert.equal(TOOL_BY_NAME.get(read).annotations.readOnlyHint, true);
  for (const name of ['openpnp_submit_sensing_reconciliation', 'openpnp_clear_sensing_fault', 'local_native_sensing_reconciliation']) {
    await assert.rejects(runtime.call(name, {}), { code: 'UNKNOWN_TOOL' });
  }
});

test('remote callers cannot inject fault subsets, observations, repairs or authority', async () => {
  const { calls, runtime } = fixture();
  for (const key of Object.keys(args())) { const input = args(); delete input[key]; await assert.rejects(runtime.call(request, input), { code: 'INVALID_ARGUMENT' }); }
  for (const extra of [{ request_id: 'bad' }, { expected_config_revision: 'load-1' }, { recovery_kind: 'clear-all' }, { task_id: randomUUID() },
    { fault_ids: [] }, { source: {} }, { sensor_value: 0 }, { clear_fault: true }, { operator_label: 'remote' }, { observations: [] }, { local_authority: true }]) {
    await assert.rejects(runtime.call(request, { ...args(), ...extra }), { code: 'INVALID_ARGUMENT' });
  }
  for (const input of [{}, { task_id: 'not-uuid' }, { task_id: randomUUID(), activate: true }]) await assert.rejects(runtime.call(read, input), { code: 'INVALID_ARGUMENT' });
  assert.equal(calls.length, 0);
});

test('request requires explicit simulator recovery availability; history reading is independent', async () => {
  for (const change of [null, {}, { ...profile, available: false }, { ...profile, profile: 'other' }, { ...profile, simulation_only: false }, { ...profile, hardware_qualified: true }]) {
    const { calls, runtime } = fixture(change);
    await assert.rejects(runtime.call(request, args()), { code: 'UNSUPPORTED_CAPABILITY' });
    assert.equal(calls.length, 1);
    assert.equal((await runtime.call(read, { task_id: randomUUID() })).historical, true);
  }
});

test('official MCP SDK discovers and invokes the request/read schema without an observation submit path', async t => {
  const { runtime, calls } = fixture(), server = createServer(runtime), client = new Client({ name: 'recovery-contract', version: '1' });
  const [a, b] = InMemoryTransport.createLinkedPair(); t.after(async () => { await client.close(); await server.close(); });
  await Promise.all([server.connect(b), client.connect(a)]);
  const tools = await client.listTools(); assert.ok([request, read].every(name => tools.tools.some(tool => tool.name === name)));
  const result = await client.callTool({ name: request, arguments: args() }); assert.notEqual(result.isError, true); assert.equal(result.structuredContent.state, 'accepted');
  const before = calls.length, invalid = await client.callTool({ name: request, arguments: { ...args(), sensor_value: 0 } });
  assert.equal(invalid.isError, true); assert.equal(calls.length, before);
});

test('lost or mismatched recovery receipts keep the original request ID and are never retried', async t => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'recovery-client-')); t.after(() => rm(directory, { recursive: true, force: true }));
  const tokenFile = path.join(directory, 'token'), connectionFile = path.join(directory, 'connection.json');
  await writeFile(tokenFile, 'T'.repeat(48), { mode: 0o600 }); await writeFile(connectionFile, JSON.stringify({ url: 'http://127.0.0.1:54321/', tokenFile }), { mode: 0o600 });
  const input = args(), valid = { operation_id: randomUUID(), request_id: input.request_id, config_revision: input.expected_config_revision, state: 'accepted' };
  for (const result of [{ task_id: randomUUID() }, { ...valid, request_id: randomUUID() }, { ...valid, config_revision: 'cfg-2' }, { ...valid, operation_id: 'bad' }]) {
    let calls = 0; const client = new BridgeClient({ connectionFile, fetchImpl: async () => { calls++; return new Response(JSON.stringify({ result })); } });
    await assert.rejects(client.call(request, input, { mutating: true }), error => error.code === 'OUTCOME_UNKNOWN' && error.details.request_id === input.request_id); assert.equal(calls, 1);
  }
});
