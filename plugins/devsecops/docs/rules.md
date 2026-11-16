# Deterministic rule catalog

This catalog describes the implemented rules in
[`checks.py`](../src/devsecops/checks.py). They review declarations in the supplied
inventory; they do not compute effective cluster authorization, admission,
connectivity or workload health.

Every finding records a stable rule ID, severity, resource identity, field,
remediation and source/document/JSON-pointer evidence. Findings include a
`source_type` of `deterministic` and a fingerprint derived from the rule, resource
and logical location. Evidence does not reproduce resource values or Secret
payloads. Rendered Helm pointers identify rendered documents, not exact original
template line numbers.

## Severity and change classification

Built-in rule severities are `high`, `medium`, and `low`; profiles can also assign
`critical`. They express the review importance of
the detected declaration; they are not CVSS scores or proof of exploitability.

| Classification | Meaning |
| --- | --- |
| `introduced` | A matching baseline finding was absent, or a consequential transition is newly observed |
| `worsened` | Severity increased or an explicit protection was weakened |
| `preexisting` | The matching finding remains present without a detected severity increase |
| `resolved` | The baseline finding is no longer present under the same checks |

Logical anchors avoid treating container or RBAC-rule reordering as new access.
Structural resource diffs still report changed field paths. Findings and the
gate decision are separate: resolved findings are retained as evidence, and
pre-existing findings remain visible even when a change-only gate does not block
on them. The selected trusted profile determines the applicable gate policy;
schema failures and required coverage gaps are separate from rule severity.

The default gate blocks introduced or worsened findings at effective severity
`high` or above. A profile can change the minimum severity, include `preexisting`
findings, or override a known rule's severity. Reports retain original and
effective severities. Exact target/rule/resource exceptions retain the finding,
identify the owner and reason, and expire after their declared UTC date. They
cannot exempt schema failures or missing required evidence. See
[policy configuration](policy.md) for the full contract.

## Workload declarations

These checks apply to `Pod/v1` and Pod templates in `Deployment/apps/v1`, including
supplied init/ephemeral containers. Effective *manifest* security settings inherit
supported Pod-level fields and apply container overrides. No image defaults,
admission mutation or node configuration is assumed.

| Rule | Severity | Trigger and boundary |
| --- | --- | --- |
| `K8S-HOST-ACCESS` | High | Explicit `hostNetwork`, `hostPID` or `hostIPC` is true |
| `K8S-HOSTPATH` | High | A hostPath volume is declared; evidence does not expose its path |
| `K8S-PRIVILEGED` | High | A container explicitly requests privileged execution |
| `K8S-ESCALATION` | Medium; high for weakening | Explicit escalation is allowed; losing an explicit false setting is a high-severity transition |
| `K8S-ROOT-USER` | Medium | Effective manifest `runAsUser` is numeric zero |
| `K8S-CAPABILITIES` | Medium or high | Added Linux capabilities; high for the bounded sensitive-capability set in source |
| `K8S-NONROOT-WEAKENING` | High | Effective `runAsNonRoot: true` is lost |
| `K8S-ROOTFS-WEAKENING` | Medium | An explicit read-only root filesystem setting is lost |
| `K8S-SECCOMP-WEAKENING` | High | An effective `RuntimeDefault` or `Localhost` seccomp requirement is lost |
| `K8S-TOKEN-AUTOMOUNT` | Medium | Pod-level explicit false token-automount setting is lost; ServiceAccount/default admission behavior remains unverified |
| `K8S-DEPLOYMENT-SELECTOR` | High | A declared Deployment selector does not match its own template labels |
| `K8S-IMMUTABLE-SELECTOR` | High | An existing apps/v1 Deployment selector changes, requiring replacement/migration review |

The sensitive capability set is `ALL`, `SYS_ADMIN`, `NET_ADMIN`, `SYS_PTRACE`,
`DAC_READ_SEARCH`, `SYS_MODULE`, and `SYS_RAWIO`. This is a bounded heuristic, not
an exhaustive capability risk model.

## Images and configured metadata

| Rule | Severity | Trigger and boundary |
| --- | --- | --- |
| `POLICY-IMAGE-DIGEST` | High | Required digest policy is enabled and an image lacks a complete SHA-256 digest reference |
| `K8S-IMAGE-MUTABLE` | Low | The same mutable image observation when required digest policy is disabled |
| `POLICY-IMAGE-REGISTRY` | High | An image registry is outside a nonempty exact allowlist; matching is case-insensitive and implicit registry defaults to `docker.io` |
| `POLICY-REQUIRED-LABEL` | High | A configured metadata label key is absent; this checks key presence, not value correctness |

Digest pinning does not establish image provenance or vulnerability status.
Registry policy checks do not contact a registry. Required-label checks also
apply to kinds without specialized semantic rules.

## RBAC declarations

These checks apply to the supported RBAC v1 kinds. A RoleBinding remains scoped
by its namespace even when it references a ClusterRole. Referenced-role analysis
uses only the supplied inventory and correct role namespace.

| Rule | Severity | Trigger and boundary |
| --- | --- | --- |
| `K8S-RBAC-WILDCARD` | High | Wildcard API group, resource, verb or non-resource URL declaration; a literal resource-name asterisk is not treated as wildcard authorization |
| `K8S-RBAC-DELEGATION` | High | Explicit `bind` or `escalate` privileges on roles/clusterroles |
| `K8S-RBAC-SECRETS` | High | Explicit get/list/watch access to Secret contents |
| `K8S-RBAC-IMPERSONATE` | High | Impersonation grants for supported identity resources |
| `K8S-RBAC-TOKEN` | High | Explicit service-account token creation permission |
| `K8S-RBAC-CLUSTER-ADMIN` | High | Binding references the cluster-admin ClusterRole; its actual definition and binding scope still require review |
| `K8S-RBAC-SENSITIVE-BINDING` | High | A subject is bound to a supplied role with a detected high-severity permission declaration |
| `K8S-IMMUTABLE-ROLE-REF` | High | A binding's roleRef changes, requiring replacement review |
| `K8S-RBAC-BINDING-CHANGE` | Medium | Declared binding subjects change; ordering alone does not count |

Aggregated ClusterRoles and unavailable referenced roles produce advisory
coverage gaps. These checks do not compute effective permissions or discover
identities outside the selected inventory.

## Networking declarations

| Rule | Severity | Trigger and boundary |
| --- | --- | --- |
| `K8S-SERVICE-EXPOSURE` | High | LoadBalancer or NodePort requested; public reachability is not established |
| `K8S-SERVICE-EXTERNAL-IPS` | Medium | External IPs declared; routing and address ownership are unverified |
| `K8S-SERVICE-SELECTOR` | Medium | No supplied Pod/Deployment in the same namespace matches a nonempty selector; external inventory or EndpointSlices may explain it |
| `K8S-NETPOL-ALLOW-ALL` | High | A selected ingress/egress direction contains a bounded all-peers/all-ports form |
| `K8S-NETPOL-CHANGE` | Medium | NetworkPolicy declaration is added, removed or changed |

The allow-all check recognizes absent/empty peer lists or an empty peer object
with no port restriction. It does not flatten all selectors, CIDR exceptions, or
the union of policies into an effective network model. Valid selectorless or
ExternalName Services are not treated as selector defects.

## Resource lifecycle

| Rule | Severity | Trigger and boundary |
| --- | --- | --- |
| `K8S-DESTRUCTIVE-REMOVAL` | High | Namespace or PVC declaration is removed; actual deletion/data loss is not asserted |
| `K8S-PVC-MIGRATION` | High | Selected PVC storage/binding fields change: storage class, access modes, volume mode, selector or volume name |
| `K8S-CRD-LIFECYCLE` | Medium | A CRD changes; conversion, stored-data and controller migration behavior need separate review |
| `K8S-HELM-HOOK` | Medium | A resource with a Helm hook annotation changes; lifecycle, order, deletion and rollback behavior are not executed |

## Coverage and non-rules

Duplicate identities are excluded from reliable comparison and create required
gaps; they must not be converted into fabricated additions/deletions. Kinds
outside the specialized allowlist receive structural and available schema checks
with an advisory `specialized_rules_unavailable` gap.

Missing schemas, failed parsing/rendering, non-deterministic rendering and stale
CI evidence are validation/coverage conditions, not findings with invented rule
severity. Resource requests/limits, probe quality, workload rollout strategy,
PDB/HPA behavior and effective service-mesh policy are not specialized checks in
this release. See the [support matrix](support-matrix.md).
