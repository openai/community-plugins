# Owned Linux backend: production composition and diagnostics

`devsecops.factory.owned_linux.OwnedLinuxDriver` is the fixed phase dispatcher
for the `bigbang-live` reference configuration. With the explicit production
selections, it composes owned provisioning, CI/delivery, original native and
runtime evidence, cases, replay and cleanup. The [installed runbook](../demo/two-hour-runbook.md)
shows the complete resident command; [bootstrap inputs](../owned-bootstrap-inputs.md)
defines its selected materials. Controlled implementation tests do not establish
live interoperability, complete recordings or repeatability.

Without those production selections, the legacy diagnostic path described below
makes bounded HTTPS reads and remains incomplete before reserving infrastructure.
That path cannot accept native results, authorize operational mutation or validate
whole-factory cleanup. The private export handler retains checkpoint evidence
without manufacturing a passing canonical result.

Diagnostic publication retains the device/inode captured when this invocation
exclusively creates its terminal file. A failed write or final audit withdraws
only that owned inode; a pre-existing file or a replacement at the same name is
preserved. Partial writes, file-sync failures and close failures remain failures.
Cleanup errors do not replace the original failure or interruption. If withdrawal
itself fails, its note accompanies the original exception and the remaining file
is failed-attempt evidence, not accepted phase output. These checks assume private
output ownership and do not claim an atomic comparison-and-unlink against an
arbitrary concurrent filesystem writer. Phase outcomes and authority are unchanged.

The lifecycle and CLI register this fixed handler as `owned-linux`. Select it
explicitly with `--operation-backend owned-linux` and supply both `--plugin-id`
and `--plugin-version`. The default remains `--operation-backend unavailable`;
`--driver current-host` selects the native handoff protocol separately. Full
production execution uses the resident `codex-cli` driver with its pinned selection
and all production recipes. The diagnostic example below intentionally omits
those selections and returns incomplete preflight without provisioning.

## Invocation and admission

Run the installed wrapper with the approved dependency interpreter and explicit
operator inputs. The selected plugin ID/version must match its actual Codex
installation cache; running from the source checkout fails installed-package
validation before service reads.

```sh
"$DEVSECOPS_PYTHON" -B "$DEVSECOPS_PLUGIN_ROOT/scripts/factory_demo.py" e2e \
  --profile bigbang-live \
  --config "$FACTORY_CONFIG" --lock "$FACTORY_LOCK" \
  --environment "$FACTORY_ENVIRONMENT" \
  --environment-inventory "$FACTORY_INVENTORY" \
  --operation-backend owned-linux \
  --plugin-id "$DEVSECOPS_PLUGIN_ID" \
  --plugin-version "$DEVSECOPS_PLUGIN_VERSION" \
  --output "$FACTORY_RUN"
```

The result remains nonterminal and incomplete (exit 2). The read-only diagnostic
artifact is retained in the phase journal; it cannot establish canonical
success. Resume uses `--resume` in place of `--output` with the same explicit
inputs and backend identity. An incomplete preflight is retained, not silently
rerun. Plugin-selection flags cannot be combined with the default unavailable
backend, and a registered backend cannot accept an injected test driver.

For fixed Python composition, the lifecycle constructs:

```python
from devsecops.factory.owned_linux import OwnedLinuxDriver

driver = OwnedLinuxDriver(
    journal,
    plugin_id="devsecops@devsecops-local",
    plugin_version=selected_installed_version,
)
# The lifecycle supplies its current PhaseContext while holding its journal
# lock; no configuration-selected callback is executed.
outcome = driver.execute(current_phase_context)
```

The journal must already contain the exact canonical scenario and six pinned
inputs, be initialized with driver ID `owned-linux`, and have the selected phase
active. The driver checks the current process, run ID, phase, execution mode,
input references, and retained input bytes. A pending operation requiring
reconciliation blocks diagnostics. These checks reuse the ownership journal;
they do not turn its saved JSON into a build, publication, deployment, or native
execution capability. Preflight does not reserve the environment or create a
mutation intent. Every unsupported phase returns `incomplete` and
`mutation_effect=not_applied` because its handler performs no external effect.
This is not an observation that earlier resources are absent.

The installed package is checked through the existing native package verifier:
exact `CODEX_HOME/plugins/cache/<marketplace>/<plugin>/<version>` location,
manifest hash, complete membership/content, plugin metadata, and the loaded
backend and shared service-inventory modules' fixed paths and included bytes.
A valid manifest proves content integrity, not
release qualification. Operator lock schema and local tool hashes reuse doctor;
versions declared in a lock remain claims until their relevant live checks pass.

## Foundation service declarations

When the frozen configuration selects `runtime.foundation`, preflight first
admits the complete closed [foundation service inventory](owned-rke2-foundation.md#explicit-operator-inputs).
Its environment ID must match the configuration, every configured service must
appear exactly once, Kubernetes must be `retained-foundation`, and Registry1
must be `external-shared`. No other service may be retained. An invalid inventory
stops before local doctor inspection or any service request.

The shared validator projects operator declarations with `ownership: unverified`,
`bootstrap: not-performed` and `cleanup: not-performed`. For `planned-owned` and
`retained-foundation` services, preflight reports `observation_status: deferred`
and `credential_sent: not-sent`: it constructs no HTTP transport, sends no
credential, and creates no transport CA snapshot. It records an explicit
bootstrap or readiness gap. Deferred does not establish that a service is absent,
running, ready, or owned. Declared `external-shared` services use the existing
bounded read handlers below; Registry and Registry1 still have no handler.

Doctor continues to inspect the full original configuration locally, including
credential-reference presence and configured CA-file hashes. Missing local
inputs remain gaps even for deferred services; the frozen configuration and its
reported hash are not rewritten. Missing required services remain gaps as well.
Without `runtime.foundation`, existing preflight diagnostics are unchanged.

This classification does not dispatch the foundation, reserve infrastructure,
grant provisioning authority, or complete preflight. It only separates declared
service prerequisites from deferred observations while backend composition
remains incomplete.

## Closed read routes and evidence meaning

For service requests, only the configured read credential reference is used. Operator credentials
are never sent. Equal read/operator secret-reference names are rejected. Private
CA bytes are frozen into the private diagnostic directory before TLS setup.
HTTPS uses hostname/certificate verification and the shared transport; redirects,
ambient proxies, credential helpers, and service-provided URLs are not followed.

| Service | Fixed GET routes | Retained observation |
| --- | --- | --- |
| GitLab | `/api/v4/projects/<configured numeric ID>` | Exact returned project ID and successful scoped read |
| Kubernetes | `/version`, `/readyz`, `/api/v1/namespaces/<configured name>` | Server version versus lock, literal readiness response, namespace name/UID |
| Argo CD | `/api/version` | Server version versus lock; application access/revision remain unverified |
| Prometheus | `/api/v1/status/buildinfo` | Recognized version; target access, freshness and functional samples remain unverified |
| Registry / Registry1 | None in this slice | Explicit unimplemented diagnostic; no image access/trust claim |

The Prometheus metadata read uses its documented
[build-information endpoint](https://prometheus.io/docs/prometheus/latest/querying/api/#build-information).
No configuration, flags, reload or administrative endpoint is requested.

A returned HTTPS status confirms a TLS-verified destination. A request carrying
the selected read credential is reported separately. Public project and version
responses cannot establish authenticated caller identity, read-role restriction,
operator authorization, or ownership. All those fields remain unverified.
Observed namespace UIDs are not an independently selected cluster fingerprint.
The handler projects only the table's fields, not raw project descriptions,
namespace annotations, service diagnostics, or credentials. Request receipts
retain body hashes/counts, status and safe transport categories; raw response
bodies are not retained.

Kubernetes and GitLab scopes are each limited to eight items, without truncation.
Each service uses at most ten requests, 128,000 bytes per response and 1,280,000
bytes total, with five-second request and twenty-second collection ceilings.
The entire handler shares the earlier of its phase deadline, a 120-second cap,
and the actual remaining operator window. Actual UTC is rechecked before each
service request and at publication; a wall-clock advance cannot extend the
window. Local doctor inspection checks the selected deadline around document
reads and each one-MiB tool/CA hash chunk, propagating expiry rather than
classifying it as a missing tool. Existing doctor callers may omit the optional
deadline. There are no application retries.
Final publication rechecks inputs, package contents, output route and deadline;
a terminal change withdraws `diagnostic.json`.

Outputs live in a new private `owned-linux-<id>` directory inside the existing
run journal. `diagnostic.json` is the `PhaseOutcome` artifact; it binds the run,
phase and six input hashes and reports `readiness=unknown` and
`canonical_success=false`. No D1–D6 assertion is emitted. The caller may freeze
that artifact into the existing phase journal using its normal finish operation.

## Private evidence checkpoint before cleanup

The registered `export` operation copies one validated journal checkpoint while
the lifecycle and environment locks are held. Its membership is the six retained
inputs, the exact event prefix and state projection, and the explicitly typed
artifact references in operation, native-request/result, cleanup and prior-phase
records. Cancelled native requests and accepted incomplete native results remain
included. The exporter does not discover files recursively, follow paths inside
opaque reports, resolve credential references, or read service endpoints.
Unregistered native traces remain outside this membership contract.

The output is private: a new mode-0700 run child contains mode-0600 numbered ZIP
parts and `checkpoint-manifest.json`. Concatenating parts in manifest order
reconstructs a standard uncompressed ZIP. Each part is at most 8,000,000 bytes;
the complete output, including the manifest, is at most 60,000,000 bytes. There
are at most 4,096 reference occurrences, metadata documents are bounded to
1,000,000 bytes, and existing journal event/file limits also apply. Oversized or
changed evidence fails the export rather than being truncated. The lifecycle
then freezes the returned parts and manifest using its ordinary artifact limits.

The manifest identifies the captured run, six input hashes, mode, journal
sequence/head, state hash, exact archive membership and part hashes. It excludes
itself and all later export-completion, cleanup, final-result and video-review
records. It is a pre-cleanup checkpoint, not a terminal archive or public release
bundle; no extraction/import or authority-restoration interface is introduced.

Source and destination directories are held by descriptors, with no-follow
component walks, exact file length/hash checks and before/after route audits.
Symlinks, multiply linked source files, changed routes and changed bytes are
rejected. Output files are created exclusively through the held private output
descriptor. On interruption, deadline expiry, caller package-check failure or a
final audit failure, partial parts remain as failed-attempt evidence and only
the invocation's still-owned manifest inode is withdrawn. The original manifest
descriptor stays open through this check so inode reuse cannot remove a foreign
replacement. A concurrent move can retain partial bytes at the held directory;
it cannot redirect writes through a replacement path or yield successful export
after the move is detected. Existing trusted-process and journal-lock assumptions
apply; these checks do not claim atomic topology or arbitrary hostile-host safety.

The driver retains the manifest guard through its final phase and installed
package checks. The existing synchronous lifecycle alarm bounds the export
phase (300 seconds in the canonical scenario); per-chunk checks also observe the
driver's narrower 120-second cooperative budget. Export remains allowed after
the operator authorization window expires, as required by the existing tail.

A successful export returns `completed`, `owned-linux-evidence-exported`, no
assertions, and `mutation_effect=not_applied`. This establishes only the copied
checkpoint. The original abort remains unchanged, cleanup remains owed, and no
D1–D6, runtime, native, release, video or terminal success is inferred. Complete
cleanup and finalization are still required before any final lifecycle result.

## Remaining integration dependencies

1. **Bootstrap and ownership:** actual Linux/guest capacity and independently
   selected cluster fingerprint; complete owned-resource inventory; exact TCG or
   other reference foundation composition; DNS/PKI, registry/GitLab/runner
   bootstrap; pinned RKE2, Big Bang and Flux installation; storage, CNI, mesh,
   admission, identity and telemetry qualification. No provisioning commands are
   accepted by this diagnostic module.
2. **CI and delivery:** independently verified service principals/grants;
   same-execution GitLab job/source/runner binding to direct base qualification,
   build, scan, signing, publication and proposal capabilities; bounded remote
   Git delivery and immutable readback; Argo ownership and revision observation;
   separate unchanged Iron Bank NGINX acquisition and runtime proof. Local Git
   preparation alone cannot perform delivery.
3. **Native recovery:** fixed native workflow handoffs; retention of the actual
   model correction and its direct execution origin through deterministic
   validation, delivery and recovery; authenticated collector/probe provenance,
   exact positive/negative traffic tests and fresh functional samples.
4. **Remaining phases and cleanup:** actual fault injection, platform dependency
   failure/repair, disconnected controls and reset/replay; exact
   reconciliation of ambiguous effects; deletion/revocation observations for
   every owned compute, volume, network, DNS, project, runner and disposable
   credential. The TCG runtime's absence receipt is not complete factory cleanup.

Focused tests exercise real localhost TLS/private CA, scoped response mismatch,
denial, redirects, malformed evidence, secret canaries, package/input changes,
deadline boundaries, complete inventory admission, deferred transport, loaded
inventory-module substitution and unimplemented dispatch. Controlled observations do not
qualify a customer service, a Linux lab, the package release, or a factory run.
