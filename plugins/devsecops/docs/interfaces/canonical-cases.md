# Canonical production case contracts

The canonical owned driver calls `OwnedCanonicalCases.execute` for the existing
`platform-dependency`, `supply-chain-controls`, and `evidence-gaps` phases. It does
not select callbacks or a different deployment graph. All cases require the
current reserved journal, exact context and immutable `owned_cases_recipe`
selection. The closed schema is `schemas/owned-cases-recipe.schema.json`.
Controlled tests do not issue live scenario assertions.

## Image origin before baseline

`qualify_locked_images(context, operations=..., output=...)` performs actual
publisher authority, required signature/predicate and image-chain acquisition.
Operations calls it during build/promote and explicitly requalifies when needed.
`VerifiedLockedImages` is an original process-bound capability; JSON cannot
restore it. `require_locked_images` audits original evidence and current expiry.
The recipe requires `max_verification_age_seconds` (1–3600); telemetry sample
freshness is not image-policy validity. Expiry is also bounded by the publisher,
signer and current operation authorization windows.

Under the explicit `publisher-policy-sha256-v1` contract only,
`lock.images.*.trust_sha256` means the independently selected publisher policy
file SHA256. It is distinct from the fresh publisher verification receipt seal.
No legacy unqualified trust hash grants authority. `runtime-image-mapping.json`
has stable content/policy identities without timestamps; each newly acquired
origin proves that same mapping and retains separate time-sensitive acquisition
and verification receipts. This allows one image identity across baseline,
fault and recovery without treating old signature observations as fresh.

## D3: actual optional extension failure and repair

The selected owned GitLab deployment project contains one pinned extension
Kustomization under `platform/`. Its only staged defect is `spec.dependsOn`
changing from the selected existing dependency to its observed-absent
`-unavailable` name. The producer uses a single guarded GitLab commit API update
with exact parent/file readbacks and checks every unrelated blob. A create-only
`codex-d3-<run UUID>` branch starts at the originally selected immutable platform
source. Both fault and repair stay on that branch; mission `main` is read back
unchanged. An existing case branch cannot be adopted. Repair restores the
original bytes. There is no arbitrary push, merge, path or script hook.
The original `PlatformGitTransition` capability allows the bootstrap producer
to CAS the originally owned GitRepository to the resulting immutable commit.
An uncertain write stops further writes and retains the intent for reconciliation.

Actual GET observations bind source, root, dependency and extension UIDs and
generations. The extension's current Ready condition must show DependencyNotReady
while its last successfully applied revision remains the baseline. The applied
and attempted revisions are recorded separately. Repair requires the selected
new revision, current readiness, and the same dependent Service identified in
the extension inventory. A bounded Kubernetes Service proxy health GET proves
functional service health; it makes no NetworkPolicy-path claim. Independently
observed core Flux Deployment, Big Bang HelmRelease and Argo Application keep
their original UIDs/specifications and remain healthy throughout.

These distinctions follow the upstream [Flux Kustomization contract](https://fluxcd.io/flux/components/kustomize/kustomizations/)
and [v1 status API](https://fluxcd.io/flux/components/kustomize/api/v1/):
lastAppliedRevision is not lastAttemptedRevision, and the overall observed
generation can lag while the current failure condition identifies the attempted
generation. The source commit alone is not proof of successful dependent apply.

## D4: trust and evidence rejection

D4 freshly verifies actual reference and mission-base subjects, then runs the
real offline signature verifier against copies of acquired envelopes: valid,
missing, payload-tampered and independently selected untrusted key. Original
evidence and registry subjects stay unchanged. Derived SBOM/scan controls use
the original CI identity with a fresh `RuntimeImageProvenance` verification,
not expired publication/write authority. Exact base/index/platform/config and
derived manifest/archive/source relationships are checked before altered SBOM
and scan copies are rejected. This path performs no scanner execution or scanner
database acquisition. Missing actual trust/acquisition produces incomplete.

## D5: evidence gaps and new scoped review

The bootstrap's original owned no-RBAC ServiceAccount reader makes one GET to
the originally owned mission Deployment. Only an authenticated 403 qualifies as
the read-denial control. The caller checks the exact original run, cluster,
ServiceAccount UID and empty selected grants. The private token remains inside
the original capability; no environment export or external token selection is
needed. TLS uses frozen pinned CA bytes, a maximum 20-second deadline, one
request and no redirects or retries. The receipt retains route, origin,
ServiceAccount UID, CA hash and HTTP observation, without secrets. This separate
control does not assert that ordinary access was revoked.

Missing and stale copies of actual baseline/fault observations run the same
deterministic diagnosis validator. An explicitly expired validity control is
rejected by the native request validator before client invocation. Each outcome
preserves unknown and records the concrete restoration action. Copies never
become main runtime origins.

After the original inputs are restored, a separate child journal records a new
`diagnose-gitops-deployment` request using the resident session's selected client,
model and installed package. The actual native report bytes must match an
independent fixed-engine replay. This is a newly scoped historical baseline/fault
comparison. It issues no live recovery or mutation authority and does not alter
the eight primary/replay native requests. Missing actual origins, owned restricted
reader, package or client leaves D5 incomplete.

## Validation scope

Local controlled tests exercise actual offline cryptography, state interpretation,
subject mismatch, credential admission and deterministic evidence controls.
They do not establish live GitLab/Flux recovery, Registry1 trust, a real denied
guest request, model execution, completed end-to-end repetitions or recording
playback. Those remain actual canonical-run acceptance gates.
