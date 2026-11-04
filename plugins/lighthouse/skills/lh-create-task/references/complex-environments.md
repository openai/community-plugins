# Build tasks with shared worlds, tools and state

Use this reference for document workflows, simulated organizations, MCP apps,
mutable services and other tasks whose starting state is more than source code.
Build and qualify the complexity the user requested. Choose the smallest useful
fidelity that preserves the work: files, a stateful tool service, several real
services or a desktop environment, according to the behaviors being evaluated.

## 1. Separate the reusable environment from each task

Keep reusable images, service recipes, public tools and seed data under
`environments/`. Add each task's starting-state changes, request, solution and
checks to its native Harbor bundle. Follow [shared foundations](shared-foundation.md)
for build contexts and image provenance; keep exported tasks self-contained.
Share immutable assets and recipes, not the learner's mutable trial workspace.

Keep seed history distinct from events generated in the current trial. A prior
approval or canary in the seed is context, not proof the learner performed a new
required action. Related scenarios must state which facts changed and start
independently unless they are explicitly one ordered episode.

Keep a service's dependency lock in its own build context. For a standalone
nested Python service, `uv init --no-workspace` avoids silently adding it to the
benchmark's Harbor environment and changing that dependency closure.

Write the environment contract in the benchmark's existing documentation:

- What entities, files, policies and services exist, and which may change.
- How the base seed and task-specific changes produce the exact starting state.
- Which tools, identities, clock behavior and simulated actors the learner sees.
- How initialization, readiness, reset, evidence capture and verification work.

Keep cross-file facts coherent: identifiers, dates, references and permissions
must agree unless a deliberate discrepancy is part of the task. Ensure the
learner can obtain the information needed to succeed. Preserve realistic context
and distractors without embedding the answer or hidden rubric in seed data.

Keep stable entity IDs separate from display names across applications. Tool and
output contracts must make any required ID form clear; otherwise grade supported
representations of the same entity consistently.

## 2. Package the tools through supported Harbor capabilities

Use Harbor's [environment and special-feature guidance](../../../vendor/harbor/skills/create-task/SKILL.md)
for containers, Compose, MCP and readiness configuration. Check the benchmark's
locked Harbor source, selected backend and learner adapter before choosing a
feature; a newer public adapter does not establish support in this benchmark.

Give tools the operations the workflow needs, with defined state changes and
useful errors. Keep the service's authoritative state distinct from learner
reports about it. Expose schemas and normal usage guidance, not evaluator
controls or oracle actions. Optional learner skills describe how to use the
available environment; they do not replace the tool implementation. Record their
contents and enabled versions as part of the tested agent setup.
For fields with a closed set of public codes, expose those values in the tool
schema or reject invalid writes with a useful error. A permissive string field
can accept prose that later violates a documented classification-code contract.

Define absence and conditional requirements in the public tool/data contracts,
such as no assignee for deferred work. Grade the facts applicable to that state;
do not turn different representations of absence across JSON and CSV into a
business failure. Scheduled work must still identify a valid worker.

Pin runtime, service, seed, tool and simulator identities where they affect
results. Record model/settings and clock behavior for simulated collaborators.
Smoke-test tool discovery and representative reads, writes and failures through
the actual learner adapter. A healthy server alone does not prove usable tools.

## 3. Prove the starting state and isolation

Initialize a fresh trial, check its required data and service behavior, mutate it,
then reset or recreate it and confirm the original state returns. Test through
the configured task user and supported Harbor lifecycle. Retain the observations
and failed setup logs with the environment evidence.

When parallel trials are intended, demonstrate that one trial's writes and reset
do not affect another. If services must be shared, document that constraint and
use a compatible sequential job configuration until isolation is demonstrated.
Check readiness after seeding; an open port does not establish data readiness.
Make a required service unavailable and confirm the native readiness check stops
before learner setup or execution. Cleanup may still export sealed baseline
state; those artifacts do not prove that a learner ran.

## 4. Grade outcomes with sufficient evidence

Use [construction](construction.md) to map the request to observable promises.
Check produced artifacts for their substantive contents, required final state
and prohibited side effects. Check intermediate actions only when the request
or supplied policy requires them; accept valid routes with different tool calls.
Keep deterministic observations separate from genuinely subjective judgments.

Prefer declared artifacts and a separate verifier when files or exported state
fully express the result. Capture required deliverables and a trusted service
export or event record; do not treat a learner-written success summary as service
state. Establish who writes each evidence item and test that the learner cannot
replace it with a fabricated pass. Follow [artifact capture](shared-foundation.md#4-declare-the-submitted-source)
for collection and restore checks, adapting the declared paths to this task.

For multiple state owners, use distinct absolute artifact source paths and prove
the offline restoration layout. A different host destination does not necessarily
change the path restored by Harbor. Carry owner, scenario, version and content
identities through outputs and cross-application references.

Serialize export finalization with all writers, including separate processes and
in-flight tool calls. A process-local lock cannot protect a separate export hook.
Test the chosen seal or transaction boundary and rejected writes after it. When
grading order across services, retain trusted evidence of the required ordering;
unrelated local sequence numbers or learner-authored timestamps are insufficient.

Use native supported live-state verification when the requested behavior needs
the running environment. Document the exact state inspected, verifier access
and trust limits. Do not replace that workflow with a file-only task merely to
simplify packaging, or claim a saved replay that the retained evidence cannot
support. A learner's missing required deliverable can be a task failure; failed
collection or unavailable service evidence leaves the grade incomplete.

For an export-based verifier, run native regrading on a saved submission and
confirm it checks the same evidence without rerunning the learner or silently
recreating missing state. For live-state checks, test repeatability on clean
trials and state whether saved regrading is supported. Retain original results.

Run a collection-failure control too: omit the trusted service export or make its
collection fail, then confirm the native result is incomplete rather than a scored
learner failure. Collection hooks and downloads can fail without stopping later
verification; inspect the artifact manifest and require the expected state identity.

## 5. Qualify the complete workflow

Apply [evaluator review](review-workflow.md) to the actual environment: reference
and valid alternative, no-op, meaningful wrong outcomes and a weakened checker.
Include environment-specific failures such as stale state, an omitted required
deliverable or an unauthorized record change when the request makes them relevant.
Confirm controls fail because of the intended criterion, not setup errors.

An extended agent episode can remain one Harbor task with one request and final
verification. Use native [multi-step tasks](../../../vendor/harbor/skills/create-task/SKILL.md#multi-step-tasks)
only when ordered requests, separate phase grades or early stopping are part of
the intended evaluation. Lighthouse's current [metrics renderer](../../lh-run-bench/references/metrics.md)
supports single-step Codex trials; retain native step results and disclose that
reporting limit for multi-step tasks instead of flattening them into that report.

Record qualified versions, backend requirements, reset/isolation evidence,
control results and any replay limits in the existing task review and environment
docs. Keep the native task runnable without Lighthouse. Continue through the
requested learner runs and handoff; packaging and oracle success are not the
completed benchmark.
