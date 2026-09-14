# Shipped tool paths and conditional workflows

The actual MCP schema and `openpnp_get_capabilities` response control call arguments, effects, and supported runtime scope. The implementation plan and these skills do not advertise additional native adapters by mentioning them.

The three read tools `openpnp_get_status`, `openpnp_get_operation` and `openpnp_get_request_status` accept `view: "progress"` for bounded repeated polling. The default `full` view remains unchanged. Progress does not retain an exact snapshot or consume response-store slots; fetch selected complete receipts at pause/terminal. See [polling and exact response semantics](control-contract.md#bounded-responses-and-exact-details).

## Native OpenPnP path

The current native path inspects configuration/status, acquires a simulator-only session (automatic in isolated headless mode; local GUI grant required in GUI mode), enables/homes, plans/executes machine-frame motion, captures raw/settled camera frames, prepares the bundled `pnp-test` job or a supported local imported artifact, checks its native model, and controls supported job execution. Physical-machine qualification remains unavailable. Never reinterpret a simulator session as a physical grant.

The [sustained simulator recipe](sustained-simulator-qualification.md) is separately identified. Its full-soak admission requires actual native `Unsorted` job order for the supplied ordered grid; older runs and the default profile have different recipe provenance.

- `openpnp_request_control_session` takes a request ID and optional bounded TTL. Read/renew/release the returned session using the appropriate session tools.
- `openpnp_plan_motion` takes the session and a pose with explicit millimeters; its current coordinates are the native machine frame. No board-frame pose API is implied.
- `openpnp_plan_configuration` exposes 34 distinct typed change types in this catalog: package/part creation and complete simulator nozzle assembly creation; package footprint/compatibility; part properties/height; feeder assignment/enablement and strip/tray geometry; machine speed; camera geometry and fixed/dynamic settling; nozzle/nozzle-tip settings and the complete `set_vacuum_sensing_settings` change; axis motion limits, typed backlash settings and existing mapped-axis geometry; native job planner/retry settings; head park XY and machine discard pose; part/feeder retries; and seven vision-setting changes. Read the exact fields, bounds and active-profile support in the [installed schemas](../mcp/tool-inputs.json) and returned capabilities. Setting groups replace their named fields; planning is revision-bound and applies nothing. Vision changes support the existing scalar stages and parameters described below. Outside the declared complete simulator nozzle assembly adapter, arbitrary driver/device creation, pipeline XML replacement, arbitrary stages and unsupported native classes remain unavailable.
- Native command schemas require request and session IDs, and may accept the current expected configuration revision. Enable/home, capture, job preparation/validation/control, supported config apply, feeder test, actuator control, tip change, backup, and report export can return asynchronous operations. Follow each operation's outcome before dependent work.
- `openpnp_test_feeder` advances a feeder; it is not a passive test or an implicit pick.
- `openpnp_change_nozzle_tip` uses actual native change semantics. Omitting the tip ID means unload, not retain the current tip.
- `openpnp_run_calibration` supports `nozzle-tip-runout` only, with `nozzle_id` and optional `enable`. `openpnp_validate_calibration` reads that existing model. Returned fitted offsets are correction-model samples, not independent residual measurements.
- `openpnp_locate_fiducials` processes enabled board locations in the loaded native job; it can move and update registration. It does not create a physical board-load record.
- `openpnp_list_issues` performs a fresh native scan under session/request authority; each returned finding declares that applying its solution is unsupported.
- `openpnp_save_job`/`openpnp_load_job` save and reload bounded native job archives under session/request authority. They require idle execution state; save also requires a loaded job. They retain recorded placed history and static definition references, drop transient registration, and require validation before starting. Load accepts only a native artifact saved within the same owned persistent document store with unchanged referenced part/package definitions. Read [native document boundaries](documents-and-importers.md).
- `openpnp_get_native_artifact` reads native camera/backup/report/job-document artifacts by their returned native IDs. `openpnp_restore_configuration` restores a verified retained backup's representable existing typed settings within the [declared restoration scope](backup-restore-and-migration.md), preserving newer material counts and current placed history. Complete portable restore is unsupported.
- `openpnp_reconcile_operation` can record abandonment of a prior simulator instance's unknown operation after reset. It cannot determine the old physical outcome or recover the old processor.
- `openpnp_get_request_status` reconciles the original request if the response/operation ID was lost.

### Native job and load tools

The current 59-tool MCP bundle includes `openpnp_get_board_loads`, `openpnp_register_board_load`, `openpnp_inspect_job`, `openpnp_plan_placement_edits` and `openpnp_apply_placement_edits` for native job and board-load workflows. Use them only when the installed schemas and native capabilities agree. [Inspection and edits](native-placement-editing.md) cover disabled/opposite-side/pseudo records, active page fingerprints, exact revision envelopes, shared versus instance effects, and preserved history. [Load actions](boards-panels-and-registration.md#candidate-native-load-records) require explicit identity and intent; they do not verify physical presence. All except the registry snapshot return executor-owned operations, including inspection/preview. These additions are tested separately from the immutable historical 42 skill scenarios.

## Local canonical job path

`openpnp_import_job` parses supported supplied content into a local canonical artifact. `openpnp_validate_imported_job` checks that artifact. `openpnp_get_artifact` reads it by the returned SHA-256 ID. To load the canonical artifact into the native simulator, pass its returned `artifact_id` to `openpnp_prepare_job` and follow the operation. Alternatively pass the supported bundled sample; the two inputs are exclusive. Importing/validating alone does not load a native job, prove physical registration, or authorize production. A requested production validation mode cannot manufacture missing physical evidence.

## Conditional workflows

The fourteen skills retain conditional workflows for issue-solution apply, unsupported device/driver configuration, independent native measurements, calibration beyond nozzle-tip runout, diagnostic pick/place handling, physical board-load registration, native independent inspection, physical recovery apply, and complete restore/migration. Simulator single-step execution is implemented by `openpnp_step_job` when its native profile advertises support. Offline supplied-measurement inspection receipts and coverage assessment are implemented; they do not collect or authenticate observations or authorize production. Invoke conditional operations only if the installed runtime supplies the corresponding schema and advertises support. Otherwise deliver supported inspection, local import/validation, or the concrete prepared procedure with the missing adapter/qualification stated. Do not invent response objects, recipe IDs, measurements, operator completion, or substitute a raw script/GUI/device command.

No installed metadata, skill evaluation, or simulated run is evidence of physical-machine qualification.

## Native vision configuration

The catalog includes seven typed vision changes with inherited native holder/profile readback. Direct stage edits cover existing exact `BlurGaussian`, `Threshold` and manual `MaskHsv` scalar fields; supported numeric or Boolean parameter edits target an existing native `Threshold` stage. They do not replace pipeline XML or add/reorder arbitrary stages. Use the [vision settings workflow](vision-settings.md), and inspect actual capabilities before calling them. Vision-only patches keep shared effects explicit; typed restoration covers its declared existing-setting groups, including supported dynamic settling and backlash, and does not restore these vision edits.

## Complete simulator nozzle assembly

When advertised by the active profile, `create_simulator_nozzle_assembly` creates the attached nozzle, tip, valve and independent Z/Rotation axes as one retained configuration change. Read the [assembly and recovery workflow](simulator-nozzle-assembly.md) before selecting the existing head and exclusive package compatibility. Partial creation remains fenced after same-state restart; recover the forced preimage into a fresh portable generation.

## Native vacuum sensing

Use these tools only when discovery advertises the requested tool and `vacuum_sensing.available: true` with `native-vacuum-sensing-v1` and source profile `controlled-native-vacuum-v1`.

| Tool | Supported behavior |
| --- | --- |
| `openpnp_measure_sensor` | Collect 1–32 native vacuum readings under the current session/configuration revision. Returns an asynchronous operation with source/units provenance; readings alone do not establish occupancy. |
| `openpnp_verify_part_state` | Perform a configured native part-on or part-off check. Returns an asynchronous operation; part-off actively pulses vacuum and requires supported Safe Z conditions. |

Supply a fresh canonical UUID request ID, then read the returned operation. Reconcile a lost response using its original request ID. Signals are explicit synthetic inputs; settings, model Part, valve commands and replayed history do not grant sensing authority. Follow [native vacuum sensing](vacuum-sensing.md).
