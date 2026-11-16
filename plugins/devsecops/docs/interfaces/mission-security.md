# Staged-image security assessment

`scripts/assess_mission.py` runs the direct qualification, build, immutable
staging/readback and security assessment chain in one process. It uses the
existing pinned Syft 1.55.0 and Grype 0.121.0 adapter. It generates SPDX 2.3 from
the exact staged OCI archive, imports a selected offline database, runs Grype,
and evaluates an independently pinned policy.

For an OCI image, the scanner catalogs once to Syft's native JSON and uses the
same pinned Syft executable to convert that catalog to the retained SPDX.
Grype reads the native catalog: the pinned SPDX conversion loses distribution
metadata, while native JSON preserves distribution and package matcher details.
Directory-only scans keep their existing SPDX handoff and coverage limitation.

This is a **scan and policy gate**. It does not sign, attest or promote an image,
and `factory_decision` stays `unknown`. A usable scan result is a prerequisite
for those later operations, not an authorization to perform them.

## Run from an installed package

The runner supplies `DEVSECOPS_PYTHON`, an approved Python 3.11+ interpreter with
the plugin's exact external runtime dependencies installed. The wrapper does not
depend on the current checkout or the system Python. Use `-B` to keep the
installed plugin cache immutable.

```sh
"$DEVSECOPS_PYTHON" -B "$DEVSECOPS_PLUGIN_ROOT/scripts/assess_mission.py" \
  --build-config "$BUILD_CONFIG" --build-config-sha256 "$BUILD_CONFIG_SHA256" \
  --staging-config "$STAGING_CONFIG" --staging-config-sha256 "$STAGING_CONFIG_SHA256" \
  --security-config "$SECURITY_CONFIG" --security-config-sha256 "$SECURITY_CONFIG_SHA256" \
  --expected-source-repository "$SOURCE_REPOSITORY" \
  --expected-source-revision "$SOURCE_REVISION" \
  --runtime-root "$OWNED_LINUX_RUNTIME_ROOT" \
  --output "$NEW_RUN_DIRECTORY" --timeout 3000
```

The command **writes blobs and a digest-addressed manifest to the selected
staging registry before scanning**. Keep the separately selected build/staging
configuration and write credential controls from [mission staging](mission-staging.md).
All three configuration pins and the policy/listing/database hashes are checked
before build or upload begins. Keep operator configurations and their expected
hashes in protected runner configuration outside the candidate checkout. A
candidate must not be able to select weaker limits or alter an exception.

Exit codes are `0` for the narrow assessment pass, `1` for an established policy
failure and `2` for incomplete or unsupported evidence. An incomplete run after
staging retains its mutation intents; inspect those exact digests before another
attempt. The command does not automatically delete, retry or promote a staged
image. Total time is bounded to 3600 seconds and assessment to 900 seconds. The
native scanner sequence defaults to 300 seconds; the pinned optional
`scanner_timeout_seconds` can select an integer from 1 through 600. These are
execution budgets and do not extend any image, policy or exception validity.

For slower or emulated runners, the pinned optional
`database_preparation_timeout_seconds` selects 1 through 1800 seconds to import
the exact offline database **before** starting short-lived image qualification
and build authority. Preparation consumes the same overall operation deadline.
The imported cache is private, read-only and usable only within the creating
process and context. Its exact tool, archive, file identities and content hashes
are checked before and after the actual image scan; freshness is checked at use.
The original policy, listing and archive are rechecked after preparation before
build or upload starts. The cache is removed when the context exits. A retained
`database-preparation.json` receipt describes this step but cannot authorize a
scan, build or promotion. Omitting this field keeps import inside the scanner
sequence. No previous scan result is reused.

The [configuration template](../../demo/software-factory/ci/mission-security.example.json)
contains local absolute paths for the policy, database package, original listing
and pinned scanner binaries. Replace every placeholder and independently pin
the exact configuration bytes. The
[policy template](../../demo/software-factory/ci/mission-security-policy.example.json)
requires explicit validity dates and package identities; it deliberately cannot
authorize a run unchanged.

## Database trust and freshness

The selected contract is `operator-selected-official-anchore-v6`: a trusted
operator obtains an original Anchore `v6/latest.json` and the archive it names
through an approved route, retains both, and selects their exact SHA-256 hashes
in the independently pinned policy. `trust_reference` records the organization's
acquisition/approval record; `acquired_at` records when the selected package was
obtained. No credentials or token values belong in either field.

The assessment checks the listing's active status, exact archive checksum,
schema and build time against the imported database status and actual Grype
output. Acquisition cannot precede the build or be in the future. The database's
maximum age is explicit, 1–720 hours, measured from its **build time** and checked
again at completion and success publication. Gzip and bounded Zstandard packages
are supported by the [database archive adapter](database-archives.md).

The original listing bytes are retained unchanged. Current publisher metadata
uses a `v`-prefixed schema such as `v6.1.10` and a timestamp-containing archive
basename such as `vulnerability-db_v6.1.10_2026-10-27T00:30:54Z_1793082641.tar.zst`.
Both that schema spelling and the earlier unprefixed `6.1.10` spelling bind to
the native scanner's exact `v6.1.10` status. Only that optional prefix is
normalized for comparison. Version numbers, build time and checksum must still
agree. The bounded archive basename accepts timestamp colons, but no path
separators, URL escapes, query or fragment syntax; it is never followed here.

The listing's SHA-256 proves byte identity. It does not authenticate where the
listing came from. Authority is explicitly **operator selected**; the report
does not claim publisher cryptographic authentication. The retained source URL
is provenance context, never a URL followed by this operation. No database
download, network update, proxy, mirror or fallback occurs. Other integrations
can implement a different explicit provenance contract; this contract does not
change the generic artifact scanner's requirements.

`controlled-test` is a separate provenance mode for fixtures. It can demonstrate
native import, scan and policy mechanics but can never issue `AssessedMission`,
even if no matches are returned. The deliberately empty test database also lacks
the default provider coverage. It is not genuine vulnerability intelligence.

## Fixed policy, visible findings and narrow exceptions

Schema 1 has fixed fields, not an executable policy language:

| Field | Behavior |
| --- | --- |
| `id`, `version`, validity interval | Names the selected policy and limits its lifetime. |
| `severity_limits` | Maximum unique findings for each of Critical, High, Medium, Low, Negligible and Unknown. `null` means no numeric gate for that severity. |
| `coverage.minimum_packages` | Requires at least this many non-container SPDX packages. |
| `coverage.require_distro` | Requires Grype to identify a distribution name and version. |
| `coverage.required_providers` | Requires these provider names in the imported database metadata. |
| `database` | Selects offline authority, exact listing/archive bytes and freshness. |
| `exceptions` | Exact, independently approved vulnerability/package selectors with expiry. |

The customer-neutral template blocks Critical, High and Unknown findings, keeps
all other findings visible, requires package and distribution coverage, and
requires `nvd` and `github` provider metadata. Customers can change these fixed
limits in their protected policy. Missing coverage remains `unknown`; an empty
match array alone is not a pass.

An exception contains `id`, `selector`, `reason`, `reference`, `approved_at`, and
`expires_at`. Its selector must name the exact vulnerability ID, namespace,
package name, package version, package type, purl (or an explicitly empty purl),
and severity. No pattern matching is performed. Changed versions, namespaces or
severity do not reuse an old exception. Duplicate IDs/selectors are rejected.
Expired and not-yet-active exceptions are visible but do not suppress findings.
The report retains every normalized finding and identifies the exception used.
Only the exact selected, unexpired exceptions can reduce gate counts.

Counts combine `matches` and all **reported** `ignoredMatches`. Duplicate
locations for an identical vulnerability/package count once. Conflicting
severity for one identity is unsupported evidence. Every finding must match a
package in the generated SPDX. This gate measures reported findings under the
pinned scanner configuration, not exploitability or exhaustive absence of
vulnerabilities. Grype's pinned default matcher behavior remains in effect; the
raw bounded JSON is retained for review.

## Identity and publication

The consumer accepts only a live, same-process `StagedMission`; no saved report
or arbitrary object can construct one. It checks run, source commit, architecture,
staging origin/repository and expiry before reading inputs or executing tools.
The OCI archive is recopied and validated against its archive hash, platform
manifest digest, config digest and platform. SPDX's image checksum and source
version, Grype's image subject, scanner versions, database status and retained
artifact hashes must agree with direct execution.

Before conversion and scanning, the native catalog's source name, archive
identity, platform, manifest digest, config digest, and embedded manifest/config
bytes must match the validated OCI archive. Its private scratch file is checked
again after both consumers. Native JSON is not published or retained as an
artifact because it includes the full image configuration. Its byte count,
SHA-256 and native schema version are recorded with the scanner receipt.

Grype's original image result undergoes the same strict source identity checks.
The retained `grype.json` preserves matches, reported ignored matches, and
distribution information, then applies the named
`devsecops-grype-image-projection-v1` projection. This replaces the source
envelope with the archive alias, manifest/config digests and platform; removes
the entire scanner configuration envelope; and retains only database schema,
build time, validity and bounded provider metadata from the descriptor. Raw
image configuration, registry settings and temporary paths are not copied into
published evidence. Unknown envelope fields stop the operation. The receipt
records original stdout hashes, configuration hash and projection method;
those hashes do not imply that the raw configuration-bearing bytes are retained.

The distribution policy is unchanged. A missing or unrecognized distribution
still leaves required coverage incomplete. Preserving this metadata does not
establish image origin, runtime architecture, hardening or release approval.

The output retains private copies of policy, database listing/package, OCI
archive, SPDX, Grype JSON and scanner receipt. `mission-security.json` binds
those identities to the source context, build/base and immutable staging
readback. Retained files are rehashed through the owned output directory before
and after terminal success/failure publication. Changed bytes, swapped routes,
expired authority or an expired applied exception withdraw the terminal result.

Only complete, current, non-controlled evidence satisfying the explicit policy
can issue process-local `AssessedMission`. Its validity is bounded by the staged
image, policy, database freshness, applied exceptions and a maximum 15-minute
lifetime. Its constructor and serialization are closed. `require_assessed_mission`
rechecks exact run/destination/policy and both wall and monotonic expiry. Reports
are review artifacts and cannot restore this authority in another process.

Signing must consume that direct typed result in the same trusted process or
use a separately engineered durable admission mechanism. A later CI job cannot
turn `mission-security.json` into permission to sign.

## Qualification status

Tests run genuine pinned Syft/Grype binaries against controlled OCI and database
fixtures, check strict subject binding, stale/mismatched inputs, threshold and
exception cases, reported ignored matches, deadline/terminal changes and a clean
installed wrapper. They do not establish live official database provenance or a
canonical mission release. The earlier official metadata GET returned HTTP 403.
A subsequent request through the normal route returned HTTP 200; its unchanged
listing and checksummed archive passed real native import. That controlled image
scan found 10 matches (7 High, 3 Medium) and correctly failed the selected policy.
The original SPDX handoff lost distribution metadata. The retained 2026-10-28
native-catalog regression preserves Ubuntu 24.04, reports the same 10 findings
with no coverage gaps, and still fails the unchanged High-severity gate. Image
Env/Labels canaries are absent from its retained artifacts. This genuine-database
regression requires an explicitly selected retained package and never downloads
one. Authenticated publisher base access, policy assessment of the actual mission
image and the real Linux build remain required for the canonical demonstration.
Signing/promotion is not part of this slice.

Format references: [Anchore database guidance](https://oss.anchore.com/docs/guides/vulnerability/database/),
[pinned Grype listing model](https://github.com/anchore/grype/blob/v0.121.0/grype/db/v6/distribution/latest.go),
and [pinned Grype output model](https://github.com/anchore/grype/blob/v0.121.0/grype/presenter/models/document.go).
