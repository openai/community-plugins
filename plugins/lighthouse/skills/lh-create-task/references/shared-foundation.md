# Share dependency images across tasks

For repository tasks, use ordinary Docker layers: one source-free dependency image for compatible
tasks, then a thin layer containing each task's exact starting source. Containers
and workspaces remain separate. For stateful services or workflow assets, use
[complex environments](complex-environments.md) to choose the starting-state and
verification contract; apply the build-context and capture guidance here where relevant.

## 1. Build the dependency foundation

Group starting revisions by compatible runtime and dependency locks. Keep each
foundation's Dockerfile, lockfiles and build instructions in
`environments/<dependency-family>/`. Include the runtime, locked dependencies
and public tools; keep private tests, solved source and Git history out.

```sh
docker build -t repo-bench-base:local environments/DEPENDENCY_FAMILY
docker image inspect repo-bench-base:local --format '{{.Id}}'
```

Do not copy source into a base layer and delete it later; the earlier layer still
contains it. Install dependencies into a reusable location appropriate to the
repository. A changed dependency closure may require another foundation.

Local tags are useful during authoring. Record the resolved image ID with runs.
For a release or another worker, provide a retrievable image digest or exact
build inputs and an image export/import procedure. Publishing an image requires
separate authorization.

## 2. Add the starting source

Keep the archive inside the task's environment build context:

```sh
git -C SOURCE archive --format=tar START_COMMIT > TASK/environment/source.tar
```

A thin task Dockerfile can then use:

```dockerfile
FROM repo-bench-base:local
WORKDIR /app
ADD source.tar /app/
```

Use the exact starting commit, not working-tree edits or a moving branch. Review
the archive for answer-bearing fixtures and tracked secrets. Git archives omit
history; tasks needing submodules, LFS or selected history need explicit pinned
assets and their own preparation recipe.

Connect the source to its locked dependencies using the repository's supported
setup. Keep setup in task assets. Never rely on `COPY ../../...` or external
symlinks that are absent from Harbor's build context.

State the dependency policy in `instruction.md`: a fixed verifier image requires
an explicit existing-dependencies-only constraint, unless the task supplies a
deterministic way to verify dependency changes.

## 3. Smoke-test the agent environment

Use Harbor's [interactive environment command](../../../vendor/harbor/skills/create-task/SKILL.md#step-3-build-the-environment)
with the benchmark's locked dependency. Test imports, public commands and required
cache writes as the configured task user. A successful image build alone does not
show that the agent can work.

Preserve failed logs and resolve setup problems before spending time on private
controls. Keep public smoke helpers outside the source snapshot when they are
not part of the learner's intended inputs.

## 4. Declare the submitted source

The separate verifier receives declared artifacts, not the whole agent workspace.
For a repository rooted at `/app`, add this to `task.toml`. Keep `[[artifacts]]`
at the top level, alongside `[task]`:

```toml
[[artifacts]]
source = "/app"
destination = "app"

[environment]
network_mode = "public"

[verifier]
environment_mode = "separate"

[verifier.environment]
network_mode = "no-network"
```

Harbor 0.23.0 saves this artifact under the trial's host `artifacts/app` and
restores it to its original `/app` path in the separate verifier. `destination`
controls host placement only. Capture every source and output file the checks
need, including newly created files. Exclude only
rebuildable caches or dependencies after proving that the verifier does not need
their submitted bytes. Before qualification, run task-owned probes for added and
deleted files, relevant executable modes/symlinks and a missing required artifact.
Check the restored bytes and the native `artifacts/manifest.json` entries: required
artifacts need successful collection. An existing directory alone is insufficient;
Harbor can leave one behind after a failed download.

Single-step regrade in Harbor 0.23.0 dereferences saved symlinks. Keep failed
probes and inspect the actual submission before replay. This version does not
qualify replay for tasks whose results require symlink identity.

Use separate environment baselines for public-network agent execution and offline
verification.

## 5. Build a trusted verifier

`tests/` is the verifier image's build context. Start its Dockerfile from the
compatible dependency foundation and copy trusted tests to `/tests`. Harbor
expects `/tests/test.sh`; use absolute paths in that script.

Harbor empties the declared directory before restoring its captured contents.
Keep the verifier image source-free, and attach trusted dependencies after that
restore when needed. Check `/app` directly; do not overlay a baseline source tree
or look for declared source under `/logs/artifacts`. The latter is Harbor's
separate convention for agent-written artifacts. Missing required artifacts are
an incomplete trial, not an empty or baseline submission to grade.

Keep assertions, fixtures and runner configuration in the verifier image. Run trusted
checks against submitted source; do not accept learner-edited
tests or runner configuration as proof of success. Test this boundary with an
adversarial control during [evaluator review](review-workflow.md).

Retain the agent image, verifier image and dependency identities alongside native
run results. Run one saved submission through native regrading before considering
the artifact/verifier contract ready for a benchmark handoff.
