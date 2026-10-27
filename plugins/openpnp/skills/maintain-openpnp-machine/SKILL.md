---
name: maintain-openpnp-machine
description: Diagnose OpenPnP maintenance needs, perform selected startup and shutdown procedures, or export selected local support evidence.
---

# Maintain an OpenPnP Machine

Read [the runtime/control contract](../../references/control-contract.md) and, for this workflow, [maintain an openpnp machine guidance](../../references/startup-shutdown-and-maintenance.md). Use only tool operations and recipes reported as supported by the current runtime. Tool presence alone does not qualify physical hardware.

## Offline support evidence

For an offline diagnosis or support-export request, follow [local diagnostics and selective export](../../references/installation-and-adoption.md#bundled-local-diagnosis). Leave paused and unknown source operations unchanged; exporting evidence does not require a lease, abort, homing or reconciliation. Select raw artifacts only by their explicit IDs, hashes and camera/document scope. Keep the bundle local unless sharing is requested. If publication is uncertain, verify the existing output before any retry; do not delete evidence or replay machine work. Exporting a historical summary does not establish current state or manage runtime retention.

## Workflow

1. Inspect capabilities/status, recent operation evidence, configuration, and the selected manufacturer maintenance/startup/shutdown procedure. Reconcile unresolved work before a maintenance action.
2. Compare symptoms with actual measurements and history; separate lighting/vision drift, loose mechanics, nozzle wear, leaks, and material problems. A clean log or commanded actuator state does not establish health.
3. Use supported camera/sensor/part-state measurements and bounded calibration tests to investigate. Prepare any changes through typed configuration plans; coordinate cleaning, tightening, and replacement as explicit physical tasks.
4. After replacement or adjustment, identify invalidated calibration and obtain supported revalidation. The shipped `openpnp_validate_calibration` only reads an existing nozzle-runout model; it does not establish new measurements after maintenance. Do not transfer another tool's measured offsets.
5. For shutdown, resolve held parts, park only on a valid qualified path, preserve the journal/report, and apply the profile's supported enable/disconnect sequence. Return any restart/homing requirement.

## Completion and recovery

Return the diagnosis with evidence and uncertainty, physical tasks completed/pending, affected calibration, and final machine/occupancy state.

Stop on unexpected motion/occupancy, unsupported repair procedure, or missing post-maintenance evidence; do not infer a universal service interval. If a dependent tool or capability is unavailable, complete the supported inspection or preparation and name the exact remaining gap; do not imitate execution or use raw commands as a fallback.
