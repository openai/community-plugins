# Publisher public-key trust

`scripts/assess_publisher_trust.py` assesses an explicitly selected publisher key
policy. It can return **pass** when that policy is satisfied. It never issues
image or build authority and never reads a key, endpoint or approval from image
metadata.

The implementation follows the public-key option in the official
[Iron Bank signing guide](https://p1docs.dso.mil/iron-bank/tutorials/cosign).
That guide separately describes certificate verification. A certificate chain,
OCSP response or CRL is not a universal prerequisite for the selected key-pinning
mode. Customers requiring certificate validation must use a separately supported
certificate policy; unrecognized certificate options are rejected, not downgraded.

## Trust policy

Choose the policy hash through the operator's trusted configuration or package
distribution process. A hash printed by an untrusted image or its repository
does not select authority. All fields below are mandatory:

```json
{
  "schema_version": 1,
  "kind": "publisher-public-key-policy",
  "publisher": "platform-one-iron-bank",
  "scope": {
    "registry_origin": "https://registry1.dso.mil",
    "repository_prefix": "ironbank/"
  },
  "key": {
    "pem_sha256": "<SHA256 of the exact selected public-key file>",
    "spki_sha256": "<SHA256 of its DER SubjectPublicKeyInfo>"
  },
  "validity": {
    "not_before": "<YYYY-MM-DDTHH:MM:SSZ>",
    "not_after": "<YYYY-MM-DDTHH:MM:SSZ>"
  },
  "source": { "kind": "p1-current-document" },
  "revoked_spki_sha256": []
}
```

The PEM hash binds exact supplied bytes; the SPKI hash binds key identity
independently of PEM wrapping. Only valid P-256 public keys are currently
supported, matching the existing signature and attestation verifier.

The validity window is positive and at most 90 days. Select a shorter window
when appropriate to the operator's rotation/review practice. Every denylist
entry is an SPKI SHA256; a matching key fails. This is an operator policy
denylist, not a claim that an online certificate revocation service was checked.
The policy and package never use a caller-overridden clock.

## Online P1 publication

```sh
python3 scripts/assess_publisher_trust.py \
  --policy "$PUBLISHER_POLICY" --policy-sha256 "$SELECTED_POLICY_SHA256" \
  --public-key "$SELECTED_PUBLISHER_KEY" \
  --registry-origin https://registry1.dso.mil \
  --repository ironbank/redhat/ubi/ubi9 \
  --output "$NEW_TRUST_OUTPUT"
```

The online mode is restricted to the declared Iron Bank scope and the fixed
official signing-guide URL. It performs one direct HTTPS GET with system trust,
no credentials, no proxy configuration, no redirects and no links from the
response. The body is limited to 1 MB and retained with its SHA256. Parsing
selects exactly one key from the current signing-key section; the historical
section is never a fallback. Missing, ambiguous or changed structure is unknown.

The served current key must match the independently selected SPKI pin. A
publisher rotation therefore requires an explicit policy update. This proves
what the official HTTPS publication currently serves, not a certificate's
historical signing time or the absence of every possible revocation event.
The separately selected policy's validity and denylist remain in force.

## Operator-trusted offline package

Disconnected operators can select a package through their existing trusted
distribution process. Replace the policy's `source` with:

```json
{
  "kind": "operator-trusted-package",
  "package_sha256": "<exact package file SHA256 selected by the operator>",
  "max_age_seconds": 86400
}
```

The package is bounded JSON with these exact fields:

```json
{
  "schema_version": 1,
  "kind": "operator-trusted-publisher-key-package",
  "publisher": "platform-one-iron-bank",
  "scope": {
    "registry_origin": "https://registry1.dso.mil",
    "repository_prefix": "ironbank/"
  },
  "source_url": "https://p1docs.dso.mil/iron-bank/tutorials/cosign",
  "reviewed_at": "<YYYY-MM-DDTHH:MM:SSZ>",
  "public_key_pem": "<complete public-key PEM including newlines>"
}
```

Add `--package "$TRUSTED_KEY_PACKAGE"` to the command. The package hash,
publisher, scope and key must match the selected policy. Its review time must
not be in the future or older than the configured maximum, which is bounded to
90 days. Validity and freshness are rechecked at completion. The source URL is
retained provenance, never fetched or treated as evidence of an online read.

Receipts explicitly identify this as **operator-selected package authority**
and operator-asserted provenance. This mode supports other publishers and exact
registry namespace scopes. An arbitrary generated key or package is not official
Iron Bank authority unless the operator's separately trusted policy has actually
selected it; test package policies do not establish production acceptance.

## Evidence and integration

The output is a new private directory containing the selected policy and key,
the actual official HTTPS body or the frozen offline package, the extracted
source key and `publisher-trust.json`. Every artifact has a retained hash and
is read back before a pass is published. Policy expiry, elapsed deadline,
changed output identity or changed artifacts withdraw the terminal receipt.
No private key or registry credential is needed. Total time defaults to 30
seconds and is capped at 120 seconds; errors expose categories only.

Exit 0 means the **publisher key policy** passed. Exit 2 means fail or incomplete.
`build_authority` remains `not-issued` in either case. The later F0 issuer must
invoke this check directly and bind the same key to actual image signature and
required attestation verification, image layer acquisition and runtime checks.
Replaying this receipt must not create authority.

Controlled TLS endpoints in tests always produce `execution_mode=controlled-test`
and unknown publisher authority, even when all content checks pass. Tests also
cover real offline package validation, key rotation/mismatch, the current versus
legacy section, duplicate/malformed source material, scopes, denylist entries,
freshness/expiry, credential-free requests and final publication withdrawal.
