import test from 'node:test';
import assert from 'node:assert/strict';
import Ajv from '../../src/openpnp/node/node_modules/ajv/dist/ajv.js';
import { TOOL_BY_NAME } from '../../src/openpnp/node/contracts.mjs';
import { OpenPnpRuntime, PINNED_UPSTREAM } from '../../src/openpnp/node/runtime.mjs';

const tool = TOOL_BY_NAME.get('openpnp_run_calibration');
const validate = new Ajv({ strict: true, allErrors: true, coerceTypes: false }).compile(tool.inputSchema);
const camera = { request_id: 'e4971148-4248-43f9-9511-d5e2a8016887', session_id: 'lease', expected_config_revision: 'cfg-2', recipe_id: 'camera-planar-scale', camera_id: 'C1', displacement_mm: 1, expected_feature_diameter_px: 40 };
const accepted = args => assert.equal(validate(args), true, JSON.stringify(validate.errors));
const rejected = args => assert.equal(validate(args), false, JSON.stringify(args));

test('existing runout commands retain their original request, enable and revision semantics', () => {
  accepted({ request_id: 'historical-id', session_id: 'lease', recipe_id: 'nozzle-tip-runout', nozzle_id: 'N1' });
  for (const enable of [true, false]) accepted({ request_id: 'historical-id', session_id: 'lease', expected_config_revision: 'cfg-1', recipe_id: 'nozzle-tip-runout', nozzle_id: 'N1', enable });
});

test('camera scale admission binds its target, current revision and bounded sampling inputs', () => {
  accepted(camera);
  accepted({ ...camera, displacement_mm: 0.5, expected_feature_diameter_px: 12 });
  accepted({ ...camera, displacement_mm: 2, expected_feature_diameter_px: 96 });
  for (const key of Object.keys(camera)) { const missing = { ...camera }; delete missing[key]; rejected(missing); }
  for (const patch of [{ displacement_mm: 0.49 }, { displacement_mm: 2.01 }, { displacement_mm: '1' }, { displacement_mm: NaN }, { displacement_mm: Infinity }, { expected_feature_diameter_px: 11 }, { expected_feature_diameter_px: 97 }, { expected_feature_diameter_px: 40.5 }, { expected_feature_diameter_px: '40' }, { camera_id: '' }, { expected_config_revision: '2' }, { request_id: 'not-a-uuid' }, { request_id: camera.request_id.toUpperCase() }]) rejected({ ...camera, ...patch });
});

test('recipes reject mixed targets, arbitrary processing, tolerances and application authority', () => {
  for (const extra of [{ nozzle_id: 'N1' }, { enable: true }, { pipeline: 'unbounded' }, { source_url: 'file:///tmp/image.png' }, { z: 0 }, { maximum_error_px: 500 }, { apply: true }, { physical_scale_verified: true }]) rejected({ ...camera, ...extra });
  rejected({ request_id: 'old', session_id: 'lease', recipe_id: 'nozzle-tip-runout', nozzle_id: 'N1', camera_id: 'C1' });
  rejected({ ...camera, recipe_id: 'lens-fit' });
});

test('runtime forwards one exact admitted measurement request and never submits invalid parameters', async () => {
  const calls = [];
  const runtime = new OpenPnpRuntime({ client: { async call(name, args, options) {
    calls.push({ name, args, options });
    if (name === 'openpnp_get_capabilities') return { schema_version: 1, bridge_version: '0.1.0', upstream_commit: PINNED_UPSTREAM, tools: ['openpnp_run_calibration'] };
    return { operation_id: 'measurement', state: 'accepted' };
  } } });
  assert.deepEqual(await runtime.call('openpnp_run_calibration', camera), { operation_id: 'measurement', state: 'accepted' });
  assert.deepEqual(calls.filter(x => x.name === 'openpnp_run_calibration'), [{ name: 'openpnp_run_calibration', args: camera, options: { mutating: true } }]);
  const before = calls.length;
  await assert.rejects(runtime.call('openpnp_run_calibration', { ...camera, apply: true }), error => error.code === 'INVALID_ARGUMENT');
  assert.equal(calls.length, before);
});
