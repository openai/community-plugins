# Virtual tray load changeover

This private integration adds `openpnp_get_material_loads` and `openpnp_register_material_load` only to the **headless sustained simulator**. Discover both native capabilities first. The controller diagnostic profile, ordinary simulator, GUI, physical feeders, powered feeders, serial devices, and physical stock verification are outside this slice.

## Bind a configured virtual tray

1. Read configuration and finish supported part/geometry settings with the configuration plan/apply tools **before enrollment**. Use one enabled, exact native `ReferenceTrayFeeder` for the selected canonical part/package, normal feed mode, and finite capacity of at most 10,000 configured slots. No lot selection or quantity override is supported.
2. Read `openpnp_get_material_loads`. Select the exact `feeder_id`, `part_id`, and `geometry_sha256` from `available_trays`; retain `available_trays_observed_at` and `material_setup_revision`. This is cached native-model evidence, not a physical inventory observation. Unsupported rows have a reason code; do not guess geometry.
3. With a valid control session, idle job, disabled machine and empty native nozzles, register `action: "bind-existing"`. Supply a new canonical UUID `request_id`, `expected_config_revision`, `expected_material_revision`, and the three selected fields (digest as `expected_geometry_sha256`). Omit `expected_load_id`. Binding records the existing native index; it does not feed or reset it.
4. Observe the returned operation using progress polls, then read its full receipt once. Retain its exact new `load_id` and material setup revision. Validate the job before starting.

## Replace a full virtual tray between jobs

Read the current load state first. After the current job finishes, disable the machine and confirm idle/empty native state. Use `action: "replace-full-tray"` with the same part and geometry plus the exact prior `expected_load_id`, current material/config revisions, session, and a new request UUID. This creates a new load identity and resets the native tray index to zero only after the intent has been forced to the journal. It preserves the old load's recorded index and consumption. It is an explicit simulator replacement, not proof of a physical refill.

Observe completion and revalidate the prepared job. A finite-capacity failure must not be bypassed by a generic counter edit, configuration restore, manual feed test, or replaying the job. After enrollment, this slice blocks generic configuration changes/restores and `openpnp_test_feeder` for enrolled feeders. A load change revokes job validation; it does not claim to invalidate native fiducial registration.

## Unknown outcomes and retained history

Keep request/operation/load IDs, material revision, `pending_changes`, `pending_feeds`, and full receipts. Do not reset an index, replace a tray, or retry a feed to resolve an unknown. Reusing the exact original request observes its existing receipt; a new request is another action. Native feed intents and after-hook observations track configured slot advances, including retry consumption; they do not prove that a component was physically picked up.

Replay reconstructs observations but does **not** reattach native material authority. This slice has no restart reattachment/reconciliation API. Preserve the previous state and use a separate fresh isolated simulator for subsequent experiments. Never delete or rewrite unknown history. At 512 retained loads, capacity fails without eviction. Journal/disk retention and fresh-process crash qualification are separate remaining work.
