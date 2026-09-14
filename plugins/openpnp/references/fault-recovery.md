# Fault classification and recovery

Read the original operation/action IDs and journal, then obtain fresh board/feeder/nozzle observations. Classify the physical effect separately from the software error. A request rejected before dispatch can be replanned; an accepted timeout or crash may have already fed or placed.

For a disconnected bridge or a requested offline journal review, use the bundled `diagnostics` CLI in [installation and diagnosis](installation-and-adoption.md). Preserve its source hash and anomaly flags. Its sanitized historical summary is useful for locating original request/operation evidence; it cannot infer current occupancy, authorize reconnect, or settle an uncertain action. The summary intentionally omits request payloads and is not a substitute for supported live reconciliation.

| Observation | Required next decision |
| --- | --- |
| Known empty pocket, no held part | Use the remaining qualified native retry budget and known next pickup state |
| Feeder advanced, pick outcome unknown | Inspect occupancy and next pocket before any feed |
| Vision mismatch | Preserve the image/result; use a qualified alternative or diagnose, without weakening thresholds automatically |
| Board moved | Invalidate registration and re-register the affected physical instance |
| Reset or depowered axes | Treat position as uncertain; inspect occupancy/clearance before any qualified homing |
| Release occurred but checkpoint missing | Resolve actual part presence on board/nozzle/discard before retry |
| Inspection rejected | Hold/rework the physically present part; preserve placement history |

`openpnp_plan_recovery` should identify the bounded action and its prerequisites. `openpnp_apply_recovery` is useful only when that capability and current scope are valid. If either is absent, return the exact unresolved observation and prepared recovery sequence; do not implement it with raw commands.

The shipped `openpnp_reconcile_operation` is limited to `outcome_unknown` operations from a prior simulator instance. After that simulator has reset, use disposition `abandon-after-simulator-reset` under a current session to record abandonment, retaining `previous_physical_outcome: "unknown"`. It does not repeat the old action, resume its processor, establish physical occupancy, or reconcile an unknown action from the current instance. When native capabilities advertise action observers, inspect the original operation's `native_action_ledger` or recovered `native_action_recovery`: unmatched intents, safety gaps, and `requires_reconciliation` remain unresolved. Native hook completion records software-reported action boundaries, not independently measured occupancy. Stock runtimes without the observer retain only coarser native-step checkpoints. Explicit abandonment survives later restarts while preserving historical unknown facts.

Configuration restore does not roll back tape position, a held part, or PCB contents. Reconnect does not recover authority. Report what is reconciled and what remains unknown; retain the latter across restarts. The native workflow permits correction/restart, but the bridge's durable uncertainty contract must still be satisfied. [Job processing](https://github.com/openpnp/openpnp/wiki/Job-Processing)

Loading an older [native job document](documents-and-importers.md) restores its recorded placed history, which may differ from the physical board; it cannot recover the prior processor or clear an unknown operation. Native job saving is rejected while running or paused, so it is not a fault-recovery checkpoint. Preserve the interrupted operation and obtain the exact missing observation instead of aborting, loading older history, or clearing placed flags to force progress.

The candidate [load registry](boards-panels-and-registration.md#candidate-native-load-records) distinguishes unconfirmed presence from an interrupted loading outcome. `same-load` cannot accept changed history/geometry or unknown loading. The [placement editor](native-placement-editing.md) refuses changed placed records and provides no history-reset operation. A publication/rollback failure leaves the Bridge fault fence active; keep its operation evidence and investigate the native model instead of bypassing the fence. Inspector source fingerprints bind software history, not independently observed physical occupancy.
