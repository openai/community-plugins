---
name: verify-engineering-design
description: Measure the selected drone-module model and exports against the active saved requirements, preserving real failed or unknown results and their provenance.
---

# Verify an engineering design

Use the installed plugin's `scripts/engineering.py verify STUDY`.
Read [the brief](../../references/brief-template.md). Confirm the study's selected
geometry and active requirement revision before measuring.

The checker and fixture live outside generated model source. Preserve their
hashes and acceptance limits. Verification reads actual geometry/export content;
a dimension declared in source or printed in a report is not a measurement.

## Required evidence

- Unique expected parts, valid solids and accumulated assembly placements.
- Actual frame/board hole diameters and positions.
- Battery lateral clearance, support contact and straight insertion sweep in the
  declared released-retention service state.
- No unintended part/component overlaps and the fixture's declared planar walls.
- Volume-based polymer estimate at the recorded illustrative density.
- Export identity, units and consistency with the selected source/run.

Unresolved features, missing outputs, changed checker, kernel errors or incomplete
measurements are failed/unknown, never pass. Report which geometry/file supplied
each measurement and the applicable tolerance. Do not turn sampled mesh thickness
into a universal exact minimum-wall claim.

A new battery requirement may deliberately be checked against old geometry. Keep
that failed run and report the actual cavity/clearance. Do not rebuild until the
user's requested failure demonstration is recorded.

Review the actual CAD view and drawing PDF for legibility and dimension placement
when presenting a handoff. Automated checks do not establish readable visuals.

Return a concise measured pass/fail/unknown table and the evidence paths. A digital
geometry pass does not qualify printing, strength, electronics or flight.
