# OpenPnP Codex plugin

Configure and operate the pinned OpenPnP native simulator through **61 typed MCP tools, 34 configuration change types and 14 skills**. This review candidate includes guarded configuration, native vision/motion/jobs, material and board loads, inspection records, and local sensing reconciliation with bounded crash/restart replacement. Read runtime capabilities before choosing a workflow.

The current source41 build passes 91 native programs on each of macOS ARM64 and Linux ARM64, 17 packaged MCP workflows, and two scripted native GUI restart-to-placement journeys. Stage06 package assembly and Node22 pass 475 tests with 18 explicit live-test skips. A separate source-SDK live history case passes. These are distinct scoped results; see [qualification and limitations](references/qualification.md).

**The full implementation plan remains unfinished: all R01–R18 are partial.** No physical machine is available. Manual desktop installation, uniform fresh evaluation of all fourteen skills, current performance and continuous endurance remain unqualified. Physical control is unavailable in this candidate.

## Known source41 limitation

Source41 has a reproduced mapped-axis homing postcondition defect: a native input-axis setter listener can mark the machine homed during configuration apply, and the singleton apply path can report success. A separate S80 repair is under test and is not included here. Treat this path as unqualified; require independent current-state verification before dependent operation.

## Prerequisites

- Codex with local plugin and stdio MCP support.
- Node.js 22.19 or newer on the Codex host.
- A separately built OpenPnP runtime pinned to commit `5bd404cfc70f34103a3ca0fbb6b50c2b465f407c`.
- JDK 17, Python 3.11 or newer, Git and `patch` for the native build. A first stock runtime build also needs Maven 3.9; the pinned Docker wrapper supplies these build dependencies. Running the resulting simulator requires a compatible Java runtime or container.
- A local writable state directory. Tokens and connection files must have owner-only permissions on Unix. Machine history belongs in this persistent state directory, outside the Codex plugin cache.

No physical machine is required for the simulator workflow. Real serial drivers, network controllers, and unsupported cameras/feeders are rejected by the native mutation guard. GUI control requires the verified patched native runtime, including ownership and GUI event dispatch, and an explicit local grant. The camera-scale recipe additionally requires the verified circular-symmetry patch.

## Build from this repository

From the repository root:

```sh
npm run build:openpnp
git clone https://github.com/openpnp/openpnp.git /absolute/path/openpnp-source
git -C /absolute/path/openpnp-source checkout 5bd404cfc70f34103a3ca0fbb6b50c2b465f407c
OPENPNP_SOURCE=/absolute/path/openpnp-source npm run test:openpnp:native
```

With Docker, use `npm run test:openpnp:native:docker` instead of the last command. Each native build creates a new directory under `validation/` and returns a receipt identifying its bridge JAR, patched native runtime and test results. It uses a verified stock runtime or builds one in a fresh archive of the pinned source. It does not modify the upstream checkout or replace the packaged plugin. See [the canonical build and publication contract](bridge/BUILD.md) for selecting an output path and publishing matching validated artifacts. Do not replace runtime JARs or sample assets after the hash inventory is generated.

The bundled MCP server has no runtime npm-install step. Dependencies and license notices are included in the built plugin; source dependencies use an exact lockfile.

## Start the simulator

Run the bundled CLI from any directory, substituting absolute paths:

```sh
node /absolute/path/plugin/scripts/openpnp.mjs install-bridge --state-dir /absolute/path/openpnp-state
node /absolute/path/plugin/scripts/openpnp.mjs start-simulator \
  --state-dir /absolute/path/openpnp-state \
  --openpnp-home /absolute/path/validated-build/build/runtime \
  --java /absolute/path/jdk17/bin/java
```

The launcher verifies the bridge/runtime hashes, creates a private token, starts a fresh isolated default OpenPnP configuration, and writes `connection.json` after the authenticated native readiness check passes. It limits the JVM heap to 2 GiB; native camera allocations also contribute to process memory and are measured separately. It stays in the foreground; Ctrl-C stops that simulator process. Journals survive restarts; the simulated machine configuration is freshly created on each launch.

Set `OPENPNP_CONNECTION_FILE` in the Codex MCP environment to the absolute `connection.json` path. Optionally set `OPENPNP_STATE_DIR` for local import and evidence artifacts. Reload the plugin's MCP server after changing its environment.

For an already running local bridge, use:

```sh
node /absolute/path/plugin/scripts/openpnp.mjs configure \
  --state-dir /absolute/path/openpnp-state \
  --url http://127.0.0.1:PORT/ \
  --token-file /absolute/path/openpnp-state/bridge.token
node /absolute/path/plugin/scripts/openpnp.mjs doctor --state-dir /absolute/path/openpnp-state
```

The URL must use a loopback IP and explicit port. Credentials are read from a file, never passed as a token argument or printed in normal output.

## Optional GUI simulator

The separately built patched runtime supports a fresh desktop simulator through `start-gui-simulator`. Follow [the GUI build recipe](bridge/BUILD_GUI.md) and [the GUI launcher workflow](scripts/GUI_LAUNCH.md). Native code enforces job/executor ownership, while the visible local panel controls grant, takeover, and disconnect. UI preferences are confined to the session; machine XML, documents and journals remain on disk. Existing physical configurations remain unsupported.

## First prompt

> Use setup-openpnp to inspect capabilities. Connect to my native simulator, acquire a simulator session, home it, and run the bundled pnp-test job after validation. Show the actual placement counts and inspection limitations.

Typical control flow:

1. `openpnp_get_capabilities`, status, and configuration.
2. Acquire the simulator lease; enable and home through native operations.
3. Prepare the sample or import a canonical job artifact. Native imports require existing compatible package definitions and known part heights.
4. Validate the native model, then start the selected job.
5. Observe operation IDs. Pause/resume at native cooperative boundaries; export the completed run report.
6. Disable and release the lease when the workflow finishes.

Every asynchronous operation must be observed to its terminal state. An accepted response is not completed motion. Native placed flags are distinct from independent inspection.

## Tools and skills

The MCP interface provides capabilities/status/configuration, leases, planned movement, camera capture, supported typed configuration, calibration, fiducial location, Issues & Solutions scanning, native job control, feeder/actuator/nozzle actions, bounded backup/restore, journal observations, and report artifacts. Unsupported native operations are blocked before forwarding. Local imports and validation remain available while disconnected. The exact public input schemas ship in `mcp/tool-inputs.json` and are generated from the same definitions used by runtime validation and MCP discovery.

Configuration plans support 34 change types covering machine speed; part height and properties; part/package creation; package footprints and tip compatibility; feeder assignment, enabled state, strip and tray geometry; basic 2D camera geometry and settling; nozzle/tip dwell and compatibility; linear axis limits and backlash; complete simulator nozzle assemblies; native job planner/retry settings; head park XY and discard pose; part/feeder retry counts; and complete native vacuum-sensing settings. Planning validates the whole patch before applying it. Read the native settings snapshot for actual editable device classes and values. Geometry changes invalidate affected native calibration; linear axis changes also require homing again.

Camera geometry apply and typed snapshot restore clear cached native board/panel fiducial transforms and active-load registration before camera setters. Settling-only changes leave registration intact. The focused tests seed native registration state; they do not measure fiducials or establish calibration accuracy. The registration fix is covered by ten isolated native cases / 246 checks on each platform. That registration68 qualification did not include the later camera-scale recipe.

**Planar camera scale.** When `camera_planar_scale.runtime.available` is true, the candidate can measure a supported down-looking simulator ImageCamera against one isolated circular feature. [The camera guide](references/camera-and-vision-calibration.md#planar-camera-scale-measurement) explains initialization of the existing source, explicit camera positioning, FixedTime settling, bounded sampling and artifact retrieval. The recipe retains eight frames on acceptance and proposes X/Y scale; it does not change geometry. Preview and apply an available proposal separately through the typed configuration tools, then refresh invalidated registration, nozzle runout and job validation. A completed operation can still contain a rejected measurement or an accepted measurement with an unavailable proposal.

The fixed policy checks image-motion signs, cross-axis response, a maximum 20% correction and one-pixel holdout/final-baseline prediction errors. The stock-image trial's inferred Y scale differs from the independent renderer by 2.71%; passing the image check does not establish 2% absolute accuracy or physical calibration. Failed or unresolved sequences preserve their images and last pose without automatic replay or cleanup movement.

Native document bundles save and reload the current job with its board/panel definitions, per-instance exclusions, and placed history. Reload accepts a verified bundle from the same owned persistent store, including after a bridge restart with unchanged referenced part/package definitions, and invalidates transient registration. Root-panel pseudo-fiducials and explicit custom definition/root outlines are rejected because the pinned OpenPnP outline serializer does not round-trip them reliably. Configuration snapshots restore the supported setting families from verified retained artifacts, including after restart and report omissions; added identities and newer material counts remain intact.

The 61-tool integration catalog includes [native placement inspection and typed edits](references/native-placement-editing.md), [board-load registration and panel history](references/boards-panels-and-registration.md), job stepping, placement insertion/removal, dynamic camera settling, mapped-axis geometry, [typed backlash compensation](references/motion-and-homing.md) and [portable simulator adoption](references/portable-simulator-adoption.md). Installed capabilities determine which tools the selected native profile supports. Inspection includes excluded records and source fingerprints. Native edits preserve placed history and invalidate dependent registration and validation.

Portable export binds an idle, disabled source to its observed configuration revision. A separate native JVM validates seven native XML documents, inventoried images and the declared saved flat-board library before publishing a new adoption; scripts remain quarantined. The board profile admits up to 32 clean saved boards and 10,000 placement definitions, with exact resource completeness and native load/save checks. First launch uses a new journal and simulator identity and starts disabled and unhomed. Existing-directory replacement and spent activation reuse are refused. Read the portable profile's limits before choosing this workflow.

The [owned controller diagnostic](references/controller-diagnostics.md) has a separate fresh launch and restricted native profile. It saves a fixed native model, exchanges tagged G21/G90/M115 commands with its own in-process responder, then closes the connection. It adds no arbitrary-controller, firmware, physical motion or serial-device support. Native simulator job workflows continue to use their existing profile.

Use `view: "progress"` on status, operation and request-status reads for repeated polling; fetch `full` once at pause/terminal for complete evidence. Large full JSON replies return bounded summaries with their known outcomes, IDs and counts. `openpnp_read_response_page` retrieves selected details or exact retained bytes without repeating a native action; native camera frames remain MCP images. Retention failures preserve the known action outcome. See [the response contract](references/control-contract.md#bounded-responses-and-exact-details).

The optional native observer runtime journals feed, pick, release, alignment, discard and automatic changer hooks with their operation/placement/tool/material context. Unpaired hooks and explicit unknown outcomes remain unresolved through recovery. Hook records remain software observations; sensing, arbitrary scripts, some native cleanup details and independent physical verification are outside that coverage.

The headless sustained simulator additionally supports [explicit virtual tray-load binding and same-part full-tray replacement](references/virtual-tray-loads.md). It retains consumption across separate loads, requires fresh job validation, and does not restore material authority after restart. The controller profile keeps its existing diagnostic-only capability set.

Job validation counts pending, already placed, and excluded placements separately. It checks package/height definitions, compatible native tooling, manual changer requirements, calibration prerequisites, enabled feeder bindings and known finite stock. Unknown capacity is explicitly reported. A successful model preflight is not independent inspection or proof that a physical pickup will succeed.

Fourteen skills cover setup, commissioning, motion calibration, vision calibration, tooling, feeders, job preparation, board alignment, job validation, production, recovery, maintenance, optimization, and backup/restore. Their shared references describe prerequisites and capability-dependent limits. A skill mentioning a future qualified operation does not make that operation available.

Canonical imports support reference CSV, KiCad position files, BOM joins, DNP/variants, top/bottom side conventions, board/panel transforms, and X-outs. KiCad bottom rows require an explicit convention. Unknown heights, conflicting mappings, invalid numbers, ambiguous columns, and duplicate references remain errors. Native XML is not parsed by the local domain importer.

## Control and data boundaries

- The native bridge checks actual runtime classes, ownership, revisions, and operation state. The language model does not generate raw serial commands, G-code, or Java evaluation payloads.
- OpenPnP remains responsible for native motion and placement. The bridge runs actions on its native machine executor and waits for the native completion condition required by each workflow. Diagnostic closure is not physical standstill.
- A lost mutation response produces `OUTCOME_UNKNOWN`. Reconcile the original request ID; never substitute a new request ID to repeat an uncertain movement, feed, pick, or place.
- Pause is cooperative. Native abort may move axes or discard a part. Neither is a hardware emergency stop.
- Restart history is retained. Abandoning an unknown operation after a fresh simulator reset records that disposition without replaying its historical action.
- Typed backups restore a supported subset of existing settings. The separate portable workflow covers fresh adoption of its declared bounded simulator profile; it does not provide in-place rollback or arbitrary physical-machine migration. Neither workflow restores physical position, placement outcomes or operational authority.
- Nozzle runout results are native fitted offsets. Independent residual measurements and hardware qualification remain separate.
- This is a local bridge. A privileged process or malicious program running as the same user is outside the token's isolation boundary.
- Camera frames, imported designs, configuration snapshots, and reports can contain private engineering data. The plugin writes them locally and returns requested content to the active Codex task. It has no external telemetry endpoint.

## Updates and removal

Install a new version alongside the existing version, then switch only while the simulator is stopped. The CLI checks hashes and refuses silent replacement of a different JAR under the same version. Interrupted staging does not publish a truncated JAR. Keep the original build and receipts for rollback.

```sh
node /absolute/path/plugin/scripts/openpnp.mjs uninstall-bridge --version 0.1.0 --state-dir /absolute/path/openpnp-state
```

Uninstall refuses a responding or uncertain local endpoint. It removes only the selected bridge installation; user configuration, tokens, artifacts, and operation journals remain. Removing the Codex plugin alone does not erase machine history.

## Verification

```sh
npm run test:openpnp
npm run verify:openpnp:source
OPENPNP_SOURCE=/absolute/path/openpnp-source npm run test:openpnp:native:docker
npm run test:openpnp:mcp-harness
npm run test:openpnp:mcp -- \
  --runtime /absolute/path/validated-build/build/runtime \
  --java /absolute/path/jdk17/bin/java
```

Test the separate controller diagnostic with a fresh output directory:

```sh
npm run test:openpnp:controller -- \
  --runtime /absolute/path/validated-build/build/runtime \
  --java /absolute/path/jdk17/bin/java \
  --output /absolute/path/new-controller-evidence
```

This launches the installed CLI, verifies its authenticated native profile, drives the diagnostic through the official MCP client, checks single execution and retained history, and stops its owned processes. It does not connect to a physical controller.

The default suite covers deterministic imports, geometry, evidence/ledger logic, transport contracts, malformed/lost responses, package integrity, installer failures, and a real MCP SDK stdio client. HTTP fixtures in unit tests test transport behavior; they are not OpenPnP simulators. The native suite links the real pinned OpenPnP application, its cameras and feeders, and `ReferencePnpJobProcessor`. The live suite tests the entire MCP-to-native path and requires an explicitly configured simulator; its default skip is not a pass for native integration.

The macOS/Linux MCP runner installs the packaged bridge into a new owned state directory and starts a fresh native simulator for each selected core, vision, structure, placement, recovery, portable, camera, stepping, mapped-axis, backlash, material-load or nozzle-assembly suite. Recovery uses the sustained fixture for 200 native placements, explicit progress polling, client disconnect, lease expiry and original-operation resume. It preserves logs, journals, exact input hashes and completion evidence under a new `validation/` directory, including the MCP response store when recovery fails. It rejects skipped tests and forced process cleanup as successful qualification. The focused `--suite material` workflow uses a fresh four-slot virtual tray for two real native jobs with five total placements and explicit replacement. The `--suite backlash` workflow applies all five native compensation methods and verifies motion, readback and restore. The portable workflow runs the native sample, exports its saved board library and settings, then runs a job in a fresh adopted instance. Select one of those names with `--suite` for a focused rerun; reruns use fresh simulators and retain earlier results. Its separate harness tests exercise interruption and child-process cleanup with controlled test programs.

See repository `tests/openpnp/evidence/` for frozen independent skill evaluations and integration evidence as it is recorded. Sustained-load and maximum-job qualification must be recorded separately before claiming the implementation plan's A11 acceptance criterion.

The machine-readable `tests/openpnp/coverage.json` maps all eighteen requirements to shipped skills, schemas, native adapters, tests, evidence and remaining work. Test source references alone do not count as passing execution evidence.

### Sustained simulator fixture

The default `native-simulator` profile uses the pinned OpenPnP sample and its native strip feeders. Repeated jobs consume their finite simulated supply; start a fresh isolated instance for a fresh test fixture. Do not reset counts to disguise an exhausted run.

The optional `--profile sustained-workload` launcher argument provisions actual native tray feeders with 10,000 virtual pockets per sample part. Pocket pitch is zero because every virtual pocket uses the same rendered sample supply location. It uses OpenPnP's `Unsorted` job order for the explicitly ordered workload grid; default `NozzleTips` route optimization proved too costly when repeatedly applied to 10,000 pending placements. Native planning, feeding, pickup, placement, motion, vision and material counters still execute. This profile is a synthetic load-test fixture, not evidence for physical tray geometry or strip indexing. Simulator and hardware qualification are always reported separately.

The historical inspection75 paired benchmark measured **22.782 seconds through Bridge versus 4.040 seconds directly per 100 accelerated simulator placements**, with 463.90% median paired overhead. The proposed 5% target is unmet. A separate private timing copy attributes 76.59% of its job elapsed time to the required journal force calls. These are short simulator measurements; all durability barriers remain enabled. [Historical analysis and reproduction](../../docs/openpnp-performance-analysis.md#current75-performance-measurement-and-closed-predecessor-run).

The measurements below retain their historical artifacts and recipes.

Three matched macOS ARM64 trials of 100 placements on the initial `NozzleTips` fixture measured a median 219.22% added cycle time through the bridge, with every native step's durable intent and completion retained. The plan's proposed 5% overhead target was not met. These accelerated, zero-dwell simulator results do not predict physical machine throughput or establish performance for the revised `Unsorted` fixture; full methodology and counts are in repository `tests/openpnp/evidence/native-placement-benchmark-2026-09-11/`.

The revised `Unsorted` fixture's three matched pairs measured about 4.10 seconds directly and 18.00 seconds through the bridge per 100 placements: median added cycle time **339.10%**. All 600 measured placements, 120 warmups, native feeds and required step records were verified. This revised measurement also misses the 5% target. See `tests/openpnp/evidence/native-placement-benchmark-unsorted-2026-09-11/` for exact build hashes and methodology.

Native status metrics expose JVM heap, journal size, event/operation counts and artifact retention. Image bytes are stored on disk and only bounded metadata is cached. The event ring has 5,000 entries; a stale cursor requires a new authoritative snapshot. The journal and immutable artifacts retain historical evidence and have explicit admission/storage limits; reaching a limit does not silently discard an irreversible-action record.

Status includes cached `job_progress` with its observation time and event sequence. OpenPnP samples it at native step boundaries, so a long step can make the sample older than the usual 100 ms refresh interval. Normal completion and pause wait for the native task Future and forced journal publication. Wrapper failures retain known body results as part of an unknown outcome; publication faults keep ownership fenced. `native_busy` remains a separate executor observation; wait for it to clear before dependent commands. Future completion does not establish physical standstill.

### Offline diagnostics

After a failure, inspect the preserved journal without connecting to OpenPnP:

```sh
node /absolute/path/plugin/scripts/openpnp.mjs diagnostics \
  --state-dir /absolute/path/openpnp-state \
  --output /absolute/path/new-diagnostics.json
```

The output must be a new file outside the machine state directory. Diagnostics report recorded operation states, missing/corrupt sequences, partial writes and the SHA-256 of the inspected journal prefix. They omit credentials, session grants, free text and design/configuration contents. They neither replay actions nor establish current physical state. Keep the original journal for deeper investigation.

## Licensing

The MCP server, CLI, and skills use Apache-2.0. The Java bridge uses GPL-3.0-or-later and links OpenPnP. The combined package declares both licenses. See `THIRD_PARTY_NOTICES.md` and `licenses/`; corresponding bridge source and reproducible build scripts are in `src/openpnp/java/` and `scripts/openpnp-build-native*` in the source repository.

### Qualification records

Historical package hashes and skill evaluations remain bound to their original artifacts. A later source change requires its own integration evidence. The source repository's implementation status and machine-readable coverage distinguish current results, preserved failures, and outstanding acceptance criteria.
