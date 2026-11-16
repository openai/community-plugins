# Offline image signature verification

This adapter verifies a local image-signature payload against an explicitly supplied public key using checksum-pinned Cosign **3.1.4**. It then compares the signed repository and digest with the exact requested image identity. These are narrow observations: successful cryptography does not establish that a key belongs to Iron Bank, that an image came from its official origin, or that required attestations exist. It never authorizes recovery, deployment, or publication.

## Supported inputs and API

`devsecops.adapters.signatures.verify_image_signature` accepts keyword arguments:

```python
from pathlib import Path
from devsecops.adapters.signatures import verify_image_signature

evidence = verify_image_signature(
    expected_repository="registry1.dso.mil/ironbank/example/image",
    expected_digest="sha256:" + "a" * 64,  # Replace with the reviewed immutable digest.
    public_key=Path("/trusted-inputs/publisher-public-key.pem"),
    payload=Path("/trusted-inputs/image-payload.json"),
    signature=Path("/trusted-inputs/signature.base64"),
    cosign=Path.home() / ".cache/devsecops/cosign-v3.1.4/bin/cosign",
    timeout=15,
)
```

Supply exactly one `signature` or `bundle`; all filesystem arguments must be absolute `Path` objects with no symlink components. `expected_repository` is a full registry authority plus repository path, with no URL scheme, userinfo, tag, or digest suffix. The digest is a lowercase SHA-256 identifier. The timeout is 1–60 seconds, defaults to 15, and supplies the remaining common deadline to both bounded child commands after input snapshotting. There is no shared CLI integration in this slice. The optional Python `now` argument supports deterministic caller tests; there is no command-line clock override.

Qualified formats are deliberately narrow:

| Component | Accepted shape |
| --- | --- |
| Public key | Local PEM SubjectPublicKeyInfo containing an uncompressed NIST P-256 EC public key |
| Payload | Raw JSON Cosign Simple Signing object with exact `critical.identity.docker-reference`, `critical.image.docker-manifest-digest`, and `critical.type = "cosign container image signature"`; optional metadata is never exported |
| Detached signature | Canonical base64 encoding of a bounded ECDSA signature over the exact raw payload bytes |
| Bundle | `application/vnd.dev.sigstore.bundle.v0.3+json` containing only `verificationMaterial.publicKey.hint` and `messageSignature` with a SHA2_256 digest and signature |

The bundle key hint is not trusted as key authority. The separately supplied public key performs verification. Unsupported bundle versions, certificates, certificate chains, transparency entries, DSSE envelopes, attestation payloads, key algorithms, duplicate JSON fields, nonfinite values, and excessive nesting remain **unknown**. No fallback interprets an unsupported shape as verified evidence. A richer bundle requires a separately qualified verifier, not manual removal of its trust material.

## Execution and receipts

The verifier lock, executable, public key, payload, and selected signature artifact are copied into a fresh private directory before execution. Descriptor-based path traversal rejects symlinks; input reads reject nonregular files, oversized inputs, and identity or content changes during copying. Copied input files are read-only, with constant private filenames. Limits are 160 MB for the executable, 1 MB for the payload/bundle, 16 KiB for the key/detached signature, and 64 KiB combined output per command.

Both the executable size/hash and reported version must match the shipped lock. Two shell-free commands run: `version --json`, followed by `verify-blob --key public-key.pem --offline --insecure-ignore-tlog` with the local signature or bundle and payload. These pinned flags select key-only verification and deliberately do not verify a transparency log. Cosign's `--offline` and detached-signature flags are deprecated in this release; their exact behavior is tested and must be requalified when the version changes.

The child receives an isolated empty home, cache, Docker configuration and executable search path. Ambient credentials, proxy configuration, cloud/KMS configuration, Sigstore root overrides and credential helpers are not forwarded. No registry lookup, key URL, certificate discovery, TUF update, layer download, or signature acquisition is part of this adapter. The pinned key-only path is offline; the adapter itself is not a portable operating-system network sandbox. Separate macOS qualification can execute the real verifier under OS network denial.

The receipt contains hashes and sizes of input artifacts, the executable and lock identities, command labels, exit codes, elapsed times and output hashes/sizes. It excludes source paths, public-key material, signature bytes, payload/optional metadata, process output and credentials. Private scratch is removed on completion. File-system I/O still depends on the host; process time/output bounds do not claim hard real-time guarantees for a stalled storage device.

## Evidence semantics

The common envelope has `schema_version: 1`, `provider: "signatures"`, `scope: {repository, digest}`, collection timestamps, `execution_status: "partial"`, `decision: "unknown"`, and `confirmed_failure: false`.

Within `observations`:

- `crypto.decision` records `pass`, `fail`, or `unknown` for the supplied key and bytes.
- `subject_binding.decision` compares the authenticated subject to the expected exact repository **and** digest. The observed subject is exported only after successful cryptography.
- `verification.decision` passes only when both checks pass. A wrong key/tampered signature or signed subject mismatch fails this narrow check. An unavailable tool or unsupported input remains unknown.
- `inputs`, `tool`, and `receipts` bind the narrow result to the processed bytes and pinned tool. `bundle_payload_digest_matches`, when present, is a content equality observation, not signature proof.

Every result retains required gaps for signer authority, key lifecycle, transparency-log verification, official image origin and required attestations. Cryptographic success does not clear any of these gaps. A signature mismatch is not an assertion that a running workload or service has failed. Unknown process errors are sanitized and do not become a cryptographic failure merely because a command returned nonzero.

## Provisioning and trust

```console
python scripts/bootstrap_cosign.py
python scripts/bootstrap_cosign.py --offline
```

Provisioning is a separate, explicitly network-using operation. It checks the pinned official release checksum list and selected binary hash, stores the binary only below `~/.cache/devsecops`, and writes a provisioning receipt. It supports Darwin/Linux on AMD64/ARM64. Only the host used for a recorded test has been execution-qualified; listing other release hashes is not cross-platform qualification. `--offline` validates an existing staged binary and never repairs it by downloading.

The bootstrap verifies checksums from a trusted shipped lock; it does **not** verify the release's own signature. The provisioning receipt explicitly reports `release_signature_verification: "not-performed"`. Lock/package distribution trust remains an operator responsibility. Neither the bootstrap nor verifier fetches or installs an Iron Bank public key. Operators must supply separately reviewed, current trust material and obtain artifacts through the separately scoped acquisition workflow.

The official [Iron Bank Cosign guide](https://p1docs.dso.mil/iron-bank/tutorials/cosign) documents public-key and certificate-chain verification, signature acquisition, and separate DSSE attestation checks. This implementation covers its key-backed Simple Signing primitive only. It does not qualify certificate lifecycle, revocation, signing time, trust-root rotation, attestation predicates, or Registry1 access. Generated lab keys prove local adapter behavior and cannot authenticate Iron Bank.

Pinned implementation references: [Cosign v3.1.4 release](https://github.com/sigstore/cosign/releases/tag/v3.1.4), [verify-blob source](https://github.com/sigstore/cosign/blob/v3.1.4/cmd/cosign/cli/verify/verify_blob.go), [key-only trusted-material handling](https://github.com/sigstore/cosign/blob/v3.1.4/cmd/cosign/cli/verify/common.go), and [Sigstore verification documentation](https://docs.sigstore.dev/cosign/verifying/verify/).

## Reproducible validation

```console
.venv/bin/python -m pytest tests/test_signatures.py tests/test_bootstrap_cosign.py -q
```

Tests generate disposable real P-256 keys and signatures with the pinned binary. Positive detached/bundle cases, tampered payload/signature, wrong key, exact repository/digest binding, unsupported shapes, unsafe file types/paths, frozen copies, environment isolation, sanitized failures, and provisioning boundaries are covered. When the pinned binary is absent, real-tool tests skip explicitly; skipped checks do not qualify a release.

On macOS, with normal host execution permission:

```console
DEVSECOPS_TEST_NETWORK_SANDBOX=1 .venv/bin/python -m pytest tests/test_signatures.py tests/test_bootstrap_cosign.py -q
```

The extra cases verify both real signature modes inside `sandbox-exec` with `network*` denied, plus a canary proving a reachable loopback socket is denied. They are opt-in because an already sandboxed Codex process may be unable to establish a nested OS sandbox. The default suite explicitly skips those three host-only cases. No live Iron Bank signature, hosted factory, or human acceptance is established by these tests.
