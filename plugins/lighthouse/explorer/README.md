# Lighthouse Explorer

Lighthouse Explorer is an optional, read-only graphical dashboard for an ordinary
Harbor benchmark. It shows tasks, learner requests, curator rubrics and
qualification, selected leaderboard cohorts, and saved code-quality decisions.
It does not start jobs, write grades, or expose curator documents through
model-visible MCP tools.

Click **Lighthouse → Open plugin** in Codex. Its global entrypoint opens the
graphical dashboard inside Codex; no web address or file menu is involved. If
you pin Lighthouse in the Codex sidebar, the sidebar item opens the same view.
It is also available as a conversation tab. Codex does not provide the current
workspace to global apps: select a detected benchmark or choose its folder in
the dashboard. The dashboard remembers the last selected benchmark when host
storage is available.

Use the Codex window's **Back** and **Forward** arrows to revisit global
dashboard views, including a selected task or rubric, leaderboard comparison or
quality review. The dashboard records those locations with app deep links. The
authoring side panel has its own task and document controls; it does not change
the window's global history.

The native file viewer remains available as a secondary route. Ordinary file
links show the raw marker because Desktop prefers its text editor for text
files; use the dedicated plugin action for the dashboard.

## Follow a task while writing it

When `lh-create-task` knows the benchmark folder and task slug, it opens
**What we’re evaluating** beside the Codex coding conversation. The panel has
separate **Learner request**, **Curator rubric** and **Evaluation** views. The
last task explicitly opened for that conversation is restored when you reopen
the Lighthouse side panel; otherwise choose a benchmark and task in the panel.

Drafts in `.local/drafts/<task>/` appear before `task.toml` exists. The panel
also shows curator-only work started in `review/<task>/` and published tasks in
`tasks/<task>/`. The checklist distinguishes missing or invalid files from
qualification. While visible, the panel checks the selected task's saved files
every five seconds; choose **Refresh** for an immediate check. Unsaved editor
changes will not appear.

## Launcher

The root file is JSON with an explicit version. New benchmarks receive an empty
launcher from `lh-create-bench`:

```json
{
  "schemaVersion": 1,
  "name": "example-bench",
  "leaderboard": "leaderboard/README.md",
  "cohorts": [],
  "qualityIndexes": []
}
```

When jobs exist, record a separate entry for each compatible comparison. Results
may be exact native `result.json` paths, trial/job directories, or a pattern with
one `*` in its final path component. Pick only original learner trials, or label
an amended verifier cohort explicitly; do not combine versions or count regrades
as new learner attempts. The original task's binary functional key is mandatory
for a cohort: use `solved`, or `reward` only where that historical verifier was
authored as binary. Every path is relative to the benchmark root.

```json
{
  "schemaVersion": 1,
  "name": "example-bench",
  "leaderboard": "leaderboard/README.md",
  "cohorts": [{
    "id": "original-2026-09",
    "label": "Original comparison",
    "revision": "full-benchmark-commit",
    "functionalKey": "solved",
    "results": ["jobs/comparison-*"],
    "qualityIndexes": ["experiments/quality/quality-index.json"]
  }],
  "qualityIndexes": ["experiments/quality/quality-index.json"]
}
```

A quality index maps original Harbor trial UUIDs to native regrades. Both the
native source link and saved quality policy must agree. Missing quality remains
unmeasured; it cannot produce a benchmark pass. A fresh checkout without ignored
`jobs/` and regrades can still show committed tasks and rubrics, but may have no
raw results or review queue. Refresh explicitly when another process adds results.

## Development

```sh
npm --prefix explorer ci --ignore-scripts
npm --prefix explorer run check
```

The build emits a standalone stdio MCP server and a self-contained iframe in
`explorer/dist/`; no network or package installation is needed to start the
installed viewer. Node 22+ is required. `lighthouse_explorer_home` and
`lighthouse_explorer_thread` are the global and conversation entrypoints;
`lighthouse_open_benchmark` remains the file entrypoint.
`lighthouse_open_task_authoring` opens a conversation panel from an absolute
benchmark folder or marker, with an optional `taskId` and `pane` (`instruction`,
`rubric` or `evaluation`); it returns focus only. Document, library and evidence
tools are app-only. A file view uses the host-supplied path; the global and
conversation views read through an opaque ID after selection. Folder selection
and source opening use optional Desktop capabilities.

For this local prototype, discovery checks direct `*-bench*` children of
`~/code` for an actual `benchmark.lighthouse`; it does not recursively scan the
machine. Set `LIGHTHOUSE_BENCHMARK_HOME` to a different parent or
`LIGHTHOUSE_BENCHMARKS` to a platform-delimited list of explicit folders or
markers. Benchmarks elsewhere can be selected directly in the dashboard.

The separate browser host is a development fallback, not how the installed
plugin opens. To run that host and keep it available after the command exits:

```sh
node explorer/dist/browser.cjs /absolute/path/to/example-bench/benchmark.lighthouse
```

The command prints a loopback URL, reuses an existing viewer for the same
benchmark, and does not make it public. Evidence opens in the local dashboard.
To serve several explicitly named benchmarks together during development:

```sh
node --experimental-strip-types explorer/scripts/preview.mjs \
  example=/absolute/path/to/example-bench/benchmark.lighthouse
```

Open `http://127.0.0.1:4173/?repo=example` in a browser after starting that
foreground command. This exercises the packaged iframe and local evidence; it
does not exercise the native custom-file menu.

The [two-benchmark acceptance checklist](acceptance.md) distinguishes package,
MCP, browser, and actual installed Codex Desktop verification.
