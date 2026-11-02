import test from 'node:test';
import assert from 'node:assert/strict';
import Ajv from '../../src/openpnp/node/node_modules/ajv/dist/ajv.js';
import { NATIVE_CHANGE_SCHEMAS } from '../../src/openpnp/node/settings-contracts.mjs';

const schema = NATIVE_CHANGE_SCHEMAS.find(row => row.properties.type.const === 'set_mapped_axis_geometry');
const validate = new Ajv({ strict: true, coerceTypes: false }).compile(schema);
const change = { type: 'set_mapped_axis_geometry', axis_id: 'mapped:X', input_axis_id: 'raw:X', input_0_mm: 0, output_0_mm: -20, input_1_mm: 10, output_1_mm: 0 };
test('mapped-axis schema accepts explicit bounded points and existing identifier syntax', () => {
  assert.equal(validate(change), true);
  assert.equal(validate({ ...change, output_0_mm: 20, output_1_mm: 0 }), true);
  assert.equal(validate({ ...change, input_0_mm: -1000, input_1_mm: 1000, output_0_mm: -1000, output_1_mm: 1000 }), true);
});
test('mapped-axis schema rejects incomplete, coerced, unitless or arbitrary kinematic edits', () => {
  for (const key of Object.keys(change)) { const missing = { ...change }; delete missing[key]; assert.equal(validate(missing), false, key); }
  for (const key of ['input_0_mm', 'input_1_mm', 'output_0_mm', 'output_1_mm']) for (const value of [1000.1, -1000.1, '1', true, null, NaN, Infinity])
    assert.equal(validate({ ...change, [key]: value }), false, `${key}=${value}`);
  for (const extra of [{ axis_id: '' }, { input_axis_id: '../../axis' }, { units: 'in' }, { matrix: [[1, 0], [0, 1]] }, { driver: 'serial' }, { scale: 2 }, { code: 'setInputAxis()' }])
    assert.equal(validate({ ...change, ...extra }), false, JSON.stringify(extra));
});
