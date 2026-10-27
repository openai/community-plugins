# Launch a fresh GUI simulator

This candidate adds a GUI launcher beside the existing headless `start-simulator` command. It requires the separately built GUI-ownership/action-observer native runtime, its isolated-preferences launcher JAR, and the matching installed bridge/verified bootstrap. The launcher supports macOS/Linux private-file semantics; it does not accept an existing machine configuration or qualify physical hardware.

```sh
node plugins/openpnp/scripts/openpnp.mjs install-bridge --state-dir /absolute/candidate-state
node plugins/openpnp/scripts/openpnp.mjs start-gui-simulator \
  --state-dir /absolute/candidate-state \
  --openpnp-home /absolute/verified-gui-runtime \
  --java /absolute/jdk17/bin/java
```

The bridge installation's build manifest must pin the selected patched native JAR, runtime manifest, and packaged `bootstrap.js` SHA-256. The runtime inventory must include `gui_launcher_jar`, which contains only the isolated preference factory. An older headless-only bridge installation is rejected before Java is started. Use a separate candidate state directory while another simulator is running.

## Operator flow

1. The launcher verifies runtime and bootstrap hashes, then creates a new `gui-simulators/gui-…` session below the selected state directory. Every invocation receives a new native configuration, private home, and bridge state. UI preferences exist only in memory for that JVM and are not persisted to the user's operating-system preference store. Native configuration/job XML and bridge evidence remain on disk.
2. Plain native `org.openpnp.Main` displays its Welcome dialog. The launcher reports `connected:false` while waiting. Complete Welcome, then choose **Scripts → codex-bootstrap.js**.
3. The verified native bootstrap compares the generated sixteen example scripts with their exact native-JAR resources before moving them outside the active script tree into its retained bridge state. Modified, incomplete, unknown, or linked scripts are refused before that move. User scripts are never removed by the launcher. See `bridge/BUILD_GUI.md` for the native boundary.
4. The GUI emits `OPENPNP_CODEX_GUI_READY` after attachment. The launcher independently authenticates a read-only capability request and checks the actual bridge hash, pinned upstream, GUI simulator profile, and physical-qualification flag. Only then does it report `connected:true` and select that session's connection in the state directory.
5. Use the visible local controller's **Allow Codex control** button when ready. The launcher never grants control or acquires a remote lease. Its `local_grant` field reflects the native observation, separately from transport connectivity.

Waiting for the manual bootstrap has no automatic readiness timeout. Exiting the GUI before attachment reports `gui-exited-without-attachment` and preserves any prior connection selection. Closing after attachment reports that the GUI session ended; its saved connection is historical metadata, not proof the bridge still runs. `doctor` performs a fresh connection check.

The launcher stays in the foreground and forwards Ctrl-C/SIGTERM to its own JVM. An unsolicited child signal or nonzero exit fails the launch; an intentional forwarded stop is reported separately. Closing or stopping a simulator is not a claim that an interrupted native operation completed. The configuration, logs, launcher receipt, native journal, and archived examples are retained.

## Isolation and verification limits

The bridge JAR is absent from the native application classpath and is loaded only by the verified manual bootstrap. The classpath supplies the verified preferences-only JAR, native application JAR, and individually inventoried dependencies. The stock native JAR also carries its upstream `Class-Path` manifest entries; the verified native build retains those pinned library resources. Wildcard libraries and inherited `JAVA_TOOL_OPTIONS`, `JDK_JAVA_OPTIONS`, `_JAVA_OPTIONS`, or `CLASSPATH` are not used by this GUI command.

`tests/openpnp/gui-launcher.test.mjs` uses explicitly labelled installer/executable/HTTP fixtures to test arguments, fresh roots, integrity rejection, readiness/authentication, no automatic control, and process lifecycle. Those fixture tests are not native GUI qualification. The separate native GUI and end-to-end launcher runs must provide that evidence. Existing headless launcher/installer regression tests are run alongside the new tests.
