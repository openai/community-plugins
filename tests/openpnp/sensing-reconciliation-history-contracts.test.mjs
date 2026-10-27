// SPDX-License-Identifier: Apache-2.0
// The optional live case connects to a genuinely restarted, source-free native Bridge.
import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID, createHash } from 'node:crypto';
import { mkdtemp, readFile, writeFile, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { OpenPnpRuntime, PINNED_UPSTREAM } from '../../src/openpnp/node/runtime.mjs';
import { TOOL_BY_NAME } from '../../src/openpnp/node/contracts.mjs';
import { BridgeClient } from '../../src/openpnp/node/client.mjs';
import { ResponseStore } from '../../src/openpnp/node/responses.mjs';
import { createServer } from '../../src/openpnp/node/server.mjs';
import { Client } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { InMemoryTransport } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/inMemory.js';

const read = 'openpnp_get_sensing_reconciliation', request = 'openpnp_request_sensing_reconciliation';
const requestArgs = () => ({ session_id: 'not-a-grant', request_id: randomUUID(), expected_config_revision: 'cfg-1', recovery_kind: 'restore-sensing-readiness' });
// Existing Bridge HTTP Gson omits null-valued object fields; native task/receipt equality
// including nulls is checked independently in the real JVM regression.
const wireProjection = value => Array.isArray(value) ? value.map(wireProjection) : value && typeof value === 'object'
  ? Object.fromEntries(Object.entries(value).filter(([, item]) => item !== null).map(([key, item]) => [key, wireProjection(item)])) : value;
const sourceFree = () => ({ schema_version: 1, bridge_version: '0.1.0', upstream_commit: PINNED_UPSTREAM,
  tools: [read], vacuum_sensing: { available: false },
  sensing_reconciliation: { profile: 'native-simulator-sensing-reconciliation-v1', available: false, simulation_only: true, hardware_qualified: false } });

test('historical read contract requires only its task identity and grants no execution channel', () => {
  const tool = TOOL_BY_NAME.get(read);
  assert.deepEqual(tool.inputSchema.required, ['task_id']);
  assert.deepEqual(Object.keys(tool.inputSchema.properties), ['task_id']);
  assert.equal(tool.inputSchema.additionalProperties, false);
  assert.equal(tool.annotations.readOnlyHint, true);
  assert.equal(tool.mutating, false);
  for (const name of ['openpnp_submit_sensing_reconciliation', 'openpnp_clear_sensing_fault', 'local_native_sensing_reconciliation']) assert.equal(TOOL_BY_NAME.has(name), false);
});

test('source-free discovery preserves historical read and blocks request dispatch', async () => {
  for (const hasProfile of [true, false]) {
    const capabilities = sourceFree(), calls = [], taskId = randomUUID();
    if (!hasProfile) delete capabilities.sensing_reconciliation;
    const retained = { historical: true, task: { task_id: taskId }, live_resolution_activated: false, execution_authority_restored: false };
    const runtime = new OpenPnpRuntime({ client: { async call(name, args, options) {
      calls.push({ name, args, options });
      if (name === 'openpnp_get_capabilities') return capabilities;
      assert.equal(name, read); return retained;
    } } });
    const discovered = await runtime.call('openpnp_get_capabilities');
    assert.equal(discovered.connected, true); assert.ok(discovered.tools.includes(read)); assert.ok(!discovered.tools.includes(request));
    assert.deepEqual(await runtime.call(read, { task_id: taskId }), retained);
    assert.deepEqual(calls.at(-1), { name: read, args: { task_id: taskId }, options: { mutating: false } });
    await assert.rejects(runtime.call(request, requestArgs()), { code: 'UNSUPPORTED_CAPABILITY' });
    assert.ok(calls.every(call => call.name !== request));
    const before = calls.length;
    for (const input of [{ task_id: taskId, activate: true }, { task_id: taskId, session_id: 'old' }, { task_id: taskId, source: {} }, { task_id: taskId, repair: true }])
      await assert.rejects(runtime.call(read, input), { code: 'INVALID_ARGUMENT' });
    assert.equal(calls.length, before);
  }
});

async function connect(t, runtime) {
  const server = createServer(runtime), client = new Client({ name: 'sensing-history-contract', version: '1' });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  t.after(async () => { await client.close(); await server.close(); });
  await Promise.all([server.connect(serverTransport), client.connect(clientTransport)]);
  return client;
}
async function completeReply(client, name, args, transcript) {
  const response = await client.callTool({ name, arguments: args }); transcript.push({ name, args, response });
  assert.notEqual(response.isError, true, JSON.stringify(response.structuredContent));
  const value = response.structuredContent;
  if (!value.truncated) return value;
  assert.equal(value.response_retention.details_available, true);
  const chunks = []; let offset = 0, digest;
  for (let index = 0; index < 1537; index++) {
    const arguments_ = { response_id: value.response_retention.response_id, format: 'bytes', pointer: '', offset, limit: 16384 };
    const page = await client.callTool({ name: 'openpnp_read_response_page', arguments: arguments_ });
    transcript.push({ name: 'openpnp_read_response_page', args: arguments_, response: page });assert.notEqual(page.isError, true);
    const data = page.structuredContent;assert.equal(data.offset, offset);chunks.push(Buffer.from(data.data, 'base64'));digest = data.selected_sha256;
    offset = data.next_offset;
    if (data.eof) { const bytes = Buffer.concat(chunks);assert.equal(createHash('sha256').update(bytes).digest('hex'), digest);return JSON.parse(bytes); }
  }
  throw new Error('MCP retained response exceeded the bounded native response size');
}

test('official MCP SDK advertises schemas while capability result keeps live request unavailable', async t => {
  const capabilities = sourceFree(), taskId = randomUUID(), calls = [], root = await mkdtemp(path.join(os.tmpdir(), 'sensing-history-contract-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const runtime = new OpenPnpRuntime({ responses: new ResponseStore(root), client: { async call(name, args, options) {
    calls.push({ name, args, options });return name === 'openpnp_get_capabilities' ? capabilities : { task: { task_id: taskId }, historical: true, live_resolution_activated: false, execution_authority_restored: false };
  } } });
  const client = await connect(t, runtime), transcript = [], tools = await client.listTools();
  assert.ok(tools.tools.some(tool => tool.name === read && tool.annotations.readOnlyHint));
  const discovered = await completeReply(client, 'openpnp_get_capabilities', {}, transcript);
  assert.ok(discovered.tools.includes(read)); assert.ok(!discovered.tools.includes(request));
  const history = await completeReply(client, read, { task_id: taskId }, transcript);
  assert.equal(history.historical, true); assert.equal(history.live_resolution_activated, false);
  const refused = await client.callTool({ name: request, arguments: requestArgs() });
  assert.equal(refused.isError, true); assert.equal(refused.structuredContent.error.code, 'UNSUPPORTED_CAPABILITY');
  assert.ok(calls.every(call => call.name !== request));
});

test('official MCP SDK reads actual restarted Bridge history without a source or lease', {
  skip: !process.env.OPENPNP_HISTORY_CONNECTION_FILE,
}, async t => {
  const evidence = process.env.OPENPNP_HISTORY_EVIDENCE_DIR;
  assert.ok(evidence, 'Live native qualification needs an explicit evidence directory.');
  const expected = JSON.parse(await readFile(path.join(evidence, 'expected-history.json'), 'utf8'));
  const clientCalls = [], transcript = [], native = new BridgeClient({ connectionFile: process.env.OPENPNP_HISTORY_CONNECTION_FILE });
  const responseRoot = await mkdtemp(path.join(os.tmpdir(), 'sensing-history-live-responses-'));
  t.after(() => rm(responseRoot, { recursive: true, force: true }));
  const runtime = new OpenPnpRuntime({ responses: new ResponseStore(responseRoot), client: { async call(name, args, options) {
    const result = await native.call(name, args, options);clientCalls.push({ name, args, options, result });return result;
  } } });
  const client = await connect(t, runtime);
  try {
    const capabilities = await completeReply(client, 'openpnp_get_capabilities', {}, transcript);
    assert.equal(capabilities.connected, true); assert.ok(capabilities.tools.includes(read)); assert.ok(!capabilities.tools.includes(request));
    assert.equal(capabilities.bridge.sensing_reconciliation.available, false);assert.equal(capabilities.bridge.vacuum_sensing.available, false);
    const observedWireHistory = await native.call(read, { task_id: expected.task_id }, { mutating: false });
    const history = await completeReply(client, read, { task_id: expected.task_id }, transcript);
    assert.deepEqual(history, observedWireHistory);
    assert.equal(history.historical, true);assert.equal(history.live_resolution_activated, false);assert.equal(history.execution_authority_restored, false);
    for (const key of ['task', 'state', 'local_action', 'intervention', 'verification', 'receipt', 'recovery_operation_id']) assert.deepEqual(history[key], wireProjection(expected.resolved[key]));
    const refused = await client.callTool({ name: request, arguments: requestArgs() });transcript.push({ name: request, response: refused });
    assert.equal(refused.isError, true);assert.equal(refused.structuredContent.error.code, 'UNSUPPORTED_CAPABILITY');
    assert.ok(clientCalls.every(call => call.name !== request));
    assert.deepEqual(clientCalls.filter(call => call.name === read).map(({ args, options }) => ({ args, options })), [{ args: { task_id: expected.task_id }, options: { mutating: false } }]);
    const status = await completeReply(client, 'openpnp_get_status', {}, transcript), lease = await completeReply(client, 'openpnp_get_control_session', {}, transcript);
    assert.equal(status.gui_ownership ?? null, null);assert.equal(lease.session_id ?? null, null);assert.equal(lease.expires_in_ms, 0);assert.equal(status.machine.enabled, false);assert.equal(status.native_busy, false);
    await writeFile(path.join(evidence, 'mcp-history-proof.json'), JSON.stringify({ passed: true, task_id: expected.task_id, actual_native_bridge_http: true,
      official_mcp_sdk: true, clean_restart_history_only: true, read_without_source_or_lease: true, live_request_not_dispatched: true,
      history_matches_original_http_projection: true, observed_http_history: observedWireHistory, native_client_calls: clientCalls, mcp_transcript: transcript }, null, 2), { flag: 'wx' });
  } catch (error) {
    await writeFile(path.join(evidence, 'mcp-history-failure.json'), JSON.stringify({ passed: false, failure: String(error), native_client_calls: clientCalls, mcp_transcript: transcript }, null, 2), { flag: 'wx' });throw error;
  }
});
