---
name: lh-open-explorer
description: Count, list, or check the status of existing Lighthouse benchmark tasks while opening the conversation's task view; inspect a named task or rubric, or open the global dashboard for leaderboard and quality reviews.
---

# Open Lighthouse Explorer

Show the appropriate graphical view and answer the user's question from the
available evidence. This reads local benchmark evidence and does not run jobs or
change grades.

## Tasks beside the conversation

For counts, lists, qualification status, or “show me the tasks,” open the task
catalog before answering in the current Codex conversation. Find the absolute
benchmark folder from the current project or explicitly named local benchmark,
then call `lighthouse_open_task_authoring` with only `benchmarkPath`; omit
`taskId` and `pane` to show the catalog. This includes “how many tasks do we
have in this bench now”, “what are the 10 tasks?” and “Which tasks are
qualified?” The tool returns task counts for the chat; distinguish published
tasks, drafts and curator-only review material. If more detail is needed, use the
benchmark's files to substantiate the answer. Do not substitute a global app link
for this side panel.

If the user names a particular task, call the same tool with `benchmarkPath` and
its folder slug as `taskId`. Use `pane: instruction` for the learner request,
`pane: rubric` for what is graded, or `pane: evaluation` for qualification and
verifier details. Opening Lighthouse manually from the conversation's side panel
resumes the view last selected in that conversation when available.

## Global dashboard

When the user asks to open the full dashboard, leaderboard, or quality review
queue, return [Open Lighthouse](codex://plugins/lighthouse@community-plugins/app/lighthouse_explorer_home).
The installed plugin's main **Open plugin** button and a pinned Lighthouse
sidebar item reach the same screen. Identify a named benchmark for the user to
select there: the global host does not supply a repository path. Its native
**Choose benchmark folder** action can add a checkout not already shown. Codex's
Back and Forward arrows move through dashboard views already visited.

Do not send a localhost URL or open `benchmark.lighthouse` as text unless the
user specifically asks for a browser development fallback or the source. If the
conversation tool is unavailable or fails, still answer from available evidence
and say the side panel did not open.

An app link is not evidence that the user opened it. Distinguish live visual
verification from a validated plugin registration.
