# Bounded Git base possession observation

`devsecops.factory.git_base_possession.verify_git_base_possession` implements a
read-only boundary between local Git transport preparation and a future remote
writer. It takes the current `PreparedGitTransport`, its exact five scope
arguments, the existing destination configuration path and SHA-256, a new
absolute output directory, and a timeout of at most 120 seconds. It does not
accept a deserialized transport plan or destination report.

The verifier selects one canonical HTTPS origin, numeric GitLab project,
repository path, base ref/commit and prospective target ref. It independently
reads the selected project, upload-pack advertisement and commit metadata before
and after the complete tree/blob walk. Both snapshots must agree. API and Git
credentials are selected separately, frozen in memory for the whole operation,
and removed from the clients afterward. Sending a credential does not establish
its principal or effective server role; those remain unverified.

Fixed GET routes are:

| Route | Purpose |
| --- | --- |
| `/api/v4/projects/SELECTED_ID` | Compare numeric ID and exact selected repository URL. |
| `SELECTED_REPOSITORY/info/refs?service=git-upload-pack` | Observe exact base ref and prospective target ref. |
| `/api/v4/projects/SELECTED_ID/repository/commits/BASE_OID` | Compare commit ID and exact locally pinned parent IDs. |
| `/api/v4/projects/SELECTED_ID/repository/tree` | Walk selected commit/path with explicit nonrecursive, bounded pages. |
| `/api/v4/projects/SELECTED_ID/repository/blobs/BLOB_OID/raw` | Read exact raw blob bytes at a locally expected object identity. |

Tree requests use the selected full commit ID as `ref`; paths are derived from
previously admitted tree entries. Pagination uses local page increments, never
response URLs. Every returned entry has a checked name, path, type, mode and
object ID. Duplicate names, unsupported modes/gitlinks, unexpected objects and
incomplete trees fail. UTF-8 names, executable modes, symbolic-link blob contents
and empty blobs are supported. Names that cannot round-trip through UTF-8, or
that contain NUL, CR, LF, TAB or a slash, fail.

The selected pinned native Git executable reconstructs every tree with
`mktree -z --missing`; `cat-file` returns the native tree bytes. Each tree and
blob must match the direct transport closure's Git object ID, kind, length and
SHA-256. The complete object set must equal the locally pinned base closure
excluding its commit. Native Git uses private scratch, disabled network protocols,
configuration, hooks and credential helpers; it does not modify the deployment
repository. Scratch is removed through its held parent descriptor before terminal
publication. No custom Git object or pack encoder is used.

The commit endpoint does not supply raw commit bytes. The result separately
records authenticated API acknowledgement of the selected commit ID/parents and
its **locally** pinned raw identity. It does not claim that the remote commit's
raw bytes were retrieved or independently rehashed. GitLab documents repository
tree and raw-blob reads in the [Repositories API](https://docs.gitlab.com/api/repositories/)
and commit metadata in the [Commits API](https://docs.gitlab.com/api/commits/).

One shared deadline and at most 64 GETs cover both clients. Responses are limited
to 2 MiB each and 32 MiB in aggregate. For an unframed body, the fixed transport
may read one additional byte to detect excess. These response-body allowances do
not establish an encrypted-wire-byte guarantee. The existing closure limits remain 256 objects,
8 MiB raw bytes, 2 MiB per object, depth 16 and 20,000 tree entries. Request limits
can reject a closure before its object limit. There are no retries or expanded
limits for larger repositories.

Outputs include the pinned destination configuration, optional selected CA,
concatenated verified tree/blob bytes with per-object offsets, and the sealed
`git-base-possession.json`. Unselected project fields, commit descriptions, raw
credentials and remote diagnostics are not retained. The descriptor-based
consumer guard protects all local source/preparation/transport roots, audits
retained artifacts, and withdraws the terminal receipt if its final audit fails.

A successful call returns `(report, ObservedGitBase)`; an incomplete service
observation returns `(report, None)`. Invalid direct authority or changed local
inputs can raise before terminal publication. `require_observed_git_base` and
`describe()` recheck process identity, exact transport/configuration/scope, current
authority, deadline and retained evidence. The object cannot be constructed from
JSON or serialized. Its lifetime cannot exceed this observation's deadline or
the source transport's current authority.

The result grants no object omission, Git write, branch creation, CI, merge,
deployment or recovery authority. It proves no atomic remote snapshot, server
permission minimum, live GitLab version compatibility or canonical factory run.
Future transmission still needs fresh writer-side admission, exact P/B/T object
checks, a durable one-shot intent and independent readback. Controlled fixtures
and local TLS tests do not replace the selected owned GitLab qualification.
