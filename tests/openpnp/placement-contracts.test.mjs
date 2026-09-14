import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { OpenPnpRuntime, PINNED_UPSTREAM } from '../../src/openpnp/node/runtime.mjs';
import { BridgeClient } from '../../src/openpnp/node/client.mjs';
import { TOOL_BY_NAME } from '../../src/openpnp/node/contracts.mjs';
import { createServer } from '../../src/openpnp/node/server.mjs';
import { ResponseStore } from '../../src/openpnp/node/responses.mjs';
import { readCompleteResponse } from '../../scripts/openpnp-read-response.mjs';
import { Client } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { InMemoryTransport } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/inMemory.js';

const methods = ['get_board_loads', 'register_board_load', 'inspect_job', 'plan_placement_edits', 'apply_placement_edits'].map(name => `openpnp_${name}`);
const capabilities = { schema_version: 1, bridge_version: '0.1.0', upstream_commit: PINNED_UPSTREAM, tools: methods };
const envelope = { request_id: 'request', session_id: 'session', expected_config_revision: 'cfg-7', job_id: 'job', expected_job_revision: 'a'.repeat(64), expected_board_load_revision: 'load-12' };
const loadId = 'ddfb0a72-62b1-42c0-8224-788d095682d3';
const change = () => ({ scope: 'job_instance', holder_instance_id: 'P1⇒nested⇒A', placement_id: 'R1', set: { location: { frame: 'holder', units: 'mm', x: 25.4, y: -12.7, z: 0, rotation: -30 }, enabled: false } });
const plan = () => ({ ...envelope, changes: [change()] });
const load = () => ({ request_id: 'request', session_id: 'session', expected_config_revision: 'cfg-7', expected_board_load_revision: 'load-12', job_id: 'job', root_instance_id: 'P1', action: 'replace', side: 'top' });
function routingFixture(advertised = capabilities) {
  const calls = [];
  const runtime = new OpenPnpRuntime({ client: { async call(name, args, options) {
    calls.push({ name, args, options });
    return name === 'openpnp_get_capabilities' ? advertised : { operation_id: 'fixture-operation', state: 'accepted' };
  } } });
  return { runtime, calls };
}

test('placement/load contracts route exact typed identities and revisions as asynchronous operations', async () => {
  const { runtime, calls } = routingFixture();
  for (const [name, args] of [
    ['openpnp_inspect_job', { ...envelope, offset: 3, limit: 2, expected_source_fingerprint: 'b'.repeat(64) }],
    ['openpnp_plan_placement_edits', plan()], ['openpnp_apply_placement_edits', { ...envelope, plan_id: 'plan' }],
    ['openpnp_register_board_load', load()],
    ['openpnp_register_board_load', { ...load(), expected_load_id: loadId, action: 'flip', side: 'bottom' }],
    ['openpnp_register_board_load', { ...load(), expected_load_id: loadId, action: 'same-load' }],
  ]) {
    const input = structuredClone(args);
    assert.equal((await runtime.call(name, args)).state, 'accepted');
    assert.deepEqual(calls.at(-1), { name, args: input, options: { mutating: true } });
    assert.deepEqual(args, input);
    assert.equal(TOOL_BY_NAME.get(name).annotations.readOnlyHint, false, 'Admitted preview has a durable request receipt.');
  }
  await runtime.call('openpnp_get_board_loads', {});
  assert.deepEqual(calls.at(-1), { name: 'openpnp_get_board_loads', args: {}, options: { mutating: false } });
  assert.equal(calls.length, 14, 'Exactly one discovery read and one requested dispatch per call.');
});

test('placement schemas reject stale-shape inputs and arbitrary properties before any native request', async () => {
  const { runtime, calls } = routingFixture();
  const bad = [];
  for (const key of Object.keys(envelope)) { const args = plan(); delete args[key]; bad.push(args); }
  bad.push({ ...plan(), expected_job_revision: 'job-1' }, { ...plan(), expected_board_load_revision: 'cfg-12' }, { ...plan(), expected_config_revision: 'load-7' });
  for (const edit of [
    { set: {} }, { scope: 'global_definition' }, { holder_instance_id: '' }, { placement_id: 'A⇒R1' },
    { set: { location: { frame: 'machine', units: 'mm', x: 1, y: 2, z: 0, rotation: 0 } } },
    { set: { location: { frame: 'holder', units: 'mm', x: 1, y: 2, z: 0 } } },
    { set: { location: { frame: 'holder', units: 'mil', x: 1, y: 2, z: 0, rotation: 0 } } },
    { set: { enabled: 'false' } }, { set: { part_id: null } }, { set: { side: 'top' } },
    { set: { type: 'Ignore' } }, { set: { rank: 0.5 } }, { set: { rank: 100001 } },
    { set: { comments: '\0' } }, { set: { comments: 'x'.repeat(2049) } },
    { set: { error_handling: 'Skip' } }, { set: { placed: false } }, { eval: 'machine.enable()' },
  ]) bad.push({ ...plan(), changes: [{ ...change(), ...edit }] });
  for (const n of [NaN, Infinity, -Infinity, '2', null, 10001]) {
    const args = plan(); args.changes[0].set.location.x = n; bad.push(args);
  }
  for (const [field, value] of [['x', 10000 / 25.4 + 1], ['z', 1000 / 25.4 + 1], ['rotation', 361]]) {
    const args = plan(); args.changes[0].set.location.units = 'in'; args.changes[0].set.location[field] = value; bad.push(args);
  }
  bad.push({ ...plan(), changes: [] }, { ...plan(), changes: Array.from({ length: 1001 }, change) }, { ...plan(), raw_xml: '<job/>' });
  for (const args of bad) await assert.rejects(runtime.call('openpnp_plan_placement_edits', args), { code: 'INVALID_ARGUMENT' });
  for (const key of [...Object.keys(envelope), 'plan_id']) {
    const args = { ...envelope, plan_id: 'plan' }; delete args[key];
    await assert.rejects(runtime.call('openpnp_apply_placement_edits', args), { code: 'INVALID_ARGUMENT' });
  }
  for (const extra of [{ offset: -1 }, { offset: 10001 }, { offset: 1.5 }, { limit: 0 }, { limit: 201 }, { limit: '1' }, { expected_source_fingerprint: 'stale' }, { include_private_xml: true }])
    await assert.rejects(runtime.call('openpnp_inspect_job', { ...envelope, ...extra }), { code: 'INVALID_ARGUMENT' });
  assert.equal(calls.length, 0);
});

test('explicit inches and complete supported set groups pass unchanged; native graph semantics remain native', async () => {
  const { runtime, calls } = routingFixture();
  const args = plan();
  args.changes[0].scope = 'job_shared_definition';
  args.changes[0].set = { location: { frame: 'holder', units: 'in', x: 1, y: -0.5, z: 0, rotation: 360 }, side: 'Bottom', type: 'Fiducial', enabled: true, part_id: 'R0805-1K', error_handling: 'Defer', comments: '', rank: -100000 };
  await runtime.call('openpnp_plan_placement_edits', args);
  assert.deepEqual(calls.at(-1).args, args, 'The adapter must not convert, mirror, resolve shared definitions, or rewrite native references.');
  assert.match(TOOL_BY_NAME.get('openpnp_plan_configuration').description, /vision-only patch/i);
});

test('load schema rejects missing guards/identities and implicit flip confirmation before native requests', async () => {
  const { runtime, calls } = routingFixture();
  const bad = [];
  for (const key of Object.keys(load())) { const args = load(); delete args[key]; bad.push(args); }
  bad.push({ ...load(), action: 'flip' }, { ...load(), action: 'same-load' }, { ...load(), expected_load_id: 'not-a-load-uuid' },
    { ...load(), root_instance_id: 'P1⇒A' }, { ...load(), side: 'Bottom' }, { ...load(), clear_history: true }, { ...load(), action: 'assume-present' });
  for (const args of bad) await assert.rejects(runtime.call('openpnp_register_board_load', args), { code: 'INVALID_ARGUMENT' });
  await assert.rejects(runtime.call('openpnp_get_board_loads', { job_id: 'other' }), { code: 'INVALID_ARGUMENT' });
  assert.equal(calls.length, 0);
});

test('an older bridge cannot accidentally advertise or receive newly added job tools', async () => {
  const { runtime, calls } = routingFixture({ ...capabilities, tools: ['openpnp_get_status'] });
  for (const [name, args] of [['openpnp_get_board_loads', {}], ['openpnp_register_board_load', load()], ['openpnp_inspect_job', envelope], ['openpnp_plan_placement_edits', plan()], ['openpnp_apply_placement_edits', { ...envelope, plan_id: 'plan' }]])
    await assert.rejects(runtime.call(name, args), { code: 'UNSUPPORTED_CAPABILITY' });
  const discovered = await runtime.call('openpnp_get_capabilities');
  assert.ok(methods.every(name => !discovered.tools.includes(name)));
  assert.ok(calls.every(call => call.name === 'openpnp_get_capabilities'));
});

test('new asynchronous receipt validation preserves lost-outcome request identity without replay (transport fixture)', async t => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'openpnp-placement-client-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const tokenFile = path.join(dir, 'token'); const connectionFile = path.join(dir, 'connection.json');
  await writeFile(tokenFile, 'T'.repeat(48), { mode: 0o600 });
  await writeFile(connectionFile, JSON.stringify({ url: 'http://127.0.0.1:54321/', tokenFile }), { mode: 0o600 });
  let calls = 0; let result;
  const client = new BridgeClient({ connectionFile, fetchImpl: async () => { calls++; return new Response(JSON.stringify({ result })); } });
  for (const name of methods.filter(name => name !== 'openpnp_get_board_loads')) {
    for (result of [{}, { plan_id: 'direct-plan', plan: {} }, { operation_id: 'operation', state: 'invented' }]) {
      const before = calls;
      await assert.rejects(client.call(name, { request_id: `original-${name}` }, { mutating: true }), error => {
        assert.equal(error.code, 'OUTCOME_UNKNOWN'); assert.equal(error.details.request_id, `original-${name}`); return true;
      });
      assert.equal(calls, before + 1);
    }
    result = { operation_id: 'operation', state: 'accepted' };
    assert.deepEqual(await client.call(name, { request_id: name }, { mutating: true }), result);
  }
});

test('official SDK preserves an async 10000-record placement preview through offline exact response pages (adapter fixture)', async t => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'openpnp-placement-sdk-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const responses = new ResponseStore(dir); const dispatched = [];
  const completed = { operation_id: 'preview-operation', request_id: 'preview-request', state: 'succeeded', result: { plan_id: 'preview-plan', expires_in_ms: 300000, plan: { affected_root_ids: ['P1'], side_effects_performed: false, hardware_qualified: false,
    changes: [{ affected: Array.from({ length: 10000 }, (_, i) => ({ holder_instance_id: 'P1⇒A', placement_id: `R${i + 1}`, before: { x: i / 100, enabled: true }, after: { x: i / 100 + 1, enabled: true }, placed: false })) }] } } };
  const runtime = new OpenPnpRuntime({ responses, client: { async call(name, args, options) {
    dispatched.push({ name, args, options });
    if (name === 'openpnp_get_capabilities') return { ...capabilities, tools: [...methods, 'openpnp_get_operation'] };
    if (name === 'openpnp_get_operation') return completed;
    return { operation_id: completed.operation_id, request_id: args.request_id, state: 'accepted' };
  } } });
  const server = createServer(runtime); const client = new Client({ name: 'placement-adapter-fixture', version: '1' });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  t.after(async () => { await client.close(); await server.close(); });
  await Promise.all([server.connect(serverTransport), client.connect(clientTransport)]);
  const catalog = await client.listTools(); assert.ok(methods.every(name => catalog.tools.some(tool => tool.name === name)));
  const receipt = await client.callTool({ name: 'openpnp_plan_placement_edits', arguments: { ...plan(), request_id: completed.request_id } });
  assert.equal(receipt.structuredContent.operation_id, completed.operation_id);
  const result = await client.callTool({ name: 'openpnp_get_operation', arguments: { operation_id: completed.operation_id } });
  assert.notEqual(result.isError, true); assert.equal(result.structuredContent.state, 'succeeded'); assert.equal(result.structuredContent.truncated, true);
  const nativeBeforePages = dispatched.length;
  const full = await readCompleteResponse(result.structuredContent, async args => {
    const page = await client.callTool({ name: 'openpnp_read_response_page', arguments: args });
    assert.notEqual(page.isError, true); return page.structuredContent;
  });
  assert.deepEqual(full, completed); assert.equal(dispatched.length, nativeBeforePages);
  assert.equal(dispatched.filter(call => call.name === 'openpnp_plan_placement_edits').length, 1);
  assert.equal(dispatched.filter(call => call.name === 'openpnp_apply_placement_edits').length, 0);
});
