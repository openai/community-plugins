# Native job control and changeover

Resolve the requested job/revision, actual physical loads, validation scope, batch size, remaining placements, and current authority. A requested run can include normal cycles and bounded retries without prompting for every move. Extra boards, different material, or changed calibration require updated scope.

Start the native job once and follow its operation ID. For continuous execution use `openpnp_start_job`; for single-step execution begin with `openpnp_step_job` and the validated `job_id`. Do not implement an independent placement loop with one model call per motor move. Renew a supported client supervision lease while actively observing, within the existing scope/expiry.

## Single-step simulator execution

Use this only when the current native runtime advertises `openpnp_step_job`. Supply `session_id`, a new `request_id`, and the current `expected_config_revision`, plus exactly one of:

- `job_id` to begin the validated job in single-step mode;
- `operation_id` to advance that job's paused operation.

The boundary is exactly one call to OpenPnP's native processor `next()`. Depending on its planner settings and current phase, that call can include multiple motions or feed/pick/alignment actions, or no placement. It is not one motor move or one completed component. Read the operation until it pauses or completes, and wait for the native busy flag to clear. A normal single-step pause reports `pause_reason: "single-native-step-completed"` and its native step count and completion barrier. Native standstill is simulator evidence, not independent physical inspection.

Keep the original operation ID for every step, continuous resume and abort. A repeated request ID returns its receipt without executing an additional step. `openpnp_resume_job` resumes continuous processing of that same paused operation. Abort can move and discard held material; it must reach its reported completion before another job starts.

If ownership is revoked before a queued step dispatches, the native job and any held part remain paused and retain their operation lock. Read its `pause_reason` and acquire valid authority before any explicit continuation or cleanup. A pending abort blocks stepping/resume. After restart an interrupted step remains uncertain and requires reconciliation; never infer that the last part was placed from a command acknowledgement.

OpenPnP job processing coordinates preflight, registration, planning, tip changes/calibration, picking, alignment, and placement. Its pause is at a processor step boundary; a board-location change can require reinitialization and cleanup may discard held parts. [Job processing](https://github.com/openpnp/openpnp/wiki/Job-Processing)

After a timeout, cancellation, event gap, or reconnect, inspect the durable operation and refresh a consistent snapshot. Do not restart the batch because the client lost its response. Pause/abort/disable follow the runtime's exact semantics, and a software request is not an independent emergency stop.

For a board replacement/flip, record the physical load and side, resolve held parts, check fixture clearance, re-register, and revalidate affected state. Keep the previous board's ledger. End with counts for placed, inspected, skipped, failed, unresolved, consumed material, and interventions. Run the selected shutdown procedure only when included in the requested scope.

When the candidate load tools are advertised, follow [the exact replace/flip/same-load contract](boards-panels-and-registration.md#candidate-native-load-records). Read the current load ID and revision before each change; keep full history across a flip and obtain fresh validation after every load action. Reloads and native placement edits require a new binding decision. The [typed editor](native-placement-editing.md) cannot operate on a running/paused processor or resolve an unknown physical outcome. Simulator-origin load records never verify physical loading.
