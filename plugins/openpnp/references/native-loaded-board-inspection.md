# Inspect a completed native loaded board

Use this workflow when `openpnp_get_capabilities` advertises
`loaded_board_inspection.profile: "native-loaded-board-inspection-v1"` and
`request_available: true`. It is available through the local GUI simulator.
The headless simulator can read retained receipts but cannot open or submit a form.

## Request the local form

1. Observe the original job operation through native completion. Read full
   `openpnp_get_status` and `openpnp_get_board_loads` to select the current job,
   its configuration/job/load revisions, and one exact `loaded_board_id`.
   Repeated instances of the same board definition have separate identities.
2. Disable the simulator through `openpnp_set_machine_enabled` and observe that
   operation through completion. The native job must remain `completed`, all
   nozzles must be empty, and there must be no unresolved operation.
3. Call `openpnp_request_board_inspection` with the active `session_id`, a fresh
   canonical UUID `request_id`, `expected_config_revision`, `job_id`,
   `expected_job_revision`, `expected_board_load_revision`, and `loaded_board_id`.
   Use values read from this instance; do not invent revisions or IDs.
4. Follow the returned request operation. Its successful result contains
   `task_id` and a captured `snapshot`. The local form appears only after that
   short native request has durably completed. Keep the lease current while
   waiting; the form expires after five minutes.

One task covers all 1–200 enabled ordinary placements on the selected enabled
board's active side. Native placed history must say each was placed. Disabled
records, opposite-side placements and fiducials are outside that task's required
coverage. Native history supplies identity and progress, never measurement data.
Required placement coordinates are bounded to ±10,000 mm X/Y, ±1,000 mm Z and
±360 degrees rotation; placement and part IDs are at most 128 characters.

## Record local observations

The local operator fills the form. Codex has no MCP tool for submitting its
observations. The operator supplies a label, note, XY/rotation tolerances and a
presence/polarity observation for every required placement. For a present part,
enter holder-frame X/Y residuals in millimeters, rotation residual in degrees,
and uncertainty in the same units. Unknown or missing parts stay explicit;
blank fields do not mean zero, present, or correct. Polarity applicability is
operator-reported.

Optional evidence references use existing **native** artifact UUIDs, their exact
SHA-256 hashes and a kind: `image`, `measurement-file`, or `operator-note`.
The Bridge verifies and retains the bytes already in its artifact store; paths
and remote URLs are not accepted. At most 32 references and 32 MiB total content
are allowed. This verifies stored bytes, not the source or accuracy of a
measurement. Local offline canonical artifacts use a different store.

Exact entered decimals determine tolerance decisions. A definite failure is
`failed`; an uncertainty interval crossing a tolerance or unknown observation
is `uncertain`; `passed` requires complete coverage within the entered limits.
For simulator tests, label synthetic observations as synthetic. Do not derive
observations from native placed flags or describe synthetic values as metrology.

## Read the outcome and recover

Read `openpnp_get_board_inspection` with `task_id`. Its `submission_operation`
identifies the distinct local submission operation; it does not reuse the MCP
request operation. Read the retained receipt and use `openpnp_get_native_artifact`
for its `artifact_id`. The receipt binds the captured scope, entered observations,
evaluated result and verified evidence references.

Invalid fields may be corrected before admission. An admitted submission consumes
the local task once. A native model, identity, configuration, load, history or
ownership change makes its captured scope stale. Cancelled, expired or revoked
forms cannot be resumed. Request a fresh scope after a known pre-submission
failure. Retain the original IDs if an outcome or journal publication is unknown;
do not resubmit to make uncertainty disappear. A restart restores historical
records without restoring a form, callback, lease or submission authority.

These are local operator reports bound to native simulator state. Operator labels
and instruments are not authenticated. A receipt grants no production or physical
qualification, advances no batch, resolves no unknown feed, and changes no placed
history. A failed inspection does not make the component absent or authorize
placing another part over it. The journal retains at most 64 inspection tasks
without evicting existing evidence.
