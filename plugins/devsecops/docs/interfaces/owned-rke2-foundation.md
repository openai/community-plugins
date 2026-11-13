# Retained RKE2 foundation component

`factory.owned_foundation.OwnedRKE2Foundation` connects the existing retained TCG
guest driver to a current factory journal. It can start an already provisioned
private RKE2 disk, observe its identity and readiness, reconcile an uncertain
operation, and remove the exact owned runtime. It does not install a fresh
cluster or factory services. Every returned canonical phase outcome remains
`incomplete`; it cannot release the environment reservation.

The separate fresh-generation component supports an explicit
[KVM acceleration selection](qemu-acceleration.md). That option does not apply
to the retained TCG ownership and adoption contract described here.

This is an internal callable component. The registered `owned-linux` CLI handler
still performs read-only readiness diagnostics; it does not dispatch this
component. There is no additional CLI or alternate acceptance scenario.

## Calling contract

```python
foundation = OwnedRKE2Foundation(
    journal, plugin_id="devsecops@your-marketplace", plugin_version=installed_version,
)
# The existing lifecycle owns both locks and records its mutation intent first.
# context is the exact current canonical provision/up or cleanup/down context.
outcome = foundation.up(context)
outcome = foundation.down(context)
# A reopened or uncertain operation is observed, never blindly retried.
outcome = foundation.reconcile(context, original_operation_id)
```

These are separate calls at their corresponding lifecycle boundaries, not a
sequence to run outside the harness. `RunJournal.current_mutation` checks the
held environment lock, active phase/action, current PID and owned operation.
Reconciliation may observe an existing unresolved operation from a reopened
journal. It does not resolve that operation or authorize another mutation.
The caller retains the returned artifacts and records the observed effect
through the existing journal API. A lost reply or exception leaves the intent
pending for independent reconciliation. No supplied JSON creates an operation
identity, native origin or image capability.

The component validates the actual installed Codex package, its complete
manifest, the loaded implementation and all six frozen lifecycle inputs. It
accepts only the existing Linux amd64 RKE2 `v1.36.5+rke2r1`/Canal reference
recipe. The lock uses `distribution: "rke2"` and the same exact value for
`distribution_version` and `kubernetes_version`. This is recipe admission, not
Big Bang compatibility qualification. All existing QEMU resource limits,
restricted networking, Docker isolation and pinned SSH behavior remain intact.

## Explicit operator inputs

The existing factory configuration optionally accepts `runtime.foundation`:

```json
{
  "kind": "retained-tcg-v1",
  "host": "approved-linux-host",
  "remote_lab": "/home/operator/.local/share/devsecops/labs/approved-lab",
  "private_state_directory": "/approved/private/foundation-state",
  "tool_image": "sha256:<exact local tooling image ID>",
  "provision_run_id": "<original provision run>",
  "original_ca_sha256": "<original guest CA SHA-256>",
  "source_receipt": {
    "path": "/approved/receipts/original-rke2-screen.json",
    "sha256": "<exact receipt SHA-256>"
  },
  "transport": {
    "config": {"path": "/approved/transport/config", "sha256": "<SHA-256>"},
    "known_hosts": {"path": "/approved/transport/known_hosts", "sha256": "<SHA-256>"},
    "certificate": {"path": "/approved/transport/certificate", "sha256": "<SHA-256>"},
    "identity": {"path": "/approved/transport/identity", "sha256": "<SHA-256>"},
    "proxy": {"path": "/approved/transport/proxy", "sha256": "<SHA-256>"},
    "identity_agent": "/approved/agent.sock",
    "host_key_sha256": "<independently verified SSH host key SHA-256>",
    "verification_basis": "operator-verified",
    "agent_protocol": "native"
  },
  "retained_dependencies": "preserve"
}
```

Placeholders are explanatory and are rejected as real inputs. Host/path names
are operator-selected; no customer's hostname or credential value is built in.
The original receipt must be the actual sanitized RKE2 screen report, selected
and pinned independently by the operator. Its bytes, original run, remote path,
tooling image, guest geometry, CA, RKE2 artifact pins, three ready observations
and completed original runtime cleanup must agree. Historical receipt content
is labeled `operator-selected-historical-provenance`; it does not establish
current readiness or independently authenticate its author. Current observations
come from the direct fixed driver call.

A retained lab is already bound to its original `TCGSpec` and driver source.
A new factory run or changed package cannot silently adopt that owner. Reuse
requires a separately reviewed transition while the runtime is inactive,
preserving the old binding/state and original disk/CA before installing the
exact new specification and source binding. This component does not perform
that transition. A historical receipt, even with the selected hash, cannot
override an existing owner or establish current authority.

The environment input must have exactly `schema_version: 1`, `environment_id`
and `services`. Every configured service appears exactly once:

```json
{
  "schema_version": 1,
  "environment_id": "approved-environment",
  "services": [
    {"id": "kubernetes", "disposition": "retained-foundation"},
    {"id": "registry1", "disposition": "external-shared"},
    {"id": "gitlab", "disposition": "planned-owned"},
    {"id": "registry", "disposition": "planned-owned"},
    {"id": "argocd", "disposition": "planned-owned"},
    {"id": "prometheus", "disposition": "planned-owned"}
  ]
}
```

Kubernetes must identify the retained foundation; Registry1 must remain external
and shared. Other services may be explicitly external/shared or planned owned.
The declaration itself grants no ownership or write permission. Service origins
and credentials remain in the existing frozen configuration. Services retain
`ownership: unverified`, `bootstrap: not-performed` and `cleanup: not-performed`.

The shared `service_inventory` validator admits this same complete declaration
for both the foundation component and registered `owned-linux` preflight. Both
callers bind its loaded fixed path and bytes to the installed package manifest.
Preflight defers transport for planned-owned and retained-foundation services,
keeps external-service diagnostics bounded, and preserves doctor's local
credential-presence and CA-file checks. This inventory integration neither
dispatches this foundation component nor completes any lifecycle phase.

## Existing retained-lab ownership

A new factory run changes its run, package and input identities. An existing
retained binding is never adopted from a historical readiness receipt. Dispatch
requires an exact matching binding or a separately authorized and reviewed
inactive-owner transition to the new factory identity. That transition must
verify current runtime/tunnel absence and preserve the selected disk, original
CA and transport scope. This component implements no transition adapter; the
existing binding checks must remain in force until that handoff is qualified.

## Observations, interruption and cleanup

Each attempt creates private evidence beneath the run directory. The summary
binds the operation, package, inputs, original receipt, selected transport and
actual TCG receipt. Original receipt/transport snapshots remain private; they
must not be presented as secret-redacted publication artifacts. The summary
contains references and projected facts, without reading private agent keys.

The inventory distinguishes observed container/network IDs and tunnel identity
from the retained disk's device/inode, seed, guest credentials, tooling image and
local private credential/state directory. The retained items are explicitly
preserved. Full cluster fingerprint observations bind the original CA and
namespace/node UIDs. A restart cannot substitute a new cluster for durability.

`down` uses the driver's exact ownership checks and immediate post-removal
observations. It may report `runtime_cleanup: observed-absent`; it still reports
`complete_cleanup: false`. A retained disk or credential, an uncovered service,
or an unresolved mutation cannot be converted into complete factory cleanup.
The component's cleanup validator always rejects environment release.

Monotonic budgets bound each operation; actual UTC also gates provisioning before
and after dispatch/publication. Cleanup and read-only reconciliation remain
possible after the provisioning window, with the already recorded cleanup
authorization. Expired, changed or replaced terminal evidence is withdrawn;
the original driver receipt and unresolved operation remain available.

## Work still required for the canonical factory

The retained recipe has no general network route and only SSH/API forwarding.
Independent GitLab, registry, runner, DNS/PKI and identity access therefore need
an explicit qualified connectivity recipe. Fresh seed/offline RKE2 installation
must be extracted from the existing engineering screen into the same journal;
calling the screen CLI would create a separate run and does not satisfy this
contract. Big Bang/Flux source and values, storage/CNI/mesh/identity/telemetry
checks, Argo ownership, real CI delivery, credential revocation and complete
owned-resource removal remain separate implementation and live gates.

Preflight now distinguishes declared external prerequisites from planned-owned
and retained-foundation services. Provisioning through the registered handler
still requires the remaining ownership, readiness, connectivity and lifecycle
composition work. Neither a declaration, fixture success nor a successful
metadata read may close those gates.
