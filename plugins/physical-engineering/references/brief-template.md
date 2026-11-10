# Engineering brief

Record requirements before authoring geometry. Use the `mechanical-part` profile
for a single solid with supported measurements, or the `drone-module` profile for
its specialized assembly. The [custom-part guide](custom-part.md) defines the
first contract; the [drone brief](drone-brief.md) defines the example.

| Field | Record |
| --- | --- |
| Purpose | What the part must do and where it is installed |
| Interfaces | Mating parts, axes, positions, hole sizes and permitted movement |
| Shape and space | Required dimensions, datum, maximum envelope and keepouts |
| Materials/process | Material assumption, density, process and unresolved manufacturing choices |
| Acceptance | Each measured property, limit, units and tolerance |
| Unmeasured requirements | Strength, minimum wall, retention, thermal or other requirements needing a separate method |
| Deliverables | Editable source, STEP/STL, measured report, BOM and rebuild instructions; drawings where supported |
| Change | What changed, why, and which requirements remain fixed |

Translate supported requirements into the profile's JSON contract. Keep the
human brief in `src/brief.md` so source snapshots and handoffs retain it. Do not silently drop unsupported requirements
or label a geometry pass as whole-design acceptance. If requirements conflict,
record the conflict and obtain a decision before changing acceptance limits.

Each accepted change gets a new revision and a reason through the CLI. Measure
the existing geometry first, preserve the result, then revise source and rebuild.
