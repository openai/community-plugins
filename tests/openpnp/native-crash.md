# Actual JVM-halt qualification

The crash matrix runs separately from the ordinary native test mains. It executes the real native job processor and production action ledger, halts that JVM, then starts two new Bridge recovery JVMs against the unchanged journal.

## Run

Build the pinned native runtime first using the existing build command. Select that immutable build directory and a JDK 17 home:

```sh
npm run test:openpnp:native:crash-harness
npm run test:openpnp:native:crash -- \
  --build validation/native-build-example/build \
  --java-home /absolute/jdk17-home \
  --output validation/native-crash-example
```

The output must be a new directory below this checkout's `validation/`, with an existing parent, and cannot overlap the selected build. Omit `--output` to reserve a unique name. Existing outputs, symlink components, escaped inventory paths, modified native/JDK/source inputs, unlisted runtime files and remote JAR manifest classpaths are refused. `--case` selects a diagnostic subset; only all 14 passing cases set `qualification_complete:true`.

The runner verifies the bridge, all current production Java files against the build manifest, the entire native runtime inventory, and the JDK executables/modules. It checks native JAR manifest classpaths too: optional absent dependencies must remain absent, and local class directories must stay within the exact inventoried runtime. Native-loader and JVM option environment variables are removed from child environments. Source copies, commands, JVM identity, hashes, per-child cleanup receipts and all failures are retained. Each child has a 90-second deadline and 2 GiB heap; the matrix has a 15-minute total deadline. Interruption and timeout clean up only session/process groups created by the runner.

The OpenPnP workflow invokes this separate command after its ordinary native suite in the same pinned Maven/JDK image. CI retains reports, source, journals and logs. It excludes generated bearer-token files. Configured CI execution is distinct from a recorded successful run.

## Exact scope

The 14 cases cover feed, pick and release intent/outcome, plus native placement completion checkpoints:

- **Before:** halt before the target envelope append begins; target bytes have not been written.
- **After:** halt after the complete target write and `FileChannel.force(true)` return.

The forced halt-seam observation reads the actual native tray/nozzle/placed model on its executor. It is a test-only observation, with no independent physical inspection. Initial operation admission and logical board-load identity are fixtures; the native processor, ledger envelopes and recovering Bridge are production code.

Recovery must preserve the machine ID, create new Bridge IDs, retain the exact preceding journal prefix and original action/checkpoint facts, keep the interrupted operation `outcome_unknown`, and refuse a new enable command with `RECOVERY_REQUIRED`. Feed-count/nozzle-part property listeners and native activity/enable/home/busy listeners must observe zero replay events across both recoveries.

Each recovery reloads saved **pre-run** simulator configuration. Its zero material counters and empty nozzle are not treated as recovered consumed inventory. No resume, abandonment, reconciliation or cleanup is requested. This does not qualify a written-but-unforced window, torn writes, host power loss, full Bridge command-admission atomicity, physical state persistence or safe automatic resume.

The Java child harness is copied unchanged from the independently reviewed 14-case prototype. Runner boundary tests use clearly identified controlled process/metadata fixtures; they are not native machine qualification.
