# Demo the Whoop 65

> Use $engineer-subsystem to build the complete Whoop 65 in a new folder. Show the assembled quad and exploded view, open the flight board, and give me the formatted BOM and measurement report.

Use the installed plugin directory containing this guide as `PE_PLUGIN`. The demo uses a 65 mm diagonal guarded frame, a snap dock and canopy, a single-cell battery, a purchased 5-in-1 control board and analog camera. These two board models describe physical interfaces and placement. The reference hardware supplies the flight electronics.

## Run

Prepare the pinned runtime once on the supported macOS arm64 host with Python 3.14 and uv. The default runtime path works across Codex sessions. To use an existing installation, set `PE_RUNTIME` to its directory.

```sh
python3 "$PE_PLUGIN/scripts/engineering.py" setup --runtime "${PE_RUNTIME:-$HOME/.cache/physical-engineering/runtime}"
python3 "$PE_PLUGIN/scripts/engineering.py" whoop-demo "$PWD/Whoop 65"
```

A prepared runtime builds locally without network access. Choose a new or empty destination for each take. The command checks the runtime pins, assembles the editable CAD, reads the exported geometry back, renders the viewer and validates the workbook before publishing the directory. An occupied destination stays intact.

## Present

1. Open **viewer.html**. Orbit the assembled aircraft, switch to **Exploded**, then **Flight board**. **Underside** shows the battery retention. On hosts without WebGL 2 the viewer displays the rendered assembly image.
2. Open **bom.xlsx** from **Parts list**. The **Aircraft** sheet covers fitted quantities, sourcing, included accessories and printed components. The **Bench** sheet covers materials and tools. Local links open the printed parts and CAD references alongside the workbook.
3. Show **components/flight-controller.step** and **boards.md** for the board stack and interfaces. **measurements.json** records what the checker measured. **print/** contains the frame, dock, canopy and two latch-test pieces.

## Rehearse a repeat run

```sh
python3 "$PE_PLUGIN/scripts/engineering.py" assembly-verify "$PWD/Whoop 65"
sh "$PWD/Whoop 65/rebuild.sh" "$PWD/Whoop 65 rehearsal"
```

The second command runs from delivered source and compares the results with the first build. See **manifest.json** in the rehearsal folder for the comparison and SHA-256 inventory. If an input or output changed, inspect the reported file or check, then rebuild in a fresh directory. Keep a verified handoff on the demo machine for immediate presentation.
