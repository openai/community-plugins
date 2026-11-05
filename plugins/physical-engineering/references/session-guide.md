# Reusable engineering-change session

The lesson is to preserve interfaces while a component changes: brief → measured
baseline → failed fit → revision → reproduced handoff. The drone is the worked
example; explain the same method with an enclosure, robot mount or equipment
fixture, without implying that this fixture's verifier supports those geometries.

| Format | Material | Outcome |
| --- | --- | --- |
| 10-minute introduction | Qualified 6–8 minute cut plus discussion | Trace a changed requirement to a measurement |
| 30-minute guided demo | Qualified baseline checkpoint and scenes C–D | Diagnose the fit failure and inspect the revised handoff |
| 60–90 minute lab | Empty study, scenes A–E, transfer exercise | Run and explain the complete workflow |

Use a new study directory for every session. A checkpoint is a labeled recovery
point; a resumed checkpoint is not a new end-to-end recording.

## A. Brief

Use `engineer-subsystem` to initialize the bundled fixture in an empty study.
Show the fixed frame/board interfaces, battery A, clearance, insertion state,
walls, density and mass target. Save the authoritative study and generated resume
summary. Show the exact installed package and CAD versions.

## B. Baseline

Build the two-part assembly. Use `verify-engineering-design` to measure its
actual exports. Show the CAD assembly, numeric report, dimensioned drawing and
BOM. Preserve sources, requirements, logs and output hashes.

## C. Changed requirement

Start a fresh Codex session and resume the study. Confirm its saved constraints.
Apply battery B without rebuilding. Verify the existing geometry and retain the
real failed result. The expected cavity is smaller than battery B; investigate
the fixture/checker if it passes.

## D. Revised handoff

Revise the design, then verify it against the same active requirements. Generate
a new handoff with editable source, assembly STEP, individual print files, drawing,
BOM, notes and reproduction command. Read the actual PDF and trace the changed
cavity to the new measurement. Frame and board interfaces remain fixed.

## E. Reproduction

Execute the delivered reproduction command in a clean destination with independent
CAD state. Compare geometric results within the recorded tolerance and retain the
new receipt. Report elapsed time, retries/interventions and unknown usage/cost
honestly. State the remaining physical fabrication/bench-test work.

## Transfer exercise

```text
Use engineer-subsystem to create a new requirement revision with a
110 × 60 mm frame mounting pattern. Keep battery B, the board interface,
envelope and process requirements unchanged. Verify the old geometry and
preserve its mounting-pattern failure. Revise and verify the design,
then regenerate the handoff. Do not edit the verifier. Show the measured
mounting-pattern change and evidence that remaining requirements hold.
Report infeasibility rather than relaxing another requirement.
```

A second facilitator should be able to run this exercise without the author's
help. Record whether that independent run actually happened. An automated
reproduction alone does not establish facilitator readiness.

## Expected evidence

| Scene | Expected observation |
| --- | --- |
| Baseline A | 35 mm battery; measured lateral clearance 0.5–1.0 mm per side |
| A geometry with B requirements | 40 mm battery does not fit the old cavity; failed fit/insertion remains recorded |
| Corrected B | New measured clearance 0.5–1.0 mm per side; frame 100 × 60 and board 30 × 30 preserved |
| Transfer | Frame 110 × 60; B clearance and other applicable requirements still pass |
| Reproduction | Same accepted geometry within tolerance from delivered source/requirements |

Recorded checkpoints must name their run, requirement revision, source digest and
result. Store large checkpoint/media bundles with the recorded release, outside
the installed package. If live tools fail, use labeled playback or checkpoints and
state that mode explicitly.
