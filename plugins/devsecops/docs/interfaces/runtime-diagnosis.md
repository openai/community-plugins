# Offline baseline/fault diagnosis

`devsecops.runtime.diagnosis.diagnose_runtime(baseline_bundle, fault_bundle)` accepts two collection dictionaries, explicit `pathlib.Path` inputs, or `None` for missing evidence. It returns a deterministic version 1 `runtime-diagnosis` document conforming to `DIAGNOSIS_SCHEMA` and `schemas/runtime-diagnosis.schema.json`. It does not call providers, run commands, follow embedded paths, read credentials, sleep, mutate resources, or execute probes.

Every output has `decision: unknown`, `execution_status: partial`, `causality: unproven`, `recovery: not-evaluated`, and `http_health: not-observed`. A narrow controller, readiness, scrape or counter observation is never promoted into a causal explanation, actual application HTTP health, policy enforcement, successful repair, or recovery. A diagnosed historical window does not assert the current live state.

## Input integrity and comparison

Explicit files use the collection limit of 16,000,000 bytes, rather than the context document limit of 1 MB. The reader rejects symlinks, special files, duplicate JSON keys, non-finite numbers, non-object JSON, and files that change while read. `inputs` records raw file hashes or canonical dictionary hashes, input state, and validated bundle/context seals. Missing, invalid and foreign inputs produce required gaps and an unknown report; there is no successful default baseline.

The function validates collection/context schemas, both seals, embedded canonical content hashes, config/lock/intent identity relations, target sets, and each fixed window recomputed from the before snapshots. It does **not** reopen historical config, lock, intent, receipt, or CA paths. Historical raw-file hashes cannot be authenticated from an embedded parsed object, and unkeyed SHA-256 seals do not authenticate an actor or an original collection. `immutable-journal-authentication-unverified` therefore always remains required.

Comparisons require the same run, attempt, environment, profile, scenario, and all six frozen identities: scenario, assertion set, package, lock, config and environment hashes. They require the same scoped targets, roles, Deployment and Service UIDs, Argo application identity and destination, source positions/repositories/paths/renderers, and exact Prometheus label scope. Baseline and fault phases must be correct. Measured windows must be ordered and disjoint, including their endpoints; completion of the baseline may not overlap the fault window. Window timing and capture expiry are checked against recorded observation times, not the wall clock at a later forensic read.

Selected immutable Git revisions and complete source-spec hashes may differ between the phases, and differences are retained in `source_changes`. Argo source/destination parity is rechecked against the frozen intent and recorded full-spec digests. A different source path or repository is not treated as merely another revision. Source-intent and environment-inventory authentication remain required even when hashes match.

Concrete runtime identity is reconstructed from normalized Deployment/Service UIDs, ReplicaSet/Pod controller ownership and Pod/container image IDs and restart counts, and matched against image snapshots. Before/after image intervals must bracket each fixed window; ownership, images, restart counts and Deployment generation must remain compatible across the two windows. Missing identity evidence, Pod replacement, restart, changed image, missing Service UID, or changed controller identity prevents a complete comparison.

`comparison_status` has deliberately narrower semantics than a verdict:

- `aligned`: the saved scopes, identities and time windows align and the supported narrow observations are available. Authentication and independent control gaps still remain.
- `incomplete`: at least one input or necessary provider observation is missing, invalid, stale, partial, or has incompatible runtime identity evidence.
- `incompatible`: valid bundles identify different runs/scopes, inappropriate phases, or windows that cannot be compared in order.

## Observations and retained failures

`observations` contains fixed fact kinds: scrape state, counter progression, Kubernetes readiness, Argo sync and health, allowed container error reasons, ready EndpointSlice count, Service selector/port changes, NetworkPolicy changes, runtime identity, and Deployment generation. Each fact contains baseline/fault values, a changed/unchanged/unavailable comparison, and source JSON pointers plus canonical hashes. Returned values use fixed enums, numbers and hashes; arbitrary provider messages, logs, event narratives, annotations, credentials and inline values are not copied into diagnosis text.

Readiness is recomputed from observed generation, replica counts, Pod/container readiness and selected EndpointSlices. It is **Kubernetes readiness**, not an actual HTTP request. Argo Healthy/Synced reports only controller state. Neither eliminates DNS, listener, transport, mesh or application instrumentation alternatives.

Prometheus source samples are rechecked for scope, finite values, strictly ordered unique timestamps, at least the locked sample count, source-window alignment, freshness bounds and counter resets. Counts or summary `pass` flags cannot replace the concrete source samples. The actual completion clock also bounds the final source timestamp in each series. Since the collection hardening, the provider's `observed_at` is actual completion; `observations.evaluated_at` is the fixed query endpoint, `window_assessment` preserves its original narrow historical assessment, and `collection_freshness` records the actual-read check. Diagnosis defensively recomputes freshness from `transport.finished_at` and the samples rather than trusting those summary fields.

A failed scrape, non-progressing fresh counter, observed non-ready workload, or degraded/out-of-sync controller is retained in `retained_failures` when its normalized evidence supports that narrow fact, even if another provider or the baseline is unavailable. `confirmed_failure` means at least one such **recorded observation**, not authenticated incident causality. Valid source samples that were fresh in the fixed query window but aged out before collection finished preserve their recomputed failure with `freshness: historical-window-only`; their current-at-collection observation stays unknown. A stale series lacking enough valid window samples or a reset counter does not become a flat-counter failure. Unsupported provider summary flags alone cannot create a failure fact.

## Alternative ranking

The fixed alternatives are NetworkPolicy, discovery/selector, application, DNS, and mesh/certificate. Rank is the count of concrete supporting observations, with equal counts sharing rank; it is not a probability, likelihood estimate, or causal confidence score. Alternatives without direct support have no rank and remain untested, not disproven.

A changed policy can support the policy hypothesis only when it selects the target using known label evidence and the comparable fault window records a scrape outage after a baseline with successful scrapes. Policies selecting unrelated workloads do not lead the ranking. An extra Pod label absent from the Deployment's selector is unknown, not proof that the label is absent. Changed selectors, ports or ready endpoints support discovery alternatives. Non-ready workloads, enumerated container errors, or a flat fresh counter while scrapes are up support the application/instrumentation alternative. Ready status is limited counterevidence to an application-readiness failure; it does not rule out listener or instrumentation faults.

The collector has no direct DNS or mesh/certificate probes, so those alternatives remain explicitly untested. Required controls include effective CNI policy enforcement, monitoring discovery, actual namespace and Pod labels, endpoint/listener checks, DNS resolution, mesh enrollment/authorization, transport certificates, authorized positive and negative probes, and direct HTTP/functional requests. Temporal order, unchanged images, selected policy syntax and Argo Healthy do not establish policy causality.

## CLI and future verification boundary

The root CLI integrates this API as `devsecops-review diagnose-gitops --baseline PATH --fault PATH --output NEW_EXTERNAL_DIR`. The CLI must retain exact raw input hashes in its manifest, validate the report schema/seal, and bind raw and canonical bundle hashes to independently authenticated immutable-journal entries before treating the files as originating evidence. The standalone function cannot provide that journal authentication and cannot clear its required gap merely from a hash match.

Future cause/recovery verification additionally needs authenticated frozen inventory and image mappings, delivered patch/revision and native handoff bindings, all relevant source samples after actual delivery, direct mission and NGINX HTTP receipts, and the authorized positive/negative controls for the actual CNI/mesh topology. No diagnosis observation or rank is an authorization to change a cluster or deliver a patch.

Qualification here is offline engineering evidence using actual adapter-composed synthetic bundles, adversarial resealed mutations and bounded local files. It is not a customer or Platform One live qualification.
