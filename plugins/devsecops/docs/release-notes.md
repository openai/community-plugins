# DevSecOps development preview

The v0.2 alpha extends the reusable review engine with local Kustomize rendering,
GitOps evidence diagnosis, constrained patch validation and recovery comparison.
All five native skills are packaged. Registry acquisition, signature and publisher
checks, offline runtime qualification and the native execution driver are under
integration. The canonical Software Factory demo is not yet qualified.

Use the [implementation ledger](demo/implementation-status.md) for exact tested
components and remaining live, native-host and human acceptance gates. The stable
v0.1 evidence below must not be read as v0.2 release qualification.

## DevSecOps 0.2.0-alpha.14 candidate

Adds explicit [KVM acceleration](interfaces/qemu-acceleration.md) for fresh RKE2
and separate runner VMs on an approved Linux x86_64 host. Both recipes must
select the same exact device group. The packaged helper checks real nonroot
device access; the runtime receives only that device and group. Missing KVM
fails without falling back to emulation, and cleanup requires no new device
admission. Existing TCG defaults and execution budgets remain intact.

Refreshes runtime schema exports to include the already supported per-image
attestation policies. Full package regression, installed-client verification
and the actual KVM demo remain separate qualification requirements.

## DevSecOps 0.2.0-alpha.13 candidate

Adds optional offline Grype database preparation before short-lived image
qualification starts, with explicit pinned preparation and scanning budgets.
The actual image is still scanned; a saved preparation receipt cannot supply
authority or a scan result. A private read-only cache is bound to the exact
archive, tool, current process and file contents, then removed on context exit.
Policy, exception, image-authority and database-age limits remain unchanged.
Fresh TCG foundations use a ten-minute kubelet runtime request budget so the
air-gapped Helm installer can finish unpacking. The bounded foundation readiness
deadline and its three consecutive authenticated observations remain unchanged.
Full package regression and live qualification remain separate gates.

## DevSecOps 0.2.0-alpha.12 candidate

One registry service can now select a bounded list of exact per-image attestation
policies. This allows a Python base and an NGINX reference image to retain their
different descriptor limits on the same Registry1 origin. Each selected policy
must match its immutable subject, configured repository scope and pinned bytes.
Duplicate subjects, conflicting selections and changed policies are rejected;
limits are never combined. The existing single-policy setting remains supported.
Focused checks pass; full package regression and live qualification are separate
gates for this candidate.

## DevSecOps 0.2.0-alpha.11 candidate

Adds the explicit Codex CLI `0.167.0-alpha.3.2` target. Both this client and the
retained `0.164.0-alpha.11.1` protocol use strict local and managed startup
validation, effective disabled-MCP checks, and exact returned-version checks.
Managed configuration rejection stops before a model call. The legacy
`0.144.3` target retains its explicitly narrower local-only preflight scope.
Full package-bound regression, installation, and live qualification remain
separate gates for this candidate.

## DevSecOps 0.2.0-alpha.10 candidate

Supports real Helm templates named `values.yaml`, `Chart.yaml`, and
`values.schema.json`; inspects every template file type for `lookup`. Chart-relative
classification preserves strict metadata/schema checks when a dependency itself
is named `templates`. The regression includes real Helm rendering and unsafe
vendored metadata controls. Full package and live qualification remain required.

Supports the standard HPA, PodDisruptionBudget, ResourceQuota, and admission
webhook resources used by the selected platform charts. Exact installed Flux
and Argo CRDs can reuse their registered routes without admitting a different
plural or scope. Helm inventory accepts a validating webhook strengthening
`failurePolicy` from `Ignore` to `Fail`, records the change, and still rejects
weakened policies or changed callbacks. Bare `=` values in CRD enums remain
strings; explicit legacy tags and the existing YAML limits remain enforced.
Auxiliary CRDs now become established before Helm release provisioning; failed
CRD readiness stops that provisioning. Portable, native and runtime versions
are aligned at alpha.10.
Local scanner catalogs, SBOMs and vulnerability results use a separate fixed
budget of 16 MB and 500,000 JSON values. This accommodates real language-runtime
image inventories while retaining strict depth, duplicate-key and nonfinite
number checks. Network-response and attestation limits are unchanged. The same
budget applies during policy evaluation and derived-subject negative controls.

## DevSecOps 0.2.0-alpha.9 candidate

Composes the fixed 33-phase owned factory path, eight resident native handoffs,
CI and deployment Git evidence, original runtime readers and probe inputs,
NetworkPolicy fault qualification, fresh foundation and resource cleanup.
Final evidence binds the actual native invocations and phase observations.
The qualification command checks repeated runs and continuous recording
acceptance separately from broader release gates.

This is implementation and local qualification work. Real GitLab/registry/cluster
handoffs, unchanged repeated runs, continuous capture and independent playback
acceptance must still be demonstrated with the selected installed package.

## DevSecOps 0.1.0

Initial local evaluation release: Kubernetes and Helm change review for Codex.

- Native `$review-kubernetes-change` skill and a standalone Python CLI.
- Exact Git and working-tree snapshots, baseline-trusted repository policy,
  bounded repeated Helm rendering and local Kubeconform validation.
- 34 deterministic rules covering selected workload, image, RBAC, exposure,
  selector and lifecycle risks, with explicit unsupported coverage.
- Configurable gates, attributed severity overrides and exact expiring
  exceptions that cannot suppress missing evidence or schema failures.
- Optional GET-only GitLab MR/CI evidence tied to exact commits; opt-in job
  artifact metadata/digests with no comments, job retries or deployment writes.
- Sanitized JSON, Markdown and responsive standalone HTML reports with terminal
  artifact hashes. Missing required evidence preserves `unknown` while retaining
  independent findings.
- Generic and explicitly unconfigured Lockheed examples; synthetic demos and
  checksum-pinned, separately provisioned tools/schemas.

See the [validation record](test-validation.md) for executed tests and host
versions, [support matrix](support-matrix.md) for coverage, and
[dependency inventory](dependencies.md) for the observed Python environment.

This release has no required MCP or cloud service. It does not provide live
cluster checks, deployment, compliance certification, vulnerability scanning,
full Big Bang/Flux resolution, or public marketplace availability. Authenticated
customer GitLab, customer-owned profiles, support ownership and a second human's
installation replay belong to the separate customer pilot.
