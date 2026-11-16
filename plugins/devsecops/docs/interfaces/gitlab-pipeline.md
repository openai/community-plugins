# Exact GitLab pipeline acquisition

`collect_gitlab_pipeline` performs bounded authenticated reads of one explicitly
selected GitLab project, pipeline and full commit SHA. It retains exact API and
selected artifact bytes in a new private directory. It does not trigger, retry,
cancel, approve or modify a job, pipeline, repository, registry or environment.
The existing MR-oriented `devsecops.gitlab.GitLabClient` is unchanged.

The installed command uses the runner's approved external Python environment:

```sh
"$DEVSECOPS_PYTHON" -B "$PLUGIN_ROOT/scripts/collect_gitlab_pipeline.py" \
  --origin "https://gitlab.example.test" \
  --project-id 7 --pipeline-id 42 --expected-sha "$EXACT_SOURCE_COMMIT" \
  --token-env DEVSECOPS_GITLAB_READ_TOKEN \
  --ca-bundle "/approved/lab-ca.pem" \
  --artifact "99:mission-proposal/publication/publication/mission-publication.json" \
  --output "/private/new-ci-evidence" --timeout 60
```

Omit `--ca-bundle` for system trust. Repeat `--artifact JOB_ID:RELATIVE_PATH` for
up to eight exact selections, or omit it to collect pipeline/job metadata only.
`--token-env` is a variable name, never a token value. The command accepts no
imported JSON report or latest-ref selector. It prints the normalized acquisition
report and exits 0 for its narrow `pass`, 1 for `fail`, and 2 for `unknown` or an
input/acquisition error. Parser and runtime errors omit supplied values and raw
diagnostics. The existing API's request, byte, path, pagination and time bounds
remain in force; the command does not expand them.

The same collector is available to Python callers:

```python
from pathlib import Path
from devsecops.adapters.gitlab_pipeline import collect_gitlab_pipeline

report = collect_gitlab_pipeline(
    origin="https://gitlab.example.test",
    project_id=7,
    pipeline_id=42,
    expected_sha=exact_source_commit,
    token_env="DEVSECOPS_GITLAB_READ_TOKEN",
    ca_bundle=Path("/approved/lab-ca.pem"),  # None uses system trust.
    artifact_requests=({
        "job_id": 99,
        "path": "mission-proposal/publication/publication/mission-publication.json",
    },),
    output=Path("/private/new-ci-evidence"),
    timeout=60,
    max_pages=10,
)
```

The token is supplied by one explicit environment-variable reference, resolved
once and used as a Bearer credential for the exact selected HTTPS origin. Select
a read API token with access only to the intended project. No credential value or
private hash enters the normalized report. The CA is copied to a private frozen
file before constructing the verified TLS client and rechecked at completion.
TLS verification cannot be disabled. Ambient proxies, redirects, remote URL
fields, Link destinations and automatic authentication discovery are not used.
GitLab supports the Bearer header for personal, project, group and OAuth tokens;
this API does not implement the differently scoped CI job-token header.
[Authentication contract](https://docs.gitlab.com/api/rest/authentication/).

The caller selects an existing pipeline ID; the collector never substitutes the
latest branch pipeline or searches for a more convenient successful run. It reads
that pipeline before and after collection, matching project, pipeline and exact
SHA, then checks stable status, source and ref identity. Jobs are paged with
`include_retried=false`; the complete bounded job set is collected again after
artifact reads. Selected artifact jobs have separate before/after detail reads,
with matching pipeline/commit/status/runner identities. An omitted nested
`pipeline.project_id` is supported because the authenticated project-scoped route
already binds it; a conflicting supplied ID is rejected. Pipeline or job races,
changed job sets and foreign artifacts stay unknown.
[Pipelines](https://docs.gitlab.com/api/pipelines/),
[jobs](https://docs.gitlab.com/api/jobs/).

Only six bounded numeric `X-*` pagination headers are exposed by the shared
transport. Page, page size and next-page metadata are required; totals are
checked when present. Repeated IDs, skipped pages, changing totals, missing
metadata or a page limit cannot silently become complete coverage. Bridge jobs
are read before and after; a bridge or child/multi-project pipeline produces an
explicit coverage gap. No downstream pipeline is followed. Merged-result refs
likewise retain an uncollected-context gap.
[Pagination](https://docs.gitlab.com/api/rest/#pagination).

Artifacts use only the selected numeric job ID and normalized relative path,
never a latest-ref endpoint. At most eight paths are admitted, each at most 1,024
ASCII characters; traversal, encoded paths, absolute paths and empty components
are rejected before any network call. Responses are stored as inert opaque bytes,
not extracted or executed. Redirecting artifact/object-storage configurations
remain unsupported and unknown; configure the approved service for direct
same-origin delivery instead of forwarding credentials to a redirect.
[Artifact API](https://docs.gitlab.com/api/job_artifacts/).

Bounds are one 1–120 second deadline (default 60), at most 30 seconds per request,
64 requests, 64 MB of response bytes, 2 MB per API JSON document, 8 MB per selected
artifact and 1–10 pages of 100 entries per list. The same budget covers before/after
reads and artifacts. Large combinations can exhaust the request or byte budget
and remain unknown. No automatic polling or retry resets those budgets.

`gitlab-pipeline.json` contains selected identities, narrow status, safe gaps,
transport request/response hashes, pagination metadata, and content-addressed
file references. Full API responses may contain sensitive metadata and stay only
in private `sha256-<digest>.blob` files. File names are generated locally; no
response field controls a local path. The directory is mode 0700, data files 0600,
and the CA snapshot 0400. Terminal completion rechecks retained bytes, directory
identity and deadline; a failed terminal check withdraws the terminal report.
Partial failure retains its observed bytes and fixed safe error categories.

`decision: pass` means a stable successful pipeline with complete admitted
coverage; `fail` means a stable failed pipeline. Both are narrow observations.
`factory_decision` always remains `unknown`, and `origin_authority` is always
`not-issued`. A successful pipeline can contain skipped or allowed-failure jobs,
so it is not proof that a required factory job executed. The eventual trusted
consumer must require the exact intended publication job and its successful
execution, independently verify the trusted runner/configuration and actual
registry evidence, and bind all source, tool, image and artifact identities.
This module has no JSON-to-authority constructor and cannot restore any
process-local `PublishedMission` from an artifact file.

## Current CI recipe and registry compatibility

`demo/software-factory/ci/gitlab-ci.yml` now invokes the current installed
`scripts/publish_and_propose_mission.py` chain in one owned Linux shell-runner job. The operator
must configure `DEVSECOPS_RUNNER_TAG`, the verified plugin installation and external
Python environment, six independent configuration files and SHA pins, the new
owned runtime root, and credentials referenced by those configurations. Missing
configuration fails; no placeholder token, automatic install or fake passing gate
is provided. The source repository and SHA must equal `CI_PROJECT_DIR` and
`CI_COMMIT_SHA`. The sixth pair is `DEVSECOPS_PROPOSAL_CONFIG` and
`DEVSECOPS_PROPOSAL_CONFIG_SHA256`; it selects the existing immutable deployment
checkout, approved tools and local schemas on the runner. The job does not fetch
that checkout or bootstrap its tools. All six configurations are frozen and
hash-checked before upstream writes. The existing publication job name and stage
are retained; there is no separate job that imports saved authority.

The artifact allowlist retains only these files when they exist:

- `mission-proposal/mission-proposal-operation.json`
- `mission-proposal/publication/mission-publication-operation.json`
- `mission-proposal/publication/publication/mission-publication.json`
- `mission-proposal/proposal/deployment-proposal.json`
- `mission-proposal/proposal/proposal.patch`

The full private execution tree, frozen configurations, complete values files,
rendered manifests and signing credentials are excluded. `when: always` retains
available failure evidence; absent later artifacts do not become a pass. Exit 1
and 2 fail the job, with no allowed-failure setting or automatic retry.

A successful job establishes only that invocation's publication and local
proposal component outcome. It grants no Git commit, push, merge request, merge,
promotion or deployment authority. The uploaded reports and patch are review
evidence and cannot recreate `PublishedMission` or `DeploymentProposal` for a
later job. Exact-job CI authentication, Git delivery, canonical D1–D6 and hosted
acceptance remain separate. Offline shell tests verify argument expansion and
exit/artifact contracts; no live GitLab run is claimed by this recipe or those tests.

The first supported topology is **GitLab CI plus a separately configured staging
registry** that implements the required native OCI Referrers API and same-origin
authentication. GitLab's container registry documents support for OCI `subject`
but not the complete Referrers API. Its usual registry authentication realm is
also on the separate GitLab service origin; current staging deliberately rejects
that cross-origin credential path. Using a separate registry in a CI script does
not mean integrating it as a replacement for GitLab's native registry service.
No fallback tag or weakened publication check is silently selected.
[GitLab registry](https://docs.gitlab.com/user/packages/container_registry/),
[registry authentication](https://docs.gitlab.com/administration/packages/container_registry/).
