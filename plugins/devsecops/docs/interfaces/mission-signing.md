# Local mission image signing

`scripts/sign_mission.py` extends the direct build, staging and assessment chain
with actual local signing by pinned Cosign 3.1.4. It accepts only a current
same-process `AssessedMission`. The signer reconstructs a fixed Simple Signing
payload from that verified result, signs it, and independently verifies the
actual generated Sigstore v0.3 bundle with the existing signature adapter.

**Signatures remain local.** A narrow pass establishes local signing and
verification under the selected application key policy. It does not publish a
registry signature, establish CI provenance, or promote the image.
`factory_decision` remains `unknown`; publication, registry readback and
promotion remain `not-started`.

## Run the same-process chain

Use the approved external dependency interpreter supplied by the runner and the
installed plugin root. `-B` avoids writing bytecode into the immutable install.

```sh
"$DEVSECOPS_PYTHON" -B "$DEVSECOPS_PLUGIN_ROOT/scripts/sign_mission.py" \
  --build-config "$BUILD_CONFIG" --build-config-sha256 "$BUILD_CONFIG_SHA256" \
  --staging-config "$STAGING_CONFIG" --staging-config-sha256 "$STAGING_CONFIG_SHA256" \
  --security-config "$SECURITY_CONFIG" --security-config-sha256 "$SECURITY_CONFIG_SHA256" \
  --signing-config "$SIGNING_CONFIG" --signing-config-sha256 "$SIGNING_CONFIG_SHA256" \
  --expected-source-repository "$SOURCE_REPOSITORY" \
  --expected-source-revision "$SOURCE_REVISION" \
  --runtime-root "$OWNED_LINUX_RUNTIME_ROOT" \
  --output "$NEW_RUN_DIRECTORY" --timeout 3300
```

The preceding stages **write image content to the selected staging repository**.
The signing stage writes only local artifacts. Keep the protected operator
configuration, exact image destination and dedicated credential controls from
[staging](mission-staging.md) and [security assessment](mission-security.md).
All four outer configuration pins, signing trust/public key and repository
scope are checked before delivery begins. The security operation separately
checks its exact policy/listing/database before its build/upload. Key credentials
are read only when a direct current assessment reaches signing.

The [signing configuration](../../demo/software-factory/ci/mission-signing.example.json)
selects absolute local paths for the encrypted private key, public key, trust
policy and pinned Cosign, plus the **name** of a password environment variable.
The runner injects that value from its approved secret store; never place the
password in configuration, arguments, reports or source control. Missing
credentials do not trigger an interactive prompt or a fallback identity.

Exit codes are `0` for local signing success, `1` for an established upstream
policy failure, and `2` for incomplete evidence. A later failure does not undo
earlier staging writes. Retain the exact mutation intents and inspect their
digests before another attempt. Total execution is bounded to 3600 seconds;
local signing is bounded to 300 seconds, including its file checks and verifier.

## Independent application signer trust

The [trust policy](../../demo/software-factory/ci/mission-signing-trust.example.json)
is selected and hash-pinned independently of the candidate and private key. It
contains an explicit issuer, public-key file SHA-256, exact HTTPS staging
origin/repository, active/revoked status, UTC validity interval and operator
trust reference. The policy must be active and current before signing and again
at publication. A missing, changed, expired, future or revoked policy cannot pass.
The private key's derived public key must match the independently trusted key
**before** Cosign signs the payload.

Supported authority labels are `operator-managed-application-key` and
`owned-lab-application-key`. These describe the operator's application signing
authority. They never establish Iron Bank/Platform One publisher authority.
The canonical publisher destination is excluded from this application signing
policy. The separate `controlled-test` mode cannot issue local signing authority.

Lifecycle assurance here is the protected, operator-selected active policy and
its expiry. This offline local-key contract makes no online revocation, X.509,
transparency-log, trusted timestamp or keyless identity claim. It does not impose
those mechanisms as a universal gate on customer signing systems. Selecting a
different trust mechanism requires its own explicit verified integration; a key
checksum alone does not establish authority.

## Explicit owned-lab credential setup

For an owned lab, this separate helper creates a new local credential directory:

```sh
"$DEVSECOPS_PYTHON" -B "$DEVSECOPS_PLUGIN_ROOT/scripts/generate_lab_signing_key.py" \
  --cosign "$PINNED_COSIGN" --output "$NEW_PRIVATE_LAB_KEY_DIRECTORY"
```

It uses the pinned binary to generate an encrypted P-256 key and a random
password. The directory is mode `0700`; `application.key`, `password.txt`,
`application.pub` and `retrieval.json` are mode `0600`. The helper confirms the
key pair and retained bytes before returning retrieval metadata. It refuses to
overwrite an existing directory. Password/private-key bytes and their hashes
are not included in that metadata or command output.

Store this directory outside repositories, demo reports, CI artifacts and plugin
packages. The password file contains the exact UTF-8 secret without a newline;
load it through the approved runner secret mechanism without printing it. The
retrieval record names the files and public-key SHA-256 so an operator can find
and manage the credential later. Only the public key is suitable for sharing.

Key generation does **not** write or approve a trust policy. Select the lab issuer,
repository and validity separately in the protected trust policy, then pin its
exact bytes in the signing configuration. No KMS, paid resource, registry key or
third-party publisher credential is created.

## Signed identity and retained evidence

The Simple Signing critical payload binds the exact staging repository and
platform manifest digest. Its `optional.devsecops` block binds:

- Issuer, authority label, public-key hash and independent trust-policy hash.
- Run/attempt, source commit, source context, architecture and verified base identity.
- Archive/config/manifest identities and immutable staging readback.
- Exact scan policy, database provenance, scanner/tool identities and scanner receipt.
- SPDX and Grype output hashes, applied exceptions, signing time and expiry.

The signed evidence omits local artifact paths. It additionally hashes the
complete direct assessment description, preserving its exact identity. The
image archive, SPDX, Grype JSON and scanner receipt are recopied and matched to
the live assessment before signing. The actual payload, bundle and public key
are then passed to `verify_image_signature`; its expected subject, input hashes,
binary identity and cryptographic result must all agree.

Cosign uses only a local encrypted key with signing configuration discovery,
ambient OIDC and transparency uploads disabled. It receives a private HOME,
empty PATH, private caches and only the explicitly selected password value.
No registry, KMS, OIDC, TUF, certificate discovery or timestamp endpoint is used.
The test suite includes OS-level network denial with a separate reachable
localhost canary; this distinguishes working offline operation from a missing
network service. Runtime network enforcement remains the runner's responsibility.

`mission-signing.json` retains safe execution receipts and input/output hashes.
Private credentials exist only in their selected storage and transient private
signer scratch. Credential diagnostics are discarded, including their hashes.
The output directory's identity and retained bytes are checked before and after
terminal publication; a swapped route, changed artifact or expired authority
withdraws the terminal result. The local bundle may still exist after a failed
run and must not be treated as a release grant.

Only a current, complete, non-controlled chain can issue process-local
`LocallySignedMission`. Its expiry is bounded by the assessment, signer policy
and 15 minutes. It has no public constructor or serialization path. A saved
bundle/report cannot restore that authority or authorize a later CI job.

## Remaining qualification

Real-tool tests use disposable locally generated keys and controlled image/
database fixtures. They exercise cryptography, subject binding, key mismatch,
payload tampering, expiry, file replacement and installed wrappers. They do not
establish a canonical live build or genuine current CVE coverage. Official
database acquisition and publisher base access remain the previously recorded
external gaps. Registry/CI signature publication and immutable readback are
still required before claiming published supply-chain evidence or promotion.
