# Bounded direct HTTP observations

The mission image includes a standalone, standard-library-only `probe.py`. An
authorized operator can invoke it independently of the app server:

```sh
/opt/mission-status/venv/bin/python /opt/mission-status/probe.py \
  --request /mounted/http-probe-request.json
```

The image's normal entry point remains the mission application. A separate
authorized harness must explicitly select the probe command. The helper does not
create Pods, read Kubernetes, discover endpoints, execute remote commands, mutate
policy, read credentials, or deliver a patch. No container build, live cluster,
Iron Bank provenance, mesh or policy enforcement is qualified by this helper.

## Trusted request

`schemas/http-probe-request.schema.json` defines the version 1 request. The image
contains equivalent strict validation without adding a dependency. Inputs are
bounded to 65,536 bytes; only explicit regular JSON files are read, with no final
symlinks, duplicate keys, non-finite values, unknown fields or in-place changes.
The explicit request is treated as an operator-supplied scope, but its authority
still needs independent authentication before acceptance.

| Field | Meaning |
| --- | --- |
| `run_id`, `attempt_id`, `case_id`, `phase` | Exact harness identities and one observation case; phase is baseline, fault, recovery or observation. |
| `identities` | The six frozen scenario, assertion, package, lock, config and environment SHA-256 identities. Hash presence alone does not prove their origin. |
| `source.expected` | Actual source Pod namespace, name and UID obtained independently by the operator. The helper does not fabricate or discover these values. |
| `source.environment` | Fixed references `DEVSECOPS_PROBE_POD_NAMESPACE`, `DEVSECOPS_PROBE_POD_NAME`, `DEVSECOPS_PROBE_POD_UID`, keyed by `namespace`, `name`, `uid`. No arbitrary environment-variable lookup is supported. |
| `destination` | Explicit namespace, Service name, cluster domain, port, approved path, scheme and actual Service ClusterIP addresses. |
| `expectation` | HTTP status `200`, and an optional exact response body SHA-256 (`null` when not pinned). A dynamic metrics response normally has no expected body hash. |
| `limits` | DNS, connection, response-header and body time budgets, total wall-time budget and maximum response body bytes. |

The harness must populate the three source variables using Kubernetes Downward
API metadata fields, and separately corroborate the actual source Pod through a
trusted API receipt. Missing or mismatched values stop the probe before DNS. Only
matching expected values are copied into the receipt; mismatched environment
values are never echoed. A match still has `authentication: not-verified`.

The expected Pod UID must come from the actual created/existing Pod inventory
before invocation. Do not manufacture a UID or self-bind the expected value from
the same environment being checked. How the authorized harness supplies the
request to that Pod is outside this helper; the helper adds no execution or
Kubernetes authorization path.

`destination.expected_addresses` contains one normalized IPv4 address, one
normalized IPv6 address, or one of each, copied from the selected Service's
actual ClusterIPs. The operator must independently bind those addresses to the
Service UID and frozen environment. They are not derived from DNS. Loopback,
link-local, multicast, unspecified, reserved and IPv4-mapped IPv6 addresses are
rejected, as are noncanonical textual forms. Private ClusterIP ranges are allowed.

The DNS name is exactly `service.namespace.svc.cluster_domain`; resolution uses
an absolute name with a trailing dot and requests only stream/TCP addresses. The
normalized DNS result set must equal the declared address set. The helper opens
one direct connection to the first declared address, verifies its connected peer
is in that exact set, and makes one GET. There is no alternate-address failover,
proxy use, DNS-based endpoint discovery, redirect following or retry.

The approved paths are `/`, `/health`, `/healthz` and `/metrics`, with no query,
fragment or custom headers. The current mission application implements `/health`
on its health listener and `/metrics` on its separate metrics listener. It does
not implement `/healthz`; probing that path correctly records a 404. `/` is for
the separately qualified reference web service. Each actual service/port/path
combination still comes from the explicit request.

## Bounds and supported HTTP surface

Each phase budget is 0.05–5 seconds; the total budget is 0.05–15 seconds, and no
phase may exceed the total. The body limit is 1–65,536 bytes. Response headers
have a separate fixed 16,384-byte limit. Header and body timers close the active
socket, so trickled bytes cannot keep renewing an inactivity timeout. DNS and
connect waits have wall-time deadlines; late DNS does not trigger a connection,
and a late connector result is closed without sending HTTP.

Only plaintext HTTP is implemented in this slice. A declared HTTPS destination
returns `unsupported-scheme` before DNS or connection; there is no downgrade,
insecure TLS flag or system trust bypass. A transparent service mesh may add
transport encryption, but this helper cannot observe or claim that protection.
The selected topology and mesh behavior need independent qualification.

Content-Length framing and connection-close framing are supported. Transfer
encoding (including chunked), duplicate Content-Length fields and compressed
content return explicit unsupported/unknown outcomes. Header blocks require
CRLF lines, valid field names/values, and an actual empty-line terminator;
obsolete folded fields, parser defects, and EOF in headers are rejected.
Invalid or truncated
responses, oversized bodies, DNS errors, connection refusal, stage deadlines and
total deadlines are distinct fixed codes. These are measurements, not evidence
that NetworkPolicy caused a block. Redirect status is a recorded HTTP mismatch,
with no follow-up request and no Location header exposed.

## Receipt and interpretation

The helper emits one `http-probe-receipt` JSON object to stdout, conforming to
`schemas/http-probe-receipt.schema.json`. It includes the validated request,
exact raw/canonical request hashes, actual UTC start/end, monotonic elapsed time,
matching source claims, checked DNS/peer addresses, status, bounded body byte
count/hash, fixed outcome, and a canonical receipt SHA-256 seal. Response bodies,
headers, arbitrary errors and credential values are never included. The harness
must preserve exact stdout bytes and independently authenticate their origin.

`observation.http_decision` is only the direct HTTP observation:

- `pass`: a complete expected 200 response, plus the expected body hash when supplied.
- `fail`: an observed unexpected HTTP status, rejected redirect or body-hash mismatch.
- `unknown`: unsupported, incomplete, invalid or transport evidence without a complete supported response. A previously observed wrong status can remain a failure fact when the body is incomplete.

Process exit codes are 0, 1 and 2 respectively. They are not a policy, phase or
recovery decision. Every receipt has aggregate `decision: unknown`,
`policy_verdict: unknown` and `recovery: not-evaluated`. Required gaps retain
unverified request authority, source Pod identity, destination Service mapping,
receipt origin, policy causality and recovery. Hashes detect changes; they do not
authenticate a source, image, request or receipt.

Python callers may use `probe(request, *, _resolver=None, _connector=None,
_environ=None)` for controlled tests. Any hook forces `execution_mode:
controlled-test` and a required test-evidence gap. There are no corresponding
CLI flags or loopback exceptions. Test connectors can route a declared synthetic
address to a real local HTTP server and supply a simulated peer identity; those
receipts are explicitly not live Service connectivity evidence. Production
`execution_mode: operator` still does not authenticate its claims.

## Positive and negative controls

The authorized harness owns the source Pods, their exact namespace/labels,
Service/image inventory, policy/CNI/mesh snapshots, probe request/receipt journal
and delivery timing. The canonical control matrix should include:

1. Direct mission `/health` and reference web HTTP positives in baseline, fault
   and recovery, using frozen authorized sources and the unchanged image IDs.
2. The same locked metrics destination and port from an authorized scrape source,
   plus wrong-namespace/right-label and right-namespace/wrong-label source Pods.
   A namespace selector and Pod selector intended as an AND must be qualified
   together; ServiceAccount identity is not a substitute for Pod-label evidence.
3. Allowed-source positives surrounding each negative attempt, plus qualified DNS,
   listener, endpoint and transport controls. A timeout, refusal, DNS failure or
   TLS/mesh issue alone cannot identify a policy block.
4. Independent source Pod UID/image/label and namespace-label corroboration,
   actual destination Service UID/ClusterIP binding, and topology-specific CNI
   and mesh enforcement evidence. No marker in this receipt supplies those proofs.

Recovery verification must additionally bind the actual delivered patch/revision
and native handoff, require every probe and functional source sample after that
delivery, and bracket the evidence with stable actual image snapshots. The
HTTP probe is one measured input, not the recovery verifier or authorization to
run the control matrix.
