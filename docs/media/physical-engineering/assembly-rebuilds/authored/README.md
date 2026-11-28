# Replay the two authored design revisions

The film starts with two supplied references. It then changes the quad’s four guard walls and adds a bench-mounting interface to the enclosure. The [Whoop patch](whoop-65.patch) and [enclosure patch](sensor-enclosure.patch) contain the exact editable recipes used for the reviewed builds. Neither patch changes the installed plugin. The [original rebuild evidence](../../../../physical-engineering-assembly-rebuilds.md) reports measurements for the supplied references.

## Try the same brief in Codex

Select Physical Engineering in a writable local project and paste this prompt. A fresh authoring run may choose different CAD details within the drawing.

> Use Physical Engineering in this local Codex project. Create the next unused physical-demo-NNN folder and show it is empty. Initialize editable recipes from the plugin’s supplied Whoop 65 and tabletop sensor references. Author the revisions in this folder using the installed references as your starting point. Identify what each reference provides.

> For the indoor quad, change all four radial guard walls to 1.40 mm. Keep the inner bores, propellers and 65 mm motor diagonal. Update the frame’s BOM specification. Add a design-specific check that measures the inner and outer cylinders on the exported STEP. Show the impact on overall width and swept propeller clearance.

> For the sensor housing, design two external mounting ears joined to the base. The drawing calls for vertical 3.40 mm through holes at X = −32 and +32 mm, Y = 0. Keep the board and removable lid in place. Document the mounting hardware. Make a deliberate 60 mm pitch candidate; show which general checks pass and have a drawing check refuse that export. Fix the geometry to the drawing.

> Use the plugin’s build and verify workflows. Keep each handoff self-contained. Run only its own drawing check on its own exported STEP during every build and rebuild. Rebuild the two accepted handoffs from their delivered source in fresh folders. Open the revised model in Codex and link the workbooks, measured results and rebuild receipts.

## Rebuild the reviewed designs from a checkout

Run the commands from the repository root. Use the prepared CAD environment, or follow the [runtime setup instructions](../../../../physical-engineering-assembly-rebuilds.md#reproduce-from-a-checkout) first. The patches apply to editable copies made by this checkout’s `assembly-init` command.

```sh
set -eu
export PE_RUNTIME="${PE_RUNTIME:-$HOME/.cache/physical-engineering/runtime}"
pe_cli="$PWD/plugins/physical-engineering/scripts/engineering.py"
patches="$PWD/docs/media/physical-engineering/assembly-rebuilds/authored"
run=$(mktemp -d)
for design in whoop-65 sensor-enclosure; do
  recipe="$run/$design-recipe"
  first="$run/$design-accepted"
  second="$run/$design-rebuilt"
  python3 "$pe_cli" assembly-init "$recipe" --from "$design"
  git -C "$recipe" apply --no-index --check "$patches/$design.patch"
  git -C "$recipe" apply --no-index "$patches/$design.patch"
  python3 "$pe_cli" assembly-build "$recipe" "$first"
  python3 "$pe_cli" assembly-verify "$first"
  sh "$first/rebuild.sh" "$second"
  python3 "$second/source/scripts/engineering.py" assembly-verify "$second"
done
for stage in accepted rebuilt; do
  housing="$run/sensor-enclosure-$stage"
  "$PE_RUNTIME/bin/python" "$housing/source/design/geometry/validation/verify_mounts.py" \
    "$housing" --report "$run/$stage-mounts.json"
done
printf 'Handoffs and reports: %s\n' "$run"
```

Each output contains `viewer.html`, `assembly.step`, `components/`, `print/`, `bom.xlsx`, `bom.csv` and `measurements.json`. The quad’s extra drawing report is `guard-wall.json`. The housing inspection above checks its standalone base STEP and the installed base in the assembly. Each rebuilt `manifest.json` identifies the matching baseline and comparison result.

| Reviewed result | Measured on the exports |
| --- | --- |
| Revised quad | Four 1.40 mm walls with 16.30 mm inner radii. Frame width grew from 80.56194 to 81.36194 mm. Both motor diagonals stayed 65.00 mm and all four swept disks retained 0.80 mm clearance. All 37 standard checks passed. |
| First enclosure candidate | The diagnostic export passed all 19 standard checks. The separate drawing check read 60.00 mm between X = −30.00 and +30.00 mm and the normal build refused delivery. |
| Corrected housing | Two vertical Ø3.40 mm through holes at X = −32.00 and +32.00 mm, Y = 0.00 mm, through 3.20 mm ears in one connected base. Board-to-lid clearance stayed 6.20 mm. All 19 standard checks passed. The Bench sheet adds two site-supplied M3 mounting screws. |
| Fresh rebuilds | The changed dimensions remeasured as above. Both designs reproduced their STL files and both BOM formats byte for byte. The assembly STEP bytes differed across runs, and the geometric checks passed again. |

To replay the refusal, continue in the same shell. This makes a separate candidate recipe and changes only its modeled pitch. The drawing stays fixed at 64 mm.

```sh
bad="$run/60mm-candidate"
cp -R "$run/sensor-enclosure-recipe" "$bad"
python3 - "$bad/geometry/build.py" <<'PYTHON'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text()
old = "BENCH_BORE_PITCH_MM = 64.0"
assert s.count(old) == 1
p.write_text(s.replace(old, "BENCH_BORE_PITCH_MM = 60.0"))
PYTHON
if python3 "$pe_cli" assembly-build "$bad" "$run/refused" >"$run/refusal.log" 2>&1; then
  echo 'Unexpected acceptance of the 60 mm candidate' >&2
  exit 1
fi
grep -F 'measured pitch 60.0 mm' "$run/refusal.log"
test ! -e "$run/refused"
```

These checks measure exported CAD. Actual latch fit, guard deflection, fastener engagement and flight require bench or prototype testing.
