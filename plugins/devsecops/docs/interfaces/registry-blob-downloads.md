# Explicit registry blob downloads

Some registries return an HTTP 307 for a content-addressed blob and place its
bytes in object storage. The optional blob download policy selects that exact
storage origin. It does not grant network access or establish image trust.
Without the policy, redirects remain rejected.

Select a private policy file outside the installed package and pin its SHA-256.
The public acquisition, verification and assessment interfaces accept the pair
`blob_download_policy` and `blob_download_policy_sha256`; command-line tools use
`--blob-download-policy` and `--blob-download-policy-sha256`. Service and mission
configuration use `blob_download_policy: {path, sha256}`. The service must already
permit the selected repository. Effective repository scope is the intersection
of the policy and that existing scope.

The policy has exactly these fields:

```json
{
  "schema_version": 1,
  "kind": "registry-blob-download-policy",
  "registry_origin": "https://registry1.dso.mil",
  "repositories": ["ironbank/opensource/nginx/nginx"],
  "storage_origins": ["https://ib-prod-harbor-storage.s3.us-gov-west-1.amazonaws.com"]
}
```

Only a fresh 307 response from the selected repository's exact blob digest route
can select one follow-up request. Manifest, token and ordinary service routes
retain their strict redirect rejection. The target must match an exact selected
HTTPS origin and the supported AWS SigV4 presigned GET form. Wildcards, alternate
origins, ambiguous paths or queries, and a second redirect are rejected.

The storage connection uses its own normal TLS verification and receives no
registry Authorization, cookies, client certificate, private CA context or proxy
configuration. Source, token and storage requests share the session's original
request, byte and time budgets. A storage authentication failure cannot trigger
another registry token exchange. Buffered content and streamed layers retain
their descriptor size, digest, media and encoding checks. Streamed layouts also
retain decoded layer and private-file checks.

Signed URL values stay in memory. Evidence may record the public origin, URL
and path hashes, query parameter names and selected policy identity; it must not
record the temporary signed capability itself. Policy selection does not verify
the AWS signature or claim the URL remains unexpired. The storage response and
the content checks must succeed.

For the owned Platform One runner, select the same policy pin in
`egress.blob_download_policy` and in the staged build configuration. The explicit
storage route must agree across the VM recipe, host relay, guest route evidence
and runner admission. The default profile does not gain storage access. Source
preparation alone does not start a VM, install routes or authorize a download.

## Explicit attestation size profile

Redirect support does not change the default attestation limits or image-subject
selection. Without a size profile, acquisition and verification allow 4,000,000
bytes per envelope and 2,500,000 bytes per decoded DSSE payload. Generic buffered
HTTPS configuration retains its 8,000,000-byte ceiling.

The separate optional `attestation_size_policy` path and
`attestation_size_policy_sha256` select a closed, hash-pinned JSON document with
`schema_version: 1`, `kind: registry-attestation-size-policy`, `origin`,
`repository`, `subject_digest`, and `descriptors`. Each descriptor contains only
`digest` and `size_bytes`. Configuration uses the corresponding `{path, sha256}`
object. The origin, repository and subject must match the independently selected
image. This profile provides resource bounds, not acquisition or image authority.

In versions 1 and 2, only an exact listed descriptor receives the fixed 8 MiB envelope and 6 MiB
decoded-payload ceilings. The actual response must match its digest and exact
size before parsing. Unknown oversized descriptors and changed sizes fail.
The HTTP exception is internal and bound to that individual selected blob;
ordinary metadata, token, manifest and signature requests retain their limits.
Acquisition, frozen verification artifacts and reassessment use the same pinned
profile. The owned runner must stage and validate its original bytes.

Version 1 and unselected objects retain the 100,000-value JSON limit. Version 2
uses the same top-level fields and requires `max_payload_json_values` on every
selected descriptor. Its integer allowance must be between 100,000 and 300,000;
booleans, implicit defaults and unknown fields are rejected. The exact policy
version, per-descriptor allowances and content hash are included in the issued
policy identity. A changed or copied policy object cannot grant an allowance.
Acquisition, cryptographic verification, negative controls and predicate-policy
assessment all use the same digest/size-bound allowance. A parser limit is
reported explicitly; an unparsed object does not establish publisher omission.

Version 3 requires an explicit per-descriptor JSON allowance from 100,000 to
500,000 values and admits selected envelopes up to 16 MiB with decoded payloads
up to 12 MiB. It is intended
for larger signed SBOMs, including the 10,339,372-byte Syft envelope observed in
the Iron Bank Python 3.13 metadata on November 4. Its decoded payload contains
326,178 JSON values; a selected allowance of 350,000 accommodates that object.
Versions 1 and 2 keep their
original byte and JSON limits. Unselected objects, ordinary HTTP requests, the 16-descriptor
maximum and the shared 64,000,000-byte budget also keep their original limits.
The issued transport selection includes the version's envelope ceiling, and the
response still must match the independently pinned descriptor's exact size and
digest. This opt-in does not establish signature validity or authorize a build.

JSON depth remains 64. Duplicate keys, nonfinite numbers, invalid UTF-8 and
non-object roots remain invalid. Predicate types are exact HTTPS identifiers;
fragments such as Syft's `#output-formats` are preserved in the signed identity
and compared byte-for-byte. They are never dereferenced or normalized.

The original aggregate request, byte and time caps still apply across source, token and storage traffic;
a larger selected object does not replenish those budgets. Signature, predicate
and subject checks remain mandatory.

## Interpreting publication metadata

For a factory registry service that serves several selected images, configure
`attestation_size_policies` as a list of one to sixteen exact `{path, sha256}`
references. This setting is mutually exclusive with the existing singular
`attestation_size_policy`. Each canonical image control still selects its own
singular policy reference. That reference must appear in the service list and
bind the control's exact origin, repository and immutable subject digest.

The list must contain unique paths and unique subjects. All policies must match
the configured registry origin and repository scope. The verifier rereads and
hashes the bounded selections before matching a subject; changed bytes and
ambiguous matches fail. Descriptor and parser limits remain local to each
selected policy and are never merged across images. The singular setting and
default limits remain compatible for existing configurations.

The retained November 3 Registry1 metadata contains six platform attestation
descriptors totaling 22,078,644 bytes. Three exceed the envelope limit, and the
largest is 8,318,244 bytes. A metadata observation alone does not establish payload
sizes, predicate types or cryptographic validity. The explicit
profile can accommodate their observed envelope sizes, but actual parsing and
cryptographic verification still must pass. Objects cannot be silently skipped
or accepted by relaxing a limit during a run.

The observed index attestation route returned 404. Platform attestations cannot
substitute for an index subject. Select the actual subject and required
predicates explicitly, then verify the corresponding signed evidence. Neither
publication metadata nor the redirect policy qualifies the canonical demo.
