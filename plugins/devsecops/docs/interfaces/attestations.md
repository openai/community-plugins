# Individual DSSE attestation verification

`devsecops.adapters.attestations.verify_attestations` checks the exact signed
statement in every supplied required envelope. It uses the existing pinned Cosign
3.1.4 and a separately supplied P-256 public key. Its result distinguishes valid
cryptography, exact image-subject binding, required predicate-type binding and
missing evidence. A valid image signature cannot replace these checks.

```python
from pathlib import Path
from devsecops.adapters.attestations import verify_attestations

result = verify_attestations(
    expected_repository="registry1.dso.mil/ironbank/example/image",
    expected_digest="sha256:" + reviewed_manifest_sha256,
    required_predicates=reviewed_required_predicate_uris,
    public_key=Path("/trusted-inputs/publisher.pem"),
    envelopes={uri: Path(filename) for uri, filename in downloaded_envelopes.items()},
    cosign=Path.home() / ".cache/devsecops/cosign-v3.1.4/bin/cosign",
)
```

The required URI list comes from the independently selected policy/lock. Each
URI maps to one absolute local file; an omitted URI or `None` produces a missing
evidence gap. Unknown additional URI keys and duplicate requirements are rejected.
No embedded URL is fetched and no envelope-supplied key hint selects trust.

The supported envelope contains `payloadType = application/vnd.in-toto+json`,
one signature, and an in-toto v0.1 or v1 statement with one SHA-256 subject.
Both standard and URL-safe canonical base64 are accepted. Exact subject name
and digest must match the requested registry repository and immutable image.
The predicate must be a nonempty object and its type must equal the required
URI. Multiple signatures/subjects, certificate-bearing wrappers and unsupported
shapes remain unknown; they are never stripped into an acceptable shape.

The verifier constructs DSSE v1 pre-authentication encoding from the frozen
payload's exact bytes and payload type. It then verifies each signature using
Cosign's offline key-only `verify-blob` path. It never reserializes the signed
payload for verification. An independent test also verifies the generated DSSE
envelope using Cosign's native `verify-blob-attestation` command, including its
digest and predicate checks.

## Bounds and evidence

There are at most 16 required envelopes, each at most 4 MB, with a decoded payload
limit of 2.5 MB. Duplicate JSON fields, nonfinite numbers, excessive nesting and
nonregular/symlink inputs are rejected. The tool, lock and key are copied before
execution; each envelope is frozen before its verification. A common 1–120 second
deadline (default 60) includes snapshots and every child command. The child has
private HOME/cache/Docker directories, an empty helper PATH, scrubbed environment
and 64 KiB output bound. No registry, TUF, KMS or credential-helper lookup occurs.
Filesystem I/O is bounded by size and checked deadlines, not a hard realtime
guarantee against a stalled storage device.
The deadline is rechecked after each child exits and before accepting the signed
statement. Budget or execution failure stops subsequent envelope reads. Every
required predicate retains its own result row, including unattempted work, so an
interrupted operation cannot hide the remaining requirements.

Receipts retain input, statement and normalized predicate hashes, tool identity,
per-envelope decisions and bounded command output hashes. Payload bodies,
credentials, paths, signatures and raw diagnostics are excluded. A tampered
signature or a valid signature for another subject/type fails that narrow check.
Missing files, unsupported forms and execution failures remain unknown. One
valid envelope cannot hide a missing or invalid second requirement.

The aggregate remains unknown: this primitive does not authenticate publisher
authority, official acquisition, key lifecycle, transparency requirements or
the truth/policy acceptability of predicate contents. It cannot issue a verified
base capability, build, promote or establish recovery. F0 must combine these
checks with the concrete trusted acquisition/runtime verifier. A synthetic
generated key cannot become Iron Bank authority.

## Validation and references

Run `.venv/bin/python -m pytest tests/test_attestations.py -q`. The tests use real
Cosign and generated disposable P-256 keys. Set
`DEVSECOPS_TEST_NETWORK_SANDBOX=1` on an authorized macOS host to additionally run
the verifier with operating-system network access denied. Default skips and
synthetic fixtures are explicit; they do not qualify actual Registry1 contents.

The [DSSE protocol](https://github.com/secure-systems-lab/dsse/blob/master/protocol.md)
defines payload-type binding and pre-authentication encoding. The
[Iron Bank guide](https://p1docs.dso.mil/iron-bank/tutorials/cosign) describes
separately signed envelopes, SBOM predicates and the VAT/hardening metadata
predicates. Actual required predicate choices and repository content must still
be resolved and qualified against the current policy and authenticated image.
