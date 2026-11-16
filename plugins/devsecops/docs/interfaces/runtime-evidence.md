# Scoped runtime evidence interfaces

These Python adapters collect a narrow Kubernetes snapshot and a Prometheus
scrape/functional window. They do not deploy, reconcile, repair, or declare an
application recovered. Root orchestration must independently bind these receipts
to the intended environment, delivery revision, image digest, repair time, and
immutable evidence files. This implementation has fixture and controlled local
TLS tests; those tests are not live Kubernetes, Iron Bank, or hosted Platform One
qualification.

## Transport and common result

Construct a new `HTTPSReadClient` for each collection. Supply an explicit HTTPS
origin, a token **environment-variable name** when required, and an optional
private CA file. Credentials are resolved by the shared transport. No kubeconfig,
exec credential helper, ambient proxy, insecure TLS mode, redirect, cookie, or
retry is used. The only network method is GET; each adapter further limits the
semantic read endpoints. Transport receipts remain on `client.receipts` and
contain bounded response hashes and request timings, without response bodies,
headers, tokens, or query text.

Results share this envelope:

```json
{
  "schema_version": 1,
  "provider": "kubernetes",
  "execution_status": "complete",
  "decision": "pass",
  "confirmed_failure": false,
  "observation_kind": "scoped_runtime_snapshot",
  "scope": {"namespace": "demo", "deployment": "mission", "service": "mission"},
  "observed_at": "2026-10-26T12:00:00+00:00",
  "observations": {},
  "gaps": []
}
```

Any required gap yields `execution_status=partial` and `decision=unknown`.
`confirmed_failure` can remain true when a failure was actually observed and
another source is unavailable. Missing evidence alone is not a confirmed failure.
The two observation kinds describe their narrow checks; neither is a global
recovery, security, compliance, or availability result. Configuration errors fail
before requests with a safe `ValueError`; collection errors produce safe gaps.
API free-text diagnostics are never copied into gaps.

## Kubernetes

```python
KubernetesCollector(client, max_pages=5, max_items=100).collect(
    namespace, deployment, service,
    expected_deployment_uid=None, expected_generation=None, now=None,
)
```

Configure the transport with `max_requests=32` and `total_timeout=30` for this
collector. The normal path makes nine reads; pagination consumes the same global
request, byte, and time budgets. The per-resource list cap is 100 items and five
pages by default, with explicit upper configuration bounds. Reaching any cap
returns an evidence gap rather than silently truncating a successful result.

The endpoint allowlist is:

| API | Scope and purpose |
| --- | --- |
| `apps/v1` Deployment GET | Explicit namespace/name, before and after collection |
| `v1` Service GET | Explicit namespace/name, before and after collection |
| `apps/v1` ReplicaSet LIST | Namespace and Deployment's exact `matchLabels` selector |
| `v1` Pod LIST | Same namespace/selector, verified ReplicaSet controller ownership |
| `discovery.k8s.io/v1` EndpointSlice LIST | Namespace and exact `kubernetes.io/service-name` label |
| `networking.k8s.io/v1` NetworkPolicy LIST | Bounded namespace list; policy applicability cannot be inferred from policy metadata labels |
| `v1` Event LIST | Namespace and exact `involvedObject.uid` of the Deployment |

There are no reads of Secrets, logs, node resources, cluster-wide lists, watches,
exec, attach, proxy, or port-forward. Events currently cover the Deployment only.
The collector emits selected structured fields, dropping annotations, environment
values, arguments, managed fields, condition messages, event messages, and other
free text. Event/container reasons use a small fixed vocabulary; unknown free-text
reasons are omitted or replaced with `Other`.
Event occurrence timestamps are retained separately from collection time; old
events are historical context and do not establish a current failure.

`observations` contains `deployment`, `service`, `replicasets`, `pods`,
`endpoint_slices`, `network_policies`, `deployment_events`, `workload_ready`, and
`image_snapshot` when the relevant evidence is available. It retains resource
UIDs/versions, generation and observed-generation counts, controller ownership,
readiness, container image IDs/restart counts, Pod IPs, EndpointSlice Pod references
and addresses, Service ports/selectors, and normalized NetworkPolicy selectors,
peers, ports, and directions.

Supported workload selection is a Deployment with nonempty `matchLabels` and an
exactly matching Service selector. Expression-based Deployment selection,
selectorless/external Services, ephemeral containers, unknown API shapes, and
unsupported endpoint address families return required gaps. NetworkPolicy
expressions are normalized, but policy presence does not prove CNI or mesh
enforcement. Service/EndpointSlice fields are evidence for diagnosis; this
collector does not open application ports or prove traffic delivery.

ReplicaSet → Deployment and Pod → ReplicaSet controller UIDs must match.
EndpointSlices must belong to the selected Service; every referenced Pod UID/name
and endpoint address must match collected Pod evidence. Deployment and Service
rechecks detect identity/status/version changes. Lists require a stable collection
resource version across pages and reject duplicate resources or repeated tokens.
An expired continuation (`410`) produces an explicit gap; no fallback unbounded
list or cache is used. These are bounded reads, not an atomic cross-kind snapshot.
Kubernetes documents collection versions and continuation consistency in its
[API concepts](https://kubernetes.io/docs/reference/using-api/api-concepts/).

Current Deployment and active ReplicaSet observed generations must match. A
complete supported snapshot passes only with positive desired replicas, matching
updated and sufficient ready/available counts, no negative Deployment condition, enough ready
Pods, and a ready nonterminating endpoint. This narrow readiness interpretation
uses the official [Deployment](https://kubernetes.io/docs/reference/kubernetes-api/apps/deployment-v1/),
[EndpointSlice](https://kubernetes.io/docs/reference/kubernetes-api/discovery/endpoint-slice-v1/),
and [NetworkPolicy](https://kubernetes.io/docs/reference/kubernetes-api/networking/network-policy-v1/)
field definitions. API compatibility with a selected customer's version still
requires qualification.

## Prometheus

```python
PrometheusCollector(
    client, window_seconds=120, step_seconds=15,
    max_sample_age_seconds=45, minimum_samples=3,
).collect(
    namespace, service, job, instance,
    image_evidence={"before": before_snapshot, "after": after_snapshot},
    delivered_at=None, now=None,
)
```

Namespace, Service, job, and instance are required exact selectors. The collector
does not accept arbitrary PromQL, metric names, regex matchers, URLs, or headers.
It issues four GET `/api/v1/query_range` requests, each limited to two result
series (multiple results are rejected):

```promql
up{namespace="demo",service="mission",job="mission",instance="10.0.0.4:9090"}
timestamp(up{namespace="demo",service="mission",job="mission",instance="10.0.0.4:9090"})
mission_events_total{namespace="demo",service="mission",job="mission",instance="10.0.0.4:9090"}
timestamp(mission_events_total{namespace="demo",service="mission",job="mission",instance="10.0.0.4:9090"})
```

Query-range evaluation timestamps do not prove fresh scrapes. The paired
`timestamp(metric)` queries recover underlying sample timestamps; interpolated
repetitions count once. This distinction follows Prometheus's
[timestamp function](https://prometheus.io/docs/prometheus/latest/querying/functions/#timestamp)
and [range-query API](https://prometheus.io/docs/prometheus/latest/querying/api/#range-queries).
Exactly one matching float series, complete evaluation grids, matching label and
source-time windows, and at least three distinct fresh samples are required.
Source timestamps cannot regress, be in the future, exceed the age bound, or have
gaps exceeding it. Warnings, partial responses, NaN/Inf, native histograms,
unsupported shapes, missing/multiple series, and sample races remain unknown.

`observations.scrape` records source timestamps, `up` values, and fresh sample
count. `observations.functional` separately records `mission_events_total`
progression. Scrape success alone never establishes functional success. An
observed `up=0` or stalled counter is a narrow confirmed failure. Counter resets
prevent uninterrupted progression evidence. Every fresh counter sample must
increase; the collector does not extrapolate between samples.

If `delivered_at` is provided as an aware datetime or ISO timestamp, **every used
source sample must be strictly later than it**. A query executed after a repair
does not satisfy this rule if it returns older samples. Choose a window wholly
after the selected delivery/repair; the collector will not trim away inconvenient
pre-delivery samples to manufacture success.

`image_evidence` requires two concrete snapshots with namespace, Service,
Deployment UID, observation time, and nonempty containers containing Pod UID/name,
container name/type, SHA-256 runtime image ID, and restart count. Bare stability
booleans are rejected. Identities, digests, and restart counts must remain equal.
This comparison retains both normalized snapshots and its caller-supplied basis;
it does not authenticate the supplied evidence itself. Root orchestration must
bind it to independently collected Kubernetes artifacts and their hashes.

Kubernetes snapshots include collection interval bounds `observed_from` and
`observed_through`, with `observed_at` at completion. The before interval must end
at or before the metric window starts; the after interval must start at or after
it ends. Each collection interval and boundary age is capped by
`max_sample_age_seconds`. Before age is measured relative to **window start**,
not window end. To use a real after snapshot, set the metric window end (`now`)
from its `observed_from`; the later completion may be within the configured age
bound. Imported point observations without intervals use `observed_at` for both
bounds. Clock synchronization and trustworthy timestamp provenance remain root
integration requirements.

No `/api/v1/targets` fallback, broad discovery, configuration reload, administrative
API, or application request is made. A hosted profile that denies query access,
omits the required target labels, hides source timestamps, or lacks the functional
metric has a required visibility gap. Argo `Healthy`, an accessible dashboard, or
a healthy application HTTP port cannot replace that evidence.

## Validation boundary

The adapter tests exercise malformed and adversarial fixtures, scoped pagination,
ownership and generation races, unavailable evidence, redaction, source freshness,
duplicate interpolation, counter resets/stalls, image changes/restarts, delivery
boundaries, and snapshot bracketing. Controlled localhost TLS tests run both
collectors through the actual shared HTTPS transport; the shared transport tests
cover certificate validation, deadlines, response limits, and token redaction.
No test here contacts or qualifies a customer cluster or hosted metrics tenant.
