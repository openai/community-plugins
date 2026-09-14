# OpenPnP performance analysis and follow-up

## Current75 performance measurement and closed predecessor run

The exact qualified75 Bridge (`970e4635`) and native runtime (`810773c1`) passed three fresh, alternating direct/Bridge pairs of 100 measured placements after 20 warmups per JVM. Median durations were **4.040 s direct and 22.782 s through Bridge per 100 placements**. Median paired added time was **463.90%**, or **187.42 ms per placement**; all three pairs missed the proposed 5% target. This accelerated simulator result does not predict physical cycle time.

A separate, private instrumented copy ran the same three pairs. Across its measured Bridge jobs, 15,333 `force(true)` calls took 54.409 seconds: **76.59% of instrumented job elapsed time** and **98.27% of measured journal elapsed time**. Mean time inside a force call was 3.549 ms. These are elapsed storage-barrier observations, not CPU attribution. Native-next includes action journaling, so its total must not be added to journal time. Sequential campaigns leave profiler effects confounded with host drift.

Each campaign completed 600 measured and 120 warmup placements, counting direct and Bridge trials separately. For every Bridge trial, the independent raw-journal audit verifies contiguous step and ledger identities, one starting/before-assembly/complete checkpoint per placement, and exactly one returned feed, pick, align and release per completed placement. All twelve successful campaign JVMs closed naturally. The first baseline attempt retained a stale revision after homing and was refused before Bridge placement; it remains excluded and preserved. The timing overlay is private and leaves every production write/force/effect boundary intact.

The measured priority is journal/storage architecture. Preserve the existing qualified package while evaluating changes against its required intent-before-effect and receipt-after-force guarantees. Small event-copy or status-payload improvements may help allocation but cannot remove the measured force cost. Do not skip barriers, slow the direct fixture, change job order, or substitute a different workload to claim the 5% target.

The separate END57 predecessor run is closed. Its retained disposition reports 10,000 placements, 40,000 returned feed/pick/align/release actions and no pending action. Sampled native runtime spans 7h 14m 25.430s; the largest observation gap is 920.931s, so continuous eight hours remains unqualified. Sampled heap peaked at 1.774 GiB under its 2 GiB cap; final journal size was 310,886,431 bytes under its 512 MiB cap. Recorded status p95 is at most 25 ms, a histogram bound. These observations neither establish retention steady state/leak freedom nor qualify the current75 artifact.

[Public measurement summary](../tests/openpnp/evidence/performance76/summary.json). The full independent journal audit and closed predecessor review are retained privately; they are not bundled repository files.

The reusable packaged benchmark compiles only its test harness against an already-built, hash-checked runtime. It preserves the legacy source-compiling benchmark as a separate historical tool. The final runner adds checked cancellation cleanup and valid-campaign gating; these changes are separately tested and are not retrospectively attributed to the held campaign runner.

```sh
python3 scripts/openpnp-benchmark-packaged.py \
  --build /absolute/path/to/native-build/build \
  --java-home /absolute/path/to/jdk17 \
  --output /absolute/path/to/new-private-benchmark
```

`--build` is the directory containing `build-manifest.json`; direct invocation of the low-level builder may use its output directory without the extra `/build`. The default hashes select the measured75 artifacts. Each trial gets a fresh temporary configuration, isolated preferences and finite simulator supply. This command measures 100 placements per trial, not a 10,000-placement job or eight-hour run.

## Historical6f9 analysis — preserved original scope

The following analysis and its references belong to the earlier6f9/c85 campaigns. Statements about the then-current package and then-active qualification retain that historical scope; the current measurements above supersede them.

## Decision and scope

**The proposed 5% added-cycle-time target is not met.** The current simulator implementation preserves its journal and recovery guarantees; the measured limitation must remain visible. Changing the fixture's native ordering addressed a separately demonstrated large-job planning cost. It did not satisfy the overhead target.

This analysis uses the two completed paired benchmarks, the preserved diagnosis of the interrupted original 10,000-placement attempt, and the corresponding source. It makes no claim about the result of the separate eight-hour qualification. No active JVM was profiled, workload executed, or production source/runtime changed to prepare this document.

- Current packaged bridge: `6f9efe43114463e0e0c8dea1de6000987b05794d4a556ae57d8ee9b56abf8045`.
- Analyzed `Bridge.java`: `04130abd16338fa660da2bbea0c9ea049a0b2955f864b3abf63f15ef125e0280`.
- Pinned OpenPnP: `5bd404cfc70f34103a3ca0fbb6b50c2b465f407c`.
- Historical bridge: `c85b1c12e27a4df842f59cd56742d2bc2f2819286282a678ed84fe0ae5e6f24c`.

The requirement is in the [implementation plan's operational targets](openpnp-codex-plugin-implementation-plan.md#operational-performance-and-resource-targets). Hardware cycle time, stopping distance and independent inspection remain outside these simulator measurements.

## What was measured

Each campaign ran three predeclared alternating pairs in fresh JVMs and configurations. Every trial used 20 warmup placements followed by 100 measured placements. Each campaign completed 600 measured and 120 warmup native placements with matching finite feeder consumption. The measured job executed 2,004 native processor steps. Each Bridge trial retained 2,004 intent records and 2,004 completion records, with production journal force calls enabled.

| Campaign and native job order | Median direct duration, 100 placements | Median Bridge duration, 100 placements | Median paired added time | Observed paired range | 5% target |
| --- | ---: | ---: | ---: | ---: | --- |
| Historical c85, `NozzleTips` | 6.201 s | 19.767 s | 219.22% | 218.53–220.01% | Failed |
| Current 6f9, `Unsorted` | 4.102 s | 17.996 s | 339.10% | 338.68–341.92% | Failed |

Percentages are calculated within each pair as `(Bridge − direct) / direct × 100`; they are not ratios of pooled medians. Exact values, all trials, class/runtime hashes and retained exclusions are in the [historical report](../tests/openpnp/evidence/native-placement-benchmark-2026-09-11/summary.json) and [current report](../tests/openpnp/evidence/native-placement-benchmark-unsorted-2026-09-11/summary.json).

For the current recipe, the median paired difference is approximately **139 ms per placement**. Five percent of its median direct duration allows approximately **205 ms total added time per 100 placements**. This arithmetic describes the size of the gap; it does not assign the gap to a subsystem. The current Bridge's arithmetic rate of about 20,005 placements/hour converts a short run's duration and is not a sustained hourly observation.

Separate settled-camera samples included native capture and PNG encoding on both paths, with normal artifact storage and operation receipts added by Bridge:

| Campaign | Direct median | Bridge median | Samples per mode |
| --- | ---: | ---: | ---: |
| Historical c85 | 6.37 ms | 18.09 ms | 60 |
| Current 6f9 | 6.89 ms | 20.08 ms | 60 |

Samples within one JVM are correlated. Neither the camera distributions nor three placement pairs establish population confidence, an HTTP/MCP latency distribution, or physical throughput. Other host load was not controlled. The [benchmark harness](../tests/openpnp/native/NativePlacementBenchmark.java) times direct native executor calls against in-process `Bridge.call`, with 2 ms Bridge operation polling. Import and the separate model preflight finish before timing. Native processor initialization/preflight, execution, standstill and end-of-job result bookkeeping remain inside the measured interval. HTTP/MCP transport is excluded.

The historical campaign's sandboxed incomplete pair and subsequent optional-field export correction are preserved in its report; neither was silently substituted into the primary comparison. All 21 production classes match the privately compiled classes used by the current benchmark. A separate historical cross-platform comparison, retained privately, recorded identical production class bytes for Linux/macOS builds. That comparison does not establish identical platform performance or qualify the current candidate.

## Recipe differences and the large-job finding

Both paired campaigns use the accelerated native simulator: high configured simulated axis rates, zero configured dwell/settle times, machine speed 1, a real native processor, and finite 10,000-position native trays with zero physical pitch. These trays are explicitly virtual supply. They do not qualify default strip feeding, tray geometry on hardware, material pickup reliability or physical cycle times.

The current sustained profile selects the public native `Unsorted` job-order hint for an already ordered grid. Its capabilities report the actual order and mutations reject sustained-profile order drift. The default `native-simulator` profile retains upstream `NozzleTips`. The current benchmark asserts the actual order before and after its jobs. Therefore, 219% and 339% compare different native baselines: the larger percentage in the second campaign is not evidence that the same fixed workload became slower. Bridge's observed total duration decreased while its direct baseline decreased more. That cross-campaign observation is not a controlled attribution of the decrease to ordering alone.

The original c85 10,000-placement attempt has stronger evidence of a particular bottleneck: a captured native task stack was in `TravellingSalesman.swapLocations → simulateAnnealing → solve → Plan.optimizePlaceLocations`, with repeated planning/motion intervals and approximately 118.8 CPU seconds over 122.9 elapsed thread seconds. Twelve additional placements took 151.1 seconds. This demonstrates recurring native planning cost in that attempt, beyond startup overhead. It does not provide a CPU percentage for every interval or a firm completion forecast. The attempt was cooperatively paused at 13 placements/13 feeds and preserved as incomplete. The full diagnosis and private-dump digest are retained privately and are not bundled repository files.

Pinned source explains the observation: [each Plan considers pending placements](https://github.com/openpnp/openpnp/blob/5bd404cfc70f34103a3ca0fbb6b50c2b465f407c/src/main/java/org/openpnp/machine/reference/ReferencePnpJobProcessor.java#L619-L645), the ordered feeder group invokes the [traveling-salesman solver](https://github.com/openpnp/openpnp/blob/5bd404cfc70f34103a3ca0fbb6b50c2b465f407c/src/main/java/org/openpnp/machine/reference/ReferencePnpJobProcessor.java#L943-L970), and [FinishCycle creates another Plan](https://github.com/openpnp/openpnp/blob/5bd404cfc70f34103a3ca0fbb6b50c2b465f407c/src/main/java/org/openpnp/machine/reference/ReferencePnpJobProcessor.java#L1922-L1926). The public [Unsorted branch preserves pending order](https://github.com/openpnp/openpnp/blob/5bd404cfc70f34103a3ca0fbb6b50c2b465f407c/src/main/java/org/openpnp/machine/reference/ReferencePnpJobProcessor.java#L752-L761); native planning, vision and placement execution still occur.

The 100-placement benchmark is one horizontal row. The full soak uses a 100×100 grid with interior-centered coordinates, slow event consumption, HTTP/MCP interaction and subsequent ambient camera/motion work. The small benchmark cannot establish 10,000-placement scaling or eight-hour stability. Preserve both recipes in future comparisons; do not add artificial delay to the fast baseline merely to reduce its overhead percentage.

## Demonstrated mechanisms versus unmeasured attribution

The following are observations of [Bridge source](../src/openpnp/java/org/openpnp/codex/Bridge.java), not measured time allocations:

| Mechanism | Established fact | Still unmeasured |
| --- | --- | --- |
| Durable step boundary | `submitJob` persists an intent before every `next()` and completion afterward. `event` writes the full record and calls `FileChannel.force(true)` before publishing its sequence/event. | Flush, write, filesystem and storage-device latency distributions; their share of the 139 ms gap. Dividing total added time by 4,008 records would not measure flush latency. |
| Event serialization | Each event normalizes DTO collections, serializes JSON, creates UTF-8 bytes, then parses a detached copy for the event buffer. | Allocation/GC cost and whether it materially affects cycle time. The detached copy also prevents later mutations from changing historical payloads. |
| Event retention | A bounded `ArrayList` removes its first entry once it exceeds 5,000 events. | Sustained cost after overflow. The 100-placement benchmark does not represent a long period of operating beyond that threshold. |
| Synchronization | Dispatch and several journal/snapshot functions use the same Bridge monitor; synchronous journal work holds it. | Native-task versus reader/watchdog lock wait, and the effect of 2 ms polling or slower MCP consumers. |
| Counts and snapshots | Counts rescan only when a native Job property event marks them dirty. Full configuration/settings snapshots are sampled no more frequently than every 100 ms between steps, with terminal refresh. | Residual cost at 10,000 placements and large supported model sizes. A long native step can make a truthful snapshot older than 100 ms. |
| Native work | Processor, cameras, vision, feeder and motion code remain real native implementations. | A stage-by-stage comparison proving which native stages differ in elapsed time under the two execution paths. |

The current native 100-placement regression reports 101 count recomputations across 2,004 steps. This proves the cache avoids rescanning after every step; it does not establish negligible large-job cost. The pinned [Job placed event](https://github.com/openpnp/openpnp/blob/5bd404cfc70f34103a3ca0fbb6b50c2b465f407c/src/main/java/org/openpnp/model/Job.java#L292-L332) exposes the mutable placed-status map rather than a complete immutable old/new placement delta. Incremental counts therefore require careful event semantics, not an assumed one-event/one-new-placement increment.

## Follow-up backlog — separate future candidate

Run this work only after the active qualification ends, in fresh isolated fixtures. Retain the current artifact and reports as the comparison baseline. Do not profile the active qualification JVM.

1. **Measure exclusive and waiting time before optimizing.** Add bounded, monotonic timing histograms around executor queueing, native `next`, JSON preparation, journal write, `force(true)`, event-copy work, snapshot building, monitor wait and terminal standstill. Count bytes, records and native steps. Keep measurements in bounded diagnostic counters; avoid recursively journaling a timing event for every journal event. Separate CPU from elapsed time. If a future JFR/allocation/lock recording is used, record its configuration and overhead against an otherwise identical unprofiled trial.
2. **Exercise the same workload at several sizes.** Extend the currently bounded benchmark harness explicitly for 1,000 and 10,000 placements using fresh finite inventory, matching direct/Bridge order, coordinates and calibration. Record time to first placement, interval throughput, native step count, queue/flush quantiles, GC, RSS and output size. Include event-buffer steady operation and the largest advertised panel graph. Report each size separately; use an idle host window and predeclared trial order.
3. **Reduce event overhead without changing durable facts.** Candidate changes: a sequence-indexed ring/deque instead of shifting an array after overflow; a typed encoder that writes each record once; and an immutable event snapshot that avoids serialize-then-parse while preserving exact historical values and supported DTO types. Keep the existing sequence, timestamp, request identity, record framing, full-record write loop, force barriers and post-force publication order. Test restart compatibility with retained old journals and immutable payloads before measuring improvement.
4. **Separate infrequently changing settings from live state.** Reuse immutable configuration DTOs only while their configuration revision and all relevant native invalidations remain valid. Continue refreshing dynamic feed counts, installed tooling, calibration changes, position and job progress on the native executor. Preserve observation timestamps/sequence and terminal refresh. Do not infer freshness from delivery time. Measure this proposal on large models; the small default configuration may offer little gain.
5. **Measure read contention before changing locks or polling.** Compare declared polling rates and slow consumers on identical workloads, preserving the 2 ms historical benchmark as one named workload. An immutable read snapshot may shorten monitor occupancy; a dedicated journal owner is a larger architectural change. Neither may publish unforced events, admit two mutations, bypass ownership/revision checks, or turn a lost reply into a resubmission. Report control acknowledgement and lease/pause boundary latency as well as throughput.
6. **Investigate storage cost without weakening barriers.** Compare ordinary persistent storage implementations on the same declared durability contract, recording filesystem/device and flush timings. Do not use a memory filesystem, skip force calls, defer required completion durability across later native actions, or remove critical records to manufacture a qualifying number. More invasive transaction/group-commit designs require a separate ordering/crash specification and proof that every required intent is durable before its effect and every claimed completion is durable before publication. They are not presumed compatible optimizations.

A count-cache redesign should be attempted only if size-scaling measurements justify it. It must reconcile actual native placed history, X-outs, per-instance placement identity, reset/reload and failed/retried operations; terminal and restart checks must compare its result with the native model. No counter increment may stand in for a native placement result.

## Requalification criteria

For any changed candidate:

- Freeze and hash source, packaged JAR, native dependency resolution, JVM, simulator recipe, job input and benchmark/harness before execution. Declare host conditions, storage, sampling and exclusions. Match the actual native order and finite inventory in both modes. Preserve all earlier failures.
- Run relevant native regression tests plus full MCP workflow and reconnect/lost-reply tests. Durability changes additionally require process interruption at intent, post-native/pre-completion and post-completion/pre-reply boundaries; torn/truncated journal handling; I/O/flush/capacity failures; repeated restart; and unchanged-prefix/monotonic-sequence checks. These process-level tests do not qualify host power-loss storage behavior.
- Require exactly matching native placement/feed counts, no duplicate effects from reused request IDs, no automatic replay of uncertain work, and an `outcome_unknown` fence when completion or stillstand cannot be established. Continue enforcing queued-work ownership/revision checks and partial-configuration failure fences. Pause/abort completion must retain its standstill contract.
- Report every predeclared paired trial. To claim the existing 5% target on a declared recipe, every observed pair must meet it; publish absolute added time as well as percentages and characterize variation with enough repetitions for the intended claim. Three successful pairs would still be descriptive host/fixture evidence, not a universal bound. If the target still fails, retain the failure and supported-limit statement.
- Separately measure the plan's one-second 95th-percentile cached-status and control-acknowledgement targets under supported load. No placement or capture average substitutes for those distributions; acknowledgement does not establish physical stopping.
- Re-run a fresh 10,000-placement job and eight-hour continuous simulator workload with slow consumers, bounded heap/native memory/storage, truthful sample age, event resynchronization, unchanged runtime provenance and retained finite material consumption. Validate the largest advertised graph separately where its job shape differs. A prior soak cannot qualify altered native code or a changed fixture.
- Publish profiler-on/off effects, distributions, resource trends, raw denominators, hashes and any unsupported behavior. Profiling, improved throughput, completed native jobs and an eight-hour run each answer different questions; none supplies physical-machine qualification.

No optimization is approved or implemented by this analysis. The next actionable step is the isolated instrumentation and scaling experiment, followed by a measured choice of change.

## Private historical evidence identities

These digests identify retained source reports; they are not downloadable artifacts or new qualification results. The public summaries above retain the measurement scope and limitations.

| Retained report | Original SHA-256 |
| --- | --- |
| `performance76/journal-audit.json` | `f1fdfe1c7a4f64928e4df62e86bdaee48b1bab2e99e5ec55598f4bb2eecc4b4d` |
| `performance76/endurance57-closed-review.json` | `44bbdc920616cfa7e9b2f0079c0cf3f403ee4339ec74f10d201f7a812c1dd3af` |
| `native-class-equivalence.json` | `f6b09dbe1dae608ef8f50621afc6cf2bf1c7bba7e6f47748578cabe6f3a7ed81` |
| `soak-eight-hour-c85b1c12/planner-diagnosis.json` | `21697bf8b3404d155c9fd17399cd6b8484815040983c6ae8359f74ebba9623a6` |
