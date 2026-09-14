# OpenPnP verification

Tests distinguish local data logic, actual MCP transport, native OpenPnP execution, and physical qualification. The user selected simulator-first work; no physical machine is available. The active implementation status is in `docs/openpnp-implementation-status.md`.

## Reproducible package checks

From the repository root, with Node.js 22.19 or newer:

```sh
npm run build:openpnp
npm run test:openpnp
npm run verify:openpnp:source
npm run validate
npm run test:marketplace
```

`test:openpnp` installs the exact source lockfile and dispatches the complete prerequisite-gated automated suite. Its always-on contract phase runs Node tests, all Python harness unit tests, and source rebuild/offline verification. Native and GUI phases report explicit skips by default; an explicitly enabled phase with missing prerequisites fails before dispatch. The packaged MCP server itself requires no dependency installation. Source verification rebuilds in a temporary directory, compares generated server/schema/license bytes and asks a real MCP SDK client to verify discovery.

The default suite includes imports, geometry, material/inspection records, request validation, lost replies, credential boundaries, artifact integrity, lifecycle failures, offline journal diagnostics, skill contracts and workload-harness checks. Stage06 recorded 18 live-test skips. A clean checkout also skips six dated historical-fixture checks unless OPENPNP_HISTORY_JAVA, OPENPNP_HISTORY_RUNTIME and OPENPNP_HISTORY_REAL_FIXTURES are explicitly supplied; the CI history runner generates and verifies fresh native histories separately. The synthetic packaged recovery test creates its own fresh private fixture when no state directory is supplied. A skipped live test is not an integration pass.

## Native OpenPnP checks

Build and test the pinned upstream application and bridge:

```sh
git clone https://github.com/openpnp/openpnp.git /absolute/path/openpnp-source
git -C /absolute/path/openpnp-source checkout 5bd404cfc70f34103a3ca0fbb6b50c2b465f407c
OPENPNP_SOURCE=/absolute/path/openpnp-source npm run test:openpnp:native
```

Use JDK 17 and Maven 3.9 on the host, or run `npm run test:openpnp:native:docker` with the same `OPENPNP_SOURCE`. The Docker script pins its build image by digest. The build rejects a different upstream commit or modified tracked upstream source.

Native tests call the actual OpenPnP machine executor, cameras, feeders, configuration models and `ReferencePnpJobProcessor`. They cover the sample and imported jobs, staged settings, runout calibration, fiducial location, ownership and recovery boundaries, configuration-save failures, native document round-trips, capacity limits, listener retention and resource admission. Temporary fault fixtures test a particular failure mechanism; they do not represent physical-device qualification.

Do not rebuild the shared runtime while a launcher or sustained test is using it. Native JARs have a fixed archive timestamp; build and loaded-JAR hashes identify the actual tested code.

## Complete MCP-to-native workflow

Follow `plugins/openpnp/README.md` to install the bridge and start a **fresh** `native-simulator` instance, then run:

```sh
OPENPNP_E2E_CONNECTION_FILE=/absolute/path/fresh-state/connection.json \
OPENPNP_E2E_EVIDENCE_DIR=/absolute/path/new-evidence \
  node --test tests/openpnp/live.test.mjs
```

The test uses the official MCP SDK and packaged stdio server. It checks native enable/home/motion, a captured PNG, configuration plans/restore, stale-plan rejection, a 32-placement sample with pause/resume and deduplication, a one-placement imported job, native save/reload, issue scan and run report. It consumes native feeder stock; reusing an exhausted fixture does not count as a fresh test.

To test supervision loss, select an idle `sustained-workload` simulator with at least 200 remaining R0603 parts:

```sh
OPENPNP_E2E_RECOVERY_CONNECTION_FILE=/absolute/path/sustained-state/connection.json \
OPENPNP_E2E_EVIDENCE_DIR=/absolute/path/new-reconnect-evidence \
  node --test tests/openpnp/reconnect-live.test.mjs
```

This test disconnects both MCP processes during native work, allows the ten-second lease to expire, reconnects, verifies native process continuity, and resumes the original operation under a new lease. It checks exactly 200 additional feeds and placements. It does not inject a JVM or controller crash. A previously consumed idle sustained fixture is acceptable here because the test measures its exact inventory delta; the full soak below requires fresh stock.

## Eight-hour and 10,000-placement run

Start a fresh `sustained-workload` simulator from the final immutable native build. Use its JVM PID, connection and runtime manifest:

```sh
node scripts/openpnp-soak.mjs \
  --connection-file /absolute/path/fresh-sustained-state/connection.json \
  --native-pid 12345 \
  --native-build-manifest /absolute/path/openpnp-source/target/codex-runtime/codex-build-manifest.json \
  --evidence-dir /absolute/path/new-soak-evidence
```

Defaults are eight hours and 10,000 placements. A short trial, such as `--duration-hours 0.03 --placements 100`, exercises the harness but remains explicitly unqualified. The full run requires matching loaded-JAR/package hashes, fresh finite supply, exact placement/feed counts, slow-consumer event resynchronization and continuing camera/motion workload after the placement job. It records JVM heap, optional process RSS, storage and latency samples, and preserves failure/interruption evidence without replaying uncertain actions.

The maximum board/panel model boundaries are separately tested by `NativeGraphLimitsTest`. `scripts/openpnp-placement-benchmark.py --help` describes the paired direct-native versus bridge timing experiment. Status-request latency and placement-cycle overhead are different measurements. Simulator timings use accelerated native motion and zero dwell/settling; they are not physical throughput claims.

## Evidence and release scope

- `coverage.json` maps R01–R18 to skills, tools, native adapters, tests, evidence and remaining scope.
- `evidence/native-*.json` identifies the native build and executed checks.
- `evidence/mcp-native-*.json` records complete MCP workflows and offline journal inspection.
- `evidence/soak-smoke/` retains the short trial, its initial pre-mutation failure and explicit sanitization provenance.
- Skill evidence preserves historical case versions, the original partial result and later documented contract walkthroughs. It is not a uniform new blind evaluation of every changed skill.

An evidence file applies to its recorded hashes and scope. Native placed flags are not independent inspection. Physical machine control, actual manual desktop installation, complete portable configuration restore and broader per-effect crash recovery remain separate qualification requirements. The current scripted native GUI ownership/restart evidence has the explicit seams in [the portable summary](evidence/sensing79-review/summary.json).

The single OpenPnP CI job runs the root master sequentially on Node22/24/26, then the pinned native build, all 91 native mains, packaged MCP/controller/history and JVM-halt qualification. GUI, hardware, current performance and eight-hour endurance need their own environments; skips do not count as passes. Hosted success is claimed only after an actual workflow run.

## Root master qualification gates

Use the same root command for the full headless native campaign, on a host with Docker and the pinned clean upstream checkout:

```sh
OPENPNP_TEST_NATIVE=1 OPENPNP_SOURCE=/absolute/path/openpnp-source \
OPENPNP_TEST_OUTPUT=validation/new-full-campaign npm run test:openpnp
```

This runs the contract phase, canonical 91-main native build/tests, a fresh generated package through all 17 MCP workflows plus controller/history, then the actual JVM-halt/recovery campaign, in order. A phase failure stops later phases; no automatic retry or existing output reuse occurs. Linux hosts use the current Node distribution in the pinned JDK container; it must be Node22.19. Non-Linux hosts must additionally set `OPENPNP_TEST_CONTAINER_NODE_ROOT` to an explicit compatible Linux Node22.19 distribution (containing `bin/node`). The native builder independently verifies the upstream commit and runtime inventories.

For the seven existing programmatic native GUI cases, use `OPENPNP_TEST_GUI=1`, `OPENPNP_TEST_GUI_BUILD=/absolute/path/to/immutable-build-below-this-checkout`, `OPENPNP_TEST_JAVA_HOME=/absolute/path/jdk17`, and a fresh `OPENPNP_TEST_OUTPUT`; then run the same master. A compatible native desktop display is required. The GUI runner verifies its actual runtime manifest before execution. These are scripted component cases, not manual installed-desktop qualification.

Performance and the eight-hour workload use the explicit protocols above; physical and manual desktop qualification require their actual environments. The default master prints these omissions as skips and never promotes them to passing results.
