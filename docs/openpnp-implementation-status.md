# OpenPnP implementation status

## Current review candidate

**This is a simulator review candidate. The full implementation plan is unfinished; all R01–R18 remain partial.** PR #5 provides 61 typed MCP tools, 34 configuration change types and 14 skills against pinned OpenPnP commit `5bd404cfc70f34103a3ca0fbb6b50c2b465f407c`. Physical-machine control remains unsupported and unqualified.

The revision fixes configuration changes that could leave homing valid after axis-dependent geometry changed. Supported mapped-axis, backlash, nozzle-assembly and restore paths invalidate affected state, reject callbacks that restore homing during the change, and preserve a configuration fault when native effects may already have occurred. Ordinary controls retain their existing behavior. Dependency security updates, installed-lifecycle checks and reproducible scripted GUI restart checks are also included.

The implemented simulator workflows include guarded configuration, camera/motion/calibration operations, job import and editing, native execution, material/board identity tracking, loaded-board inspection, sensing faults and explicitly authorized local recovery. Restart replacement reconstructs preserved native graphs under a fresh decision, observes source/load state and requires a separate continuation before new execution. Historical unknown operations and original placed history are preserved.

## Current bounded evidence

| Campaign | Verified result |
| --- | --- |
| macOS ARM64 native | Qualification07: 94 actual OpenPnP test programs pass; 800 inputs unchanged. |
| Packaged headless MCP | Qualification07: all 17 workflows pass against real OpenPnP with unchanged inputs and clean owned-process completion. Sixteen use the shipped launcher; mapped axes use an explicit native test fixture. |
| Installed lifecycle | The final corrected runner passes uninstall/reinstall against the stopped, populated simulator state, preserving all 78 other state files and the unchanged journal. Seven controlled interruption/control regressions are separate harness evidence. |
| Controller and offline history | The packaged controller recipe passes 15 checks. Seven valid histories inspect successfully; three malformed copies are refused without restoring native authority. |
| Actual JVM halt/recovery | A separately recorded crash campaign passes 14 actual halts and 28 fresh recoveries against matching production hashes. It does not simulate host power loss or qualify physical recovery. |
| Scripted native GUI | All nine routes pass: seven component routes and two packaged restart-to-placement journeys. Each atomic-intent/pre-archive restart completes one native placement through public MCP and a third-JVM history check, with 4,736 unchanged inputs. |
| Root master and package | Node 22.19.0, 24.20.0 and 26.0.0 each pass 416 Node tests and 84 Python harness tests; 24 Node tests explicitly skip. Source rebuild, offline discovery and package bindings pass; package/source bytes remain unchanged during each run. Node22 also runs the nine GUI routes. |
| Dependency audit | The October 27, 2026 UTC dependency refresh records zero production or full npm-audit findings for the selected lockfile. This is a time-specific audit result. |

[Native/package report and exact hashes](../tests/openpnp/evidence/pr5-qualification/final-candidate/summary.json) · [GUI and Node matrix](../tests/openpnp/evidence/pr5-qualification/final-candidate/gui-matrix.json) · [Lifecycle correction](../tests/openpnp/evidence/pr5-qualification/final-candidate/lifecycle-correction.json) · [Requirement coverage](../tests/openpnp/coverage.json) · [Verification commands](../tests/openpnp/README.md).

The tested Bridge is `070c833972fc4dfb6ad322adfc8f677f723aff583d0cdda88ccf5baaac228420`; the generated MCP is `f328aaf1421f264db693707ccb1c1a29083c0a5bac975a2c010216381b4b2d48`. The linked receipts identify the remaining artifacts and original inputs.

Qualification06 retained a GUI takeover timing failure. The corrected fixture waits inside a real initialized native step and requires local revocation before releasing it, then checks abort, one completed step, one cleanup, wrapper completion and durable journal records. The earlier failure remains separate from the passing final GUI run.

These campaigns qualify only their stated simulator paths. GUI forms are invoked programmatically through Swing; they are not manual desktop qualification. Synthetic sensors, native model geometry and placed flags do not establish physical accuracy or independent inspection. Hosted CI and independent review must be checked on the submitted PR head; no hosted pass or merge is asserted here.

## Remaining work

See [remaining work](openpnp-remaining-work.md) and the [comprehensive plan](openpnp-codex-plugin-implementation-plan.md). Manual installed-desktop use, uniform fresh independent evaluation of all fourteen skills, broader configuration/recovery coverage, current performance, continuous endurance and physical qualification remain open. No physical machine is available; that work is deferred until an exact machine/controller/firmware/camera/feeder/host is selected.

Historical source41/stage06, sensing78, inspection75 and other milestones remain under `tests/openpnp/evidence/` and the historical sections of `coverage.json`. Their Linux, desktop, skill and performance results do not transfer to this revision. Public exports omit private runtime/configuration state, connection files, credentials and raw journals while retaining original receipt hashes.
