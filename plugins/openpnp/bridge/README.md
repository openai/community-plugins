# Native OpenPnP bridge

This GPL-3.0-or-later Java bridge links to OpenPnP commit
`5bd404cfc70f34103a3ca0fbb6b50c2b465f407c`. OpenPnP itself and its dependencies are a separate, pinned runtime.

## Supported runtime

The final 13-main native suite passes on Linux ARM64 with Temurin 17.0.15 and macOS ARM64 with Temurin 17.0.16, using the pinned OpenPnP source build. An x86-emulated run completed the 32-placement native sample but did not pass the full suite because HTTP polling timed out; its failed attempt is retained in the evidence summary. The Docker build defaults to Linux x86-64; set `OPENPNP_DOCKER_PLATFORM=linux/arm64` for an ARM64 Docker host. Windows and other JVM versions have not been exercised.

Build with `OPENPNP_SOURCE=/absolute/pinned/source bash scripts/openpnp-build-native-docker.sh --test` from the source repository. The build produces this directory's bridge JAR and `build-manifest.json`, plus a separate runtime distribution at `$OPENPNP_SOURCE/target/codex-runtime` with its own content manifest. JDK/Maven users can run `scripts/openpnp-build-native.sh` directly.

Main class: `org.openpnp.codex.SimulatorMain`. The bundled launcher and native suite use `-Xmx2g -XX:+ExitOnOutOfMemoryError`; native camera memory is measured separately from the Java heap.

Required arguments:

```text
--token-file /private/path/token
--config-dir /private/path/simulator-configs
--journal-dir /private/path/journal
--sample-root /pinned/runtime/samples
--port 0
--profile native-simulator
```

The classpath contains `openpnp-codex-bridge.jar`, the pinned OpenPnP GUI JAR, and `lib/*`. JDK 17 requires the OpenPnP launcher flags `--add-opens=java.base/java.lang=ALL-UNNAMED`, `--add-opens=java.desktop/java.awt=ALL-UNNAMED`, and `--add-opens=java.desktop/java.awt.color=ALL-UNNAMED`.

Startup creates a new configuration child from OpenPnP's built-in simulator defaults. It does **not** load a user-supplied `machine.xml`. The token is read from a local file and never printed. HTTP binds to `127.0.0.1`; `/health` exposes only version/health information and `/rpc` requires bearer authentication. Browser origins, unknown fields at the envelope boundary, deeply nested JSON, and bodies above 8 MiB are rejected.

The optional `sustained-workload` profile replaces native strip feeders with actual native `ReferenceTrayFeeder` instances. Each has 10,000 finite virtual slots at zero pitch and retains the original simulated pick pose. Feed counts increase normally and are never reset by the workload harness. This profile sets the public native processor order to `JobOrderHint.Unsorted` for its preordered grid, avoiding repeated whole-job route optimization while retaining the native planner, vision, feed/pick/place execution, and every durable step boundary. The default `native-simulator` profile retains upstream `NozzleTips` order. Capabilities report the actual `native_job_order`; sustained mutations reject any order drift. Capability attestation names this fixture separately. It establishes simulator behavior; it does not qualify physical tray geometry or feeding.

Both profiles use the acceleration recipe from upstream `SampleJobTest.makeMachineFastest`: driver feed rate 0, controller-axis native feed rate 1,000,000 per second, acceleration 2,000,000 per second squared, jerk 0, fixed camera settling 0 ms, and nozzle pick/place dwell 0 ms. These simulated cycle rates do not measure physical machine throughput.

## Current authority boundaries

- Only the isolated native simulator profiles can mutate. Drivers, cameras, feeders, actuators, heads, and nozzles are checked against exact native classes at dispatch. Mixed hardware/simulator configurations are rejected.
- GUI attachment and physical machine control are unqualified. `bootstrap.js` intentionally refuses GUI activation.
- Every execution request has a durable ID and digest. The journal uses locked, append-only JSONL with `FileChannel.force(true)`, not SQLite. Interrupted operations replay as `outcome_unknown`; new work is fenced until explicit simulator-reset reconciliation.
- The journal records native processor step boundaries. A step can contain multiple irreversible actions; this is **not** per-placement physical crash recovery.
- Configuration changes cover 16 named typed adapters, staged as a whole batch before changing native models. Reads expose their current fields under `machine.settings`. Backup exports native configuration files plus every representable value in the 13 existing-setting families (package/part/feeder/camera/nozzle/tip/linear-axis settings and speed). Typed restore uses the most recent 128 backups in the same bridge instance, stages all changes before applying, explicitly lists omitted fields, preserves later identities and newer feeder consumption, and retains the current job placed history. Older geometry that cannot contain already consumed inventory is rejected. It does not restore physical state, calibration measurement evidence, unsupported fields, or a complete portable machine setup. A partially failed native save advances the revision, invalidates the job, and fences further mutations until the isolated simulator is restarted.
- Calibration currently adapts native nozzle-tip runout only. Reported offsets are native fitted outputs, not independently measured physical accuracy.
- Canonical import creates real native board/panel/placement models. It requires explicit dimensions/heights and existing package definitions; arbitrary XML, scripts, G-code and Java expressions are never accepted.
- Native job save/load exports native Job XML with linked Board/Panel definitions to a hash-verified generated ZIP. Reload accepts only bundles created in this bridge instance with unchanged part/package dependencies; it preserves supported static geometry, overrides and placed history while invalidating transient registration. Custom outlines are rejected because the pinned native persistence/copy APIs lose them. It accepts no arbitrary XML or path.
- The default native nozzle changer is manual. A different tip or unload is rejected before changing its model; native automatic changer operation requires a configured automatic changer fixture.

## Retention and diagnostics

`get_status.metrics` reports monotonic uptime, used/committed/maximum JVM heap, retained request and operation counts, event buffer count, plan count, artifact count/bytes, and journal bytes. During job execution, full machine/settings snapshots refresh at most every 100 ms and retain their actual `snapshot_at`; `get_status.job_progress` publishes native-executor counts with `observed_at` and `through_sequence`, so an event reader can resynchronize without HTTP traversing the mutable native job; final completion forces a fresh snapshot. Native Job property events invalidate cached placement counts, while every native processor intent/completion remains durable. Metrics include `job_count_recomputations` and `machine_snapshot_refreshes` for measuring this behavior. The event ring retains 5,000 events and reports resynchronization when a reader's cursor is older. Plans expire after five minutes and retain at most 128 entries. Artifact bytes stay on disk and load only on retrieval; the in-memory metadata cache retains at most 128 entries. Each artifact is limited to 8 MiB. Aggregate artifact content is limited to 1 GiB; admission fails before publishing further bytes and preserves existing evidence. Artifact files remain until explicit state-directory cleanup. Native job-document working bundles separately retain at most 16 bundles/64 MiB, and at most 32 native reloads per bridge instance.

The journal retains request identity/digest receipts; it never evicts deduplication records while continuing execution. New requests stop at 20,000 retained receipts, and journal writes fail closed at 512 MiB. A new isolated state directory starts a new journal; preserve the old one for recovery and reports. These finite capacities support bounded simulator testing and do not establish indefinite operation.

## Native verification

The `--test` build runs the real upstream processor, model, cameras, vision, feeders, calibration and executor. Coverage includes the 32-placement upstream sample, a canonical imported job, top/bottom/nested transforms, lost-response deduplication, expired/revoked leases, repeated restart recovery, failed native configuration save, typed setting batches, calibration invalidation, 129 captures with metadata eviction, and a finite 100-placement sustained workload. `NativeSustainedTest` accepts an explicit count up to 10,000. A 100-placement test is not an eight-hour soak; sustained qualification requires its own recorded result.

The capability response includes the SHA-256 of the actual loaded bridge JAR (unavailable when running unpackaged test classes) and is authoritative for that build. Tests also cover typed rollback and material conflicts, 100 rejected canonical imports with no retained native Part listeners, a 10,000-record native document round trip without machine execution, and aggregate artifact admission at capacity. Native job completion is reported separately from independent inspection.
