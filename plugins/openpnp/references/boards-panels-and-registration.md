# Boards, panels, and registration

A reusable board definition and a physical board load are distinct. Record board/panel/load identities, selected side, fixture/support/height, relevant calibration, child-instance enablement, and registration. Each new copy starts new physical history; a flip of the same board preserves its placement history.

For nested panels, keep parent/child transforms and reference-designator identity separate. Arrays and repeated child designs must not collapse into a single board. Apply defective-board exclusions to the intended physical instance. OpenPnP supports board/panel relationships and per-instance configuration. [Panels](https://github.com/openpnp/openpnp/wiki/Panels)

Choose the profile's supported fiducial/transform model. Two distinct fiducials can be sufficient for one model; more observations do not rescue ambiguous identities or a degenerate geometry for another model. Respect legitimate bottom-side mirroring and rotation. Fiducials locate board registration; their exact availability and use depend on the selected board and machine setup. [Fiducials](https://github.com/openpnp/openpnp/wiki/Fiducials)

Before locating, verify the initial search pose and fixture/underside-component clearance. Use the deterministic returned observations and residuals. Do not fabricate fiducials or switch to an unqualified fiducial-free transform to keep a run moving.

The shipped `openpnp_locate_fiducials` operates on the currently loaded native job's enabled board locations and takes no board selector. It can move the simulator and update registration, requires session/request IDs, and returns an operation with native result/board locations. It does not create a physical board-load record or provide an independent residual-measurement report. Revalidate the changed job and report any absent transform diagnostics explicitly.

Moving/reclamping/flipping a board invalidates registration. Re-register and revalidate before dependent placements, preserving verified history. An OpenPnP board object ID alone is insufficient evidence that the same physical board remains clamped.

## Candidate native load records

The [55-tool catalog](native-placement-editing.md#version-and-authority) adds `openpnp_get_board_loads` and `openpnp_register_board_load`. Read actual installed support first. The registry exposes exact root and descendant IDs, `load_id`, job/load revisions, side, retained placement-history counts, definition matching and unresolved presence. Inspect `complete_native_history`: explicit changeover requires the additive native complete-history API. Stock compatibility can initialize fresh simulator imports but cannot claim complete explicit changeover.

`openpnp_register_board_load` takes session/request IDs, `expected_config_revision`, `expected_board_load_revision`, the current `job_id`, exact `root_instance_id`, `side` (`top`/`bottom`), and one action:

- `replace`: explicitly install a new simulated board/panel load. Creates new load/descendant identities and resets the current root's native history while retaining the previous load's ledger. Supply `expected_load_id` whenever that root has an existing binding; omit it only for a genuinely new root.
- `flip`: select the opposite retained side of the same load, requiring its `expected_load_id`. Preserve the entire history, including other-side, disabled and orphaned native keys.
- `same-load`: explicitly confirm/rebind the exact retained load, side, shape and history, requiring `expected_load_id`. A changed definition/pose/history or interrupted loading cannot be silently accepted.

The call returns an operation. Every successful load action invalidates registration and production validation; it neither moves a fixture nor verifies physical presence/underside clearance. A restored document or restarted bridge requires explicit current presence confirmation before validation. Read fresh revisions after each action. Never use replacement to erase an unresolved placement on the same physical board.

Use [native inspection](native-placement-editing.md#read-the-complete-native-model) to discover exact disabled/opposite-side/fiducial records. These software records and native `locate_fiducials` results do not substitute for physical loading or independent transform-quality evidence.
