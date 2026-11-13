# Explicit image policy assessment

`scripts/assess_registry_image.py` acquires registry evidence and invokes the real
pinned Cosign verifier before evaluating an explicitly selected policy. It has no
imported-report mode. The policy path and its expected SHA-256 must come from the
operator's trusted configuration, separately from candidate source and registry
metadata. Copying an arbitrary policy and calculating its hash does not establish
organizational approval.

The policy selects one exact HTTPS registry origin, repository, immutable digest,
Linux architecture, signer key hash and validity window. Every required predicate
has explicit equality rules. There are no shell expressions, callbacks, regular
expression evaluators, implicit exceptions or defaults that approve missing
evidence. Policy validity is checked before authentication and after publication.

```json
{
  "schema_version": 1,
  "kind": "registry-image-policy",
  "subject": {
    "origin": "https://registry.example.test",
    "repository": "team/base",
    "digest": "sha256:<actual immutable digest>",
    "architecture": "amd64"
  },
  "signer": {
    "key_sha256": "<reviewed public key file hash>",
    "valid_from": "<UTC timestamp YYYY-MM-DDTHH:MM:SSZ>",
    "valid_until": "<UTC timestamp YYYY-MM-DDTHH:MM:SSZ>"
  },
  "predicates": [{
    "type": "https://example.test/predicate/v1",
    "rules": [{"path": "/review/status", "equals": "accepted"}]
  }]
}
```

This example intentionally contains invalid placeholders. It is not a qualified
Iron Bank policy, and the example predicate is not an Iron Bank schema. Select
actual required fields from the service's observed, supported predicate format
and your approved policy. The public Iron Bank guide identifies distinct VAT,
hardening and SBOM attestations; each has its own signature and must be verified
individually. [Iron Bank signing documentation](https://p1docs.dso.mil/iron-bank/tutorials/cosign)

Rules use JSON Pointer paths relative to the predicate, with `~0` and `~1`
escaping and canonical nonnegative array indexes. Missing paths fail. Comparisons
preserve JSON types: `true`, `1` and `1.0` differ. Object key order is immaterial;
array order matters. Limits are 16 predicate types, 32 rules per type, 16 pointer
components, 4 KB per expected value and 64 KB total policy. The operator policy
window is positive and at most 90 days. This window is not a certificate validity
or revocation check.

The content-addressed image config must match the selected Linux architecture and
have no inherited `OnBuild` triggers. Any nonempty or malformed `OnBuild` value
fails. Other runtime requirements—including Python, venv/ensurepip, UID/GID and
writable paths—still require the actual isolated runtime probe. Image layers are
not acquired by this command.

Noncanonical case aliases for the root `config`, `os`, `architecture` and nested
`OnBuild` fields are rejected. This prevents disagreement with BuildKit's Go JSON
decoder, which also recognizes field names without regard to case.
[Go JSON decoding rules](https://pkg.go.dev/encoding/json#Unmarshal),
[BuildKit base-config handling](https://github.com/moby/buildkit/blob/v0.34.1/frontend/dockerfile/dockerfile2llb/convert.go).

```sh
python3 "$DEVSECOPS_PLUGIN_ROOT/scripts/assess_registry_image.py" \
  --policy "$APPROVED_IMAGE_POLICY" --policy-sha256 "$APPROVED_POLICY_SHA256" \
  --public-key "$APPROVED_PUBLIC_KEY" --cosign "$PINNED_COSIGN" \
  --token-env REGISTRY_READ_TOKEN --output "$NEW_ABSOLUTE_OUTPUT"
```

Basic credentials are supported through `--basic-username-env` and
`--basic-password-env`, with the same explicit scoped token endpoint/service as
the registry verifier. Values never belong in command arguments. The common
deadline is 10–300 seconds, default 240. Retained private artifacts include the
complete underlying verification evidence and `policy-assessment.json`.
Normalized rule observations contain only rule indexes, hashes and decisions;
they do not copy expected or observed predicate values.

`policy_satisfaction: pass` means acquired, cryptographically verified bytes met
the selected rules while that policy was current. It does not prove that signed
claims are true, that the key belongs to the official publisher, that the key is
unrevoked, or that the image runs correctly. The aggregate remains `unknown`,
`build_authority` remains `not-issued`, and this component exits 2. All underlying
required gaps are retained. Only the future concrete base qualifier can combine
these observations with official trust, layer acquisition and runtime execution.
