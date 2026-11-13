# Canonical runner identity and cleanup composition

Canonical production selects the pinned `owned-bootstrap-v2` recipe before any
component effect. The v1 container runner remains available for explicit
component diagnostics; it cannot enter the production coordinator.

The v2 bootstrap owns a separate disposable Linux/amd64 guest. Its original
VM, route and service objects are registered with cleanup before provisioning.
The fixed `gitlab-runner` user has UID/GID 10001 and home
`/home/gitlab-runner`. Runtime source does not use root SSH, sudo, system units,
user creation or sysctl changes. The outer VM container retains its existing
network, capability and no-new-privileges constraints. Supplied guest-image
prerequisites and the current installed package must pass independent guest
observations; a recipe is not proof that those prerequisites work.

The direct API runner ticket is retained by `OwnedRunnerService`. Installation
consumes the original `RunnerInstallPlan`, route-owned CA inodes and hashes,
and the current package identity. Tokens, secret environment values and the
supervisor nonce are sent only through the private transport, never public
phase artifacts. A fresh guest observation issues a nonserializable
`RunnerExecutionOrigin`. Source publication and deployment MR workflows require
that original origin and compare the actual API job runner ID and manager
system ID. The MR gate also requires the original project's assignment and
Linux/amd64 manager. Expired, stopped, revoked or uncertain current runner state
does not grant a new job or merge. Historical job/runner evidence remains
auditable without extending a mutation window.

The admitted publisher policy determines the exact external dependency set:
Registry1 is required for source-build qualification, and the P1 current-document
mode additionally requires P1docs. Operator-supplied publisher packages do not
add P1docs. Credential references must name explicitly staged private variables;
the external CA must match the staged build configuration. This completeness
check does not confer permission to acquire from either external service.
Existing action-time restrictions and the scanner acquisition hold still apply.

The complete inventory contract is `owned-runner-vm-v1`, with eleven original
scopes. Cleanup orders Kubernetes, writer, reader, GitLab registration/projects
and token, runner service, runner routes, runner VM, service links, services,
registry, then foundation. Each component retains original up/down observations;
serialized IDs or a missing file cannot substitute for original ownership.

Loss of guest control leaves guest-local effects unresolved. A separate read-only
reconciliation may prove that those residuals are contained in the original VM,
that its API registration is revoked, and that host relay/API/parent effects are
known. Only that original proof can produce the restricted partial marker
`owned-guest-contained-residual-parent-disposal-only`. Ordinary continuation
cannot consume it. An explicit resident operator selection records
`cleanup_mode=dispose-owned-runner-guest` on a fresh, unexpired, chained down
operation. It removes the original host relays, disposes the identified VM, and
requires an opaque guest-kernel cessation proof before marking guest process and
private-file resources absent. It never claims that an unknown remote CI result
has been reconciled. All prior unknown receipts remain retained.

If the disposal reply is lost, the exact resident disposal intent and read-only
parent observation can issue the same scoped cessation proof without deleting
again. If an original pre-dispatch host read failed, an affirmative resident
`parent_dispatched=False` observation preserves the guest residual and permits
only a new explicit operation after fresh host proof. Missing intent or receipts
never establish nonexecution. Historical closure includes the original disposal
intent, parent-absence bytes and prior aggregate receipts.

These source contracts and controlled tests are prerequisites for a live demo.
They are not live VM, CI, repeatability or recording acceptance.
