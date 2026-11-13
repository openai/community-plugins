# OCI content identity resolution

This library reads one immutable image reference from one configured HTTPS registry and repository. It verifies raw-byte SHA-256 identities for an index, its selected platform manifest, and the configuration blob. These are separate identities. It does not fetch layers, execute images, inspect configuration environment variables, verify publisher signatures, authenticate attestations, or qualify a vendor registry.

The implementation follows the scoped pull and digest contracts in [OCI Distribution 1.1.1](https://github.com/opencontainers/distribution-spec/blob/v1.1.1/spec.md), the [OCI manifest](https://github.com/opencontainers/image-spec/blob/v1.1.1/manifest.md), [index](https://github.com/opencontainers/image-spec/blob/v1.1.1/image-index.md), and [configuration](https://github.com/opencontainers/image-spec/blob/v1.1.1/config.md) definitions. OCI image/index/config and Docker schema 2 media types are supported. Legacy signed schema 1 and artifact configurations are unsupported. This bounded adapter is not a general OCI conformance implementation.

## Library interface

```python
from devsecops.adapters.oci import collect_oci

evidence = collect_oci(
    registry_origin,
    repository="ironbank/opensource/nginx/nginx",
    requested_digest=selected_sha256_digest,
    architecture="amd64",
    allowed_repositories=["ironbank/opensource/nginx/nginx"],
    basic_username_env="REGISTRY_READ_USERNAME",
    basic_password_env="REGISTRY_READ_SECRET",
    ca_bundle=approved_private_ca_file,  # or None for the platform trust store
    token_path=approved_same_origin_token_path,
    token_service=approved_token_service,
)
```

The origin and repository allowlist come from trusted caller configuration. Only lowercase `sha256:<64 hex>` requests are accepted; there is no tag lookup, repository discovery, or registry catalog operation. The OS is explicitly Linux. Architecture is explicit; `variant` is optional. A selected variant must also be present in and agree with the configuration. Without a variant, exactly one matching Linux architecture entry is required. An index is limited to 256 descriptors and one level: ambiguous selection, missing platform mappings, selected nested indexes, and unsupported platform constraints produce gaps. Configuration OS/architecture must agree with the selection. This verifies declared metadata, not that image binaries run on that platform.

Each selected descriptor's size and SHA-256 must match the bytes fetched from the configured repository. The optional `Docker-Content-Digest` response header must match those same bytes. HTTP manifest media type and a present JSON `mediaType` must agree; the selected descriptor's media type must also agree. Configuration blobs may arrive as their supported config media type, JSON, or octet-stream; their descriptor establishes how they are parsed. Content-type parameters are ignored. Layers and rootfs DiffIDs receive bounded structural/count checks, but their bytes are never read or verified. Alternate descriptor URLs and embedded content are never followed or executed.

## Authentication and network boundary

Credentials are references to environment variables, never literal configuration. Use either `token_env` for an existing Bearer token, both `basic_username_env` and `basic_password_env`, or neither for a registry that permits anonymous reads. Basic credentials are bounded printable ASCII; usernames cannot contain colons. No Docker credential files, helpers, login subprocesses, or ambient proxies are used. TLS verification and private-CA support are inherited from the shared HTTPS client.

For Basic credentials, one 401 challenge may trigger one token GET and one retry. This requires an independently configured exact token path and service. The challenge must contain exactly one Bearer realm, service, and scope. Realm must equal the configured origin plus token path, service must match, and scope must be exactly `repository:<selected repository>:pull`. Cross-origin/userinfo/query/fragment realms, additional permissions, alternate repositories, duplicate fields, unknown challenge extensions, redirects, and token refresh/discovery are rejected. Token-endpoint credentials stay on the same origin. The transient returned token stays in memory for this collection; it is never persisted or printed. The token protocol reference is [CNCF Distribution token authentication](https://distribution.github.io/distribution/spec/auth/token/).

[Registry1's image-pull documentation](https://docs-ironbank.dso.mil/tutorials/image-pull/) requires a username and issued CLI secret and states that anonymous image pulls are unavailable. Its [robot-account guidance](https://docs-ironbank.dso.mil/faq/#24-how-do-i-get-a-robot-account-to-automate-image-pulls-from-registry1) describes requesting an account through the help desk. This adapter neither creates access nor substitutes synthetic credentials. Those documents do not qualify this implementation's token path/service against a live Registry1 deployment; those values require approved configuration and a later live test. Registries that redirect configuration blobs to storage endpoints currently produce an explicit gap.

Reads are limited to six requests, 2 MB per body, and 8 MB total, with default 5-second request and 30-second collection transport limits. DNS, TLS, headers, and body reads share the existing bounded transport. JSON rejects duplicate keys, nonfinite values, excessive depth, and oversized node counts. All routes are immutable manifest or configuration reads plus the explicitly configured token endpoint. No layer, referrer, upload, delete, or execution routes exist.

## Evidence contract

The shared envelope has `provider: oci`, `execution_status: partial`, `decision: unknown`, and `confirmed_failure: false`. Missing trust remains required even when content equality succeeds:

- `requested-digest-origin-unverified`: the supplied immutable digest has no authenticated acquisition receipt.
- `publisher-signature-unverified`: no publisher signature trust has been established.
- `required-attestation-unverified`: attestation subjects and required trust have not been verified.

`observations.content_identity.decision` may be `pass`, `fail`, or `unknown` and describes only the bounded byte/metadata checks. Observations include separate `requested_digest`, `index_digest`, `platform_manifest_digest`, and `config_digest`; a direct manifest request has a null index digest. `objects` records each verified object's role, digest, media type, and byte count. `platform` records declared OS/architecture, separate index/config variants, and layer count. Collection timestamps and sanitized request receipts are retained.

Configuration Env, labels, author, history, annotations, command lines, raw JSON, layer identifiers, credentials, and remote error diagnostics are excluded from evidence. A successful content identity check must not be used as Iron Bank acceptance, signature verification, runtime image-ID equivalence, or business recovery evidence. A later verifier must bind these identities to authenticated acquisition, signer, attestation, and deployment receipts before clearing their corresponding gaps.

## Validation

`python -m pytest tests/test_oci.py` uses an ephemeral localhost TLS registry with the actual shared HTTP client. It tests OCI and Docker index/direct-manifest resolution, distinct digest levels, body/header/size/media mismatches, wrong architectures/variants, ambiguous and nested indexes, scoped Basic/Bearer authentication, hostile challenges, redirects, bounded raw responses, strict JSON, and privacy. These are protocol and boundary tests, not live Registry1 or vendor qualification.

The shared transport adds `get_bytes(path, params=None, *, accept=(...), allow_unauthorized=False)` and optional Basic environment references while retaining `get_json(path, params=None)`. Raw response bodies and authentication headers are deliberately absent from response reprs and receipts. The existing Argo, Kubernetes, Prometheus, and runtime suites must continue to pass after changes to this transport.
