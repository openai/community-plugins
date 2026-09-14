# Historical stage05 audit cutoff

This preserved audit describes stage05 before the later Linux91, separate live history and stage06 CLI correction results. Its unresolved statements and table digests apply to that cutoff and original reports. Public report files contain de-identified provenance exports; their original SHA-256 values are recorded inside each export. See [current status](openpnp-implementation-status.md) for the current review scope.

# Stage05 implementation coverage audit

All **R01–R18 remain partial**. This is a proposed documentation/coverage update for private **source41 / package79-staging05**, not a release, installation, promotion or goal-completion decision. It records implemented sensing recovery accurately without importing historical qualification into a new artifact.

## Exact candidate and evidence

- Bridge: `62b17d35cb4d39c531fdd6698dece3f41ad19f92e604e35b3fc9f8e9c8aac907`; patched OpenPnP runtime: `e0a6ab35c3cdbcf355ecfa9c592751efeef69b0d2f124295ef37ea807de56bd5`.
- Runtime manifest: `007f28c38d244186aec519de0d919b474b2dd54fc671883e8207c50ab75619de`; canonical build manifest: `86542eae4f9423b388dabcd47ef224de96035499c22fdc836dd204891d13cb49`.
- MCP server: `75d5f3df16f1177fc07ff57c966c4209b50431b7a1b35965cc6ee9d90989d460`; tool catalog: `ff4b09d08c6f57fbb474d22cd3ce35ca09d0fa2b09cfd0ef6cb49a03a2e3581a`.
- Inventory: **61 tools, 34 typed configuration changes, 14 skills; 63 production Java sources and 176 compiled native-test sources**, Java11 production / Java17 tests. A compiled test source is not necessarily executed.
- Current bounded results: **91 macOS native programs**, **17 headless SDK/MCP suites** (16 shipped simulator launches plus one test-only mapped-axis fixture), and **two complete scripted GUI/MCP replacement-restart journeys**. Each GUI journey performs one fresh placement, with all public workflow calls through the packaged SDK/MCP; each subsequent history-only JVM performs three direct read-only Bridge calls.
- Non-live Node22.19.0 /24.20.0 /26.0.0: each **475 passed, 0 failed, 18 skipped** across 73 test files. Counts across runtimes are repeated checks, not additional unique cases.
- **The real fresh sensing-GUI CLI fails before OpenPnP Main under Java17.** The preparer lacks module opens used by the Main launcher. Programmatic GUI/MCP success does not close this launch failure.
- Linux results are excluded at this audit cutoff. Continuous-eight-hour, current performance-target, fresh 14-skill and physical qualification remain false. Existing package metadata stays assembled-unqualified.

Evidence copies below preserve exact bytes; the compact native program index is explicitly derived from the complete receipt and carries its hash. Full journals/commands/process proofs remain at the original paths recorded by each delivery.

| Evidence | SHA-256 | Scope |
| --- | --- | --- |
| [sensing79-stage05-package-node](../tests/openpnp/evidence/sensing79-stage05/package-node.json) | `7c37a90f1961282533166b2b2597ab472c797ae3a85163963b5050c939d09134` | Private stage05 assembly and 73-file non-live Node22/24/26 suite: each475pass,18live skips,0fail. Exact package/source bindings; no native workflow or full-plan inference. |
| [sensing79-stage05-headless-mcp](../tests/openpnp/evidence/sensing79-stage05/headless-mcp.json) | `f1c1c185610310ef4c2072b84f925fd45331db7d75f5a363d70664e14ee536ed` | Exact stage05 packaged SDK/native simulator17suites:16shipped start-simulator launches and one test-only mapped-axis fixture. Separate from GUI and physical qualification. |
| [sensing79-stage05-gui-restart-mcp](../tests/openpnp/evidence/sensing79-stage05/gui-restart-mcp.json) | `3e651a8a2a2870e3633029d2195e579da51ba269d912807a11a8685fb6f7f7ee` | Exact stage05 Runtime.halt at initial replacement intent and pre-archive boundary, then shipped GUI bootstrap, programmatic local forms, actual packaged MCP restart/continuation/freshvalidation/placement, plus separate third-JVM read-only history. Scripted actor; not shipped CLI launch or fresh skill behavior. |
| [sensing79-stage05-gui-cli-failure](../tests/openpnp/evidence/sensing79-stage05/gui-cli-failure.json) | `70ed194f65b3cba2ebf92a94f392d689511d6b354ed82caed56dd1b3efa96436` | Retained actual stage05 shipped sensing-GUI CLI failure underJava17: preparer lacks required module opens and Main never launches. Evidence of an unresolved package defect, not successful restart qualification. |
| [sensing79-stage05-macos-native91-delivery](../tests/openpnp/evidence/sensing79-stage05/macos-native91-delivery.json) | `9f257e3d3a907df35219b95e2b4b06794a3a677f574b21573c1c45c162316f75` | Exact source41/macOSARM64 native91 programs;790 inputs current,92 recorded process groups absent,zero forced cleanup. Native component coverage, not all176 compiled test sources orGUI/workflow qualification. |
| [sensing79-stage05-macos-native91](../tests/openpnp/evidence/sensing79-stage05/macos-native91.json) | `99e65c9ca88b3dafe0b5220cc0151d7a46c618e8633fcdc3d00426038a7ba1ee` | Derived compact index of exact stage05 macOS91 native programs; original receipt retains complete commands, logs,790input hashes and process records. Presence of other test sources in the build is not evidence they ran. |

## Five highest-impact software gaps

These are engineering and simulator acceptance tasks that can progress without a physical machine. Physical tests remain a separate column below.

### 1. Repair the shipped sensing-GUI launch and prove the complete CLI route

**Observed defect:** `plugins/openpnp/scripts/gui-launcher.mjs:86` launches `GuiSensingFixture` without the module-open options present at line101. The real stage05 CLI receipt records reflective access failure while saving `java.awt.Color`, with Main never launched. `gui-restart-launcher.test.mjs` uses controlled executable/HTTP fixtures; passing it does not prove the actual OpenPnP preparer.

**Close with:** a newly frozen launcher/package; actual install → fresh `start-gui-simulator --profile vacuum-sensing` → native GUI attach → controlled crash → `restart-gui-simulator` → source-absent capability → local observation decision → separate continuation → fresh registration/validation → one native placement → clean detach. Preserve the stage05 failure, bind every launched artifact and prove no inherited source, lease or old placement authority. Existing native entrypoints: `NativeGuiRestartPackagedMcpTest`, `NativeReplacementIntentCrashBridgeTest`; extend the actual CLI campaign, not merely argument assertions. This closes the launch defect, not all A01 installation cases. **R01/R08/R16/R18; A01/A08/A09.**

### 2. Complete the selected reference-profile configuration, calibration and material adapters

Actual capability code advertises a fixed owned-controller **diagnostic** recipe (`Bridge.java:2437`), typed-settings-only restore (`:2452`), and exact same-part finite `ReferenceTrayFeeder` loads (`NativeMaterialLoads.java:64`). The native calibration dispatcher only has runout plus the separate planar-scale route (`Bridge.java:810`); `NativeConfigurationSnapshots.java:84` explicitly omits vision and sensing settings. These are concrete software limits, beyond waiting for hardware. No general serial/firmware configuration, complete configuration restore, lens/camera-to-nozzle recipe, powered-feeder slot/refill workflow or global alias profile is established.

**Close with:** explicitly choose the reference device/profile subset, add the missing closed adapters and round-trip/dependency checks, then run three simulator commissioning/configuration restore repetitions, selected controller protocol fault fixtures, native calibration invalidation and actual feeder index/refill/slot-change tests. Reuse `NativeSettingsTest`, `NativeConfigurationSnapshotsTest`, `NativeControllerDiagnosticTest`, `NativeCameraScaleMeasurementSuiteTest`, `NativeMaterialLoadsWorkflowTest`, and packaged `core`, `camera-scale`, `material` suites; add cases for the new behavior rather than counting existing bounded tests again. Automatic changers/additional kinematics remain advanced extensions unless explicitly selected. **R02–R10/R13; A02–A06/A09.**

### 3. Bind first-article acceptance to batch progression and complete changeover

Inspection receipts explicitly set `production_authority_granted:false` (`NativeLoadedBoardInspection.java:176`; `Bridge.java:2479`). Current job validation checks model/load consistency; it is not an inspection-acceptance gate. A model placement or a caller-supplied measurement cannot silently qualify a production batch. Current recovery successfully runs a fresh replacement job, but this is not A07 accepted-scope batch progression or the full A10 job-A → moved feeder → manual tip change → job-B/X-out → shutdown/restart sequence.

**Close with:** a typed accept/reject disposition tied to exact inspection evidence, job/configuration/load revisions and explicit batch scope; invalidate it on every relevant change. Demonstrate a deliberately rejected first article cannot advance, accepted scope can complete a bounded simulator batch, and the complete changeover cannot reuse registration, pocket, occupancy or pending-placement state. Extend `NativeBoardInspectionBridgeTest`, `NativeBoardLoadBridgeTest`, `NativeJobSteppingTest`, and packaged `material`, `placement`, `stepping`/GUI inspection journeys. Independent metrology authenticity and the five-board/500-placement physical target remain separate. **R12/R14/R15/R17; A07/A10.**

### 4. Extend recovery and persistence coverage beyond the exact replacement seams

S79 has substantial implemented recovery: standalone sensing repair; original faulted-job replacement; repeated continuation that adopts exact completed steps and supersedes unknown candidates; and explicit restart that reconstructs inactive graphs and records observations before a separate continuation. `Bridge.java:2003` completes restart observations without resolving sensing faults. `NativeMaterialLoads.java:231` limits fresh restart bindings to a later independently authorized continuation. The general reconciliation endpoint still only abandons a previous simulator instance (`Bridge.java:430`), and generic per-placement crash recovery remains unsupported (`:2452`). These are compatible scopes, not a reason to label all material restart missing.

**Close with:** an explicit supported-fault matrix across before/after feed, pick, release, journal force, partial write/disk-full, controller/JVM/client loss, every partial replacement phase and repeated restart. Require immutable original outcomes/history, no repeated completed effect, fresh IDs for unknown loads, current ownership and exact final revalidation. Existing actual entrypoints include `NativeReplacementIntentCrashBridgeTest`, `NativeMissingReplacementDocumentBridgeTest`, `NativeRestartExecutionBridgeTest`, `NativeRestartAdmissionCleanupBridgeTest`, `NativeReplacementContinuationMutationTest` and the full GUI/MCP driver. Broader controller operational restart and complete config-save/upgrade/uninstall-with-pending-work remain separate software work; `openpnp.mjs:425` already has a stopped-connection uninstall guard, so uninstall is not wholly unimplemented. **R02/R05/R11/R16/R18; A08/A09.**

**Specific current test gap:** `sensing-reconciliation-history-contracts.test.mjs:102` is the 18th live skip. It requires `OPENPNP_HISTORY_CONNECTION_FILE` and `OPENPNP_HISTORY_EVIDENCE_DIR/expected-history.json`. Its live path is source `createServer` + official SDK `InMemoryTransport` → real Bridge HTTP, not packaged stdio. The 17-suite headless runner does not supply this fixture; the GUI third-JVM direct reads do not execute it. The historical `validation/history79-native02/run.py` supplies the workflow on compile06. Rerun the exact native history fixture on the current artifact and separately record this source-SDK result, or add a packaged stdio history companion. Do not claim all 18 live cases passed.

### 5. Qualify sustained operation and independent skill behavior on the current artifact

Current capability retention is bounded by fail-closed capacity, not proven retention steady-state (`Bridge.java:2442`). The old qualified75 benchmark missed the 5% overhead target; neither that measurement nor END57's 10,000 placements establishes source41 performance or continuous-eight-hour operation. Current skill evidence explicitly reports `uniform_fresh_blind_run:false` and `actual_tool_calls:false`; scripted GUI/MCP automation and structural skill validation do not fill the 14-skill behavioral matrix.

**Close with:** current-artifact equal-workload performance measurements, continuous 8h plus 10,000 placements at advertised limits, slow consumers, camera loops, bounded heap/native memory/storage, actual reasoning delays/cancellation/reconnect and retained failure analysis. Then use fresh independent actors for all 14 skills' happy/failure/recovery tasks with real tool calls, missing capabilities, hostile BOM/log text and ambiguous operator observations. Highest-value first task: actor discovers source-absent restart, requests a local observation decision, recognizes that it grants no readiness, requests separate continuation, then registers/validates/runs once. Only the operator harness supplies local GUI decisions. Reuse the generic packaged MCP driver and existing 17 native suite entrypoints; hide the intended call sequence and grade exact receipts/old history. **R01/R05/R15/R17/R18; A11/A12.**

## Remaining scope by requirement

Every row stays partial. Hardware absence is not used to mask software work.

| Requirement | Remaining software / simulator acceptance | Hardware qualification |
| --- | --- | --- |
| R01 — Installation and discovery | Fix Java17 sensing-GUI preparer; qualify actual fresh CLI→saved-session restart,clean host and desktop lifecycle. | Real device enumeration and selected physical host. |
| R02 — Existing-machine adoption | Complete configuration/resource restore and selected migration; current typed snapshots omit settings and portable adoption is bounded/fresh-instance-only. | Manufacturer baseline and already calibrated physical machine adoption. |
| R03 — Drivers and communications | Add qualified typed controller endpoint/serial/firmware configuration and reset/recovery; current owned loopback recipe is diagnostic-only. | Controller completion/reset and measured hold/stop behavior. |
| R04 — Motion | Broaden required motion/kinematics and map-limit transactions within an explicitly selected profile; bounded direct/mapped linear subset exists. | Direction/scale/backlash and workspace/clearance measurement. |
| R05 — Machine control | Qualify complete software shutdown/changeover and operator GUI workflow; preserve current cooperative ownership semantics. | Physical interlocks,supervision loss and stop/hold at maximum supported speed. |
| R06 — Cameras | Add selected camera class/exposure/lens/transform adapters and prove native image-memory behavior; current recipe is planar ImageCamera/fixed bounded settings. | Exposure/focus/lens/image quality and optical calibration. |
| R07 — Calibration | Add selected lens/camera-to-nozzle/working-height routines and complete invalidation coverage; only runout and bounded planar-scale recipes exist. | Independent residual metrology across nozzles,rotations,workspace and heights. |
| R08 — Tooling and pneumatics | Fix normal GUI sensing launch and extend selected pump/sensing graph/profile support; local sensing recovery itself is implemented. | Installed tip/pneumatics/pressure baselines,missed-pick and retained-part sensor accuracy. |
| R09 — Feeders and material | Add selected powered/slot-aware feeder and partial-refill/changed-geometry workflows; ordinary general restart authority remains unsupported. | Actual stock/lot/slot/refill presence and physical feed/pick effects. |
| R10 — Component library | Add required aliases/custom component-profile coverage; import-local bindings do not establish global equivalence. | Physical part dimensions,polarity and substitution/electrical equivalence. |
| R11 — Job import, edit and export | Complete required import/native-document corpus and panel authoring/migration; scoped inert replacement reconstruction already exists. | Imported design intent validated on the selected physical process. |
| R12 — Boards and panels | Bind accepted board/load scope to batch advancement and complete changeover; current registration/replacement histories are scoped. | Loaded board/fixture identity,height,registration accuracy and changeover. |
| R13 — Vision and alignment | Add selected pipeline/stage adapters and positive/negative package/image corpus coverage. | Optical alignment/size performance on representative packages and negative examples. |
| R14 — Validation and quality | Implement/qualify inspection accept/reject disposition that gates exact subsequent batch scope; current receipts explicitly grant no production authority. | Authenticated independent inspection with uncertainty; five inspected boards/500placements per plan. |
| R15 — Production | Complete inspection-gated batch/load progression and A10 changeover; run current A11 workload/resource/latency acceptance. | Physical batch presence and accurate material/placement quality denominators. |
| R16 — Recovery | Broaden full packaged crash-phase matrix and operational controller recovery; current supported replacement restart does not resume arbitrary old jobs. | Operator resolution of real uncertain physical effects without inferred presence. |
| R17 — Maintenance and optimization | Execute startup/changeover/shutdown/drift workflows and equal-workload quality-aware optimization; measure current performance. | Cleaning/leak/wear/drift and throughput-quality measurement. |
| R18 — Distribution and support | Close actual CLI defect,platform/install/upgrade/uninstall cases,evidence retention steady-state and fresh independent14-skill behavioral matrix. | Physical support-profile qualification; no machine presently available. |

## Scope corrections and integration

- Preserve every historical evidence object and hash. Move the former S78 `current_candidate` value intact to `historical_sensing78_current_candidate`; retain old changed-row wording under `historical_sensing78_requirement_scopes`.
- R08's “explicit sensing reconciliation / GUI sensing operation missing” and R09/R16's blanket “no material restart” descriptions are stale for S79. Replace them with the exact local replacement/restart scope and its independent CLI failure.
- R18's panel74 inventory (55 tools/33 changes) is historical; current source41 has 61/34. R02's count is likewise 34.
- Add request/read tools, current native adapters, CLI/contract tests and exact stage05 evidence links. Test-path mappings are reproducibility pointers, not claims every mapped source ran in the91-main campaign.
- Apply this proposal only after verifying the coverage preimage and supporting source41 files. A corrected later package needs its own artifact hashes and evidence; do not rename stage05 failure as a later pass. No source, package metadata, old evidence or plan text is modified by this proposal.
