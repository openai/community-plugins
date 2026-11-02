---
name: configure-openpnp-tooling
description: Configure or change OpenPnP nozzles, tips, vacuum, air, and part sensing for the selected machine and packages.
---

# Configure OpenPnP Tooling

Read [the runtime/control contract](../../references/control-contract.md) and, for this workflow, [configure openpnp tooling guidance](../../references/tooling-and-pneumatics.md). Use only tool operations and recipes reported as supported by the current runtime. Tool presence alone does not qualify physical hardware.

## Workflow

1. Inspect capabilities/configuration/status; identify installed nozzles/tips, occupancy, compatible packages, working heights, and mapped pneumatic devices.
2. Stage the supported tooling changes with `openpnp_plan_configuration` and apply the returned plan. A copied nozzle identity does not inherit valid measured calibration.
3. Use `openpnp_change_nozzle_tip` only when the returned capability supports the installed changer or manual-change continuation. Coordinate actual manual installation; never mark it complete from agent-authored text alone.
4. For the advertised controlled simulator profile, follow [native vacuum sensing](../../references/vacuum-sensing.md): use a current session, revision and canonical request UUID for `openpnp_measure_sensor` or `openpnp_verify_part_state`, then observe the returned operation. Part-off verification actively pulses vacuum and requires Safe Z. Preserve source generation, units, thresholds, occupancy and unresolved outcomes. Use `openpnp_control_actuator` only for the known device operation in the active recipe.
5. Validate required calibration and a bounded `openpnp_handle_part` test when supported. Account for held parts and consumption even outside a production job.

For a new isolated simulator nozzle, follow [complete nozzle assembly](../../references/simulator-nozzle-assembly.md). Use the advertised compound change in its own retained configuration plan, collect generated axis/nozzle/tip/valve IDs, and verify an actual mixed native job uses both old and new tooling. Its explicit simulated initial tip state is separate from a physical manual installation. Preserve the forced portable recovery preimage; typed subset restore cannot undo object creation.

When the runtime advertises local sensing reconciliation, use [the explicit recovery workflow](../../references/sensing-reconciliation.md) after a retained sensing fault. Standalone sensor repair cannot settle job/material dependencies; use the exact job-replacement kind only when available. Historical receipts never restore current readiness.

## Completion and recovery

Return installed/verified tooling, compatibility, measured sensing evidence, occupancy, and outstanding calibration or physical tasks.

Stop on unknown occupancy, indistinguishable sensing distributions, unsupported automatic changing, or uncertain actuator effects. If a dependent tool or capability is unavailable, complete the supported inspection or preparation and name the exact remaining gap; do not imitate execution or use raw commands as a fallback.
