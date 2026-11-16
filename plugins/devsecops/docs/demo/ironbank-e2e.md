# Canonical Iron Bank end-to-end demo

This guide describes the installed `factory-demo e2e` acceptance path for
`ironbank-mission-recovery-v1`, using `bigbang-live`, the fixed `owned-linux`
backend and the resident `codex-cli` driver. It is an operator procedure for
explicitly selected, authorized inputs, not a statement that the current
candidate or any example configuration is live-qualified.

The required result is a real GitLab → owned registry → deployment Git MR → Argo
CD → Big Bang/RKE2 run, followed by the actual installed native correction,
recovery, D3–D5 controls, reset/replay and complete owned-resource cleanup. The
[two-hour runbook](two-hour-runbook.md) provides 120 minutes of planned content;
actual execution and the continuous source recording may take longer.

## What each boundary proves

| Boundary | Required actual evidence |
| --- | --- |
| Upstream Iron Bank / Registry1 | Authorized authenticated image bytes, immutable manifest/index/platform/config relationships, and each required signature/attestation checked against selected trust. An example digest or source URL is insufficient. |
| Owned GitLab source CI | Exact seeded source commit, the API-created pipeline, successful required jobs on the original dedicated runner ID and manager identity, and the authenticated publication artifact. Seed pushes do not trigger an extra build. |
| Owned Zot registry | Actual pushed/read-back immutable mission image and digest-linked SBOM, scan and signature evidence, with independent verification before promotion. The owned registry is a destination, not evidence that the derived app has upstream Iron Bank approval. |
| Deployment Git | The separate owned writer creates the selected review branch from an observed base; the owned MR workflow checks the exact source/base, successful required jobs on the original runner, and the actual merged watched-branch revision. A local patch or successful branch write alone is not deployment. |
| Argo and Flux | Argo reconciles the exact deployment revision for the workloads; Flux owns the actual Big Bang platform. Platform admission before first delivery is distinct from subsequent Healthy/Synced workload and inventory observations. |
| Runtime | Both fixed image identities, unchanged Iron Bank NGINX HTTP response, mission health and fresh metrics, positive and negative probes, and the qualified policy-only fault and repair. |

The upstream unchanged NGINX path and the factory-built mission image have
different provenance. Retain both evidence chains. Keep both image identities
fixed throughout the telemetry fault/repair so an image change cannot substitute
for the policy correction. Review and evidence readers do not receive the
separate build, writer, MR actor or lifecycle permissions.

## Prepare actual selections

Use a verified installation outside the author checkout, an external locked
Python runtime, and a new private evidence directory. The selected installed
`SHA256SUMS`, plugin ID/version and native client bytes must agree with the
current package, native selection, configuration and lock. Configure approved
native authentication separately; installing the plugin does not establish model
access. Select the model and reasoning effort explicitly. Canonical trace
retention must fit its 16 MB per-file and phase/export budgets; missing required
artifacts cannot be omitted to force a passing export.

The six main input identities are the installed package manifest, packaged
scenario, packaged assertions, operator configuration, tested lock and independent
environment inventory. The scenario and assertions are loaded from this installed
package. Prepare the remaining files outside it, with real environment ownership,
expiry, service identities, tool paths, trust and the permitted mutation scope.
[Local preflight](preflight.md) explains inspection without service effects.

There are sixteen auxiliary identities, supplied through six path/hash flag
pairs and their pinned dependencies:

| Explicit selection | Additional required material |
| --- | --- |
| Native selection | Installed root; actual supported Codex executable/version/SHA; external interpreter; explicit model/effort and bounded native invocation settings. |
| Mission Git invocation | Eight pinned build, staging, security, signing, publication, proposal, commit and destination configurations bound to the six main hashes. Linux runner tools/materials must be selected separately from host-native validation tools. |
| Owned registry recipe | Exact owned Zot image, data/storage inputs, credentials by approved reference, private CA/server trust and original host binding. Its bytes must equal the bootstrap recipe's nested registry selection. |
| Owned bootstrap recipe | `schema_version: 2`, `kind: owned-bootstrap-v2`, with the pinned `services.runner_vm` recipe, fresh foundation, service inputs, source/deployment seeds, platform configuration and matching writer policy. |
| Owned operations recipe | Required source/deployment jobs, polling bounds, native profile/tool/schema selections, and pinned writer/runtime recipes. Writer and runtime bytes become separately frozen auxiliaries. |
| Owned cases recipe | Exact D3 platform dependency, D4 supply-chain and D5 evidence-gap selections and their real observation inputs. |

See [auxiliary inputs](../interfaces/lifecycle-auxiliary-inputs.md),
[bootstrap inputs](../owned-bootstrap-inputs.md),
[operations](owned-operations.md), [native selection](../interfaces/canonical-native-driver.md)
and [case contracts](../interfaces/canonical-cases.md). Path/hash selections
record bytes; they do not grant actions. Do not obtain authority or destinations
from a saved report. Newly created project, runner, manager and commit IDs come
from the original owned operations and authenticated readbacks, not guessed
future IDs. Changed selection bytes require deliberate requalification.

The [runner VM contract](../interfaces/owned-runner-vm.md) selects a separate
Linux amd64 guest, distinct disk and SSH identities from RKE2, and the existing
`gitlab-runner` UID/GID 10001. The approved boot image must already contain the
**final current package**, GitLab Runner 19.5.1, external Python, selected locked
tools/schemas and normal uidmap/subordinate-ID prerequisites. A stock image or an
old embedded package does not meet this contract. Its real image bytes and
builder behavior still need qualification. No runtime package installation,
root SSH, sudo, global policy change or relaxation of the outer container's
NoNewPrivs/capability/network restrictions is part of this procedure.

The compiler stages the original selected private inputs beneath
`/home/gitlab-runner/.local/share/devsecops/<full-UUID-hex>/inputs`. It preserves
`/home/gitlab-runner/builds/factory-<UUID>/publication` and the 59-byte runtime
root `/home/gitlab-runner/builds/<full-UUID-hex>`. Current package membership and
staged input bytes are checked before jobs. Original route-owned CA files, an
empty CA directory and cleared proxy variables provide explicit guest trust;
there is no system-CA or public-registry fallback.

Choose and authorize each required actor's trust/access separately: host,
runner/builder, nodes/containerd, controllers and workloads. Explicit Registry1
access and, only for the selected publisher mode, P1 documentation access are
inputs, not permissions implied by this guide. Scanner database content,
acquisition authority, freshness/as-of time, signature trust and offline assets
remain explicit. Existing holds or uncertain operations must be reconciled
through their supported route before dependent work. A completed acquisition or
lab operation does not authorize replay. Each new action must remain within its
current scoped approval.

If Registry1 returns a blob download redirect, select the pinned
[blob download policy](../interfaces/registry-blob-downloads.md) and its matching
runner route before acquisition. This does not resolve attestation size,
predicate, signature or subject compatibility; retain those independent gates.

## Inspect, then launch once

Set the following variables from the actual approved selections. They contain
public paths and identities, never credential values:

- `DEMO_PYTHON`, `DEMO_PLUGIN_ROOT`, `DEMO_PLUGIN_ID`, `DEMO_PLUGIN_VERSION`.
- `DEMO_CONFIG`, `DEMO_LOCK`, `DEMO_ENVIRONMENT_ID`, `DEMO_INVENTORY`, a new
  absolute `DEMO_RUN`, and a private absolute `DEMO_SHARED_LOCK_DIR`.
- `DEMO_NATIVE`, `DEMO_MISSION`, `DEMO_REGISTRY`, `DEMO_BOOTSTRAP`,
  `DEMO_OPERATIONS`, `DEMO_CASES`, and each corresponding `_SHA256` value.
- An existing private `DEMO_WORKING_DIRECTORY` outside the author checkout and
  installed cache, used as the working directory for launch.

Inspect the configuration locally first:

```sh
"$DEMO_PYTHON" -B "$DEMO_PLUGIN_ROOT/scripts/factory_demo.py" doctor \
  --profile bigbang-live --config "$DEMO_CONFIG" --lock "$DEMO_LOCK"
```

Resolve actual reported gaps. `doctor` does not authenticate services or prove a
working platform. Check that scoped authorization and credentials cover the full
run, waits and cleanup; a two-hour presentation does not extend an expiry.

Select and verify the approved recorder and readable public terminal/UI/report
views. Start continuous capture before the following canonical command. For an
interactive terminal, retain the original process with cleanup controls:

```sh
"$DEMO_PYTHON" -B "$DEMO_PLUGIN_ROOT/scripts/factory_demo.py" e2e \
  --scenario ironbank-mission-recovery-v1 --profile bigbang-live \
  --config "$DEMO_CONFIG" --lock "$DEMO_LOCK" \
  --environment "$DEMO_ENVIRONMENT_ID" --environment-inventory "$DEMO_INVENTORY" \
  --package-manifest "$DEMO_PLUGIN_ROOT/SHA256SUMS" \
  --shared-lock-dir "$DEMO_SHARED_LOCK_DIR" --output "$DEMO_RUN" \
  --driver codex-cli --operation-backend owned-linux \
  --plugin-id "$DEMO_PLUGIN_ID" --plugin-version "$DEMO_PLUGIN_VERSION" \
  --native-selection "$DEMO_NATIVE" --native-selection-sha256 "$DEMO_NATIVE_SHA256" \
  --mission-git-invocation "$DEMO_MISSION" --mission-git-invocation-sha256 "$DEMO_MISSION_SHA256" \
  --owned-registry-recipe "$DEMO_REGISTRY" --owned-registry-recipe-sha256 "$DEMO_REGISTRY_SHA256" \
  --owned-bootstrap-recipe "$DEMO_BOOTSTRAP" --owned-bootstrap-recipe-sha256 "$DEMO_BOOTSTRAP_SHA256" \
  --owned-operations-recipe "$DEMO_OPERATIONS" --owned-operations-recipe-sha256 "$DEMO_OPERATIONS_SHA256" \
  --owned-cases-recipe "$DEMO_CASES" --owned-cases-recipe-sha256 "$DEMO_CASES_SHA256" \
  --cleanup always --resident-cleanup-controls
```

The console equivalent is `factory-demo e2e` with exactly the same arguments.
Select one launch route, not both. The separate installed qualification wrapper
is documented in [release qualification](../interfaces/release-qualification.md)
and the [runbook](two-hour-runbook.md#installed-qualification-launch). It invokes
this same factory command and records its own started/terminal receipt. It is
noninteractive: omit `--resident-cleanup-controls` there. A direct terminal run
does not manufacture that separate wrapper receipt.

The resident driver services eight primary/replay native handoffs using the
actual installed review, diagnosis, correction and recovery skills. Only the
actual accepted native correction is eligible for delivery. Fixture patches and
saved result JSON cannot replace it. Follow real source pipeline/job IDs,
registry subjects, MR/merged revisions, Argo state and running workload/probe
observations in the approved views. Headless stdout alone is not a presentation
of those handoffs.

## Outcomes, cancellation and cleanup

The packaged 33-phase graph covers preflight/prepare/provision/build, primary
D1–D2, D3–D5, one reset, replay D1–D2, export, cleanup and finalization. Replay
excludes D3–D6 and cannot invoke itself. Phase budgets bound operations; they are
not estimates of how long the full recording will take. An expected defect may
produce a review failure while the corresponding fault-detection assertion
passes. Infrastructure failure or unknown evidence never counts as that defect.

Final exits are 0 for complete scenario pass, 1 for a completed assertion failure
and 2 for incomplete/error. Exit 3 is a nonterminal `awaiting_native_step` handoff.
The `current-host` protocol retains exit 3, but production scope/capability
restoration after process exit is not implemented; its manual resume path is
not qualified parity with the resident driver. Do not claim both drivers pass
from protocol tests.

`--cleanup always` retains the environment across native handoffs and requires
cleanup on terminal completion/failure or explicit cancellation. SIGINT/SIGTERM
use the resident cancellation path. Keep the original process alive when a
cleanup control is offered; enter only the displayed fresh command/challenge
under the existing current authorization. Unknown effects require read-only
reconciliation before any fresh action. Follow the distinct normal continuation
and restricted guest-disposal paths in
[resident cleanup continuation](../owned-cleanup-continuation.md).

The sealed runner architecture has eleven cleanup scopes. Guest process/input
cleanup, host routes and VM disk/kernel termination each require their original
observations. A stopped VM alone does not resolve uncertain CI effects, and
successful cleanup cannot turn a failed scenario into a pass. Hard process loss
cannot restore these resident objects from JSON; ordinary `--resume --abort` is
not a general full-factory recovery mechanism. Preserve the original operation
and ownership evidence and use supported inspection/recovery. Never rerun an
uncertain request, reuse an expired helper, or infer nonexecution from absence.

## Retain the complete recording and prove repeatability

Display the finalized `e2e-result.json` after cleanup and verify its referenced
`evidence-manifest.json`. They bind package/scenario/assertion/lock, environment,
phase outcomes, actual native identities/patch, runtime linkage and cleanup.
Do not rewrite either after capture. Keep the continuous full video, chapters,
timecoded transcript/captions and `video-review.json` outside the finalized run.
Map visible phase handoffs to the exact registered receipts, including reset,
cleanup and terminal display. Preserve waits and operational steps; disclose and
map any edits in an optional shorter version. Keep secrets and raw private traces
out of recorded views while retaining original evidence privately.

Use the strict qualification/video schemas and
[release qualification procedure](../interfaces/release-qualification.md).
Require an actual playable file and an independent viewer's completeness,
readability, explanation and value decisions. A pending viewer or a media file
hash is not accepted playback. Select a supported authorized player; a denied
file/browser route is not permission to switch to an indirect bypass.

G7 requires three successful same-package runs without implementation changes:
two distinct fresh environments operated by a second human and one reset/replay
run, with all native, image, D1–D6 and cleanup evidence. One qualifying run also
needs the accepted full recording. Component tests, partial clips and the
120-minute runbook do not close these gates; other release gates retain their
separate required evidence.

## Current prerequisites that still require actual evidence

Before claiming this demo is ready, establish the exact final installed package
and native access; the approved separate runner image and real nonroot builder
qualification; the authenticated resolved Iron Bank/base image lock, required
attestations, scanner database and trust; complete environment/recipe/credential
selections; and authorized live GitLab/registry/RKE2/Big Bang/controller/runtime
operation and cleanup. Then complete the repeated runs, continuous capture and
independent playback review.

No tested `ironbank-mission-recovery-v1.bigbang-live.lock.json` is supplied while
its actual artifact identities and compatibility remain unqualified. Generating
example digests or relabeling a fixture lock would not resolve that input gate.
The source command and this guide are preparation for the qualified result, not
proof that it has already occurred.
