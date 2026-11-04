# Explorer prototype: manual acceptance

These are checks for the installed global app and file extension in Codex Desktop.
Opening HTML directly, a server unit test, or a browser-only screenshot does not
confirm the installed extension. The benchmark links below refer to sibling local
repositories and their ignored evidence; they may not exist in another checkout.

## Two-repository walkthrough

| View | Polars | Grimoire |
| --- | --- | --- |
| Launcher | Open the Lighthouse plugin directly; select Polars and confirm its title and identity. Optionally check the separate file handler. | Switch to Grimoire in the native app and confirm it has no Polars tasks or reviews. Switch back to confirm isolation. |
| Tasks | Ten native tasks. Open `iterable-full-schema`: native name `polars/iterable-full-schema`, version `1.0.0`. Also find `parquet-legacy-list`, whose namespace is `polars-bench`, not `polars`. | Five native tasks. Open `bookmarklet-capture`: native name `grimoire/bookmarklet-capture`, version `1.0.3`. Also find `search-sort`, whose namespace is `grimoire-bench-3`, not `grimoire`. |
| Rubrics | Open `iterable-full-schema`; the private requirements include R1 for late promotable values. It must be labelled curator-only and distinct from the learner request. | Open `bookmarklet-capture`; the private requirements are available separately from the learner request. No functional verifier amendments should masquerade as model code-quality reviews. |
| Leaderboard | The immutable original baseline has 20 attempts, 17 functionally measured (14 passed, three failed) and three original API errors. Regrades are never additional learners. Re-read the evolving cascade before comparing overall/quality numbers. | The published *amended* functional leaderboard has high `5/5`, xhigh `4/5`; the immutable originals had high `4/5`, xhigh `3/5` under a known defective bookmarklet verifier. A view computing native originals must explicitly identify itself and must not display them as the amended figures. |
| Quality queue | `iterable-full-schema` Luna (source UUID `350aa829-0ae2-43b7-a3d3-8297c46696b5`) is a confirmed quality failure in `tests`; Sol and Astra give independently retained evidence and the decision is overall fail despite a functional pass. The Sol submission for the same task (`3f3e5b0f-d879-4a11-b009-5ffa24e5d785`) is a clean quality pass. A quality pass on the historical `udf-containment` Luna API-error original (`d5865988-074b-4ea4-86b3-9ac3ef8928a2`) cannot become an overall pass. `iceberg-commit-recovery` Luna (`10b57b20-7249-450e-8e34-a4539ad3458a`) is a real `maintainability` dispute: Sol fails, Astra passes. Quality awaits review but the overall result is already a failure because the functional original failed. | No Lighthouse Sol/Astra quality index was present when these checks were written; quality is unknown and must never be inferred from original functional rewards, amended rewards, or a checked-in Markdown leaderboard. |

The Polars cascade was actively adding results when this checklist was written.
Compare displayed numbers with the current
[`quality-index.json`](../../polars-bench/experiments/quality-cascade/quality-index.json)
and [`RESULTS.md`](../../polars-bench/experiments/quality-cascade/RESULTS.md), and
the actual open-review set with
[`HUMAN_REVIEW.md`](../../polars-bench/experiments/quality-cascade/HUMAN_REVIEW.md).
Do not create a fake review in either real benchmark. If the real queue is empty,
the desktop should show a truthful empty state; exercise a disputed result with
an isolated fixture and identify it as simulated. A new cascade result should
appear after Refresh without changing benchmark root. Grimoire's amendments are
documented in its [`leaderboard`](../../grimoire-bench-3/leaderboard/README.md).

Also check keyboard navigation between views, a readable narrow window, an
unavailable/missing data state, evidence opening in the native desktop handler,
and an invalid/missing marker returning a useful error without reading sibling
files. Record the desktop build and where its actual launch succeeded or failed;
do not report developer-tool/static results as desktop validation.

## Check main-window navigation and conversation authoring separately

1. In the installed global app, visit a task, its curator rubric, the
   leaderboard and a quality review. Use the Codex window's **Back** and
   **Forward** arrows. Confirm the prior screen and selection return, including
   a selected leaderboard comparison. These are global app deep links, not
   side-panel navigation controls.
2. In a coding conversation, ask Lighthouse to open a benchmark folder and draft
   task in the authoring panel. Confirm **Learner request**, **Curator rubric**
   and **Evaluation** stay separate; a newly created draft should appear before
   its `task.toml`. Also check work started only in `review/<task>/` and a
   published task.
3. Save a change to the selected draft's instruction or curator rubric. Confirm
   the visible panel reflects it within the next five-second check and that
   **Refresh** works immediately. Close and reopen the Lighthouse conversation
   tab and confirm the focus previously set in this thread is restored. Another
   conversation must not inherit that focus.

## Verification recorded on 2026-09-26

- Installed `lighthouse-explorer@personal` preview version
  `0.1.0+codex.20260926182603`; both repository launchers were submitted to the
  Desktop opener. The user's follow-up screenshot clarified that Grimoire opened
  as raw JSON in the text editor, not as the Explorer. The referenced chat shows
  a direct tool call without host file metadata failed before the ordinary file
  opener was used. Desktop source confirms generic opening gives text priority;
  the native custom viewer must be explicitly selected using the chevron beside
  **Open → Lighthouse Benchmark Explorer**. The installed MCP server is
  discoverable and advertises the file handler. Direct automation of the native
  Codex app was unavailable under Computer Use policy. The native app identified
  by that tool was Codex Nightly `26.924.10608` (build `11119`); the native menu
  click is not independently verified.
- The Codex in-app browser ran the actual packaged iframe against both real
  repositories with a local MCP host simulator. Keyboard navigation exercised
  tasks and curator rubrics, the leaderboard and quality queue. Polars displayed
  the original functional `14/17` and the saved Sol/Astra Iceberg disagreement.
  Grimoire displayed amended xhigh `4/5` and original xhigh `3/5` in separate
  comparisons and marked all ten originals as unmeasured for quality. The
  simulated host received the benchmark-relative rubric source action. This
  browser evidence does not confirm native file opening or every installed
  Desktop tab.
- `npm run check` passed type checking, a release bundle build and 14 Explorer
  tests including the actual stdio protocol and executable inline widget. The
  root verification passed the lock, plugin structure, Ruff, typing and 51
  Python tests. The root and staged preview plugin manifests both validated.
- After the screenshot, installed `0.1.0+codex.20260926183653` with the direct
  graphical launcher as the preview's default action. Both installed-bundle
  launch commands returned persistent loopback URLs and both dashboards were
  opened in the Codex in-app browser. The Grimoire curator source opened in the
  dashboard with its contents; Polars rendered the leaderboard and quality queue.
  The revised release passed 21 Explorer tests (including source boundaries and
  replacement of an older viewer on the same URL) and all 51 root tests. This is
  verification of the direct browser experience; the native custom-viewer menu
  remains supported by source but was not directly operated during this check.

## Native global app verification recorded on 2026-09-27

- Installed the main `lighthouse@personal` plugin at
  `0.3.0+codex.20260927095828`, keeping all existing skill files byte-identical,
  and the separately named `lighthouse-explorer@personal` preview at
  `0.1.0+codex.20260927095804`. The exact built widget and MCP server hashes
  match the installed main plugin.
- Queried the real Codex desktop app-server via `mcpServerStatus/list`. The main
  plugin registered `lighthouse_explorer_home` as **global**,
  `lighthouse_explorer_thread` as **thread**, and the existing `.lighthouse`
  tool as **file**, each pointing to its correct UI resource. The preview was
  discovered under a separate server and a separate global title.
- The packaged global widget was exercised in the Codex in-app browser using an
  empty launch and the actual stdio MCP server. It displayed both live checkouts,
  switched between Grimoire and Polars, rendered both dashboards, and showed the
  Polars original functional `14/17`. This browser test bridge is not the native
  plugin button. The native Codex application was denied to Computer Use; a
  direct agent click on the plugin and the operating system folder dialog are
  not claimed as visually verified. The folder protocol separately passed actual
  MCP transport round-trips, including cancellation and invalid URI handling.
- The release passed 33 Explorer tests plus the complete 51 root Python tests.
- The user subsequently confirmed that the main global app works and supplied a
  screenshot of Grimoire's graphical overview inside the actual Codex window.
  The screenshot predates the dashboard history and focused authoring changes;
  it does not verify the new main-window arrow clicks or the authoring panel.

## Dashboard history and authoring checks recorded on 2026-09-27

- Automated [navigation tests](src/ui/navigation.test.ts) cover route
  round-trips, host Back/Forward restoring a view without pushing a duplicate,
  and rejection of external or invalid locations. The
  [stdio server test](src/integration/server.test.ts) covers model-visible panel
  registration, host thread isolation, restoration across a server restart, and
  private rubric content remaining available only to the app. The
  [benchmark tests](src/benchmark/index.test.ts) cover unfinished drafts,
  curator-only work, published tasks, selected-task refresh and file boundaries;
  [panel tests](src/ui/authoring.test.ts) cover separation of learner and curator
  content, keyboard operation and unavailable tasks.
- The new main-window arrow clicks and the focused authoring panel have not yet
  been independently operated in the native Desktop app. Keep the existing user
  confirmation of the global dashboard separate from that remaining native
  acceptance step.

The native design follows the global and thread surfaces in the
[Bits & Bolts server](https://github.com/openai/openai/blob/34a0a48f10dd22c636b4157b72837e10bbdfe211/plugins/bits-and-bolts/src/server/register.ts#L167-L216).
Codex selects this global tool for the plugin's main
[Open plugin action](https://github.com/openai/openai/blob/34a0a48f10dd22c636b4157b72837e10bbdfe211/codex/codex-apps/webview/src/plugins/plugin-detail-page.tsx#L1609-L1625).
