# Follow Terminal-Bench's layout

Use Terminal-Bench as a readable example of a benchmark repository: a concise
README, a task directory per problem, human-readable instructions and native
Harbor configuration. The references below are pinned to commit
`3ef4bf2b79263f0c31a3f114a564087c3dda72fa`.

| Reference | Use it for |
| --- | --- |
| [README][readme] | Purpose, task discovery and commands near the top |
| [A complete task][task] | `task.toml`, instructions, environment, solution and tests |
| [Cargo flight dispatch][cargo] | Symptoms, input files, deliverable and operational constraints |
| [Session window debug][sessions] | Observed failures and an existing design document to investigate |
| [Payments pipeline fix][payments] | A short request with a measurable user outcome |
| [Task configuration][config] | Declared source artifact and separate verifier |
| [Release workflow][release] | Tagged benchmark versions tied to prepared images |

## Repository layout

```text
<repo>-bench/
  README.md                  # Purpose, setup and task index
  AGENTS.md                  # How to continue work here
  pyproject.toml + uv.lock    # Exact Harbor runtime
  docs/                      # Methodology, environments and releases
  environments/<family>/     # Shared dependency image recipes and locks
  tasks/<task>/
    task.toml
    instruction.md           # Realistic learner request and essential constraints
    README.md                # Provenance, environment, checks and commands
    environment/             # Starting source and thin Docker layer
    solution/solve.sh
    tests/                   # Separate verifier image and checks
  review/                    # Discovery, selection and qualification evidence
  experiments/               # Native Harbor configs and comparison notes
  leaderboard/               # Tables derived from saved run evidence
  jobs/                      # Local raw Harbor output, ignored by Git
  .local/                    # Local source bindings and drafts, ignored by Git
```

Create task and environment directories when their contents exist. Each task's
build context must contain its source inputs; do not copy files through `../../`
or depend on another checkout. Share Docker dependency layers, not writable source
directories. See the generated [environment guide](../assets/bench/docs/environments.md).

## Task instructions

These examples give the learner a problem to investigate. Cargo dispatch includes
input locations and necessary flight constraints; session-window debugging points
to the supplied design; the payments task states the required notification outcome
and timing. None exposes criterion IDs or reward calculations.

Match that separation, not a fixed word count. Keep the detailed evaluator rubric
and control matrix in curator review files. The learner needs enough information
to determine correctness, without a checklist of the private tests or the solution.
See [task construction](../../lh-create-task/references/construction.md).

## Version the benchmark

Terminal-Bench develops tasks continuously and publishes tagged releases. Use the
same basic model locally: the `tasks/` tree at a full Git commit defines membership;
a release tag names that commit. Record the separate Harbor task version and exact
image identities with results. The generated [release guide](../assets/bench/docs/releases.md)
defines the convention, including results from an uncommitted local benchmark.

The reference repository also publishes datasets and images. Those are optional
distribution steps. A local benchmark needs the pinned tasks, retrievable or
rebuildable images, its lockfile and native Harbor commands. Do not copy upstream
cloud settings, model choices, concurrency or CI publishing workflows by default.

[readme]: https://github.com/harbor-framework/terminal-bench/blob/3ef4bf2b79263f0c31a3f114a564087c3dda72fa/README.md
[task]: https://github.com/harbor-framework/terminal-bench/tree/3ef4bf2b79263f0c31a3f114a564087c3dda72fa/tasks/batched-eval-parity
[config]: https://github.com/harbor-framework/terminal-bench/blob/3ef4bf2b79263f0c31a3f114a564087c3dda72fa/tasks/batched-eval-parity/task.toml
[release]: https://github.com/harbor-framework/terminal-bench/blob/3ef4bf2b79263f0c31a3f114a564087c3dda72fa/.github/workflows/release-prebuilt.yml
[cargo]: https://github.com/harbor-framework/terminal-bench/blob/3ef4bf2b79263f0c31a3f114a564087c3dda72fa/tasks/cargo-flight-dispatch/instruction.md
[sessions]: https://github.com/harbor-framework/terminal-bench/blob/3ef4bf2b79263f0c31a3f114a564087c3dda72fa/tasks/session-window-debug/instruction.md
[payments]: https://github.com/harbor-framework/terminal-bench/blob/3ef4bf2b79263f0c31a3f114a564087c3dda72fa/tasks/payments-pipeline-fix/instruction.md
