# Motion, homing, and clearance

Start from the profile's units, coordinate frames, transformed/coupled axes, homing method, travel/speed limits, nozzles, and fixtures. A nozzle that is not being commanded can still move with its head. Include its envelope in the planned path.

Use a narrow commissioning recipe for initial homing or an unknown position. Ordinary positioning requires trusted homing and valid dependencies. Controller reset, motor depowering, collision, or suspected lost steps can invalidate position confidence even when a stored homed flag remains true.

Inspect the complete path returned by `openpnp_plan_motion` before execution. Check safe working heights and known obstructions; raising Z is not universally sufficient. Native jobs/calibration must obey the same profile constraints, so an unqualified internal-motion path cannot be rescued by a good standalone move plan.

Wait for the operation's qualified completion barrier. Controller acknowledgement and commanded coordinates are weaker evidence than independently measured position. Report the actual evidence rather than renaming it standstill or verified geometry.

OpenPnP's asynchronous driver can buffer work, making completion and driver-specific synchronization relevant. The plugin must preserve those native semantics; it must not equate tool return with completed movement. [GcodeAsyncDriver](https://github.com/openpnp/openpnp/wiki/GcodeAsyncDriver)

After an interruption, inspect position, held parts, and the route before rehoming. Rehoming can itself move hardware and does not determine whether a previous placement occurred.

## Existing mapped axes

When the runtime advertises `set_mapped_axis_geometry`, it can edit one existing exact native `ReferenceMappedAxis` for X, Y or Z. Use a separate configuration plan with exactly one change: `axis_id`, `input_axis_id`, `input_0_mm`, `output_0_mm`, `input_1_mm`, and `output_1_mm`. The input must be an existing compatible linear `ReferenceControllerAxis` on the simulator's `NullDriver`. All endpoints use explicit millimeters within ±1000; native admission additionally rejects singular spans, invalid links and a transformed travel envelope outside its bounds.

For example, input 5→output −20 and input 15→output 0 represent `output = 2 × input − 30`. This describes a model mapping, not a measured calibration. Check the preview's source link, scale, offset, direction and complete transformed source limits. A matching configuration revision does not make a plan valid if the actual source object or link changed; refresh and stage a new plan.

A successful change invalidates homing, nozzle-runout compensation, dormant advanced-camera validity, board/fiducial registration, job validation and cached motion plans. After the apply operation finishes and the native executor is idle, read the saved configuration and current status. Check `installed_tip_runout_calibrated` for installed tips and `advanced_calibration_valid` for every affected camera, including cameras whose advanced calibration is disabled. Retain the apply receipt and registration/job invalidations; if an affected cache is not exposed, report that verification gap. Do not preserve or restore calibration validity merely because a model remains present. Required revalidation must finish before dependent movement.

Mapped chains, coupled transforms, rotation, virtual-axis inputs, axis creation and arbitrary property edits remain unsupported. Changing a mapped source's controller limits requires a supported transaction for the dependent envelope; do not bypass that refusal. Typed version-one snapshots and the current portable profile explicitly omit or refuse mapped geometry. Preserve the refusal and unsupported source content without substituting an ordinary controller-axis edit.


## Typed linear-axis backlash settings

On a supporting simulator runtime, `set_axis_backlash_settings` configures an existing exact `ReferenceControllerAxis` X/Y/Z on the installed `NullDriver`. Read `backlash_settings_editable` and the current `axis_id` from `openpnp_get_configuration`. Supply every field: `method`, signed `offset_mm` (-10..10), `speed_factor` (0.001..1), `sneak_up_mm` (0..10), and `acceptable_tolerance_mm` (0.000001..1). These bounds define the simulator software profile; they are not measured machine tolerances. Enabled methods require a nonzero offset; `DirectionalSneakUp` also requires a positive sneak-up distance. Inactive fields are preserved for readback and snapshots.

Supported methods are `None`, `OneSidedPositioning`, `OneSidedOptimizedPositioning`, `DirectionalCompensation`, and `DirectionalSneakUp`. One-sided positioning overshoots by the signed offset then returns; optimized positioning skips that extra move in the opposite direction. Directional methods leave the controller endpoint offset only when the travel direction matches the offset sign. Speed-controlled final segments use the minimum of requested speed and `speed_factor`, not their product. `acceptable_tolerance_mm` configures native acceptance tolerance; it does not record a measurement.

Plan the typed change through `openpnp_plan_configuration`, disable the simulator, then apply its original revision-bound plan. Held parts, advanced/3D camera geometry, changed native references or settings, mapped-source targets and unsupported classes are refused. This profile does not create axes or calibrate measured backlash. Application invalidates homing, nozzle runout, camera validity, board registration and job validation. Typed backup/restore preserves these software values and revokes dependent validity; it never restores execution or calibration authority.

**Motion envelope:** OpenPnP applies logical soft limits before backlash compensation. Native controller segments can extend beyond those limits. Configuration and motion previews disclose active method behavior and a conservative extra distance from the logical segment: absolute offset, plus sneak-up distance for `DirectionalSneakUp` (up to 20 mm in this profile). This is not a computed full Safe-Z path, collision check, measured controller travel, or a physical-machine qualification. After applying, re-home and use a supported simulator motion plan; retain native completion evidence and report the unmeasured physical scope explicitly.
