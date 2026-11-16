# Fresh owned offline RKE2 foundation

`runtime.foundation.kind: fresh-owned-tcg-v1` selects `OwnedFreshRKE2Foundation`.
It is a separate creation protocol from `retained-tcg-v1`. The recipe is defined
by `fresh_foundation_recipe.schema()` and the standalone
`schemas/fresh-owned-foundation.schema.json`; the enclosing factory config must
also admit that exact branch. A schema-valid recipe is not an action grant.

The default accelerator remains TCG. An explicit matching
[`acceleration` selection](../../../../docs/interfaces/qemu-acceleration.md)
enables KVM for this fresh component and its separate runner VM on an approved
Linux x86_64 host. The existing wire kind is preserved for compatibility.
This does not change retained-TCG ownership or permit an emulation fallback.

The canonical caller supplies its original locked journal, current up/down
mutation, six immutable input identities, installed package and bounded deadline.
Only the original component object can observe its returned results or clean up
its generation. Copying a report does not reconstruct this authority.

## Selected inputs

All inputs must already exist. Select an approved Linux host transport snapshot from
the normal supported certificate workflow, with the exact host, host-key pin,
config/certificate/identity/proxy hashes and agent socket. The closed generated
SSH config syntax is checked before dispatch. A changed original certificate/config remains a changed immutable recipe. For a
long-running original lifecycle, `renew_certificate(context, issued_certificate,
minimum_validity_seconds=1800)` admits an already-issued certificate through the
normal action-time workflow. It never issues a certificate or extends the run's
provisioning grant. Original key, issuer, principals, critical options, extensions,
reserved data, host pin, proxy, agent and route must remain identical. Only the
certificate nonce/serial/validity/signature and its derived config reference may
change. The fixed read-only `true` probe must authenticate successfully before
activation; failed or uncertain probes retain the preceding active generation.
Both pre/post-probe lifetime checks apply; there is no expired-certificate or
changed-identity fallback. Cleanup still needs its original current down context. The bounded original
component allows at most 32 renewal attempts. `current_transport_for` shares a
validated resident generation only with an identical original selected transport
and the same active journal. Bootstrap binds the exact fresh component once to
registry and service transports before any dispatch. Those components retain
all their own operation/package/grant checks and send their unchanged original
recipe; a mismatch fails rather than falling back to the expired certificate.

Select an existing private `/home/<user>/.local/share/devsecops/fresh` parent,
unused local private state directory, cached immutable tool image ID, reviewed
Ubuntu boot-image pin, the five reviewed RKE2 v1.36.5+rke2r1 release assets and
four matching pinned SSH key files. Source keys/images are preserved; copied SSH
keys are explicitly reported as supplied PKI, never newly generated PKI. The API
CA and client credentials are issued by RKE2 inside the newly observed guest.
The worker does not pull an image, download an asset, adopt a disk, or accept an
operator command/cloud-init body.

The fixed reference uses 8 vCPU, 32 GiB guest memory and a new 160 GiB qcow2 root
disk. Admission checks 44 GiB available host memory and 310 GiB free host space.
The tool image must contain the pinned QEMU/cloud-image-utils inventory and the
existing bounded Python/SSH tools. An exclusive run UUID intent precedes the new
generation, input copies, seed, helper, isolated network and runtime creation.

## Readiness and cleanup evidence

`bootstrap_observation(original_outcome)` returns `report`, `actual`, and the
private TLS directory. Fresh readiness requires a newly derived disk, a pristine
RKE2 guest check, pinned local artifact installation, three guest readiness reads,
and three independent mTLS API observations agreeing on the actual node and
namespace UIDs plus CA. The report's `origin` retains the exact generation,
creation operation, disk identity, source pins and cluster fingerprint.

`cleanup_observation(original_outcome)` returns the original audited component
report. Cleanup checks the current root, disk, source assets, image, Docker IDs,
labels and confinement before deleting any owned runtime object. It deletes only
the new generation's runtime/disk/seed/PKI payload and original local tunnel and
credential copies. It independently observes absence and rechecks the preserved
inputs. Ownership/journal/transport audit metadata remains as evidence. Run-created
renewal certificate/config copies are public certificate audit material and are
explicitly preserved; they contain no copied private SSH signing key. Every
renewal intent and result enters the terminal phase artifacts, and the foundation
report carries a hash-linked initial-to-current certificate chain using artifact
basenames and hashes. Verifiers resolve it only through the terminal manifest,
never by opening a path from report JSON. The original recipe and fresh-origin
binding remain unchanged. The live caller must obtain any required action-time
authorization before renewal and supply an already-issued certificate from the
supported normal issuance path. The
component always returns an incomplete canonical outcome: the aggregate must
also prove every other owned service/resource cleaned up before releasing the
factory environment.

Interrupted requests remain uncertain until exact observations reconcile them.
Read-only reconciliation does not start another cleanup. If a down step remains
partially complete, a new scoped cleanup action and an explicit canonical
continuation contract are required; the current aggregate deliberately stops.
Absent delayed creations never establish nonexecution. No state JSON becomes a
new process's authority to delete resources.

## Acceptance limits

The local tests replace external Docker/guest/API boundaries and label their
journal execution `controlled-test`. They exercise source code and actual local
private filesystem/journal guards; they are not a live environment or recording
qualification. Fresh-run acceptance must bind these artifacts to the terminal
manifest and compare separate new generation, disk and kube-system identities.
The implementation plan still requires the real frozen installed runs, second
operator repetitions, full continuous capture and playback review.
