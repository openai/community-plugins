# Proposed GitLab deployment delivery

**Status: local object preparation is in implementation; remote delivery is
proposed and unimplemented.** The local preparation API consumes the current
`DeploymentProposal` object and prepares native Git objects and a pack. A future
delivery backend can create one new review branch with the exact image-values
change. The local preparation step contacts no remote. Neither slice creates a merge
request, merge, update a watched branch, trigger CI explicitly, synchronize a
controller, or claim promotion or deployment. Normal repository automation may
react to a branch creation; the operator must authorize that project workflow.

The canonical factory contract requires a recorded deployment commit and its
required gates between verified registry publication and Argo reconciliation.
This slice establishes the commit handoff only. Existing read-only GitLab
collectors remain unchanged. The actual correction patch from the native skill
requires its own delivery admission; the image-only proposal cannot substitute
for it during the telemetry repair demonstration.

## Local preparation API

`devsecops.factory.deployment_commit.prepare_deployment_commit` implements the
local step. Supply the direct `proposal`, its explicit `run_scope`,
`source_commit`, image `origin`/`repository`/`manifest_digest`,
`deployment_repository`/`deployment_commit`, and `proposal_configuration_sha256`.
The remaining inputs are `config`, `expected_config_sha256`, a new absolute
`output` directory and `timeout` (default/maximum 120 seconds).

The local configuration has this exact shape. Values below are placeholders,
not executable example credentials or accepted pins:

```json
{
  "schema_version": 1,
  "kind": "mission-local-deployment-commit-configuration",
  "deployment_repository": "/approved/deployment-repository",
  "deployment_commit": "FULL_40_CHARACTER_BASE_COMMIT",
  "values_path": "environments/lab.yaml",
  "proposal_configuration_sha256": "INDEPENDENT_PROPOSAL_CONFIGURATION_PIN",
  "git_path": "/approved/tools/git",
  "git_sha256": "APPROVED_GIT_EXECUTABLE_SHA256",
  "author_name": "Approved Operator Identity",
  "author_email": "operator@example.invalid"
}
```

The executable must be outside the candidate repository. The approved binary is
copied privately and its hash is audited around native calls and terminal
publication. Its approval is operator-selected; this step does not authenticate
the binary's original release distribution. SHA-256 Git repositories are
rejected before construction. The local configuration accepts no remote address,
credential or command hook.

Outputs are `objects.pack`, `objects.idx`, `commit.txt`, the frozen configuration
and a sealed `deployment-commit.json`. The receipt includes exact parent/tree/blob
IDs, object inventory, source/proposal/published evidence bindings, tool identity
and bounded native-command receipts. `PreparedDeploymentCommit` is a current
process-local object with scope `local-prepared-commit-only`, not a delivery
grant. It cannot be constructed from those files or serialized. A controlled
proposal can exercise actual object preparation but returns unknown and no
capability. No-change input returns `unchanged-values-no-commit-required`, with
no objects or commit capability. No network or deployment observation is claimed.

Output must be outside the deployment checkout, including its `.git` directory
and path aliases. Only ordinary repositories with an internal `.git` directory
are supported. Gitfiles, linked/separate/shared metadata, metadata symlinks and
alternate object stores are rejected before output or scratch creation. Metadata
inspection is bounded to 20,000 entries and 32 directory levels and retains the
`.git` directory identity for subsequent audits. Scratch and all native-command
temporary input/output files are created beneath the private output directory,
independent of the ambient temporary root. Scratch is removed before return;
cleanup failure withdraws the terminal receipt.

## Proposed remote admission and configuration

The proposed API accepts a direct, current, same-process `DeploymentProposal`,
its exact run/source/image/deployment scope, independently selected delivery
configuration bytes and their SHA-256, a new private output directory, and an
overall deadline of at most 120 seconds. Reports cannot reconstruct authority.

Configuration binds one canonical HTTPS origin, numeric GitLab project ID,
literal repository HTTP path, full base commit, exact new branch, selected
values path, explicit read and dedicated write credential references, optional
private CA path/hash, approved Git executable path/hash, and operator-selected
commit author identity. It repeats the proposal configuration pin and local
deployment repository identity for an independent scope comparison. Credential
values never appear in configuration, argv, reports, URLs or error messages.

The read identity may use an explicitly selected API bearer token. The Git
write identity uses explicit username/password environment references suitable
for that GitLab project's smart HTTP authentication. Both are frozen in memory
for the operation. No credential helper, browser session, proxy, authentication
redirect, repository remote, or implicit process environment selects access.

The new branch must be absent. No-op proposals return a no-change observation
without a write or commit capability. The first qualified protocol tuple will
use 40-character SHA-1 Git object IDs; SHA-256 repositories remain unsupported
until their advertised protocol and real server behavior are separately tested.
SHA-256 still binds all retained evidence files and the configuration.

## Bounded local object preparation

No remote clone or fetch is needed. The proposal already selected a locally
available immutable base commit. A dedicated reader uses the approved Git
binary and the existing bounded process runner to read that raw commit and only
the tree objects along the selected values path. Each object is independently
hashed with its Git type/length header and compared with its expected ID.

The reader retains the existing disabled hooks, global/system Git configuration,
replacement objects and terminal prompts. It additionally disables lazy
fetching and all Git network protocols. It never checks out files, evaluates
attributes or filters, traverses submodules, writes to the source repository,
or relies on candidate remotes. Missing local objects fail before any write.

Initial limits are 16 path segments, 64,000-byte values and base commit,
2 MiB per tree, and 8 MiB total retained raw objects. Native `git hash-object`
copies only the selected base commit and ancestor objects into fresh private
bare scratch, without alternates or a source object-store write. Native
`git ls-tree -z` and `git mktree -z --missing` preserve the exact mode, name and
object ID of every sibling. Recreating each original tree must first reproduce
its original ID. The selected regular file must match the proposal's retained
before-values. Native `hash-object` creates its after-values blob and
`commit-tree` creates a commit with exactly the selected base as its sole parent.
An independent native raw diff must contain only the selected file modification
with its unchanged mode.

The commit message binds the source commit, image digest, proposal patch hash and
published evidence digest. Author/committer identity is explicit; one timestamp
is captured before the intent and held fixed. This is not a signed Git commit or
a replacement for the separately verified image signature.

Native `git pack-objects` receives only the explicit new object IDs, with no
`--revs` traversal and delta/reuse disabled. The pack contains at most 18 objects
and 9 MiB encoded. Native `git index-pack` and `git verify-pack` validate its
exact inventory in private scratch. No custom Git object or pack binary encoder
is used. The existing process runner has an additive `stdin_bytes` option capped
at 8 MiB, copied to a private temporary regular file. Receipts record only its
hash and length; callers without stdin keep their original receipt shape.
Local Git preparation supplies the runner's explicit `temp_directory`; other
callers retain their existing default. The runner rechecks its deadline after
input staging and before starting a child process.

## Exact new-reference write

The preferred candidate is native Git's fixed single-reference push with
`--force-with-lease=refs/heads/SELECTED_BRANCH:` and the exact
`NEW_COMMIT:refs/heads/SELECTED_BRANCH` refspec. The explicitly empty expected
value [requires that the ref be absent](https://git-scm.com/docs/git-push).
No bare force, implicit tracking-ref lease, wildcard, tag, delete or default
push selection is accepted. The command would run from private scratch, with
global/system configuration, hooks, credential helpers, signing, prompts,
submodule recursion, proxy environment and redirects disabled. TLS verification
and the selected CA remain enabled. The numeric project's independently read
Git HTTP URL must equal the configured origin and repository path. No credential
may appear in argv, URL, raw retained diagnostics or trace output.

**Unresolved transport qualification:** the existing process runner bounds time
and stdout/stderr, but does not bound native libcurl's received advertisement,
aggregate wire bytes, request count or authentication resend behavior. A bounded
locally prepared pack does not establish those network limits. Direct native
push is not yet admitted as meeting this backend's complete contract.

The installed Git 2.55 help exposes `send-pack --stateless-rpc` and the lease
option. If direct native push cannot meet the limits, a bounded HTTP bridge
around native `send-pack` is a possible next experiment, retaining Git's native
object/protocol handling. Its duplex stdin/stdout lifecycle, framing and
cancellation remain unqualified. No bespoke Git protocol implementation is
approved here. Required wire limits remain 2 MiB/10,000 advertised refs,
9 MiB attempted upload, bounded status/request count and the overall deadline.
The existing GET-only clients remain unchanged.

Before the single mutation attempt, a private fsynced intent binds origin/project/ref,
expected absence, base/new commit, exact pack hash/size, configuration, proposal
and published evidence. Attempted bytes count against the budget before each
send. One total request/deadline/byte budget covers the operation. A failed send
or ambiguous response poisons the writer; there is no blind retry, compensating
deletion or automatic overwrite. The retained intent and known identities remain
available even when effects are unknown.

Success requires a complete bounded status response: unpack success and one
success for the exact requested ref. Remote diagnostics are categorized and
hashed, never printed as trusted instructions or copied into safe errors.

## Independent readback and publication

A fresh read session checks the numeric project/path, exact new branch
advertisement, returned commit ID and sole parent, and the raw new blob at its
known Git object ID. The branch must point to the exact locally constructed
commit, which binds the full reconstructed tree. API diff summaries alone cannot
prove a single-file change because GitLab's
[commit diff endpoint](https://docs.gitlab.com/api/commits/#retrieve-commit-diff)
can stop at configured limits. Raw blob bytes avoid the text conversion described
by the [repository files API](https://docs.gitlab.com/api/repository_files/).

The current capability, frozen configuration, proposal bytes and locally prepared
objects are audited before the mutation and after terminal receipt fsync. A
failed final audit withdraws the passing receipt while preserving the mutation
intent. An ambiguous upload remains unknown even if a later observation finds a
matching branch; that observation does not silently turn into delivery authority.

A future process-local delivery result will bind project/origin/ref, base and new
commit, exact values and patch hashes, proposal and published evidence identity,
write/readback receipts, run scope and expiry. Its scope is a verified new review
branch only. Controlled test evidence cannot issue production delivery authority.
CI status, merge gates, Argo revision and runtime assertions remain unobserved.

## Required implementation qualification

Use actual local Git objects and an owned `git http-backend` behind TLS, with a
separate narrow GitLab API fixture. Prove the received parent and complete tree,
not only a simulated success response. Exercise branch creation between discovery
and POST, wrong project/path/base, changed before/after evidence, exact sibling and
mode preservation, duplicate/truncated status, interrupted upload, redirect and
credential canaries, expired authority, terminal output-route changes, every
resource bound, and no-op behavior. Test the extracted plugin and Python 3.11/3.14.

Local Git transport tests do not qualify a GitLab deployment. The selected real
GitLab/version, service authentication, permitted branch prefix and server hooks
must complete an authorized owned-project run before claiming live support.
