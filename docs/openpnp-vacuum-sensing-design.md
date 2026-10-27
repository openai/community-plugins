# Native vacuum sensing — implementation design

## Scope and delivery state

This advances R08 in the original implementation plan. The current sensing78 implementation targets pinned OpenPnP `5bd404cfc70f34103a3ca0fbb6b50c2b465f407c`. It is locally integrated and qualified for the bounded simulator profile; it is not a physical qualification or remote publication. The user selected simulator-first work and has no physical machine available.

The workflow must configure native sensing, collect fresh readings, execute native part checks, retain their outcomes, and exercise native placement/retry decisions. A changed threshold, a cached actuator command, a native model Part, and a verified sensor result are different facts.

## Components

| Component | Responsibility |
| --- | --- |
| Native `VacuumSensing` patch | Observe actual native reads, checks and part-off valve pulses; reject malformed/nonfinite reads; retain failures that must bypass processor retries. |
| Controlled `NullDriver` source | Explicit process-local synthetic readings, exact actuator identities and immutable provenance. Installation never serializes sample callbacks or authority. |
| `NativeVacuumSettings` | Stage one complete typed model change, retain original object identities, reject stale plans, apply once on the native executor, verify setter readback. |
| `NativeVacuumSensing` adapter | Bound sample counts, enforce current source/settings identities, execute actual native checks, declare active part-off effects. |
| `NativeVacuumJournal` | Validate intent/outcome order before append, commit after force, preserve historical facts and sticky uncertain/retained states. |
| Bridge integration | Own sessions, revisions, retained asynchronous operations, null-preserving events, configuration invalidation, preflight, retry and cleanup fences. Implemented in sensing78 with bounded native, packaged MCP, GUI regression and Node qualification. |
| MCP and tooling skills | Closed measurement/check schemas, source-gated dispatch, strict operation receipts, bounded progress, and procedures for settings, observations and recovery. |

## Typed configuration

One retained `set_vacuum_sensing_settings` change selects the nozzle, tip, existing sensor actuator and existing valve. It includes explicit native actuator units, configured-threshold provenance, `None` or `Absolute` methods, part-on/off ranges, five native check-stage flags, and bounded part-off probe/dwell times.

Thresholds are limited to ±1,000,000 native-actuator units, and each probe/tooling dwell to 0–1,000 ms. Absolute ranges require finite `low < high`. On/off ranges must be disjoint, including their endpoints. Disabled `None` settings can represent stock zero-width ranges and a null sensor binding. Difference sensing, graph establishment, hardware pressure calibration and changer sequencing remain later work.

Stage is passive. Apply requires the native executor and a disabled, idle model with empty native nozzles. All relevant shared-tip consumers and identities remain bound. Unexpected setter or readback failure consumes the plan and requires the configuration fault fence; automatic rollback must not erase uncertain state.

The sensing78 Bridge gives sensing configuration its own effect path. It invalidates sensing readiness and job validation before setters while preserving occupancy history and source provenance. Mixed sensing and other configuration changes are refused. The operation adapter rechecks currentness around native observer callbacks; mandatory valve-off cleanup remains permitted after failure.

## Observation and operation contracts

Native events are `read`, `check` and `valve` with `before`, `returned` or `failed` phases. Each observation carries its own UUID, parent observation, actual nozzle/tip/sensor, source generation and native stage. Check verdicts remain native booleans. The five job stages are after pick, alignment, before place, before pick and after place; standalone operations use `direct`.

`openpnp_measure_sensor` collects 1–32 immediate native readings without valve actuation in the sensing78 candidate. The count is bounded; arbitrary future device I/O requires its own deadline qualification. Measurements alone grant no occupancy or physical qualification.

The initial operation profile requires a single exact NullDriver, exact NullMotionPlanner and ReferenceHead, same-head sensor and Boolean valve, and no configured head pump. Native jobs require the exact SimplePnpJobPlanner with its retained identity. Only exact controller or virtual axes are admitted; selected nozzle Z must be a direct ReferenceControllerAxis on that driver with both finite Safe Z limits enabled. Mapped axes and the advanced planner are excluded. Limits are 8 heads, 32 nozzles, 16 axes, 128 tips and 64 actuators. Even passive measurement requires this graph, an installed compatible exact tip, a bound valve and an active controlled source; disabled None/null-sensor settings do not make measurement available. Stock native vacuum requests can also drive the head pump; broader pump behavior needs its own declared effects and qualification.

Automatic homing and effectful or null actuator lifecycle policies are outside this profile. The explicit vacuum simulator fixture selects AssumeUnknown/LeaveAsIs/LeaveAsIs policies; the default and sustained fixtures retain their stock policies. Capability readback lists these policies and states that lifecycle assumptions are not sensor evidence. Safe disable preserves retained faults. An unsafe disable policy causes a durable global uncertainty record, including when no nozzle row exists; later dependent actions remain fenced.

`openpnp_verify_part_state` calls native `isPartOn()` or `isPartOff()` in the candidate. Part-off verification pulses vacuum and requires a homed, enabled nozzle at Safe Z with no model-held part. The receipt marks effect kinds with `native_effects_ordered:false`; observed events supply their order. It states the actual effects, without assuming the read occurs before valve closure when positive dwell moves it afterward. Safe-Z admission rejects missing mappings and disabled/nonfinite bounds, and is repeated at dispatch. Local cleanup still attempts valve-off after an observer or journal failure; failure to durably record cleanup leaves the outcome unknown.

The simulator source declares fixture, scenario and units. Native code adds its process and source generation. It requires the actual machine executor and rejects revoked or changed bindings. Its readings are synthetic test inputs, not a model of pneumatic physics or independent physical inspection. Source revocation must never silently return the actuator to random readings.

## Journal and occupancy

Observation envelopes bind operation/request/machine/instance/configuration identities. Job observations also bind job revision, board load, material setup and lineage. Parent/child order and immutable identities are checked before writing. State becomes visible only after the append and force succeed. The two new sensing tools require canonical UUID request IDs; existing job and machine commands retain their string request-ID contract. Their sensing records preserve the exact admitted string, including whitespace and Unicode, within the existing Bridge bound. Operation, machine and instance IDs remain UUIDs. A request string never substitutes for an operation ID.

Mandatory nullable fields survive persistence through a dedicated null-preserving vacuum event encoding. Historical event formats remain intact. Ownership and source identity are rechecked after forced record publication before the next native effect.

Known finite `part_on=false` is `not_detected`; the configured native retry path may run, with every material advancement retained. It does not assert that a nozzle is empty. `part_off=false` is retained-part evidence and fences later actions. Failed or incomplete reads, checks, valve operations or journal publication remain unknown and must bypass ordinary native retry handlers.

An earlier empty observation cannot authorize later work after a pick/release or source/settings change. Bridge admission must combine native model state, current observation context and sticky journal state. Configuration changes, restart, a null native Part, or operation abandonment do not clear a retained/unknown fence. Explicit reconciliation is separate pending work.

## Persistence and adoption

Native XML can preserve threshold settings and actuator names but cannot preserve process-local source callbacks. Private sensing78 preflight attests every participating sensing source before native job initialization and feed, including after reload or adoption. The portable adapter rejects enabled sensing during export and archive activation with `SENSING_TRANSFER_UNSUPPORTED`, including unused/shared tips and legacy settings that would auto-enable. A versioned settings-only transfer with fresh source binding remains future work. Unsupported sensing settings are not silently removed.

Typed snapshots explicitly disclose sensing omissions and do not restore source or occupancy authority. Complete sensing settings restore remains future work requiring original identity checks, shared-tip ordering, invalidation and fresh source readiness.

## Current integration evidence

The selected build78-03 passes 90 native programs on macOS/Linux ARM64, all 17 packaged MCP workflows, seven default-fixture GUI regressions and 400 unique non-live tests per Node 22/24/26. A separate informed tooling/recovery skill trial passes. The [qualification summary](../tests/openpnp/evidence/sensing78-qualification/summary.json) binds exact artifacts, source inputs, retained failures and test scopes. The earlier build78-02 MCP result, wrapper regression and request-ID failures remain historical evidence; they do not substitute for selected-build qualification. GUI vacuum sensing, uniform fresh skill behavior, physical pressure, current performance and continuous endurance remain unqualified.

## Qualification gates

These are required coverage, not a completed checklist. Selected component passes do not imply every case in gates 1–5 has passed.

1. Compile complete patched native class families and all Bridge/test sources from pinned clean OpenPnP input.
2. Native foundation: finite and malformed readings; exact source/actuator ownership; five stages; observer errors; active probe cleanup; revocation and save/reload authority loss.
3. Typed settings: late-invalid changes, exact object substitutions, stale values, shared consumers, disabled state, single-use apply, save/readback and fresh-JVM reload.
4. Actual native jobs: successful placement; one bounded missed-pick retry; exhausted retries; lost part; retained part after model release; exact material counts and native placed status.
5. Real forced journal: before/after-effect write failure, ordered replay, pending observations, cleanup uncertainty, malformed histories, no automatic replay or retry after an uncertain effect.
6. Bridge/MCP: source-gated discovery, closed schemas, session/revision conflicts, duplicate requests, queued expiry, takeover, cancellation, event null preservation, preflight and persistent occupancy fencing.
7. Packaging and regression: exact artifacts/provenance, source distribution, existing native/Node/MCP/GUI suites, platform qualification, updated tooling skill contracts and fresh workflow evaluation.

Passing component tests does not satisfy gates 6–7. The original R01–R18 remain partial; current sensing work neither satisfies the unmet performance target nor replaces continuous-endurance or physical qualification.
