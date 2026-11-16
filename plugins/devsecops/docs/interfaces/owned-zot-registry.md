# Owned Zot registry component

This is a callable component for an operator-owned Linux Docker host. It creates
an isolated Zot service, executes a fixed storage qualification, observes exact
resources, and removes its containers/network. It is not registered as a complete
factory backend. Every returned `PhaseOutcome` is `incomplete`; the whole phase's
mutation effect remains `unknown`, even when storage qualification passes.
It never issues mission, publisher, deployment or recovery authority.

The implemented topology uses one new **internal** Docker bridge, a Zot container
and a short-lived qualification container. It publishes no host ports. It does
not change the retained RKE2 recipe, host DNS, host routes, firewall, Docker daemon
or packages. Guest/runner connectivity is a separate fresh-lab integration.

## Required pinned inputs

Select a private JSON recipe through `service_recipe.validate`. All objects are
closed: unknown fields, tags, arbitrary commands, outside repositories and generic
configuration templates are rejected. Required fields are:

| Field | Contract |
| --- | --- |
| `schema_version`, `kind`, `version` | `1`, `owned-zot-v1`, `2.1.22` |
| `environment` | Exact canonical environment identifier |
| `origin` | `https://registry.<environment>.test:8443` |
| `repository` | `devsecops/<environment>`; the separate probe repository is its `/qualification` child |
| `remote_directory` | `/home/<operator>/.local/share/devsecops/registries/<environment>`; its private parent must already exist |
| `private_state_directory` | Absolute nonsymlink local owner/audit directory, separate from the installed package, run journal and selected inputs |
| `image` | Official `ghcr.io/project-zot/zot-minimal@sha256:…` reference, exact Docker image/config ID and selected fixed Zot entry point |
| `tool_image` | Already cached Linux amd64 image ID containing `/usr/bin/python3` version 3.11 or newer |
| `transport` | Existing pinned SSH snapshot fields: config, known_hosts, certificate, identity, proxy, identity_agent, host_key_sha256, verification_basis and agent_protocol |
| `materials` | Separately selected `{path, sha256}` pairs for CA, certificate, private key and htpasswd file; no certificate/password generation is performed |
| `credentials` | Distinct operator and reader `{username, password_env}` entries |

The canonical service configuration must independently select the same origin,
CA path, two repositories and Basic-auth credential references. The recipe is
also frozen as an artifact in the **current mutation intent** before dispatch.
It cannot be substituted by loading a previous output report. No schema or
registration changes to the existing lifecycle are required for this component.

The official v2.1.22 Linux amd64 manifest metadata was independently read and
hashed during implementation:

- Platform manifest: `sha256:27729292c45f1dc0a323b69292d15cc13a5ca4b8bf05f81106e7f89a2b1abd08`.
- Config/image ID: `sha256:6630613c2b7494ad2ea4736224ad175c065966978c61a52b9fa395b8cee5deaf`.

The exact 3,379-byte configuration was acquired on 2026-10-29 at 04:03:47 UTC
from the official GHCR blob route through its observed GitHub CDN redirect.
No registry authorization was forwarded to the CDN. The configuration reports
Linux amd64, entry point `/usr/local/bin/zot-linux-amd64-minimal`, default command
`serve /etc/zot/config.json`, user `0`, and working directory `/`. The closed
recipe accepts only that entry point; repository Dockerfile paths are not proof
of the released OCI image's contents.

The fixed create operation explicitly overrides the image's root user with the
non-root operator's numeric UID:GID and overrides the command with
`serve /config/config.json`. Provisioning rejects a real or effective UID of zero
before private state or Docker writes. Its generated configuration uses port
8443, operator-owned writable `/data`, and read-only mounted configuration and
credentials. This is compatible with the selected metadata fields, but metadata
alone does not prove executable permissions, libraries, runtime file access or
successful non-root startup. Those require acquired layers and real execution.

The retained metadata is not image-layer acquisition or execution evidence. Local Docker
inspection must match the selected image ID, amd64 architecture, Linux OS,
repository digest and entry point before creating any container. An offline
import that omits `RepoDigests` currently does not satisfy this admission; the
component does not invent a repository association from a saved report.

## Direct invocation

Use the verified installed package's `OwnedOCIRegistry`, with the actual
`RunJournal` and `PhaseContext`. Hold the lifecycle/environment locks, begin the
canonical `up` or `down` mutation and include the recipe in `intent_artifacts`.
The coordinator constructs the context from the six frozen inputs; there is no
JSON-to-context or JSON-to-capability shortcut.

```python
component = OwnedOCIRegistry(
    journal,
    recipe_path=approved_recipe_path,
    recipe_sha256=approved_recipe_sha256,
    plugin_id="devsecops@devsecops-local",
    plugin_version=installed_version,
)
# Inside the existing coordinator's locks and active provision mutation:
outcome = component.up(context)
# For that exact unresolved operation; observes without retrying any upload:
observation = component.reconcile(context, operation_id)
# Inside the separately journaled cleanup mutation:
cleanup = component.down(cleanup_context)
```

The component does not finish/reconcile the caller's journal mutation or release
the environment. The coordinator must assess the remaining factory components;
the `validate_cleanup` method deliberately cannot establish whole-factory cleanup.

## What is actually executed

The existing bounded SSH transport and process runner execute only the packaged
fixed Docker driver. The exact captured support, recipe, driver and probe module
bytes must match the admitted package hashes before transport; a later package
audit cannot substitute for this check. It records each create/start/stop/remove intent before
dispatch, uses `--pull=never`, exact resource IDs, ownership labels and retained
private state. Containers run as the operator UID with dropped capabilities,
no-new-privileges, read-only root filesystems, no host ports/devices, one CPU,
1 GiB memory and 128 processes. Their inherited health checks are disabled;
the component performs actual CA/hostname-verified HTTPS checks instead.

Zot receives a generated configuration with only the two exact repositories.
The operator has `read/create/update`; the separate reader has `read`. Anonymous
and default policies are empty. There is no admin/delete grant, registry mirror,
extension or garbage-collection service. TLS material and htpasswd are mounted
read-only. Data has its own owned writable directory.

The qualification container receives only a verified stdlib Python zip, CA,
scoped credentials and a private output directory. Python admission happens
before requests; a missing or older interpreter cannot produce a pass. The zip
contains the existing HTTP, OCI and staging adapters plus the fixed entry point,
not the whole factory runtime or a dependency installer. Credentials are supplied
through a private file, never Docker argv/environment or exported receipts.
The mounted probe CA is checked against its selected pin, and the exact private
settings bytes have a retained SHA-256 pin checked before Docker calls and final
publication. Cleanup uses that pin without requiring secret values in its request.
An older retained state with probe settings but no settings pin fails closed;
it requires reviewed reconciliation, not automatic adoption of current bytes.

The probe checks native referrers before writing, then uploads two small blobs,
a subject manifest and a referring artifact. It independently reads every byte
with the reader identity and verifies exact referrer inclusion. It confirms an
anonymous read of existing content is denied and a reader upload attempt is
denied. Every attempted mutation has its own retained intent. There are no tags,
deletions, promotion, signing or real mission artifacts in this storage probe.
The probe requires an empty private output directory and claims it exclusively
before any requests. Prior success, failure and interrupted-attempt evidence is
preserved; terminal withdrawal removes only the current invocation's own file.
The outer component receipt uses the same creation-identity check, preserving
an exclusive-write conflict or a different file substituted at that name.

Only initial connection failures may receive bounded readiness retries. Uploads
are never retried. A lost acknowledgement, mismatched readback or uncertain
discovery remains `unknown` with the attempted effects retained. Bootstrap
running, storage qualification and cleanup are reported independently. UTC
operator expiry and monotonic deadlines gate service calls and publication;
cleanup/reconciliation remain available after the provisioning window.

## Cleanup and current limits

`reconcile` performs Docker inspection only. It does not accept a previously
saved qualification report as fresh storage readiness. A repeated `up` on an
attempted operation is rejected; use reconciliation to discover its effects.
`down` rechecks exact IDs, ownership, command, mounts, network and isolation before
stopping/removing containers and the empty owned network. Unexpected members or
changed identities prevent deletion. There is no name-only adoption or pruning.
An unresolved Docker create whose target is still absent keeps its original
intent and returns unknown cleanup without further mutations. Docker may finish
that request later. Cleanup can proceed after observing the exact owned creation;
an absent observation alone never proves the dispatched create was cancelled.

The first component intentionally **retains private data, credentials, owner
state and evidence** and records their paths/identities. Cached image prerequisites
are also preserved. Runtime absence is not complete cleanup or credential
revocation. A later reviewed private-data removal/revocation path must close that
gate. The component does not generate credentials or modify existing accounts.

Tests cover actual loopback TLS upload/readback and authorization failures,
actual journals/package bytes, isolated stdlib imports, deadline withdrawal and
controlled Docker ownership/interruption models. These tests do not qualify the
actual Zot image, Docker runtime or authorized lab. GitLab/runner provisioning,
guest connectivity, rootless builder qualification and the canonical mission
pipeline remain separate gates.

Official references: [Zot released images](https://zotregistry.dev/v2.1.22/general/releases/),
[Zot authentication/authorization](https://zotregistry.dev/v2.1.22/articles/authn-authz/),
[Zot native referrers API](https://zotregistry.dev/v2.1.22/developer-guide/api-reference/),
[OCI Distribution Specification](https://github.com/opencontainers/distribution-spec/blob/v1.1.1/spec.md).
