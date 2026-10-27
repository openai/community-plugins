# Vacuum sensing development source

This is the historical, unshipped sensing77 development overlay for the pinned OpenPnP simulator. It adds native observation hooks, an explicit controlled sensor source, typed settings, bounded sensor/check operations, and a durable observation reducer. At this checkpoint, public Bridge/MCP integration was pending and the qualified inspection75 plugin was unchanged. The current source41 implementation and its qualification are documented separately; do not apply this historical overlay to the current source.

## Contents

- `overlay/`: 13 files at their intended repository paths, including five test programs, three source/mirror pairs, the native patch, and the candidate builder.
- `native-source/`: complete corresponding source for the four additional patched OpenPnP classes, taken from the successful build.
- `manifest.json`: required baseline input hashes, overlay hashes, corresponding-source hashes, and tested candidate artifact identities.
- `LICENSE.txt`: OpenPnP's GPL license. The new Java files carry GPL-3.0-or-later SPDX declarations.

The experiment is outside production source directories and is not loaded by the installed plugin. Read the [design](../../../../docs/openpnp-vacuum-sensing-design.md) and [test evidence](../../../../tests/openpnp/evidence/sensing77-development/summary.json) before integration.

## Reproduce in an isolated candidate

1. Make a fresh owned copy of the qualified repository source. Do not apply the overlay to a running installation or overwrite an existing candidate.
2. Verify every `base_inputs` entry in `manifest.json` against that copy. Apply each `overlay_files` entry at the same relative path, and verify its SHA-256. The native corresponding source is a review artifact; the builder generates it by applying the six patches to clean upstream source.
3. Use a clean upstream checkout at `5bd404cfc70f34103a3ca0fbb6b50c2b465f407c`, its original stock runtime distribution, Python 3.12 or newer, and JDK 17. From the candidate root run:

```sh
python3 scripts/openpnp-build-native-gui.py \
  --stock-source /absolute/path/to/pinned/openpnp \
  --stock-runtime /absolute/path/to/original/stock-runtime \
  --java-home /absolute/path/to/jdk17 \
  --output validation/sensing-build

python3 scripts/openpnp-test-native-candidate.py \
  --build validation/sensing-build \
  --java-home /absolute/path/to/jdk17 \
  --run validation/sensing-focused \
  --test NativeVacuumSensingTest \
  --test NativeVacuumSettingsTest \
  --test NativeVacuumOperationsTest \
  --test NativeVacuumJournalTest
```

Run `NativeVacuumJournalIntegrationTest` separately with a new owned output directory as its positional argument. Its `recover <output-directory>` mode reads the closed histories in a fresh JVM. **Do not pass it to the generic candidate test runner:** that runner supplies the simulator sample directory, while this test expects an output directory. Preserve the isolation/module-opening/classpath settings from the focused runner. Native tests use synthetic signals in real OpenPnP classes; they do not qualify pneumatic physics.

The existing 82-test native suite comes from `TESTS` in `scripts/openpnp-build-native.py`. Its Bridge/controller cases require local loopback listeners. Keep those endpoints local and use isolated simulator homes/preferences.

## Before public integration

Implement authentic Bridge context binding, source readiness before the first feed, action-boundary invalidation of old empty observations, session/revision/cancellation guards, persistent fault recovery, explicit null-preserving journal encoding, and sensing-aware snapshot/adoption rules. Update the package's sixth-patch/source provenance checks and MCP schemas. Then qualify the complete public workflows, GUI, other supported platforms and skills. A component pass alone does not make a machine ready for operation.
