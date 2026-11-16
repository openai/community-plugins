# Independent GitOps recovery conditions

`devsecops.runtime.recovery.verify_recovery` evaluates saved evidence offline.
It issues no API requests, executes no commands and changes no deployment. It
reports individual measured conditions even when the overall recovery decision
is unknown. This is the canonical mission recovery verifier for the future
factory driver; a live driver must call this implementation rather than maintain
a parallel set of recovery assertions.

```python
report = verify_recovery(
    baseline, fault, recovered,
    contract=approved_contract,
    evidence={
        "inventory": inventory_path,
        "delivery": delivery_path,
        # Remaining closed evidence names listed below.
        "http:recovery-mission-health": probe_receipt_path,
    },
)
```

Collection and JSON arguments accept an explicit `Path` or an in-memory JSON
object. `patch` accepts an explicit `Path` or raw bytes. Inputs are frozen once,
symlink paths are rejected, duplicate/nonfinite JSON is rejected, and embedded
paths remain inert. Limits are 16 MB per JSON input, 256 KB per patch and 64 MB
across inputs. The report records exact file-byte hashes; dicts use the existing
`devsecops-runtime-json-sha256-v1` encoding, including its trailing newline.
Missing/invalid evidence becomes a required gap. An invalid operator contract
or unknown evidence name raises a sanitized `ValueError`.

The root CLI integration uses `verify-gitops-recovery` with explicit
`--baseline`, `--fault`, `--recovered`, `--contract`, repeated
`--evidence NAME=PATH`, and a new external `--output` directory. Confirm that the
installed command exposes these options before invoking it; this module does
not install or update a CLI. The CLI must write the structured result and a
terminal manifest covering exact input/output hashes.

## Approved contract

See [runtime-recovery-contract.schema.json](../../schemas/runtime-recovery-contract.schema.json).
The contract is a separately supplied operator input, not an authority inferred
from collection snapshots. It binds the run, attempt, environment and six
identities; the locked timing/platform settings; and the complete approved
baseline/fault/recovery intents. Both required canonical roles have exact target
IDs. The reference target is identical across all phases. The mission's delivery
source position and correction-contract byte hash are fixed.

`image_expectations` independently fixes every target/container's platform
manifest digest, OCI config digest and image-mapping receipt hash. The two named
application containers must be present. The reference application's content must
equal the locked Iron Bank `reference_web` platform/config digests. The mission
application expectation must come from the separately approved derived-image
build/promotion evidence, never by copying the runtime inventory. Each mapping
receipt hash must also match the original recovery intent. A future authenticated
backend must verify the mission image's locked base lineage and actual build
receipt; these hash associations alone do not prove that build happened.

The contract names exactly these cases for both **baseline** and **recovery**:

- `mission-health`: mission `/health` on its health port.
- `reference-health`: reference NGINX `/` on its HTTP port.
- `metrics-allowed-before` and `metrics-allowed-after`: mission `/metrics`.
- `metrics-denied-wrong-namespace`: right Pod labels in the wrong namespace.
- `metrics-denied-wrong-label`: wrong Pod labels in the right namespace.

Fault evidence requires mission and reference health cases. Each case has a
unique ID, exact target/source ID, port/path and optional expected body hash.
Case IDs identify `http:<case-id>` inputs. The contract cannot omit controls or
substitute a different canonical scenario. Source authorization selectors are
explicit namespace and Pod label maps. Source labels, UIDs and actual addresses
come from the independent inventory, not from the probe's environment claims.

## Closed evidence inputs

| Name | Required content and linkage |
| --- | --- |
| `inventory` | Supplemental inventory described below. |
| `delivery` | Existing runtime-delivery-binding document; exact run/attempt/six identities, revision and delivery time. |
| `native_request` | Original `prepare-gitops-correction` request. Its input references include the exact correction contract. Embedded result/output paths are never opened. |
| `native_result` | Original native result with exactly one matching patch artifact. Completed status alone proves nothing. |
| `native_validation` | Existing RunJournal artifact-validation receipt, linked to exact request/result bytes and ordered before delivery. It is not a native execution receipt. |
| `native_execution` | [runtime-native-execution.schema.json](../../schemas/runtime-native-execution.schema.json): historical v1 host/turn identity or explicit v2 Codex thread/single-turn identity, configured model/client and exact trace/validation/package/request/result/patch bindings. Requires independent origin authentication. |
| `patch` | Actual native-produced patch bytes frozen before correction validation and delivery. |
| `correction` | Actual `correction.json` from the deterministic correction validator, including its complete `rendered_export` byte hashes and input binding; complete coverage and a passing static review are necessary, but its verdict is insufficient. |
| `correction_contract` | Exact reviewed correction scope. |
| `rendered_before`, `rendered_after` | Frozen objects `{resources:[{id,body}]}` from that same correction execution. The verifier reruns `ScopeValidator` over their bodies, checking resource identity, allowed pointers, unchanged image fields and complete contracted-resource coverage. |
| `git_delivery` | [runtime-git-delivery.schema.json](../../schemas/runtime-git-delivery.schema.json): independently observed repository/revision/path, delivered selected-file manifest and rendered-artifact hash. |
| `http:<case-id>` | Original versioned, sealed HTTP probe receipt for the exact case. |

The selected-file manifest digest uses the existing snapshot algorithm: sorted
`path + NUL + file-SHA256 + newline` entries, hashed with SHA-256. File count,
bytes, uniqueness and hashes are rechecked. Base and candidate file sets must
match; the actual changed-file set must equal the correction report and remain
within the reviewed file scope. Candidate and delivered manifests must be equal.
The delivered Git revision must be the recovery intent's selected revision and
must differ from the fault revision.

For recovery inputs, these existing delivery-hook fields have exact meanings:

- `native_handoff_sha256`: SHA-256 of the original native request bytes.
- `validation_receipt_sha256`: SHA-256 of the actual `correction.json` bytes.
- `candidate_tree_sha256`: the existing selected-file manifest digest.
- `patch_sha256`: SHA-256 of the actual patch bytes.

The fault and recovery intents' `rendered_artifact_sha256` fields bind the exact
`rendered_before` and `rendered_after` JSON bytes respectively. The Git delivery
observation repeats the latter hash. The correction CLI exports
`rendered-before.json` and `rendered-after.json` from the same frozen render
callback used by scope validation. Its `correction.json` binds their exact byte
hashes and sizes to the original base commit, selected base/candidate trees,
profile, correction contract and patch. The terminal `correction-manifest.json`
covers both render files and the report. Recovery rechecks that binding as part
of the delivery chain. A newly rendered fixture
or an unbound summary is not equivalent. The provenance adapter must verify
actual correction execution, including trusted tools/schemas and the link from
patch application to candidate tree and renders; this offline module does not
rerun Helm, kubeconform, Git patch application or a model turn.

Python callers retain the three-item return from `validate(args)`. To produce
recovery evidence, pass an empty `rendered_artifacts` dict to
`validate(args, rendered_artifacts=artifacts)`, then pass that same dict to
`write_outputs(..., rendered_artifacts=artifacts)`. The mapping contains immutable
UTF-8 bytes. The writer rejects missing, substituted or extra render artifacts.
Inventories are bounded to 1,000 unique resources and 16 MB per side, cover every
selected target and use the recovery canonical JSON encoding. Incomplete or
ambiguous inventories remain an explicit gap; they are never replaced by a
summary or a second render.

Full render inventories may contain sensitive manifest values. They are saved
only as private evidence files (0700 directory, 0600 files), with no bodies in
the readable reports or CLI stdout. Output writes use the original directory
descriptor; a path replacement prevents the terminal correction manifest.

## Independent inventory and provenance

Collection bundles already contain Deployment/Service UIDs, ReplicaSet/Pod
ownership, container image IDs/restarts, EndpointSlices and controller source
and destination observations. Those fields are reused and checked across all
three phases and both sides of every measured interval. The verifier does not
request a second copy of them.

[runtime-recovery-inventory.schema.json](../../schemas/runtime-recovery-inventory.schema.json)
adds the facts not established by those bundles:

- Authenticated cluster identity and Kubernetes-origin/Argo-destination association.
- Service UID, actual ClusterIPs, cluster domain and observation interval.
- Each probe-source Pod's namespace/name/UID, namespace UID, Pod and namespace
  labels, image ID, and independent observations bracketing the probe interval.
  This canonical slice runs the probe from the approved mission application
  image; independently observed source image IDs must match that runtime image.
- Runtime image ID to qualified OCI platform-manifest/config mapping for every
  container, with the same independently approved mapping receipt hash. The
  manifest/config digests must equal the contract expectations. Runtime image ID
  syntax does not establish whether its digest denotes a manifest or a config;
  the authenticated backend must establish that content mapping.
- The exact Prometheus job/instance, Service UID and endpoint Pod UID.
- Locked CNI/mesh values and an independently qualified plain-HTTP NetworkPolicy
  path, with a qualification receipt hash.

The collector's unkeyed seals and the inventory's hashes are integrity checks,
not authenticity. The `provenance` parameter is a closed adapter-registry name;
**no production adapter is registered yet**. The default report therefore
retains `evidence-origin-authentication-unavailable`. JSON fields cannot enable
authentication or register executable code.

The separate [native provenance caller](native-provenance.md) can directly invoke
the installed Codex driver and the trusted-host correction validator, then pass
its opaque `NativeExecutionOrigin` as `native_execution_origin`. The verifier binds
the exact native execution/request/result/validation/patch bytes and authenticates
only that native assurance. Imported v1/v2 JSON does not recreate the object.
The remaining eight source assurances still require their own origin mechanisms;
the report remains unknown without them. V2 records the observed thread and one
turn ordinal with a null unexposed server turn ID, preserving honest identity
without imposing a provider telemetry field the canonical contract did not require.

The internal adapter protocol is:

```python
verify(*, artifacts: Mapping[str, bytes]) -> OriginVerification
# OriginVerification(receipt_sha256, artifact_sha256, assurances)
```

The immutable mapping includes the contract, collections and every successfully
frozen evidence input. A concrete adapter must authenticate every exact byte
hash and return all nine assurances: operator intent, collection origin,
cluster/destination association, probe origin/source, image content mapping,
native execution, correction execution, actual Git delivery, and NetworkPolicy
path qualification. A JSON `pass` or `authenticated` flag does not satisfy this
protocol. The adapter receipt hash must identify independently verifiable origin
evidence, not a new self-signed claim. The future live driver owns the concrete
authentication mechanism and must not add a permissive adapter to obtain pass.

`_test_provenance` and `now` are Python-only test hooks. Either permanently marks
the result `controlled-test`, as does any controlled collection/probe. Such a
result never produces a live recovery pass. The test suite exercises the same
semantic verifier with explicit synthetic origin checks; it is engineering
proof, not customer or cluster qualification.

## Conditions and decision

[runtime-recovery.schema.json](../../schemas/runtime-recovery.schema.json)
contains `conditions`, `retained_failures`, exact `inputs` hashes, provenance,
required `gaps`, and `recovery_sha256`. Each condition has a fixed ID, phase,
optional target, `observed_state` (`recovered`, `not-recovered`, `unknown`), fixed
reason code, evidence hash/pointer references and an authentication indicator.
`recovered` means that condition is satisfied in its named phase, not that the
entire run has qualified.

The verifier recomputes source/identity parity and controller convergence,
workload readiness, stable Pod/container/image/restart identity, baseline
telemetry, the fault outage, and recovery telemetry. Recovery requires at least
three distinct matched `up`/`mission_events_total` source timestamps, all newer
than delivery, all up, with strictly increasing counters and a fresh final source
timestamp for **each** series at verification time. One fresh series cannot mask
another stale series.
Reused evaluation timestamps, resets, missing series and stale samples remain
unknown. Historical down/flat facts remain visible when current evidence is
stale. Before image snapshots are aged relative to window start; after snapshots
and current telemetry are aged relative to completion/verification time.
Provider origins, actual read intervals, transport-receipt presence and scope
expiry are checked separately. Unresolved collection failures remain required
gaps. Only the explicitly enumerated collection authentication limitations and
the previously unevaluated recovery contract are reassessed here; an arbitrary
new collection gap cannot disappear because its normalized observations look
healthy.

HTTP decisions are reconstructed from the sealed receipt fields and exact
contract/inventory scope. Source claims, DNS results, connected peer, Service
ports, expected body hashes, elapsed bounds and probe timestamps are checked.
All recovery probes start after delivery. Authorized metrics positives must
bracket both negative probes against the same endpoint and appropriate source
identities. Observed ingress policies are evaluated for the supported simple
selector/port case; a broad allow cannot be hidden by a claimed negative probe.
An additional Pod selector key absent from the collected known label subset,
named/ranged ports, IP blocks or complex expressions remains unsupported.

A timeout/refusal records failed connectivity, not proven policy causality.
Argo Healthy and Kubernetes readiness are not HTTP observations. Plain HTTP does
not prove mesh encryption, certificate validity or an ambient HBONE identity
path; unsupported topology remains unknown. Recovery verifies this scoped
contract, not all factory D1–D6 requirements or compliance certification.

Required gaps take precedence: aggregate `decision` stays `unknown`. With all
required evidence and independently authenticated origins, a failed required
condition produces `fail`; all satisfied conditions produce `pass` and
`recovery: verified`. `causality` remains `unproven`. Authenticated observed
failure facts survive unrelated missing evidence. A historical fault outage is
expected evidence of the incident, not itself a current recovery failure.
