# Build and rehearse a multi-part assembly

Use this workflow for an enclosure, fixture, or other assembly with multiple fitted parts, board interfaces and a bill of materials. The small sensor enclosure is an editable starter. Start from the Whoop 65 recipe when revising that aircraft.

## First run

```sh
python3 "$PE_PLUGIN/scripts/engineering.py" setup --runtime "${PE_RUNTIME:-$HOME/.cache/physical-engineering/runtime}"
python3 "$PE_PLUGIN/scripts/engineering.py" assembly-init "$PWD/my-design"
python3 "$PE_PLUGIN/scripts/engineering.py" assembly-build "$PWD/my-design" "$PWD/first-take"
python3 "$PE_PLUGIN/scripts/engineering.py" assembly-verify "$PWD/first-take"
sh "$PWD/first-take/rebuild.sh" "$PWD/rehearsal"
```

Run setup once per pinned runtime. Use a new or empty output directory for each build. From a clean delivery, **viewer.html** opens the assembly, exploded view, isolated component and underside. **bom.xlsx** opens the formatted parts and support sheets. The generated files work locally after setup.

## Author a design

| Input | What the designer supplies |
| --- | --- |
| assembly.json | Design ID, display title, component to isolate, exploded-view offsets, BOM worksheet names and inspection requirements |
| geometry/build.py | Editable CAD generator. Its single argument is the output directory. Export a named assembly.step and matching assembly.glb, installed-coordinate component STEP files under components/, and bed-oriented STL files under print/. Use unique node names among siblings. The viewer reads named groups directly below the single GLB root. |
| catalog/parts.csv | Stable ID, scope, group, part, fitted quantity and unit, purchasing or make instructions, specification and source. Use the first configured scope for fitted items. Include hardware, connectors, wiring and applied materials. Classify supporting supplies in the other scope. A local source link names a file shipped in the handoff. |
| boards.md and optional boards/ | Record the board revision, mounting datums, connectors and service space. Document which details come from a manufacturer, a measurement or an assumption. Place supplied native ECAD sources in boards/ to carry them into the delivery. Electrical fabrication readiness requires its own design checks. |
| handoff.md | Assembly notes, open physical tests, and instructions specific to this design. This becomes the delivered README. |

Copy the example with `assembly-init` and edit it in the workspace. The recipe's geometry script is executable Python. Review externally supplied scripts before use. All of a design's geometry modules and source assets belong in its geometry directory.

## Describe what to measure

For the general inspector, declare the actual STEP occurrence paths for each installed BOM ID under `inspection.parts`. Declare the world-coordinate bounding-box center in millimeters, the associated component STEP or print path where applicable, and measured gaps between named parts. A component pair can have the correct size while being in the wrong location. Define placement datums and limits from the brief before checking the exports. Use `inspection.unmodeled` to give reasons for installed items represented only in the BOM. The sensor enclosure recipe gives a complete example.

The Whoop 65 recipe selects an aircraft-specific inspector for rotor sweep, motor mounts, controller mounting, latch engagement and battery support. A design with different geometry can use the general inspector and declare its own parts and limits. Additional required properties beyond those checks should remain explicitly unresolved until a suitable measurement exists.

## Reproduce the result

The build snapshots the design and tools into **source/**, checks the actual exports, reopens the workbook, and inventories the delivered files by SHA-256. A failed step leaves the last completed delivery intact. To remeasure an archived delivery using its own checker, run `python3 source/scripts/engineering.py assembly-verify .` from the delivery directory.

Use **rebuild.sh** to generate a separate output from the delivered source and compare measured STEP results, print meshes, colored assembly, viewer, still image, board guide and BOM. The new manifest contains the baseline manifest hash and comparison result. A physical prototype, electrical test or flight is additional evidence.
