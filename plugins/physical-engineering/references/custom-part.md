# Custom mechanical parts

Use `mechanical-part` for a single solid whose acceptance can be expressed as
measured bounds, position, Z-axis through bores, box keepouts, volume and mass.
The source may describe a different shape from the sensor plate. The checker
uses STEP/STL readback and never imports that source to determine acceptance.

## Start from your brief

Prepare a requirements file using the contract below, then run:

```sh
python3.14 "$PE_PLUGIN/scripts/engineering.py" init "$PE_STUDY" \
  --profile mechanical-part --requirements /absolute/path/to/requirements.json
```

The default source is the [sensor plate starter](../fixtures/sensor-plate/model.py).
It uses the dimensions check for a rounded plate and subtracts declared bores.
For a spacer, bracket or other geometry, author a replacement `src/model.py`
using Text-to-CAD/build123d. Keepouts and volume limits are acceptance checks;
they do not generate cutouts or invent a suitable shape.

Inspect outside source before executing it. Run `build`, inspect the real CAD,
then `verify`. Save the human brief and unresolved requirements in `src/brief.md`, which is
snapshotted with source and included in the handoff. Unsupported requirements stay
explicitly unresolved;
never silently map a strength or minimum-wall requirement to bounding dimensions.

## Requirements contract

Use the [complete sensor plate requirements](../fixtures/sensor-plate/requirements.json)
as a runnable starting point. The schema rejects unknown fields, unsupported
checks, duplicate identifiers, nonfinite values and invalid tolerances.

| Field | Meaning |
| --- | --- |
| `schema_version` | Integer `1` |
| `profile_id` | `mechanical-part-v1` |
| `units` | `mm` |
| `part_name` | Lowercase letter followed by up to 47 lowercase letters, digits, `_` or `-`; also the solid label and export basename |
| `envelope_max_mm` | Three positive maximum axis-aligned extents in X/Y/Z; position is checked separately |
| `density_g_per_cm3` | Positive uniform solid-material density used for the mass estimate |
| `mass_max_g` | Positive maximum solid-material mass |
| `checks` | One to 100 named checks; exactly one `dimensions` check required |

Each check has a unique safe `id` and a `type`. IDs `part_solid`, `envelope`,
`mass` and `hole_feature_count` are reserved. Optional `revision` is assigned by
the workflow; omit it in authored briefs.

| Type | Required fields | Actual measurement |
| --- | --- | --- |
| `dimensions` | `size_mm: [x,y,z]`, `center_mm: [x,y,z]`, `tolerance_mm` | World-space BRep bounds and their center. Not minimum local thickness. |
| `through_holes` | `centers_xy_mm: [[x,y],…]`, `diameter_mm`, `z_span_mm: [bottom,top]`, `tolerance_mm` | Complete inward cylindrical bores along Z spanning the full declared part height, with the declared location and diameter. |
| `keepout` | `size_mm`, `center_mm`, `max_overlap_mm3` | BRep intersection volume with the declared box. |
| `volume` | `min_mm3`, `max_mm3` | Solid BRep volume within a positive ordered range. |

Dimension/position tolerances are positive and at most 0.5 mm; they cannot erase
a required dimension, bore diameter or span. Keepout allowance is between 0 and
0.001 mm³ for numerical noise. Requirements must be feasible within the envelope.
Hole positions must be distinct across groups; each declared bore spans the full
Z extent of the dimensions check. When holes are declared, every
recognized bore must be accounted for; angled, counterbored, threaded, partial
or blind holes need another measurement method. Do not describe them as verified
through bores. When no bore check is declared, hole geometry is not accepted by
an interface check.

The checker also requires one valid positive-volume solid, checks mass and
envelope, and compares the actual STL topology, bounds, volume and surface
against STEP. Individual STEP must match assembly STEP. Mesh tolerance is 0.02 mm
and the mesh-volume relative tolerance is 0.005; those numerical comparisons do
not establish fabrication tolerances.

## Source and export contract

The editable study contains `src/design.json`, `model.py` and `exports.py`.
`build` refreshes `design.json` from the active requirements, then snapshots and
executes the source. `verify` uses the prior selected geometry; it never silently
rebuilds it to satisfy new requirements.

The [model starter](../fixtures/sensor-plate/model.py) exposes `design()` and a
Text-to-CAD `@model` function `component()`. Preserve these names when using the
included [export script](../fixtures/sensor-plate/exports.py). The part must be
one solid labeled with `part_name`; nested assembly placements are accumulated.
`cad run model.py OUTPUT_DIRECTORY` creates the document and optional UUID map
`documents.json`. `cad run exports.py OUTPUT_DIRECTORY` must produce:

- `assembly.step`, containing the single named part;
- `<part_name>.step`, the same measured geometry;
- `<part_name>.stl`, exported at the declared mesh precision.

Model source is executable Python, not a restricted input language. The plugin's
fixed verifier checks the generated artifacts. Adding a new check type means
changing and testing the installed verifier in a new plugin version, not writing
a model-local checker that certifies its own output.

## Requirement changes and handoff

For a plate exercise, copy the requirements and change the four mount X
coordinates from ±30 to ±32 mm. Keep size, Y pitch and other requirements fixed:

```sh
python3.14 "$PE_PLUGIN/scripts/engineering.py" change "$PE_STUDY" \
  --requirements /absolute/path/to/revised-requirements.json \
  --reason "Move the mount pitch from 60 to 64 mm"
python3.14 "$PE_PLUGIN/scripts/engineering.py" verify "$PE_STUDY"
# Inspect and preserve the old mounting-pattern failure.
python3.14 "$PE_PLUGIN/scripts/engineering.py" build "$PE_STUDY"
python3.14 "$PE_PLUGIN/scripts/engineering.py" handoff "$PE_STUDY"
```

The starter responds to revised dimensions/bores. A custom model may need a
source edit. A changed requirement must not be implemented by editing history
or the checker. Review accepted changes against the original functional goal;
a valid schema alone does not make relaxed requirements appropriate.

The generic handoff contains editable source, requirements, STEP/STL, measured
checks/report, a one-part BOM, runtime scripts/pins/notices and `reproduce.sh`.
It does not include an automatically qualified engineering drawing or purchased
hardware specification. Rebuild in a new destination and compare the receipt. The comparison checks
actual named STEP solids in world coordinates, requiring symmetric difference
at most 0.001 mm³ and bounds drift at most 0.00001 mm, alongside scalar
measurements. This catches shape changes hidden by identical volume and bounds;
keep manufacturing, load, fit and installation validation separate.
