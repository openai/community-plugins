# Lighthouse

Build repository and workflow benchmarks as ordinary, independently runnable
Harbor repositories. Lighthouse guides task selection, environment and evaluator
authoring, controls, learner runs and evidence-based leaderboards. Explorer
browses local benchmarks in Codex.

## First run

After installing Lighthouse from the community-plugins marketplace, start a new
Codex chat. Try this read-only first prompt:

> Use $lh to explain how you would benchmark this repository and list the
> prerequisites you can verify. Do not create files, install dependencies or run
> models yet.

To start authoring, ask: “Use $lh to create a benchmark for this repository.”
For workflow tasks, provide a synthetic brief and the data and tools it needs.
Use $lh-open-explorer to browse an existing benchmark. Choose its folder in
the dashboard; alternatively set LIGHTHOUSE_BENCHMARK_HOME to a parent folder,
or LIGHTHOUSE_BENCHMARKS to explicit paths separated by the OS path delimiter.

## Prerequisites and boundaries

Codex and Node.js 22.19+ are needed for Explorer. Its committed bundle starts
without npm installation or network access. It reads the benchmark folder you
select, including curator rubrics and ignored jobs available on that machine.
It does not run jobs or expose those documents in model-visible MCP tool results.
Default discovery checks direct matching children under ~/code.

Skills may read source repositories, explicit local rollout files and supplied
data, and write a separate benchmark repository, review files and leaderboard.
Trace import preserves the selected raw rollout; use only files you intend to
retain locally. Do not include raw traces or customer data in public benchmarks.

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
- [lh-open-explorer](skills/lh-open-explorer/SKILL.md): browse saved evidence.

See the [Explorer guide](explorer/README.md) for launcher and data formats.

## Development and status

Use uv 0.11.33, the included Python pin and Node 22.19+:

```sh
npm --prefix plugins/lighthouse/explorer ci --ignore-scripts
npm run test:lighthouse
```

The master suite checks the committed cold MCP package before rebuilding,
then validates the Python lock, skill paths, Ruff, formatting, types and all
Python behavior tests, followed by Explorer type checks, build and tests.
No Docker, credentials or paid model run is required.

This is a preview. Prior synthetic and local Harbor exercises do not complete
the separate fresh-agent Grimoire acceptance requirement. Hosted CI and an
installed Desktop UI require their own verification. No missing evidence counts
as a pass.

Lighthouse is MIT licensed; see [LICENSE](LICENSE). Vendored Harbor references
retain their [provenance and Apache-2.0 license](vendor/harbor/README.md).
Explorer dependency comments are retained in the generated bundles; full
licenses and notices are in [THIRD_PARTY_NOTICES.md](explorer/dist/THIRD_PARTY_NOTICES.md).
