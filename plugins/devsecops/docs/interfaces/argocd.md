# Argo CD Application evidence — preview

This adapter collects narrowly scoped controller evidence. A `pass` means the observed Application is synced and healthy for the selected Git revisions and the observed configuration. It does **not** mean the application works, an incident recovered, the renderer matches the controller, or a deployment is approved.

The implemented wire contract is **Argo CD 3.5.6**. Build metadata such as `v3.5.6+abc123` is accepted; a different release, prerelease, or unrecognized version produces a required gap. Tests use a real local TLS server with synthetic Argo responses. They are transport and normalization tests, **not live Argo qualification**. Hosted, native-skill, human-replay, and functional-recovery gates remain separate.

## Official contract and passive routes

The implementation follows the versioned [application API proto](https://github.com/argoproj/argo-cd/blob/v3.5.6/server/application/application.proto), [Get implementation](https://github.com/argoproj/argo-cd/blob/v3.5.6/server/application/application.go), [Application types](https://github.com/argoproj/argo-cd/blob/v3.5.6/pkg/apis/application/v1alpha1/types.go), and [version API proto](https://github.com/argoproj/argo-cd/blob/v3.5.6/server/version/version.proto). General authentication guidance is in the [official API documentation](https://argo-cd.readthedocs.io/en/stable/developer-guide/api-docs/).

The adapter makes exactly three requests, in order:

1. `GET /api/version`.
2. `GET /api/v1/applications/{application}?project={project}&appNamespace={namespace}`.
3. The same Application GET, to detect changes during collection.

All scope fields are explicit. Returned name, namespace, and project must match them. There is no cluster-wide listing or namespace fallback. The account should have only the Argo `applications, get` permission for the selected project/application namespace/name. Server authorization remains required; client scoping is an additional restriction.

The typed Application REST projection may omit both Kubernetes `apiVersion` and `kind`. That exact omission is accepted only for the scoped Application route above. If either field is present, both must identify `argoproj.io/v1alpha1` / `Application`; partial or conflicting TypeMeta is rejected. The adapter does not add missing fields, and all version, scope, stable identity, selected revision, comparison-input, and freshness checks still apply.

The distinction between a passive read and a GET request matters here. The pinned server implementation returns without refreshing only when `refresh` is **absent**. Even `refresh=` takes the reconciliation branch. This adapter never sends it. It exposes no arbitrary Argo path or query option, and never requests manifests, `noCache`, resource trees, logs, resource actions, server-side diffs, sync, exec, or session creation. Manifest generation can cross renderer/plugin boundaries, so it needs its own qualified contract before inclusion.

## Python interface

```python
from devsecops.adapters.http import HTTPSReadClient
from devsecops.adapters.argocd import collect_argocd

client = HTTPSReadClient(
    "https://argocd.example.test",
    token_env="DEVSECOPS_ARGOCD_TOKEN",
    ca_bundle="/operator-managed/private-ca.pem",
)
receipt = collect_argocd(
    client,
    project="demo",
    application_namespace="argocd",
    application="mission",
    selected_revisions=selected_git_ids_by_position,
    max_age_seconds=300,
)
```

`selected_git_ids_by_position` comes from trusted frozen intent. It maps every 1-based source position to a full lowercase 40- or 64-character Git object ID. Integer positions and canonical decimal string positions are accepted; duplicate representations are rejected. Floating branches, abbreviated SHAs, and incomplete mappings are rejected or produce required gaps. A one-element `spec.sources` array uses the multi-source `status.sync.revisions` wire field, just like a larger array.

Git-backed plain manifests, Helm, Kustomize, and Git values references can be related by their resolved revisions. Chart/OCI version resolution, plugin or hydration modes, unknown source fields, and ambiguous simultaneous singular/plural source declarations produce required gaps. Collection does not render any of those sources. The application must expose its project explicitly in this initial contract.

`now` is an optional aware datetime for deterministic tests; production callers omit it. This slice provides a library interface. The factory/native integration must validate its input contract, compare captured source/spec digests with frozen reviewed inputs, and retain the result inside the versioned evidence package. A matching Git SHA alone does not prove matching Helm values, Kustomize inputs, destination, or controller configuration.

## Evidence and decisions

The shared envelope is:

```text
schema_version: 1
provider: argocd
execution_status: complete | partial
decision: pass | fail | unknown
confirmed_failure: boolean
scope: {origin, project, application_namespace, application}
observed_at: UTC timestamp
observations: {
  api_contract, server_version, spec_digest_format, destination_spec_sha256, collection, receipts,
  application, sources, comparison, freshness, generation,
  sync_status, health_status, operation, condition_types, resources, decisions
}
gaps: [{code, message, required}]
```

The adapter checks UID, generation, resource version, and desired-spec digest before and after. Any change is a required race gap. `status.sync.comparedTo` must match desired sources, destination, and ignore-differences inputs. Resolved revisions must exactly match every selected source position. The selected mapping and per-source spec hashes are retained; arbitrary source configuration and repository URLs are not exported.

`observations.spec_digest_format` is `devsecops-json-sha256-v1`: SHA-256 of Python `json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('utf-8')`. This is a versioned project format, not RFC 8785. `sources[*].source_spec_sha256` covers each complete source object, including renderer settings and inline values; `destination_spec_sha256` covers the complete destination object, or is null when that object is unavailable. `application.spec_sha256` covers the entire desired Application spec. No omitted/default fields are normalized.

The runtime verifier must compare every source position and the destination against hashes from separately authenticated frozen reviewed intent, alongside selected immutable revisions and stable controller comparison inputs. Matching supplied hashes establishes equality only. It does not authenticate where the expectations came from or prove renderer/controller parity. Missing intent authentication remains a required runtime evidence gap until an actual originating-receipt verifier is implemented; reading expected hashes back from the same API is not independent proof.

Freshness comes from `status.reconciledAt`, with a default maximum age of 300 seconds and a 30-second allowance for future clock skew. Missing, stale, or implausible time is a required gap. HTTP receipt time and server Date do not substitute for reconciliation time. This Argo version has **no Application status observedGeneration**. The adapter records observed generation as unavailable and an advisory gap; it never invents it or uses deprecated `status.observedAt`. Stable identity and `comparedTo` provide narrower evidence.

Sync, health, operation, and functional assessments remain separate. An operation is related to the selected target only through its immutable revisions and source configuration; historical operations can concern a different target. A failed selected-source operation that conflicts with current Synced/Healthy state produces an unknown result requiring investigation. Active or requested operations also prevent a settled-state result. Missing context is not inferred from a healthy status.

Required gaps force `execution_status: partial`, `decision: unknown`, and `confirmed_failure: false`. With complete evidence, current OutOfSync or Degraded/Missing state can confirm a controller failure. Progressing or Suspended health remains unknown. Positive controller state always leaves `observations.decisions.functional: unknown`.

Managed-resource summaries contain only bounded identities, sync, and health enums. They are the controller's claims, not independent ownership or runtime validation. The Kubernetes adapter must verify owner chains and live state, and functional probes/metrics must establish recovery. Arbitrary messages, annotations, operation bodies, resource/Secret bodies, Helm values, logs, and credentials are excluded. Raw API responses exist only in bounded process memory; receipts retain hashes and lengths, not bodies.

## Shared HTTPS read client

`HTTPSReadClient(origin, *, token_env=None, ca_bundle=None, client_certificate=None, timeout=5.0, total_timeout=20.0, max_body_bytes=2_000_000, max_requests=8, max_total_bytes=16_000_000)` provides `get_json(path, params=None) -> dict` and a deep-copy `receipts` property. Credentials are resolved from the named environment variable, never from literal token configuration. Private CA files are explicit; otherwise the platform trust store is used. Certificate and hostname verification are mandatory, with TLS 1.2 or newer.

An operator may supply `client_certificate=(certificate_path, key_path)` directly to the Python constructor for mutual TLS. Both paths must be absolute, nonsymlink regular files, each at most 1 MB; the private key must be owned by the current operator and private. Bytes are bounded and frozen before OpenSSL loads temporary private copies, which are then removed. Encrypted keys fail without an interactive password prompt. Errors use only `client-certificate-unavailable`; keys, certificate bodies, and paths never enter transport receipts. This constructor extension does not add kubeconfig execution, a runtime configuration field, or authority to embedded evidence. Real localhost mTLS, invalid-file, and slow-header/body deadline tests are in `tests/test_http_mtls.py`.

The origin must be HTTPS with no userinfo, path prefix, query, or fragment. Request paths cannot override that origin or embed queries. Params are a separate bounded mapping. There is no redirect following, cookie jar, retry, implicit proxy, cross-origin forwarding, or arbitrary per-request header input. Deployments needing an explicit proxy or URL prefix require a separate transport extension; the client does not silently weaken these controls.

Clients are scoped to one sequential collection. Budgets never reset between requests, and concurrent calls are rejected. Caps are 30 seconds per request, 120 seconds per collection, 64 requests, 8 MB per body, and 64 MB total body bytes. The Kubernetes collector can request a larger count than the default. DNS waiting, connection/TLS, headers, and body reads are bounded. An OS resolver thread may finish after its caller times out, but it never connects or receives credentials. HTTP headers retain the standard library's bounded parser limits. Compressed response bodies are rejected; JSON rejects duplicate keys, nonfinite values, excessive depth/node count, and non-object roots.

`APIError.code` and `.status` expose safe category/status only. Remote diagnostics, headers, URLs, and response bodies are not included in error messages. Receipts include bounded timing, status, request-target hash, bytes received, complete-body hash when available, and outcome. They omit authorization and query contents. Parsed server Date is informational, never evidence freshness.

Run `python -m pytest tests/test_argocd.py` in the project environment. The tests generate a temporary localhost certificate using OpenSSL; no private key or credential is committed. Test cases cover TLS trust/hostname failures, proxy exclusion, redirect and error redaction, credential header rejection, DNS/header/body deadlines, cumulative bounds, strict JSON, cross-scope responses, races, version/source-mode gaps, source ordering, stale evidence, and historical-operation counterexamples.
