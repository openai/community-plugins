# Native placement inspection and edits

## Version and authority

Use the installed schema and `openpnp_get_capabilities` before selecting a workflow. The editor operates on supported native simulator models with the complete-history runtime patch. Structural additions/removals require their own advertised tools and trusted durable lineage; the existence of this reference does not add those capabilities to an older bridge. These workflows do not qualify physical hardware or establish independent inspection.

The historical 42 skill scenarios were evaluated with earlier tool inventories. Their cases and rubrics remain unchanged. New schema fixtures and native/MCP tests cover this addition separately; they do not retroactively extend those behavioral results.

## Read the complete native model

1. Read `openpnp_get_status` and `openpnp_get_board_loads`. Keep the current `job_id`, `job_revision`, `config_revision` and `board_load_revision` together. A definition name is not an expanded board identity.
2. Acquire or reuse the supported native control session. Call `openpnp_inspect_job` with `request_id`, `session_id`, `job_id`, `expected_job_revision`, `expected_config_revision`, `expected_board_load_revision`, and bounded `offset`/`limit`. It returns an operation; follow `openpnp_get_operation` to completion. Inspection runs on the native executor and retains a request receipt but changes no job fields and performs no motion/feed.
3. Read `result.records`. They include disabled placements, ancestor X-outs, opposite-side placements, real panel fiducials, derived pseudo records and native placed flags. `holder_instance_id` and `placement_id` are exact native IDs; `root_instance_id` identifies the replaceable load. Coordinates are holder-local millimeters and degrees. `holder_locally_enabled` and `holder_effectively_enabled` distinguish a local flag from a disabled ancestor. `ordinary_active_side_placement` describes current production selection, not the whole document.
4. Use `next_offset` until `eof`, at most 200 records per page. Pass the first result's `source_fingerprint` as `expected_source_fingerprint` on subsequent calls, along with the original revisions. It includes ordinary and pseudo placed history, which the job revision alone does not cover. `JOB_SNAPSHOT_STALE` requires discarding the mixed snapshot and inspecting again. A truncated MCP result needs its [retained response pages](control-contract.md#bounded-responses-and-exact-details); do not treat omitted records as absent.

The inspector accepts only the editor's bounded native graph. Unsupported custom definitions/outlines remain an explicit limitation. It can inspect a restored document before its board presence is confirmed; it does not thereby confirm that presence.

## Review and apply an edit

Call `openpnp_plan_placement_edits` with the same revision/session envelope plus `changes`. A row selects `scope`, `holder_instance_id`, `placement_id`, and a nonempty `set`. The completed operation returns `result.plan_id`, `result.plan.effects` and a five-minute expiry.

| Choice | Meaning |
| --- | --- |
| `job_instance` | One exact expanded holder/ref; geometry edits clone required native board and ancestor panel definitions for persistence. |
| `job_shared_definition` | Every occurrence of that shared definition within the current job, including disabled or opposite-side instances; other jobs and source definition files stay unchanged. |
| `set.location` | Complete `{frame:"holder", units:"mm" or "in", x, y, z, rotation}`. No implicit machine-frame conversion or side mirroring. |
| Other fields | `side` Top/Bottom; `type` Placement/Fiducial; existing configured `part_id`; boolean `enabled`; `error_handling` Default/Alert/Defer; bounded `comments` and integer `rank`. |

Review every affected reference, before/after values, placed flags and root ID. Combine fields for one target in one row; overlapping rows reject. Do not treat an X-out or opposite-side record as nonexistent. Derived pseudo records are read-only: edit the linked real fiducial and review regeneration. A changed placed record rejects `PLACED_HISTORY_CONFLICT`; editing never clears it or pretends to undo a physical placement.

Apply with `openpnp_apply_placement_edits`, a new request ID, the reviewed `plan_id`, and exact current job/configuration/load revisions. Observe its operation. It preserves the native Job and opaque history, publishes a fully prepared replacement tree, and retires replaced instance listeners. A true no-op preserves registration and load bindings. A real change invalidates registration/production validation and conservatively invalidates load bindings for all returned roots. Re-read the load registry; changed shapes require an explicit replacement. Persist separately with `openpnp_save_job` and verify the returned document artifact when requested.

On stale state, inspect and create a fresh plan. On `PLACEMENT_COMMIT_FAILED` or `PLACEMENT_ROLLBACK_FAILED`, retain the operation and follow the native fault fence; a reported rollback does not reverse arbitrary GUI observer effects. A lost response is reconciled by its original request ID, never by resubmitting the edit with a new ID.

Limits are 1,000 edit rows, 10,000 expanded real/pseudo records, 5,000 copied locations, 1,000 private definitions and eight nesting levels. The setter workflow does not create/remove/rename records. Use the separate structural workflow below for supported additions/removals. Renaming, direct pseudo edits, arbitrary XML/properties, custom outlines, machine-frame edits, and physical-history reset remain unsupported. Do not abort a paused job just to make editing available.

For load actions, see [boards and registration](boards-panels-and-registration.md); for persistence, see [native documents](documents-and-importers.md).


## Add or remove a real placement

Use `openpnp_plan_placement_structure` and `openpnp_apply_placement_structure` only when the installed native bridge advertises them. A structural plan supports 1–100 add/remove rows on existing exact native boards. Shared and instance scope have the same meaning as the setter workflow. All expanded records, including disabled ancestors and opposite sides, remain part of the preview.

The bridge must prove the current job has trusted durable lineage from a fresh simulator import and has never had native processor admission. The machine must be disabled with empty nozzles. Complete current native placed history and associated current/retired board-load history must be empty, including false and orphan keys. A zero placed count, an empty enabled selection, saved XML or a client assertion does not establish these prerequisites. An accepted start/step request consumes structural eligibility even if it stops before the first placement. Do not abort, replace boards or reload an old document to evade this restriction.

1. Inspect the complete native model and current job/configuration/load revisions using the workflow above. Preserve the requested shared or instance scope and the exact holder ID. Resolve every required existing part/package; never invent a missing association.
2. Submit a plan with the normal session/request/revision envelope and `changes`. Each row has `action: "add"` or `"remove"`, `scope`, `holder_instance_id`, and `placement_id`. New IDs start with an ASCII letter/digit and contain at most 128 letters, digits, underscores, dots or hyphens. Removal uses an existing exact real ID.
3. An add row also requires `placement` with **all eight fields**: complete `location`, `side`, `type`, `part_id`, `enabled`, `error_handling`, `comments`, and `rank`. Values match the setter table. State the units and holder frame explicitly. A remove row has no placement fields. This placement branch does not create/remove holders; use the separately advertised panel workflow below for a direct board child. Panel-owned structural records, renaming, direct pseudo changes, and removal of a pseudo-referenced real record are unsupported.
4. Follow the planning operation. Review every affected before/after record, resulting inventory, shared effects, lineage/revision and reserved logical IDs. Use the freshly returned plan ID for apply; never synthesize an ID or replace the lineage from user input. The plan expires after five minutes and rechecks native identity, content, history and ownership before publication.
5. Apply with the same current job/configuration/load revision envelope and a fresh request ID. The bridge first forces a reservation for all touched logical IDs, then invalidates dependent validation/plans/registration and publishes a detached native tree. Reserved IDs survive removal or failure and cannot be reused for an addition. Observe the original operation to terminal and native idle, then inspect the exact resulting membership and lineage revision.
6. Re-read board loads, choose an explicit supported binding consistent with the user's intended board, and revalidate before dependent use. Save a native document separately if requested. New signed documents retain their lineage/revision association; older documents remain readable with unknown or historical lineage and cannot become unused current jobs by reloading.

On a lost response, read the original request status and operation. An unresolved reservation or publication outcome blocks a new plan/apply, even if the visible tree appears unchanged. Preserve its evidence and report the missing disposition. Do not retry under a new ID, clear history, reuse a reserved ID, or treat a rollback as proof that every native listener effect was undone.

Board-load action attempts conservatively advance known lineage before the native helper validates every requested load detail. A rejected load action can therefore make an earlier saved document historical even when board geometry did not change. Read current lineage and save the current job again when a fresh document is needed; do not infer current structural authority from the error alone.

## Clone or remove a direct board child of a panel

Require the MCP result from `openpnp_get_capabilities` to return `bridge.panel_board_membership.profile: "panel-board-membership-v1"`. Use the existing structure plan/apply tools with **exactly one panel action**. Every pristine-lineage, complete-empty-history, disabled-machine and empty-nozzle requirement above applies. Panel membership cannot be changed after any native processor admission, including an accepted request that performed no placement.

1. Read the complete native job and load registry with consistent revisions. Select `parent_instance_id` as the exact expanded ID of an existing panel, such as `P1` or `P1⇒Subpanel`. The job's inline root is not eligible. Select an existing **direct board child** using its local child ID, such as `A`; a descendant path or definition name is not a child selector. Disabled and empty boards still have identities and participate in the plan inventory. `inspect_job.records` enumerates placements, so an empty holder produces no placement row. Existing empty board IDs are available in `get_board_loads.roots[].boards[].board_instance_id` when that root's `definition_matches` is true; retain the exact parent path and local child segment. If the shape no longer matches, those mappings can describe the previous load. Use the plan's before/result holder inventories to review membership, and never infer that a holder is absent solely from zero inspection rows.
2. Submit one of these closed `changes` rows, together with the normal current session/request/job/configuration/load revision envelope:

   ```json
   {
     "action": "clone_board_child",
     "scope": "job_instance",
     "parent_instance_id": "P1",
     "source_child_id": "A",
     "new_child_id": "C",
     "location": {"frame": "holder", "units": "mm", "x": 50, "y": 0, "z": 0, "rotation": 0},
     "side": "Top",
     "enabled": true,
     "check_fiducials": false
   }
   ```

   ```json
   {
     "action": "remove_board_child",
     "scope": "job_instance",
     "parent_instance_id": "P1",
     "child_id": "B"
   }
   ```

   The clone's pose is complete and local to its parent panel; `mm` or `in` are supported and rotation is in degrees. Supply side and both flags explicitly from the intended job. Example coordinates are illustrative, not a clearance recommendation. Clone appends the new child and preserves its source board's placement content and configured native part references. It does not move the source. Removal must leave a board descendant. Do not combine the two rows or mix them with placement add/remove rows: review and apply each plan separately using freshly read revisions.
3. Follow the planning operation and review `before_inventory`, `result_inventory`, `affected_root_ids`, `reserved_logical_ids` and lineage/revision. The result maps each new holder to its source holder and states its pose, side, local flags and placement IDs. A clone of `P1⇒A` to `P1⇒C` must preserve unrelated occurrences such as `P2⇒A`; the source definition's name alone does not imply native shared identity. The operation creates private definitions as needed within the current job. It does not edit source board/panel files or change membership in every shared occurrence.
4. Apply the returned plan using its exact current revision envelope and a fresh request ID. The bridge durably reserves the affected holder identity and real placement identities before publication. Holder reservations use opaque `holder-v1:` keys, including for empty boards. Keep these keys intact; do not construct or decode them to obtain new authority. A removed or previously reserved holder ID cannot be reused by cloning, even when no placements remain visible.
5. Follow the original operation to completion, inspect the resulting exact membership, and read **all** root load records. Publication clears registration and confirmations for all roots. A changed root shape requires an explicit supported replacement; unchanged roots still require confirmation consistent with the observed load. Until replacement, a mismatched root's load mapping may retain the old board IDs; use the applied result inventory for the new membership. Use the reported current load decisions and revisions. Do not silently rebind a board, infer physical presence from a saved document, or use replacement to erase history. Revalidate and align as required before execution.
6. Save the current native job separately when requested, then verify its artifact and restored inventory/history. Document restoration does not transfer current structural authority from an older revision. A successful original request replay returns its original receipt; it grants no new reservation or operation, even if the job has subsequently run. Fresh edits after processor admission remain refused.

This profile allows one board child in one panel instance. It does not create panels, clone panel subtrees, rename or reparent holders, change shared-panel membership, or edit pseudo records. A pseudo reference that depends on the removed board, or whose native resolution changes after cloning, rejects. New local IDs are 1–128 ASCII letters/digits/underscores/dots/hyphens, beginning with a letter/digit. Exact existing full holder paths are bounded to 512 UTF-16 units with well-formed Unicode and no control characters. Current and resulting jobs are limited to 1,000 expanded board/panel holders, 10,000 real/pseudo records, 1,000 private definitions and eight nesting levels; stricter applicable graph limits still apply.

On reservation, listener, publication, journal or completion failure, preserve the request/operation IDs and retained unknown state. A visible edited tree, failed operation or attempted rollback does not prove the reservation is absent. Reconcile the original receipt and reported fault fence; do not replay with a new request ID or reload an older job to regain eligibility. Panel geometry in the simulator does not qualify physical fixture clearance, board presence or independent placement inspection.
