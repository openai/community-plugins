# Experimental native GUI simulator attachment

The GUI simulator requires the pinned OpenPnP source commit `5bd404cfc70f34103a3ca0fbb6b50c2b465f407c` plus the six identified native patches. Physical machine support remains refused. The retained GUI tests cover bounded native simulator workflows; they do not establish physical-machine or eight-hour GUI qualification.

## Build an isolated runtime

The canonical repository entry point is `bash scripts/openpnp-build-native.sh`
(or `npm run test:openpnp:native` for the canonical native regression suite). It creates fresh owned
output and leaves packaged artifacts unchanged; see [BUILD.md](BUILD.md).
The lower-level verified-runtime builder is also available:

Use JDK 17 (the bridge targets Java 11 bytecode). Supply a clean copy of the exact stock source and its hash-inventoried distribution. Choose a **new** output directory beneath this candidate checkout:

```sh
python3 scripts/openpnp-build-native-gui.py \
  --stock-source /absolute/pinned/openpnp-source \
  --stock-runtime /absolute/verified/stock-runtime \
  --java-home /absolute/jdk17-home \
  --output /absolute/this-candidate/validation/new-build
```

The script verifies tracked source and every stock distribution digest, rejects previously Codex-patched runtime inputs before creating output, extracts the pinned complete source into the output, applies `gui-ownership.patch`, `native-action-observer.patch`, `native-board-load-history.patch`, `gui-topology-events.patch`, `native-circular-symmetry.patch`, and `native-vacuum-sensing.patch`, and compiles only their changed native class families against the exact stock dependencies. It removes the original versions of those entire class families (including nested classes) before overlaying the compiled classes. All other native classes remain from the original pinned stock JAR. The output includes the full patched source, patched native JAR, copied dependency JARs and samples, separately packaged bridge, test classes, and manifests with patch/source/classpath/artifact provenance. It never writes into the input stock runtime or source. Compilation does not start a machine or GUI.

The GUI patch adds a native executor admission token and a JobPanel ownership API. A claim requires an idle executor, no queued submissions, and a stopped GUI job. Submissions capture the token before queueing and verify it again before execution. A cancelled running Future retains its ownership reservation until its wrapper exits. GUI job execution/editing is blocked while externally owned. The separate scripting patch adds scoped Java event observers and an execution policy; it does not install event scripts.

## Launch contract

Launch `org.openpnp.Main` with the separately inventoried **preferences-only launcher JAR**, the patched native JAR, and the dependency list recorded by the build. The native JAR retains its stock manifest Class-Path entries; every referenced library must remain in the verified inventory. Keep the bridge JAR off the application classpath so the native bootstrap actually owns its URLClassLoader lifecycle. Set these JVM properties from the verified installation manifest:

| Property | Value |
| --- | --- |
| `configDir` | Absolute fresh, simulator-only OpenPnP configuration directory |
| `java.util.prefs.PreferencesFactory` | `org.openpnp.codex.IsolatedPreferencesFactory`, loaded from the separately verified `gui_launcher_jar` |
| `user.home` | Absolute fresh private GUI-session home directory |
| `java.util.prefs.userRoot`, `java.util.prefs.systemRoot` | Fresh private paths; the selected in-memory factory does not write them |
| `openpnp.codex.bridgeJar` | Absolute immutable bridge JAR |
| `openpnp.codex.bridgeSha256` | SHA-256 of that exact JAR |
| `openpnp.codex.stateDir` | Absolute private bridge journal/artifact directory |
| `openpnp.codex.sampleRoot` | Absolute verified runtime samples directory |
| `openpnp.codex.bootstrapPath` | Absolute verified bootstrap file inside this configuration's scripts directory |
| `openpnp.codex.bootstrapSha256` | SHA-256 of that exact bootstrap |
| `openpnp.codex.runtimeManifest` | Absolute patched `codex-build-manifest.json` |
| `openpnp.codex.runtimeManifestSha256` | SHA-256 of that manifest |

Use the same bounded heap and module opens as the isolated simulator. The test runner demonstrates the exact command. The macOS legacy Apple event adapter emits an upstream compatibility warning on JDK 17; ordinary Swing window lifecycle is tested separately. The selected pure-Java factory keeps UI preferences **only in memory for this GUI session**. UI preference persistence is unavailable; machine configuration, job documents and journals use their separate explicit persistence paths. On macOS, setting `java.util.prefs.userRoot` alone does not redirect the native MacPreferences backend. The JDK includes FileSystemPreferencesFactory class files but its required native `chmod` symbol is unavailable on the tested macOS JDK; that backend is not a valid isolation recipe. The bootstrap verifies the actual preferences implementation and the launcher JAR digest.

Native GUI startup must own the first configuration load: preloading the same singleton triggers upstream immediate listener callbacks before MainFrame widgets exist.

The native Scripts menu runs the verified `bootstrap.js`. It verifies the bridge digest, uses the native GUI classloader as parent, calls one fixed entry point, and closes an unused loader on duplicate/failed attachment. The entry point verifies the actual loaded patched core JAR against the manifest and creates a visible local controller. State/token files use Unix private permissions; other filesystem platforms are not qualified by this candidate.

OpenPnP creates example executable scripts on GUI startup. After verifying the native runtime, bootstrap compares the entire Examples tree with the exact 16 native JAR resources. Only a complete, byte-identical tree is atomically moved into a retained private archive outside the active scripts tree. Modified/incomplete/unknown files or symlinks are refused before moving anything. At most 32 archives are admitted. Other user scripts remain untouched and prevent control grant. The GUI tests include modified-example refusal and exact-byte retention. After local grant, **every script evaluation is refused** until release. A File-hash allowlist alone would not prove the bytes subsequently read by a script engine are immutable.

## Local ownership and cleanup

In the selected S79 ownership patch, `JobPanel.setExternalJob` preserves the previous native job's root children and placed history while selecting the new job. It removes the previous job's title/file listeners and attaches those UI listeners to the selected job. The external owner retains responsibility for the old graph and its model subscriptions: release those resources only after no active operation, recovery transaction or retained candidate needs them. An unknown native effect is not a cleanup boundary. Releasing the external-control token does not release retained graphs. Ordinary local `setJob` still removes the previous root's children.

This behavior requires the revised `gui-ownership.patch`, SHA-256 `a1de28f8907ef703280615621c87434c1853fd622696f73f38b2fd8477815079`; the unchanged API version alone does not identify it. The isolated candidate02 runtime rebuilt all 46 `JobPanel` class-family members, with only `JobPanel.class` differing from the immutable B78 runtime. Its native JAR SHA-256 is `01471eb82e8538f70359a220505589ed64dce98f365b19a8541296a68637a791`. The original graph-loss failure and fixed real MainFrame component test remain private historical evidence. The candidate02 identities above identify that intermediate runtime. The selected source41 package has separate manifests and passes the scripted combined restart-to-placement journeys described in [current qualification](../references/qualification.md). Desktop interaction, full release and physical qualification remain open.

The modeless local controller exposes **Allow Codex control**, **Take local control**, and **Disconnect bridge**. A grant makes it application-modal, acquires native executor and JobPanel ownership, and admits one remote lease. Lease release/expiry triggers cooperative takeover. Takeover revokes remote admission, waits for the current native step, runs the native authorized abort cleanup under the same observer/durability boundaries, waits for standstill, drains all native reservations, and restores local GUI ownership. It is not an emergency stop.

An uncertain native effect keeps control fenced. A local-only **Exit simulator preserving unknown** gesture can exit the simulator JVM without aborting, saving, replaying, or declaring the effect resolved. Prior forced action intents remain authoritative. This action is not an RPC. Known drained detach closes the bridge, removes its window listener and shutdown hook, removes the controller registry, disposes its dialog and closes its URLClassLoader. A failed constructor also releases its journal lock, listeners, executors and loader so a corrected same-JVM retry is possible.

## Tests

```sh
python3 scripts/openpnp-test-native-gui.py \
  --build /absolute/this-candidate/validation/new-build \
  --java-home /absolute/jdk17-home \
  --run /absolute/this-candidate/validation/new-gui-test
```

Add `--unknown-exit` to run the separate local-only uncertain-effect exit case; its expected JVM exit is 2 and the outer runner verifies the durable disposition plus absence of cleanup/feed-completion records. A display server and loopback-listener permission are required. The test uses actual native MainFrame/JobPanel, native Nashorn bootstrap, HTTP dispatch and real native job processing. It intentionally leaves the bridge off the application classpath. Every run uses fresh config/preferences/journal roots and preserves logs and hashes. The script's successful completion is required before claiming its listed workflow passed; earlier failed attempts remain evidence.

`openpnp-test-native-candidate.py` runs selected native mains against either the patched runtime or an explicit `--runtime` stock distribution. A patched bridge's stock compatibility must be tested separately; compilation against patched classes alone does not demonstrate it. The native action ledger is available only when the patched observer API is present. Its observed hook pairs are native return boundaries, not independent physical verification, and its advertised gaps remain relevant.

The third additive patch exposes an immutable, detached snapshot of the native Job placed-status map, including removed placement keys. Simulator board replacement preserves the retired load history and resets only the selected root scope using the existing native removal API. Flip and same-load retain completed history. Local ID segments containing the native history delimiter, duplicate expanded identities, non-neutral inline roots, inconsistent parents and regressing histories are refused before journal publication. Explicit changeover requires this patch; stock-runtime compatibility covers initial fresh simulator imports only. Restart and document reload always require explicit simulator load binding and never establish physical presence.

The GUI topology patch queues native collection notifications onto Swing’s event thread in MachineControlsPanel, JogControlsPanel, AxesComboBoxModel and ActuatorsComboBoxModel. Native machine execution remains on its owned executor. Selector refresh retains existing selections instead of publishing transient null assignments. GUI attachment requires the topology patch provenance and verifies all four class origins against the inventoried native JAR.

### GUI topology regression

After building the runtime, run the reusable simulator GUI topology check in a fresh output directory:

```sh
python3 scripts/openpnp-test-native-gui.py \
  --build validation/new-build/build \
  --java-home "$JAVA_HOME" \
  --run validation/gui-topology-check \
  --topology
```

`--topology` and `--unknown-exit` are mutually exclusive. The topology mode attaches through the native script with the Bridge absent from the application classpath, obtains local simulator control through the GUI component API, and creates a second assembly while disabled. It checks event-thread updates in the real tool selector, actuator button panel, and open axis/actuator selector models; existing selected assignments must not change. The native model notifications must remain on the owned machine executor. The same run completes the real 32-placement sample with both original and new nozzles identified in native action/completion hooks, then releases control and detaches. This test uses GUI component APIs; it does not claim desktop gesture or physical machine qualification.

### Local board-inspection regression

Use a new run directory and provide the absolute Node.js 22.19-or-newer binary:

Pass the directory containing `build-manifest.json` to `--build`. The lower-level
builder above writes directly to its `--output` directory; the canonical wrapper
reports a `build` subdirectory instead.

```sh
python3 scripts/openpnp-test-native-gui.py \
  --build /absolute/this-candidate/validation/new-build \
  --java-home /absolute/jdk17-home \
  --run /absolute/this-candidate/validation/new-inspection-gui-test \
  --node /absolute/node22/bin/node \
  --board-inspection
```

This separate mode requires the source checkout's locked Node dependencies for
the official MCP SDK helper. It runs an eight-placement native panel job, opens
the local inspection form after completion, and checks exact decimal retention,
invalid-input correction, consumed failure and takeover. One form
request and task read pass through the packaged stdio MCP server. The
local form receives explicitly synthetic test observations. The runner checks
its owned JVM/process group, while the helper checks MCP child cleanup. It keeps
the Bridge off the application classpath. This tests native GUI components,
not desktop gestures or physical metrology.

## Circular detector provenance

The fifth patch replaces strict single-cell maxima with connected equal-score
regional maxima in `DetectCircularSymmetry`; public APIs and thresholds remain
unchanged. `upstream-patches/native-circular-symmetry.json` binds the exact patch,
stock source, complete GPL corresponding source, Java 11 target, and all six
reviewed class identities. The source retains its upstream copyright/license;
GPL terms are included in `licenses/OpenPnP-GPL-3.0.txt`.

The canonical builder checks the source before and after patching, compiles the
entire six-class family, removes every stock version of that family, and verifies
the new JAR entries against all six reviewed hashes. A compiler producing other
class bytes requires a separate review; an old manifest must not be reused.
The `native_circular_symmetry` object appears in both build and runtime manifests,
including `patch_sha256`, `source_sha256`, and `class_family_sha256`. Package
verification checks this object against the retained patch/source contract.

Runtime provenance alone does not establish physical calibration. The bounded
camera-scale recipe separately verifies loaded class origins/JAR bytes and
requires the detector provenance before use. Discover its current capability
and limits before requesting a measurement.
