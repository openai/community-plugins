# Owned GitLab project bootstrap component

This component implements creation and verified seeding of the plan's owned
source and deployment projects. It does not install a GitLab server, create an
account/token, register a runner, run CI, merge an MR, or delete resources. It is
not registered in the CLI or native lifecycle dispatcher. All current validation
uses controlled API models; no live GitLab bootstrap or full demo is claimed.

## Engineering contract

The first implementation targets **GitLab 19.5.3 Community Edition, REST v4**.
GitLab's [October 29, 2026 patch release](https://docs.gitlab.com/releases/patches/patch-release-gitlab-19-5-3-released/)
includes this CE version. The shared `GitLabSession` verifies the exact version
and `enterprise: false` through the [Metadata API](https://docs.gitlab.com/api/metadata/).
This is a source-level contract selection, not evidence that any lab has this
version installed. A later version requires an explicit contract update and
qualification; there is no version-range fallback.

The caller supplies one canonical HTTPS origin and a private credential reference
to the shared session. The session freezes the token and trust snapshot, verifies
the active expected user and that same token's active, unrevoked, unexpired `api`
scope through the [personal access token self endpoint](https://docs.gitlab.com/api/personal_access_tokens/#self-inform),
and repeats these observations before every mutation. Responses and credentials
cannot supply a new host, command, handler, authorization grant, or redirect.

`BootstrapScope` supplies the environment ID/fingerprint, expected user ID, and
personal namespace ID/path. `bootstrap_owned_projects` verifies that the active
username equals that namespace path and that the [namespace response](https://docs.gitlab.com/api/namespaces/#retrieve-namespace-details)
has the exact ID/path, `kind: user`, and no parent. Group namespaces are outside
this first component contract. The existing plan does not require a group.

## Effects and observations

1. Validate exactly two immutable `ProjectSeed` values, ordered `source` then
   `deployment`. Each contains 1–16 UTF8 files, at most 64 KiB per file and 256 KiB
   total, including `README.md`. Paths are bounded, relative, unique, and reject
   traversal, `.git`, and file/directory collisions. Seed bytes are provided
   directly; no filesystem traversal, command generation, remote import, or
   credential discovery occurs.
2. Exclusively create `gitlab-bootstrap-<environment_id>` beneath the supplied
   absolute journal root. An existing directory is a hard stop before any API
   request. Scope and seed digests are fsynced before service inspection.
3. Verify the session/namespace and observe that both deterministic project paths
   are absent. An existing project is always refused, even if its description
   resembles an ownership marker. Only an authenticated 404 is an absence
   observation; other failures stop the workflow.
4. Before each project creation, recheck identity and durably record the exact
   intent. Use [project creation](https://docs.gitlab.com/api/projects/#create-a-project)
   with the verified current user's explicit numeric personal namespace ID. Projects are private,
   initialized on `main` with a README, and have instance/group runners and Auto
   DevOps disabled. The description binds the environment ID/fingerprint.
   MR policy is fast-forward, never squash, successful pipelines required,
   skipped pipelines disallowed, discussions resolved, and source branch retained.
5. Retain each direct create response's numeric ID immediately, before subsequent
   identity checks. Verify owner, namespace, exact path/repository URL, marker,
   visibility, branch, archive state and policy against a fresh project read.
   An unverified observed ID is never deletion authority. Distinct roles must have
   distinct numeric IDs.
6. Verify the initial repository consists solely of a regular README. Construct
   one bounded [commit action batch](https://docs.gitlab.com/api/commits/#create-a-commit)
   updating that README with `last_commit_id` and creating the other files. The
   caller's identity, project and branch are checked again; a digest-only intent
   is fsynced before dispatch. `force` is false and `allow_empty` is true so an
   unchanged README-only seed still has a verified seed commit. Seed contents are
   not journaled.
7. A successful HTTP mutation remains an applied effect even if verification
   fails. Require exactly the observed initial parent; verify current `main`,
   the complete [repository tree](https://docs.gitlab.com/api/repositories/#list-all-repository-trees-in-a-project),
   regular file modes, and every [file response](https://docs.gitlab.com/api/repository_files/#retrieve-a-file-from-a-repository).
   Check decoded bytes, size, SHA256 and Git blob SHA1 against the submitted
   seed. The tree collection requires explicit pagination closure and is bounded
   to one page/64 entries. Recheck both project identities and heads before the
   terminal report.

The commit actions are atomic as a batch, but the README `last_commit_id` guard
does **not** compare-and-swap the whole branch. A concurrent commit changing
another file can be accepted by GitLab. A divergent returned parent or observed
tree/head is recorded as applied but incomplete; the component stops writes and
does not retry, reset a ref, or claim that nothing happened.

## Inventory and interruption

Each immutable journal file is exclusively created, fsynced, and followed by a
directory fsync. Directory traversal rejects symlinks. The terminal report lists
every created or ambiguously created project, ownership verification state,
base/seed commit when observed, and residual cleanup requirements. The durable
pre-dispatch intent remains evidence if the process cannot save a response.
Remote error text, seed contents, and secret values are excluded from receipts.
If terminal receipt durability fails, any later failure receipt remains
`incomplete`; the function does not return a successful component result.

An ambiguous create or seed poisons the shared writer and stops all subsequent
workflow mutations. GETs remain available for separate reconciliation. Local
readback divergence also stops writes. There is no replay/import constructor,
retry loop, project adoption, or cleanup action in this component. A new journal
root must never be used to turn an unresolved prior operation into a retry.

The directory reservation protects this component invocation within its supplied
journal root. It is not a replacement for the lifecycle's cross-process mutation
reservation, action-time authority, or unresolved-effect reconciliation. Native
composition must supply the correct generation journal and those guards before
this component can be registered. Saved JSON is observation only.

## Gates before a real repeatable demo

- Install and observe the pinned owned GitLab service on adequate owned capacity,
  including TLS/service identity and actual endpoint readiness.
- Provision the disposable principal/read and write credentials through the
  approved ownership lifecycle; observe numeric IDs and scope without logging
  secret values. The component does not create these prerequisites.
- Bind the component to exact lifecycle scope, source/recipe identity, mutation
  reservation, journal generation and current action-time authorization.
- Supply the reviewed real source/deployment seeds. Their CI configuration must
  implement the MR workflow's detached `merge_request_event` pipeline and required
  named jobs. Component file verification does not establish working CI.
- Register the owned runner, bind its ownership inventory, and verify the real
  pipeline, MR gates and controller reconciliation through the composed workflow.
- Implement complete ownership-bound project/runner/account/token/server cleanup
  and deletion/revocation observations, including partial/unknown outcomes and
  retained export. No bootstrap report authorizes cleanup.
- Exercise cleanup and a fresh generation, then record and play back the actual
  end-to-end run. Controlled component tests are not this evidence.

`canonical_success`, `production_preflight_ready`, and `complete_cleanup` remain
false even when `state` is `component-complete`.
