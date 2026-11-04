# Environments

Tasks share a dependency image when their runtime and lockfiles are compatible.
Each trial receives its own starting source/data and isolated writable state. No
environment has been built yet. Record the selected Harbor version, agent and
backend, and verify their support for the tools and services a task needs.

## Build a shared base

For each compatible dependency family, create `environments/<family>/` with its
Dockerfile, dependency locks and a short README. Record:

- The supported source/data revisions and target architecture.
- The exact build command and resulting image ID or registry digest.
- A public smoke command, run as the actual task user.
- How another worker can rebuild or retrieve the image.

Include runtimes, dependencies and public tools. Keep task source/data, answer
history and private tests out of the dependency base, including earlier Docker layers.

## Add starting inputs

Put the exact source archive or data snapshot inside the task's `environment/`
build context. Its Dockerfile adds those inputs on top of the shared base. Record
the source commit or corpus hashes and generation recipe/seed in the task README.
Keep identifiers and relationships consistent across document and tool views.
Do not fetch moving branches or regenerate random data during measured runs.

For export-based grading, build a separate verifier from compatible dependencies and trusted tests, with
an empty destination for submitted outputs. Restore only the artifacts captured
by Harbor so learner deletions remain deletions. Bake dependencies into the image
before offline verification.

## Add tools and services when required

Keep tool schemas, service code, initial state and dependency locks with the task
or its shared environment recipe. Use native Harbor configuration supported by
the pinned runtime and selected backend. Test actual tool discovery, healthchecks
and calls through the selected agent before qualifying a task.

Document the task's role, allowed operations, fixed time where relevant, and how
each trial starts from fresh state. Reuse images, not writable service instances.
Keep private answers and verifiers out of learner-accessible tools and storage.

For tasks that change service state, identify how the verifier obtains trusted
state. Exercise reset, capture and saved regrade for export-based verification;
ordinary workspace collection does not automatically capture another service.
If native live-state verification is required, test it before teardown and retain
its observations. Document whether saved regrading is supported. Record unsupported
backend or replay behavior instead of claiming it works.

## Handoff

A local image tag or ID is enough for local authoring. A released benchmark needs
retrievable registry digests or documented reproducible build inputs. Record image
identities in each experiment; a working local cache does not prove another worker
can run the tasks. Publish images only when authorized.
