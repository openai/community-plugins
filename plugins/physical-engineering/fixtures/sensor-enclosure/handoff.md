# Tabletop sensor enclosure

Open **viewer.html** to rotate the enclosure, separate its layers or inspect the sensor board. The STEP assembly uses millimeters, with the center of the base footprint as its XY datum. The base rests on Z = 0.

Print **print/base.stl** and **print/lid.stl** in the supplied orientations. Reference hardware follows the **Installed** tab of **bom.xlsx**. The **Bench** tab lists supplies and tools. Consult **boards.md** for the PCB mounting pattern. Seat the board on the four inner posts, then fit the four short screws. Align the vented lid with the outer posts and use the four longer screws. Verify screw engagement and pilot fit on a sample before tightening.

**measurements.json** records measured placements, board-to-lid clearance, print and component readback, the named 3D preview meshes and BOM quantities. The printed fit, screw retention and airflow still require prototype testing.

Rebuild the editable design in a separate folder with the supplied **rebuild.sh** and the pinned CAD runtime.
