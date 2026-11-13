# Separate runner VM routes

This implementation prepares fixed production routes for a disposable runner VM.
It does not authorize provisioning, external acquisition, Grype use, or a live demo.
Controlled local tests are component evidence, not an end-to-end qualification.

`OwnedRunnerVMRoutes(driver, vm=..., registry=..., services=...)` requires the
original same-journal owners. It registers with the original VM before inventory
sealing. `up`, `down`, `reconcile`, `cleanup_observation`, and `evidence_artifacts`
follow the aggregate component contract. Serialized receipts cannot construct a
route origin. `origin`/`require_ready` perform a fresh observation; the original
opaque origin also requires the same current operation and a maximum 30-second
resident age. Failed refreshes invalidate earlier readiness.

The VM's fixed guest forwards retain `restrict=on`: GitLab 10.0.2.100:8444 and
registry 10.0.2.101:8443. Explicit recipe selections may additionally admit only
Registry1 10.0.2.102:443 and P1 documentation 10.0.2.103:443. External routes require
one selected pinned public CA bundle, observed from the original remote VM asset;
a remote filename is never read as a desktop file. Bootstrap preflight checks
actual build/publisher dependencies and credential references before route effects.
A selected recipe alone does not grant permission. Registry1 remains required by
the real signed base-acquisition path; no saved JSON or offline image layout
reconstructs that authority. Grype acquisition retains its separate approval boundary.

An optional original pinned registry blob policy additionally permits the exact
Platform One storage origin on `10.0.2.104:443`, role `registry1-storage`. Its
policy bytes, hash, registry and repository must match the selected build and
image before VM dispatch, route setup and runner installation. The guest packet
retains those bytes and hash; a missing or changed policy closes admission.
The saved relay configuration also retains the original policy hash, so a later
request cannot replace its selection while reusing the original relay.
This route is absent from profiles without that explicit selection. Host SNI and
public-IP checks remain exact. Storage readiness is TLS-peer-only and does not
download an object or imply that a presigned URL works. Like the downloader, it
uses existing OpenSSL system root paths explicitly, ignoring staged CA and
`SSL_CERT_FILE`/`SSL_CERT_DIR` overrides. Missing public roots fail readiness;
no trust roots are acquired or installed. Its receipt records that trust basis,
not verification by the staged registry CA. Setup and teardown use
the existing original VM ownership and cleanup contracts.

Each host listener binds only the original VM bridge gateway and accepts only its
original container peer. TLS ClientHello SNI must exactly match the role's sole
origin before any upstream connection. External DNS is bounded to the selected
origin, requires public IPv4 addresses, and must retain the original selected
answer set; there is no alternate endpoint, redirect, proxy, or system-trust
fallback. TLS terminates at the selected service. Non-storage guest verification
uses fixed bounded GET paths with the exact origin's CA, SNI and Host header.

Every new connection receives a fresh full original host/VM/network/source audit.
An already-admitted stream may reuse only a daemon-local observation less than
one second old measured from the start of that audit. Acquisition of an audit is
bounded by ten seconds; a failed/late refresh clears the cached observation.
Immutable local file identities and expiry are checked before every effect.
Caches never cross daemon, configuration, or VM generation. Remote operation
budgets start before validation and are rechecked after slow reads and durable
intents immediately before effects. Cleanup retains current original identity
requirements while allowing restoration after the original provisioning expiry.

The nonroot user is `gitlab-runner`, UID/GID 10001, with no runtime sudo or root SSH.
First-boot seed owns `/etc/hosts`; the helper only verifies its selected mappings.
Route-owned trust lives under
`/home/gitlab-runner/.local/share/devsecops/<run UUID hex>/inputs/service-trust`.
`route_trust` contains exact original file path/hash/size/inode/uid/gid/mode facts,
a fixed environment, and the original empty `SSL_CERT_DIR` directory. It includes
owned GitLab/registry CAs, optional external CA aliases, and a deterministic CA
bundle. The eight proxy environment variables are empty. The runner service
reuses these files without replacing or removing them.

Normal cleanup removes only original trust files and original host relay/mapping
objects. Original evidence ledgers remain until the VM owner's whole-generation
cleanup. A never-dispatched original resident ledger can close an unattempted
scope without inspecting a nonexistent guest. Unknown writes are never inferred
not to have run from missing receipts.

`prepare_disposal(context, prior_operation_id)` is read-only host reconciliation.
It emits `host_disposal_eligible` only after positive original host identity and
no unknown host effects; guest uncertainty remains explicit. Only a fresh down
with `cleanup_mode=dispose-owned-runner-guest` and the matching
`continues_operation` may call `prepare_host_disposal`. Registration revocation
and parent-disposal authority belong to the aggregate and original VM owner.
Host cleanup does not close guest files or CI effects. Only a typed original
same-VM `GuestKernelCessation` may close guest-private resources; its artifacts
and original host-cleanup artifacts are retained. A read-only reconciliation of
an uncertain parent removal must forward the exact `reconciliation_operation`.
The historical outcome records the observed applied removal separately from the
current read-only observation. Historical export rehashes original evidence and
never refreshes expired readiness or invokes a live endpoint.
