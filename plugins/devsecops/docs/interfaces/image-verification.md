# Registry acquisition to signature verification

`scripts/verify_registry_image.py` connects the real scoped registry reader to
the pinned image-signature and individual DSSE verifiers. It acquires bytes in
the same invocation, freezes and rechecks them, verifies their exact repository
and digest against the supplied key, and exports private evidence with an
integrity manifest. There is no mode that imports a JSON acquisition verdict.

```sh
python3 "$DEVSECOPS_PLUGIN_ROOT/scripts/verify_registry_image.py" \
  --origin https://registry1.dso.mil \
  --repository ironbank/opensource/nginx/nginx \
  --digest "$REVIEWED_IMMUTABLE_DIGEST" --architecture amd64 \
  --public-key "$REVIEWED_PUBLISHER_KEY" --key-sha256 "$REVIEWED_KEY_SHA256" \
  --required-predicate https://vat.dso.mil/api/p1/predicate/beta1 \
  --required-predicate 'https://repo1.dso.mil/dsop/dccscr/-/raw/master/hardening%20manifest/README.md' \
  --cosign "$PINNED_COSIGN" \
  --basic-username-env DEVSECOPS_REGISTRY1_READ_USER \
  --basic-password-env DEVSECOPS_REGISTRY1_READ_PASSWORD \
  --token-path "$EXACT_REVIEWED_TOKEN_PATH" --token-service "$EXACT_REVIEWED_TOKEN_SERVICE" \
  --output "$NEW_PRIVATE_EVIDENCE_DIRECTORY"
```

Use the actual required predicates from the selected policy, including any
required SBOM type. The example does not select a release, obtain credentials,
approve a key or prove Registry1's current token/storage behavior. Token path and
service must match the independently configured same-origin challenge exactly;
the client does not discover another credential destination. A bearer token can
instead be referenced with `--token-env`. Values stay in environment variables,
not arguments or reports. Explicit CA files are optional; TLS verification is
always required.

The Python entry point is
`devsecops.adapters.image_verification.verify_registry_image`. It adds an explicit
`allowed_repositories` list to the script's one-repository scope. It requires the
key's raw-byte SHA-256 separately from its path. A changed key or existing output
is rejected before authenticated requests. This detects changed material; the
supplied hash is not evidence that a publisher owns that key.

## What the result means

The report separates three narrow checks: actual acquisition/content identity,
image-signature subject verification, and every required attestation's signature,
subject and type. `verification: pass` requires all three. Missing or ambiguous
candidates remain unknown. A cryptographically invalid signature or authenticated
subject mismatch produces a narrow failure. Acquisition labels never select an
unrelated path: every selected input must be in the frozen content-addressed map.

`decision` remains **unknown**, the script exits **2**, and `build_authority` is
**not-issued**. Publisher-key authority, key lifecycle, transparency requirements,
predicate semantics, actual image layers, legacy-storage qualification, runtime
compatibility and promotion policy remain explicit gaps. These results cannot
authorize a mission build or close the canonical F0 gate. The complete required
gaps from each component are preserved, except the exact cryptographic checks
that this invocation independently performs. Read the component receipts for
their detailed coverage and limits.

The output contains `image-verification.json`, `image-signature.json` and
`required-attestations.json` when attempted, `registry-acquisition.json`, and the
content-addressed raw acquired artifacts. `image-manifest.json` binds every
retained file. All files are private. Normalized reports and CLI output exclude
payload bodies, tokens, signatures and raw process/remote diagnostics. Acquired
raw artifacts may contain image metadata and attestation bodies; keep that
directory under the appropriate evidence access controls.

One 5–300 second deadline (default 180) bounds acquisition, snapshots and verifier
commands. The acquisition sub-budget is at most 120 seconds; each child receives
the remaining budget. Output is exclusive and descriptor-anchored. A late or
redirected publication cannot leave a terminal manifest. This does not promise
hard realtime filesystem behavior on a stalled device.

## Executed validation

`tests/test_image_verification.py` serves actual content through a local TLS
registry requiring authentication, generates real Cosign signatures and DSSE
envelopes, then invokes the same composition and script. It checks exact exported
hashes/private modes, wrong keys, missing predicates, artifact substitution,
origin normalization, deadline withdrawal and sanitized authentication denial.
This establishes the local transport-to-cryptography implementation. It does
not establish access to Registry1, official publisher trust or runtime acceptance.

Related contracts: [registry acquisition](registry-evidence.md),
[image signatures](signatures.md), [individual attestations](attestations.md),
and [build-chain gates](build-chain.md).
