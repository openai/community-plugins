# Frozen, resumable runtime collection

This module collects bounded observations. It does **not** approve an environment,
authenticate image provenance, deliver a change, execute an application probe,
or decide that a service has recovered. Every aggregate collection document has
`execution_status: partial` and `decision: unknown`. Narrow provider results retain
their original decisions and `confirmed_failure` preserves observed failures.

## Python API

```python
from pathlib import Path
from devsecops.runtime import (
    freeze_runtime_context, start_runtime_collection, finish_runtime_collection,
)

authority = {
    "config": Path("/approved/demo-config.json"),
    "lock": Path("/approved/demo-lock.json"),
    "intent": Path("/approved/runtime-intent.json"),
}
context = freeze_runtime_context(
    authority["config"], authority["lock"], authority["intent"],
    receipt_documents={
        "environment-inventory": Path("/approved/environment-inventory.json"),
        "mission-image-mapping": Path("/approved/mission-image-mapping.json"),
        "reference-image-mapping": Path("/approved/reference-image-mapping.json"),
        "reviewed-source-intent": Path("/approved/reviewed-source-intent.json"),
    },
)
pending = start_runtime_collection(context, authority=authority, delivery_receipt=None)
# Persist context and pending through the caller's immutable evidence journal.
# Return pending["window"]["next_eligible_at"] to the operator/harness. Do not sleep here.
# A separate later invocation, still within window.expires_at:
measured = finish_runtime_collection(context, pending, authority=authority)
```

The complete signatures are:

```text
freeze_runtime_context(factory_config, factory_lock, phase_intent,
                       *, receipt_documents=None, now=None) -> dict
start_runtime_collection(context, *, authority, delivery_receipt=None, clients=None, now=None) -> dict
finish_runtime_collection(context, pending, *, authority, delivery_receipt=None, clients=None, now=None) -> dict
```

Configuration, lock, intent, receipt values and delivery binding accept explicit
`pathlib.Path` arguments or JSON dictionaries. The mandatory `authority` mapping
has exactly `config`, `lock` and `intent`, obtained independently from the approved
operator source. Never construct it from saved context or pending evidence.
Both network entry points revalidate these explicit inputs, their exact raw and
canonical hashes, cross-bindings and current CA state before creating clients.
Public seals cannot authorize a destination or credential reference.

All historical input/receipt/delivery paths in evidence are inert. `verify_context`
checks only structure and integrity; `verify_runtime_authority` provides the
separate read-authorization binding. A resumed delivery binding must be supplied
explicitly again with unchanged bytes and claims. `now` and `clients` are Python testing hooks; they are not operator
flags. Client injection is keyed by provider ID or `(target_id, provider_id)` and
must still match the configured origin and pass scope/read-identity checks.
Using either testing hook sets `execution_mode: controlled-test` and retains a
required gap. Resume cannot silently promote a controlled pending bundle to
direct execution. The ordinary mode `direct` is not vendor qualification.

Invalid schemas, mismatched immutable bindings, changed files, and broken seals
raise sanitized `ValueError` before provider requests. Unavailable services,
unsupported auth, missing evidence, API denial, partial collections and unknown
provenance remain explicit required gaps. The caller owns durable writing and
terminal error artifacts; the module writes nothing.

## Operator intent and schemas

The executable schemas are:

- `schemas/runtime-intent.schema.json`
- `schemas/runtime-context.schema.json`
- `schemas/runtime-delivery-binding.schema.json`
- `schemas/runtime-collection.schema.json`

Intent names `run_id`, `attempt_id`, `phase`, `environment`, `profile`, and
`scenario_id`. Its `identities` object preserves all six journal identities:
`scenario_sha256`, `assertions_sha256`, `package_sha256`, `lock_sha256`,
`config_sha256`, and `environment_sha256`. Config/lock byte identities are checked
against actual input bytes; scenario/assertions identities must equal the lock.
Package/environment hashes are retained claims awaiting independent provenance
verification. Their presence does not authenticate an environment or package.

`targets` is a bounded list of 1–8 named targets with a list of roles. Each target
declares:

- Kubernetes service reference, namespace, Deployment and Service names; expected
  Deployment UID/generation and Service UID when known. Missing identity fields
  stay required gaps. Both Service observations are compared to the frozen UID,
  and their immutable identities must also agree with each other.
- Argo service reference, project, Application namespace/name, complete 1-based
  source positions, immutable Git revisions, credential-free HTTPS repository
  URLs, relative source paths, renderer, expected full source-spec SHA, render
  artifact SHA, source-intent receipt, and expected destination.
- A complete Argo destination-spec SHA and originating intent receipt. The
  recorded `server`/`namespace` and repository/path fields are intent metadata;
  authenticating their relationship to the full hashed specs is a separate
  verifier obligation.
- An image-mapping receipt reference. Mapping integrity is recorded, but this
  layer does not assert that a runtime image ID equals an OCI index digest or
  that the derived application inherited approval from its base image.
- Optional exact Prometheus namespace, Service, job and instance. It must refer
  to the same Kubernetes Service/namespace as that target.

For `ironbank-mission-recovery-v1` only, exactly one `mission` role and one distinct
`reference-web` role are required, and the mission target must declare telemetry.
Other scenarios do not inherit a universal two-application rule. The current
factory-lock schema itself remains canonical-only; supporting another scenario
requires an explicit factory contract change, not bypassing that validation.

Both config `scope.namespaces` and `scope.applications` constrain each provider.
Kubernetes requires both declared Deployment and Service names in the applications
allowlist; Argo requires the selected Application name, both the actual Application
namespace and destination namespace, and the selected project in `argocd_projects`;
Prometheus requires the selected Service and namespace. Argo's control namespace
and project are additionally passed as exact API parameters. Missing Argo project
or namespace authorization produces a gap before any Argo request. An origin alias or
internal Argo destination still needs independent inventory proof that it names
the intended cluster.

## Fixed window and resume semantics

The factory lock must provide `observation_window_seconds`,
`scrape_interval_seconds`, `freshness_seconds` and `required_fresh_scrapes`.
Its current validator requires a window divisible by the interval, at least two
intervals, at most 241 evaluation points, 3 or more distinct fresh samples, and
an interval no greater than the freshness bound (at most 300 seconds). This layer
never supplies an unrecorded window or weakens those bounds on retry.

Start reads every selected Argo Application, then each Kubernetes target. It
sets the window start to the **ceiling in epoch seconds of the latest completed
before image interval**, and the end to start plus the locked window. At least one
recorded before image interval must exist to schedule a window. Every target still
requires complete concrete image evidence. Earlier snapshots still have to
meet the freshness bound relative to that common start. There is no wait or sleep.

Pending output is always incomplete. It preserves the context snapshot, all
input/receipt hashes, provider receipts and before evidence, and returns exact
`next_eligible_at` and `expires_at`. Calls before the end preserve pending status
without reads. Calls after end plus freshness, or after environment expiry, are
blocked without reads. A measured/blocked bundle cannot be reused as pending.

Finish validates the saved context, raw files, referenced receipt files, private
CA bytes, pending seal, target set, immutable delivery binding and recomputed
window **before** reads. It then obtains after Kubernetes snapshots and Argo
observations, and queries exactly the frozen Prometheus range. It never moves
the range forward to obtain a favorable result. Before freshness is measured
relative to the window start; after freshness is measured relative to its end.

Prometheus uses underlying `timestamp(metric)` values, not interpolated query
grid times, to count distinct fresh scrapes. Only the approved `up` and
`mission_events_total` queries run. All four exact-label series must agree.
Missing/multiple/stale series, a restart, counter reset, source-time mismatch,
or changed image identity remain unknown. There is no `/targets` fallback for
a hosted tenant with limited metrics visibility.

`transport.started_at/finished_at` and HTTPS request receipts record actual read
times. The Prometheus provider's `observed_at` is the **frozen range endpoint**
because that adapter's time argument controls query range selection; it must not
be interpreted as the time the HTTP request was issued. The containing bundle
records its actual completion time independently.

## Read identities, limits, and integrity

The layer reuses factory `services[].origin/read_auth/ca_file/scope`. Only none or
bearer read auth is supported by these providers. Basic auth is an explicit gap.
An identical read/operator environment-variable reference is rejected. No ambient
kubeconfig, exec credential plugin, operator token, or customer credential scan is
used. Different environment variable names are not proof that their identities or
RBAC permissions actually differ; qualification must verify that separately.

Each phase has a 120-second wall budget. Each target/provider client has at most
20 seconds, 32 requests, 2 MB per response and 16 MB of response bytes. There are
at most eight targets, and the serialized result is bounded at 16 MB. The transport
uses explicit HTTPS/CA verification, no redirects, proxy environment, cookies or
retries. No logs, Secrets, pod exec, port forwarding, refresh, writes, installs,
or direct application/negative probes are performed.

File inputs retain raw-byte SHA-256 and a separate canonical content SHA. In-memory
documents use `devsecops-runtime-json-sha256-v1`: Python JSON with sorted keys,
compact separators, ASCII escaping, non-finite values rejected, **plus one newline**.
Argo full-spec comparison uses its separate `devsecops-json-sha256-v1` encoding,
which omits that newline. Neither encoding is RFC 8785.

`context_sha256` and `bundle_sha256` cover all other fields using the runtime
encoding. A completed bundle also preserves its exact `pending_sha256`. These are
unkeyed integrity seals, not signatures. Callers must retain them in the trusted
immutable journal. An attacker able to replace documents and recompute hashes
has not been authenticated by this module.

## Required trust and recovery work outside collection

Matching referenced receipt bytes always retain `authentication: not-verified`.
Full source/destination spec equality removes a parity mismatch, but cannot clear
the originating source-intent authentication gap. Missing inventory, image mapping,
source intent, expected Service UID, telemetry-to-workload binding, or parity remains visible even when provider
readiness and telemetry pass.

An optional `runtime-delivery-binding` freezes run/attempt/environment identities,
target/source position, delivered immutable revision and delivery timestamp, plus
native handoff, patch, candidate-tree and validation-receipt hashes. The revision
must match that source position; reused attempts and future delivery timestamps
are rejected. Every used Prometheus source sample must be strictly newer than
delivery. The hook preserves a future real native-patch chain but does not prove
that those bytes were proposed, reviewed, merged, or delivered. A recovery phase
without it has `delivery-binding-missing`.

A future independent verifier must authenticate the environment and approved
read scope; relate exact source specs, renderer inputs and rendered artifacts;
validate actual image index/platform/config/runtime relationships; and bind the
real native patch, validation, delivery and deployed revision. Canonical recovery
also requires the unchanged reference NGINX proof, direct NGINX and mission HTTP
receipts, and the complete authorized positive/negative network-probe matrix with
DNS/listener/connectivity controls. No provider's pass, including Argo Healthy,
substitutes for any of these obligations. This module intentionally contains no
verification function or global recovery claim.

Tests compose the real adapters with synthetic API fixtures and an actual local
TLS server. They demonstrate software behavior, not live cluster, hosted tenant,
Iron Bank, native-delivery, or customer qualification.
