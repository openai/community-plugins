# Backups, restore, and migration

Resolve the real active configuration root and supported backup scope from the runtime. A valid backup includes a manifest of resources/hashes, machine/build/schema identity, calibration provenance, and omissions. A copied XML file alone is not automatically a complete machine backup.

Use supported backup/restore tools. Before restore, require a quiescent machine and reconcile active/unknown operations and held parts. Validate referenced resources and compatibility; a cross-machine copy is an adoption workflow and does not transfer valid local measurements.

The current native backup captures selected configuration XML files plus a typed setting snapshot and declares `complete_portable_backup: false`. Restore accepts an original native backup artifact ID verified in the same owned persistent artifact store, including after bridge restart. The 128-entry cache is bounded metadata, not permission to trust arbitrary supplied snapshots. After cache loss, the adapter verifies artifact identity/kind/digest and strictly decodes the typed snapshot before staging. Captured XML is evidence; restore applies representable typed settings, not arbitrary XML. Inspect `typed_snapshot.omissions` before accepting a baseline.

When separately advertised, `openpnp_export_portable_configuration` supports [fresh simulator adoption](portable-simulator-adoption.md). Its ZIP carries seven native documents, allowed images and quarantined scripts for a new instance and one-time launch. It does not expand the typed restore operation or replace existing operational history.

| Existing-setting family | Representable restore scope |
| --- | --- |
| Machine speed | Supported bounded speed |
| Job planner | Exact native job order, Simple planner strategy and bounded options |
| Job retry policy | Vision/placement attempts and native feeder fault limit/window |
| Head park | Native head XY park location; no invented Z/rotation semantics |
| Machine discard | Supported complete discard pose |
| Part retries | Native pick retry count |
| Feeder retries | Native feed and pick retry counts |
| Package footprint | Supported body dimensions and pads |
| Package compatibility | Known nozzle-tip identities |
| Part properties | Supported part fields; known height alone is a fallback when full properties cannot be represented |
| Feeder assignment | Known, non-null part assignment |
| Feeder enablement | Captured enabled state, subject to preserving a disable after newer consumption |
| Strip feeder geometry | Supported native strip settings and finite capacity |
| Tray feeder geometry | Supported native tray settings and finite nonzero-pitch layout |
| Camera geometry | Supported camera scale, working plane, and head offsets |
| Camera settling | Supported FixedTime and dynamic settling methods; FixedTime omits inactive dynamic fields |
| Nozzle settings | Supported dwell times and tip compatibility |
| Nozzle-tip settings | Supported dwell times |
| Axis motion limits | Supported linear axis with both soft limits enabled |
| Axis backlash | Supported native linear-axis method, offset, speed, sneak-up and tolerance fields |

These restoration groups cover existing identities only. The configuration plan endpoint separately has 33 change types, including package/part creation and a distinct height-only change; restore creates or deletes no identities. Null/unrepresentable defaults, unknown strip capacity (`max_feed_count: 0`), zero-pitch sustained-workload trays, unsupported classes/fields, disabled/nonlinear axis limits, and unsupported camera-settling fields are explicit omissions. Calibration models/enabled flags, installed tip, physical enabled/homed state, external resources, and the current job document/history are excluded.

Restore preserves identities created after the backup, current job/placed history, and the greater of captured/current feed counts. A feeder disabled after newer consumption or retained faults stays disabled. Redundant enabling is skipped because the native setter clears fault history. Fault counts/history are preserved; they are not reset to make restore succeed. A missing captured identity or captured capacity below retained consumption rejects the complete staging step before applying changes. It cannot undo consumed tape or PCB contents.

Inspect the result's `scope: "representable-typed-settings"`, `restored_change_count`, `snapshot_omissions`, preserved-identity/material fields, `config_revision`, and `requires_validation`. The result explicitly reports `job_placed_history_replaced: false`, `calibration_evidence_restored: false`, `physical_state_restored: false`, and `full_configuration_restore: false`. In-place complete restore and arbitrary physical-machine migration require additional adapters. A sustained-workload fixture must retain its native Unsorted job-order invariant; an incompatible planner restore is rejected before application.

Save/restore must distinguish configuration from physical history. Retain the newer operational journal, component consumption, physical board/load ledger, and unresolved events. Restoring yesterday's settings does not make today's placed components or consumed tape disappear.

The native configuration model includes separate save/load operations; the plugin's complete backup and reconciliation behavior is additional functionality that must be implemented and supported, not assumed from a filename. [Pinned configuration source](https://github.com/openpnp/openpnp/blob/5bd404cfc70f34103a3ca0fbb6b50c2b465f407c/src/main/java/org/openpnp/model/Configuration.java)

After restore, follow the declared reload/restart requirement and inspect critical references and dependent calibration. If a save or restore is partial, keep the configuration fault visible and do not enable production. Code rollback must also be compatible with the current operational-journal schema; configuration rollback is not permission to delete newer history.

When backup/restore is unavailable, document a concrete manifest/compatibility plan and exact tooling gap. Do not substitute live XML replacement or arbitrary filesystem mutation from the skill.
