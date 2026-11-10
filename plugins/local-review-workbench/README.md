# Local Review Workbench

A skills-only preview for developers who want to understand a change before
opening or reviewing a pull request. It explains the intent, shows where human
attention matters, and checks candidate findings before reporting them.

The workflow uses local code and available session requirements. Three bounded
review perspectives cover correctness and backward compatibility,
simplification, and naming. The coordinating reviewer validates and deduplicates
their findings. Local and hosted reviews can provide different context; this
plugin complements an existing hosted review.

## Install and first run

Requires Codex with plugin/skill support and access to the selected source or
patch. Local Git is useful for a checkout. Parallel review requires subagent
support; otherwise the skill performs the same perspectives sequentially and
reports that limitation. It uses the user's configured model and reasoning
settings. No model selection, additional account, API key, Node runtime, or
monorepo checkout is required to use the skill.

```sh
codex plugin marketplace add openai/community-plugins --ref main \
  --sparse .agents/plugins --sparse plugins/local-review-workbench
codex plugin add local-review-workbench@community-plugins
```

Start a new Codex task after installing. This candidate must be merged into
the selected marketplace ref before that ref can install it; use the candidate
branch when evaluating an unmerged PR.

Try the credential-free synthetic fixture first:

```text
Use $review-local-changes to explain the bundled compatibility fixture and review
its before/after change without editing files. Do not read the evaluation answer
key until the review is complete.
```

Then try it in the session where you made a change:

```text
Use $review-local-changes on my current changes. Explain what this PR does and
where I should focus my review, then report validated issues and separate
optional suggestions. Preserve the working tree.
```

For a PR or branch, specify the PR URL or base/head refs. The skill resolves the
actual revisions and records what was reviewed; a missing base or inaccessible
PR produces a useful missing-input report rather than a guessed review.

## Behavior and boundaries

- **Read only:** inspect selected changes, applicable repository instructions,
  callers, schemas, tests, and supplied review comments. Return findings in chat.
  Do not edit source, format, install dependencies, commit, amend, rebase, push,
  merge, or post review comments. No fixes are applied by this skill.
- **Stable scope:** record revisions and the included local changes, including
  selected untracked files. Recheck before reporting; changed inputs invalidate
  affected findings and require a fresh scoped review.
- **Bounded work:** at most three discovery reviewers, one validation pass, and
  one targeted follow-up when evidence can resolve an uncertainty. There is no
  automatic fix-and-review loop.
- **Evidence:** report a concrete trigger, code location, impact, and supporting
  caller or contract. Style preferences stay optional. Missing runtime evidence
  is labeled; an empty finding list is not a correctness guarantee.

The manifest declares only `Read`. The package contains instructions, a license,
and synthetic evaluation material. It has no MCP server, app dependency, hook,
runtime package, telemetry endpoint, background task, or authentication flow.
The marketplace uses `ON_USE`; there is nothing to authenticate for this skill.

Source, session context, and tool results are processed by the user's configured
Codex model/provider under that environment's existing data controls. "Local"
describes the review context, not offline inference or on-device processing.
The plugin adds no upload or storage service. Optional PR reads use the user's
already available repository integration only for the declared PR; unavailable
access is reported. Never paste credentials or unrelated private material into
feedback. Feedback is an optional draft; nothing is sent automatically.

Repository commands can have side effects. This review workflow reads test code
and existing results by default. It runs a check only if that invocation is
already authorized and its execution is known to preserve the worktree and avoid
network or external writes. Otherwise it reports the check as unrun and suggests
the specific command for a separately authorized validation task.

## Review and evaluation

Read the [skill](skills/review-local-changes/SKILL.md),
[review contract](skills/review-local-changes/references/review-contract.md),
and [evaluation guide](evaluations/README.md). The bundled API example is
synthetic. Automated package checks establish packaging and fixture behavior;
they do not establish model accuracy, false-positive rate, or installed-client
compatibility. Human-run evaluation and a small pilot remain required before
making those claims.

The repository master command is `npm run test:local-review-workbench`.
Repository checks require Node.js 22.19 or newer; this is a contributor
requirement, not a runtime dependency of the installed skill.

Licensed under [Apache-2.0](LICENSE). There are no bundled third-party runtime
dependencies. Community marketplace inclusion and official directory review are
separate processes.
