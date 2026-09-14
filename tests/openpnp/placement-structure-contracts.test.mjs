import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { OpenPnpRuntime, PINNED_UPSTREAM } from '../../src/openpnp/node/runtime.mjs';
import { BridgeClient } from '../../src/openpnp/node/client.mjs';
import { TOOL_BY_NAME } from '../../src/openpnp/node/contracts.mjs';

const methods = ['openpnp_plan_placement_structure', 'openpnp_apply_placement_structure'];
const envelope = { request_id: 'structure-request', session_id: 'session', expected_config_revision: 'cfg-7', job_id: 'job', expected_job_revision: 'a'.repeat(64), expected_board_load_revision: 'load-12' };
const addition = () => ({ action: 'add', scope: 'job_instance', holder_instance_id: 'P1⇒nested⇒B1', placement_id: 'R33', placement: {
  location: { frame: 'holder', units: 'mm', x: 25.4, y: -12.7, z: 0, rotation: -30 },
  side: 'Bottom', type: 'Placement', part_id: 'R0805-1K', enabled: false, error_handling: 'Alert', comments: '', rank: 0,
} });
const removal = () => ({ action: 'remove', scope: 'job_shared_definition', holder_instance_id: 'P1⇒nested⇒B1', placement_id: 'R2' });
const plan = () => ({ ...envelope, changes: [addition(), removal()] });
function fixture(advertised = methods) {
  const calls = [];
  const runtime = new OpenPnpRuntime({ client: { async call(name, args, options) {
    calls.push({ name, args, options });
    if (name === 'openpnp_get_capabilities') return { schema_version: 1, bridge_version: '0.1.0', upstream_commit: PINNED_UPSTREAM, tools: advertised };
    return { operation_id: 'fixture-operation', request_id: args.request_id, state: 'accepted' };
  } } });
  return { runtime, calls };
}

test('structure contracts dispatch reviewed local coordinates and exact revisions without inventing lineage authority', async () => {
  const { runtime, calls } = fixture();
  const inches = plan(); inches.changes[0].scope = 'job_shared_definition';
  inches.changes[0].placement.type = 'Fiducial';
  inches.changes[0].placement.location = { frame: 'holder', units: 'in', x: 1, y: -0.5, z: 0, rotation: 360 };
  for (const [name, args] of [[methods[0], plan()], [methods[0], inches], [methods[1], { ...envelope, plan_id: 'observed-plan' }]]) {
    const before = structuredClone(args);
    assert.equal((await runtime.call(name, args)).state, 'accepted');
    assert.deepEqual(calls.at(-1), { name, args: before, options: { mutating: true } });
    assert.deepEqual(args, before);
    assert.equal(TOOL_BY_NAME.get(name).annotations.readOnlyHint, false, 'Planning admits a durable native operation.');
    assert.equal(Object.hasOwn(calls.at(-1).args, 'unexecuted'), false);
  }
  assert.equal(calls.length, 6, 'One discovery read and one dispatch per request.');
});

test('structure schemas refuse incomplete creation, unsupported effects and client-supplied history before native dispatch', async () => {
  const { runtime, calls } = fixture();
  const rejected = [];
  for (const key of Object.keys(envelope)) { const input = plan(); delete input[key]; rejected.push(input); }
  for (const key of Object.keys(addition().placement)) { const input = plan(); delete input.changes[0].placement[key]; rejected.push(input); }
  for (const edit of [
    { action: 'rename' }, { placement_id: 'B⇒R1' }, { placement_id: 'α1' }, { placement_id: '-R1' },
    { scope: 'global_definition' }, { holder_instance_id: '' }, { placement_id: 'R\0x' },
    { unexecuted: true }, { source_file: '/tmp/board.xml' }, { new_id: 'R44' },
  ]) { const input = plan(); Object.assign(input.changes[0], edit); rejected.push(input); }
  for (const [key, value] of [
    ['enabled', 'false'], ['part_id', null], ['part_id', ''], ['type', 'Ignore'], ['side', 'bottom'],
    ['rank', 0.5], ['rank', 100001], ['comments', '\0'], ['comments', 'x'.repeat(2049)],
    ['error_handling', 'Skip'], ['placed', false], ['lineage_id', 'pretend-fresh'],
  ]) { const input = plan(); input.changes[0].placement[key] = value; rejected.push(input); }
  for (const [key, value] of [
    ['frame', 'machine'], ['units', 'mil'], ['x', 10001], ['z', 1001], ['rotation', 361],
    ['x', NaN], ['x', Infinity], ['x', -Infinity], ['y', null], ['z', '0'],
  ]) { const input = plan(); input.changes[0].placement.location[key] = value; rejected.push(input); }
  const incompletePose = plan(); delete incompletePose.changes[0].placement.location.rotation; rejected.push(incompletePose);
  const removeWithFields = plan(); removeWithFields.changes[1].placement = addition().placement; rejected.push(removeWithFields);
  rejected.push({ ...plan(), changes: [] }, { ...plan(), changes: Array.from({ length: 101 }, addition) },
    { ...plan(), clear_history: true }, { ...plan(), expected_lineage_revision: 1 }, { ...plan(), unexecuted: true });
  for (const args of rejected) await assert.rejects(runtime.call(methods[0], args), { code: 'INVALID_ARGUMENT' });
  for (const key of [...Object.keys(envelope), 'plan_id']) {
    const input = { ...envelope, plan_id: 'observed-plan' }; delete input[key];
    await assert.rejects(runtime.call(methods[1], input), { code: 'INVALID_ARGUMENT' });
  }
  await assert.rejects(runtime.call(methods[1], { ...envelope, plan_id: 'observed-plan', confirm_empty_history: true }), { code: 'INVALID_ARGUMENT' });
  assert.equal(calls.length, 0);
});

test('older bridge capability discovery prevents unsupported structure dispatch', async () => {
  const { runtime, calls } = fixture(['openpnp_get_status']);
  await assert.rejects(runtime.call(methods[0], plan()), { code: 'UNSUPPORTED_CAPABILITY' });
  await assert.rejects(runtime.call(methods[1], { ...envelope, plan_id: 'plan' }), { code: 'UNSUPPORTED_CAPABILITY' });
  assert.ok(calls.every(call => call.name === 'openpnp_get_capabilities'));
});

test('structure, single-step and portable export require operation receipts and retain unknown request identity (transport fixture)', async t => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'openpnp-structure-transport-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const connectionFile = path.join(root, 'connection.json'), tokenFile = path.join(root, 'token');
  await writeFile(tokenFile, 'T'.repeat(48), { mode: 0o600 });
  await writeFile(connectionFile, JSON.stringify({ url: 'http://127.0.0.1:54321/', tokenFile }), { mode: 0o600 });
  let result, calls = 0;
  const client = new BridgeClient({ connectionFile, fetchImpl: async () => { calls++; return new Response(JSON.stringify({ result })); } });
  for (const name of [...methods, 'openpnp_step_job', 'openpnp_export_portable_configuration']) {
    for (result of [{}, { plan_id: 'direct-plan', plan: {} }, { operation_id: 'op', state: 'not-a-state' }]) {
      const before = calls, request = `original-${name}`;
      await assert.rejects(client.call(name, { request_id: request }, { mutating: true }), error => {
        assert.equal(error.code, 'OUTCOME_UNKNOWN'); assert.equal(error.details.request_id, request); return true;
      });
      assert.equal(calls, before + 1, 'A malformed response causes no replay.');
    }
    result = { operation_id: 'op', state: 'accepted' };
    assert.deepEqual(await client.call(name, { request_id: `accepted-${name}` }, { mutating: true }), result);
  }
});
