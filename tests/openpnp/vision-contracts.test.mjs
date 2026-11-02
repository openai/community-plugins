import test from 'node:test';
import assert from 'node:assert/strict';
import Ajv from '../../src/openpnp/node/node_modules/ajv/dist/ajv.js';
import { TOOL_BY_NAME } from '../../src/openpnp/node/contracts.mjs';

const validate = new Ajv({ strict: false, allErrors: true }).compile(TOOL_BY_NAME.get('openpnp_plan_configuration').inputSchema);
const plan = changes => ({ session_id: 'native-session', expected_config_revision: 'cfg-4', changes });
const check = (row, expected) => assert.equal(validate(plan([row])), expected, JSON.stringify(validate.errors));
test('typed vision plans admit explicit native inheritance, settings, stage variants and assignment clearing', () => {
  for (const row of [
    { type: 'clone_vision_settings', source_vision_settings_id: 'BVS_Stock', vision_settings_id: 'BVS_part', name: 'Part view' },
    { type: 'assign_vision_settings', holder: 'package:R0805', kind: 'bottom', vision_settings_id: 'BVS_part' },
    { type: 'assign_vision_settings', holder: 'part:R1', kind: 'bottom', vision_settings_id: null },
    { type: 'set_bottom_vision_settings', vision_settings_id: 'BVS_part', enabled: true, pre_rotate_usage: 'AlwaysOff', part_size_check: 'BodySize', size_tolerance_percent: 10, max_rotation: 'Adjust', asymmetric: false, offset_x_mm: 0, offset_y_mm: 0 },
    { type: 'set_fiducial_vision_settings', vision_settings_id: 'FVS_part', enabled: true, max_vision_passes: 3, max_linear_offset_mm: 0.2, parallax_diameter_mm: 0, parallax_angle_deg: 0 },
    { type: 'remove_vision_settings', vision_settings_id: 'BVS_part' },
    { type: 'set_vision_pipeline_stage', vision_settings_id: 'BVS_part', stage_name: '3', stage_type: 'gaussian', parameters: { kernel_size: 5 } },
    { type: 'set_vision_pipeline_stage', vision_settings_id: 'BVS_part', stage_name: 'threshold', stage_type: 'threshold', parameters: { threshold: 128, auto: false, invert: false } },
    { type: 'set_vision_pipeline_stage', vision_settings_id: 'BVS_part', stage_name: 'mask', stage_type: 'hsv', parameters: { hue_min: 0, hue_max: 120, saturation_min: 10, saturation_max: 255, value_min: 30, value_max: 255, invert: false } },
    ...[128, false, null].map(value => ({ type: 'set_vision_parameter', vision_settings_id: 'BVS_part', parameter_name: 'pThreshold', value })),
  ]) check(row, true);
});
test('vision schemas reject raw pipeline/code fields, mismatched scalar groups and nonfinite/out-of-range values', () => {
  const base = { type: 'set_vision_parameter', vision_settings_id: 'BVS_part', parameter_name: 'pThreshold', value: 128 };
  for (const row of [
    { ...base, value: '128' }, { ...base, value: 1.5 }, { ...base, value: 256 }, { ...base, value: -1 },
    { ...base, value: Infinity }, { ...base, value: NaN }, { ...base, value: { java: 'Runtime' } },
    { ...base, pipeline_xml: '<pipeline/>' },
    { type: 'assign_vision_settings', holder: 'arbitrary.Class', kind: 'bottom', vision_settings_id: null },
    { type: 'set_vision_pipeline_stage', vision_settings_id: 'BVS_part', stage_name: '3', stage_type: 'gaussian', parameters: { threshold: 128 } },
    { type: 'set_vision_pipeline_stage', vision_settings_id: 'BVS_part', stage_name: '3', stage_type: 'gaussian', parameters: { kernel_size: 5, property_name: 'eval' } },
  ]) check(row, false);
});
