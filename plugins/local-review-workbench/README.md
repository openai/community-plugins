# Local Review Workbench

Understand what a PR changes, where to focus your review, and which issues need
attention. This preview adds one skill, `$review-local-changes`, that uses your
local code and the requirements already discussed in your Codex session.

The review gives you:

- **A short explanation:** the intended behavior and what changed.
- **A review guide:** the files and API contracts worth checking first.
- **Verified findings:** each issue's location, trigger, impact, and evidence.
- **Optional improvements:** useful simplification and naming suggestions.
- **Known gaps:** missing context and checks that were not run.

## Get started

You need Codex with plugin and skill support, plus access to the code or patch
you want reviewed. The skill uses your existing model and reasoning settings.
It needs no additional account, API key, runtime, or monorepo checkout. Git is
useful when reviewing a local checkout.

Install from the marketplace:

```sh
codex plugin marketplace add openai/community-plugins --ref main \
  --sparse .agents/plugins --sparse plugins/local-review-workbench
codex plugin add local-review-workbench@community-plugins
```

**Trying an unmerged PR?** Replace `main` with the PR's branch name. The selected
branch must contain this plugin. Start a new Codex task after installing.

Try the bundled API example first. It uses invented data and needs no credentials:

```text
Use $review-local-changes to explain the bundled compatibility fixture and review
its before/after change without editing files. Do not read the evaluation answer
key until the review is complete.
```

Then use it in the session where you made a change:

```text
Use $review-local-changes on my current changes. Explain what this PR does and
where I should focus my review, then report validated issues and separate
optional suggestions. Preserve the working tree.
```

You can also provide a PR URL or the base and head Git refs. If the skill cannot
access the PR or establish the comparison, it explains what is missing.

## What it reviews

| Focus | What the reviewer checks |
| --- | --- |
| Correctness and compatibility | Does the change meet its requirements? Do existing callers and supported API behavior still work? |
| Simplification | What required behavior justifies added complexity? What can be simplified within this PR, and would users notice? |
| Naming and readability | Do names and structure make the change easy to understand? |

When subagents are available and useful, up to three reviewers work independently.
Otherwise, one agent checks the same areas in sequence and says so. The lead
reviewer checks the evidence, rejects unsupported findings, and combines duplicates.
It also checks any supplied hosted-review findings; local and hosted reviews can
provide different context.

The review records the exact code versions and selected local changes, including
relevant untracked files. It checks them again before reporting. If the code
changes during review, affected findings must be reviewed again.

## Read-only behavior

Findings are returned in chat. The skill reads the selected code, relevant
callers, schemas, tests, repository instructions, and supplied review comments.
It does not edit files, install dependencies, format code, change Git history,
push, post comments, or merge.

Work is limited to three discovery reviewers, one validation pass, and one
targeted follow-up when needed. There is no automatic fix-and-review loop.
An empty finding list does not guarantee correctness.

The skill reads test code and existing results by default. It runs a command
only when that command is already authorized and known to preserve files and
avoid network or external writes. Otherwise, it names the unrun check for a
separate validation task.

## Data and permissions

The manifest declares `Read`. This is an instruction boundary, not an additional
filesystem sandbox; the Codex environment controls actual permissions.

Your configured Codex provider processes source, session context, and tool
results under your existing data controls. “Local” refers to the review context;
it does not mean inference happens offline or on your device. PR reads use your
available repository integration and are limited to the selected PR.

The package contains instructions, synthetic examples, and a license. It adds no
MCP server, app dependency, hook, telemetry, background task, or upload/storage
service. Marketplace authentication is set to `ON_USE`, but this skill has no
additional sign-in flow. Feedback is optional and stays a draft until you ask
to send it. Keep credentials and unrelated private material out of feedback.

## Test and evaluate

Read the [skill](skills/review-local-changes/SKILL.md),
[review rules](skills/review-local-changes/references/review-contract.md), and
[evaluation guide](evaluations/README.md).

Contributors can run the package checks with Node.js 22.19 or newer:

```sh
npm run test:local-review-workbench
```

These checks verify packaging and the synthetic example. Review accuracy, false
positives, and installation in a Codex client still need human evaluation and a
small pilot. Node.js is needed for these contributor checks, not to use the skill.

Licensed under [Apache-2.0](LICENSE), with no bundled third-party runtime
dependencies. Inclusion in this community marketplace and review for the official
Plugins Directory are separate processes.
