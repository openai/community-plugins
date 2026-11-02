import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, readdir, writeFile, readFile, stat, symlink, chmod } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { Client } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { InMemoryTransport } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/inMemory.js';
import { createServer } from '../../src/openpnp/node/server.mjs';
import { OpenPnpRuntime, PINNED_UPSTREAM } from '../../src/openpnp/node/runtime.mjs';
import { ResponseStore, boundedReply, INLINE_JSON_BYTES, MAX_RESPONSE_BYTES } from '../../src/openpnp/node/responses.mjs';
import { PluginError } from '../../src/openpnp/node/errors.mjs';

const hash = value => createHash('sha256').update(value).digest('hex');
const serialized = value => Buffer.from(JSON.stringify(value));
async function storeFor(t, limits) { const root = await mkdtemp(path.join(os.tmpdir(), 'openpnp-responses-')); t.after(() => rm(root, { recursive: true, force: true })); return new ResponseStore(root, limits); }
const largeOperation = () => ({ operation_id: 'op-10000', request_id: 'req-start', bridge_instance_id: 'instance-7', state: 'succeeded', native_steps_started: 200004, result: { job_id: 'job-10000', state: 'completed', requested: 10000, placed: 10000, independently_inspected: 0, placements: Array.from({ length: 10000 }, (_, index) => ({ board_id: 'panel:board-1', reference: `R${index + 1}`, part_id: 'R_0603_10K', placed: true, independently_inspected: false, machine_pose: { x: index / 7, y: index % 80, z: 0.6, rotation: index % 4 * 90 }, note: `independent expected row ${index}` })) } });
async function sdk(t, response, store) {
  const calls = [];
  const client = { async call(name, args, options) { calls.push({ name, args, options }); if (name === 'openpnp_get_capabilities') return { schema_version: 1, bridge_version: '0.1.0', upstream_commit: PINNED_UPSTREAM, tools: ['openpnp_get_operation', 'openpnp_get_artifact', 'openpnp_apply_configuration'] }; if (response instanceof Error) throw response; return response; } };
  const runtime = new OpenPnpRuntime({ client, responses: store }); const server = createServer(runtime);
  const [serverTransport, clientTransport] = InMemoryTransport.createLinkedPair();
  const consumer = new Client({ name: 'large-reply-test', version: '1' });
  await server.connect(serverTransport); await consumer.connect(clientTransport);
  t.after(async () => { await consumer.close(); await server.close(); });
  return { consumer, calls, runtime };
}
async function page(consumer, args) { const result = await consumer.callTool({ name: 'openpnp_read_response_page', arguments: args }); assert.notEqual(result.isError, true, JSON.stringify(result)); assert.ok(Buffer.byteLength(JSON.stringify(result.structuredContent)) <= INLINE_JSON_BYTES); return result.structuredContent; }

test('official SDK 10000-record completion stays inline-bounded with original outcome paths; JSON and byte paging are lossless and offline', async t => {
  const operation = largeOperation(), store = await storeFor(t); const { consumer, calls } = await sdk(t, operation, store);
  const reply = await consumer.callTool({ name: 'openpnp_get_operation', arguments: { operation_id: operation.operation_id } });
  const summary = reply.structuredContent; assert.ok(Buffer.byteLength(JSON.stringify(summary)) <= INLINE_JSON_BYTES); assert.notEqual(reply.isError, true);
  assert.equal(summary.state, 'succeeded'); assert.equal(summary.operation_id, operation.operation_id); assert.equal(summary.request_id, operation.request_id); assert.equal(summary.bridge_instance_id, operation.bridge_instance_id);
  assert.equal(summary.result.state, 'completed'); assert.equal(summary.result.placed, 10000); assert.equal(summary.result.requested, 10000); assert.equal(summary.result.independently_inspected, 0);
  assert.equal(summary.truncated, true); assert.equal(summary.result.placements.total_items, 10000); assert.equal(summary.result.placements.json_pointer, '/result/placements');
  const response_id = summary.response_retention.response_id; assert.equal(response_id, hash(serialized(operation)));
  const collected = []; let offset = 0;
  do { const value = await page(consumer, { response_id, pointer: '/result/placements', offset, limit: 500 }); assert.equal(value.count, value.items.length); assert.equal(value.next_offset, offset + value.count); assert.ok(value.count > 0); collected.push(...value.items); offset = value.next_offset; if (value.eof) break; } while (true);
  assert.deepEqual(collected, operation.result.placements);
  const chunks = []; offset = 0;
  do { const value = await page(consumer, { response_id, pointer: '', format: 'bytes', offset, limit: 16384 }); const chunk = Buffer.from(value.data, 'base64'); assert.equal(chunk.length, value.count); chunks.push(chunk); offset = value.next_offset; if (value.eof) break; } while (true);
  assert.deepEqual(Buffer.concat(chunks), serialized(operation)); assert.equal(calls.length, 2); assert.equal(calls[1].name, 'openpnp_get_operation');
  assert.equal(operation.result.placements.length, 10000, 'server does not modify original native result');
});

test('successful mutation remains succeeded when retention is full; no action is repeated and earlier evidence survives', async t => {
  const store = await storeFor(t, { maxFiles: 1 }); const prior = await store.putBytes(serialized({ earlier: true })); const operation = largeOperation(); const { consumer, calls } = await sdk(t, operation, store);
  const result = await consumer.callTool({ name: 'openpnp_apply_configuration', arguments: { request_id: 'same-request', session_id: 'lease', plan_id: 'plan' } });
  assert.notEqual(result.isError, true); assert.equal(result.structuredContent.state, 'succeeded'); assert.equal(result.structuredContent.operation_id, 'op-10000');
  assert.equal(result.structuredContent.response_retention.details_unavailable, true); assert.equal(result.structuredContent.response_retention.storage_error.code, 'RESPONSE_STORE_CAPACITY');
  assert.equal(calls.filter(call => call.options?.mutating).length, 1); assert.deepEqual(JSON.parse((await store.readBytes(prior.response_id)).toString()), { earlier: true });
});

test('owner-private content store deduplicates at capacity, survives reopening, rejects tamper and retains earlier bytes', async t => {
  const store = await storeFor(t, { maxFiles: 1, maxBytes: 100 }); const data = serialized({ x: 'exact' }); const first = await store.putBytes(data);
  assert.equal((await store.putBytes(data)).deduplicated, true); assert.equal((await readdir(store.directory)).length, 1);
  const reopened = new ResponseStore(store.root); assert.deepEqual(await reopened.readBytes(first.response_id), data);
  if (process.platform !== 'win32') { assert.equal((await stat(store.directory)).mode & 0o777, 0o700); assert.equal((await stat(path.join(store.directory, `${first.response_id}.json`))).mode & 0o777, 0o600); }
  await assert.rejects(store.putBytes(serialized({ y: 'new' })), { code: 'RESPONSE_STORE_CAPACITY' }); assert.deepEqual(await store.readBytes(first.response_id), data);
  await writeFile(path.join(store.directory, `${first.response_id}.json`), 'corrupted'); await assert.rejects(store.readBytes(first.response_id), { code: 'RESPONSE_INTEGRITY_FAILURE' }); await assert.rejects(store.putBytes(data), { code: 'RESPONSE_INTEGRITY_FAILURE' });
});

test('response/store byte limits, links and permissions fail closed without eviction', async t => {
  const store = await storeFor(t, { maxBytes: 5 }); await assert.rejects(store.putBytes(serialized({ too: 'big' })), { code: 'RESPONSE_STORE_CAPACITY' });
  await assert.rejects(store.putBytes(Buffer.alloc(MAX_RESPONSE_BYTES + 1)), { code: 'RESPONSE_TOO_LARGE' }); assert.deepEqual(await readdir(store.directory), []);
  const outside = path.join(store.root, 'outside.json'); await writeFile(outside, '{}', { mode: 0o600 }); const id = hash(Buffer.from('{}'));
  await symlink(outside, path.join(store.directory, `${id}.json`)); await assert.rejects(store.readBytes(id), error => error.code === 'INSECURE_RESPONSE_STORE');
  assert.equal(await readFile(outside, 'utf8'), '{}');
  if (process.platform !== 'win32') { await rm(path.join(store.directory, `${id}.json`)); await chmod(store.directory, 0o755); await assert.rejects(store.putBytes(Buffer.from('{}')), { code: 'INSECURE_RESPONSE_STORE' }); }
});

test('JSON pointer uses own fields, decodes escaping, bounds offsets and forbids prototype traversal', async t => {
  const store = await storeFor(t); const data = JSON.parse('{"a/b":{"~key":[4,5]},"__proto__":{"hidden":1},"constructor":{"x":2}}'); const { response_id } = await store.putBytes(serialized(data));
  assert.deepEqual((await store.readPage({ response_id, pointer: '/a~1b/~0key', offset: 1, limit: 1 })).items, [5]);
  for (const pointer of ['/__proto__/hidden', '/constructor/x', '/toString', '/a~1b/~2key', '/a~1b/~0key/01', '/a~1b/~0key/-1']) await assert.rejects(store.readPage({ response_id, pointer }), { code: 'INVALID_JSON_POINTER' });
  for (const args of [{ offset: -1 }, { offset: 1.5 }, { limit: 501 }, { format: 'bytes', limit: 16385 }, { format: 'path' }]) await assert.rejects(store.readPage({ response_id, ...args }), { code: 'INVALID_ARGUMENT' });
  await assert.rejects(store.readPage({ response_id, offset: 999 }), { code: 'INVALID_PAGE_OFFSET' });
  const raw = await store.readPage({ response_id, format: 'bytes' }); assert.deepEqual(Buffer.from(raw.data, 'base64'), serialized(data));
});

test('oversized individual JSON entry is explicitly unconsumed and retrievable in byte pages', async t => {
  const store = await storeFor(t); const value = [{ text: '😀'.repeat(20000) }, 8]; const { response_id } = await store.putBytes(serialized(value));
  const first = await store.readPage({ response_id }); assert.equal(first.count, 0); assert.equal(first.next_offset, 0); assert.equal(first.eof, false); assert.equal(first.oversized_item.pointer, '/0');
  const chunks = []; let offset = 0;
  do { const next = await store.readPage({ response_id, pointer: '/0', format: 'bytes', offset, limit: 16384 }); chunks.push(Buffer.from(next.data, 'base64')); offset = next.next_offset; if (next.eof) break; } while (true);
  assert.deepEqual(JSON.parse(Buffer.concat(chunks).toString()), value[0]); assert.deepEqual((await store.readPage({ response_id, offset: 1 })).items, [8]);
});

test('official SDK preserves native PNG bytes as an image and rejects bad hashes', async t => {
  const data = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lX8AAAAASUVORK5CYII=', 'base64');
  const artifact = { artifact_id: 'frame', mime_type: 'image/png', base64: data.toString('base64'), sha256: hash(data), size: data.length };
  const store = await storeFor(t); const { consumer } = await sdk(t, artifact, store);
  const reply = await consumer.callTool({ name: 'openpnp_get_native_artifact', arguments: { artifact_id: 'frame' } });
  assert.equal(reply.content[1].type, 'image'); assert.equal(reply.content[1].data, artifact.base64); assert.equal(reply.structuredContent.base64, undefined); assert.equal(reply.structuredContent.sha256, hash(data));
  artifact.sha256 = '0'.repeat(64); const invalid = await consumer.callTool({ name: 'openpnp_get_native_artifact', arguments: { artifact_id: 'frame' } }); assert.equal(invalid.isError, true);
});

test('large native ZIP/JSON base64 is absent inline and decoded-byte pagination returns exact original binary', async t => {
  for (const mime_type of ['application/zip', 'application/json']) {
    const data = Buffer.from(Array.from({ length: 100000 }, (_, i) => i % 256)); const artifact = { artifact_id: 'binary', mime_type, sha256: hash(data), size: data.length, base64: data.toString('base64') };
    const store = await storeFor(t); const { consumer, calls } = await sdk(t, artifact, store); const reply = await consumer.callTool({ name: 'openpnp_get_native_artifact', arguments: { artifact_id: 'binary' } });
    assert.notEqual(typeof reply.structuredContent.base64, 'string'); assert.equal(reply.structuredContent.sha256, artifact.sha256);
    const response_id = reply.structuredContent.response_retention.response_id; let offset = 0; const chunks = [];
    do { const next = await page(consumer, { response_id, pointer: '/base64', format: 'decoded-base64', offset, limit: 8192 }); chunks.push(Buffer.from(next.data, 'base64')); assert.equal(next.selected_sha256, artifact.sha256); offset = next.next_offset; if (next.eof) break; } while (true);
    assert.deepEqual(Buffer.concat(chunks), data); assert.equal(calls.length, 2);
  }
});

test('large native error retains error code and SDK isError while details remain recoverable', async t => {
  const error = new PluginError('NATIVE_KNOWN_FAILURE', 'Known native failure', { diagnostics: Array.from({ length: 1000 }, (_, i) => ({ index: i, message: 'evidence'.repeat(20) })) });
  const store = await storeFor(t); const { consumer } = await sdk(t, error, store);
  const result = await consumer.callTool({ name: 'openpnp_get_operation', arguments: { operation_id: 'failed' } }); assert.equal(result.isError, true); assert.equal(result.structuredContent.error.code, 'NATIVE_KNOWN_FAILURE'); assert.equal(result.structuredContent.response_retention.details_available, true);
});

test('small replies remain byte-identical and require no retention', async t => {
  const store = await storeFor(t, { maxFiles: 0 }); const value = { state: 'accepted', operation_id: 'small' }; assert.equal(await boundedReply(value, store), value); assert.deepEqual(await readdir(store.root), []);
});

test('wide/deep summaries and escaped long keys still honor the inline byte ceiling', async t => {
  const store = await storeFor(t); const many = Object.fromEntries(Array.from({ length: 200 }, (_, i) => [`field_${i}`, Object.fromEntries(Array.from({ length: 100 }, (_, j) => [`value_${j}`, '\\"😀'.repeat(100)]))]));
  const value = { operation_id: 'known-op', request_id: 'known-request', state: 'succeeded', result: { state: 'completed', placed: 10000, requested: 10000, ...many }, ...many };
  const summary = await boundedReply(value, store); assert.ok(Buffer.byteLength(JSON.stringify(summary)) <= INLINE_JSON_BYTES); assert.equal(summary.state, 'succeeded'); assert.equal(summary.operation_id, 'known-op'); assert.equal(summary.result.placed, 10000);
  const unusual = { ['"'.repeat(50000)]: 'x'.repeat(50000) }; const { response_id } = await store.putBytes(serialized(unusual)); const result = await store.readPage({ response_id });
  assert.equal(result.oversized_item.read_parent_bytes, true); assert.ok(Buffer.byteLength(JSON.stringify(result)) <= INLINE_JSON_BYTES);
});

test('adversarial insertion order cannot replace a known outcome or IDs after detail budget exhaustion, including storage failure', async t => {
  const branch = Object.fromEntries(Array.from({ length: 40 }, (_, i) => [`branch${i}`, Object.fromEntries(Array.from({ length: 40 }, (_, j) => [`n${j}`, j]))]));
  const result = { branches: branch, state: 'completed', requested: 10000, placed: 10000 };
  const operation = { result, state: 'succeeded', operation_id: 'native-op', request_id: 'native-request', bridge_instance_id: 'native-instance' };
  const wrapped = { result: { branches: branch, deep: branch }, operation };
  for (const maxFiles of [0, 10]) {
    const store = await storeFor(t, { maxFiles });
    for (const value of [operation, wrapped]) {
      const reply = await boundedReply(value, store, { forceRetention: true }); const known = value === operation ? reply : reply.operation;
      assert.equal(known.state, 'succeeded'); assert.equal(known.operation_id, 'native-op'); assert.equal(known.request_id, 'native-request'); assert.equal(known.bridge_instance_id, 'native-instance');
      assert.equal(known.result.state, 'completed'); assert.equal(known.result.placed, 10000); assert.equal(known.result.requested, 10000);
      assert.equal(reply.response_retention.details_available, maxFiles !== 0); assert.ok(Buffer.byteLength(JSON.stringify(reply)) <= INLINE_JSON_BYTES);
    }
  }
});
