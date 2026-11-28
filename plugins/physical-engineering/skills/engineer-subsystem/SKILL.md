---
name: engineer-subsystem
description: Design and verify mechanical parts or complete multi-part CAD assemblies with board interfaces, printable parts and a formatted BOM, including the Whoop 65 reference demo.
---

# Engineer a subsystem

## Design a complete assembly

Resolve the installed plugin root two directories above this skill. For a multi-part product with purchased or custom components, start a local design with `scripts/engineering.py assembly-init RECIPE_DIR`. Use the [editable assembly contract](../../references/assembly-designs.md). Author the design geometry, board references, sourced catalog and measurable interfaces for the user's brief. Run `assembly-build RECIPE_DIR NEW_DIRECTORY`, inspect the generated viewer, and return the actual CAD, BOM and measurements. Identify manufacturer models, measured interfaces and unverified assumptions in the handoff. Package any supplied native board files with their provenance.

For the complete reference tiny whoop, TinyWhoop or Whoop 65, run `scripts/engineering.py whoop-demo NEW_DIRECTORY` directly. Choose the installed package exposing this command when multiple marketplaces are enabled. The [Whoop 65 guide](../../references/whoop-65.md) covers setup and the live demo. Use `assembly-init RECIPE_DIR --from whoop-65` when the brief asks to revise this aircraft. Select measurement methods that apply to the new design.

## Design a single part or the two-part module

Use the following study workflow for a custom mechanical part or the two-part drone module.
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
