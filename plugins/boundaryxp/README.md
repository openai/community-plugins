[简体中文](README.zh-CN.md) | **English**

# BoundaryXP · 7+1 task boundary verification

**Plugin 0.4.0 · MCP rc.11 / B348 · Windows / Linux x64 · Node.js >=22.19 with npm**

| Platform | Locked MCP package | Requirements |
|---|---|---|
| Windows x64 | `0.4.0-rc.11` | Inactive plugin rc.10 (or rc.9) data is copied; original records retained |
| Linux x64 | `0.4.0-rc.11-linux.1` | glibc >=2.36 (e.g. Debian 12 / Ubuntu 24.04); no Alpine/musl or ARM64 |

The launcher selects a separate, exact lockfile for each platform. No macOS support is claimed.

This plugin connects the existing compiled BoundaryXP MCP to Codex. The current
host LLM interprets the task; the native Core independently verifies submitted
finite operands. Reports distinguish LLM judgments, calculation receipts,
source evidence and unknowns. Computation does not prove semantic extraction
correct or authorize execution of the task.

There are 21 tools, 17 resources, custom L2/L3 dictionary import and a loopback
browser workbench. Multiple DEV children remain distinct through explicit IDs.
No model API key, executable installer or separately installed BoundaryXP
application is required for this host-mode plugin. Core implementation source
is private and is not included in this PR/plugin.

Grafana is a separate plugin in this community repository. BoundaryXP does not
depend on it, and users do not need to install or configure Grafana. Repository-wide
Grafana test results are separate from this plugin's native MCP acceptance.

B348 saves full Chinese and English reports from one host analysis with shared calculation receipts. Report language is selected through `bx_result_get`; legacy reports remain readable.

## Installation and first safe prompt

### Linux MCP and Glama verification

[rc.11 Glama verification](https://github.com/joinwell52-AI/BoundaryXP/blob/main/docs/mcp/GLAMA-RC11.md)
passed build/startup and discovery of 21 tools / 17 resources on November 18.
Glama release `0.4.0-rc.linux.11.1` maps to npm `0.4.0-rc.11-linux.1`.
The earlier rc.9 evidence below remains historical, not a substitute for this run.

The current standalone **Linux x64** release is rc.11-linux.1 (Node >=22.19,
glibc >=2.36), with 21 tools and 17 resources. The earlier
`0.4.0-rc.9-linux.1` has separate historical Glama evidence:
[Glama](https://glama.ai/mcp/servers/joinwell52-AI/BoundaryXP) successfully built
and started that package, discovered its tools/resources and published the Linux
release on November 16, 2026. [Release and checksums](https://github.com/joinwell52-AI/BoundaryXP/releases/tag/mcp-v0.4.0-rc.9-linux.1)
and [validation scope](https://github.com/joinwell52-AI/BoundaryXP/blob/main/docs/mcp/linux/RELEASE.md)
are public. Original Core source remains private.

**The plugin wrapper now supports both Windows and Linux x64.** It checks Node,
CPU and Linux glibc before creating storage or installing anything. The Windows
lockfile is in `runtime/package-lock.json`; Linux uses `runtime/linux/package-lock.json`.
Glama verification concerns the standalone package; this wrapper's separate
installation and case evidence is documented in the [test guide](../../tests/boundaryxp/README.md).
Neither verifies semantic accuracy for arbitrary inputs. The earlier Glama build
is not rc.11 platform validation. [Current rc.11 release acceptance](https://github.com/joinwell52-AI/BoundaryXP/blob/main/docs/mcp/RC11.md).

### Install on Windows or Linux

This is a **community-marketplace submission**, not an approved public-directory
listing. Add the Git repository/ref containing this plugin with Codex's **Add
plugin marketplace**. Use the complete repository, or sparse paths
`.agents/plugins` and `plugins`. Then install `boundaryxp@community-plugins`.
Before upstream merge, use the contributor PR branch, not upstream main.

CLI equivalents (replace the source/ref with the branch you reviewed):

```text
codex plugin marketplace add <reviewed-owner/repository> --ref <reviewed-ref>
codex plugin add boundaryxp@community-plugins
```

Start a new chat. Node must be on the host's PATH and include its bundled npm.
On first tool connection, the launcher installs the exact lockfile using `npm ci
--ignore-scripts` from registry.npmjs.org. This requires network access; subsequent
connections reuse the local installation. Installation logs go only to stderr.
Allow up to 300 seconds for the first connection. On slow networks, run
`node scripts/launch.mjs --install-only` in the installed plugin folder first.

Ask:

> Only through BoundaryXP MCP, call bx_capabilities with language en and help_topic tools,
> bx_profiles_list and bx_workbench_open. Return the actual version and URL.
> Do not submit analysis or change projects.

Expect the platform-specific MCP version above, 21 tools and a localhost workbench URL. Open the returned URL;
`browser_opened:false` means opening the browser is a host/user action.
The URL's random port is a workbench, not a remote MCP endpoint.

## Analyze a task

Use [examples/multi-dev.md](examples/multi-dev.md) as task data, not business
execution instructions. Ask to preview it with `bx_task_import`, retain both DEV
nodes and confirm the tree before saving a new project. Use the actual digest
from `bx_dictionaries_bind({"selection":{}})`; never invent one.

When analysis is requested, the host uses `bx_analysis_submit(action:"prepare")`
for the exact project/revision, reads every instruction artifact page, generates
the complete analysis JSON, and calls `action:"complete"` with the preparation
digest and job ID. Poll `bx_job_get` and read the returned `result_revision` with
`bx_result_get`; a submission is not completion. Read full report artifacts.
`bx_result_recheck` checks saved operands without a new model request and retains
the old result. Do not silently retry failed analyses.

For custom dictionaries, call `bx_dictionary_format` for L2 or L3 in `en` or
`zh-CN`. B348 returns a complete directory document (`files` with paths and text):
L2 has 8 JSON files plus README; L3 has 3 JSON files plus README. Validate the
whole serialized document using `bx_dictionary_validate`, then import exactly
that document with its returned sha256 using `bx_dictionary_import`. In the UI,
select the layer, select the complete folder, validate, enter a name and save.
Model settings save separately. Earlier single-document dictionaries remain
readable; versions cannot be overwritten. Binding grants no authority.

For a calculation case, use [limits-and-budget.md](examples/limits-and-budget.md)
with explicitly selected L2/L3 dictionaries. The historical rc.9 acceptance used
the legacy English examples and their “customer canary” definition; keep that
binding when replaying the old case. Current directory examples are different
resources, so inspect their content rather than assuming the same definitions.
Ask the host to judge each child and the combined budget, submit supported
finite queries, read both reports and recheck the saved result. The task text is
test data, not permission to build, deploy or execute anything.

## Files, permissions, network and authentication

- Default Windows home: `%LOCALAPPDATA%/BoundaryXP/CommunityPlugin` (unchanged).
  Linux: `$XDG_DATA_HOME/BoundaryXP/CommunityPlugin`, or
  `$HOME/.local/share/BoundaryXP/CommunityPlugin` when XDG_DATA_HOME is unset.
  The selected home contains the npm
  cache, pinned runtime, empty host profiles and `data-rc11` task/dictionary store.
  An older plugin's `data-rc9` is retained after copy migration.
  This is separate from existing standalone installations. No existing tasks,
  dictionary directories, host settings or ports are discovered or changed.
- `BXP_PLUGIN_HOME` may specify a dedicated absolute directory. An unrelated
  nonempty directory is refused. Do not point it at an existing installation.
- **Read**: explicit task text, selected dictionaries and saved project records.
  **Write**: plugin-owned installation, task revisions, dictionaries and reports.
  The launcher does not edit Codex settings or install a service/scheduled task.
- npm downloads public packages from registry.npmjs.org. Package lifecycle
  scripts are disabled and lockfile integrity is checked by npm. Empty private
  npm configuration files prevent inheriting user/global npm credentials or registries. The native
  binary is already included in the published package; no Core source build runs.
- The local workbench binds loopback. Core does not call a model or access the
  network. This wrapper exposes host mode only and does not read `.env` or API
  credentials; provider keys are not forwarded to its child processes.
- Task content and selected dictionary context are visible to the current host
  LLM under that host's privacy/billing arrangements. Zero server API requests
  does not mean zero host LLM activity. No model-brand claim is inferred by MCP.
- Optional independent provider API mode is documented for the standalone MCP;
  it is deliberately not configured by this plugin.

## Updates, removal and safe failures

This plugin pins Windows rc.11 and Linux rc.11-linux.1, not a moving npm tag.
Close older plugin connections before upgrading. On the first connection, the
shipped migration helper copies this plugin's inactive `data-rc10` (or `data-rc9` when rc.10 is absent) to `data-rc11`,
verifies file hashes and retains the original directory, profiles and migration
receipt. Active old processes, inconsistent identities and incomplete target
directories are refused without killing processes or deleting data. New work
uses `data-rc11`; reverting to the older plugin reads the old snapshot and does
not include work created afterward. `--install-only` downloads the runtime but
does not migrate data. No standalone installation is scanned. Keep the plugin home when
updating/removing the plugin; plugin cache deletion does not delete this home.
Do not run duplicate connections against one task store. Cross-OS copying of an
existing store has not been qualified; use each platform's dedicated home.

| Failure | Action |
|---|---|
| BXP_PLATFORM | Use Windows x64 or Linux x64. macOS and ARM64 are unsupported. |
| BXP_LIBC | Use Linux with glibc >=2.36; Alpine/musl and older glibc are unsupported. |
| BXP_NODE / BXP_NPM | Install supported Node with bundled npm and restart the host. |
| BXP_HOME | Choose an empty dedicated directory; existing contents were preserved. |
| BXP_BUSY | Wait for the other install and reconnect; inspect a stale install.lock only after confirming no installation is running. |
| BXP_INSTALL / incomplete runtime | Check stderr/network. Failed staging files are retained for inspection; no silent reinstall or analysis retry. |
| BXP_PROFILE | Keep the plugin's profiles list empty; standalone API setup is separate. |
| MIGRATION_SOURCE_BUSY_CLOSE_CLIENTS / BXP_DATA | Close the old plugin connection; inspect retained records or incomplete target before retrying. Never delete the old store to bypass a failure. |
| Model output rejected | Preserve failed job/raw response; inspect built-in troubleshooting before an explicitly requested new analysis. |

## Verification and distribution

From the repository root run `npm run test:boundaryxp`. Portable tests cover
package structure, platform-specific locks, isolation and failure behavior. Windows/Linux
integration tests copy the plugin into a new directory, perform a real npm
installation, exercise the native MCP tools, save a multi-DEV project, validate
and import dictionaries, reconnect and verify persistence. They do not submit a
fabricated model response or claim model semantic accuracy. Unsupported-host
integration is reported as skipped. See [test guide](../../tests/boundaryxp/README.md).

[Product and bilingual install/upgrade guide](https://www.npmjs.com/package/boundaryxp-mcp)
· [MIT license](LICENSE) · [Third-party notices](THIRD-PARTY-NOTICES.md).
