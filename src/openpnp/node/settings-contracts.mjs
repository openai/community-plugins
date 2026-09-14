// Exact typed subset implemented by org.openpnp.codex.NativeSettings.
// Cross-field geometry, references, current native class, and invalidation are checked again in Java.
const identifier = { type: 'string', pattern: '^[A-Za-z0-9_.:+-]{1,128}$' };
const description = { type: 'string', maxLength: 1024, pattern: '^[^\\u0000-\\u001f\\u007f]*$' };
const real = (minimum, maximum) => ({ type: 'number', minimum, maximum });
const integer = (minimum, maximum) => ({ type: 'integer', minimum, maximum });
const object = properties => ({ type: 'object', properties, required: Object.keys(properties), additionalProperties: false });
const change = (type, properties) => object({ type: { const: type }, ...properties });
const location = object({ x_mm: real(-1000, 1000), y_mm: real(-1000, 1000), z_mm: real(-100, 100), rotation_deg: real(-360, 360) });
const tips = { type: 'array', maxItems: 64, uniqueItems: true, items: identifier };
const footprint = {
  package_id: identifier, description, body_width_mm: real(0.001, 100), body_height_mm: real(0.001, 100),
  pads: { type: 'array', maxItems: 256, items: object({ name: identifier, x_mm: real(-100, 100), y_mm: real(-100, 100), width_mm: real(0.001, 100), height_mm: real(0.001, 100), rotation_deg: real(-360, 360), roundness: real(0, 100) }) },
};
const part = { part_id: identifier, package_id: identifier, name: description, height_mm: real(0.001, 50), speed: real(0.001, 1) };
const boolean = { type: 'boolean' };

export const NATIVE_CHANGE_SCHEMAS = [
  change('set_part_height', { part_id: identifier, height_mm: real(0.001, 50) }),
  change('assign_feeder', { feeder_id: identifier, part_id: identifier }),
  change('set_feeder_enabled', { feeder_id: identifier, enabled: { type: 'boolean' } }),
  change('set_machine_speed', { speed: real(0.001, 1) }),
  change('create_package', footprint), change('set_package_footprint', footprint),
  change('create_part', part), change('set_part_properties', part),
  change('set_package_compatibility', { package_id: identifier, nozzle_tip_ids: tips }),
  change('set_tray_feeder_geometry', { feeder_id: identifier, location, count_x: integer(1, 100), count_y: integer(1, 100), pitch_x_mm: real(-100, 100), pitch_y_mm: real(-100, 100), feed_count: integer(0, 10000) }),
  change('set_strip_feeder_geometry', { feeder_id: identifier, location, reference_hole: location, last_hole: location, part_pitch_mm: real(0.1, 100), hole_pitch_mm: real(0.1, 100), tape_width_mm: real(2, 100), hole_diameter_mm: real(0.1, 20), reference_hole_to_part_mm: real(-100, 100), feed_count: integer(0, 1000000), max_feed_count: integer(1, 1000000) }),
  change('set_camera_geometry', { camera_id: identifier, units_per_pixel_x_mm: real(0.000001, 10), units_per_pixel_y_mm: real(0.000001, 10), working_plane_z_mm: real(-100, 100), head_offsets: location }),
  change('set_camera_settling', { camera_id: identifier, settle_time_ms: integer(0, 10000) }),
  // Native settling can return a frame at its loop timeout without reporting
  // stability. This configures the algorithm; it does not qualify the image.
  change('set_camera_dynamic_settling', {
    camera_id: identifier, method: { type: 'string', enum: ['Maximum', 'Mean', 'Euclidean', 'Square'] },
    timeout_ms: integer(20, 5000), debounce: integer(0, 10),
    threshold_percent: { type: 'number', exclusiveMinimum: 0, maximum: 100 }, full_color: boolean,
  }),
  change('create_simulator_nozzle_assembly', {
    head_id: identifier, driver_id: identifier, x_axis_id: identifier, y_axis_id: identifier,
    nozzle_name: { type: 'string', minLength: 1, maxLength: 64 }, tip_name: { type: 'string', minLength: 1, maxLength: 64 }, valve_name: { type: 'string', minLength: 1, maxLength: 64 },
    head_offsets: object({ x_mm: real(-100, 100), y_mm: real(-100, 100), z_mm: real(-100, 100) }),
    z_axis: object({ home_mm: real(-100, 100), low_mm: real(-100, 100), high_mm: real(-100, 100), safe_z_mm: real(-100, 100), feedrate_mm_per_s: real(0.001, 1000), acceleration_mm_per_s2: real(0.001, 10000), jerk_mm_per_s3: real(0, 1000000) }),
    rotation_axis: object({ home_deg: real(-360, 360), low_deg: real(-360, 360), high_deg: real(-360, 360), feedrate_deg_per_s: real(0.001, 1000), acceleration_deg_per_s2: real(0.001, 10000), jerk_deg_per_s3: real(0, 1000000) }),
    tip: object({ min_part_diameter_mm: real(0, 50), max_part_diameter_mm: real(0.001, 50), max_part_height_mm: real(0.001, 50), max_pick_tolerance_mm: real(0, 10), pick_dwell_ms: integer(0, 10000), place_dwell_ms: integer(0, 10000) }),
    pick_dwell_ms: integer(0, 10000), place_dwell_ms: integer(0, 10000),
    exclusive_package_ids: { type: 'array', minItems: 1, maxItems: 64, uniqueItems: true, items: identifier },
    simulated_initial_tool_state: { const: 'installed-on-new-nozzle' },
  }),
  change('set_nozzle_settings', { nozzle_id: identifier, pick_dwell_ms: integer(0, 10000), place_dwell_ms: integer(0, 10000), nozzle_tip_ids: tips }),
  change('set_nozzle_tip_settings', { nozzle_tip_id: identifier, pick_dwell_ms: integer(0, 10000), place_dwell_ms: integer(0, 10000) }),
  change('set_vacuum_sensing_settings', {
    nozzle_id: identifier, nozzle_tip_id: identifier,
    vacuum_sense_actuator_id: { anyOf: [identifier, { type: 'null' }] }, vacuum_actuator_id: identifier,
    reading_units: { const: 'native-actuator-units' }, threshold_provenance: { const: 'configured-thresholds' },
    method_part_on: { enum: ['None', 'Absolute'] }, method_part_off: { enum: ['None', 'Absolute'] },
    part_on_low: real(-1000000, 1000000), part_on_high: real(-1000000, 1000000),
    part_off_low: real(-1000000, 1000000), part_off_high: real(-1000000, 1000000),
    part_on_check_after_pick: boolean, part_on_check_align: boolean, part_on_check_before_place: boolean,
    part_off_check_after_place: boolean, part_off_check_before_pick: boolean,
    part_off_probe_ms: integer(0, 1000), part_off_dwell_ms: integer(0, 1000),
  }),
  change('set_axis_motion_limits', { axis_id: identifier, soft_limit_low_mm: real(-1000, 1000), soft_limit_high_mm: real(-1000, 1000), feedrate_mm_per_s: real(0.001, 1000), acceleration_mm_per_s2: real(0.001, 10000), jerk_mm_per_s3: real(0, 1000000) }),
  change('set_axis_backlash_settings', { axis_id: identifier, method: { type: 'string', enum: ['None', 'OneSidedPositioning', 'OneSidedOptimizedPositioning', 'DirectionalCompensation', 'DirectionalSneakUp'] }, offset_mm: real(-10, 10), speed_factor: real(0.001, 1), sneak_up_mm: real(0, 10), acceptable_tolerance_mm: real(0.000001, 1) }),
  change('set_mapped_axis_geometry', { axis_id: identifier, input_axis_id: identifier,
    input_0_mm: real(-1000, 1000), output_0_mm: real(-1000, 1000), input_1_mm: real(-1000, 1000), output_1_mm: real(-1000, 1000) }),
  change('set_job_planner_settings', {
    job_order: { type: 'string', enum: ['Part', 'PartHeight', 'PartBoard', 'HeightPartBoard', 'BoardPart', 'PickLocation', 'PickPlaceLocation', 'NozzleTips', 'NozzleTipsByFlexibility', 'Unsorted'] },
    strategy: { type: 'string', enum: ['Minimize', 'StartAsPlanned', 'FullyAsPlanned'] },
    optimize_multiple_nozzles: boolean, pre_rotate_all_nozzles: boolean, stepping_to_next_motion: boolean,
  }),
  // Attempts include the first attempt; native deferred-placement handling and nested part /
  // feeder loops still govern retry behavior. Java also requires fault limit <= fault window.
  change('set_job_retry_settings', { vision_attempts: integer(1, 10), placement_attempts: integer(1, 10), feeder_fault_limit: integer(1, 100), feeder_fault_window: integer(1, 100) }),
  change('set_part_retry_settings', { part_id: identifier, pick_retries: integer(0, 5) }),
  change('set_feeder_retry_settings', { feeder_id: identifier, feed_retries: integer(0, 5), pick_retries: integer(0, 5) }),
  // MovableUtils.park ignores stored Z / rotation and uses native Safe Z; do not accept no-op axes.
  change('set_head_park_location', { head_id: identifier, x_mm: real(-1000, 1000), y_mm: real(-1000, 1000) }),
  change('set_machine_discard_location', { location }),
  change('clone_vision_settings', { source_vision_settings_id: identifier,
    vision_settings_id: { type: 'string', pattern: '^[A-Za-z][A-Za-z0-9_-]{0,127}$' },
    name: { type: 'string', minLength: 1, maxLength: 128, pattern: '^[^\\u0000-\\u001f\\u007f]*$' } }),
  change('set_bottom_vision_settings', {
    vision_settings_id: identifier, enabled: boolean,
    pre_rotate_usage: { type: 'string', enum: ['Default', 'AlwaysOn', 'AlwaysOff'] },
    part_size_check: { type: 'string', enum: ['Disabled', 'BodySize', 'PadExtents'] },
    size_tolerance_percent: integer(0, 100), max_rotation: { type: 'string', enum: ['Adjust', 'Full'] },
    asymmetric: boolean, offset_x_mm: real(-50, 50), offset_y_mm: real(-50, 50),
  }),
  change('set_fiducial_vision_settings', {
    vision_settings_id: identifier, enabled: boolean, max_vision_passes: integer(1, 10),
    max_linear_offset_mm: real(0, 20), parallax_diameter_mm: real(0, 50), parallax_angle_deg: real(-180, 180),
  }),
  change('assign_vision_settings', {
    holder: { type: 'string', maxLength: 140, pattern: '^(machine:(bottom|fiducial)|(part|package):[A-Za-z0-9_.:+-]{1,128})$' },
    kind: { type: 'string', enum: ['bottom', 'fiducial'] }, vision_settings_id: { anyOf: [identifier, { type: 'null' }] },
  }),
  change('remove_vision_settings', { vision_settings_id: identifier }),
  ...[
    ['gaussian', object({ kernel_size: integer(3, 99) })],
    ['threshold', object({ threshold: integer(0, 255), auto: boolean, invert: boolean })],
    ['hsv', object({ hue_min: integer(0, 255), hue_max: integer(0, 255), saturation_min: integer(0, 255), saturation_max: integer(0, 255), value_min: integer(0, 255), value_max: integer(0, 255), invert: boolean })],
  ].map(([stage, parameters]) => change('set_vision_pipeline_stage', {
    vision_settings_id: identifier, stage_name: identifier, stage_type: { const: stage }, parameters,
  })),
  // Only existing enabled parameters with supported native Threshold targets are admitted by Java.
  change('set_vision_parameter', { vision_settings_id: identifier, parameter_name: identifier,
    value: { anyOf: [integer(0, 255), boolean, { type: 'null' }] } }),
];
