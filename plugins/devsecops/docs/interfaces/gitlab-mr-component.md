# Bounded owned-project MR component

This component implements MR creation, required CI gate observation, one
source-pinned fast-forward merge and exact branch readback. It is deliberately
unregistered: no CLI, installed-package preflight or canonical factory phase is
activated by these files. A successful component receipt is not a factory
delivery grant, controller observation or complete demo recording.

The implementation contract is **GitLab 19.5.3 Community Edition, REST v4**.
The shared `gitlab_api.GitLabSession` captures one explicit environment PAT and
one TLS trust snapshot. It checks the actual metadata version/edition, current
active user and the same PAT's active, nonrevoked, unexpired `api` scope before
every POST or PUT. It does not create credentials or derive permission from a
saved report. Exact HTTPS origin, request count, elapsed time, per-body and total
byte budgets apply. DNS resolution uses the existing bounded resolver; a timed
out DNS worker cannot connect or send credentials. Redirects and proxies are not
followed. Error messages exclude remote diagnostics and credentials.

## Interfaces

`GitLabSession(origin, token_env=..., trust=..., expected_user_id=...)` provides
`metadata()`, `principal()`, `verify_identity()`,
`request(method, path, payload=None, expected=(200,))`,
`get_collection(path, max_pages=8, max_items=800)` and `stop_writes()`.
Trust is the existing `{'mode': 'system'}` or pinned absolute bundle contract.
Collections require explicit complete GitLab pagination; server link URLs are
never used as request destinations. HTTP errors expose only a safe code,
status and `outcome` (`none` or `unknown`). An unknown mutation or semantic
readback mismatch stops all later session writes while allowing bounded GETs.

`MRSelection` independently names one personal owned project and namespace,
one existing review branch, the watched default branch, exact source and
baseline target SHAs, required job names and the run identity.
`MergeRequestWorkflow(session, selection, journal_root=...)` exposes:

- `create()`: verify current personal ownership, permissions, project policy,
  both branch SHAs and absence of any MR for that branch pair; retain intent;
  create once and read back the exact MR and branches.
- `observe_gates()`: observe the same source/target/project/MR, mergeability,
  resolved discussions and diff SHAs; bind its head pipeline to a successful
  detached `merge_request_event` pipeline for the exact reviewed SHA; require
  every selected job to be successful, nonoptional and bound to that pipeline.
  Complete pagination, unique job identities and no child/trigger pipelines are
  required. Pending or malformed evidence stops the attempt; it never triggers,
  retries or plays a CI job.
- `merge()`: collect current gates again; retain intent; send exactly one merge
  request with `sha=<reviewed source>`, `auto_merge=false`, `squash=false` and
  source deletion disabled; verify the merged MR, unchanged project policy,
  same successful head pipeline and target/source branch equality afterward.
- `reconcile()`: after uncertainty, GET current MR candidates and target branch
  state. A matching state is an observation, not proof of who performed a write.
  Absence never proves nonexecution, and reconciliation never reopens writes.

`reconcile_selection(session, selection)` supports that same read-only
observation after process loss. It cannot reconstruct a writable workflow.

The caller supplies one stable, owned journal root. A deterministic guard for
origin/project/source branch/target branch is exclusively created and fsynced
before service activity. Changing run ID, SHA, principal or gate ordering cannot
reset that guard. Existing guards always reject workflow reentry. Intent and
response events are exclusive, fsynced files; evidence is audited before each
subsequent effect. Use a fresh review branch for a distinct legitimate change.
Removing or changing an old guard is a separate explicit owner recovery action.

## Server policy and concurrency limits

The project must be private, active and owned by the selected personal user,
with maintainer-or-higher project access. The target must be the protected
default branch. Project policy must select fast-forward merges, prohibit
squashing, require successful pipelines and resolved discussions, reject skipped
pipelines and retain source branches. Bootstrap sets and verifies the project
merge settings when creating the owned projects; it does not configure or
verify the exact protected-default-branch policy inherited from the instance.
Before live use, the owner must establish and verify the selected protected
`main` policy and the selected principal's actual merge permission. The MR
component checks current target protection and never relaxes these settings.

GitLab's merge `sha` parameter is an atomic **source** HEAD precondition. This
API does not supply a target SHA or exact pipeline-ID compare-and-swap. The
component makes no such claim: pre/post reads detect observed divergence and
fast-forward policy prevents a non-fast-forward merge, but those reads do not
create an atomic target/pipeline precondition. Project policy itself is also
observed, not atomically pinned by this API. If the canonical harness requires
stronger concurrency semantics it must not activate this component until an
appropriate server-side contract is available. No human approval requirement is
claimed from a CE CI gate, and this component never approves on a human's behalf.

## Qualification boundary and remaining integration

Tests use a scripted HTTPS/socket boundary with synthetic service responses and
synthetic credentials. They demonstrate bounded client behavior, source/target,
project, pipeline and job binding, denied permissions, malformed responses,
readback divergence, interruption and uncertainty handling. They do not qualify
a real GitLab server, runner, certificate, CI configuration or owned lab.

Before live activation, the canonical harness still needs its current
action-time admission and direct reviewed-change/branch-writer authority bound
to this selection, a stable run journal root, real owned project inventory,
verified selected-server responses and detached MR CI rules. Seed the source
project's `.gitlab-ci.yml` with the reviewed publication recipe and its direct
MR-only rules. Seed the deployment project with a separate reviewed
`.gitlab-ci.yml` containing deployment validation jobs whose exact names match
that project's `MRSelection.required_jobs`. Those jobs validate the reviewed
deployment change; they must not publish the application a second time. Owned
project creation alone supplies neither this CI seed selection nor the protected
`main` qualification. The automatic MR
pipeline must actually report the documented `refs/merge-requests/<iid>/head`
ref for the exact source SHA. The harness must then compose post-merge CI and
controller evidence and complete owned cleanup. This module performs none of
the existing branch-push primitive's work and emits no replacement authority.

Public primary sources checked on 2026-10-30:

- [GitLab 19.5.3 release](https://docs.gitlab.com/releases/patches/patch-release-gitlab-19-5-3-released/)
- [Version and edition metadata](https://docs.gitlab.com/api/metadata/)
- [PAT self-information and scopes](https://docs.gitlab.com/api/personal_access_tokens/)
- [Project merge policy](https://docs.gitlab.com/api/projects/)
- [MR creation, pipelines and source-SHA merge precondition](https://docs.gitlab.com/api/merge_requests/)
- [Pipeline identity and status](https://docs.gitlab.com/api/pipelines/)
- [Pipeline jobs and trigger jobs](https://docs.gitlab.com/api/jobs/)
