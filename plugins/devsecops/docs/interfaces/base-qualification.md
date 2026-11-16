# Direct mission-base qualification

`devsecops.factory.base_qualification.qualify_mission_base` composes the concrete
F0 checks in one process. It acquires the configured image from Registry1,
verifies the selected publisher key and each required signature, evaluates the
separately pinned image policy, acquires every selected platform layer, and
executes a fixed offline Python probe using those exact bytes. A successful call
returns `(report, VerifiedBase)`. An incomplete or failed call returns a report
and `None`; invalid invocation parameters raise before execution.

This is an internal build prerequisite. It does not approve a deployment,
certify compliance, or complete the canonical demo. No serialized report can be
imported as a `VerifiedBase` object. The [mission executor](mission-executor.md)
consumes this object in the same process; staging/security/promotion remain
separate unfinished factory gates.

## Inputs and operation

The caller must run on an explicitly owned Linux builder account with the
[rootless executor prerequisites](rootless-executor-qualification.md). The API uses separate
new absolute directories for retained F0 evidence and the temporary runtime;
the runtime path must satisfy the qualifier's short socket-path limit. It does
not create user accounts, subordinate UID/GID mappings, host packages, or
third-party credentials.

```python
report, verified_base = qualify_mission_base(
    publisher_policy=publisher_policy_path,
    publisher_policy_sha256=selected_publisher_policy_hash,
    publisher_package=trusted_package_path,  # Only for selected offline-key mode.
    image_policy=image_policy_path,
    image_policy_sha256=selected_image_policy_hash,
    public_key=selected_publisher_key_path,
    cosign=pinned_cosign_binary,
    wheel=locked_prometheus_wheel,
    tools=pinned_linux_build_tools_directory,
    output=new_evidence_directory,
    runtime_output=new_short_runtime_directory,
    expected_uid=owned_builder_uid,
    run_scope=selected_six_run_identity_hashes,
    token_env="REGISTRY1_READ_TOKEN",
    total_timeout=900,
)
```

`run_scope` has the six exact SHA-256 fields from `factory.state.IDENTITY_KEYS`:
scenario, assertions, package, lock, configuration, and environment identities.
Both policy files and the public key are frozen before use. The exact key must
match the publisher and image policies. The public API requires the
`platform-one-iron-bank` publisher, `https://registry1.dso.mil`, an `ironbank/`
repository, an immutable digest, and the selected Linux architecture. It does
not select a mutable tag or substitute another image.

The [publisher policy](publisher-trust.md) supports direct current-key
publication or explicitly pinned, separately trusted operator material. The
[image policy](image-policy.md) supplies the required predicate types and exact
field rules. Required signature verification and config assessment must pass
before layer acquisition or execution. Unknown schema, absent required
predicate, invalid signature, mismatched signer, inherited `ONBUILD`, or failed
rule leaves authority unissued. The policy rules express operator requirements;
matching a signed assertion does not independently prove the assertion's facts.

All registry requests use the existing bounded, same-origin authenticated HTTPS
transport, without redirects, proxy inheritance, or credential forwarding.
The origin, repository, requested digest, index, selected manifest, config, and
architecture are compared across signature acquisition and
[complete OCI acquisition](image-layout-acquisition.md). The build namespace has
no network; external acquisition happens before it starts. An isolated retained
guest needs its own approved acquisition/transport path. A downloaded JSON
receipt is never a substitute for that path.

## Fixed runtime probe

The qualifier freezes and rechecks the acquired OCI layout and installed
dependency lock, requirements file, and exact wheel. BuildKit receives the
original digest-qualified `FROM` reference through its named OCI layout context
at the selected platform manifest digest. It cannot resolve a registry fallback
inside RootlessKit's `--net=none` namespace. The existing private daemon/socket,
pinned toolchain, worker admission, process identity, bounded execution, and
exact cleanup rules also apply to this recipe.

The generated Dockerfile has two `RUN --network=none` steps and no candidate
application code. It requires Python 3.11 or newer, `ensurepip`, working virtual
environment creation, and installation of the pinned wheel with `--no-index`,
`--require-hashes`, `--only-binary=:all:`, `--no-deps`, and isolated pip settings.
It then imports the dependency at the locked version under UID/GID 10001. A
fresh nonce binds those results to this invocation. The final scratch stage
only packages the proof file; both executable probe steps use the exact Iron
Bank base.

Success requires the observed execution marker, the actual exported OCI
manifest/config/layer identities, a matching BuildKit exporter digest, the
nonce-bound proof file, unchanged worker identity, and completed cleanup. The
proof records the actual Python version, machine architecture, root and
application UID/GID, venv support, and dependency version. A platform inspection
or successful worker RPC alone cannot pass it.

Bounds remain explicit: the F0 deadline is 30–1800 seconds and each rootless run
is 30–600 seconds. Owned cleanup retains a separate bounded safety allowance
after expiry, but late completion cannot pass or issue authority. The frozen
source layout is at most 512 MB, and the exported
proof artifact is at most 128 MB. The sole decoded proof layer is at most 64 KB
and its JSON at most 16 KB. Unsupported inputs remain incomplete.

## Authority lifetime and evidence

The process-local `VerifiedBase` has no normal constructor and cannot be
pickled. `require_verified_base` admits only an object issued by this process,
for the exact six run identities, repository, requested digest, and architecture.
The lifetime is the earliest of 15 minutes, either policy's validity end, and
the trusted offline key package's freshness end. Both monotonic and wall clocks
are checked. Forked processes, stale objects, dictionaries, and saved report
files are rejected.

Bindings include the selected manifest/config, exact OCI inventory and hash,
publisher and cryptographic evidence hashes, runtime run/proof, and expiry. A
build consumer must call the guard and freeze/revalidate the referenced
layout before use; a retained path alone never establishes current byte
identity. Terminal publication checks its output route and deadline before an
object is issued, withdrawing the receipt on publication failure.

The retained tree contains publisher evidence, actual signature/policy evidence,
the acquired OCI layout, the sanitized runtime observation, and a sealed
`base-qualification.json`. Component facts remain visible even when the aggregate
cannot issue authority. The runtime tree retains its own qualification and
cleanup observations. It does not retain credential values in the normalized
reports.

## Validation scope

Tests execute authenticated localhost TLS, real pinned Cosign image and DSSE
verification, complete tar/gzip layer acquisition, policy assessment, and the
composition's runtime boundary. That boundary is explicitly controlled and can
never issue canonical authority. Additional tests cover the fixed recipe,
locked offline wheel, content-bound proof decoding, identity disagreements,
expired package freshness, output swaps, and rejection of imported authority.
Existing rootless scratch/cleanup tests remain in the regression set.

No actual Registry1 image or Linux Python-runtime probe was qualified by those
local tests. Live authenticated P1 acquisition, the real rootless probe,
mission build/push/read-back, real vulnerability data, CI delivery, and the full
canonical recovery demo remain separate acceptance work.

Primary references: [P1 signing and attestation verification](https://p1docs.dso.mil/iron-bank/tutorials/cosign),
[BuildKit v0.34.1 named OCI contexts](https://github.com/moby/buildkit/blob/v0.34.1/frontend/dockerui/namedcontext.go),
and [BuildKit rootless operation](https://github.com/moby/buildkit/blob/v0.34.1/docs/rootless.md).
