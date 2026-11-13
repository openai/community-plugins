# Runtime evidence and GitOps diagnosis

`devsecops-review collect-runtime` starts scoped Argo CD and Kubernetes reads and
records a fixed observation window. `resume-runtime` finishes that same window
and queries the declared Prometheus series. `diagnose-gitops` compares recorded
baseline and fault bundles offline. These commands produce a local HTML report,
Markdown, structured evidence and a terminal manifest with artifact hashes.

This development slice does not establish image provenance, authenticate a
delivery, execute application HTTP probes or decide recovery. Its aggregate
decision is always `unknown`, with exit code **2**, including a successfully
collected observation. Provider observations and actual failures remain visible.
Exit 3 is reserved for the separate native workflow handoff protocol.

## Collect and resume

Use trusted external operator configuration and intent. Candidate repository
content, API responses and model-generated reports must never authorize an API
origin, credential reference, namespace or resource scope. Review the published
[runtime interface](interfaces/runtime-collection.md) and schemas before creating
these files. The CLI accepts explicit receipt paths and credential **references**;
it has no credential-value, clock override, injected-client or discovery flag.

```sh
devsecops-review collect-runtime \
  --config /approved/demo-config.json \
  --lock /approved/demo-lock.json \
  --intent /approved/baseline-intent.json \
  --receipt environment-inventory=/approved/inventory.json \
  --receipt mission-image-mapping=/approved/mission-image.json \
  --receipt reference-image-mapping=/approved/reference-image.json \
  --receipt reviewed-source-intent=/approved/source-intent.json \
  --output /private/reviews/baseline-start
```

The receipt IDs must match those declared in the intent. For a recovery phase,
also provide `--delivery-binding /approved/delivery-binding.json`. A matching
binding remains a claim until the independent verifier authenticates it.

The stdout summary gives `next_eligible_at` and `expires_at`. Resume within that
interval using a **new** output directory:

```sh
devsecops-review resume-runtime \
  --config /approved/demo-config.json \
  --lock /approved/demo-lock.json \
  --intent /approved/baseline-intent.json \
  --context /private/reviews/baseline-start/runtime-context.json \
  --pending /private/reviews/baseline-start/runtime-collection.json \
  --output /private/reviews/baseline-finished
```

Resume requires the original **separately trusted** config, lock and intent.
They authorize the destinations, credential references, selectors and resources;
a saved context or its public hash cannot authorize a connection. Both Python
network entry points require the same explicit `authority` mapping. Current CA
state and hashes are derived from that trusted config. Snapshot paths are inert:
the collector never follows a config, receipt or delivery path found in evidence.
If the first collection used a delivery binding, supply its original explicit
`--delivery-binding` again; both exact bytes and claims must match.

An early resume performs no provider reads. An expired window stays incomplete;
start another collection instead of moving its timestamps. The explicitly supplied
inputs and CA files must match their frozen identities. No command waits in the
background, renews credentials, refreshes controllers or changes the cluster.

Prometheus evaluation timestamps and actual underlying sample timestamps remain
separate. Reports distinguish samples in the recorded window from freshness when
the reads completed. Repeated evaluation points do not count as additional
scrapes. Kubernetes readiness does not substitute for application HTTP health.

## Diagnose recorded changes

```sh
devsecops-review diagnose-gitops \
  --baseline /private/reviews/baseline-finished/runtime-collection.json \
  --fault /private/reviews/fault-finished/runtime-collection.json \
  --output /private/reviews/diagnosis
```

Diagnosis validates bounded inputs, schemas, seals, identities and aligned
windows. It reads only the explicit bundle files; historical config and CA paths
embedded inside them are not reopened. Missing, invalid, foreign, stale or partial
evidence remains an explicit gap. Omitting `--baseline` is allowed and cannot
produce a completed comparison. Alternative order reflects supporting
observations, not proven causality or statistical confidence.

## Artifact integrity and interruption

Each invocation creates a new directory with mode `0700`; files use `0600`.
Existing outputs and symlink ancestors are rejected. Directory-relative writes
prevent an ancestor replacement from redirecting evidence. The frozen context is
saved before network reads. The terminal `runtime-manifest.json` is written last,
after all listed artifacts have been flushed. The output path is checked again
after that write; a detected swap withdraws the terminal marker and prints no
artifact paths. Its `finished` field describes the
CLI invocation, not successful recovery or completed qualification.

A crash can leave a partial directory with no terminal manifest. Preserve it for
investigation and choose another output directory; never treat partial files as
success. Expected input errors create an unknown error report without echoing
untrusted input or credentials. An output I/O error can prevent any final report.

Unkeyed hashes detect content changes. They do **not** authenticate an observation,
receipt, source, package or environment. A factory workflow must bind each input's
raw bytes and sealed content identity to its immutable trusted journal before
using the observations in an acceptance decision. Standalone reports explicitly
retain this missing authentication.

Python testing clients or clock overrides mark a collection `controlled-test`;
that mode persists through resume and diagnosis and appears prominently in its
HTML report. It cannot qualify a live run. `direct` means the collector used its
ordinary transport and clock, not that a vendor or environment was authenticated.
