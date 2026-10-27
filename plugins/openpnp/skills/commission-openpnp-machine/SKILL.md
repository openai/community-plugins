---
name: commission-openpnp-machine
description: Configure a new or changed OpenPnP machine from its selected hardware profile while preserving valid existing calibration.
---

# Commission an OpenPnP Machine

Read [the runtime/control contract](../../references/control-contract.md) and, for this workflow, [commission an openpnp machine guidance](../../references/commissioning.md). Use only tool operations and recipes reported as supported by the current runtime. Tool presence alone does not qualify physical hardware.

## Workflow

For the dedicated owned-controller profile, follow [owned-controller diagnostic guidance](../../references/controller-diagnostics.md); it exposes a fixed communication diagnostic and no configuration/calibration adapters. Do not apply the machine commissioning workflow or substitute raw commands.

1. Read capabilities, status, and configuration; resolve the exact controller, cameras, nozzles, feeders, host build, and profile. Separate factory/template values from measurements for this machine.
2. Build a dependency-ordered configuration plan with `openpnp_plan_configuration`; include referenced objects, physical setup steps, affected shared definitions, and calibration invalidation. Retain a complete supported backup.
3. Run `openpnp_list_issues` under its required session/request and follow its operation; the shipped tool performs a fresh native scan. Classify results using the manufacturer procedure. Its findings are not directly applicable; use `openpnp_plan_solution` only if separately supported.
4. Apply the scoped configuration/solution with `openpnp_apply_configuration` or `openpnp_apply_solution` only while quiescent and under the required session. Refresh stale plans instead of changing their expected revisions.
5. Run only calibration recipes reported by the current runtime through `openpnp_run_calibration`. Inspect nozzle-tip runout with `openpnp_validate_calibration`; its fitted offsets are not independent residual measurements. For available simulator camera planar-scale measurement, follow [camera calibration guidance](../../references/camera-and-vision-calibration.md#planar-camera-scale-measurement), retain the images and holdout checks, and preview/apply its available proposal separately. Refresh registration and job validation after geometry changes. Keep physical handoffs and unresolved dependencies explicit.

For new simulator topology, use [complete nozzle assembly](../../references/simulator-nozzle-assembly.md) when advertised. This creates attached tooling with independent Z/Rotation and shared existing X/Y, returns generated native IDs, and revokes dependent validation. Qualify the saved/adopted assembly with mixed native placements, including the original nozzle. No physical machine is needed for this simulator workflow.

## Completion and recovery

Return the saved revision/backup, completed measurements, allowed manufacturer exceptions, unresolved prerequisites, and readiness scope.

Stop on partial save, unexpected hardware identity, unqualified native solution, or an operator step without the required evidence. If a dependent tool or capability is unavailable, complete the supported inspection or preparation and name the exact remaining gap; do not imitate execution or use raw commands as a fallback.
