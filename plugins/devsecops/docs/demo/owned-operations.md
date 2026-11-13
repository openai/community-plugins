# Owned production operation contracts

The canonical owned Linux backend retains one `OwnedOperationCoordinator` with
the original journal, bootstrap, runtime and native session. It has no report
import, callback dispatcher or JSON restoration path. The 33-phase scenario
remains unchanged. Production completion also requires the separately composed
bootstrap, policy delivery, runtime input, case and cleanup coordinators.

The immutable operations recipe selects the writer policy, runtime recipe,
mandatory publication and deployment pipeline job names, bounded polling and
native tool/profile/schema inputs. Runtime project, runner, job, commit and
resource identities come from direct observations. The nested writer/runtime
pins must match separately frozen `owned_git_writer` and `owned_runtime_recipe`
inputs. The eight mission component templates remain frozen inputs.

`gitlab-owned-api-v1.yml` is a distinct CI recipe. Its API-only workflow avoids
running an extra publication for the bootstrap seed push. The existing MR
recipes retain their behavior. The runner publishes the exact selected mission
source and emits one bounded selector artifact. The host authenticates that
artifact from the original successful job and freshly verifies the registry
subject, referrers, signed evidence and independent signing policy before it
prepares an image-values proposal.

Initial delivery prepares a native Git commit and transport, observes the actual
remote base, uses the separate owned Developer writer for a create-only branch,
and continues through the owned MR workflow. A completed watched-branch receipt
must precede runtime preparation. The owned Reporter reader uses separate
`read_api`/`read_repository` authority; its original opaque origin selects the
API and Git credentials. Owned destinations never fall back to ambient
credentials, the writer or the administrator.

Owned checkouts reconstruct bounded ordinary Git objects from authenticated
project, commit, tree and blob reads. Native Git must reproduce every original
object identity. Unsupported commit headers, symlinks, submodules, moved main
branches or incomplete history remain explicit failures. Source selection uses
the materialized delivered commit, rather than the old base repository or an
unverified neighboring working tree.

Publication authority keeps its existing short lifetime. Later runtime phases
use the separate `RuntimeImageProvenance`: original job and artifact identities
and issuance timing are reauthenticated; the exact current registry bytes and
signature are reread; current policy, signing trust, database freshness and
exceptions are reevaluated over the exact signed scanner evidence. This type
grants runtime identity only. It cannot authorize publication, promotion, Git
mutation or recreate expired build/signing handles. The retained raw OCI
subject manifest supplies a stable image mapping hash across observations.

Every renewed GitLab transport shares the original irreversible uncertain-write
latch. An unknown write on any clone blocks writes and mutation renewal from
all clones, including older handles. A permanently read-only clone remains
available for reconciliation and cannot upgrade itself. Credentials and TLS
trust are frozen; renewal does not reread environment secrets.

Local qualification uses controlled API/registry/signature boundaries, real
policy evaluation and native local Git reconstruction. Those tests do not
establish actual GitLab provisioning, image publication, lab recovery,
repeatable E2E runs or recording acceptance. Those gates require the live
authorized canonical run, complete receipts, continuous capture and playback.

Canonical build, fault, correction delivery and reset results register the exact original
public evidence closure alongside runtime inputs. CI polling and fresh historical
readbacks are exported in one immutable `original-ci-evidence-bundle`; each entry
contains the original UTF-8 bytes, relative label, original path, SHA-256 and byte
count. The bundle preserves failed and waiting observations and does not issue
execution authority. Unregistered neighboring files are excluded.

Delivery evidence includes the original source/render/commit/pack, exact base
readback, native request body/report, durable claim, receive status, independent
branch readbacks and exact original MR event ledger. Materialized checkout evidence
contains the object inventory plus authenticated API read receipts. Private
credentials and the whole `.git` directory are excluded. Every original file is
rechecked through its retained process object before inclusion; phase results
remain bounded to 256 files, 16 MB per file and 64 MB total.
