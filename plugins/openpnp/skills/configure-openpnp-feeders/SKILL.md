---
name: configure-openpnp-feeders
description: Configure, load, calibrate, or refill supported OpenPnP strip, tray, and powered feeders with part and consumption tracking.
---

# Configure OpenPnP Feeders

Read [the runtime/control contract](../../references/control-contract.md) and, for this workflow, [configure openpnp feeders guidance](../../references/feeders-and-materials.md). Use only tool operations and recipes reported as supported by the current runtime. Tool presence alone does not qualify physical hardware.

## Workflow

If both native material-load tools are advertised, follow [virtual tray load changeover](../../references/virtual-tray-loads.md). Configure before enrollment, bind the exact observed tray, and replace only a full same-part virtual tray between jobs. Skip the generic feed-test step for enrolled trays; retain unknown outcomes and previous consumption.

1. Inspect capabilities/configuration and resolve the physical feeder, slot/address, assigned part/lot, tape or tray geometry, remaining quantity, and pickup pose.
2. Prepare the supported assignment or geometry change with `openpnp_plan_configuration`. Check pitch, index, orientation/polarity, height, usable pockets, and nozzle compatibility; apply the returned plan.
3. Coordinate loading/refill through the actual operator evidence path. A powered feeder identity and its mounted slot geometry are separate facts.
4. Use `openpnp_test_feeder` or a supported `openpnp_handle_part` recipe for a bounded feed/pick test. Inspect both the material position and nozzle occupancy; include native retry consumption.
5. After failure or timeout, inspect the recorded operation and fresh physical state before advancing again. A repeat of the request ID queries its result; a new request could consume another pocket.

## Completion and recovery

Return feeder/slot/part binding, known next pickup state, consumption/remaining estimate, and test evidence.

Stop when part identity/polarity is unresolved, material position is uncertain, or the feeder class lacks a qualified adapter. If a dependent tool or capability is unavailable, complete the supported inspection or preparation and name the exact remaining gap; do not imitate execution or use raw commands as a fallback.
