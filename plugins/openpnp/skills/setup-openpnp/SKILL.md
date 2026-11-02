---
name: setup-openpnp
description: Inspect OpenPnP plugin installation, connection readiness, available machine capabilities, and existing-machine adoption.
---

# Set Up OpenPnP

Read [the runtime/control contract](../../references/control-contract.md) and, for this workflow, [set up openpnp guidance](../../references/installation-and-adoption.md). Use only tool operations and recipes reported as supported by the current runtime. Tool presence alone does not qualify physical hardware.

## Workflow

When the user asks to test the dedicated controller simulator, follow [owned-controller diagnostic guidance](../../references/controller-diagnostics.md). Launch a new separate state and verify its exact profile/build/controller identity. It does not connect to or qualify the user’s physical controller.

1. Call `openpnp_get_capabilities` and `openpnp_get_status`; identify the returned runtime origin, machine/profile/build, available tools, and compatibility limitations. An isolated simulator result does not describe a connected machine.
2. For an existing installation, call `openpnp_get_configuration` and preserve its active configuration, resource paths, measured calibration, and unknown device types. Identify missing evidence instead of restarting commissioning.
3. If the user requested connection and the capability is available, resolve the exact selected device and call `openpnp_connect` using its declared effect and control requirements. Inspect afterward; connection is not enablement, homing, or production qualification.
4. Use supported backup tools before an adoption change. For a supported portable simulator export, follow [fresh simulator adoption](../../references/portable-simulator-adoption.md), retain the archive hash, and use new adoption/state directories. First launch starts disabled/unhomed under a new identity; a spent activation is not restart permission. Read the installation reference for the bundled `doctor` and offline `diagnostics` CLI when the bridge is missing or unreachable. Resolve the actual installed plugin and state paths; diagnose without inventing an MCP diagnostics operation or editing startup hooks through a guessed command.
5. Return the installation/machine identity, origin, available workflows, existing calibration evidence, and precise missing prerequisite. Route configuration work to the commissioning skill only when requested or necessary.

To add complete tooling after the fresh simulator is running, use [the simulator nozzle assembly workflow](../../references/simulator-nozzle-assembly.md) when its typed change is advertised. Resolve existing native head/driver/X/Y IDs and keep the simulator disabled for creation. No physical-machine details or installation acknowledgement are required for the explicitly simulated initial tool state.

## Completion and recovery

A supported inspection or connection has returned evidence; otherwise provide the compatibility gap and prepared next action.

Stop dependent control on mismatched devices, incompatible builds, missing tools, or an unavailable bridge. If a dependent tool or capability is unavailable, complete the supported inspection or preparation and name the exact remaining gap; do not imitate execution or use raw commands as a fallback.
