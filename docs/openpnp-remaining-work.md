# OpenPnP remaining work

All R01–R18 remain partial. The current scope is a reviewable plugin for the pinned native simulator, with targeted configuration-safety and qualification fixes. Physical-machine control remains unsupported and unqualified. [Current status and evidence](openpnp-implementation-status.md).

## Simulator PR readiness

The candidate has completed the local 94-program native campaign, all 17 packaged MCP workflows, installed lifecycle, controller/history, matching-production crash campaign, all nine scripted GUI routes and Node 22/24/26 root masters. Required hosted checks and repository review requirements must still be verified on the submitted PR head before merge. Local qualification and maintainer approval are separate evidence.

The mapped-axis homing postcondition defect described in the original source41 review is repaired in this revision. Focused regressions cover affected mapped-axis, backlash, nozzle-assembly and restore paths, returning native callbacks during apply/restore, and ordinary-control cases. This does not qualify arbitrary machine graphs or physical homing behavior.

## Required before full-plan completion

1. **Distribution and actual desktop use:** qualify versioned update/rollback, broader installation/uninstall paths and genuine desktop operator journeys on the intended Codex/OpenPnP hosts. The current automated uninstall/reinstall preserves a populated simulator state; scripted Swing forms and helper-observed package workflows have separate evidence. Extend platform support only with qualification for the exact artifacts and environment.
2. **Uniform skill evaluation:** run fresh independent tasks for all fourteen shipped skills against the current package. Retain unsuccessful and partial trials. Offline rubric/decision cases, historical informed trials and scripted workflows are not a complete fresh behavioral evaluation.
3. **Recovery breadth:** extend beyond the recorded halt/recovery and replacement boundaries, including native effect/internal retry, written-but-unforced/torn persistence, repeated interruption and resource-capacity cases. Preserve unknown outcomes and require fresh local authority; do not replay old feed/release actions or clear history to obtain readiness.
4. **Configuration and native coverage:** complete the remaining profile-specific work in R01–R18, including broader controller/driver/axis/planner graphs, physical pumps/tool changers, powered feeders and general material reconciliation, broader panel/library/restore/migration support and operator inspection/batch advancement.
5. **Performance and endurance:** measure the final artifact against the plan's paired overhead target and complete the continuous eight-hour/10,000-placement acceptance scenario with exact feeds, resources and retained failure evidence. Previous short benchmarks miss the proposed 5% target; predecessor long runs do not qualify the current candidate.
6. **Physical qualification (deferred):** choose the exact machine, controller, firmware, cameras, feeders and host when hardware exists. Qualify physical limits/interlocks/stop behavior, calibration accuracy, pickup/release discrimination, actual material identity, registration, inspection and production operation. Simulator signals are synthetic.

## Evidence boundaries

The revision preserves historical source41/stage06 and earlier reports under their original hashes and scopes, including failed attempts. Fresh generated artifacts have separate qualification; historical success is not rebound to them. Review or merge of the scoped simulator PR does not declare the full plan complete or enable physical control. No hosted CI pass is inferred from the local campaigns.

The exact per-requirement implemented and remaining scopes, skills, tools, native adapters and acceptance scenarios are in [coverage.json](../tests/openpnp/coverage.json). The full design and twelve acceptance scenarios remain in the [implementation plan](openpnp-codex-plugin-implementation-plan.md).
