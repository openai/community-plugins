# Local simulator sensing recovery

This is the S79 development workflow. Use it only when the connected runtime advertises the request tool and `sensing_reconciliation.available: true` with profile `native-simulator-sensing-reconciliation-v1`, `simulation_only: true`, and `hardware_qualified: false`. Restart additionally requires `restart_request_available: true`. The packaged S78 baseline does not expose these requests. Check the exact runtime's qualification: bounded native Bridge restart tests do not establish full GUI, packaged, or physical qualification.

## Choose the exact workflow

Read full status, the original operation, capabilities, and the retained sensing records. A recovery request is distinct from retrying the failed operation.

| Kind | Required scope | Local procedure |
| --- | --- | --- |
| `restore-sensing-readiness` | Standalone sensing fault with no job, held material or workpiece dependencies | Replace the declared synthetic source, perform fresh native empty-nozzle probes, disable, and publish a separate recovery receipt after native task completion. |
| `replace-faulted-job-attempt` | Exact faulted native job, original operation, job/configuration/board/material revisions, and the complete affected dependency set | Dispose material through the native discard path, verify every shared-source nozzle, retire the old trays, quarantine the old board attempt, and create a separate replacement job with new loads and lineage. |
| `continue-faulted-job-replacement` | Canonical `replacement_attempt_id`, current configuration revision, and the exact retained native transaction | Under a new local decision, complete or adopt recorded steps, replace uncertain loads with new identities, repair/dispose/probe the current source, and publish the candidate for fresh validation. |
| `restart-faulted-job-replacement` | Canonical `replacement_attempt_id`, current configuration revision, explicit source-absent restart capability, and fresh local GUI attestation | Reattach inactive graphs, record prior-process disposition, install an unready fresh synthetic source, and observe loads. Complete only the observation phase; a separate continuation is required. |

A job replacement does not resume the old board. Keep every old feed, release, placement and unknown outcome in the report. A new simulator tray starts full by explicit local choice; that is separate from the old tray's consumption history.

GUI recovery also requires the private S79 native ownership patch described in [BUILD_GUI.md](../bridge/BUILD_GUI.md#local-ownership-and-cleanup). External job publication preserves the prior native graph and history while moving the GUI's title/file listeners to the selected job. The external owner must release retained graphs and model subscriptions after their last operation or recovery dependency ends. A publication, token release or unknown outcome alone does not authorize that cleanup. The unchanged native API version does not identify this behavior; verify the exact patch/runtime provenance. The bounded real MainFrame component regression is separate from combined Bridge restart and release qualification.

Before admitting a job that uses this recovery profile, configure and enroll its finite trays through the advertised typed tools. The material adapter requires 1–100 pockets on each axis, at most 10,000 total, and the supported native geometry. Some generated sensing fixtures begin with a 10,000-by-1 tray; configure a supported geometry before load enrollment and job execution. Do not change the admitted tray geometry after a fault to bypass a recovery refusal.

## Request and observe

1. For current-process recovery, wait for the original native task wrapper to finish and disable through the supported tool. A cancelled Future alone is not proof that native work ended. For a crashed prior process, use the explicit restart sequence below; its missing wrapper remains unknown. Preserve pending completion or journal failures rather than repeating their effects.
2. Supply `session_id`, a new lowercase canonical UUID `request_id`, `expected_config_revision`, and the chosen `recovery_kind`. Job replacement additionally requires:
   - `job_id` and `original_operation_id`: the discovered UUIDs.
   - `expected_job_revision`: the current native job digest.
   - `expected_board_load_revision`: the observed `load-N` value.
   - `expected_material_revision`: the observed `material-N` value.
   Continuation and restart instead require `replacement_attempt_id`; omit the original-job replacement fields for those kinds.
3. Observe the asynchronous request operation. When it succeeds, retain its `task_id` and use `openpnp_get_sensing_reconciliation`. A lost response requires lookup by the original request ID; do not issue another request to replay an unknown action.
4. The five-minute local form displays the captured faults, source, affected loads, and exact proposed effects. The local operator chooses its one-use action. There is no MCP tool to submit a decision, invent observations, clear faults, or set placement history. Revocation, expiry or changed scope requires a fresh decision.
5. Observe the separate local recovery operation and task. For source repair, replacement or continuation, require `state: resolved_for_current_scope`, `live_resolution_activated: true`, the forced final receipt, and successful native wrapper completion. Successful probe readings alone are insufficient. Restart has a distinct observation-only completion described below. Any missing outcome remains unresolved.
6. For a replacement, read the returned new job and fresh board/material revisions. Enable/home using the supported simulator procedure, register or locate as required by that new setup, and freshly validate the new job before starting it. Do not reuse the old job's validation or add its unknown placements to the replacement's completed count.

## History and restart

`openpnp_get_sensing_reconciliation` remains readable when the current source or local GUI grant is absent. Historical task/receipt fields describe the old process; they grant no new lease, source, occupancy, registration, load binding or readiness.

Development runtimes can also return `job_replacements`. Each transaction identifies the original and replacement attempts and reports separate lineage, material, board, definition, document, compound-receipt and publication phases. `completed` describes a forced past receipt. It does not mean that the replacement is currently loaded, unconsumed, registered or ready. For example, a completed tray replacement remains completed after a later job consumes a pocket; `current_matches_replacement_receipt` then becomes false while the old receipt stays unchanged.

An unmatched intent remains `pending`. A saved document preserves the candidate's definitions and source mapping for a later explicit reattachment; reading its journal record does not verify current storage or reconstruct the job. If `job_replacement_progress_error` is present, retain the returned historical task/receipt and report that exact progress limitation. Do not infer the missing phase from the displayed job or a zero feeder counter.

When `continuation_supported: false`, preserve the transaction and its exact limitation. A new replacement of the original attempt cannot stand in for continuation. When supported, continuation binds the existing transaction, adopts verified completed receipts and explicitly disposes unknown effects under a new local decision. An old callback or saved document supplies no such decision.

### Explicit source-absent restart

Use the explicit launcher command with the **original** GUI session directory printed by `start-gui-simulator --profile vacuum-sensing`:

```sh
node scripts/openpnp.mjs restart-gui-simulator \
  --session-dir /absolute/original/gui-session \
  --state-dir /absolute/installed-plugin-state \
  --openpnp-home /absolute/verified-openpnp-runtime
```

`--java` optionally selects the Java executable. There are no raw configuration, journal, manifest, JVM-option or automatic-grant arguments. The original `launcher.json` fixes the exact configuration, bootstrap, prepared manifest, token and journal paths. The installed bridge must explicitly advertise the `native-gui-source-absent-restart-v1` startup profile; its runtime must carry the exact retained-job GUI ownership patch. An older build is refused before Java starts. If the installer refuses different contents at an existing version, install the matching build into a separate private `--state-dir`; keep `--session-dir` pointed at the original session.

The launcher keeps original files in place and records a separate `restart-attempts/restart-*` receipt with fresh home and in-memory preferences. Saved configuration may differ from its initial prepared manifest; the manifest remains provenance, while a bounded current inventory and closed NullDriver/XML/script preflight run before native class loading. This launcher profile requires all seven saved native configuration files and empty native board/panel libraries. Nonempty library lists are refused because native startup recursively loads their external XML; the launcher never clears those lists. Unknown classes, external camera resources, active scripts other than the verified bootstrap, writable or symbolic-link inputs, empty/missing history and conflicting processes are refused. The current native GUI still independently attests the live graph and locks/replays the journal. These file and process checks do not prove native ownership, material presence or sensing readiness.

Keep this launcher in the foreground. Complete Welcome if shown, run `Scripts → codex-bootstrap.js`, and make the local control grant in the visible controller. The launcher verifies the same historical machine ID and an absent current source before writing a connection. It never prepares a source or submits a recovery decision. Closing the GUI or Ctrl-C preserves the original session and records the result. Cancellation requests termination of this launcher’s child; after five seconds it forcibly stops only that child if needed, records `forced_stop: true`, and releases the reservation only after exit. A force-stopped native operation remains unknown.

A simultaneous restart is refused by the original session’s `restart-reservation`; legacy process arguments are checked as well. An abandoned reservation is preserved for inspection. Do not remove it while its launcher or GUI may still own the configuration/journal. A stale reservation requires an independent ownership check and explicit manual removal of that reservation directory only; the launcher never deletes or reconstructs historical configuration or journal files to recover a lock.

1. Read the historical replacement attempt and exact pending phases. Use the verified command above with its original configuration, prepared manifest identity and journal; this must leave its source absent. The local user grants control. Do not restore a historical source or create a source through an ordinary fixture setup to bypass this step.
2. Confirm `restart_request_available: true`, then request `restart-faulted-job-replacement` with the current session, request UUID, configuration revision and existing attempt UUID. The task labels its old source bindings as historical provenance; a new source ID cannot be claimed before installation.
3. The local restart action captures current saved files and native objects on the GUI thread. Its native callback records exclusive current ownership and preserves old unknown outcomes. It observes current tray counters and complete board histories, creates a fresh source with unknown nozzle material, and selects the exact original job as inactive.
4. Require a successful new local operation with actual completed native wrapper and task state `restart_observations_completed`. This receipt must retain `faults_resolved: false` and `execution_authority_restored: false`. It does not qualify material presence, clear sensing faults, or authorize a job run.
5. Obtain fresh status and request `continue-faulted-job-replacement` for the same attempt under a separate local action. Observe its complete source/load disposition and `resolved_for_current_scope` state as above. Then perform fresh registration and validation before starting the new replacement job.

A replayed task cannot be submitted again. General material-load reattachment remains unsupported outside the exact advertised replacement workflow. Required interruption windows and combined GUI/package qualification are still in progress. Report exact refusals and pending receipts; do not substitute generic operation abandonment, counter resets, cleared placed flags, earlier board files, or weaker sensing thresholds.

## Report

Include the original operation IDs and outcomes, disposal actions, probe source generations, old and new load IDs, replacement job ID, receipt ID, new validation, and actual replacement completion counts. Keep simulation results separate from physical inspection. These signals are synthetic native-actuator inputs, not pneumatic physics or independent occupancy measurements.
