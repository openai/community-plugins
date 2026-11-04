# Lighthouse skills

Build repository and workflow benchmarks as ordinary, independently runnable
Harbor repositories. Lighthouse guides task selection, environment and evaluator
authoring, controls, learner runs and evidence-based leaderboards.

This contribution contains skills and Python helpers only. It has no UI, MCP
server, frontend dependencies or generated JavaScript bundles. Explorer is a
separate follow-on contribution; the skills work without it.

## First run

After installing Lighthouse from the community-plugins marketplace, start a new
Codex chat. Try this read-only first prompt:

> Use $lh to explain how you would benchmark this repository and list the
> prerequisites you can verify. Do not create files, install dependencies or run
> models yet.

To start authoring, ask: “Use $lh to create a benchmark for this repository.”
For workflow tasks, provide a synthetic brief and the data and tools it needs.

## Prerequisites and boundaries

Skills may read source repositories, explicitly selected local rollout files and
supplied data, and write a separate benchmark repository, review files and
leaderboard. Trace import preserves the selected raw rollout; use only files you
intend to retain locally. Do not publish raw traces or customer data.

Execution requires uv, the Python version in the generated benchmark and its
locked Harbor dependencies, plus a supported container backend and model access.
Harbor jobs may send task context and submissions to the configured model
provider; judge reviews may send code to their configured provider as well.
Dependency installation uses the registries in the benchmark locks; container
builds use the registries in its Dockerfiles. Installation alone does not
authenticate or run models. Supply credentials only through supported provider
configuration; do not write credentials into tasks, traces or the plugin.

Code-quality review currently requires `openai/gpt-6-sol` and
`openai/gpt-6-astra` at medium reasoning, as specified in the
[quality recipe](skills/lh-create-task/references/code-quality.md). These judge
models are fixed in this preview; arbitrary replacement judge models are not
supported. If either model is unavailable, quality is unmeasured and the
overall task cannot be marked passed. Missing containers, auth, files or
provider access must stop the affected stage with its evidence intact.
Static checks do not qualify a task. Each benchmark needs its own executed
reference, alternative and adversarial controls.

## Skills

- [lh](skills/lh/SKILL.md): route or resume.
- [lh-create-bench](skills/lh-create-bench/SKILL.md): scaffold a Harbor benchmark.
- [lh-identify-tasks](skills/lh-identify-tasks/SKILL.md): select useful tasks.
- [lh-investigate-traces](skills/lh-investigate-traces/SKILL.md): import explicitly selected local rollouts.
- [lh-create-task](skills/lh-create-task/SKILL.md): author and qualify one task.
- [lh-run-bench](skills/lh-run-bench/SKILL.md): execute and report.
- [lh-improve-harness](skills/lh-improve-harness/SKILL.md): test a bounded harness change.

## Development and status

Use uv 0.11.33, the included Python pin and Node 22.19+ for repository checks:

```sh
npm run test:lighthouse
```

The master suite checks the skills-only package boundary, Python lock, skill
paths, Ruff, formatting, types and all Python behavior tests. No Docker,
credentials, frontend installation or paid model run is required.

This is a preview. Prior synthetic and local Harbor exercises do not complete
the separate fresh-agent Grimoire acceptance requirement. Hosted CI and an
installed Desktop workflow require their own verification. No missing evidence
counts as a pass.

Lighthouse is MIT licensed; see [LICENSE](LICENSE). Vendored Harbor references
retain their [provenance and Apache-2.0 license](vendor/harbor/README.md).
