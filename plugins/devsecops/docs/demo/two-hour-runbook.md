# Two-hour Software Factory presenter runbook

This is 120 minutes of planned content. Actual provisioning, native calls,
replay and cleanup can take longer. Preserve all operational waits in the full
recording; optional edits must identify cuts and speed changes and map them to
the continuous source. A component rehearsal, prepared cue sheet or passing
local regression does not qualify the live demo.

## Start from explicit qualified inputs

Select one installed package, a qualified `bigbang-live` lock, an independently
established environment inventory and scoped authorization covering the intended
operations. Establish actual Registry1/image, scanner database, service and model
access before the run. Missing acquisition authority remains a blocker; changing
the image, scanner or profile is not a substitute for the canonical path.

Use [preflight](preflight.md), [bootstrap inputs](../owned-bootstrap-inputs.md),
[operations](owned-operations.md), [auxiliary inputs](../interfaces/lifecycle-auxiliary-inputs.md)
and [native selection](../interfaces/canonical-native-driver.md) to prepare the
explicit inputs. The six main inputs are package, scenario, assertions, config,
lock and environment. The installed scenario/assertions are fixed. The production
selection files below also contain pinned dependencies; do not silently repin
changed files. A manifest hash records bytes and does not itself grant permission.

Use `owned-bootstrap-v2` with the pinned `services.runner_vm` selection described
in [the dedicated runner VM contract](../interfaces/owned-runner-vm.md). It uses
a disposable guest separate from RKE2 and the nonroot `gitlab-runner` account
(UID/GID 10001). Verify the selected guest assets and service routes before the
run; demonstrate the actual guest identity, GitLab runner ID and manager identity
alongside the CI job receipts. The legacy container-runner selection cannot pass
full D6. This selection remains within the existing bootstrap auxiliary input;
the 33-phase graph and sixteen auxiliary input names do not change.

Choose the operator, recording surface, audio or captions, private media storage,
second operator and independent viewer. Check text readability with the existing
recorder before the qualifying run. Keep password prompts, credential stores and
Secret values out of captured views. Record any prerequisite gaps explicitly.

### Mac capture handoff at launch readiness

The current Computer Use policy blocks operating the Codex app, and its exposed
tools provide no video-capture API. Do not retry that protected-app operation or
substitute another agent-operated OS route. The presenter must foreground and
scroll Codex views when needed. Source integration and qualification must finish
before requesting the capture test.

Just before the qualifying run, the presenter uses the existing macOS Screenshot
app: press **Shift-Command-5**, choose **Record Selected Portion**, frame a safe
view, and set **Options → Microphone → None** plus a private local save location.
Make a short nonsensitive test clip, stop from the menu bar, and verify saved
playback and readable text. Capture on this Mac remains unverified until that
test passes; unexpected permission prompts require a separate decision.

For the real run, start recording before the canonical command and stop only
after the final result and cleanup are shown. Preserve the continuous original;
create captions and chapters after evidence finalization. No microphone, camera,
new recorder installation or computer reconnection is part of this handoff.
See [Apple's Screenshot instructions](https://support.apple.com/guide/mac-help/take-a-screenshot-mh26782/mac).

Set `DEMO_PYTHON` to the approved dependency interpreter and `DEMO_PLUGIN_ROOT`,
`DEMO_PLUGIN_ID`, `DEMO_PLUGIN_VERSION` from the actual installation. Set
`DEMO_CONFIG`, `DEMO_LOCK`, `DEMO_ENVIRONMENT_ID`, `DEMO_INVENTORY` and a new private
absolute `DEMO_RUN` from the approved input selection. For each of `DEMO_NATIVE`,
`DEMO_MISSION`, `DEMO_REGISTRY`, `DEMO_BOOTSTRAP`, `DEMO_OPERATIONS` and `DEMO_CASES`,
set the absolute selected JSON path and corresponding `_SHA256` variable to its
reviewed digest. These variables contain paths and public identities, not secrets.
Select an existing
private absolute `DEMO_WORKING_DIRECTORY` outside the author checkout and installed
plugin cache, and use it as the working directory. Check that the original grants,
service-route lifetime and credentials cover the full expected run, waits and cleanup;
a longer presentation does not extend a frozen authorization window.

Select one launch path below before starting. The direct terminal path supports
resident cleanup controls. The qualification wrapper provides a separate installed
invocation receipt and is noninteractive. Both require the same approved operation
inputs; neither restores a lost process from saved evidence.

The local readiness check performs no service or model call:

```sh
"$DEMO_PYTHON" -B "$DEMO_PLUGIN_ROOT/scripts/factory_demo.py" doctor \
  --profile bigbang-live --config "$DEMO_CONFIG" --lock "$DEMO_LOCK"
```

Resolve its actual gaps before capture. Set the approved recorder's source, audio
or captions, destination and stop control, then verify its preview. The resident CLI
prints its result at the end; it does not display a live presentation of each phase
or turn private native traces into video. Before launch, select existing approved
terminal, service UI and public report views for the capture:

- Follow the source revision and actual pipeline/job IDs in the owned GitLab view
  or the corresponding public CI receipts.
- Follow the immutable image/evidence subjects and deployment MR/merged commit in
  approved registry/GitLab views or their public readback and delivery receipts.
- Show the original native review/diagnosis report, actual correction diff and
  independent validation after those artifacts exist, then the delivered revision.
- Show approved Argo/Flux and runtime views or the public recovery reports for
  fresh metrics, positive probes and continued isolation.

Use `DEMO_RUN/state.json` privately to locate the current
`lifecycle.active_phase.phase_id` and completed `lifecycle.phase_records`.
Each completed record's `artifacts` contains run-relative paths and hashes;
use those exact references to select the corresponding public reports. After
finalization, `e2e-result.json` provides the phase records and manifest reference.
These data select views only; they never authorize another operation. Do not
record the entire state projection or substitute neighboring files, old reports
or a guessed filename. Keep raw
native traces, private auxiliary selections, credential prompts and Secret bodies
out of recorded views. Preserve originals; any presentation redaction is a labeled
copy stored outside the run. The [terminal evidence contract](../interfaces/terminal-evidence.md)
explains the final phase references and immutable manifest. These views are chosen
and operated by the presenter; no automatic dashboard or capture service is implied.
A recording of headless stdout alone does not demonstrate the required handoffs.

### Direct interactive launch

Begin continuous recording before this command, showing the selected starting
environment and package/lock/scenario identities. Run it in the selected
`DEMO_WORKING_DIRECTORY`. The resident process retains the original execution
objects needed for all native and operational handoffs:

```sh
"$DEMO_PYTHON" -B "$DEMO_PLUGIN_ROOT/scripts/factory_demo.py" e2e \
  --profile bigbang-live \
  --config "$DEMO_CONFIG" --lock "$DEMO_LOCK" \
  --environment "$DEMO_ENVIRONMENT_ID" --environment-inventory "$DEMO_INVENTORY" \
  --output "$DEMO_RUN" --driver codex-cli --operation-backend owned-linux \
  --plugin-id "$DEMO_PLUGIN_ID" --plugin-version "$DEMO_PLUGIN_VERSION" \
  --native-selection "$DEMO_NATIVE" --native-selection-sha256 "$DEMO_NATIVE_SHA256" \
  --mission-git-invocation "$DEMO_MISSION" --mission-git-invocation-sha256 "$DEMO_MISSION_SHA256" \
  --owned-registry-recipe "$DEMO_REGISTRY" --owned-registry-recipe-sha256 "$DEMO_REGISTRY_SHA256" \
  --owned-bootstrap-recipe "$DEMO_BOOTSTRAP" --owned-bootstrap-recipe-sha256 "$DEMO_BOOTSTRAP_SHA256" \
  --owned-operations-recipe "$DEMO_OPERATIONS" --owned-operations-recipe-sha256 "$DEMO_OPERATIONS_SHA256" \
  --owned-cases-recipe "$DEMO_CASES" --owned-cases-recipe-sha256 "$DEMO_CASES_SHA256" \
  --cleanup always --resident-cleanup-controls
```

The last option requires interactive stdin/stderr and enables only the bounded
[resident cleanup controls](../owned-cleanup-continuation.md). Omit that option
for a deliberately noninteractive run; record that choice and its recovery
limitations. Do not manufacture terminal input or a continuation challenge.
If the original cleanup observer seals a contained guest residual with revoked
runner registration, the terminal offers only the explicit
`dispose-runner-guest <prior-operation-id> <fresh-challenge>` command. Normal
`continue` is rejected in that state. Keep the original uncertainty visible;
only separate original host-route and parent-disposal proofs can close the
remaining guest-local resources. Do not treat this as an automatic retry.
Process loss, an uncertain external effect or a missing original inventory
requires supported reconciliation, not a second command with imported receipts.
This direct invocation does not create the qualification wrapper's installed
invocation receipt.

### Installed qualification launch

Use this alternative when the separate installed invocation receipt is required.
Set `DEMO_PACKAGE_SHA256` to the reviewed hash of the installed `SHA256SUMS`,
`DEMO_AUTHOR_CHECKOUT` to the actual author checkout, `DEMO_INVOCATION` to a new
private absolute receipt directory separate from the package and canonical run,
and `DEMO_TIMEOUT` to the approved overall duration in seconds. The wrapper's
supported bound is 1–2592000 seconds; select a duration that covers the complete
run within its original authorization. Begin continuous recording and operate the
same approved views before this command:

```sh
"$DEMO_PYTHON" -I -B "$DEMO_PLUGIN_ROOT/scripts/qualify_release.py" run \
  --installed-root "$DEMO_PLUGIN_ROOT" --package-sha256 "$DEMO_PACKAGE_SHA256" \
  --plugin-id "$DEMO_PLUGIN_ID" --plugin-version "$DEMO_PLUGIN_VERSION" \
  --engine-python "$DEMO_PYTHON" --author-checkout "$DEMO_AUTHOR_CHECKOUT" \
  --output "$DEMO_INVOCATION" --timeout "$DEMO_TIMEOUT" \
  -- e2e --profile bigbang-live \
  --config "$DEMO_CONFIG" --lock "$DEMO_LOCK" \
  --environment "$DEMO_ENVIRONMENT_ID" --environment-inventory "$DEMO_INVENTORY" \
  --package-manifest "$DEMO_PLUGIN_ROOT/SHA256SUMS" \
  --output "$DEMO_RUN" --driver codex-cli --operation-backend owned-linux \
  --plugin-id "$DEMO_PLUGIN_ID" --plugin-version "$DEMO_PLUGIN_VERSION" \
  --native-selection "$DEMO_NATIVE" --native-selection-sha256 "$DEMO_NATIVE_SHA256" \
  --mission-git-invocation "$DEMO_MISSION" --mission-git-invocation-sha256 "$DEMO_MISSION_SHA256" \
  --owned-registry-recipe "$DEMO_REGISTRY" --owned-registry-recipe-sha256 "$DEMO_REGISTRY_SHA256" \
  --owned-bootstrap-recipe "$DEMO_BOOTSTRAP" --owned-bootstrap-recipe-sha256 "$DEMO_BOOTSTRAP_SHA256" \
  --owned-operations-recipe "$DEMO_OPERATIONS" --owned-operations-recipe-sha256 "$DEMO_OPERATIONS_SHA256" \
  --owned-cases-recipe "$DEMO_CASES" --owned-cases-recipe-sha256 "$DEMO_CASES_SHA256" \
  --cleanup always
```

The wrapper supplies empty stdin and captures process output. Do not add
`--resident-cleanup-controls`: its terminal check would reject this launch before
effects. The wrapper does not pause for cleanup commands or provide a live native
trace display. Its timeout may leave an effect uncertain; inspect the current
operation through its supported route before any next action. Keep `started.json`
and the eventual `invocation.json` in `DEMO_INVOCATION`. Neither a wrapper exit nor
its receipt replaces the canonical terminal result or actual media acceptance.

## Planned presentation — 120 minutes

| Time | Segment | Evidence and explanation |
| --- | --- | --- |
| 00:00–00:10 | Goal and ownership | Mission app, unchanged Iron Bank NGINX, exact package/lock/environment; what Codex, CI, registry, Argo and Flux each own. |
| 00:10–00:30 | PR-review discussion | Inspect a separately verified installed review and safe/risk controls. Label synthetic or retained excerpts and their package versions. Explain findings and the actual diff. This discussion is outside the canonical capture. |
| 00:30–00:45 | Source through CI | Follow the source revision, actual pipeline/jobs, original dedicated guest and runner registration, build and security/signing receipts. Explain rejected and successful controls. |
| 00:45–01:00 | Artifact through deployment Git | Link registry push/readback, immutable image and evidence subjects, exact proposal, required deployment MR validation and merged commit. |
| 01:00–01:10 | Running baseline | Show exact Argo revision, Flux ownership, both real workloads, NGINX HTTP, fresh mission metrics and negative access probes. |
| 01:10–01:30 | Fault, review, diagnosis and correction | Confirm the qualified fault, then show the same-run native review, diagnosis, actual minimal patch, independent validation and delivery. |
| 01:30–01:40 | Independent recovery | Explain fresh telemetry, image continuity, successful positive probes and continued isolation; expose any unknowns. |
| 01:40–01:50 | Failure and evidence controls | Show actual D3 platform dependency failure/repair, D4 supply-chain controls and D5 missing/stale evidence with passing controls. |
| 01:50–02:00 | Reset, replay, cleanup and handoff | Follow the separate interrupted attempt, Git baseline restoration, one D1/D2 replay with four new native handoffs, export, all eleven owned cleanup scopes and terminal result. Show separate runner-service, runner-routes and runner-vm evidence. Explain repetitions and rerun inputs. |

These are discussion allocations, not engine deadlines. The canonical 33 phases
always follow their installed graph. Do not reorder or omit operations to fit
the schedule. If timings overrun, extend the session or show a clearly labeled
replay of a qualifying complete recording. Never splice failed attempts or
off-camera repairs into a supposedly continuous successful run.

Use the [22 blank capture cues](recording-cues.csv) and
[33-phase map](canonical-phase-cue-map.json). Fill actual run/attempt/phase IDs,
source time ranges, observation times and evidence paths during the capture.
The two build/promote views are one phase, not two independent successes.
PR discussion from a different run does not satisfy the same-run review phases.

## Complete execution and media acceptance

Display the immutable `e2e-result.json` and evidence-manifest identity after
cleanup/finalization, then stop capture. Store `full-demo.mp4` (or a documented
playable equivalent), `chapters.json`, captions/transcript and `video-review.json`
outside the finalized run evidence. Hash the media and companions there; do not
rewrite the run result to include a video that already displays it. Follow the
packaged [qualification contract](../interfaces/release-qualification.md) and
video/chapters schemas for exact fields and identity bindings.

Require three passing lifecycle runs on unchanged source/package: two fresh
environments replayed by a second human operator and one reset/replay. Each main
run demonstrates the unchanged Iron Bank workload and full required D1–D6.
One qualifying run must supply the continuous source recording. Preserve failed
attempts separately; implementation changes restart the same-package evidence set.

Review actual delivered playback, readability, captions/audio and credential
exposure using a permitted player. Decoding or frame extraction alone is not
viewer acceptance. An independent viewer who did not build the demo must explain
what deployed, Codex's contribution, the cause, the delivered patch, recovery and
isolation evidence, and how to rerun it. Record concrete answers and separate
completeness/clarity decisions in `video-review.json`. Missing live evidence,
repetitions, continuous capture or viewer acceptance leaves the demo incomplete.
