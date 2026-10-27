---
name: run-openpnp-job
description: Start, step, supervise, pause, resume, or abort a validated OpenPnP placement job or bounded production batch.
---

# Run an OpenPnP Job

Read [the runtime/control contract](../../references/control-contract.md) and, for this workflow, [run an openpnp job guidance](../../references/job-control-and-changeover.md). Use only tool operations and recipes reported as supported by the current runtime. Tool presence alone does not qualify physical hardware.

## Workflow

1. Inspect capabilities/status, exact job/load revisions, current placement state, and validation. Confirm the requested batch size and existing locally enabled control scope.
2. Use `openpnp_get_control_session` to verify current authority; request a pending local session with `openpnp_request_control_session` only if needed and supported. A simulator grant never authorizes real hardware.
3. For continuous execution, call `openpnp_start_job` once for the validated scope. For advertised single-step execution, begin with `openpnp_step_job` using `job_id` and the current `expected_config_revision`; advance its paused operation with `operation_id` and the current revision. Supply exactly one identity. Retain that original operation ID throughout. One step means one native processor `next()` call and may include several motions/actions or no placement; the native processor owns feed, pick, alignment, and placement timing. Observe the returned pause or completion before advancing again.
4. Follow `openpnp_get_events` or poll `openpnp_get_operation` with `view: "progress"`. Use status's separately timestamped `job_progress` for current counts; a running operation may retain its previous paused result. Read the operation once with `view: "full"` at pause/terminal for complete evidence, following retained pages when needed. Renew `openpnp_renew_control_session` while actively supervising, within existing scope/expiry. Use `openpnp_pause_job`, `openpnp_resume_job`, or `openpnp_abort_job` for user steering with the returned semantics.
5. Reconcile timeout/unknown outcomes before further actions. For advertised virtual tray loads, read `openpnp_get_material_loads` and follow [explicit full-tray changeover](../../references/virtual-tray-loads.md) between jobs; a replacement requires fresh job validation and retains old consumption. When advertised, read `openpnp_get_board_loads` before `openpnp_register_board_load` and follow [exact replacement/flip/confirmation semantics](../../references/boards-panels-and-registration.md#candidate-native-load-records). Preserve identity/history and revalidate after load changes. Do not edit placements while the native processor is running or paused. Finish with `openpnp_export_run_report` and the qualified shutdown/handoff procedure when requested.

## Completion and recovery

Return runtime origin, job/batch and physical loads, final operation state, placed/inspected/skipped/failed/unresolved counts, and the report reference.

Stop new work on lost ownership, expired scope, unknown physical outcome, failed validation, or an unqualified live-control capability. Do not auto-resume on reconnect. If a dependent tool or capability is unavailable, complete the supported inspection or preparation and name the exact remaining gap; do not imitate execution or use raw commands as a fallback.
