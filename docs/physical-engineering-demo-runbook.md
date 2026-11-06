# Physical Engineering: showcase, live exercise and evidence

**Start with a brief. Build, check and revise the design in Codex.**

Start with the **two-minute narrated CAD explanation**, then build the bounded
fixture live from a new study. The story begins with the brief, creates the
tray and bridge, checks the fit, then introduces a wider battery. Keep the full
recorded CLI replay and evidence available for inspection.

This runbook accompanies the [implementation candidate](../plugins/physical-engineering/README.md).
It defines presentation and session acceptance; executed receipts establish
which gates a selected revision actually completed. Record actual results and
remaining gates rather than treating this plan as evidence of a completed run.

The worked example is a synthetic civilian quadcopter battery tray and removable
electronics bridge. The implemented plugin supports this parameterized fixture:
battery A or B and a 100 or 110 mm frame X pitch. Teach the broader method of
preserving interfaces, measuring a failure and rebuilding a traceable handoff;
another geometry class requires its own model and acceptance checks.

## Present the two-minute build and revision

Use the reviewed narrated animation as the opening explanation. Its large CAD
views, sections and annotations answer one question at a time: what should be
built, does it meet the brief, and what changes when the battery gets wider?
The [showcase build guide](../tests/physical-engineering/support/showcase/README.md)
describes the renderer, asset provenance and release checks. Select the actual
reviewed media from the delivery index; generated video is not bundled with the
plugin or this runbook.

| Time | Explain and show |
| --- | --- |
| 0–10 s | Start with an empty study and a brief: battery tray, removable electronics bridge and fixed mounts |
| 10–23 s | Build the tray, showing actual construction geometry and its battery, mounting and strap features |
| 23–36 s | Add the bridge and assemble both parts; retain the 100 × 60 mm frame and 30 × 30 mm board patterns |
| 36–48 s | Check battery A against the completed baseline: 36.5 mm cavity and +0.75 mm clearance per side |
| 48–59 s | Change the battery width from 35 to 40 mm; check the unchanged tray first |
| 59–72 s | Show the interference and −1.75 mm clearance per side; preserve the failed check |
| 72–88 s | Widen the cavity to 41.5 mm, restoring +0.75 mm clearance while keeping the mounts fixed |
| 88–109 s | Check the insertion path and exports; deliver the source, CAD, drawing and BOM; show the source rebuild matching measured geometry |
| 109–120 s | Introduce the live exercise: build in Codex from the brief to the checked handoff |

These numbers describe the selected reference fixture. Read a future run's
measurements before using the same callouts. Make Codex's role explicit: it
creates the study from the brief, builds the model, measures its exports,
preserves the changed requirement and failed check, and packages the revised
design with its checked deliverables. Keep one headline and one supporting
measurement per scene; use narration to explain cause and result.

The animation explains recorded CAD work. A construction reveal must use the
actual model operations or actual intermediate geometry, with the new study and
build identified in the release receipt. Its sections come from actual CAD
intersections; battery/board solids are requirement envelopes. Preserve earlier
qualified plugin and run bytes. The animation itself is not new live footage
or evidence of CAD qualification. Keep provenance and runtime/physical
qualification details in the companion material and
receipts. The spoken and on-screen story leads to the next live exercise;
the primer has no LIVE badge or real-time execution claim. The build guide
records world coordinates, identity mesh transforms and every derived asset's
source, including the actual separate rebuild supporting the verified handoff.

## Session formats

| Format | Material | Participant outcome |
| --- | --- | --- |
| 10-minute introduction | Two-minute explanation, actual failed/corrected checkpoint, discussion | Explain the fit failure and trace the correction to a measurement |
| 30–45 minute guided session | Explanation, empty study, live brief/build/check/change/revision and handoff | Build from the brief, diagnose the failure and inspect the checked deliverables |
| 60–90 minute hands-on lab | Explanation, empty study, live scenes A–E and transfer exercise | Run and explain the complete bounded engineering-change workflow |

These are facilitation budgets. Adjust after rehearsal. A resumed checkpoint
supports teaching and recovery; it is not a fresh end-to-end run.

Use the package's [brief template](../plugins/physical-engineering/references/brief-template.md)
and [session guide](../plugins/physical-engineering/references/session-guide.md).
Include baseline, failed-fit and final checkpoints from the recorded take,
with their exact run IDs, requirement revisions and hashes. Large bundles and
videos stay outside the installed package.

## Run the bounded fixture live

Preflight the runtime before the audience arrives, then start the live exercise
in a new study. Use an unused path such as
`work/physical-engineering/live-from-brief`; rehearse in a different directory.
Show the brief, build the tray and bridge, and measure the exported baseline.
Then change only the battery width, check the unchanged tray, and revise it
without relaxing the requirements. Show the failed and corrected clearances
alongside the CAD, then inspect the delivered drawing and source rebuild.
Runtime installation may be prepared; the live design is built during the exercise.

The supported transfer changes the frame X pitch to 110 mm. It does not ask the
plugin to invent a different drone, arbitrary hardware or a new subsystem. Use
the prompts below for the complete A–E exercise. Keep manufacturing, hardware
selection and bench qualification explicit as subsequent work.

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

## A. Brief

```text
Use engineer-subsystem to start a drone-module study in
[EMPTY_STUDY_DIRECTORY].

Verify that the study is new and confirm the installed plugin/ref and
Text-to-CAD version. Use the drone-module fixture with battery A:
100 × 35 × 25 mm. Keep the frame pattern at 100 × 60 mm, the board pattern
at 30 × 30 mm and clearance at 0.5–1.0 mm per side. Preserve the fixture's
other acceptance requirements. Show a compact requirements table.

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
revised clearance, unchanged mounts and the drawing/BOM changes.

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

## Inspect the complete CLI evidence and replay

After the explanation and live exercise, use the recorded replay to inspect
specific prompts, tool results, waits, errors and artifact observations. Its
full timeline provides the execution evidence behind the short visual story.
The 6–8 minute teaching cut is a compressed event replay; it is distinct from
the two-minute narrated CAD animation.

The reference recording method captures actual `codex exec --json` processes,
exact operator prompts, receipt timestamps and newly generated CAD outputs.
Use the [recording tools](../tests/physical-engineering/support/README.md).
When making a new take, start before the installed-version proof and stop after
the final evidence trace. The replay remains labeled **Recorded Codex CLI event
replay** throughout. Every CAD image must be generated from that take's known
model and observed/hash-recorded during the take. The later animation's new
camera angles and explanatory sections do not become observations in that take.

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

## Media, captions and evidence

Keep the animation's render/audio receipts and source-asset manifest separate
from the original take's capture and replay receipts. A new voice or camera
does not update the recorded plugin's qualification. Use the selected delivery
index for actual final filenames; do not link to planned video or HTML outputs.

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
