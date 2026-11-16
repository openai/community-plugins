# Direct image acquisition

`devsecops.adapters.image_layout.acquire_image_layout` fetches one selected Linux
platform from an explicitly permitted registry repository and digest. It writes
the original manifest, config and layer bytes into a private OCI image layout.
This is the content acquisition component for a future fixed offline BuildKit
recipe. It does not authorize a build or qualify a Registry1 connection.

```python
from pathlib import Path
from devsecops.adapters.image_layout import acquire_image_layout

result = acquire_image_layout(
    "https://registry.example.test",
    repository="approved/base",
    allowed_repositories=["approved/base"],
    requested_digest="sha256:" + "<64 lowercase hex characters>",
    architecture="amd64",
    token_env="APPROVED_REGISTRY_READ_TOKEN",
    output=Path("/absolute/new/private-acquisition"),
)
```

The example hostname and digest are placeholders. Supply an operator-approved
origin, digest and environment-variable reference. The implementation neither
creates third-party credentials nor reads a Docker credential configuration.
An explicit CA bundle is optional; it is frozen and hashed before use. System
trust applies otherwise. Credentials are required even for a public registry.

## Transport and scope

- Only HTTPS GETs to the exact configured origin are available. Proxy environment
  variables, redirects, alternate blob URLs and credential helpers are unused.
- Requests select a configured repository's digest routes. An optional Basic to
  Bearer exchange requires the exact configured same-origin token path, service
  and `repository:<repository>:pull` scope. There is one exchange and one retry.
- `HTTPSReadClient` keeps its existing small-response limits. The separate
  `HTTPSBlobReadClient` streams blobs into a caller-owned, empty, private,
  unlinked-from-other-names, read/write regular file descriptor. It hashes the
  received bytes and reads the file back before returning a bounded receipt.
- The configured SHA256, descriptor byte size and optional digest response
  header must agree. No blob body is accumulated in memory. The file API returns
  no body bytes and leaves an incomplete private file on failure.
- Current ceilings are 512 MiB per compressed layer, 1 GiB cumulative received
  bytes including metadata, 1 GiB cumulative decoded bytes, 128 image layers,
  100,000 tar entries, and 600 seconds for the entire acquisition. Defaults are
  300 seconds overall and 30 seconds per request. A request may be configured up
  to 120 seconds. Callers may reduce byte and time limits, but cannot raise the
  ceilings. Descriptor totals are checked before the first layer request.

## Layout verification

The collector verifies index selection, platform manifest, config, every layer,
and the config's ordered rootfs `diff_ids`. OCI tar and gzip layers and Docker
schema-2 gzip layers are supported. Zstd, foreign layers, descriptor URLs and
inline data are deliberately unsupported in this version. No layer is extracted.

Tar paths, entry types, declared sizes, depth, PAX/GNU metadata and gzip inflation
are bounded. Gzip optional-header reads share the absolute deadline. Rootfs
diffIDs cover all decoded bytes, including tar padding. Each repeated layer is
checked against its position's diffID, while identical blobs occupy one file.

Before publishing, the collector reopens the output directory route and hashes
every private blob again. It then writes `layout/index.json` with one selected
platform and `layout/oci-layout`. The original requested multi-platform index is
retained as an unreferenced provenance blob. Missing platforms are never added
to the usable layout. Completion markers are removed if final checks fail.

`image-layout.json` contains the acquisition receipt, byte inventory, scope,
bounded transport receipts and `devsecops-image-layout-v1` identity. This identity
hashes a canonical inventory of all blob hashes/sizes, the one-platform index,
the OCI layout version marker and the selected manifest digest. It is not a
registry image digest. Paths and bytes may not be changed after acquisition;
future consumers must freeze and verify them again.

## Claims and integration

`observations.acquisition.decision == "pass"` means authenticated byte acquisition
and local layout verification completed. The overall decision remains `unknown`
and `build_authority` remains `not-issued`. Publisher authority, signatures,
required attestations, predicate semantics and runtime compatibility require
separate verification. A saved JSON receipt cannot grant those permissions.

The canonical build remains closed until a concrete issuer combines these bytes
with independently trusted current publisher material, the required signed
predicate policy and an actual runtime compatibility check. There is no alternate
mission base and no caller-supplied success object.

Tests use an ephemeral authenticated localhost TLS registry with real tar/gzip
content. They cover content substitution, diffID mismatch, redirects, scoped
auth, malformed/oversized layers, decompression and time budgets, private file
requirements, output replacement and final readback. A real pinned Syft test
consumes the resulting image archive and identifies its fixture package. That
proves scanner interoperability on the fixture, not Registry1 or live base
qualification.
