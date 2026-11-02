# Reviewer

Read-only Git review for the Codex desktop app. Open the current project's
file tree and inspect side-by-side Monaco diffs with line and word highlights.
The panel includes Seti file icons, a resizable tree, changed-file emphasis,
searchable branches (main/master/dev first), scrollbar change markers, and
floating previous/next buttons that navigate diff locations across files.

## Install and first run

Requires Git, Node.js 22.19 or newer on PATH, and a Codex desktop version
supporting thread extension entrypoints and fullscreen MCP Apps. The host
renders this mode in its right-hand panel. Chat-inline rendering is disabled.
The committed distribution starts without npm install or a build.

After this contribution is merged into the marketplace:

```sh
codex plugin marketplace add openai/community-plugins --ref main \
  --sparse .agents/plugins --sparse plugins/codex-reviewer
codex plugin add codex-reviewer@community-plugins
```

Start a new project task, then use this safe first prompt:

```text
Open Reviewer for this project's Git changes. Do not modify files.
```

Open Reviewer from the current task's extension entrypoint. The project folder
comes from that task; a linked Git worktree stays on its own root and branch.
There is no worktree switcher. A task without project context shows a welcome
page; open it from a project task or supply an explicit repository path to
`code-review.open`.

## Comparisons and limits

- **Worktree changes:** current HEAD versus staged/unstaged/untracked contents.
  A repository with no commits is supported.
- **Branch comparison:** merge base of the selected local or remote-tracking
  branch versus current HEAD. Uncommitted edits are excluded. No fetch occurs;
  remote-tracking branches reflect the existing local refs.
- Untracked and unchanged files open as read-only source. Diff navigation skips
  source-only files, unsupported files and changes with no differing text.
- Binary, invalid UTF-8 and files over 1 MiB show an explanation instead of text.
  Symlinks show their link text, never the target contents.
- Snapshots live only in server memory (at most eight); changed-file content is
  captured at review creation. Ordinary source is fixed on first open. Refresh
  when the stale-state indicator appears. Expired snapshots require a refresh.
- Annotations, AI edits and persistent review history are not implemented.

## Permissions, authentication and data

Capability: **Read**. No plugin account, API key or OAuth setup is required;
the marketplace uses ON_USE policy, but this plugin has no authentication flow.

The local Node stdio MCP server runs Git inspection commands (`rev-parse`,
`for-each-ref`, `status`, `ls-files`, `ls-tree`, `merge-base`, `diff`, `cat-file`)
and reads the selected repository. It does not checkout, commit, fetch, push
or edit project files. Git external diff and text conversion are disabled.
The directory tree includes ignored file names, but skips `.git` and does not
traverse symlink directories. Do not open repositories or files containing
credentials or other data you are not authorized to expose to Codex.

For automatic task context, the server can start the installed `codex app-server
--stdio` and request `thread/read` with `includeTurns: false`. On Windows it can
inspect the parent executable path with PowerShell to locate that CLI. It does
not read authentication files or conversation turns. An explicit project path
or host-provided roots are fallback paths if task metadata is unavailable.

The plugin has no independent HTTP service or outbound network client. File
contents and repository metadata travel through the local MCP connection to
Codex; model-visible tools can return diff contents to the assistant. Normal
Codex account and data-processing policies still apply. This is not a sandbox:
the process has the filesystem permissions of the user who starts it.

No review database, credential store or telemetry is created.

## Failure and removal

A non-Git path, unavailable branch, missing merge base or expired snapshot
returns an error; no repository mutation is attempted. If a repository changes
during capture, refresh and retry. For a safe failure check, ask Reviewer to
compare against a branch that does not exist: it should reject that selection.

If the panel is unavailable, check Git/Node on PATH and the desktop's extension
support. Updating a plugin can require reopening the panel or reloading the
desktop host. No restart is performed by the plugin. Remove with:

```sh
codex plugin remove codex-reviewer@community-plugins
```

## Source, license and verification

Adapted from [killf/codex-reviewer](https://github.com/killf/codex-reviewer),
source commit `094b69bad57ee3be8b990d79681815b0e447d1f6`, version 0.3.9.
The portable `plugin.json` and compatibility `.codex-plugin/plugin.json`
identify the same package. Runtime configuration is `mcp.json`.

MIT, copyright 2026 killf; see [LICENSE](LICENSE).
[Third-party notices](dist/THIRD_PARTY_NOTICES.txt) retain the bundled dependencies
and VS Code Seti licenses. The font is retained as the source for generated SVG
glyphs; the UI renders those glyphs rather than loading an icon font.

From the marketplace root:

```sh
# Cold runtime and boundary checks need only Node and Git.
npm run test:codex-reviewer:package
# Complete master: exact rebuild, unit, MCP integration and optional host gates.
npm --prefix plugins/codex-reviewer ci --ignore-scripts
npm run test:codex-reviewer
```

See the [test guide](../../tests/codex-reviewer/README.md) for actual host gates
and how skipped checks differ from a successful desktop interaction test.
