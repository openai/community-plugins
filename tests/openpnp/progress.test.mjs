// Native-response-shaped adapter fixtures; these tests do not execute OpenPnP.
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, readdir } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { Client } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { InMemoryTransport } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/inMemory.js';
import { createServer } from '../../src/openpnp/node/server.mjs';
import { OpenPnpRuntime, PINNED_UPSTREAM } from '../../src/openpnp/node/runtime.mjs';
import { ResponseStore, boundedReply, INLINE_JSON_BYTES } from '../../src/openpnp/node/responses.mjs';
import { projectProgress } from '../../src/openpnp/node/progress.mjs';
import { readCompleteResponse } from '../../scripts/openpnp-read-response.mjs';

const tools = ['openpnp_get_status', 'openpnp_get_operation', 'openpnp_get_request_status'];
const bytes = value => Buffer.from(JSON.stringify(value));
async function storeFor(t) { const root = await mkdtemp(path.join(os.tmpdir(), 'openpnp-progress-')); t.after(() => rm(root, { recursive: true, force: true })); return new ResponseStore(root); }
const rows = count => Array.from({ length: count }, (_, index) => ({ reference: `R${index + 1}`, board_id: 'B1', part_id: 'R0603-1K', placed: index < 42, independently_inspected: false, machine_pose: { x: index, y: 1, z: 0, rotation: 0, units: 'mm' } }));
function fixture(count = 10000) {
  const placements = rows(count);
  const operation = { operation_id: 'original-operation', request_id: 'original-request', bridge_instance_id: 'same-native-instance', state: 'running', updated_at: '2026-09-11T00:00:00Z', native_steps_started: 500, native_effect_pending: true,
    result: { state: 'paused', job_id: 'job', requested: count, placed: 42, independently_inspected: 0, placements },
    native_action_ledger: { scope: { operation_id: 'original-operation', job_id: 'job', boards: placements }, events_committed: 300, actions_started: 120, native_placed_observed: 42, independently_verified: 0, durability_fault: false, unresolved_action_fault: false, pending_actions: [{ action_id: 'action-121' }], outcomes: { 'feed:native_hook_returned': 43 } } };
  const status = { bridge_instance_id: operation.bridge_instance_id, machine_id: 'machine', config_revision: 'cfg-1', ownership_epoch: 7, through_sequence: 301, job_id: 'job', job_revision: 'a'.repeat(64), board_load_revision: 'load-2', native_busy: true, active_operation_id: operation.operation_id, job_state: 'running', journal_fault: false, configuration_fault: false,
    job_progress: { state: 'running', requested: count, placed: 43, independently_inspected: 0, observed_at: '2026-09-11T00:00:01Z', through_sequence: 299 },
    machine: { snapshot_at: '2026-09-11T00:00:01Z', enabled: true, homed: true, settings: { parts: placements }, feeders: placements },
    board_loads: { board_load_revision: 'load-2', authority: 'native-simulator', physical_load_verified: false, restart_presence_confirmation_required: false, pending_changes: 0, roots: [{ id: 'B1' }], loads: [{ placed_history: placements }] },
    native_action_ledger: operation.native_action_ledger };
  return { operation, status };
}
async function sdk(t, native, store) {
  const calls = [];
  const client = { async call(name, args, options) {
    calls.push({ name, args, options });
    if (name === 'openpnp_get_capabilities') return { schema_version: 1, bridge_version: '0.1.0', upstream_commit: PINNED_UPSTREAM, tools };
    assert.equal(Object.hasOwn(args, 'view'), false, 'MCP-only selection must never enter the native request');
    return name === tools[0] ? native.status : name === tools[1] ? native.operation : { found: true, operation: native.operation };
  } };
  const runtime = new OpenPnpRuntime({ client, responses: store }); const server = createServer(runtime);
  const [a, b] = InMemoryTransport.createLinkedPair(); const consumer = new Client({ name: 'progress-regression', version: '1' });
  await server.connect(a); await consumer.connect(b); t.after(async () => { await consumer.close(); await server.close(); });
  const call = async (name, args = {}) => {
    const response = await consumer.callTool({ name, arguments: args }); assert.notEqual(response.isError, true, JSON.stringify(response.structuredContent)); return response.structuredContent;
  };
  return { runtime, calls, consumer, call };
}

test('changing full polling replies reproduce the exact default 128-file capacity failure without evicting earlier evidence', async t => {
  const store = await storeFor(t), native = fixture(200); let first;
  assert.ok(bytes(native.operation).length > INLINE_JSON_BYTES);
  for (let index = 0; index < 128; index++) {
    native.operation.native_steps_started = index;
    const retained = await boundedReply(native.operation, store);
    assert.equal(retained.response_retention.details_available, true);
    if (index === 0) first = { id: retained.response_retention.response_id, bytes: bytes(native.operation) };
  }
  native.operation.native_steps_started = 128;
  const full = await boundedReply(native.operation, store);
  assert.equal(full.state, 'running'); assert.equal(full.operation_id, 'original-operation');
  assert.equal(full.response_retention.storage_error.code, 'RESPONSE_STORE_CAPACITY');
  assert.equal(full.response_retention.details_unavailable, true);
  assert.equal((await readdir(store.directory)).length, 128);
  assert.deepEqual(await store.readBytes(first.id), first.bytes);
});

test('official SDK 450 changing large progress polls retain no full snapshots; paused and terminal full reads remain exact and offline-pageable', async t => {
  const store = await storeFor(t), native = fixture(), { call, calls } = await sdk(t, native, store);
  let largest = 0;
  for (let index = 0; index < 150; index++) {
    native.operation.native_steps_started++; native.status.through_sequence++; native.status.job_progress.placed++;
    for (const name of tools) {
      const args = name === tools[0] ? {} : name === tools[1] ? { operation_id: 'original-operation' } : { request_id: 'original-request' };
      const result = await call(name, { ...args, view: 'progress' });
      assert.equal(result.response_view.name, 'progress'); assert.equal(result.response_view.retained_snapshot, false);
      assert.equal(result.truncated, undefined); assert.equal(result.response_retention, undefined);
      largest = Math.max(largest, bytes(result).length);
      const op = name === tools[2] ? result.operation : result;
      if (name !== tools[0]) { assert.equal(op.operation_id, 'original-operation'); assert.equal(op.request_id, 'original-request'); assert.equal(op.state, 'running'); assert.equal(op.native_steps_started, native.operation.native_steps_started); assert.equal(op.result.placed, 42); assert.equal(op.result.state, 'paused'); assert.match(op.result_observation, /earlier pause/); }
      else { assert.equal(result.job_progress.placed, native.status.job_progress.placed); assert.equal(result.job_progress.observed_at, native.status.job_progress.observed_at); assert.equal(result.job_progress.through_sequence, 299); assert.equal(result.through_sequence, native.status.through_sequence); }
    }
  }
  assert.ok(largest < INLINE_JSON_BYTES); assert.deepEqual(await readdir(store.root), []);
  assert.equal(calls.filter(row => row.name !== 'openpnp_get_capabilities').length, 450);
  assert.equal(calls.some(row => row.options?.mutating), false);
  for (const state of ['paused', 'succeeded']) {
    native.operation.state = state;
    native.operation.result.state = state === 'paused' ? 'paused' : 'completed';
    const expected = structuredClone(native.operation);
    const full = await call(tools[1], { operation_id: 'original-operation', view: 'full' });
    assert.equal(full.response_retention.details_available, true);
    const before = calls.length;
    const reconstructed = await readCompleteResponse(full, args => call('openpnp_read_response_page', args));
    assert.deepEqual(reconstructed, expected); assert.equal(calls.length, before, 'reconstructing retained evidence is offline');
  }
  assert.equal((await readdir(store.directory)).length, 2);
  assert.equal(native.operation.result.placements.length, 10000);
});

test('progress defaults do not change full raw calls, strip only the MCP view, and reject unsupported selections before native dispatch', async t => {
  const native = fixture(1), store = await storeFor(t), { runtime, calls } = await sdk(t, native, store);
  assert.equal(await runtime.call(tools[0]), native.status);
  assert.equal(await runtime.call(tools[0], { view: 'full' }), native.status);
  assert.deepEqual(calls.at(-1).args, {});
  const before = calls.length;
  for (const args of [{ view: 'summary' }, { view: null }, { view: 'progress', path: '/tmp/raw' }]) await assert.rejects(runtime.call(tools[0], args), { code: 'INVALID_ARGUMENT' });
  await assert.rejects(runtime.call('openpnp_start_job', { request_id: 'id', session_id: 'lease', job_id: 'job', view: 'progress' }), { code: 'INVALID_ARGUMENT' });
  assert.equal(calls.length, before);
});

test('known failure, unknown outcome, lease state and native GUI faults survive progress selection with exact codes and no fabricated freshness', () => {
  const op = { state: 'outcome_unknown', operation_id: 'original', request_id: 'request', result: { code: 'NATIVE_ACTION_OUTCOME_UNKNOWN', message: 'x'.repeat(5000), effect_outcome_unknown: true, repeat_action_performed: false }, native_action_recovery: { operation_id: 'original', events_committed: 7, native_hook_outcomes: 3, requires_reconciliation: true, physical_effect_verification: false, unresolved_actions: [{}], safety_gaps: [{}] } };
  const projection = projectProgress(tools[1], op, { operation_id: 'original' });
  assert.equal(projection.state, 'outcome_unknown'); assert.equal(projection.result.code, op.result.code); assert.equal(projection.result.repeat_action_performed, false);
  assert.equal(projection.result.message_details.characters, 5000); assert.equal(projection.native_action_recovery.unresolved_actions_count, 1);
  assert.equal(projection.native_action_recovery.native_hook_outcomes, 3);
  const lease = { found: true, session_receipt: { session_id: 'expired', ownership_epoch: 5, active: false, expires_in_ms: 0, lease_state: 'expired-or-revoked', state: 'succeeded' } };
  assert.deepEqual(projectProgress(tools[2], lease, { request_id: 'original' }).session_receipt, lease.session_receipt);
  const machine = { native_busy: true, journal_fault: true, configuration_fault: false, gui_ownership: { state: 'fenced', local_grant: false, native_ownership_held: true, last_error: { type: 'IllegalStateException', message: 'native failure' } } };
  const status = projectProgress(tools[0], machine);
  assert.deepEqual(status.gui_ownership, machine.gui_ownership); assert.equal(status.journal_fault, true);
  assert.equal(Object.hasOwn(status, 'observed_at'), false); assert.equal(Object.hasOwn(status, 'job_progress'), false);
  assert.equal(Object.hasOwn(status, 'session_id'), false);
});

test('progress remains available in a full retained-evidence store, rejects malformed scalar fields and never silently archives omitted details', async t => {
  const store = await storeFor(t); store.maxFiles = 0;
  const native = fixture(), { call } = await sdk(t, native, store);
  const progress = await call(tools[0], { view: 'progress' });
  assert.equal(progress.response_view.retained_snapshot, false); assert.equal(progress.native_busy, true);
  assert.deepEqual(await readdir(store.root), []);
  assert.throws(() => projectProgress(tools[1], { operation_id: [], state: 'running' }), { code: 'INVALID_PROGRESS_RESPONSE' });
  assert.throws(() => projectProgress(tools[1], { operation_id: 'x'.repeat(5000), state: 'running' }), { code: 'INVALID_PROGRESS_RESPONSE' });
  const none = projectProgress(tools[2], { found: false, request_id: 'missing' }, { request_id: 'missing' });
  assert.equal(none.found, false); assert.equal(none.operation, undefined);
});

test('resource and complete small-feeder progress survives more than the archive capacity without retaining snapshots', async t => {
  const native = fixture(), store = await storeFor(t);
  store.maxFiles = 0;
  native.status.metrics = { uptime_ms: 0, heap_used_bytes: 100, heap_committed_bytes: 200, heap_max_bytes: 300,
    event_buffer_count: 5000, operation_count: 5, request_count: 6, plan_count: 0, artifact_count: 0,
    artifact_metadata_cache_count: 0, artifact_bytes: 0, artifact_memory_content_bytes: 0,
    journal_bytes: 0, job_count_recomputations: 0, machine_snapshot_refreshes: 0, private_extra: 'omitted' };
  native.status.machine = { snapshot_at: '2026-09-11T00:00:00Z', feeders: [{ id: 'tray', part_id: 'resistor',
    class: 'org.openpnp.machine.reference.feeder.ReferenceTrayFeeder', enabled: true, capacity: 10000,
    feed_count: 0, virtual_supply: true, unbounded_extra: 'x'.repeat(100000) }] };
  const { call } = await sdk(t, native, store);
  let first;
  for (let i = 0; i < 150; i++) {
    native.status.metrics.uptime_ms = i * 1000;
    native.status.metrics.journal_bytes = i * 100;
    native.status.machine.feeders[0].feed_count = i;
    const result = await call(tools[0], { view: 'progress' });
    first ??= result;
    assert.equal(result.metrics.uptime_ms, i * 1000);
    assert.equal(result.metrics.journal_bytes, i * 100);
    assert.equal(result.metrics.private_extra, undefined);
    assert.equal(result.machine.feeders_complete, true);
    assert.equal(result.machine.feeders_count, 1);
    assert.equal(result.machine.feeders[0].feed_count, i);
    assert.equal(result.machine.feeders[0].unbounded_extra, undefined);
    assert.equal(result.machine.snapshot_at, native.status.machine.snapshot_at);
    assert.equal(result.response_retention, undefined);
    assert.ok(bytes(result).length < INLINE_JSON_BYTES);
  }
  assert.equal(first.machine.feeders[0].feed_count, 0, 'previous progress remains an observation of its own call');
  assert.deepEqual(await readdir(store.root), []);
});

test('large feeder progress explicitly marks count/byte truncation and resource metrics reject invented values', () => {
  const feeder = i => ({ id: `tray-${i}`, class: 'Tray', part_id: 'part', enabled: true, capacity: 20, feed_count: i, virtual_supply: true });
  for (const count of [0, 1, 16, 17, 1000]) {
    const result = projectProgress(tools[0], { machine: { feeders: Array.from({ length: count }, (_, i) => feeder(i)) } });
    assert.equal(result.machine.feeders_count, count);
    assert.equal(result.machine.feeders.length, Math.min(16, count));
    assert.equal(result.machine.feeders_complete, count <= 16);
    assert.ok(bytes(result).length < INLINE_JSON_BYTES);
  }
  const huge = Array.from({ length: 16 }, (_, i) => ({ ...feeder(i), id: 'i'.repeat(1000), class: 'c'.repeat(1000), part_id: 'p'.repeat(1000) }));
  const truncated = projectProgress(tools[0], { machine: { feeders: huge } });
  assert.equal(truncated.machine.feeders_complete, false);
  assert.equal(truncated.machine.feeders_count, 16);
  assert.ok(truncated.machine.feeders.length < 16);
  assert.ok(bytes(truncated.machine.feeders).length <= 8192);
  for (const invalid of [-1, 0.5, Number.MAX_SAFE_INTEGER + 1, '123', true]) {
    assert.throws(() => projectProgress(tools[0], { metrics: { journal_bytes: invalid } }), { code: 'INVALID_PROGRESS_RESPONSE' });
  }
  assert.equal(Object.hasOwn(projectProgress(tools[0], {}), 'metrics'), false);
  assert.throws(() => projectProgress(tools[0], { machine: { feeders: [null] } }), { code: 'INVALID_PROGRESS_RESPONSE' });
});

test('native unavailable journal size preserves publication fault and known body outcome in progress', () => {
 const value = projectProgress('openpnp_get_status', { journal_fault: true, metrics: { journal_bytes: null, uptime_ms: 7 },
  native_submission: { operation_id: 'operation', phase: 'publication-fault', publication_fault: { code: 'NATIVE_COMPLETION_PUBLICATION_FAILED', durable: false, known_body_outcome: { state: 'succeeded', captured: true } } } });
 assert.equal(value.metrics.journal_bytes, null); assert.equal(value.journal_fault, true);
 assert.equal(value.native_submission.publication_fault.known_body_outcome.state, 'succeeded');
 assert.equal(value.native_submission.publication_fault.code, 'NATIVE_COMPLETION_PUBLICATION_FAILED');
});
