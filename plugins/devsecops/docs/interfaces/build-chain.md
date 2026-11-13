# Build and artifact chain: implemented boundaries and unfinished gates

The current slice performs **real pinned Syft/SPDX and Grype operations on local bytes**, prepares the mission build context from an exact Git commit and the locked offline wheel, and implements a [direct F0-to-owned mission build](mission-executor.md), [explicit staging and immutable readback](mission-staging.md), and [staged-image security assessment](mission-security.md). The real Registry1/Linux build and approved staging/security chain have not yet been qualified. Signing/promotion and hosted CI qualification remain unfinished.

The [F0 authority](base-qualification.md) composes direct publisher trust, exact signed subject, required attestations, explicit policy and fixed runtime qualification. Its process-local result is required by the mission executor. A valid lock, content digest, successful Simple Signing check or JSON `qualified: true` cannot replace it. The GitLab recipe ends at publication and local proposal validation; Jenkins retains the unfinished full `build-gate`. Neither recipe establishes canonical factory acceptance.

## Pinned tools

`supplychain.lock.json` pins Syft 1.55.0 and Grype 0.121.0 for Darwin/Linux AMD64/ARM64, including the official checksum-file hash, archive hash/size and extracted executable hash/size. It additionally pins BuildKit 0.34.1 Linux AMD64/ARM64 release archives and `buildctl`/`buildkitd`/`buildkit-runc` executable hashes; GitHub's release-asset digest is the archive integrity source for BuildKit. RootlessKit 3.3.0 has explicit official checksum-file, archive and executable hashes for both Linux architectures. These checks are integrity checks, not publisher release-signature verification.

```sh
python3 scripts/bootstrap_supplychain.py
python3 scripts/bootstrap_supplychain.py --offline
# Also stage the Linux builder executables. This starts no daemon.
python3 scripts/bootstrap_supplychain.py --builder
# From a different host platform, prepare Linux bytes for explicit transfer:
python3 scripts/bootstrap_supplychain.py --platform linux-amd64 --builder
```

Host-platform executables stay below `~/.cache/devsecops/supplychain-v1/bin`; cross-platform staging defaults to a separate `supplychain-v1-<platform>/bin` cache to avoid replacing host tools. The bootstrap never executes staged binaries. No global install, daemon startup, UID-map/AppArmor changes, Docker socket or runtime fallback is performed. A runnable rootless worker remains a separate qualification task. Offline staging verifies all selected executable hashes and writes a private provisioning receipt; archive availability alone is not runtime qualification.

## Frozen mission preparation

The fixed recipe uses the existing mission Dockerfile, an explicit `registry1.dso.mil/ironbank/...@sha256:...` Python base, and the hashed prometheus-client wheel. `prepare_mission_build` accepts an exact full Git commit, source subdirectory, explicit local wheel, base repository/digest/architecture and new output directory.

It copies `app.py`, `probe.py`, `Dockerfile`, `requirements.txt` and `dependency-lock.json` through the existing immutable Git snapshot reader; checkout hooks/filters and candidate build scripts are not invoked. Dockerfile, requirements and dependency lock must match the installed recipe byte for byte. The offline wheel must match its independently pinned hash and size. The output contains `context/` and `prepared.json`, with commit, file hashes and base identity. Preparation is not evidence that the base can be pulled or run.

```sh
python3 "$DEVSECOPS_PLUGIN_ROOT/scripts/build_mission.py" prepare \
  --repo "$APP_REPOSITORY" --revision "$EXACT_COMMIT" \
  --wheel "$LOCKED_OFFLINE_WHEEL" \
  --base-repository "$IRONBANK_PYTHON_REPOSITORY" \
  --base-digest "$IRONBANK_PYTHON_DIGEST" --architecture amd64 \
  --output "$NEW_ABSOLUTE_OUTPUT"
```

The command exits 0 only for completed preparation; its receipt explicitly leaves build, push, scan, signing, promotion and base qualification unverified. No alternate base or unresolved digest is accepted. Operator variables in this example supply real values; they are not a filled qualified release lock.

## Local artifact scanning

Python API:

```python
scan_local_artifact(
    source=source_path, source_kind="directory", expected_sha256=tree_hash,
    syft=syft_path, grype=grype_path, output=new_output_directory,
    database_snapshot=database_archive_path, database_sha256=archive_hash,
    max_database_age_hours=120, timeout=120,
)
```

The source kind is explicit: `directory` or `oci-archive`. A directory is frozen as bounded regular files and directories with no symlinks/special files. Its hash is SHA-256 over the canonical sorted path/type/content manifest (`devsecops-tree-sha256-v1`), returned by `freeze_directory`; it is not an image digest. Directory evidence always retains `directory-not-whole-image-evidence`.

An OCI archive requires its raw archive hash **and** the expected single-platform image manifest digest and Linux architecture. The adapter verifies `oci-layout`, one index entry, all blob hashes, descriptor sizes/media types, the config platform and each uncompressed layer's `rootfs.diff_ids` identity. The archive, manifest, config and uncompressed layer digests remain distinct. Multi-platform/index nesting, foreign layers and other unsupported archive shapes stay unknown. Layers are read locally for cataloging; no registry pull occurs. Bounds currently limit source bytes and cumulative decoded layer bytes to 512 MB each, directory entries to 10,000, directory depth to 32, OCI entries to 1,024, layers to 128 and cumulative layer entries to 100,000. Nested gzip expansion is checked before invoking Syft. PAX/GNU extended headers are bounded before the tar parser consumes their payloads: 64 KB each, 8 MB cumulative, and 16 consecutively nested metadata headers. Sparse extensions are rejected. These protections also apply to the explicit Grype database archive. This is a deliberately bounded admission surface, not arbitrary-image coverage.

Both scanners are copied into private scratch and matched against the packaged lock and actual version output. Execution has empty helper PATH, private HOME/XDG/Docker/cache/config directories, no inherited credentials/proxy settings, explicit config files, no auto-update and no remote license enrichment. Commands are argument arrays through the shared bounded runner. Input-copy/archive loops share its monotonic deadline. No shell or source-supplied command/config is executed. This is application-level offline configuration; an OS network-denial qualification is separate unless explicitly recorded.

The database must be an explicit local `.tar.gz` archive hash selected by the operator. Its sole regular member is `vulnerability.db`; links, extra members, traversal and oversized decompression are rejected before import. Grype really imports it, reports schema/build time and scans the actual generated SPDX. The receipt records database archive hash, schema, build time, maximum allowed age, age at completion and scan completion time. Stale/future or missing DB remains unknown; no database download happens during scanning. The default maximum age is 120 hours and is bounded to 1–720 hours. The operator archive hash does not prove upstream database authority.

Retained outputs are `sbom.spdx.json`, `grype.json` when available, and terminal `receipt.json`, all mode 0600. Receipt fields include tool/input/output hashes, version/platform, bounded command exit/time/size/hash receipts, package count and severity counts. No raw image config, Env, credential values, key material or process output is included in the normalized receipt. Full SPDX/Grype files are intentionally retained scan artifacts, not prompts or instructions. The new output directory is exclusive; path identity is rechecked and a terminal receipt is withdrawn if the directory changes.

The common evidence envelope remains partial/unknown because origin, signature/attestation and promotion trust are unverified. A Grype reported-match decision is a narrow scanner observation; it is not exploitability, a policy-approved vulnerability disposition, or authorization to promote. `confirmed_failure` is false at the envelope level: there is not yet an independently configured vulnerability policy verdict. Consumers must inspect the retained findings and must not interpret a zero-match result against an arbitrary/synthetic database as a security approval.

`scripts/build_mission.py scan` exposes the same API. It returns exit 2 for the current incomplete aggregate. It accepts only explicit scanner paths, source hash and optional local database path/hash; an OCI scan additionally requires `--image-digest` and `--architecture`.

Original gzip and official-format Zstandard database archives are supported;
see [database archive admission](database-archives.md) for decoder bounds,
temporary disk requirements and actual native import evidence. A successful
archive import does not establish genuine or current vulnerability coverage.

## Rootless executor admission

`inspect_rootless_builder` requires Linux and an explicitly authorized Unix socket device/inode and non-root owner UID matching the caller. It checks restrictive socket ownership/mode, kernel `SO_PEERCRED`, the peer's pinned buildkitd executable, explicit `--rootless`, a non-host-root user-namespace UID mapping, process start identity, and before/after socket identity. Process executable, command, UID mapping and kernel namespace identities must remain unchanged. It rejects `--oci-worker-no-process-sandbox` and insecure-entitlement flags. Its shared absolute deadline bounds socket connection and executable hashing. It connects only to the supplied local socket and performs no build RPC.

`inspect_rootless_worker` adds the pinned BuildKit client's actual read-only worker RPC. It requires a network namespace distinct from the caller, one OCI worker, process sandbox mode, an admitted snapshotter, the exact client/daemon release revision, and the requested Linux architecture. Automatic CDI devices are not admitted. The client executes from frozen private scratch with empty helper PATH, private HOME/config/Docker directories, disabled telemetry, bounded output and a single absolute deadline; peer and socket identity are rechecked after the RPC. No credentials, daemon labels, raw diagnostics or arbitrary version metadata are copied into the normalized result.

```sh
python3 "$DEVSECOPS_PLUGIN_ROOT/scripts/build_mission.py" inspect-worker \
  --socket "$AUTHORIZED_BUILDKIT_SOCKET" --owner-uid "$BUILDER_UID" \
  --device "$SOCKET_DEVICE" --inode "$SOCKET_INODE" \
  --buildctl "$PINNED_BUILDCTL" --architecture amd64 --timeout 30
```

The result distinguishes `rpc-verified` worker readiness from untested runtime execution, registry TLS and egress enforcement, and it grants no build authority. A different network namespace is evidence of namespace separation, not an egress allowlist. The [owned executor](rootless-executor-qualification.md) now holds ownership across fixed scratch, base-runtime and mission recipes; [the mission consumer](mission-executor.md) requires concrete F0 authority and verifies exported image identity. Their controlled tests do not qualify a live Linux daemon. No Docker daemon fallback is permitted.

## Shared CI recipes and next concrete implementation

`demo/software-factory/ci/gitlab-ci.yml` calls the installed
`scripts/publish_and_propose_mission.py` with six pinned configurations, producing
registry publication evidence and a local deployment proposal in one process.
The [GitLab interface](gitlab-pipeline.md#current-ci-recipe-and-registry-compatibility)
lists the operator inputs and exact review artifacts; it performs no Git delivery
or deployment. The existing Jenkins recipe still prepares frozen inputs, calls
`scripts/build_mission.py build`, then the unfinished full `build-gate`, which
exits 2.

`DEVSECOPS_PLUGIN_ROOT` must be an operator-controlled, manifest-verified plugin
installation outside candidate source; CI definitions and runner authorization
likewise belong to the trusted CI configuration. No CI definition itself
authenticates a source/build relationship. The examples have not been executed
on hosted GitLab/Jenkins/Party Bus. The trusted runner must bind the build
configuration's source revision to its pipeline commit and select a new short
owned runtime root. No `allow_failure` bypass is configured.

Remaining ordered work:

1. Qualify the implemented F0 composition with actual authenticated Registry1 content and a real owned Linux runtime. No JSON constructor or config-selected Python callback can issue its base object.
2. Run the implemented typed mission consumer on that qualified base; retain actual source/context, toolchain, daemon and exported image evidence.
3. Qualify the implemented same-process staging consumer on the approved registry. It uploads the actual OCI artifact to one explicitly configured repository and independently reads back every byte by digest before issuing its process-local result.
4. Qualify the implemented direct scanner/policy consumer with a genuine authorized vulnerability snapshot. It binds the original listing and archive, exact SPDX/Grype subjects, required coverage, severity limits and exact expiring exceptions. Then implement signing of the verified immutable subject using credential references and verify against the approved key authority. Never retain raw keys or helper logs.
5. Promote the verified digest through the existing declared GitOps delivery path; no tag re-resolution. Prove the actual GitLab/Jenkins source/build/artifact chain, then integrate the canonical factory lifecycle and hosted/human qualification gates.

## Executed validation and limits

Local tests execute pinned Syft on synthetic directory and OCI fixtures and pinned Grype against a deliberately empty, freshly generated synthetic SQLite database. Both source paths also completed with macOS OS-level network denial applied to every scanner subprocess. This proves local parser/import/scan mechanics and those bounded offline executions, not current CVE coverage, a cold disconnected factory run or Iron Bank suitability. The earlier official metadata GET returned HTTP 403; a later normal-route request succeeded. The unchanged listing and checksummed official archive passed native import and produced 10 findings against a controlled older urllib3 image. Distribution metadata remained a coverage gap; see [database archive qualification](database-archives.md). Real rootless build, named registry push/readback, signing/promotion, hosted CI and human acceptance remain open. Adversarial cases cover digest/platform changes, tool tampering, source links/mutation, archive traversal/member and hidden metadata bounds/deadlines, stale/future/completion-expired databases, credential canaries and output-directory swaps.

Primary references: [Syft configuration](https://oss.anchore.com/docs/reference/syft/configuration/), [Grype database lifecycle](https://oss.anchore.com/docs/guides/vulnerability/database/), [Grype configuration](https://oss.anchore.com/docs/reference/grype/configuration/), [Grype 0.121.0 provider status contract](https://github.com/anchore/grype/blob/v0.121.0/grype/vulnerability/provider.go), [BuildKit rootless requirements](https://github.com/moby/buildkit/blob/v0.34.1/docs/rootless.md), [GitLab BuildKit integration](https://docs.gitlab.com/ci/docker/using_buildkit/), and [Iron Bank signatures/attestations](https://p1docs.dso.mil/iron-bank/tutorials/cosign).
