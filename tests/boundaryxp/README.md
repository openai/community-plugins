# BoundaryXP plugin checks

## rc.12 / B353 synchronization — November 28, 2026

Plugin **0.5.0** pins Windows **0.4.0-rc.12** and Linux **0.4.0-rc.12-linux.1**.
The canonical `npm run test:boundaryxp` passed **9/9 with zero skips** on each
Windows/Linux x64 × Node **22.19.0 / 24.19.0** combination. These were real cold
npm/native installs with four bilingual directory examples, two custom imports,
distinct DEV nodes, HTTP workbench and reconnect persistence. Both lock audits
reported zero vulnerabilities. Marketplace validation and the npm dispatcher
suite passed (11/11).

Separate complete Windows/Linux plugin case replays produced 2 PROGRAM results
(DEV-01 VERIFIED, DEV-02 VIOLATED), full Chinese/English reports, retained result
revision 7 and created recheck revision 8 with zero server model requests. Both
report SHA-256 values equal
`c807286585b80769244fdb2f728262c9a57b96ba03c7e83feb466aa636272159`.
This replays the saved current-Codex case; it is not a new independent model analysis.

The shipped migration helper was exercised through the plugin's `prepareData`
on both platforms using the same synthetic rc.11 acceptance store. All 54 original
files and two custom dictionaries were retained; the old result remained readable.
The rc.11 source takes precedence, then rc.10, then rc.9. Each fallback pathname
was tested with the rc.11 fixture, not relabeled as an older-runtime qualification.
The Linux check used the copied synthetic Windows fixture; general cross-OS user
store migration remains unqualified. Existing installations/configuration were untouched.

Initial setup failures remain recorded: Windows correctly refused Linux lock
generation, the first Linux replay referenced an absent fixture, and the existing
Linux Node 22.18 directory correctly failed the >=22.19 guard. Native Linux lock
generation, the saved Linux response and checksum-verified official Node 22.19
resolved them without weakening tests.

`npm run validate` still fails the unchanged Grafana source-part size assertion
on this Windows CRLF checkout (143531 vs 140000); prior baseline evidence is linked
below. The full marketplace/provider suite is not claimed green or rerun. No
workflow was dispatched. Later on November 28, [Glama rc.12 evidence](https://github.com/joinwell52-AI/BoundaryXP/blob/main/docs/mcp/GLAMA-RC12.md): build 01a221a5-5b6b-758f-99e0-1bde7c002499 passed on November 28 in 1m 1.9s, pinned to public commit a2064abb41f5dc6f68db7742a8b864d4bfb98268. Native startup and discovery passed (21 tools / 17 resources). Published as Latest at 18:15 UTC+8; Glama automatically assigned platform ID 0.4.0, mapping to npm 0.4.0-rc.12-linux.1, not an npm stable release. Original Core source stays private.
The earlier JSON receipt remains the original local-test snapshot.

[Sanitized rc.12 evidence](evidence/rc12-20261128.json) ·
[Product acceptance](https://github.com/joinwell52-AI/BoundaryXP/blob/main/docs/mcp/RC12.md).
Earlier records below retain their original dates and scope.

## rc.11 / B348 synchronization — November 18, 2026

Plugin **0.4.0** pins public Windows **0.4.0-rc.11** and Linux
**0.4.0-rc.11-linux.1** archives. The canonical `npm run test:boundaryxp`
suite passed **9/9, zero skips** on Windows x64 and Debian 12 Linux x64,
each with Node **22.19.0 and 24.19.0**. Marketplace validation and the npm
dispatcher suite passed (11/11, zero skips); both runtime locks audited with
zero vulnerabilities. No GitHub workflow was dispatched. The full unrelated
marketplace/provider suite is not claimed green.

Cold plugin installs exercised actual native tools, four complete bilingual
L2/L3 directory examples, two custom imports, distinct DEV task IDs, HTTP
workbench and reconnect persistence. Separate complete Windows/Linux plugin
case replays used the same saved current-Codex response: 2 valid PROGRAM
calculations (DEV-01 VERIFIED, DEV-02 VIOLATED), complete Chinese/English
reports, zero-server-model recheck from revision 7 to 8, and the original
revision retained. Report SHA-256 on both platforms:
`26e8a827431f6727319417662f7c9230c511d9f0b7a50b60b79a25272e3dbb00`.
This is delivery regression replay, not additional independent model analysis.

The launcher copies inactive `data-rc10` to `data-rc11`; only when rc.10 is
absent does it select `data-rc9`. Isolated tests on both platforms preserved
all 61 source files and verified reconnect. Both path-selection branches were
tested with a saved rc.10 fixture; this does not relabel it as an actual rc.9
runtime migration. Original directories are retained; package migration logic
remains the only migration implementation.

Initial test setup failures are retained: the first Linux test archive omitted
the marketplace manifest; a separate attempt used Node 22.18 and correctly
failed the >=22.19 guard. Restoring the manifest and using the official
checksum-verified Node 22.19.0 distribution resolved those setup issues without
weakening tests or platform requirements. The direct dispatcher run skipped
its npm-lifecycle check; the canonical npm run then passed all 11.

[Sanitized rc.11 evidence](evidence/rc11-20261118.json) ·
[Product acceptance](https://github.com/joinwell52-AI/BoundaryXP/blob/main/docs/mcp/RC11.md) ·
[Glama rc.11 build/discovery](https://github.com/joinwell52-AI/BoundaryXP/blob/main/docs/mcp/GLAMA-RC11.md).
Original Core source remains private. Prior records below retain their dates.

## rc.10 / B345 synchronization — November 17, 2026

Plugin **0.3.0** pins Windows **0.4.0-rc.10** and Linux
**0.4.0-rc.10-linux.1**. `npm run test:boundaryxp` passed **9/9, zero skips**
on Windows and Debian 12 Linux x64, each with Node **22.19.0 / 24.19.0**.
Cold npm installs exercised real native MCP discovery (21 tools / 17 resources),
four complete bilingual L2/L3 directory examples (9/4 files), validation/import,
multiple DEV persistence, workbench HTTP and reconnect preservation.
Both production locks audited with zero vulnerabilities. Marketplace validation
and the canonical npm dispatcher tests passed (11/11, zero skips). No GitHub
workflow was dispatched and the unrelated full marketplace suite was not rerun.

Separate real rc.9 fixture-copy upgrade checks passed on Windows and Linux:
15 / 62 persistent files retained, respectively, with 2 / 5 custom dictionaries,
original source-store bytes and profiles unchanged. New protocol-audit events
append to the copied log; its original prefix remains intact. Active old source
processes are refused without termination. New work uses data-rc10; old data-rc9
is retained. The helper is imported from the locked compiled package, not a
second implementation of migration or Core.

The migrated Linux case retained both old reports. An attempted recheck of the
deliberately superseded revision correctly returned PROJECT_REVISION_CONFLICT.
In a new isolated project, the original input and saved host analysis were
replayed: 3/3 native calculations valid, no incomplete/invalid calculations,
and zero-model recheck produced the same report. This is regression replay,
**not a fresh LLM semantic analysis**. Original failed/cancelled records remain.
Historical 0.2.0 acceptance below is not relabeled as 0.3.0 evidence.

After publishing implementation `d3a372d`, a separate Linux CODEX_HOME installed
0.3.0 directly from the GitHub contributor branch with sparse `.agents/plugins`
and `plugins` paths. Its actual installed MCP copy returned rc.10-linux.1,
21 tools / 17 resources and all five migrated custom dictionaries. The launcher
SHA-256 matched the tested copy. No analysis was resubmitted by this read-only
installation check. [Sanitized rc.10 evidence](evidence/rc10-plugin-20261117.json).

## Linux and Windows acceptance — November 17, 2026

Plugin **0.2.0** has real local acceptance on Windows x64 and Debian 12 Linux
x64 / glibc 2.36. The canonical master passed **9/9, zero skips**, on **each**
platform with Node **22.19.0 and 24.19.0**. Both pinned production dependency
audits reported zero vulnerabilities. Marketplace validation and dispatcher
tests (11/11) passed. Full static validation passed with exact Git LF checkout
bytes; the initial Windows CRLF Grafana part-size failure was retained, not
fixed by editing Grafana. The full marketplace functional suite was not rerun.

On Linux, Codex CLI **0.169.0-alpha.11.1** added the local candidate marketplace,
installed and enabled `boundaryxp@community-plugins` **0.2.0** in a fresh
CODEX_HOME. A real stdio client then launched that **installed plugin copy** in
a separate BXP_PLUGIN_HOME. The current Codex assistant read all frozen prepare
instructions and authored the analysis; the client submitted it via MCP. This
was not an external provider API call, prerecorded model response or claim that
the CLI's own Agent autonomously generated the analysis.

The [limits-and-budget case](../../plugins/boundaryxp/examples/limits-and-budget.md)
uses the explicitly bound English L2/L3 tool examples. Actual results:

- All **21 tools** exercised; 17 resources discovered and bilingual help read.
- DEV-1 action subset verified; DEV-2 production deployment violated; combined
  effort **3+3=6 >5 engineer-hours** violated. All three were PROGRAM results,
  with zero invalid/incomplete calculations and complete dictionary references.
- Seven report steps and the technical report read back; revision **7** retained,
  zero-model recheck produced revision **8** with the same calculations/report.
- Workbench HTTP/data matched the submitted analysis; UI dictionary validation
  and import worked. Editing the test task and reconnecting retained old results,
  source, five custom dictionary versions and a deliberately cancelled job.
- On Windows, the old 0.1.0 and new 0.2.0 launchers reused the same isolated rc.9
  installation with all **19** saved task/dictionary/configuration files unchanged.

[Sanitized machine-readable evidence](evidence/linux-plugin-20261117.json)
contains the versions, hashes and calculation receipts. Raw prepared instructions,
local caches and Core implementation are excluded. These checks qualify this
wrapper's Linux installation and case flow, not Linux desktop GUI behavior,
arbitrary-input semantic accuracy, macOS/ARM64/musl or cross-OS data migration.

After pushing implementation `02e940c`, a **second fresh Linux CODEX_HOME** added
the GitHub contributor branch with sparse `.agents/plugins` and `plugins` paths,
then installed/enabled 0.2.0. Its launcher hash matched the locally tested copy.
Actual MCP calls through that Git-installed copy returned Linux rc.9-linux.1,
21 tools / 17 resources, both earlier result revisions and all five dictionaries.
Profiles, ownership marker and runtime receipt remained byte-identical; no
analysis was resubmitted. This separately verifies the published candidate
installation route, without claiming upstream marketplace approval.

The November 17 implementation adds Linux x64/glibc >=2.36 to plugin 0.2.0.
Windows retains its exact rc.9 runtime lock and default data path. Linux selects
the independent rc.9-linux.1 lock and an XDG data directory. Platform/Node/libc
checks run before installation. The separate Glama build is supplementary
evidence, not a substitute for running this plugin's tests on Linux.

Run `npm run test:boundaryxp` from the repository root. The single master command
covers unit/package checks, safe failures/isolation and native integration.
It needs Node >=22.19, Windows x64 or Linux x64/glibc >=2.36, npm from the Node
distribution and outbound registry.npmjs.org access. macOS/ARM64 are unsupported;
older glibc/musl fail before installation rather than being silently skipped.

The native suite copies the installable plugin into a fresh OS temporary folder,
uses a dedicated BXP_PLUGIN_HOME, installs the locked published npm binary, and
exercises real stdio MCP discovery, bilingual help, four L2/L3 format examples,
validation/import/idempotence, multi-DEV import/validation/project persistence,
source artifact digest and local workbench HTTP. Reconnection must preserve the
new test project and dictionaries. Test evidence remains in that temporary folder.

Portable checks cover manifest/catalog/lock agreement, absence of original Core
files and credentials, unsupported platforms, nonempty data directories and
filtered child-process environment. They never access existing user task stores,
provider credentials, installed MCP configurations or a pre-existing workbench.

This is a plugin packaging/native-tools test, not an LLM semantic accuracy test.
No fabricated LLM response is submitted and no paid provider is invoked.
Unsupported OS/CPU report native integration as skipped, not passed. Native Core
source/rebuild reproducibility is outside this compiled-dependency submission.

Repository-wide `npm run validate:marketplace`, `npm run test:marketplace` and
`npm run validate` remain separate checks. Any unrelated dependency/platform
failures must be reported rather than counted as a plugin pass. All acceptance
for this change is local. The optional manual-only `boundaryxp.yml` workflow
defines one master job, with a Windows or Ubuntu 24.04 runner selection. It runs
Node 22.19 and 24.19 sequentially, audits both locks and checks unchanged tracked
files. There is no platform matrix or additional job. It does not run on push/PR and was not
dispatched. It is not used as evidence of hosted qualification or a required gate.

See [the Windows baseline comparison](GRAFANA-WINDOWS-BASELINE.md) for the
unmodified upstream Grafana failures, exact comparison and reproducible commands.

Local acceptance on 2026-11-16 ran the unchanged master sequentially on Windows
x64 with Node 22.19.0 and 24.19.0: **7/7 passed on each, zero skips**. The Node
22.19 distribution was downloaded from nodejs.org and checked against its
published SHA-256 before execution. Both runs performed a fresh locked npm
installation and real native MCP operations. The locked production audit
reported zero vulnerabilities. Full static validation passed; full-marketplace
Windows success and hosted-workflow execution are not claimed.
