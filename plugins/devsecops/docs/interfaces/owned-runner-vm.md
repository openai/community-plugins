# Dedicated owned runner VM inputs

The canonical VM runner uses the existing `owned_bootstrap_recipe` auxiliary
selection. `schema_version: 2`, `kind: owned-bootstrap-v2` replaces
`services.runner_image` with the pinned file reference `services.runner_vm`.
Both fields cannot coexist. Version 1 remains parseable for retained diagnostics;
it cannot silently select the new VM or bypass the legacy container prerequisite
failure. This input contract adds no canonical lifecycle phase or auxiliary key.

The nested file has `schema_version: 1`, `kind: owned-runner-vm-v1` and the closed
schema `schemas/owned-runner-vm.schema.json`. It selects:

- The original owned host and frozen SSH transport, plus its existing QEMU tool
  image ID. Normal authenticated host-transport renewal remains separately bound
  to the original foundation object.
- An explicitly pinned preprovisioned runner boot image, four independently pinned
  operator/host SSH key inputs, a separate remote generation parent ending in
  `/.local/share/devsecops/runner-vm`, and a separate private local state directory.
  Runner image bytes, guest SSH identities and disposable directories must differ
  from the RKE2 guest. No retained guest or disk may be adopted.
- Explicit bounded `cpus`, `memory_mib` and `disk_gib`; there are no resource
  defaults or claims that these values have been benchmarked.
- The fixed guest account: SSH and job user `gitlab-runner`, UID/GID 10001, home
  `/home/gitlab-runner`. The preprovisioned image must provide GitLab Runner 19.5.1,
  Linux Python 3.11 or newer, the selected dependency/tool/schema material, and
  normal subordinate-ID mapping helpers and at least 65536 subordinate IDs.
  These are admission requirements, not assertions that a selected image already
  meets them. Runtime guest access has no sudo, root SSH or administrative account.
- `cleanup: remove-owned-runner-generation-preserve-inputs`. Supplied images,
  transport files and original key inputs are preserved; exact created copies,
  guest processes, credentials, disks and other owned resources need separate
  original lifecycle observations and complete teardown.

GitLab server and Runner have independent patch versions. The
[GitLab 19.5.3 release notes](https://docs.gitlab.com/releases/patches/patch-release-gitlab-19-5-3-released/)
select Runner 19.5.1. Its official Linux amd64 checksum is
`0e11e71404352e67d286269aee397ec8e2825e0fc91a12f60c04cef0b85edb85`, from the
[versioned checksum list](https://s3.dualstack.us-east-1.amazonaws.com/gitlab-runner-downloads/v19.5.1/release.sha256).
Verify those bytes during image preparation; successful version admission alone
does not verify the download or qualify the image.

The outer QEMU container retains no-new-privileges, dropped capabilities and
restricted networking. An optional, matching [KVM selection](qemu-acceleration.md)
adds only the checked device and group to the runtime container. The default
remains TCG. Guest
preprovisioning and the fixed first-boot seed are the only privileged setup
boundary. No runtime package installation, arbitrary boot command, global sysctl
change or security-policy adjustment is accepted by this recipe.

## Current package and private staging

The selected guest image must contain the **actual candidate package**, external
interpreter and immutable tools at the selected `/opt/devsecops/` locations.
`RunnerInstallPlan.guest_package` binds the selected root to the original current
run's package manifest hash, plugin ID and version. An image-era package, fixture
package, copied receipt or unverified saved identity cannot substitute. Guest
admission must read and verify the real package bytes before CI execution.

Existing recipe material targets and invocation selectors under
`/opt/devsecops/runner-inputs/` are logical input names. Version 2 compiles them
only into:

```text
/home/gitlab-runner/.local/share/devsecops/<full-UUID-hex>/inputs/
```

The original same-process plan holds all six configuration templates, public
trust files and selected private bytes. It retains and rechecks the bootstrap
recipe, nested VM recipe, original invocation, every template, selected material
and public CA hash before staging. Private material never becomes a public
receipt. The fixed guest handler stages only this original plan, with private
permissions and actual readback before launching the registered runner.

Package, Python and validation-tool paths remain immutable selected `/opt` paths;
the job user does not install or modify them. Publication output remains
`/home/gitlab-runner/builds/factory-<UUID>/publication`. Runtime state is
`/home/gitlab-runner/builds/<full-UUID-hex>`: all 128 run-identity bits are retained
within the build entry point's 60-byte limit and downstream executor limits.

## Explicit network and trust selection

Owned GitLab and registry endpoints derive from original service capabilities,
with their exact names, ports and public CA pins. Their guest hostname mappings
belong to the reviewed first-boot seed. The nonroot runtime only verifies them;
it does not rewrite `/etc/hosts`, DNS, system trust or proxy configuration.

`egress.external_origins` is an ordered, unique list drawn only from
`https://registry1.dso.mil` followed by `https://p1docs.dso.mil`. An empty list is
valid input describing unavailable external access. Nonempty selection requires
the explicit public file pin `egress.external_ca_bundle`; empty selection requires
that field to be null. There is no system-trust fallback, wildcard, alternate
port, redirect permission or generic proxy destination.

The optional `egress.blob_download_policy` is an exact `{path, sha256}` file
reference. For this closed Platform One VM profile, it permits appending only
`https://ib-prod-harbor-storage.s3.us-gov-west-1.amazonaws.com` to that ordered
list, with Registry1 also selected. The policy must use the registry blob policy
schema, exact Registry1 origin, and an explicit repository list containing the selected image repository. The
host verifies its original bytes and agreement with the actual mission build
and image policy before VM dispatch. Neither a storage origin without the policy
nor a policy without its storage origin is valid. This extends no default route.

Select the same policy as a runner material and in the build configuration.
The compiler preserves its bytes and hash while mapping its path into the private
guest inputs. The authenticated route packet carries the original policy bytes
and hash; the remote and guest validators check them before route effects. The
installed runner requires the same policy identity and exact external dependency
set. The storage forward uses fixed `10.0.2.104:443` with the exact selected SNI.
Its readiness observation verifies only the TLS peer, without issuing an unsigned
object request. Real blob access and digest verification remain separate checks.
The storage downloader uses platform TLS roots independently of registry-private
CA selections; those public roots must already exist in the selected boot image.
The storage readiness handshake checks the same explicit OpenSSL root paths,
without environment override fallback. Controlled source tests do not establish
that the real selected guest has those roots; its approved live readiness check
remains required.

An optional build `attestation_size_policy` is a separate exact `{path, sha256}`
selection, not an egress permission. Its file and the image policy must be
explicit runner materials. Before VM dispatch, compilation and installed-runner
admission, their original hashes and exact origin, repository and subject must
agree. The compiler preserves both policies' bytes while mapping their paths.
Missing, changed or differently scoped size profiles fail before runner use;
they do not add a route or change the default acquisition limits.

The actual source build currently requires fresh Registry1 verification and
layer reads. Its original `registry_auth.ca_bundle` must match the selected
external bundle; the compiler stages that exact CA with the private inputs.
Publisher-document access is conditional on the independently selected publisher
mode. Offline publisher packages do not authorize a P1 documentation route.
Missing required routes/trust/credential references remain explicit admission
gaps. Origin selection is never an access grant: actual routing still requires
current original operation scope and the applicable action-time authorization.
Existing Registry1/Grype approval boundaries are not lifted by this source contract.

Local parser, staging and lifecycle tests are component evidence. Actual boot,
nonroot builder qualification, authenticated CI, resource cleanup, repeat runs
and the full recording remain separate acceptance gates.
