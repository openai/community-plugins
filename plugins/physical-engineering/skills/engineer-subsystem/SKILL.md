---
name: engineer-subsystem
description: Turn a mechanical design brief into a saved CAD study, author or revise its model, and preserve requirements and failed measurements through Text-to-CAD.
---

# Engineer a subsystem

Use this workflow for a new mechanical part or the bundled drone assembly.
Read [the brief](../../references/brief-template.md) and
[compatibility](../../references/compatibility.md). Resolve the plugin root two
directories above this skill; assets must work from the installed package.

## Establish the study

1. Resolve the user's goal, interfaces, material assumptions and acceptance
   limits. Record unresolved requirements. Choose `mechanical-part` for a custom
   single-solid part with supported measurements, or `drone-module` only for the
   two-part example. Do not force a new project into the drone dimensions.
2. Use `scripts/engineering.py`. Inspect `--help` as needed. New studies need an
   empty directory. Resume existing studies with `resume STUDY`; `study.json`
   is authoritative and `context.md` is a generated summary.
3. Check Python 3.14 and `PE_RUNTIME`. If needed, explain the dependency download
   and use `setup --runtime DIR` in an authorized location. Preserve existing CAD
   installations and respect host permissions.
4. For a custom part, follow the [model and requirements contract](../../references/custom-part.md),
   then run `init STUDY --profile mechanical-part --requirements FILE`. Omitting
   the file provides a sensor plate starter. Save the functional brief and
   unresolved requirements in `src/brief.md` so handoffs retain them. Use `init STUDY --profile drone-module`
   for the [drone brief](../../references/drone-brief.md).

## Author and revise

Implement the accepted brief in the study's `src/model.py` and export source.
The generic starter constructs a plate; replace it for another shape. Keep its
export contract. Use the separately installed Text-to-CAD authoring/viewing
capabilities for CAD work. Inspect externally supplied Python before execution.

Run `build STUDY`, then use `verify-engineering-design`. Model declarations and
rendered appearance are not acceptance measurements. A property unsupported by
the checker needs a separate measurement method; retain it as unresolved.

For a custom part, save revised requirements and run
`change STUDY --requirements FILE --reason TEXT`. For the drone example, use
`--battery B` or `--frame-pitch-x 110`. Never edit the installed checker or weaken
acceptance limits to make geometry pass. A changed requirement does not update
old geometry: verify it first, retain the actual result, then revise and build.

Keep failed runs. Routine repairs within the brief need no further approval;
conflicting requirements need an explicit decision before alteration. Record
real interventions; unknown usage or cost stays unknown.

## Return

Summarize the selected profile/run, requirement revision, measured results and
next action. Show the actual CAD view when available. Use
`prepare-engineering-handoff` to regenerate deliveries. Distinguish the measured
geometry from unresolved manufacturing, structural and other physical checks.
