# OpenPnP implementation status

## Current review candidate

**Not complete against the full implementation plan. All R01–R18 remain partial.** The current review contains 61 typed MCP tools, 34 configuration changes and 14 skills. It uses the source41 native build and stage06 packaging; it has no qualified physical-machine profile.

The implemented simulator workflows include guarded configuration, camera/motion/calibration operations, job import and editing, native execution, material/board identity tracking, loaded-board inspection, sensing faults and explicitly authorized local recovery. Restart replacement reconstructs preserved native graphs under a fresh decision, observes source/load state and requires a separate continuation before new execution. Historical unknown operations and original placed history are preserved.

## Bounded evidence

| Campaign | Verified result |
| --- | --- |
| macOS ARM64 native | 91 actual OpenPnP test programs pass; 790 inputs unchanged. |
| Linux ARM64 native | 91 programs pass; 58 sensing child JVM cases pass 865 checks. Exact packaged Bridge used; all Bridge/test classes match the macOS build. Linux runtime/compiler distinctions remain recorded. |
| Packaged headless MCP | All 17 workflows pass against real native OpenPnP, with 4,325 unchanged inputs and clean owned-process exits. |
| Packaged native GUI restart | Atomic-intent and pre-archive crashes each recover in a fresh JVM, complete a separate local continuation and one native placement through public MCP. Actual scripted Swing forms, shipped bootstrap and a disclosed inspection helper are used. Each third JVM reads retained history without restoring authority. |
| Separate source-SDK history | One live case passes after clean standalone recovery; source InMemoryTransport to actual Bridge HTTP. Not an additional packaged stdio suite. |
| Actual stage06 CLI | Installation, native sensing preparation and real Main launch pass. Desktop tool could not bind Java Main, so bootstrap/grant/SDK/restart were not exercised. Normal owned cancellation closed all three process groups. |
| Stage06 assembly and Node22 | Source/offline verification and package assembly pass. All 73 test files run: 475 pass, 18 live skips, zero failures. Stage05 Node24/26 passes retain their earlier package scope. |

[Portable report and exact source hashes](../tests/openpnp/evidence/sensing79-review/summary.json) · [Requirement coverage](../tests/openpnp/coverage.json) · [Verification commands](../tests/openpnp/README.md).

These campaigns qualify their stated simulator paths. They do not establish physical accuracy, independent placement inspection, all interruption boundaries or overall readiness. No hosted CI result or published PR is asserted by this document. Review preparation is separate from release promotion.

## Known source41 limitation

Source41 has a reproduced mapped-axis homing postcondition defect: a native input-axis setter listener can mark the machine homed during configuration apply, and the singleton apply path can report success. A separate S80 repair is under test and is not included here. Treat this path as unqualified; require independent current-state verification before dependent operation.

## Remaining work

See [remaining work](openpnp-remaining-work.md) and the [comprehensive plan](openpnp-codex-plugin-implementation-plan.md). Manual installed-desktop use, all fourteen skills under a uniform fresh evaluation, current performance, continuous endurance and physical qualification remain open. No physical machine is available; that work is deferred until an exact machine/controller/firmware/camera/feeder/host is selected.

Historical milestones and their retained failures remain under `tests/openpnp/evidence/` and the historical sections of `coverage.json`. Older sensing78/inspection75 results are not current source41 evidence. Public historical exports replace private root paths with provenance labels and retain the original report SHA-256.
