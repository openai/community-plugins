import test from 'node:test';
import assert from 'node:assert/strict';
import Ajv from '../../src/openpnp/node/node_modules/ajv/dist/ajv.js';
import { TOOL_BY_NAME } from '../../src/openpnp/node/contracts.mjs';

const validate = new Ajv({ strict: false, allErrors: true }).compile(TOOL_BY_NAME.get('openpnp_plan_configuration').inputSchema);
const base = { type: 'set_camera_dynamic_settling', camera_id: 'CAM1', method: 'Mean', timeout_ms: 80, debounce: 1, threshold_percent: 0.5, full_color: false };
const check = (row, expected) => assert.equal(validate({ session_id: 'session', expected_config_revision: 'cfg-4', changes: [row] }), expected, JSON.stringify(validate.errors));

test('camera plans preserve fixed dwell and explicitly select bounded dynamic algorithms', () => {
  check({ type: 'set_camera_settling', camera_id: 'CAM1', settle_time_ms: 12 }, true);
  for (const method of ['Maximum', 'Mean', 'Euclidean', 'Square']) {
    check({ ...base, method }, true);
    check({ ...base, method, timeout_ms: 5000, debounce: 10, threshold_percent: 100, full_color: true }, true);
  }
});

test('dynamic settings reject unsupported algorithms, coercions and unbounded values', () => {
  for (const patch of [
    { method: 'Motion' }, { method: 'FixedTime' }, { method: 'mean' },
    { timeout_ms: 19 }, { timeout_ms: 5001 }, { timeout_ms: 30.5 }, { timeout_ms: '80' },
    { debounce: -1 }, { debounce: 11 }, { debounce: 0.5 },
    { threshold_percent: 0 }, { threshold_percent: -0.1 }, { threshold_percent: 100.1 },
    { threshold_percent: Infinity }, { threshold_percent: NaN }, { full_color: 1 },
  ]) check({ ...base, ...patch }, false);
});

test('dynamic settings require every declared field and reject raw property/code/stability claims', () => {
  for (const key of Object.keys(base).filter(key => key !== 'type')) {
    const missing = { ...base }; delete missing[key]; check(missing, false);
  }
  for (const extra of [{ settle_time_ms: 12 }, { gaussian_blur: 99 }, { class: 'Camera' }, { script: 'eval' }, { stable: true }, { physical_calibration_valid: true }])
    check({ ...base, ...extra }, false);
});
