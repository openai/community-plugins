# OpenPnP remaining work

All R01–R18 are partial. The current implementation is a review candidate for the pinned native simulator, not a complete pick-and-place-machine integration. [Current status and evidence](openpnp-implementation-status.md).

## Known source41 limitation

Source41 has a reproduced mapped-axis homing postcondition defect: a native input-axis setter listener can mark the machine homed during configuration apply, and the singleton apply path can report success. A separate S80 repair is under test and is not included here. Treat this path as unqualified; require independent current-state verification before dependent operation.

## Required before full-plan completion

1. **Distribution and actual desktop use:** complete installation/update/uninstall and genuine desktop operator journeys on the intended Codex/OpenPnP hosts. Scripted Swing forms and helper-observed package workflows have separate evidence. Run hosted CI after a PR exists; no local result substitutes for it.
2. **Uniform skill evaluation:** run fresh independent tasks for all fourteen shipped skills against the current package. Retain unsuccessful and partial trials. Historical informed tooling/recovery trials and scripted workflows are not an all-skill score.
3. **Recovery breadth:** extend the tested crash windows beyond the two completed replacement paths, including native effect/internal retry, written-but-unforced/torn persistence, repeated interruption and resource-capacity cases. Preserve unknown outcomes and require fresh local authority; do not replay old feed/release actions or clear history to obtain readiness.
4. **Configuration and native coverage:** complete the remaining profile-specific work in R01–R18, including broader controller/driver/axis/planner graphs, physical pumps/tool changers, powered feeders and general material reconciliation, broader panel/library/restore/migration support and operator inspection/batch advancement.
5. **Performance and endurance:** measure the final artifact against the plan's paired overhead target and complete the continuous eight-hour/10,000-placement acceptance scenario with exact feeds, resources and retained failure evidence. Previous short benchmarks miss the proposed 5% target; predecessor long runs do not qualify source41.
6. **Physical qualification (deferred):** choose the exact machine, controller, firmware, cameras, feeders and host when hardware exists. Qualify physical limits/interlocks/stop behavior, calibration accuracy, pickup/release discrimination, actual material identity, registration, inspection and production operation. Simulator signals are synthetic.

## Review preparation

The public review preserves the tested source41 Bridge/MCP bytes and stage06 CLI correction. It adds portable evidence, current documentation, marketplace integration and bounded CI/test portability fixes. It does not promote a release, claim hosted checks passed, or erase the earlier partial/failing evidence.

The exact per-requirement implemented and remaining scopes, skills, tools, native adapters and acceptance scenarios are in [coverage.json](../tests/openpnp/coverage.json). The full design and twelve acceptance scenarios remain in the [implementation plan](openpnp-codex-plugin-implementation-plan.md).
