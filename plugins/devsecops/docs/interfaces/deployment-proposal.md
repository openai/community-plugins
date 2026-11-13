# Verified image to a deployment Git proposal

`devsecops.factory.deployment_proposal.propose_deployment_change` takes a current
same-process `PublishedMission` and prepares a local, reviewable deployment
change. It uses the verified image repository and manifest digest directly. It
does not resolve a tag, copy a registry image, re-sign evidence, write a remote
Git commit, open or merge a merge request, or claim a deployment occurred.

This is the proposal API for the subsequent operator delivery backend. The
`scripts/publish_and_propose_mission.py` command keeps publication and proposal in one process;
it does not accept a saved publication receipt. JSON files cannot recreate
publication or proposal authority. The existing recovery correction
validator still requires unchanged images; this separate proposal path does not
weaken that contract.

## Exact scope

The operator selects an absolute local deployment repository, a full immutable
commit ID, a mission chart path, one values file, a release and namespace, and the
two explicit selectors `image.repository` and `image.digest`. The chart must
match the shipped `mission-status` chart, templates and schema. Customer values
can differ; modified chart implementations are not supported by this slice.

The values file can be the chart's `values.yaml` or a separate overlay. Both image
fields must already be unambiguous string scalars. The editor replaces only those
scalar spans, preserving all other bytes, comments, quoting, Unicode and line
endings. Aliases, anchors, merge keys, duplicate keys, multiline target scalars,
multiple documents and unsupported paths are rejected. Identical image values
produce an explicit `change_required: false` and an empty patch; a downstream
delivery backend must not create a commit for that result.

The baseline must already be a valid deployment configuration with a digest-pinned
image. Empty starter values do not establish a valid baseline. The selected
architecture must match the published image's known build architecture. The
rendered node selector records desired placement; it does not prove the
architecture of a running container.

## Real local validation

The existing frozen Git snapshot reader selects only the chart and values at the
exact commit, without checkout, hooks, filters or repository writes. Approved
Helm and Kubeconform executables are copied to private scratch and checked against
their independently selected SHA-256 pins and the packaged toolchain versions.
The operator approves executable identity; this operation does not re-download
and independently authenticate their original release archives. Do not use a
binary or schema path from the candidate deployment checkout.

The existing Helm adapter runs strict lint and repeated offline template
rendering on both baseline and proposal. Plugins and Kubernetes credentials are
disabled. The existing Kubeconform adapter checks both renderings against exact
lock-hashed local schemas. Explicit Kubernetes selections **1.34.0** and
**1.36.5** each select their own packaged 24-schema set. The latter matches the
Kubernetes version of the retained RKE2 candidate; use `1.36.5` without the RKE2
distribution suffix. Neither selection falls back to another version, downloads
schemas during review, or claims CRD coverage. Other versions leave the proposal
incomplete until their schema set is separately qualified and packaged.

All resource identities and rendered fields must remain equal except the
selected Deployment's single `mission-status` container image. The new image must
equal the published `repository@sha256:digest`. This also catches an accidental
extra edit in the scalar editor. The proposal uses existing rendering/schema
code with an additive optional overall deadline; ordinary review callers keep
their existing behavior and per-command cap.

## Operator configuration

Select and hash the exact configuration bytes independently of the deployment
checkout. Replace these illustrative paths and pins; placeholders do not pass
validation.

```json
{
  "schema_version": 1,
  "kind": "mission-deployment-proposal-configuration",
  "deployment_repository": "/approved/deployment-repository",
  "deployment_commit": "FULL_IMMUTABLE_COMMIT_ID",
  "chart_path": "charts/mission-status",
  "values_path": "environments/lab.yaml",
  "image_keys": {
    "repository": ["image", "repository"],
    "digest": ["image", "digest"]
  },
  "release": "mission-status",
  "namespace": "mission",
  "kubernetes_version": "1.34.0",
  "helm_path": "/approved/tools/helm",
  "helm_sha256": "APPROVED_HELM_EXECUTABLE_SHA256",
  "kubeconform_path": "/approved/tools/kubeconform",
  "kubeconform_sha256": "APPROVED_KUBECONFORM_EXECUTABLE_SHA256",
  "schemas_path": "/approved/schemas"
}
```

The schema path is the directory containing the selected
`v1.34.0-standalone-strict/` or `v1.36.5-standalone-strict/` directory. Source
paths within the repository use normalized literal ASCII paths; the repository
itself may be in an absolute path containing spaces. Tool paths must point to
actual files, with no symlink ancestors.

## Executable same-process handoff

`scripts/publish_and_propose_mission.py` calls
`devsecops.factory.mission_proposal_operation.qualify_publish_and_propose`. It
accepts the five independently pinned configurations used by
[`scripts/publish_mission.py`](../../scripts/publish_mission.py), plus
`--proposal-config` and `--proposal-config-sha256`. The remaining options are
`--output`, `--runtime-root`, `--timeout` (default 3500 seconds, bounded to
246–3600), and optional `--expected-source-repository` / `--expected-source-revision`.
All paths and pins must come from the explicitly authorized operator configuration.

All six configuration files are frozen and hash-checked before publication can
write to the staging registry. The proposal document is structurally validated
and its schema version checked against the packaged lock at that point. The
proposal's actual Git snapshot, chart, tool and schema validation still occurs
after publication; a later failure preserves the upstream mutation receipts and
does not retry or roll back registry writes automatically.

The current `PublishedMission` object is passed directly to the local proposal
API. Both capabilities are revalidated after the aggregate terminal receipt is
written. A missing capability, controlled input, expiry, changed scope or changed
frozen configuration cannot become a passing proposal. One overall deadline
bounds both stages. The command never accepts a receipt JSON as authority or
silently reruns publication to reconstruct a lost process-local object.

Outputs are `publication/` (the existing publication operation and its child
evidence), `proposal/` (the local patch and validation), and
`mission-proposal-operation.json` (the sealed aggregate receipt). Exit 0 means
the current local proposal passed, 1 preserves a completed upstream/policy
failure, and 2 means incomplete/unknown. `factory_decision` remains `unknown`;
Git commit/MR/merge, promotion, deployment and CI execution are not established.
This command must only run with authorization for its upstream registry writes.

The shipped [GitLab recipe](../../demo/software-factory/ci/gitlab-ci.yml) invokes
this command in its existing publication job. The operator must supply
`DEVSECOPS_PROPOSAL_CONFIG` and `DEVSECOPS_PROPOSAL_CONFIG_SHA256` alongside the
five existing configuration pairs. The proposal configuration selects an existing
local deployment checkout at an immutable commit, approved tools and local
schemas accessible to the owned runner; the job does not fetch or prepare them.
All six pins are admitted before upstream registry mutation. The source build
is still bound to `CI_PROJECT_DIR` and `CI_COMMIT_SHA`.

The job retains only the aggregate operation receipt, publication operation and
publication receipts, proposal receipt and patch under `mission-proposal/`.
Files exist only for stages actually reached; a missing proposal is never filled
in or treated as success. Private execution trees, frozen configurations, whole
values files and rendered manifests are excluded from the artifact list. These
artifacts support review; they cannot recreate either process-local capability.
Git delivery remains unimplemented by this job. This is offline-tested CI wiring,
not evidence of a live GitLab run, Git commit, merge request or deployment.

The same-process Python API can also be called immediately after successful
direct publication:

```python
from pathlib import Path
from devsecops.factory.deployment_proposal import propose_deployment_change

# published is the direct object returned by the current publication operation.
bound = published.describe()
report, proposal = propose_deployment_change(
    published=published,
    **{name: bound[name] for name in (
        "run_scope", "source_commit", "architecture", "origin", "repository",
        "policy_sha256", "trust_policy_sha256"
    )},
    publication_config_sha256=selected_publication_configuration_sha256,
    config=Path("/approved/deployment-proposal.json"),
    expected_config_sha256=selected_configuration_sha256,
    output=Path("/private/run/deployment-proposal"),
    timeout=300,
)
```

## Review artifacts and acceptance

The output includes `proposal.patch`, exact before/after values, normalized
before/after rendered resources, selected configuration/toolchain, and
`deployment-proposal.json`. The receipt binds the deployment commit and frozen
file hashes, actual source/build identity, published evidence manifest, signer,
scan database/tools/policy, patch hash and render/schema checks. Deterministic
inputs produce identical patch and normalized rendered bytes; timestamps and
tool durations remain observation metadata.

Publication evidence is audited before and after validation. Frozen input,
schema, executable and output bytes are rechecked, including after the terminal
receipt is fsynced. Expiry or changed evidence withdraws that terminal receipt.
`DeploymentProposal` is process-local, non-serializable and expires no later than
the current publication. Reports alone never authorize delivery.

Tests use a real Git repository, real pinned Helm and Kubeconform, and controlled
published evidence generated through actual local TLS/Cosign publication. They
independently apply the patch with Git and check the original checkout is
untouched. Controlled evidence cannot issue a production proposal capability.
Live CI, remote Git delivery, merge gates, controller reconciliation and running
image assertions remain separate acceptance steps.
