# Portable simulator configuration adoption

Check `openpnp_get_capabilities` before using this workflow. It requires the portable-export tool and a matching native runtime. Existing typed `backup_configuration` / `restore_configuration` operations retain their narrower, same-store contract.

## Source and archive

1. Identify the source simulator, build and current configuration revision. Complete or reconcile pending work; the machine must be idle and disabled, with no held part. Disabling can have effects and uses its normal control lease.
2. Call `openpnp_export_portable_configuration` with a session, unique request ID and the observed `expected_config_revision`. Observe the original operation to completion. After a lost reply, reconcile that request; do not submit a fresh export blindly.
3. Retrieve the returned native artifact with `openpnp_get_native_artifact`. Save the ZIP bytes to a new local file, verify their SHA-256 against the export receipt, and retain the manifest. A hash verifies bytes; it does not authorize arbitrary native classes or scripts.

The declared profile preserves all seven native configuration XML documents, bounded inventoried camera images, and inert script files. When both board and panel libraries are empty, the archive retains the version-1 format. Version-2 archives also preserve the `saved-flat-board-library-v1` profile: up to 32 saved, clean flat-board definitions and 10,000 placement definitions in total. Distinct boards remain distinct even when their bytes match. Exact native `BoardPad` records with `Pad.RoundRectangle` geometry also transfer losslessly, up to 10,000 pads in total; unit factors bound geometry while preserving serialized units and roundness. Other pad shapes, panels, custom outlines, legacy fiducial collections and job documents are outside the version-2 flat-board profile; jobs use the separate native document workflow. Exact native classes, resources, object counts, image allocations and XML structure are checked before native configuration loading. Unsupported profiles are refused without dropping fields. The source must already have completed its pinned native configuration migrations; changed fields during a fresh native load/save refuse activation.

Before export, save each board through OpenPnP. Export compares the saved file with the current native definition and its exact registry, part and package identities; clearing a dirty flag does not bypass this check. It refuses unsupported or changed definitions before a native save dialog. Board XML also shares a cumulative 50,000-element and 2 MiB text/attribute budget, within the archive file and byte limits.

Every board resource is inventoried by generated identity, bytes, semantics and placement count. Adoption checks the manifest, resources and native registry before loading, verifies that OpenPnP loaded every definition, and includes board files in activation hash checks. Source placed history and board-load records remain with the source simulator. Imported definitions carry no job, load registration, inspection, homing or machine authority.

The incoming profile requires planar pixel geometry and disabled advanced camera correction. Pipelines must match the pinned stock topology and parameter definitions for their consumer, with only the supported bounded Gaussian, threshold, HSV and declared scalar parameter edits. Other stage attributes and parameter names/targets remain fixed. It also bounds image allocations and footprint-template dimensions. Inspect the returned `native_model_limits` for exact counts and dimensions. Custom pipelines outside that profile are refused without clamping or discarding fields. Serialized calibration data never establishes physical calibration validity.

## Saved panel libraries

Use this extension only when `openpnp_get_capabilities` reports `bridge.portable_configuration.panel_library.profile` as `saved-board-child-panel-library-v1` with archive version 3. Tool presence or an older flat-board profile is insufficient. Read that descriptor for panel, child, record, XML and archive limits; the original `native_model_limits.board_library` still describes flat boards.

Save the source boards and panels through OpenPnP before export. The bounded panel profile preserves exact saved Panel definitions whose direct children are BoardLocation instances referencing the included saved board definitions. It preserves child order, exact references, design locations and units, Top/Bottom side, enabled and check-fiducials flags, plus supported own and derived pseudo fiducials. The export operation does not author panels. When new panel preparation is requested, use [the canonical panel example and prepare/save/load sequence](canonical-panel-preparation.md) to obtain supported saved definitions through the separate job workflow. Nested panels, arbitrary outlines, executable panel component placements, ambiguous pseudo targets, unsaved changes and measured registration remain outside this profile. A refusal requires correcting the source through a supported workflow; deleting fields or clearing flags does not make a definition portable.

The exported version-3 manifest inventories panel and board files separately and binds every child and pseudo target to its exact definition. Distinct definitions remain distinct even when their contents match. Adoption checks the complete reference graph before native loading, then verifies every loaded definition, child reference and supported derived fiducial after native normalization. Missing files and definitions silently skipped by OpenPnP must cause refusal. Panel references use logical archive paths during export and generated native paths after adoption, so a panel file’s archive hash can differ from its materialized hash. Verify each against its own manifest or activation receipt. Generation files are covered again before the one-time first launch.

Treat the panel library as design data. A successful transfer carries no Job, placed map, board-load identity, registration, inspection or execution authority. Existing machine feeder counters still transfer only as unqualified source simulator data. Preparing a new job from an adopted library requires separate fresh load/material setup, validation and execution; transferring the library is not evidence of placement or independent inspection. Preserve any failed adoption directory and its original receipt as described below.

## Create and launch a new instance

Resolve actual installed plugin, runtime and state paths. Use absolute paths for the archive and new directories. With the source bridge installed in `<installed-state>`:

```sh
node "<plugin>/scripts/openpnp.mjs" adopt-configuration \
  --state-dir "<installed-state>" --openpnp-home "<verified-runtime>" --java "<jdk17-java>" \
  --bundle "<archive.zip>" --sha256 "<export-sha256>" --destination "<new-adoption-directory>"
```

Adoption runs in a separate Java process. It reserves a new directory, stages files, performs native load/save validation, and publishes an activation receipt. It never overwrites a source configuration or an existing destination. Scripts remain outside the active scripting directory. Captured material counters and calibration coefficients remain source simulator data; operational journals, board-load identity, homing and physical qualification do not transfer.

After successful adoption, launch once with an explicit new state directory:

```sh
node "<plugin>/scripts/openpnp.mjs" start-adopted-simulator \
  --adoption-dir "<new-adoption-directory>" --state-dir "<new-state-directory>" \
  --openpnp-home "<verified-runtime>" --java "<jdk17-java>"
```

The launcher installs the verified bridge in the fresh state directory. Native startup validates and claims the activation before loading it, creates a new journal, and starts disabled and unhomed. It preserves adopted geometry, rates and settling settings. Read the returned connection and capabilities, including `adopted-simulator` provenance, before enabling or homing under a new lease. Launch is foreground; Ctrl-C stops the owned simulator.

The runtime manifest and native application JAR must match the hashes recorded by the installed bridge build. Keep the plugin package, verified runtime, adoption directory and new operational state in separate directory trees. The launcher rejects overlap before creating state. A simulator that ignores graceful shutdown is terminated after five seconds and reported as an unsuccessful shutdown; retained journals still determine what is known about its operations.

Java children use the launcher's declared JVM arguments and verified classpath. The launcher removes inherited JVM option variables, `CLASSPATH`, and `LD_`/`DYLD_` library overrides while preserving ordinary environment settings.

## Interrupted or failed adoption

Keep the original destination and logs. A reservation or staging directory alone is not an activated configuration. Publication or startup errors can leave a durable activation/launch record; inspect those records and the process state before further work. Do not delete a claim, rewrite a receipt, reuse an old journal, copy XML into a default simulator, or repeat a spent activation to force success. This release exposes first launch of a freshly adopted simulator; restarting its spent activation is unsupported.

Native configuration round-trip, simulator execution and independent physical inspection are different results. Report exactly which was verified, along with archive/build/instance identities. This workflow does not provide in-place rollback, arbitrary existing-machine adoption, physical machine qualification or physical calibration validation.
