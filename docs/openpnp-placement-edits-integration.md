# Native placement edit integration proposal

Status: candidate source and actual native model tests; **not exposed by the frozen c65 bridge or its 43-tool MCP package**. This adapter does not enable physical hardware support.

## Java boundary

```java
NativePlacementEdits.Patch patch = NativePlacementEdits.stage(configuration, job, changes);
JsonObject preview = patch.preview();
Map<String, Object> result = patch.apply();
Map<String, Object> page = NativePlacementEdits.inspect(configuration, job, offset, limit);
```

`stage` validates every row and the current native graph, captures native object identities and a semantic fingerprint, and returns a private immutable plan. It does not clone native objects, write files, move the machine, feed a part, or change job fields. Returned previews and caller JSON cannot alter the plan. Calling `apply` rechecks the graph, current placed flags and selected part/package identities. A plan is single-use after an apply attempt reaches preparation.

`apply` builds private native definitions and all new instances before replacing the native root panel's child list with one setter. The same `Job` remains installed. This preserves its complete opaque placed-status map, including keys for removed/older placements which the public native API cannot enumerate. Shared original definitions and other jobs stay untouched. Native property notifications may occur while creating new objects; this is not a general transaction system for arbitrary third-party event listeners.

On publication failure, the helper restores the old child objects and dirty flags. It reports `PLACEMENT_COMMIT_FAILED` after successful rollback, or `PLACEMENT_ROLLBACK_FAILED` if rollback also throws. **Either error requires the Bridge to fence further work and retain the failure record.** A rollback does not retract arbitrary observer side effects.

After a successful publication it retires the known subscriptions from original definitions to replaced job instances, and old instance subscriptions to the Job. It preserves self-definitions and subscriptions for other instances. Rollback retains the original listeners. As with the native job loader, a mutable instance object belongs to one job/load; sharing definitions between jobs is supported, sharing the same mutable instance object between independent jobs is outside this adapter. Discard consumed plan objects promptly so their old graph references are not kept indefinitely.

## Typed changes

```json
{
  "scope": "job_instance",
  "holder_instance_id": "P1⇒nested⇒A",
  "placement_id": "R1",
  "set": {
    "location": {
      "frame": "holder", "units": "mm",
      "x": 4, "y": 5, "z": 0, "rotation": -30
    },
    "side": "Top",
    "part_id": "configured-part-id",
    "type": "Placement",
    "enabled": true,
    "error_handling": "Defer",
    "comments": "Reviewed coordinate correction",
    "rank": 7
  }
}
```

Use the exact existing native `PlacementsHolderLocation.getUniqueId()` and placement reference returned by job inspection. They are revision-scoped identities, not names, array indexes or new identifiers. `⇒` is OpenPnP's native hierarchy delimiter. It is not allowed inside an individual holder ID or placement reference. No prefix matching or ID renaming occurs.

| Field | Supported values |
| --- | --- |
| `scope` | `job_instance` or `job_shared_definition` |
| `holder_instance_id` | Exact expanded native ID, up to 2,048 characters |
| `placement_id` | Existing native ID, 1–128 characters |
| `set.location` | Complete local holder-frame pose; explicit `mm` or `in`; rotation in degrees |
| Converted geometry bounds | Absolute X/Y ≤10,000 mm, Z ≤1,000 mm, rotation ≤360 degrees |
| `side` | `Top` or `Bottom`; native holder coordinates are retained without inferred mirroring |
| `type` | `Placement` or `Fiducial`; real panel-owned records must remain fiducials |
| `part_id` | Existing configured Part with its configured Package; null/unassignment unsupported |
| `enabled` | Boolean |
| `error_handling` | `Default`, `Alert`, `Defer` |
| `comments` | String up to 2,048 characters; empty string permitted |
| `rank` | Integer from −100,000 through 100,000 |

Unknown keys, missing pose components, numeric strings, nonfinite values, deprecated placement types, overlapping rows and invalid native references are rejected. Multiple fields targeting one placement must be combined into one row. `set` must contain at least one supported field.

### Scope and persisted meaning

`job_shared_definition` changes every expanded instance of the anchor's native definition **within the current job**, including disabled boards and opposite-side records. The preview enumerates each affected holder/reference with before/after fields and placed status. The result uses a new private shared definition; source files and occurrences in other jobs are not changed. Shared enabled/error edits apply to all listed instances, including preexisting overrides.

`job_instance` changes only the selected expanded instance. Enabled/error policy uses native job overrides without splitting the shared board definition. Geometry, side, type, part, comments or rank require a private definition. For a nested board, the adapter clones each necessary ancestor panel definition so the new reference remains representable in the native document format. Other children retain shared definitions where possible. The native save/reload tests verify this path rather than relying on unsupported instance-only XML fields.

Pseudo placements are derived native panel-alignment records. Direct editing is rejected. Editing a referenced real placement regenerates its linked native pseudo placements against the private definition tree. Their enabled/error/comment/rank metadata is retained. Root-panel pseudo placements remain unsupported, matching the document adapter.

Any changed field on a placement whose native placed status is true is rejected with `PLACED_HISTORY_CONFLICT`. This adapter never clears that status or pretends to undo a placement. An unchanged row remains a no-op even for a placed record. Placement type/side/part changes require a fresh native job preflight; package compatibility, part height, material and vision readiness remain preflight responsibilities.

## Bridge integration requirements

The candidate source contract adds `openpnp_inspect_job`, `openpnp_plan_placement_edits`, and `openpnp_apply_placement_edits`. All return asynchronous native operation receipts because their work executes under the native machine executor and owner. Even inspection and preview require a request ID for that durable receipt; neither changes native job fields. The frozen c65 package does not expose these tools.

All three require `request_id`, `session_id`, `expected_config_revision` (`cfg-N`), `job_id`, `expected_job_revision` (64-character lowercase SHA-256), and `expected_board_load_revision` (`load-N`). Planning adds `changes`; applying adds `plan_id`. Inspecting accepts `offset` (default 0), `limit` (default 100, maximum 200), and optional `expected_source_fingerprint`.

Inspection includes every supported real placement and derived pseudo record, including disabled placements, ancestor X-outs, opposite-side records, and panel fiducials. It returns explicit holder-local millimeters/degrees, exact holder/root/reference IDs, side and enable flags, native placed history, and `derived`/`read_only`. Its `total_records`, `count`, `next_offset`, and `eof` describe the complete expanded record list. Use the first page's `source_fingerprint` on later pages; it also covers native placed flags and registered transforms. In this pinned native model, copied pseudo records are ordinary `Placement` objects, so the inspector identifies them by membership in the native panel's pseudo list.

The candidate also adds `openpnp_get_board_loads` and `openpnp_register_board_load`. After an edit, read the authoritative load registry and explicitly replace or confirm the exact current root. Changed geometry cannot be reconciled as the same physical load by guessing. Simulator records never independently verify presence, underside clearance, or physical material history.

1. Run stage/apply on the native executor under the current owner/lease. Require matching machine, instance, job/load/configuration revisions and request ID. Reject running, paused, stopping or unresolved jobs whose processor may retain old placement pointers.
2. Retain plans only in a bounded per-instance registry with expiry and explicit job revision. Reconnection/restart must not revive a plan or replay apply. Retain the preview fingerprint, complete effects and request receipt.
3. Before calling `apply`, apply the Bridge's mutation fence and durable intent rules. Report preparation failure as a known failure, not an unknown physical placement.
4. When `changed:true`, invalidate production validation, cached job counts, native planner state and physical load/registration bindings for **every `affected_root_ids` entry**. Initially the helper conservatively reports all job roots, because it replaces the full native tree and drops every measured child registration. Use the BoardLoads mapping invalidation API before allowing another production action.
5. Refresh the GUI job model through the supported native controller/UI boundary, even though the Job identity is unchanged. Old UI selections can retain old placement objects; they must not become alternate mutation targets. This helper does not implement GUI ownership or refresh policy.
6. Keep native document export a separate explicit action. Nothing is saved automatically and no arbitrary output path is accepted. `NativeJobDocuments` provides the verified archive and native reload path.
7. Publish the complete native result or retain it with the bounded MCP response store; a large preview must not lose its complete affected-reference list.

`affected_root_ids`, `registration_invalidated`, `production_validation_required`, `private_shared_definition_count`, and `private_instance_definition_count` describe actual copied-tree effects. No-op results set `changed:false` and preserve original objects and registration.

## Limits and deferred operations

- At most 1,000 change rows, 10,000 expanded placement/pseudo records, 5,000 copied definition/instance locations, 1,000 total private definitions, 100,000 copied definition/instance records, and eight levels of nesting. Native document byte/storage limits still apply separately at export.
- Exact native `Job`, `Board`, `Panel`, `BoardLocation`, `PanelLocation`, `Placement`, and derived `PseudoPlacement` classes only.
- Preexisting instance-only geometry or inconsistent definition links are rejected; they are not silently normalized.
- Expanded loads must own distinct native holder and placement instance objects. Aliased mutable instances are rejected with `ALIASED_JOB_INSTANCE`; sharing their native definitions remains supported.
- Explicit custom outlines, solder-paste pads, custom root frames, direct pseudo editing, placement create/remove/rename, arbitrary XML/properties, machine-frame coordinates, and physical-history reconciliation are deferred.
- A source revision or package/part configuration change requires a fresh caller revision check. The helper fingerprint covers the native graph and relevant placed flags; it does not replace Bridge configuration, ownership or load revisions.
- Registered transforms are recognized through the pinned native registration status. Unregistered transform getters synthesize defaults, so the preview avoids calling them. Callers must not inject custom matrices while leaving registration status unset.

## Pinned native APIs used

Source commit `5bd404cfc70f34103a3ca0fbb6b50c2b465f407c`:

- [Job history APIs](https://github.com/openpnp/openpnp/blob/5bd404cfc70f34103a3ca0fbb6b50c2b465f407c/src/main/java/org/openpnp/model/Job.java)
- [Placement fields and copy constructor](https://github.com/openpnp/openpnp/blob/5bd404cfc70f34103a3ca0fbb6b50c2b465f407c/src/main/java/org/openpnp/model/Placement.java)
- [Panel copy, child lists and pseudo resolution](https://github.com/openpnp/openpnp/blob/5bd404cfc70f34103a3ca0fbb6b50c2b465f407c/src/main/java/org/openpnp/model/Panel.java)
- [Native pseudo derivation](https://github.com/openpnp/openpnp/blob/5bd404cfc70f34103a3ca0fbb6b50c2b465f407c/src/main/java/org/openpnp/model/PseudoPlacement.java)
- [Native registration status and lazy transforms](https://github.com/openpnp/openpnp/blob/5bd404cfc70f34103a3ca0fbb6b50c2b465f407c/src/main/java/org/openpnp/model/PlacementsHolderLocation.java)

These APIs were read from the pinned local upstream checkout. Tool contracts, transaction boundaries and supported limits above are adapter design choices. The 19-group native helper test and the separate 103-assertion/three-group listener/alias review use actual pinned native classes; MCP adapter fixtures are labeled separately and do not establish native or physical machine operation.
