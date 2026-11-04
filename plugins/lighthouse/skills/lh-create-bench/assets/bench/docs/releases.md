# Releases

Use Git releases to name a fixed task collection. No release exists yet.

## Keep identities separate

| Identity | Record |
| --- | --- |
| Authoring workflow | Lighthouse plugin version and commit, when available |
| Benchmark | Full Git commit and optional release tag |
| Task | Stable name, Harbor task version and evaluated file hashes |
| Runtime | Harbor lockfile, installed Codex version and model/settings |
| Environment | Base, task and verifier image identities |
| Result | Native job/trial IDs, config and saved artifacts |

For a local benchmark without commits, label results `unreleased` and retain an
exact task snapshot with file hashes and the lockfile. Do not invent a commit or
mix those results with a tagged release. Commit only when the user requests it.

## Name a release

The `tasks/` tree at the release commit defines membership. Confirm each task's
qualification evidence and rebuild/retrieval instructions before release:

- Patch: fix a task or checker without changing its intended scope.
- Minor: add tasks.
- Major: change the benchmark's purpose or scoring contract.

Use tags such as `v0.1.0`; resolve them to full commits in experiment records.
Increment Harbor `[task].version` when instructions, environment, solution or
verifier change. Harbor's `schema_version` describes the file format, not the task
revision. Never move an existing release tag to different bytes.

## Correct results

Even a checker correction can change scores. Keep result tables separated by
benchmark version and preserve old results. Document what changed and why.

Regrade saved submissions only if they contain the inputs needed by the corrected
verifier and the public task is unchanged. Preserve the source result and record
the new evaluator identity. New public requirements need new learner runs.

Publishing a Git release, image or Harbor Hub dataset is optional and requires
authorization. A pinned local checkout remains a usable benchmark.
