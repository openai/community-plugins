---
name: verify-engineering-design
description: Measure a study's CAD exports against its saved requirements, preserving failed or unknown results and the source, checker and artifact provenance.
---

# Verify an engineering design

Use the installed plugin's `scripts/engineering.py verify STUDY`. Resume first;
confirm the profile, selected geometry and active requirement revision.

The installed checker stays separate from editable model source. Keep its
hashes and acceptance limits. Measurements must come from actual exported
geometry, not source parameters, prose or a generated PASS label.

For `mechanical-part`, read the [measurement contract](../../references/custom-part.md).
Check the single solid's declared dimensions and placement, envelope, volume/mass,
requested Z-axis through bores and keepouts, and STEP/STL consistency. These
checks do not infer minimum wall thickness, strength or other undeclared features.

For `drone-module`, use the [example brief](../../references/drone-brief.md).
The specialized checker also measures assembly interfaces, battery clearance,
support, straight insertion with straps removed, interference and declared planar
walls. It reads the two parts in accumulated assembly coordinates.

Missing outputs, changed checkers, unresolved features, kernel errors or
incomplete measurements must remain failed/unknown. Report the source file and
applicable tolerance for each result. Required failed/unknown checks block a
passing handoff. A geometry pass does not resolve requirements outside the
profile's measurement scope.

Check a new requirement against the existing geometry before rebuilding when
assessing a change. Preserve the genuine result; an expected failure in a demo
must never be manufactured. Review CAD views and any drawing PDF for legibility
when presenting results; automated checks do not establish visual quality.

Return a concise measured pass/fail/unknown table, evidence paths and unresolved
requirements. Digital geometry checks do not qualify manufacture or hardware.
