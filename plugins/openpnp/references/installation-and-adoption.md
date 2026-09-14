# Installation and existing-machine adoption

Use this reference for missing dependencies, connection readiness, and an existing configuration. Ordinary setup starts by inspecting the runtime; installation alone must not connect a controller, enable motors, or home. If the user requests a fresh simulator and no bridge is running, follow the bundled [prerequisites](../README.md#prerequisites) and [simulator launch procedure](../README.md#start-the-simulator) with observed absolute plugin/runtime/state paths. Launch the requested isolated simulator, then inspect its capabilities; no physical machine is needed.

Record the actual OpenPnP build, bridge/protocol version, host runtime, active configuration root, device identities, and selected profile. If an install helper is advertised, use its published command/schema and inspect its concrete changes. Do not derive an installation path from the caller's working directory or assume a plugin cache is persistent. Never overwrite an existing startup hook without reviewing its ownership and content.

For an existing machine, snapshot the supported resources before a typed change. Compare the selected profile with actual values and distinguish manufacturer baseline, local measurement, and unsupported configuration. Preserve valid calibration and unknown device types. Discovery can succeed even when mutation is unqualified.

For the advertised portable simulator profile, follow [fresh simulator adoption](portable-simulator-adoption.md). It preserves the supported native configuration in a new instance and uses a separate first-launch claim. Existing physical-machine configuration adoption remains unqualified.

A connection can have controller-specific effects, so use the connection capability's prerequisites. Verify the selected port/camera identity rather than trying every device. Follow connection with status; a transport connection is not homing or production readiness.

If the bridge is absent or incompatible, return the exact missing build/component and supported installation route. Credentials remain in the runtime's local provisioned mechanism; never ask the user to paste bridge secrets into chat.

## Bundled local diagnosis

Resolve the installed plugin directory and selected machine state directory from actual local configuration. With Node.js 22.19.0 or later, the bundled CLI supports:

```sh
node "<installed-plugin>/scripts/openpnp.mjs" doctor --state-dir "<state-dir>"
node "<installed-plugin>/scripts/openpnp.mjs" diagnostics --state-dir "<state-dir>" --output "<new-absolute-report-path>"
```

The placeholders must be replaced with observed paths. `doctor` checks runtime readiness through the local connection; `diagnostics` analyzes the selected `journal/operations.jsonl` offline and never connects to a machine. There is no MCP diagnostics operation. Omit `--output` to receive the summary directly; a requested output must be a new file outside the machine state directory. Preserve existing reports and journals.

Diagnostics reports a bounded historical prefix with a content hash, operation IDs/states, allowlisted method names, counters, and corruption/sequence/changed-file flags. It excludes raw operation payloads and credentials. Limits are 512 MiB of journal, 16 MiB per line, and 20,000 operations. Report missing prefixes, stale/out-of-order records, truncation, or growth instead of treating the summary as a complete live ledger. Even a structurally complete journal cannot establish current physical state or authorize replay. Keep exported evidence local unless sharing is requested.

### Selective support archive

For an offline export, use the installed CLI without connecting through `doctor` or changing a paused/unknown source:

```sh
node "<installed-plugin>/scripts/openpnp.mjs" support-export --state-dir "<state-dir>" --output "<new-absolute-support.tar>"
```

The default exports allowlisted provenance and diagnostic summaries, with no selected operation records or raw artifacts. Add `--operation-ids UUID,UUID` for every available projected journal record belonging to those exact operations. Read the returned completeness/change flags and omitted-field counts; unresolved facts remain unresolved. The archive neither deletes source evidence nor changes runtime retention.

Raw payloads require explicit `--artifacts UUID:SHA256:camera` or `UUID:SHA256:job-document`, comma-separated for multiple selections. Use the exact lowercase ID and hash from an observed native artifact receipt; do not infer selection from a filename or include unrelated payloads. Selected PNG/ZIP bytes are copied verbatim and may contain sensitive image/design data. Credentials, connection data, scripts and arbitrary files are excluded. Follow [the implemented export contract](../scripts/SUPPORT_EXPORT.md) for exact scope and limits.

Return the archive path, SHA256 and source limitations. Do not send the archive automatically. On `SUPPORT_PUBLICATION_UNCERTAIN`, preserve and inspect the existing output and its hash; do not automatically retry, delete it, or replay native operations. `SUPPORT_OUTPUT_EXISTS` preserves prior evidence. This workflow requires no physical-state change and makes no physical-state or production qualification claim.

Upstream directs modern setup through Issues & Solutions and discourages legacy live XML editing. Apply that guidance through supported adapters for the chosen build. [OpenPnP setup guidance](https://github.com/openpnp/openpnp/wiki/Setup-and-Calibration)

## Restart a faulted GUI simulator session

`restart-gui-simulator --session-dir ABSOLUTE_ORIGINAL_GUI_SESSION --openpnp-home ABSOLUTE_VERIFIED_RUNTIME [--state-dir PATH] [--java EXECUTABLE]` is the explicit saved-session route for the supported simulator sensing-replacement workflow. It requires an installed bridge whose build manifest declares `gui_sensing_restart` with schema 1, profile `native-gui-source-absent-restart-v1`, startup mode `restart`, prepared profile `prepared-native-gui-vacuum-fixture-v1`, and `source_installed_at_startup: false`. The compatible native runtime also needs the exact retained-job ownership patch; the generic ownership API version alone is insufficient.

This command preserves the original session paths and history, starts with no controlled sensing source, and waits for native attachment and a fresh local decision. It requires all seven saved native configuration files and empty native board/panel libraries; nonempty external library graphs are outside this launcher profile and are preserved/refused. It does not adopt an arbitrary existing machine. Follow the full [source-absent restart procedure](sensing-reconciliation.md#explicit-source-absent-restart), including separate continuation and fresh validation. Launcher executable/HTTP tests and the underlying native recovery tests have distinct scopes. Physical qualification remains deferred.
