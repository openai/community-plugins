---
name: recover-openpnp-job
description: Reconcile OpenPnP feeder, nozzle, board, and placement state after errors, interrupted work, or uncertain physical outcomes.
---

# Recover an OpenPnP Job

Read [the runtime/control contract](../../references/control-contract.md) and, for this workflow, [recover an openpnp job guidance](../../references/fault-recovery.md). Use only tool operations and recipes reported as supported by the current runtime. Tool presence alone does not qualify physical hardware.

## Workflow

For an interrupted owned-controller diagnostic, follow [owned-controller diagnostic guidance](../../references/controller-diagnostics.md) and retain the original request/controller/operation identities. A spent or uncertain generation has no retry or resume path; do not apply job reset/reconcile recipes to it.

1. Read capabilities, `openpnp_get_operation`, and fresh status for the interrupted operation. Use `view: "progress"` for repeated operation/status/request-status polls and one `view: "full"` read at the paused or terminal transition for complete evidence. A resumed operation may retain its earlier paused result; use status's timestamped `job_progress` for current counts. Resolve the last known action, physical board/load, feeder position, nozzle occupancy, and revisions.
2. For advertised material loads, preserve `pending_changes`, `pending_feeds`, exact load identities and [the no-reattachment limit](../../references/virtual-tray-loads.md#unknown-outcomes-and-retained-history). Replayed material history grants no native authority; do not use generic reset/reconcile or configuration restore to bypass it. Classify each affected outcome as known or uncertain using journal and actual observations. For a disconnected bridge or requested journal diagnosis, use the bundled offline diagnostics CLI described in the recovery reference. Its historical summary cannot establish current machine state. Use supported `openpnp_measure_sensor`, `openpnp_verify_part_state`, or inspection tools with truthful evidence provenance.
3. For an unknown operation from a prior simulator instance, `openpnp_reconcile_operation` supports only `disposition: "abandon-after-simulator-reset"`; preserve the old physical outcome as unknown and do not repeat it. For broader recovery, call `openpnp_plan_recovery` only if supported. Identify exact inspection/refill/discard/rehome/recalibration steps and preserve placement history.
4. Use `openpnp_apply_recovery` only after its prerequisites and current session are valid. Neither configuration restore, loading an older job document, nor a client reconnect resets physical material or establishes whether a component was placed. When advertised, read `openpnp_get_board_loads` and [its retained load/history state](../../references/boards-panels-and-registration.md#candidate-native-load-records); unknown loading or changed history cannot be accepted as the same load. The [typed editor](../../references/native-placement-editing.md) has no history-reset operation and cannot bypass a publication fault fence. Native document saving is unavailable while a job is paused; do not abort work just to obtain an archive.
5. Revalidate affected job/load state, then use supported `openpnp_resume_job` only for reconciled remaining work. If evidence cannot settle a placement, retain it as unresolved and request the specific physical observation.

For an advertised local sensing recovery request, follow [local simulator sensing recovery](../../references/sensing-reconciliation.md). Bind the exact original job and revisions; the local operator selects the displayed disposal and replacement. A replacement gets new loads and fresh validation, while the old attempt stays failed or uncertain.

After an interrupted replacement, read its existing transaction phases. Preserve completed subreceipts and pending effects separately, including any saved replacement document. A historical completed phase does not establish current load readiness. If continuation is unavailable, report that limitation and retain the transaction; a new original-job replacement request cannot stand in for continuation.

For a crashed replacement with `sensing_reconciliation.restart_request_available: true`, follow the reference's explicit source-absent restart sequence. Request `restart-faulted-job-replacement` using the existing `replacement_attempt_id` and current configuration revision. The local GUI action supplies fresh attestation; Codex cannot submit it. Require the new operation's completed native wrapper and `restart_observations_completed` receipt, keeping faults and execution readiness unresolved. Then request a separate `continue-faulted-job-replacement` local decision for that same attempt. Run a replacement only after its final disposition, fresh registration and validation. Preserve the old missing wrapper and unknown outcome in the report.

For [native vacuum sensing](../../references/vacuum-sensing.md), keep retained or uncertain occupancy separate from the native model Part. Later ordinary checks, source/configuration changes, homing and operation abandonment cannot clear a sensing fault. If no explicit sensing reconciliation is advertised, report the unresolved evidence and stop dependent actions; do not repeatedly pulse the valve or advance a feeder.

## Completion and recovery

Return reconciled and unresolved action/placement IDs, recovery evidence, consumed material, changed revisions, and resumable remaining scope.

Never directly retry an uncertain feed/release/place, assume homing is safe after reset, or clear placed state to make the job run. If a dependent tool or capability is unavailable, complete the supported inspection or preparation and name the exact remaining gap; do not imitate execution or use raw commands as a fallback.
