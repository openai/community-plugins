# Bench accelerometer

Secure the printed base to the unpowered guard-test fixture, seat the custom PCB on its four posts and install the board screws. Orient J1 toward the rounded cable window at the front. Fit the removable lid, then pass the four-conductor cable through the opening. Check the actual host wiring against the released board schematic before applying power.

Open the [interactive model](viewer.html), [bill of materials](bom.xlsx) and [board integration guide](boards.md). The [mechanical integration report](integration.json) checks the installed PCB, lid, posts and external cable approach against reimported STEP geometry. The board and case each have their own design datums. Print the base and lid in the bed orientation supplied under **print/**.

Run the delivered build in a fresh output directory using the prepared CAD runtime.

```sh
sh rebuild.sh ../rebuilt-recorder
python3 source/scripts/engineering.py assembly-verify ../rebuilt-recorder
```

Prototype checks include printed screw and ear fit, connector insertion and strain relief, sensor identity on the I²C bus, axis orientation and repeatability after installation. A repeated guard displacement is a geometric input. Measuring the plate’s dynamic response requires the physical fixture, calibrated acquisition and a test run.
