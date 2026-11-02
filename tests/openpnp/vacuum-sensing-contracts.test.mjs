// SPDX-License-Identifier: Apache-2.0
// Schema/transport fixtures; native sensing and physical qualification are separate.
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import Ajv from '../../src/openpnp/node/node_modules/ajv/dist/ajv.js';
import { OpenPnpRuntime, PINNED_UPSTREAM } from '../../src/openpnp/node/runtime.mjs';
import { BridgeClient } from '../../src/openpnp/node/client.mjs';
import { TOOL_DEFINITIONS, TOOL_BY_NAME } from '../../src/openpnp/node/contracts.mjs';
import { NATIVE_CHANGE_SCHEMAS } from '../../src/openpnp/node/settings-contracts.mjs';
import { createServer } from '../../src/openpnp/node/server.mjs';
import { Client } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { InMemoryTransport } from '../../src/openpnp/node/node_modules/@modelcontextprotocol/sdk/dist/esm/inMemory.js';
import { launchSimulator } from '../../plugins/openpnp/scripts/openpnp.mjs';
const names = ['openpnp_measure_sensor', 'openpnp_verify_part_state'];
const request = '781abcde-1111-4222-8333-000000000001';
const base = () => ({ request_id: request, session_id: 'lease', expected_config_revision: 'cfg-4', nozzle_id: 'N1' });
const profile = { profile: 'native-vacuum-sensing-v1', available: true, source_profile: 'controlled-native-vacuum-v1', simulation_only: true, hardware_qualified: false };
function fixture(change = {}) {
  const calls = [];
  const caps = { schema_version: 1, bridge_version: '0.1.0', upstream_commit: PINNED_UPSTREAM, tools: names, vacuum_sensing: profile, ...change };
  return { calls, runtime: new OpenPnpRuntime({ client: { async call(name, args, options) {
    calls.push({ name, args, options });
    return name === 'openpnp_get_capabilities' ? caps : { operation_id: 'observed-operation', request_id: args.request_id, state: 'accepted' };
  } } }) };
}

test('sensing operations retain request/revision/nozzle guards and asynchronous mutation semantics', async () => {
  assert.equal(TOOL_DEFINITIONS.length, 61);
  const { calls, runtime } = fixture();
  for (const [name, args] of [[names[0], base()], [names[0], { ...base(), samples: 32 }], [names[1], { ...base(), state: 'part_on' }], [names[1], { ...base(), state: 'part_off' }]]) {
    const original = structuredClone(args); const result = await runtime.call(name, args);
    assert.equal(result.state, 'accepted'); assert.deepEqual(args, original);
    assert.deepEqual(calls.at(-1), { name, args: original, options: { mutating: true } });
    assert.equal(TOOL_BY_NAME.get(name).annotations.readOnlyHint, false);
    assert.equal(TOOL_BY_NAME.get(name).annotations.idempotentHint, false);
  }
  assert.equal(calls.length, 8);
});

test('invalid sensing commands are refused before any capability lookup or native dispatch', async () => {
  const { calls, runtime } = fixture();
  const bad = [];
  for (const field of Object.keys(base())) { const args = base(); delete args[field]; bad.push(args); }
  for (const [field, value] of [['request_id', 'new-request'], ['request_id', request.toUpperCase()], ['nozzle_id', '../N1'], ['expected_config_revision', 'load-4'], ['samples', 0], ['samples', 33], ['samples', 1.5], ['samples', '1'], ['samples', NaN], ['samples', Infinity], ['sensor_id', 'arbitrary'], ['raw_command', 'M115']]) bad.push({ ...base(), [field]: value });
  for (const args of bad) await assert.rejects(runtime.call(names[0], args), { code: 'INVALID_ARGUMENT' });
  for (const state of [undefined, 'empty', true, null]) await assert.rejects(runtime.call(names[1], { ...base(), state }), { code: 'INVALID_ARGUMENT' });
  await assert.rejects(runtime.call(names[1], { ...base(), state: 'part_off', clear_fault: true }), { code: 'INVALID_ARGUMENT' });
  assert.equal(calls.length, 0);
});

test('tool names alone cannot authorize sensing without the declared active simulator source profile', async () => {
  const variants = [undefined, { ...profile, available: false }, { ...profile, profile: 'arbitrary' }, { ...profile, source_profile: 'native-driver' }, { ...profile, simulation_only: false }, { ...profile, hardware_qualified: true }];
  for (const vacuum_sensing of variants) {
    const { calls, runtime } = fixture({ vacuum_sensing });
    for (const [name, args] of [[names[0], base()], [names[1], { ...base(), state: 'part_off' }]]) await assert.rejects(runtime.call(name, args), { code: 'UNSUPPORTED_CAPABILITY' });
    assert.ok(calls.every(x => x.name === 'openpnp_get_capabilities'));
  }
  const { calls, runtime } = fixture({ tools: ['openpnp_get_status'] });
  await assert.rejects(runtime.call(names[0], base()), { code: 'UNSUPPORTED_CAPABILITY' }); assert.equal(calls.length, 1);
});

const setting = () => ({ type: 'set_vacuum_sensing_settings', nozzle_id: 'N1', nozzle_tip_id: 'TIP1', vacuum_sense_actuator_id: 'SENSE1', vacuum_actuator_id: 'VALVE1', reading_units: 'native-actuator-units', threshold_provenance: 'configured-thresholds', method_part_on: 'Absolute', method_part_off: 'Absolute', part_on_low: 50, part_on_high: 100, part_off_low: -5, part_off_high: 5, part_on_check_after_pick: true, part_on_check_align: true, part_on_check_before_place: true, part_off_check_after_place: true, part_off_check_before_pick: true, part_off_probe_ms: 100, part_off_dwell_ms: 0 });
test('typed sensing settings require every explicit field and reject unbounded or unsupported values', () => {
  const schema = NATIVE_CHANGE_SCHEMAS.find(x => x.properties.type.const === setting().type); assert.ok(schema);
  const validate = new Ajv({ strict: true, coerceTypes: false }).compile(schema);
  assert.equal(validate(setting()), true);
  assert.equal(validate({ ...setting(), method_part_on: 'None', method_part_off: 'None', vacuum_sense_actuator_id: null, part_on_low: 0, part_on_high: 0, part_off_low: 0, part_off_high: 0 }), true);
  for (const field of Object.keys(setting())) { const value = setting(); delete value[field]; assert.equal(validate(value), false, field); }
  for (const [field, value] of [['part_on_low', -1000001], ['part_off_high', Infinity], ['method_part_on', 'Difference'], ['reading_units', 'kPa'], ['threshold_provenance', 'hardware-calibrated'], ['part_off_probe_ms', 1001], ['part_off_dwell_ms', 0.5], ['part_on_check_align', 1], ['nozzle_id', 'N 1'], ['callback', 'script']]) assert.equal(validate({ ...setting(), [field]: value }), false, field);
});

test('official MCP SDK carries sensing operations and rejects malformed requests (transport fixture)', async t => {
  const { calls, runtime } = fixture(); const server = createServer(runtime); const client = new Client({ name: 'vacuum-contract-fixture', version: '1' }); const [a, b] = InMemoryTransport.createLinkedPair();
  t.after(async () => { await client.close(); await server.close(); }); await Promise.all([server.connect(b), client.connect(a)]);
  const catalog = await client.listTools(); assert.equal(catalog.tools.length, 61); assert.ok(names.every(name => catalog.tools.some(x => x.name === name)));
  let result = await client.callTool({ name: names[1], arguments: { ...base(), state: 'part_off' } }); assert.notEqual(result.isError, true); assert.equal(result.structuredContent.operation_id, 'observed-operation');
  const before = calls.length; result = await client.callTool({ name: names[0], arguments: { ...base(), samples: 33 } }); assert.equal(result.isError, true); assert.equal(result.structuredContent.error.code, 'INVALID_ARGUMENT'); assert.equal(calls.length, before);
});

test('lost sensing receipt remains unknown and is never automatically replayed (HTTP transport fixture)', async t => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'vacuum-client-')); t.after(() => rm(directory, { recursive: true, force: true }));
  await writeFile(path.join(directory, 'token'), 'T'.repeat(48), { mode: 0o600 }); await writeFile(path.join(directory, 'connection.json'), JSON.stringify({ url: 'http://127.0.0.1:54321/', tokenFile: path.join(directory, 'token') }), { mode: 0o600 });
  const valid = { operation_id: '781abcde-1111-4222-8333-000000000002', request_id: request, config_revision: 'cfg-4', state: 'accepted' };
  for (const result of [{ value: 75 }, { ...valid, request_id: 'other-request' }, { ...valid, config_revision: 'cfg-5' }, { ...valid, operation_id: 'unbound' }]) {
    let calls = 0; const client = new BridgeClient({ connectionFile: path.join(directory, 'connection.json'), fetchImpl: async () => { calls++; return new Response(JSON.stringify({ result })); } });
    await assert.rejects(client.call(names[1], { ...base(), state: 'part_off' }, { mutating: true }), error => { assert.equal(error.code, 'OUTCOME_UNKNOWN'); assert.equal(error.details.request_id, request); return true; }); assert.equal(calls, 1);
  }
  const client = new BridgeClient({ connectionFile: path.join(directory, 'connection.json'), fetchImpl: async () => new Response(JSON.stringify({ result: valid })) });
  assert.deepEqual(await client.call(names[1], { ...base(), state: 'part_off' }, { mutating: true }), valid);
});

test('sensing scenario selection is bounded and exclusive to its simulator profile before launch', async () => {
  for (const args of [{ profile: 'native-simulator', sensingScenario: 'success' }, { profile: 'sustained-workload', sensingScenario: 'success' }, { profile: 'vacuum-sensing', sensingScenario: 'script:value=100' }, { profile: 'vacuum-sensing', sensingScenario: '' }]) {
    await assert.rejects(launchSimulator({ openpnpHome: '/not-used', stateDir: '/not-used', ...args }), /--sensing-scenario requires/);
  }
});
