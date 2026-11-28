#!/bin/sh
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
out=${1:?Pass a new absolute output directory}
test ! -e "$out" || { echo "Output path already exists" >&2; exit 2; }
mkdir -p "$out/design" "$out/gerbers" "$out/drill"
cp -R "$here/design/." "$out/design"
python3 -m zipfile -e "$here/models.zip" "$out"
# The stock KiCad libraries and pcbnew Python module must match kicad-cli.
pcb_python=${KICAD_PYTHON:-python3}
board="$out/design/bench-accelerometer.kicad_pcb"
schematic="$out/design/bench-accelerometer.kicad_sch"
kicad-cli version > "$out/kicad-version.txt"
kicad-cli sch erc --severity-all --exit-code-violations --format json -o "$out/erc.json" "$schematic"
kicad-cli pcb drc --all-track-errors --schematic-parity --severity-all --exit-code-violations --format json -o "$out/drc.json" "$board"
kicad-cli sch export pdf -o "$out/schematic.pdf" "$schematic"
"$pcb_python" "$here/trace.py" "$board" "$out/routed-trace.json"
kicad-cli sch export netlist --format kicadxml -o "$out/netlist.xml" "$schematic"
kicad-cli sch export bom --fields 'Reference,Value,Footprint,MPN,Manufacturer,Datasheet' --labels 'Reference,Value,Footprint,MPN,Manufacturer,Datasheet' -o "$out/kicad-bom.csv" "$schematic"
kicad-cli pcb export gerbers -l F.Cu,B.Cu,F.Mask,B.Mask,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,Edge.Cuts --use-drill-file-origin -o "$out/gerbers/" "$board"
kicad-cli pcb export drill --drill-origin plot --excellon-units mm --excellon-separate-th --generate-map --map-format pdf -o "$out/drill/" "$board"
kicad-cli pcb export pos --units mm --format csv --use-drill-file-origin --smd-only -o "$out/placements.csv" "$board"
kicad-cli pcb export step --force --user-origin 100x100mm --include-pads --include-tracks --cut-vias-in-body -o "$out/bench-accelerometer.step" "$board"
kicad-cli pcb export step --force --user-origin 100x100mm -o "$out/bench-accelerometer-mechanical.step" "$board"

python3 "$here/check_edge.py" "$out"
