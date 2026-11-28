# Whoop 65 with revised guards

Open **viewer.html** for the assembled aircraft, exploded stack, isolated flight board and underside. Drag to orbit and scroll to zoom. Keep the delivery together so the viewer can open the parts workbook and STEP file.

| File | Contents |
| --- | --- |
| assembly.step / assembly.glb | Inspectable CAD assembly and its colored display mesh |
| components/ | Individual installed-coordinate STEP files, including controller and camera board references |
| print/ | Bed-oriented frame, snap dock, canopy and two latch-fit coupon meshes, in millimeters |
| bom.xlsx / bom.csv | Formatted parts workbook and machine-readable purchasing and print list |
| boards.md | Board selection, electrical interfaces and limits of the model |
| measurements.json | Actual exported-geometry measurements and file fingerprints |
| manifest.json | SHA-256 inventory, runtime versions and any baseline comparison |
| source/ | Editable geometry, catalog, checker, viewer and pinned runtime requirements |

## Assemble on the bench

Print the two latch coupons first and check that the tabs seat, retain and release by hand. Inspect the resulting print dimensions before printing the frame, dock and canopy. Compare the actual purchased boards and motors with the CAD mounting pattern and stack dimensions. Assemble the motors and controller, retain the battery below the frame and install the camera in the removable canopy. Inspect wiring, screw engagement, propeller clearance and snap retention on the prototype. Physical fit, electrical operation and flight performance require bench and flight tests.

## Rehearse or rebuild

Verify the existing delivery with `python3 source/scripts/engineering.py assembly-verify .`. With the pinned runtime prepared, run `sh rebuild.sh /path/to/new-directory`. The script rebuilds from this delivery's source, checks the regenerated assembly, and compares its results and BOM to this baseline. A completed comparison appears in the new manifest. Use `PE_RUNTIME` to select a dedicated runtime. On a new host run `python3 source/scripts/engineering.py setup --runtime "$HOME/.cache/physical-engineering/runtime"` first. The build and viewer can then run offline.

To revise a part, copy the source directory, edit its design/geometry or design/catalog files, then run `python3 /path/to/copied-source/scripts/engineering.py assembly-build /path/to/copied-source/design /path/to/new-output`. Review any revised interfaces and update their acceptance requirements deliberately.

## Guard revision

The brief specifies 1.40 mm nominal radial wall thickness on all four guards with the existing propellers, motor spacing and guard bores. The geometry grows the outer radii. The design-specific check reopens the frame STEP and writes all four pairs of measured radii and the overall frame span to **guard-wall.json**. The stock aircraft verifier checks the swept disks and motor diagonals separately. Print and bench tests are needed to establish strength, deflection and actual clearance.
