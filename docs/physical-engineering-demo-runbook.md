# Physical Engineering: reusable demo and recording runbook

**The battery changed. Update the design without losing the requirements.**

This runbook accompanies the [implementation candidate](../plugins/physical-engineering/README.md).
It specifies the recording and session acceptance; it is not evidence that a
full take, installed session or independent facilitator run has happened.
Record actual results and remaining gates with the selected package revision.

The worked example is a synthetic civilian quadcopter battery tray and removable
electronics bridge. Teach the broader method: establish interfaces, measure the
design, accommodate a changed component, and regenerate a traceable handoff.

## Session formats

| Format | Material | Participant outcome |
| --- | --- | --- |
| 10-minute introduction | Qualified 6–8 minute teaching cut plus discussion | Trace a changed requirement to a measured result |
| 30-minute guided session | Selected chapters, baseline checkpoint, scenes C–D | Diagnose the failed fit and inspect the corrected handoff |
| 60–90 minute hands-on lab | Empty study, scenes A–E, transfer exercise | Run and explain the complete engineering-change workflow |

These are facilitation budgets. Adjust after rehearsal. A resumed checkpoint
supports teaching and recovery; it is not a fresh end-to-end run.

Use the package's [brief template](../plugins/physical-engineering/references/brief-template.md)
and [session guide](../plugins/physical-engineering/references/session-guide.md).
Include baseline, failed-fit and final checkpoints from the recorded take,
with their exact run IDs, requirement revisions and hashes. Large bundles and
videos stay outside the installed package.

## Preflight and rehearsal

1. Run the Text-to-CAD proof: model creation, actual geometry inspection, STEP
   export/readback, PDF drawing and graphical view. Record exact dependencies.
2. Run `npm run test:physical-engineering`. Require the baseline, wider-battery
   failure, corrected design, shifted-hole failure, transformed assembly, stale
   export, wrong units and path-escape cases. Missing/skipped cases are explicit.
3. Install the candidate from the actual source/ref and verify the loaded
   Physical Engineering and Text-to-CAD versions in a fresh Codex session.
4. Rehearse in a separate directory. Prepare an empty official study, an empty
   reproduction destination, and independent CAD state/runtime.
5. Have a second operator execute the delivered reproduction and a facilitator
   unfamiliar with the implementation complete the transfer exercise. Record
   who/what ran; independent agent rehearsal is labeled as such and does not
   substitute for human facilitator acceptance.
6. Preflight the selected capture method, readable text, available disk and
   playback. Use public fixture data and a dedicated demo profile. Exclude
   personal paths, unrelated plugin output and token-bearing viewer addresses
   from the published replay and transcript.

Run the pinned setup described in the README. Do not update dependencies during
the official take. Keep exact recorded assets immutable and maintain the separate
[compatibility note](../plugins/physical-engineering/references/compatibility.md).

## Capture the real CLI workflow

The reference recording method captures actual `codex exec --json` processes,
exact operator prompts, receipt timestamps and newly generated CAD outputs.
Use the [recording tools](../tests/physical-engineering/support/README.md).
Start before the installed-version proof and stop after the final evidence trace.
The video remains labeled **Recorded Codex CLI event replay** throughout; it is
not presented as native desktop footage. Every CAD image must be generated from
this take's known model and observed/hash-recorded during the take.

Preserve raw events privately. Keep real timing, waits and recovery in the full
replay. Derive the 6–8 minute teaching cut from the same timeline, with disclosed
compression. Retain complete prompts and tool outputs in the reviewed transcript;
do not invent streaming text or splice rehearsal results into the take.

A native desktop capture is an optional alternate format. Preflight its OS
permission, region and privacy separately, and identify the method in metadata.

Use five chapter names: **A Brief**, **B Baseline**, **C Changed requirement**,
**D Revised handoff**, **E Reproduction**. Introduce each engineering objective
and point to its observed result. Keep narration reusable: no customer names,
event dates or sales claims. Put exact technical versions in the metadata.
Provide captions and a searchable transcript.

## A. Brief

```text
Use engineer-subsystem to start a drone-module study in
[EMPTY_STUDY_DIRECTORY].

Verify the installed plugin/ref and Text-to-CAD version. Use the bundled
synthetic fixture and battery A. Show the frame/board interfaces, battery
envelope, lateral clearance, insertion state/path, planar-wall requirements,
material assumption and mass target.

Write study.json and the generated context.md. Preserve fixture values.
Record missing capabilities or conflicting requirements before proceeding.
Use only this study's known files and actual returned CAD document IDs.
```

**Show:** installed versions, initially empty study and requirements table.
Unresolved required capabilities cannot pass silently.

## B. Baseline

```text
Generate the two-part tray and removable electronics bridge through
Text-to-CAD, with stable unique part labels. Show the assembled view and
battery insertion path.

Use verify-engineering-design to measure actual interfaces, fit, service
clearance, support/contact, overlaps, declared walls and polymer mass.
Export and re-read the neutral and printable files. Preserve failed runs
and fix the design without changing frozen acceptance requirements.

Generate the baseline drawing and BOM. Preserve source, requirement
snapshot, measurements, logs and hashes. Update the resume summary.
```

**Show:** editable source, actual CAD, numeric checks, drawing PDF and BOM.
Measurements must come from geometry.

## C. Changed requirement and fresh-session recovery

Open a fresh Codex session in the same study while capture continues.

```text
Use engineer-subsystem to resume from study.json and context.md.
Recover the selected design, fixed interfaces, acceptance limits and
decisions. Confirm those values before edits.

Create a requirement revision using battery B. Keep the existing geometry
unchanged initially. Use verify-engineering-design to measure it against
the new battery and show the resulting fit failure. Preserve this failed
run and the original requirements/results.
```

**Show:** recovered constraints, 35 mm → 40 mm battery width, measured old
cavity/insufficient clearance and failed requirement. If the old tray passes,
investigate instead of fabricating a failure.

## D. Revised handoff

```text
Use engineer-subsystem to revise the tray/bridge for battery B while
preserving the fixed frame and board interfaces. Verify the revised
geometry against the new requirements.

Use prepare-engineering-handoff to regenerate assembly STEP, individual
print files, dimensioned PDF, BOM and assembly notes. Show original versus
revised fit and mass measurements and the drawing/BOM changes.

Re-read delivered geometry and inspect the PDF. Confirm every artifact
belongs to the selected run. Package editable source, frozen requirements,
verifier/version, dependencies and an exact reproduction command.
```

**Show:** corrected clearance; unchanged 100 × 60 mm frame and 30 × 30 mm
board patterns; current drawing/BOM and coherent fabrication files.

## E. Reproduction

```text
Execute the delivered reproduction command on the preflighted clean
destination with independent CAD state/runtime. Rebuild from delivered
source and requirements. Compare actual geometry within the declared
tolerances; preserve the new versions, results and hashes.

Trace one final requirement to its measured value and exact delivered
file. Summarize the initial failure, revision and fresh reproduction.
Report observed elapsed time, retries and interventions; usage/cost only
where available. State what remains for fabrication and bench testing.
```

**Show:** actual reproduction output and evidence trace. Cached replay or a
prior receipt does not replace execution. Stop recording after the result.

## Transfer exercise and facilitator notes

After the core flow, use the following prompt:

```text
Use engineer-subsystem to create a requirement revision with a
110 × 60 mm frame mounting pattern. Keep battery B, board interface,
envelope and process requirements unchanged. Verify the old geometry and
preserve its mounting-pattern failure. Revise and verify the
design, then regenerate the handoff. Do not edit the verifier.

Show the measured mounting-pattern change and evidence that remaining
requirements hold. Report infeasibility instead of relaxing another limit.
```

Expected evidence is requirement-based, not pixel-identical geometry:

| Checkpoint | Expected observation |
| --- | --- |
| Baseline | Battery A fits with 0.5–1.0 mm lateral clearance per side |
| Failed fit | Battery B does not fit the baseline cavity/insertion path |
| Revised | Battery B clearance passes; original frame/board interfaces remain |
| Transfer | Frame pitch measures 110 × 60 mm; other applicable limits pass |
| Cold rebuild | Delivered source/requirements reproduce accepted geometry within tolerance |

Ask participants to trace one changed requirement through a measurement to a
delivered file. For another geometry class, explain the new measurements needed
before reusing the workflow.

Before each future session, check qualified versions and create a new study.
Retain prior participant runs. If live tooling is unavailable, use clearly
labeled playback/checkpoints; do not describe playback as a live run.

## Recovery

| Failure | Response |
| --- | --- |
| Build error | Preserve source/error, repair and create another run |
| Command times out | Inspect the owned build before retrying; preserve real wait/recovery |
| Conflicting requirements or lost context | Recover saved requirements and record an explicit decision |
| Wrong/unknown measurement | Keep failed/unknown; repair a defective checker outside the official take |
| Stale geometry, drawing or BOM | Regenerate from the selected run and re-read |
| Reproduction differs | Compare source, requirements, tool versions and tolerances |
| Capture interruption or missing scene | Retain failed take and record a new complete take |

Do not splice an old success into a new full take. A partial take remains partial.

## Media, captions and evidence

The CLI renderer produces the full and teaching videos, captions, transcript,
chapters and hash manifest. It also runs decoder checks. For an optional native
desktop recording, equivalent conversion and decoder checks are:

```sh
PE_RAW_VIDEO="recordings/take-001/original.mov"
PE_FULL_VIDEO="recordings/take-001/physical-engineering-full.mp4"
ffmpeg -nostdin -n -i "$PE_RAW_VIDEO" -map 0:v:0 -map '0:a?' \
  -c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p \
  -c:a aac -b:a 192k -movflags +faststart "$PE_FULL_VIDEO"
ffprobe -v error -show_format -show_streams -of json "$PE_FULL_VIDEO"
ffmpeg -nostdin -v error -i "$PE_FULL_VIDEO" -f null -
shasum -a 256 "$PE_FULL_VIDEO"
```

Watch the full video for readability, audio/captions and all five scenes.
Successful decoding alone is insufficient. The teaching cut needs the same
review. Captions/transcript must describe the observed take, not planned results.

The media manifest records actual package/ref, host/model/CAD versions, run IDs,
fixture/source/checker/checkpoint/output/video hashes, A–E chapter timestamps,
and disclosed edits. Link every chapter to its matching prompt and files.
Include actual elapsed time, retries/interventions and unknown cost where
applicable. Verify public links without authentication before public release.

The evidence archive contains the same source, requirements, verifier and
reproduction command shown in the take. Retain encoded video, reviewed event
journal, captions, transcript, chapter index and session kit outside the
installable package. Keep the recorder's raw private directory out of shared
archives. Review for private data and viewer tokens before sharing.

Only a recorded, reviewed and independently reproduced candidate can be labeled
as completing these gates. Required maintainer approval, CLA and repository
checks still apply before release.

## Later physical milestone

M1 is digital subsystem verification. M2 prints/assembles it with inert component
surrogates, measures fit/mass/service access, revises from observations and retests.
Whole civilian-drone integration is M3. Electrical-harness work stays separate.

Preserve the same requirements and artifact lineage across those later stages.
Digital fit does not establish structural, electrical or flight performance.
