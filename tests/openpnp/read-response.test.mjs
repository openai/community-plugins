import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { readCompleteResponse } from '../../scripts/openpnp-read-response.mjs';
import { ResponseStore, boundedReply } from '../../src/openpnp/node/responses.mjs';
import { dispatchMutationOnce } from '../../scripts/openpnp-soak.mjs';

const sha = value => createHash('sha256').update(value).digest('hex');
function descriptor(bytes) {
  const response_id = sha(bytes);
  return { truncated: true, original_json_bytes: bytes.length, response_retention: {
    response_id, sha256: response_id, bytes: bytes.length, details_available: true,
    page_tool: 'openpnp_read_response_page', root_pointer: '' } };
}
function onePage(bytes, args) {
  const chunk = bytes.subarray(args.offset, args.offset + args.limit);
  return { ...args, encoding: 'base64', total: bytes.length, count: chunk.length,
    next_offset: args.offset + chunk.length, eof: args.offset + chunk.length === bytes.length,
    data: chunk.toString('base64'), selected_sha256: sha(bytes) };
}

test('verification consumer reconstructs all 10000 retained placement records and ZIP bytes exactly', async t => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'openpnp-read-response-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const store = new ResponseStore(root);
  const actual = { operation_id: 'operation-with-10000-records', request_id: 'request-once', state: 'succeeded',
    result: { placed: 10000, independently_inspected: 0, placements: Array.from({ length: 10000 }, (_, index) => ({
      id: `R${index + 1}`, placed: true, independently_inspected: false,
      board: `board/${index % 4}`, comment: 'Résistance ⚙ — native model record',
      pose: { units: 'mm', x: index % 100, y: Math.floor(index / 100), rotation: 0 },
    })), archive: Buffer.alloc(65537, 0xab).toString('base64') } };
  const summary = await boundedReply(actual, store); assert.equal(summary.truncated, true);
  const requests = [];
  const reconstructed = await readCompleteResponse(summary, args => { requests.push(args); return store.readPage(args); });
  assert.deepEqual(reconstructed, actual);
  assert.equal(sha(JSON.stringify(reconstructed)), summary.response_retention.sha256);
  assert.equal(reconstructed.result.placements.length, 10000);
  assert.equal(new Set(reconstructed.result.placements.map(item => item.id)).size, 10000);
  assert.deepEqual(Buffer.from(reconstructed.result.archive, 'base64'), Buffer.alloc(65537, 0xab));
  assert.equal(requests.length, Math.ceil(Buffer.byteLength(JSON.stringify(actual)) / 16384));
  assert.ok(requests.every((args, index) => args.pointer === '' && args.format === 'bytes' && args.offset === index * 16384));
});

test('small replies need no page calls', async () => {
  const reply = { state: 'accepted', operation_id: 'op' };
  assert.equal(await readCompleteResponse(reply, () => assert.fail('Unexpected read')), reply);
});

test('tampered, discontinuous, empty, or misidentified pages fail with the known outcome retained', async () => {
  const bytes = Buffer.from(JSON.stringify({ state: 'succeeded', result: { placed: 10000 } }));
  const summary = { ...descriptor(bytes), state: 'succeeded' };
  const mutations = [
    page => ({ ...page, response_id: '0'.repeat(64) }),
    page => ({ ...page, selected_sha256: '0'.repeat(64) }),
    page => ({ ...page, pointer: '/result' }),
    page => ({ ...page, offset: 1 }),
    page => ({ ...page, total: bytes.length + 1 }),
    page => ({ ...page, next_offset: bytes.length - 1 }),
    page => ({ ...page, eof: false }),
    page => ({ ...page, count: 0, next_offset: 0, data: '', eof: false }),
    page => ({ ...page, data: `${page.data}\n` }),
    page => ({ ...page, data: Buffer.alloc(bytes.length, 32).toString('base64') }),
    page => ({ ...page, data: 'x'.repeat(32768) }),
    page => ({ ...page, count: 1, next_offset: 1, data: bytes.subarray(0, 1).toString('base64'), eof: false }),
  ];
  for (const change of mutations) {
    let calls = 0;
    await assert.rejects(readCompleteResponse(summary, args => { calls++; return change(onePage(bytes, args)); }),
      error => error.code === 'RESPONSE_DETAILS_INTEGRITY' && error.known_response === summary && error.native_action_repeated === false);
    assert.equal(calls, 1);
  }
});

test('unavailable detail retrieval never replays or relabels a known native mutation', async () => {
  const summary = { ...descriptor(Buffer.from('{"state":"succeeded"}')), operation_id: 'op', state: 'succeeded' };
  const tools = [];
  await assert.rejects(dispatchMutationOnce(async name => {
    tools.push(name);
    return readCompleteResponse(summary, () => { throw Object.assign(new Error('Disconnected read'), { code: 'MCP_TRANSPORT_ERROR' }); });
  }, 'apply_configuration', { request_id: 'original' }),
  error => error.code === 'RESPONSE_DETAILS_UNAVAILABLE' && error.known_response.state === 'succeeded');
  assert.deepEqual(tools, ['apply_configuration']);
  for (const retention of [{ details_available: false }, undefined]) {
    const result = { state: 'succeeded', truncated: true, response_retention: retention };
    await assert.rejects(readCompleteResponse(result, () => assert.fail('Unexpected read')),
      error => error.code === 'RESPONSE_DETAILS_UNAVAILABLE' && error.known_response === result);
  }
});

test('invalid retention envelopes fail before allocation or reads', async () => {
  const bytes = Buffer.from('{}'); const valid = descriptor(bytes);
  for (const changes of [{ bytes: 0 }, { bytes: 24 * 1024 ** 2 + 1 }, { bytes: 0.5 }, { bytes: NaN },
    { response_id: '../state' }, { sha256: '0'.repeat(64) }, { root_pointer: '/result' }, { page_tool: 'openpnp_start_job' }]) {
    const summary = { ...valid, response_retention: { ...valid.response_retention, ...changes } };
    await assert.rejects(readCompleteResponse(summary, () => assert.fail('Unexpected read')), { code: 'RESPONSE_DETAILS_INTEGRITY' });
  }
});

test('final bytes must be UTF-8 JSON with matching critical outcome fields', async () => {
  for (const bytes of [Buffer.from('not json'), Buffer.from('[]'), Buffer.from([0xff]), Buffer.from('{"state":"failed"}')]) {
    const summary = { ...descriptor(bytes), state: 'succeeded' };
    await assert.rejects(readCompleteResponse(summary, args => onePage(bytes, args)), { code: 'RESPONSE_DETAILS_INTEGRITY' });
  }
});

test('digest-valid details cannot contradict nested known operation, error, or count facts', async () => {
  const result = { found: true, operation: { operation_id: 'known-op', state: 'succeeded', result: { placed: 10000, valid: true } } };
  for (const alter of [
    value => { value.found = false; },
    value => { value.operation.operation_id = 'different-op'; },
    value => { value.operation.state = 'failed'; },
    value => { value.operation.result.placed = 9999; },
    value => { value.operation.result.valid = false; },
    value => { delete value.operation.result; },
  ]) {
    const altered = structuredClone(result); alter(altered);
    const bytes = Buffer.from(JSON.stringify(altered));
    const summary = { ...result, ...descriptor(bytes) };
    await assert.rejects(readCompleteResponse(summary, args => onePage(bytes, args)),
      error => error.code === 'RESPONSE_DETAILS_INTEGRITY' && error.known_response === summary);
  }
});
