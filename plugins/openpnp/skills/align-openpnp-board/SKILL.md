---
name: align-openpnp-board
description: Register a clamped PCB or panel with supported fiducials and validate the resulting board-to-machine transform.
---

# Align an OpenPnP Board

Read [the runtime/control contract](../../references/control-contract.md) and, for this workflow, [align an openpnp board guidance](../../references/boards-panels-and-registration.md). Use only tool operations and recipes reported as supported by the current runtime. Tool presence alone does not qualify physical hardware.

## Workflow

1. Inspect capabilities/status and resolve the exact job revision, physical board/panel load, side, fixture, and current registration. Reused board definitions are not physical-load identities.
2. If the candidate native load tools are advertised, read `openpnp_get_board_loads` and use `openpnp_register_board_load` with the exact current root/load ID, revisions, side and explicit replace/flip/same-load action. Follow [load action semantics](../../references/boards-panels-and-registration.md#candidate-native-load-records); preserve complete history across a flip and report simulator presence as unverified. Physical loading evidence remains separate.
3. Check fiducial identities, initial search pose, clamps/underside-component clearance, and the profile's transform model. When supported, use [paged `openpnp_inspect_job`](../../references/native-placement-editing.md#read-the-complete-native-model) with a stable source fingerprint to include opposite-side, disabled and derived pseudo records. Derived records are read-only. Two distinct fiducials may be valid; degeneracy depends on the model.
4. Use `openpnp_locate_fiducials` under its declared motion scope. The shipped tool locates enabled board locations in the loaded native job, takes no board selector, and returns an operation. Inspect available native registration evidence and report absent residual diagnostics; it does not create physical-load records.
5. Return the accepted transform and its load/calibration revision. If the board moves or the fixture changes, invalidate registration and revalidate affected work before resume.

## Completion and recovery

Return the physical load/side, fiducial evidence, transform model/residuals, and accepted or unresolved registration.

Stop dependent placements on wrong load identity, unresolved side convention, ambiguous fiducials, or stale registration. If a dependent tool or capability is unavailable, complete the supported inspection or preparation and name the exact remaining gap; do not imitate execution or use raw commands as a fallback.
