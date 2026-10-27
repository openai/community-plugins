---
name: validate-openpnp-job
description: Assess staged OpenPnP job readiness through supported offline checks, physical dry runs, or independently inspected first articles.
---

# Validate an OpenPnP Job

Read [the runtime/control contract](../../references/control-contract.md) and, for this workflow, [validate an openpnp job guidance](../../references/validation-and-inspection.md). Use only tool operations and recipes reported as supported by the current runtime. Tool presence alone does not qualify physical hardware.

## Workflow

1. Inspect capabilities/status and resolve the exact job/load/material/calibration revisions. Select the requested validation mode; offline simulation, physical dry run, and first article prove different things.
2. For a local imported artifact, use `openpnp_validate_imported_job`. For a loaded native job, inspect supported `openpnp_get_board_loads` and resolve [load presence/history requirements](../../references/boards-panels-and-registration.md#candidate-native-load-records). Call `openpnp_validate_job` under its session and follow its operation. The native model preflight checks pending placement parts/packages/heights, compatible tooling/calibration, feeder assignment and known capacity; it reports unknown capacity explicitly. The processor checks again at start. Model validity does not independently establish physical clearance, registration quality or placement quality.
3. For physical dry run or first article, use only the returned qualified recipe and existing authorized scope; invoke supported job controls as needed. Turning vacuum off in an ordinary job is not a dry-run recipe.
4. For an advertised `native-loaded-board-inspection-v1` form, follow [native loaded-board inspection](../../references/native-loaded-board-inspection.md): request the completed, disabled simulator's exact loaded-board scope, let the local operator enter observations, and read the separate submission operation and receipt with `openpnp_get_board_inspection`. Do not fill measurements from native placed flags. For a local canonical artifact, `openpnp_record_inspection` evaluates supplied measurements; read its receipt with `openpnp_get_artifact` and use `openpnp_assess_inspection_coverage`. Follow [the offline boundary](../../references/validation-and-inspection.md#shipped-offline-inspection-tools). Neither path authenticates metrology or qualifies production.
5. Bind any accepted qualification to the actual revisions and sample coverage. Failed inspection produces a hold/rework disposition, not a reset to pending or automatic duplicate placement.

## Completion and recovery

Return validation mode, checked scope, accepted/rejected findings, inspection coverage, exact prerequisites, and resulting eligibility.

Stop production qualification when only simulator/native-completion evidence exists, inspection fails, or required coverage and thresholds are unset. If a dependent tool or capability is unavailable, complete the supported inspection or preparation and name the exact remaining gap; do not imitate execution or use raw commands as a fallback.
