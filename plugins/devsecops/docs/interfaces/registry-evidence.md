# Direct registry verification-artifact acquisition

`devsecops.adapters.registry_evidence.collect_registry_evidence` directly reads
one approved repository and immutable image digest over HTTPS. It preserves the
image index/manifest/config and the raw verification artifacts needed by the
signature and attestation verifiers. It does not accept a saved report as proof
that acquisition occurred, execute registry tools, write to a registry, fetch
metadata URLs or install credentials.

```python
from pathlib import Path
from devsecops.adapters.registry_evidence import collect_registry_evidence

result = collect_registry_evidence(
    "https://registry1.dso.mil",
    repository="ironbank/example/image",
    allowed_repositories=["ironbank/example/image"],
    requested_digest="sha256:" + approved_image_sha256,
    architecture="amd64", variant=None,
    required_predicates=approved_required_predicate_uris,
    basic_username_env="REGISTRY_READER_USER",
    basic_password_env="REGISTRY_READER_PASSWORD",
    token_path="/service/token", token_service="harbor-registry",
    output=Path("/absolute/new/private-acquisition"),
)
```

The example's image, digest, predicate list and token service are placeholders
that must come from separately approved configuration. The token endpoint and
audience must be verified for the actual registry. This code has **not** been
qualified against authenticated Registry1; local generated credentials do not
grant Platform One access.

The complete keyword interface is `repository`, `requested_digest`,
`architecture`, `allowed_repositories`, `required_predicates`, `output`, optional
`variant`, `token_env`, `basic_username_env`, `basic_password_env`, `token_path`,
`token_service`, `ca_bundle`, `timeout`, `total_timeout`, `max_requests`,
`max_total_bytes`, and the Python-only test clock `now`. Unknown arguments fail.
There is no client injection, imported-report authority or executable hook.

## Authenticated, scoped requests

A nonempty explicit bearer or basic read credential is required. Supplying both
modes, missing environment values, anonymous fallback or an unapproved repository
fails closed. Credential values never enter results. A basic credential can be
exchanged exactly once, and only when the challenge exactly matches the configured
same-origin token path, audience, and `repository:<exact-repository>:pull` scope.
A second challenge cannot broaden permission or mint refresh credentials.
Redirects are rejected before credentials can be forwarded elsewhere.

One shared transport session covers the image and all verification artifacts.
Default limits are 64 requests, 64 MB total response bytes, 4 MB per response,
5 seconds per request and 120 seconds total. All are bounded by the underlying
transport caps. Image metadata is limited to 2 MB, signature payloads to 1 MB,
and DSSE envelopes to 4 MB with a 2.5 MB decoded-payload limit. TLS, headers and
bodies use absolute deadlines; a peer cannot extend the deadline by trickling
data. CA files are snapshotted into private scratch before TLS initialization.
Supplied CA paths must be absolute bounded regular files without symlinks.

Network and storage operations share the total deadline. As with the other
local adapters, bounded regular-file I/O is not a hard realtime guarantee for a
stalled storage device. A final receipt may record a timed-out partial operation;
that does not turn it into complete acquisition.

## Candidate legacy Cosign storage

The image chain reuses the existing OCI validator: requested SHA-256 bytes,
platform selection, manifest/config descriptor sizes and types, Linux
architecture/variant, and config rootfs/layer-count consistency are checked.
Image layer bodies are not downloaded by this operation; it cannot claim that
image execution or full image content has been qualified.

For the **requested digest**, the collector reads these two legacy tags:

- `sha256-<hex-digest>.sig`
- `sha256-<hex-digest>.att`

Each tag is resolved once, then fetched again by its actual immutable manifest
digest. All subsequent config/layer reads use descriptor digests from that
frozen manifest. Descriptor size, body hash, content type and any advertised
`Docker-Content-Digest` must agree. Descriptor `urls`, certificate URLs, predicate
URLs and annotations are inert data and are never followed. OCI referrer
discovery and alternate repositories are not silently substituted.

The signature manifest supports 1–16 Simple Signing layers with detached
base64 signatures in `dev.cosignproject.cosign/signature`. The attestation
manifest supports 1–32 `application/vnd.dsse.envelope.v1+json` layers. Every
layer is fetched, bounded and retained; one successful layer cannot hide a
missing or malformed later layer. Raw manifests also retain unrecognized
annotations privately for later explicit assessment.

The convention is a **candidate** requiring live Registry1 qualification.
Acquisition never assumes that a `.sig` tag proves publisher authority, that a
DSSE type label is authenticated, or that a validly encoded signature is valid
cryptography. Use the separately supplied trusted key and the exact artifacts
with [image signature verification](signatures.md) and
[individual attestation verification](attestations.md).

## Output and composition

The returned normalized JSON is also saved in the new private output directory.
Its `observations.image` contains the existing OCI identity fields, and each
image object identifies its raw `artifact` filename.

`observations.signatures` contains **every** discovered candidate:

```text
{payload_artifact, signature_artifact, source_manifest_digest,
 layer_index, parsed_state}
```

`signature_artifact` is null when the detached signature could not be parsed.
`observations.attestations` contains every fetched envelope:

```text
{envelope_artifact, predicate_type, source_manifest_digest,
 layer_index, parsed_state}
```

`predicate_type` is a parsed, unverified selection hint, or null for an
unsupported envelope. `recognized-unverified` is a parsing outcome, never a
signature or publisher verdict. The caller must verify the exact signed bytes.
Every required predicate has a candidate count and selection state. Zero or
multiple matches remain explicit missing/ambiguous gaps. Multiple signatures
also remain ambiguous; the collector never picks the first candidate.

All artifact names are content addressed (`sha256-<hex>.blob`). The `artifacts`
map records each filename's SHA-256, byte size, media types and roles. Repeated
identical bodies share a file, while repeated descriptor occurrences remain
separate candidate entries. This prevents deduplication from hiding ambiguity.

`registry-manifest.json` is written last. It binds the exact content-addressed
receipt filename and every artifact. The receipt has its own integrity seal.
These unkeyed hashes show content integrity, **not authenticity or permission**.
The future F0 issuer must invoke the collector directly in its trusted execution
path and retain that execution provenance; reading this JSON alone is not an
authenticated acquisition.

The output directory is 0700 and files are 0600. Raw image metadata, signature
payloads and predicates can contain sensitive values, so they remain private and
are excluded from normalized results and diagnostics. Directory-descriptor
writes prevent an output-path replacement from redirecting raw data; replacement
also prevents the terminal manifest. Existing output directories are rejected.

`observations.acquisition.decision` is `pass` only when the content-checked
collection and required candidate coverage completed, `fail` for a measured
content/size/platform mismatch, and otherwise `unknown`. The aggregate
`decision` remains `unknown` with explicit storage-qualification, cryptography,
publisher-authority, predicate-semantics and image-layer gaps. An optional `now`
test clock marks the receipt `controlled-test`. Neither narrow acquisition pass
nor an imported receipt can produce a verified base or authorize build/push.

## Validation

Run `.venv/bin/python -m pytest tests/test_registry_evidence.py tests/test_oci.py -q`.
Tests run real TLS and credential checks against an isolated localhost registry,
covering image/config identities, same-origin read-only token exchange, exact
artifact preservation, missing/duplicate candidates, invalid/oversized/late
responses, redirects, descriptor mismatch, shared budgets and output-path swaps.
Their signature bytes are explicitly synthetic parsing fixtures, not Iron Bank
publisher verification. Actual registry access and issuer integration remain
separate acceptance gates.

The storage shape follows the
[Cosign signature specification](https://github.com/sigstore/cosign/blob/main/specs/SIGNATURE_SPEC.md)
and its [legacy attestation description](https://github.com/sigstore/policy-controller/issues/1406).
These references describe a format; they do not prove the current Registry1
deployment uses it for the selected image.
