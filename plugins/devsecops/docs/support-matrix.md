# Support and coverage matrix

This matrix distinguishes implemented behavior, exercised paths and release
qualification. A packaged executable or schema is not evidence that every target
platform or Kubernetes behavior has been tested. See the
[validation record](test-validation.md) for execution evidence and open gates.

The tables below record the stable **v0.1 review workflow**. They do not qualify
the additional v0.2 development workflows. The preview adds Kustomize review,
GitOps diagnosis, correction validation, recovery evidence comparison and a
factory orchestration engine. See the [implementation ledger](demo/implementation-status.md)
for their separate component, native-host and live-demo evidence. In particular,
installing five skills does not establish a successful factory demonstration.

## Execution surfaces

| Surface | Implementation | Evidence and limit |
| --- | --- | --- |
| Local Python CLI on macOS arm64 | Implemented | 221 tests passed with real tools on Python 3.14.6 and separately on the minimum Python 3.11.15 |
| Codex native skill | Installed and exercised | 12 acceptance cases, including unhinted discovery, dirty-index preservation and policy explanation; host and evaluation limits appear in the validation record |
| Local Python CLI on Linux arm64 | Implemented and exercised | 221 tests passed in a clean Python 3.14.8 Docker image with networking disabled and no host bind mounts |
| macOS amd64 / Linux amd64 | Pinned provisioner downloads exist | Execution on these architectures has not been established; downloads are not qualification |
| Python 3.11+ | Declared minimum | Full suite passed on 3.11.15, 3.14.6 and 3.14.8 on the platforms above |
| Windows | Not qualified | The provisioner does not support Windows; do not infer support from Python portability |
| Web/mobile/cloud-hosted plugin runtime | Not provided | A package upload does not provision local tools or a remote service |
| Customer VPC / air-gapped inference | Not provided | Local review does not establish model deployment, private routing, residency or authorization |

The pinned evaluation toolchain is Helm **4.3.1** and Kubeconform **0.8.0**.
[`toolchain.lock.json`](../toolchain.lock.json) preserves the default 24 Kubernetes
**1.34.0** strict schemas and adds an explicitly selected **1.36.5** set covering
the same 24 resource kinds. Both sets are pinned to upstream commits and individual
file hashes. Neither includes custom-resource schemas. Other tool/version
combinations need their own receipts and compatibility checks.

## Inputs and review stages

| Input or stage | Supported behavior | Boundary |
| --- | --- | --- |
| Local Git commits | Freeze selected regular files at exact locally available commits | No automatic fetch, checkout, hooks or Git writes; selected symlinks/submodules and unmaterialized LFS pointers are rejected |
| Working tree | Freeze selected tracked and non-ignored untracked regular files; detect a changing snapshot | Ignored and unselected files are excluded; a working tree cannot inherit commit CI success |
| Helm chart | Local lint, repeated template rendering, declared release/namespace/version/API capabilities, install/upgrade context | Dependencies must be local; cluster-dependent lookup is incomplete; no deployment, hooks, post-renderers or dependency update |
| Helm schemas | Inspect local schema content, including vendored chart archives | External references are rejected so schema evaluation cannot introduce implicit network reads |
| YAML/JSON manifests | Strict parsing, inventory, structural comparison and available local schema checks | Duplicate keys, ambiguous identities, unsafe/deep input and unknown custom-resource scope remain errors or gaps |
| Multiple targets | Review explicit selected targets and detect cross-target identity collisions | No inference of whole-repository coverage |
| Profiles | Strict versioned external configuration, or a repository profile read from the immutable baseline commit | Candidate profile edits, deletion or symlink replacement do not change the trusted policy; a profile name is not proof of owner approval |
| Policy gates | Configured minimum severity and change classes; known-rule severity overrides; exact scoped, expiring exceptions | Findings retain original/effective severity and exception attribution; schema failures and required evidence gaps cannot be exempted |
| GitLab MR/CI | Bounded GET-only evidence, verified MR and commit identities, exact-head pipeline/job context | Live customer authentication/network compatibility remains a separate check; unsupported downstream/merged-result cases remain gaps |
| GitLab artifacts | Explicit job/path request, identity binding, digest and metadata | Artifact bodies are not reported or executed; no claim that their test semantics were validated |
| Reports | JSON validated against the versioned report contract; Markdown, standalone HTML and terminal artifact manifest | Desktop/mobile dark appearance and keyboard disclosure exercised on synthetic engine output; light and print appearance remain unverified |

## Coverage taxonomy

| Coverage class | Meaning | Effect on interpretation |
| --- | --- | --- |
| Deterministic rule assessed | A documented rule ran on a supported kind/version and declared input | Finding evidence identifies the resource and source pointer; it proves the declaration observed by that rule |
| Schema assessed | A supplied local schema validated a resource | Structural/type validation only; not admission or controller behavior |
| Known but not semantically assessed | Inventory/diff is available but specialized behavior is outside the rule set | Advisory coverage gap; cannot be described as comprehensive review even if required checks pass |
| Required evidence unavailable | Rendering, identity, schema, selected input or required CI evidence did not complete reliably | Overall decision is `unknown`; independent confirmed findings remain visible |
| Excluded | Input lies outside selected targets or is ignored by the documented snapshot scope | No positive or negative conclusion |
| Runtime unsupported | Requires live cluster/controller/network/provider state | No tool action or inferred runtime result |

`complete` describes completion of the declared required checks. It does not
erase advisory gaps or imply that all Kubernetes semantics were assessed.

## Resource coverage

| Kind/version | Specialized v0.1 checks | Available bootstrap schema |
| --- | --- | --- |
| `Pod/v1`, `Deployment/apps/v1` | Declared workload privilege/host access, image policy, selected control weakening; Deployment selector checks | Yes |
| `Service/v1` | Declared exposure, external IPs, selector match against supplied Pod/Deployment inventory | Yes |
| `Role`, `ClusterRole`, `RoleBinding`, `ClusterRoleBinding` in `rbac.authorization.k8s.io/v1` | Selected broad/sensitive grants, supplied-role binding context, roleRef and subject changes | Yes |
| `NetworkPolicy/networking.k8s.io/v1` | Bounded unrestricted-rule detection and declaration changes | Yes |
| `Namespace/v1`, `PersistentVolumeClaim/v1` | Removal; selected PVC storage/binding changes | Yes |
| `ServiceAccount/v1` | Inventory, structural/schema and configured metadata checks; no live/default identity resolution | Yes |
| `ConfigMap/v1`, `Secret/v1` | Inventory, structural/schema and configured metadata checks; value-free data-field comparison | Yes |
| `StatefulSet`, `DaemonSet`, `ReplicaSet` in `apps/v1`; `Job`, `CronJob` in `batch/v1` | Inventory/diff/schema; specialized workload semantics are unvalidated | Yes |
| `Ingress/networking.k8s.io/v1`, `PersistentVolume/v1`, `ResourceQuota/v1`, `LimitRange/v1`, `PodDisruptionBudget/policy/v1`, `HorizontalPodAutoscaler/autoscaling/v2` | Inventory/diff/schema; specialized semantics are unvalidated | Yes |
| Custom resources / CRDs | Inventory when identity and scope can be established; CRD change advisory | Not in the bootstrap subset; supply trusted local schemas or retain an unknown result |

The current rule set does not assess resource-sizing sufficiency, probe quality,
PDB/HPA effectiveness, service-mesh policy, controller-specific CRD behavior or
actual network reachability. See the [rule catalog](rules.md) for exact checks.

## Release qualification

The reusable local implementation has the measured evidence in the
[validation record](test-validation.md). A customer pilot still requires an
approved profile, a human installation replay, named support ownership and an
authorized GitLab compatibility check if that integration is used. Public
publication, publisher identity, licensing and signing decisions remain separate
from the local package. See the [release checklist](release-checklist.md).
