---
name: backup-restore-openpnp
description: Back up, restore, or migrate supported OpenPnP configuration and referenced resources while preserving physical operation history.
---

# Back Up or Restore OpenPnP

Read [the runtime/control contract](../../references/control-contract.md) and, for this workflow, [back up or restore openpnp guidance](../../references/backup-restore-and-migration.md). Use only tool operations and recipes reported as supported by the current runtime. Tool presence alone does not qualify physical hardware.

## Workflow

1. Inspect capabilities/status/configuration and identify the requested source and destination machine, build, active configuration root, and operation state.
2. Use `openpnp_backup_configuration` to capture the supported resources and manifest. Inspect completeness, hashes, compatibility, calibration provenance, and any excluded resources.
3. Before restore, require quiescence and reconcile held parts or pending work. Compare machine/build/schema and affected references; cross-machine restore is an adoption task with local calibration requirements.
4. Use `openpnp_restore_configuration` only for a verified retained backup from the same owned artifact store and its declared representable typed settings. Inspect the reference's supported families and each snapshot's omissions. Restore preserves later identities, newer feed counts, and current placed history; it cannot restore arbitrary XML, delete later objects, or provide complete portable restore.
5. Inspect the restored revision, omissions, preserved material state, and affected dependencies; revalidate before use. Missing captured identities or capacity below retained consumption must reject staging. Treat partial save/restore as a configuration fault, and do not claim that calibration or physical state was restored.
6. For portable export/adoption, use `openpnp_export_portable_configuration` only when advertised and follow [the portable simulator workflow](../../references/portable-simulator-adoption.md). Bind export to the observed revision, verify archive bytes, and adopt into a new directory in a separate process. Save source board and panel definitions first. If the request also includes preparing a new panel, use [the canonical panel preparation example](../../references/canonical-panel-preparation.md) and its prepare/save/load sequence; do not substitute a flat `pnp-test` job for the required saved panel. Inspect the advertised board-library profile and, for panels, require `bridge.portable_configuration.panel_library.profile` to be `saved-board-child-panel-library-v1` with archive version 3. Check its exact panel/reference/record limits and preserve the manifest bindings. Refused dirty, changed, nested-panel, ambiguous-fiducial or unsupported outline definitions require source correction; do not remove fields or clear dirty flags to force export. Use a new state directory for its one-time first launch. Preserve quarantined scripts and distinguish source counters/calibration data from transferred operational authority. Retain failed destinations and inspect uncertain publication; do not clear or replay a spent activation.

For nozzle-assembly creation, retain its `recovery_preimage` and operation-linked `topology_recovery_available` event. Recover an uncertain or partial creation by hash-verified adoption into a fresh simulator generation as described in [assembly recovery](../../references/simulator-nozzle-assembly.md). Subset restore preserves later identities and cannot delete the added assembly.

## Completion and recovery

Return backup/restore or export/adoption IDs, hashes, actual scope and omissions, restored revision or new simulator identity, retained history, and required revalidation. Label full restore/migration as unsupported when only subset restore exists. A supported seven-document fresh simulator adoption does not provide in-place restore or arbitrary physical-machine migration.

Stop on incomplete manifests, active work, incompatible resources, or partial persistence; do not patch live XML or roll back physical history. If a dependent tool or capability is unavailable, complete the supported inspection or preparation and name the exact remaining gap; do not imitate execution or use raw commands as a fallback.
