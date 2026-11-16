# Immutable mission evidence publication

`publish_signed_mission` publishes the exact locally signed mission evidence to
an explicitly selected staging repository and independently reads it back. It
consumes a current `LocallySignedMission` from the same process. A saved JSON
receipt cannot substitute for that capability.

The publication configuration has its own operator-selected SHA-256 pin,
credential references, public-key pin and signing-trust policy pin. Its exact
HTTPS origin/repository must equal the signed image destination. Registry1 is
rejected. The independently selected key and current active operator trust policy
must match the actual signer and signed evidence. An owned lab application key
does not become Platform One publisher authority.

## Registry contract

The profile requires the native OCI referrers API. A valid bounded index response
is required **before any evidence write**. An unsupported endpoint, redirect, invalid
trust selection, or changed subject stops publication. The implementation does not create
fallback tags. This is a deliberately narrower profile than a general OCI client.

One OCI image manifest has:

- `artifactType: application/vnd.devsecops.mission-evidence.v1`;
- an `application/vnd.oci.empty.v1+json` config containing exactly `{}`;
- a `subject` descriptor for the actual signed image manifest;
- seven fixed named layers: `payload.json`, `bundle.json`, `public-key.pem`,
  `signing-trust.json`, `sbom.spdx.json`, `grype.json`, and `scanner-receipt.json`.

The manifest and every blob are written only at their SHA-256 digests. No tag,
promotion, existing-content deletion, private key, password, database archive or
image re-upload is part of this operation. The format follows the
[OCI 1.1.1 image manifest](https://github.com/opencontainers/image-spec/blob/v1.1.1/manifest.md)
and [OCI 1.1.1 distribution subject/referrers interfaces](https://github.com/opencontainers/distribution-spec/blob/v1.1.1/spec.md).
It is a custom evidence artifact carrying a Cosign key-backed bundle, not a claim
that generic `cosign verify IMAGE` automatically discovers this custom format.

Before each registry mutation, the operation fsyncs an immutable intent containing
the selected destination, digest, run and configuration. Upload locations and
authentication realms must remain in the configured origin and repository;
credentials are not forwarded to other destinations. Redirects and ambient proxy
settings are not used. Mutations are attempted once. If a connection breaks, the
result stays `unknown`, retaining the artifact digest, acknowledgements and
intents for inspection. No automatic retry or cleanup of registry content occurs.

A new pull-only session downloads the exact manifest and every evidence blob,
checks sizes and SHA-256 identities, and runs the pinned Cosign verifier over the
downloaded public key, payload and bundle. The verified subject, signer trust,
source, run, base, build, scan policy, database, scanner tools and evidence hashes
must match the direct signing capability. A final subject read and positive native
referrers inclusion prove the artifact is retrievable and discoverable by the
selected image digest.

Referrers discovery checks one response of at most 1,024 descriptors and 2 MB.
It proves exact inclusion, not exhaustive enumeration. Pagination links are not
followed. If the expected descriptor is absent from that response, even if another
page might contain it, discovery remains `unknown`; the uploaded digest remains
available in the receipt. Unsupported or conflicting descriptors never produce a
pass. The evidence profile permits at most 16 MB per scanner artifact and 64 MB
of evidence overall, with a single 2–600 second deadline (default 300).

## Configuration and execution

Copy `demo/software-factory/ci/mission-publication.example.json` and replace all
example paths, origins, repository names, credential references and pins. The
runner supplies the approved external dependency interpreter through
`DEVSECOPS_PYTHON`; use `-B` to avoid writing bytecode into an immutable plugin
installation. Use the same explicit signing-trust document and public key in both
the signing and independent publication configurations. Keep private signing
credentials outside evidence/output directories.

```sh
"$DEVSECOPS_PYTHON" -B "$PLUGIN_ROOT/scripts/publish_mission.py" \
  --build-config "$BUILD_CONFIG" --build-config-sha256 "$BUILD_CONFIG_SHA256" \
  --staging-config "$STAGING_CONFIG" --staging-config-sha256 "$STAGING_CONFIG_SHA256" \
  --security-config "$SECURITY_CONFIG" --security-config-sha256 "$SECURITY_CONFIG_SHA256" \
  --signing-config "$SIGNING_CONFIG" --signing-config-sha256 "$SIGNING_CONFIG_SHA256" \
  --publication-config "$PUBLICATION_CONFIG" --publication-config-sha256 "$PUBLICATION_CONFIG_SHA256" \
  --expected-source-repository "$CI_PROJECT_DIR" --expected-source-revision "$CI_COMMIT_SHA" \
  --runtime-root "$RUNNER_PRIVATE_RUNTIME" --output "$RUN_OUTPUT"
```

This command performs qualification, build, image staging, security assessment,
local signing and evidence publication in one process. It writes to the explicit
staging repository. Configuration and signer trust are checked before the upstream
chain; native referrers support is checked once that chain supplies the exact
staged image, before evidence publication. It does not accept previous build/sign/scan reports as input,
execute a hosted CI job or grant promotion. Exit status is 0 for this narrow
operation's pass, 1 for an upstream confirmed failure, and 2 for unknown.

`mission-publication.json` retains the immutable artifact descriptor, fixed
subject, per-write intents, independent network receipts and verified readback
artifacts. `PublishedMission` is non-serializable and expires no later than the
source signing capability or selected trust policy. Terminal output is audited
again after fsync; expiry, replacement or changed evidence withdraws the terminal
receipt. Reports remain evidence, never a durable authority constructor.

## Validation and remaining qualification

Controlled tests use actual pinned Cosign signatures and a local TLS registry for
upload, bearer/basic authentication, referrers discovery, immutable readback and
cryptographic verification. They cover unsupported API support, redirects,
changed content, interrupted writes, ambiguous/paginated discovery, changed trust,
expired capabilities and terminal output replacement. Controlled evidence cannot
issue a production `PublishedMission`.

A real factory pass still requires the canonical authenticated publisher base,
approved Linux execution, a genuine current vulnerability database and the
required named registry/hosted CI acceptance run. This component does not claim
those external qualifications or close their acceptance gates.
