---
name: calibrate-openpnp-vision
description: Configure or diagnose OpenPnP cameras, nozzle offsets, runout, fiducial vision, and bottom alignment with measured evidence.
---

# Calibrate OpenPnP Vision

Read [the runtime/control contract](../../references/control-contract.md) and, for this workflow, [calibrate openpnp vision guidance](../../references/camera-and-vision-calibration.md). Use only tool operations and recipes reported as supported by the current runtime. Tool presence alone does not qualify physical hardware.

## Workflow

1. Read capabilities, current configuration, and the selected camera/vision recipe. Resolve camera identity, resolution, working plane, lighting, settling, and dependent nozzle calibration.
2. Capture evidence with `openpnp_capture_camera` using its declared capture mode. Account for lighting or settling effects; do not derive a machine coordinate from an uncalibrated image-language estimate. A returned settled frame does not establish stability: the native API also returns on its settling timeout and does not report which condition ended the loop. Preserve the artifact's capture, settling, image-validity, and calibration-validity metadata.
3. Inspect effective machine/package/part vision settings. Stage supported parameter changes with `openpnp_plan_configuration`; preview shared impact and apply only the intended specialization.
4. Run the supported recipe through `openpnp_run_calibration`. `nozzle-tip-runout` takes `nozzle_id` and optional `enable`. When `camera_planar_scale.runtime.available` is true, use [the planar camera measurement workflow](../../references/camera-and-vision-calibration.md#planar-camera-scale-measurement) for an initialized down-looking simulator ImageCamera. It returns an unapplied scale proposal, eight image observations on success, and separate holdout checks. Follow the original operation and retain every returned image.
5. For nozzle runout, inspect the model through `openpnp_validate_calibration` with `nozzle_id`; its fitted offsets are model samples. For camera scale, read `measurement_status` and `configuration_proposal_status`. A successful operation can report a rejected measurement, or an accepted measurement with no applicable configuration proposal. Apply only an available proposal through the separate typed configuration preview/apply workflow within the requested scope. After apply, refresh invalidated registration and job validation. Neither fitted offsets nor simulator image prediction errors establish physical accuracy.

## Completion and recovery

Return camera/tool identities, native model status, available measurements and thresholds, retained artifacts, and missing validation evidence.

Stop production qualification on ambiguous detections, stale/wrong-plane images, missing negative-example coverage, or unqualified parameter edits. If a dependent tool or capability is unavailable, complete the supported inspection or preparation and name the exact remaining gap; do not imitate execution or use raw commands as a fallback.

When changing native vision profiles, inheritance or existing pipeline parameters, read [typed vision settings](../../references/vision-settings.md) and use only the advertised configuration changes. Preserve the distinction between setting a parameter and measuring detector/calibration accuracy.

For image-based settling, use the complete `set_camera_dynamic_settling` change only when advertised and the selected camera reports `dynamic_settling_editable: true`. Read the linked camera guidance for supported methods, bounds and capture limitations. Compare images against a declared quality criterion; timeout and elapsed-time fields do not prove stability. Preserve explicit portable-export refusal for an unsupported dynamic source instead of changing its settling mode without an intended configuration change.
