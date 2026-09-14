---
name: calibrate-openpnp-motion
description: Home or calibrate OpenPnP axes, direction, scale, backlash, and working clearance using machine-specific procedures.
---

# Calibrate OpenPnP Motion

Read [the runtime/control contract](../../references/control-contract.md) and, for this workflow, [calibrate openpnp motion guidance](../../references/motion-and-homing.md). Use only tool operations and recipes reported as supported by the current runtime. Tool presence alone does not qualify physical hardware.

## Workflow

1. Read capabilities/status and the selected motion recipe. Resolve current position confidence, homing method, axis topology, units, limits, coupled heads, and occupied tooling.
2. For initial homing, use supported `openpnp_home_machine` with its commissioning recipe and physical prerequisites. A previously homed flag is insufficient after reset, depowering, or suspected lost steps.
3. For diagnostic positioning, prepare `openpnp_plan_motion` in the correct frame and inspect the full path/envelope, including other nozzles and fixtures. Execute only the returned valid plan through `openpnp_execute_motion`.
4. Run the required recipe with `openpnp_run_calibration` only if supported for the requested axes; the shipped nozzle-tip runout recipe does not calibrate axes, scale, or backlash. Use `openpnp_measure_sensor` only for a supported measurement with its declared effects.
5. Assess actual measured residuals against the profile, preserving provenance and uncertainty. The shipped `openpnp_validate_calibration` reads a nozzle's existing runout model; it does not measure axis residuals. Record unresolved measurements and downstream camera/tool/board validity.
6. For a supported mapped-axis edit, follow the [existing mapped-axis procedure](../../references/motion-and-homing.md#existing-mapped-axes). After application, explicitly verify homing, nozzle-runout and dormant advanced-camera validity, registration, job validation and motion-plan invalidations. Keep any unobservable dependency marked unverified.

7. For simulator backlash configuration, follow the [typed linear-axis procedure](../../references/motion-and-homing.md#typed-linear-axis-backlash-settings). Use the current editable axis and all five setting values, plan then apply while disabled, and inspect native readback. Compensation can extend controller travel beyond logical soft limits; inspect the returned behavior and extra-distance bound. Re-home and revalidate dependent motion/job evidence. Report this as software configuration, never measured backlash calibration.

New simulator nozzles can receive independent Z and Rotation axes through [complete nozzle assembly](../../references/simulator-nozzle-assembly.md). Rotation creation uses degree fields; shared existing X/Y axes remain native references. Configuration and successful simulated motion do not measure backlash, offsets, clearance or physical accuracy.

## Completion and recovery

Return measured axis/homing results, completion evidence, relevant revisions, and the qualified working scope.

Stop dependent movement on uncertain position, insufficient clearance, expired ownership, unreliable measurement, or an incomplete completion barrier. If a dependent tool or capability is unavailable, complete the supported inspection or preparation and name the exact remaining gap; do not imitate execution or use raw commands as a fallback.
