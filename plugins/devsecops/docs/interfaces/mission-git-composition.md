# Same-process publication and Git preparation

`devsecops.factory.mission_git_operation.qualify_publish_and_prepare_git`
connects the existing publication/proposal producer to local deployment commit
preparation, complete local Git transport preparation, and bounded remote base
observation. This function is a production composition boundary, not a registered
lifecycle handler or a command to deliver a Git change.

Its upstream chain can acquire and build the selected base and publish artifacts
to the explicitly configured image registry. The Git portion creates only private
local objects and performs the base observer's fixed GETs. It never creates a
remote branch or merge request, pushes Git, merges, syncs a controller, or deploys.
Existing upstream effects and evidence survive a later failure; no stage retries.

## Caller contract

The caller provides a closed `configurations` mapping with exactly `build`,
`staging`, `security`, `signing`, `publication`, `proposal`, `commit`, and
`destination`. Each value is an absolute `Path` and independently selected
SHA-256 pair, using the existing component configuration schemas. Image
publication and Git destination remain separately selected origins and scopes.
The caller also supplies the six exact run input identities, expected immutable
source checkout and revision, installed plugin ID/version, new private output and
separate runtime roots. There is no saved proposal, pack plan or observation
input; all capabilities come directly from the current upstream calls.

Before upstream effects, the composition verifies its actual installed package
manifest/content and all currently loaded engine module paths and bytes. The build configuration must equal
the independently supplied run scope, including the selected package hash, and
source checkout/revision. Proposal and commit must agree on deployment checkout,
base commit, values path and proposal configuration hash; the independently
selected Git destination must name that same base. Output/runtime roots cannot
overlap each other, the source checkout, deployment checkout or installed package.
All eight configuration bytes are then retained privately under `output/inputs`
and checked against their selected pins before publication begins.

Run hashes and installed integrity do not supply a journal lock, mutation intent,
service grants, CI identity or release qualification. The separate explicit
[invocation command and GitLab recipe](mission-git-invocation.md) call this function
with independently pinned selections; they establish no authenticated CI or
journal authority. `OwnedLinuxDriver` and the legacy proposal recipe still do not
invoke it. This caller does not complete canonical preflight or provision.

## Direct composition and evidence

The returned `DeploymentProposal` is admitted against the selected source,
image destination and deployment target; it enters the existing
`prepare_deployment_commit`. The directly returned prepared commit enters
`prepare_git_transport`; that exact transport enters `verify_git_base_possession`.
The latter already repeats project/ref/commit reads around the complete tree/blob
walk, so no redundant separate destination observation is performed.

Successful return is `(summary, PreparedGitTransport, ObservedGitBase)`. The
handles retain their issuer process, expiry, original inputs and artifact guards.
No capability is serialized, reconstructed, renewed or granted a longer lifetime.
In particular, base-observation authority lasts no longer than that observer's
120-second execution deadline. Durable frozen destination inputs remain present
after return, so immediate consumers can perform the required revalidation.

One overall deadline is at most 3,960 seconds; publication/proposal retains its
3,600-second maximum and each of commit, transport and base its 120-second maximum.
Every boundary receives only remaining time, and final publication rechecks
package, frozen inputs and all live handles. Changed or expired authority cannot
leave an accepted successful terminal summary. Child decisions are retained only after their existing sealed result
identity is checked; an explicit child failure remains `fail`, and a malformed
result remains unknown. A seal never replaces a direct capability for success.
An invalid selection fails before upstream dispatch; an incomplete stage stops subsequent stages and returns no
handles. Integrity failures can raise instead of publishing a summary.

Each child retains its normal receipt. `mission-git-operation.json` records the
selected pins, separate source and new deployment commit, checks and exact narrow
scope. `factory_decision` stays `unknown`; CI execution and lifecycle completion
stay `not-established`, Git write stays `not-performed`, delivery authority stays
`not-issued`, and object omission stays false. A successful base observation
still does not identify the credential principal, prove its least privilege,
provide an atomic server snapshot or authorize a remote write.

## Consumption-time mission-base qualification

The existing `mission_operation.qualify_and_build` already calls
`qualify_mission_base` and immediately passes its returned `VerifiedBase` object
to `build_mission` within the same process and bounded operation. The upstream
publication/proposal chain uses that function. This supplies the component-level
fresh qualification boundary at build consumption; a preparation-phase receipt
is never loaded as authority and the capability's lifetime is not widened.
The remaining gap is integration into the actual journal-bound CI/build handler,
including independently observed same-run source/job/runner identities and all
required permissions and cleanup. It is not a missing second base-qualification
helper. No Registry1 or Grype acquisition was performed by the controlled tests.

## Local acceptance and remaining work

Focused tests use a controlled upstream proposal issuer and GET transport while
running the actual native Git commit, pack/closure and tree reconstruction stages.
They exercise independent source/run/package mismatch, all eight input pins,
base mismatch, missing remote base, saved/expired/foreign-scoped handles, changed
frozen input, protected outputs and terminal expiry. These fixtures qualify
composition behavior only; no real image publication, GitLab, lab, installed
release, CI job, native model, push or deployment is qualified.

Remote delivery still needs a fixed authorized writer, exact one-shot durable
intent, fresh principal/grant and project/ref checks, bounded transmission and
independent immutable readback with uncertain-effect reconciliation. Argo runtime
handoff, complete cleanup, canonical repetitions and recording remain separate.
