---
name: engineer-subsystem
description: Create or resume the bundled synthetic drone-module study, preserve its requirements, and build or revise the battery tray and electronics bridge through Text-to-CAD.
---

# Engineer a subsystem

Use this skill for the supported drone-module fixture and its battery or frame-pitch
changes. Read [the brief](../../references/brief-template.md) and
[compatibility](../../references/compatibility.md). Runtime assets resolve from
this installed plugin, not the repository working directory.

## Start or resume

1. Resolve the plugin root two directories above this skill directory. Use its
   `scripts/engineering.py` CLI. Inspect `--help` when command details differ.
2. Use the user's selected study directory. For a new study, require an empty
   directory; for an existing study, use `resume STUDY` and read `study.json`.
   `context.md` is a generated aid, never a replacement source of truth.
3. Check Python 3.14 and the selected `PE_RUNTIME`. If necessary, use
   `setup --runtime DIR` to prepare a separate pinned runtime in the authorized
   location. Explain downloads before invoking setup. Preserve existing CAD
   installations and respect host permissions; never weaken policy to start it.
4. For a new study, run `init STUDY`. Show fixed interfaces, battery dimensions,
   clearance, service state, wall profile, density and mass limit before building.

## Build or change

Run `build STUDY`, then use `verify-engineering-design`. Model scripts execute
code; inspect imported or externally supplied source before running it. Use only
this study's selected files and actual returned document IDs.

A requirement change is explicit: `change STUDY --battery B --reason TEXT` or
`change STUDY --frame-pitch-x 110 --reason TEXT`. Preserve prior requirements
and decisions. Do not edit the verifier or relax requirements to obtain a pass.

For the battery-change exercise, keep the existing geometry first, run
`verify STUDY`, and show its genuine failed fit. Only then build the revised
geometry and verify again. An unexpected pass is a diagnostic issue, not
permission to invent a failed measurement.

Retain failed runs. If requirements conflict, state the conflict and obtain a
decision about the requirements before changing them. Routine repairs within the
accepted brief do not need another approval. Record real interventions/retries;
unknown cost or usage stays unknown.

## Return

Summarize the selected run, requirement revision, measured results and next
action. Show the actual upstream CAD view when available. A build is not an
export refresh; use `prepare-engineering-handoff` to regenerate deliveries.
Do not claim physical, structural, electrical or flight qualification.
