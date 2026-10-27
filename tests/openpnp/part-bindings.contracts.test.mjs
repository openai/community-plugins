import test from 'node:test';
import assert from 'node:assert/strict';
import Ajv from '../../src/openpnp/node/node_modules/ajv/dist/ajv.js';
import { TOOL_BY_NAME } from '../../src/openpnp/node/contracts.mjs';
import { OpenPnpRuntime, PINNED_UPSTREAM } from '../../src/openpnp/node/runtime.mjs';

const validate = new Ajv({ strict: true, allErrors: true }).compile(TOOL_BY_NAME.get('openpnp_prepare_job').inputSchema);
const request = { request_id: 'a334762f-2263-491d-a22b-c3cfb7f12145', session_id: 'lease', expected_config_revision: 'cfg-2', artifact_id: 'a'.repeat(64), part_bindings: [{ source_part_id: 'SUPPLIER-10K', native_part_id: 'R-10K' }] };
const accepts = value => assert.equal(validate(value), true, JSON.stringify(validate.errors));
const rejects = value => assert.equal(validate(value), false, JSON.stringify(value));

test('legacy sample/artifact prepare commands retain optional revisions and historical request IDs', () => {
  accepts({ request_id: 'old', session_id: 'lease', sample: 'pnp-test' });
  accepts({ request_id: 'old', session_id: 'lease', artifact_id: request.artifact_id });
  accepts({ request_id: 'old', session_id: 'lease', artifact_id: request.artifact_id, expected_config_revision: 'cfg-0' });
});

test('bindings require artifact, canonical UUID, revision and closed bounded ID pairs', () => {
  accepts(request);
  accepts({ ...request, part_bindings: [{ source_part_id: 'A_.:+-9', native_part_id: 'b'.repeat(128) }] });
  accepts({ ...request, part_bindings: Array.from({ length: 1000 }, (_, i) => ({ source_part_id: `source-${i}`, native_part_id: 'existing' })) });
  for (const key of ['request_id', 'session_id', 'expected_config_revision', 'artifact_id']) { const missing = structuredClone(request); delete missing[key]; rejects(missing); }
  for (const patch of [{ request_id: 'old' }, { request_id: request.request_id.toUpperCase() }, { expected_config_revision: '2' }, { artifact_id: 'X'.repeat(64) }, { sample: 'pnp-test' }, { part_bindings: null }, { part_bindings: [] }, { part_bindings: {} }, { part_bindings: Array(1001).fill(request.part_bindings[0]) }, { canonical_job: {} }, { apply: true }, { create_missing_parts: true }]) rejects({ ...request, ...patch });
  const sample = { ...request, sample: 'pnp-test' }; delete sample.artifact_id; rejects(sample);
  for (const row of [{}, { source_part_id: 'A' }, { native_part_id: 'A' }, { ...request.part_bindings[0], package_id: 'P' }, { source_part_id: '', native_part_id: 'A' }, { source_part_id: 'a/b', native_part_id: 'A' }, { source_part_id: 'A', native_part_id: 'a b' }, { source_part_id: 'A', native_part_id: 'a'.repeat(129) }, { source_part_id: 'A', native_part_id: 3 }, { source_part_id: 'A\0', native_part_id: 'A' }]) rejects({ ...request, part_bindings: [row] });
});

function harness(profile = 'existing-parts-v1') {
  const calls = [], reads = [], canonical = Object.freeze({ schemaVersion: 1, id: 'fixture', parts: [] });
  const runtime = new OpenPnpRuntime({ client: { async call(name, args, options) {
    calls.push({ name, args, options });
    if (name === 'openpnp_get_capabilities') return { schema_version: 1, bridge_version: '0.1.0', upstream_commit: PINNED_UPSTREAM, tools: ['openpnp_prepare_job'], ...(profile ? { canonical_part_bindings: { profile } } : {}) };
    return { operation_id: 'operation', state: 'accepted' };
  } }, artifacts: { async get(id) { reads.push(id); return canonical; } }, responses: {} });
  return { runtime, calls, reads, canonical };
}

test('native dispatch retains source artifact and exact bindings beside verified canonical data', async () => {
  const h = harness(); const before = structuredClone(request);
  assert.deepEqual(await h.runtime.call('openpnp_prepare_job', request), { operation_id: 'operation', state: 'accepted' });
  assert.deepEqual(request, before);
  assert.deepEqual(h.reads, [request.artifact_id]);
  const { artifact_id, ...remaining } = request;
  assert.deepEqual(h.calls[1], { name: 'openpnp_prepare_job', args: { ...remaining, canonical_job: h.canonical, canonical_artifact_id: artifact_id }, options: { mutating: true } });
  assert.equal(h.calls.length, 2);
});

test('unsupported profiles and malformed requests never read artifacts or dispatch mutations', async () => {
  for (const profile of [null, 'all-parts', 'existing-parts-v2']) {
    const h = harness(profile);
    await assert.rejects(h.runtime.call('openpnp_prepare_job', request), { code: 'UNSUPPORTED_CAPABILITY' });
    assert.equal(h.calls.length, 1); assert.equal(h.reads.length, 0);
  }
  const h = harness();
  await assert.rejects(h.runtime.call('openpnp_prepare_job', { ...request, part_bindings: [] }), { code: 'INVALID_ARGUMENT' });
  assert.equal(h.calls.length, 0); assert.equal(h.reads.length, 0);
});

test('legacy artifact dispatch adds no new native fields and requires no binding capability', async () => {
  const h = harness(null), args = { request_id: 'old', session_id: 'lease', artifact_id: request.artifact_id };
  await h.runtime.call('openpnp_prepare_job', args);
  assert.deepEqual(h.calls[1].args, { request_id: 'old', session_id: 'lease', canonical_job: h.canonical });
});
