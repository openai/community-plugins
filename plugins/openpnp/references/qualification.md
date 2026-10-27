# Simulator qualification and limits

This review package has 61 tools, 34 typed configuration change types and 14 skills. PR #5's revised candidate includes configuration homing/fault fixes, dependency security updates and reproducible lifecycle and scripted GUI qualification. It targets pinned OpenPnP commit `5bd404cfc70f34103a3ca0fbb6b50c2b465f407c`; physical-machine control remains unsupported and unqualified.

The tested Bridge SHA-256 is `070c833972fc4dfb6ad322adfc8f677f723aff583d0cdda88ccf5baaac228420`; the MCP SHA-256 is `f328aaf1421f264db693707ccb1c1a29083c0a5bac975a2c010216381b4b2d48`.

| Evidence | Result and boundary |
| --- | --- |
| Native application | Qualification07 passes 94 actual OpenPnP programs on macOS ARM64 with 800 unchanged inputs. |
| Headless packaged MCP | All 17 qualification07 workflows pass through the official SDK, generated stdio server and real OpenPnP. Sixteen use the shipped simulator CLI; mapped axes use an explicit native test fixture. |
| Installed lifecycle | The final corrected runner passes uninstall/reinstall of the stopped, populated core simulator state, preserving all 78 other state files and the unchanged journal. Seven controlled cancellation/control regressions are separate harness checks. |
| Controller and history | The packaged controller recipe passes 15 checks; seven valid offline histories pass and three malformed copies are refused without restoring authority. |
| Actual JVM halt/recovery | A separate campaign records 14 actual halts and 28 fresh recoveries against matching production hashes. It does not establish host power-loss durability or physical recovery. |
| Scripted native GUI | All nine routes pass: ownership, unknown-exit, topology, panel membership, panel takeover, title, board inspection and two packaged restart journeys. Atomic-intent and pre-archive restart each complete one native placement and third-JVM history inspection, with 4,736 unchanged inputs. |
| Root masters and package | Node 22.19.0/24.20.0/26.0.0 each pass 416 Node and 84 Python tests with 24 explicit Node skips. Source rebuild, offline discovery and package bindings pass; package/source bytes remain unchanged during each run. Node22 additionally runs the nine GUI routes. |
| Dependency audit | The October 27, 2026 UTC dependency refresh records zero production or full npm-audit findings for the selected lockfile. Audit results are time-specific. |

GUI forms are invoked programmatically through Swing. The shipped bootstrap attaches Bridge with its JAR absent from the application classpath. Component fixtures are freshly compiled from hashed source snapshots with an explicit helper closure and collision checks against production classes. The restart journeys use a disclosed child-loader helper to observe native state. The title route does not attach Bridge. These are scripted workflows, not manual installed-desktop qualification or fresh skill actors.

The qualification06 GUI takeover failure remains retained. Its corrected fixture waits inside an initialized native step, accepts local revocation while that step is held, then requires abort, one actual cleanup, wrapper completion and durable journal counts. The final passing GUI run is separately recorded.

All eighteen implementation-plan requirements remain partial. Physical devices, manual installed-desktop use, uniform fresh behavioral evaluation of all fourteen skills, current performance targets, continuous eight-hour endurance and broader recovery/configuration coverage remain open. Simulator sensors and inspection observations are synthetic; model placed flags are not independent physical inspection. Hosted CI and review status must be checked on the submitted PR head; local campaigns do not establish hosted success.

The source repository retains current portable evidence in `tests/openpnp/evidence/pr5-qualification/final-candidate/`: `summary.json`, `gui-matrix.json`, `lifecycle-correction.json` and `dependency-summary.json`. Original receipt hashes and separate failed attempts are retained. Historical source41/stage06 evidence remains at `tests/openpnp/evidence/sensing79-review/summary.json`; its platform, desktop, skill and performance results apply only to its original artifacts. Private runtime/configuration state, connection files, credentials and raw journals are excluded from public evidence. The coverage map and comprehensive implementation plan retain the remaining R01–R18 scope.
