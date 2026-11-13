# Owned bootstrap input bindings

This source path prepares and provisions the selected owned stack. Local component tests do not qualify a live cluster, pipeline, recovery experiment, replay, or recording.

The `owned-bootstrap-v1` recipe retains exact local material hashes. Real Flux installation YAML, Big Bang chart/values, immutable images, trust material, runner image contents and scopes must be selected before execution. The preflight performs no downloads and does not acquire Grype or Registry1 data.

## Private deployment repository templates

A fresh GitLab project ID, seed commit and reader token do not exist before provisioning. Selected public Kubernetes templates can use only these fixed substitutions:

- Owned Flux `GitRepository`: `spec.url: owned://deployment`, `spec.ref: {commit: owned-deployment-seed}`. `secretRef.name` must reference the derived reader Secret in the same namespace.
- Flux Opaque Secret `stringData`: exactly `username: owned-reader-username`, `password: owned-reader-token`, `ca.crt: owned-gitlab-ca`.
- Owned Argo `Application`: `spec.source.repoURL: owned://deployment`, with `targetRevision: main`. The original path and destination stay selected. The Application must select exactly the foreground `resources-finalizer.argocd.argoproj.io` finalizer; background or custom finalizers are rejected. Its destination Namespace must be directly created in the same run, and readiness binds its observed original UID.
- Argo repository Opaque Secret: label `argocd.argoproj.io/secret-type: repository`; `stringData` exactly `type: git`, `url: owned://deployment`, `username: owned-reader-username`, `password: owned-reader-token`.
- Argo trust ConfigMap: name `argocd-tls-certs-cm`, label `app.kubernetes.io/part-of: argocd`, `immutable: true`, and `data: {owned-gitlab-host: owned-gitlab-ca}`. The map becomes the exact GitLab hostname and selected public CA.

All credential namespaces must have an explicit, owned Namespace manifest. The selected chart must not create a conflicting resource with the same name. Namespace creation precedes credentials and trust; those precede their dependent sources/controllers. The fixed compiler derives values only from the original created deployment project and its separate Reporter identity. The PAT has `read_api` and `read_repository`; the delivery writer is a different identity.

Credential bodies remain in memory and the authenticated API request. Compilation and derivation artifacts retain resource identity and hashes; they redact Secret data. Generic resource creation cannot substitute for the original private-secret capability.

## Runtime identities

`kubernetes.runtime_readers` optionally selects up to two distinct accounts, with roles `runtime` and `denied`. Each entry has exactly:

- `role`
- `serviceaccount`: an `apiVersion: v1`, `kind: ServiceAccount` descriptor with exact namespace/name
- `bindings`: selected RoleBinding/ClusterRoleBinding descriptors; empty for `denied`
- `audience`: the explicitly approved Kubernetes API audience
- `duration_seconds`: 600–14400, also bounded by the current provisioning authorization's remaining lifetime

These accounts and RBAC resources must already be selected pinned manifests and directly created by this run. Automatic service-account token mounts must be disabled. Runtime rules permit only the supported explicit GET/list resources; no wildcard, Secret access or workload write is accepted. Service proxy reads require exact resource names. Complete bounded RoleBinding/ClusterRoleBinding observations include ServiceAccount, equivalent User, and group subjects. Unselected workload grants block issuance; only the fixed Kubernetes discovery/self-access-review baseline is additionally accepted.

A TokenRequest is one-shot, bracketed by original account and effective RBAC observations. Its token is retained only by an opaque original capability. Historical capability validation never returns a token or extends expiration. Actual denied GET403 and positive runtime reads remain required live observations.

## Runner and network trust

The runner starts only after its exact original ID is assigned to both created projects, read back, locked, and enabled. Source CI remains API-only and deployment CI remains MR-only. Unknown assignment or readback outcomes poison further writes.

Runner installation stages the selected public CAs at `/opt/devsecops/runner-inputs/service-trust/gitlab-ca.pem` and `registry-ca.pem`. The fixed five-configuration compiler rewrites selected CA file references before hashing the runner invocation. The network component verifies these readonly files rather than modifying frozen inputs.

Fresh service links use original service/container/network identities and the foundation's normal authenticated transport renewal. GitLab and registry clients keep their logical origin, TLS hostname and original CA. The retained-foundation route cannot be treated as a fresh replay or assume the fresh service-links contract.

The canonical fresh-factory driver retains one authenticated OpenSSH connection
from provisioning through build, recovery, replay and cleanup. Kubernetes,
GitLab and registry forwards share that original connection. Make the protected
SSH key available for the initial authentication and keep the resident command
running. Later commands do not ask the agent to sign again. A lost or replaced
connection stops execution without automatic reauthentication or adoption of a
saved control socket. Uncertain forwarding/cancellation remains incomplete and
is not replayed. The connection is closed and its public receipts are retained
before successful canonical finalization. Choose a short private-state path so
its `host-session/control` Unix socket fits the 100-byte pathname bound.

## Evidence and cleanup

Public evidence accessors return only registered, byte-audited original receipts. Large Kubernetes ledgers are bundled with every original byte, hash and path inside bounded artifacts. Secret inputs, private keys and runner private environment archives are excluded.

Managed Argo policies have a separate transition ledger; they are never adopted as directly created resources. Cleanup requires restored baseline or exact observed absence. Owned runtime tokens are tied to the original service account UID; account deletion is recorded before its credential scope can be considered removed. Unknown effects are retained for read-only reconciliation and are not replayed automatically.

## Controller-created resource cleanup

Canonical bootstrap requires `platform.controller_cleanup` to be the fixed string
`owned-controller-inventory-v1` (validated by `controller-cleanup-policy.schema.json`).
For example, retain every existing pinned platform field and add:

```json
{"controller_cleanup": "owned-controller-inventory-v1"}
```

The original compiler applies this named policy to direct rendered HelmReleases and
Kustomizations, including the separately selected additional templates, before
computing their manifest pins. It preserves the selected chart, values and source
bytes. Derived HelmRelease configuration is:

```yaml
install: {crds: Skip, disableHooks: true, createNamespace: false}
upgrade: {crds: Skip, disableHooks: true}
rollback: {disableHooks: true}
test: {enable: false}
uninstall: {disableHooks: true, keepHistory: false, deletionPropagation: foreground, disableWait: false}
```

The compiler also fixes the explicit release name from the originally selected
name/target namespace, preserving an already selected release name. Flux uses
`prune: true` and `deletionPolicy: Delete`. Remote-cluster and suspended controllers
are unsupported. Required chart CRDs must therefore be supplied as pinned direct
manifests, with their exact names in `platform.cluster_resources`; the release
cannot silently install or retain extra CRDs. Every target and storage namespace
must have its own selected direct Namespace manifest. Explicit cluster resource
names cover both direct resources and every controller-created cluster child.
Those exact destinations are observed absent before any controller is created.

The original process reads each Ready HelmRelease's actual versioned Helm storage
Secret and bounded decoded release manifest; the Secret's contents never enter
public receipts. It recursively reads actual nested controller inventories. Flux
uses its observed applied inventory/revision. Argo uses actual Healthy/Synced
tracked resources plus original application tracking metadata. Namespaced custom
GET routes are derived only from pinned, directly created original CRD manifests;
unknown kinds and cluster-scoped custom kinds require additional supported scope
and never trigger API discovery. Each child's exact UID and content hash is retained.
An Argo child outside the originally created namespaces or selected cluster scope
is rejected. A nested Argo Application must be an independently selected direct root
so it can finalize while its controller is still alive.

Direct Argo Applications finalize first, followed by Flux Kustomizations and
HelmReleases. All captured children and Helm storage objects must be independently
observed absent before remaining controller Deployments, CRDs and namespaces are
removed. No child is adopted for a direct DELETE. A replaced UID, `keep` resource,
prune exemption, changed controller inventory or unsupported nested CRD/hook policy
keeps cleanup incomplete. Actual absence of the original namespaces closes remaining
namespaced descendants; namespace absence is not evidence about cluster resources.

A partial cleanup can continue only after original read-only reconciliation shows
no uncertain writes or untracked cluster residue, and a new scoped down operation
explicitly names its predecessor. If a controller changed after capture, its DELETE
is deferred. Under the new action, remaining original roots may refresh their actual
inventories within unchanged selected scope and spec. The complete old child UID
union is retained; removed roots are never recaptured, no child is adopted for a
DELETE, and an uncertain deletion is never repeated. Remaining original resources
are audited before any continuation effect. All children must still be absent before
controller or CRD teardown.

These contracts support local engineering tests. The selected real chart's nested
controllers and required CRD manifests still need actual qualification in the live
run; component tests or an empty saved inventory cannot establish that evidence.

After completed deletion with a lost local receipt, a removed namespaced custom
API is not queried as though its 404 proved object absence. The original observer
can instead retain fresh GET evidence that both its originally created Namespace
and CRD are absent. A replaced CRD or a namespace still present keeps this path
incomplete; no fallback route or recreated object is used.

Initial partial provisioning is explicitly narrower: if a newly created controller
never exposes a current usable release/application inventory, this implementation
cannot establish complete child ownership. For example, a first Helm install with
no usable versioned storage/history remains incomplete before controller DELETE.
This is an unresolved live cleanup prerequisite, not proof of successful recovery
from arbitrary provisioning failures. Existing inventory-backed partial cleanup
and uncertain delete reconciliation retain their original evidence requirements.

A failed Helm install can still use normal scoped cleanup when its actual latest
versioned history, storage Secret, release identity and decoded manifest agree.
The controller generation and fixed CRD/hook policy must still match. Existing
children require their actual original UID; declared manifest objects that are
currently absent are recorded as absent without adopting a UID. An absent nested
controller remains unknown and blocks this route. No successful install or
workload readiness is claimed from a failed release's cleanup evidence.

The selected mission Argo Application is admitted by original identity during
provisioning, before its first image exists. An unready Application has an explicit
pending child inventory, never an empty-success inventory. After the actual first
Healthy/Synced delivery, `capture_controller_inventory(context)` observes its real
children through the retained bootstrap owner. Cleanup also requires actual current
child scope; provisioning deferral cannot authorize uninstall without that proof.

A terminal failed initial Argo sync has a separate, narrow cleanup route. The
original platform input capability must have bound the created deployment project
and immutable seed commit before resource effects. During a scoped down action,
the original Application must report `Failed` or `Error`, completed timestamps,
no active operation, and the same seed revision and source in its request,
comparison and result. Its nonempty tracked inventory must exactly match unique
terminal resource-result identities. Hooks, nonempty hook phases, subset syncs,
comparison errors and changed original UIDs are rejected. The operation and
inventory are bracketed by fresh reads, so a retry beginning during observation
cannot authorize deletion. Actual child GETs still enforce original namespace,
cluster scope and tracking ownership; absent ordinary children are recorded
without inventing UIDs, while an absent nested controller blocks cleanup. Only
the original parent is deleted, and actual child absence is required before
controller teardown. This route makes no readiness or successful-sync claim.
An `Unknown`/`Missing` seed Application without that completed, nonempty evidence
still requires usable actual controller inventory before cleanup can proceed.

## First delivery

Both owned projects require their exact installed CI seeds. The source project uses `gitlab-owned-api-v1.yml`; the deployment project uses `gitlab-owned-deployment-v1.yml` with the sole required job `validate_owned_deployment`. The deployment MR job validates the exact actual Git checkout, shipped chart and selected values with pinned Helm/Kubeconform and packaged offline schemas. It does not publish an image. The runner stages the original proposal configuration as its sixth pinned template; its first five remain the source publication chain. The closed `services.runner_validation` selection supplies separate runner `helm` and `kubeconform` paths plus exact binary SHA256 pins, and `schemas_path`, all under `/opt/devsecops/`. These are Linux runner inputs; host tool selections remain unchanged. Only these selected validation-tool fields differ in the staged proposal. The exact packaged schema set and tool versions are checked at execution. Approved tool and schema paths must exist in the selected runner image outside the actual checkout.

The first owned deployment seed may leave both existing image scalars empty. All other environment, peer, architecture and chart settings must already be selected. An initial proposal accepts only the exact installed chart and a two-scalar change from both-empty image fields to the authenticated CI image; only the resulting deployment is rendered and schema-validated. Its evidence explicitly records the prior state as uninitialized. One-empty-field seeds, changed charts and unrelated source changes fail. Initialized images keep the existing before/after rendering checks.

Provisioning establishes actual controller/release readiness and admits the original owned Application source, watched `main` branch, destination and namespace UID. It does not claim the mission is healthy before publication. The same original bootstrap authority retains this historical admission scope. First delivery must subsequently observe its exact merged revision as Synced/Healthy, actual workload identity and health, and the complete original controller child inventory before the build/promote phase can complete. An immutable seed revision is not a supported watched Argo branch.
