# Simulator qualification and limits

This review package has 61 tools, 34 typed configuration changes and 14 skills. Its native functional build is reconciliation79/source41; stage06 changes the GUI preparation script and metadata. The Bridge and generated MCP bytes remain the tested source41 artifacts.

| Evidence | Result and boundary |
| --- | --- |
| Native application | 91 actual OpenPnP programs pass on macOS ARM64 and Linux ARM64. Linux uses its independently built, pinned native runtime and the exact macOS packaged Bridge; all 266 Bridge and 391 test classes match. |
| Headless packaged MCP | 17 workflows pass through the official SDK, generated stdio server and real OpenPnP. Sixteen use the shipped simulator CLI; mapped axes use an explicit native test fixture. |
| GUI crash/restart | Both atomic-intent and pre-archive crash boundaries pass. Each fresh JVM uses actual MainFrame/controller forms, separate recovery decisions, full public MCP registration/validation/job execution and one native placement. A third JVM reads history without restoring authority or appending records. |
| GUI test seam | Forms are invoked programmatically on Swing. The shipped bootstrap runs with Bridge off the application classpath; a disclosed test-only child-loader helper independently inspects native state. These are scripted workflows, not manual desktop installation or fresh skill actors. |
| Source SDK history | One separately gated live history case passes against actual Bridge HTTP after a clean standalone recovery. It is not an eighteenth packaged stdio workflow. |
| Actual stage06 CLI | Installation, native sensing preparation and real OpenPnP Main launch pass. Desktop tool selection failed; no local bootstrap, grant, SDK control or restart was performed. Normal owned cancellation closed all three process groups. |
| Stage06 package | Source rebuild, offline discovery and assembly pass. Node22 runs all 73 files: 475 pass, 18 explicit live skips, zero failures. Stage05 Node24/26 each passed 475 with 18 skips on their earlier package bytes. |

All eighteen implementation-plan requirements remain partial. Physical devices, manual installed-desktop use, uniform fresh behavioral evaluation of all fourteen skills, current performance targets, the full eight-hour endurance criterion, broader recovery interruption boundaries and hosted CI results remain open. Simulator sensors and inspection observations are synthetic; model placed flags are not independent physical inspection.

The source repository contains the portable current report at `tests/openpnp/evidence/sensing79-review/summary.json`, exact original report hashes, the full R01–R18 coverage map, retained historical failures and the comprehensive implementation plan. Private runtime/configuration state, connection files, tokens and raw journals are excluded from the public package. Historical results apply only to their recorded artifacts.
