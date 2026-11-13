# Qualified local mission-image build

`scripts/build_mission.py build` performs F0 qualification and the actual mission
build in one process. It accepts a pinned operator configuration, captures the
exact local Git commit, and executes the installed mission Dockerfile in the
owned rootless lifecycle. It exports a verified OCI archive. This command does
not push, scan, sign, promote, or deploy it; `factory_decision` remains `unknown`.

The code path is implemented. Live Registry1 image access and Linux execution
must still be qualified before claiming a successful canonical mission build.
Controlled test processes and synthetic OCI fixtures do not meet that gate.

## Configure and run

Start with `demo/software-factory/ci/mission-build.example.json`. Its `REQUIRED_`
values deliberately fail validation until replaced with real selected hashes
and a full immutable Git commit. Keep the completed configuration and
credentials outside candidate source. Source, publisher material, image policy,
wheel, and tool paths are explicit absolute paths; spaces are supported there.
The runner supplies `DEVSECOPS_PYTHON`, the approved external Python interpreter
with the plugin's declared dependencies installed. It must not rely on a bare
system Python. `DEVSECOPS_PLUGIN_ROOT` names the verified installation; `-B`
prevents bytecode writes inside that immutable directory.
`owner_uid` must name the actual non-root Linux caller with the qualified
[rootless prerequisites](rootless-executor-qualification.md).

The publisher fields select the [current official key or a separately trusted
offline package](publisher-trust.md). `package` is `null` for direct official-key
acquisition. The image policy selects the exact base repository/digest/platform,
required predicates, and field rules; no candidate build setting can weaken
that policy. `tools.build_tools` contains the pinned `buildctl`, `buildkitd`,
`buildkit-runc`, and `rootlesskit` executables.

`run_scope` contains the six selected **factory** identities. Its
`config_sha256` names the separate factory configuration, not this build JSON;
the build configuration has its own explicit SHA-256 pin. This avoids a
self-referential hash. `source.revision` must be the actual pipeline's selected
commit when wiring CI. The packaged CI recipes pass paired
`--expected-source-repository` and `--expected-source-revision` arguments from
their checkout/head variables. A stale or different config fails before any
credentialed acquisition. These are explicit caller bindings, not independent
authentication of a hosted pipeline; that still needs the actual CI receipts.

`registry_auth` accepts only credential reference names and scoped transport
settings: `token_env`, or the existing Basic-to-scoped-Bearer fields
`basic_username_env`, `basic_password_env`, `token_path`, `token_service`; an
optional `ca_bundle` is an absolute path. Direct token values, arbitrary helper
commands, callbacks, and extra configuration fields are rejected. The command
does not create or discover Registry1 credentials.

```sh
"$DEVSECOPS_PYTHON" -B "$DEVSECOPS_PLUGIN_ROOT/scripts/build_mission.py" build \
  --config "$OPERATOR_BUILD_CONFIG" \
  --config-sha256 "$SELECTED_BUILD_CONFIG_SHA256" \
  --output "$NEW_ABSOLUTE_BUILD_EVIDENCE" \
  --runtime-root "$NEW_SHORT_OWNED_RUNTIME_ROOT" \
  --timeout 1500
```

Both output directories must be new. The runtime root is restricted to a short
absolute ASCII path, at most 60 bytes, because it contains Unix socket paths.
For example, use an explicitly chosen new path below an owned `/tmp` directory.
No broad cleanup or reuse of an existing runtime is attempted. The lifecycle
removes only its exact owned processes and runtime trees; image/log/evidence
files remain for inspection. Installing the plugin starts none of these actions.

Exit 0 means the narrow qualification-and-build operation passed. Exit 2 means
incomplete/unknown or invalid input. The generic `build-gate` command remains
closed for the unfinished full staging/security/promotion chain. Do not use a
successful local build as a vulnerability-policy or deployment approval.

## What execution verifies

The [F0 issuer](base-qualification.md) must return an actual process-local
`VerifiedBase`. `build_mission` directly calls exact-Git preparation; it cannot
load a supplied `prepared.json` as authority. Dockerfile, requirements, and
dependency lock must match installed bytes. The wheel must match that lock.
The source commit, every selected file, and the complete context hash are
rechecked after freezing into the runtime.

The base capability is checked before preparation, immediately before daemon
and build-client startup, after execution/cleanup, and during publication. The
original qualified OCI layout is frozen and its full inventory revalidated.
BuildKit receives the original repository and requested digest through a named
OCI context pointing at the selected platform manifest. Two fixed build args
fill the existing Dockerfile's repository and SHA-256 fields. There is no
Dockerfile rewrite, arbitrary frontend, daemon fallback, networked `RUN`, or
push exporter. RootlessKit has `--net=none`, and each run has a fresh private
daemon with the pinned toolchain, no cache, and no credential forwarding.

After the actual build exits, the executor checks the complete OCI archive,
exporter metadata, config, every blob, platform, and layer diff IDs. The output
must retain the qualified base's filesystem prefix. Its final effective layers
must contain the selected `app.py` and `probe.py` bytes under UID/GID 10001,
with owner read permission. Whiteouts are interpreted as lower-layer removal;
an opaque whiteout does not erase files introduced in its own layer. Ambiguous
duplicate paths or unsupported ancestor substitutions are rejected.

Final config must have the installed entrypoint, command, work directory,
UID/GID, required exposed ports, and Python environment settings. Inherited
`ONBUILD` instructions cannot qualify. The worker identity must remain unchanged
and cleanup must pass. Output is then copied into the evidence directory,
matched to the executor's archive hash, and validated again as complete OCI.
Publication checks the output route, current authority/deadline, and retained
archive bytes. A late failure withdraws the terminal receipt.

## Retained identity and handoff

The operation tree contains `base/` F0 evidence, `mission/prepared/` frozen
source, `mission/image.oci.tar`, the sanitized builder qualification,
`mission/mission-build.json`, and `mission-operation.json`. Reports bind source
commit/context, base index/platform/config identities, layout inventory, tool
lock and binary hashes, builder run, exported image/config digest, and copied
application hashes. Raw registry credentials are not included.

The Python `build_mission` API returns `(report, BuiltMission | None)`.
`qualify_and_build` returns the same artifact object after direct F0 composition.
`BuiltMission` cannot be normally constructed, pickled, restored from JSON, or
used from a forked process. Its guard checks the same factory run, source commit,
architecture, and both wall-clock and monotonic expiry. It expires within 15
minutes and never outlives the base authority. The next staging writer must
freeze and recheck the bound archive again before any upload. That writer is
not implemented in this slice. The standalone command prints evidence and exits;
its saved report does not grant a later process push authority.

Bounds include a 60–1800 second composed deadline, 30–900 seconds for the
mission consumer, at most 600 seconds for each owned builder invocation,
512 MB source/output OCI limits, and the existing tar metadata/entry/decoded
limits. Artifact hashes are streamed rather than loading a full image into RAM.
Owned cleanup has a separate bounded safety allowance after expiry, but cannot
extend a passing deadline. Allow disk space for the retained base, preparation,
runtime export, and frozen final archive; these are intentionally separate
copies.

## Executed validation

Tests use real Git objects, the real pinned offline wheel, and valid tar/gzip
OCI byte fixtures. They exercise preparation, fixed command generation,
immutable base/context checks, rootless lifecycle orchestration, full output
validation, source/runtime mismatches, whiteouts, expiry, strict configuration,
and rejection of saved-report authority. Linux processes/worker responses in
those tests are controlled and explicitly labeled; they cannot issue a
canonical build result. Live P1, Linux build, registry staging, real scan data,
signing, hosted CI, and full recovery acceptance remain separate evidence.
