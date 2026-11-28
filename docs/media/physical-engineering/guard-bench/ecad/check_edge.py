"""Reproduce the two JST hold-down clearance errors using the shipped board rules."""

import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

out = Path(sys.argv[1])
board = "bench-accelerometer.kicad_pcb"
design = out / "edge-regression"
shutil.copytree(out / "design", design)
path = design / board
source = path.read_text()
pattern = r'(\(footprint "Connector_JST:[^"]+"[\s\S]*?\(at 100 )107\.625(\))'
changed, count = re.subn(pattern, r"\g<1>107.875\2", source)
if count != 1:
    raise ValueError("Could not identify the single expected J1 placement")
path.write_text(changed)
report_path = out / "edge-regression-drc.json"
result = subprocess.run(
    [
        "kicad-cli",
        "pcb",
        "drc",
        "--all-track-errors",
        "--schematic-parity",
        "--severity-all",
        "--exit-code-violations",
        "--format",
        "json",
        "-o",
        str(report_path),
        str(path),
    ]
)
report = json.loads(report_path.read_bytes())
errors = [
    row
    for row in report["violations"]
    if row["type"] == "copper_edge_clearance"
    and any(
        "Pad MP" in v["description"] and "J1" in v["description"] for v in row["items"]
    )
]
if result.returncode == 0 or len(errors) != 2:
    raise ValueError("Expected both J1 hold-down pads to fail the board-edge rule")
# Use the actual final footprint and checked straight board-edge location.
footprint = next(
    f for f in source.split("\n\t(footprint ")[1:] if f.startswith('"Connector_JST:')
)
placed = re.search(r"\(at ([\d.-]+) ([\d.-]+)\)", footprint)
pads = re.findall(
    r'\(pad "MP" smd roundrect\s+\(at ([\d.-]+) ([\d.-]+)\)\s+\(size ([\d.]+) ([\d.]+)\)',
    footprint,
)
if len(pads) != 2:
    raise ValueError("Expected two J1 hold-down pads")
board_edge_y = next(
    it["pos"]["y"] for it in errors[0]["items"] if "Edge.Cuts" in it["description"]
)
clearances = [
    round(board_edge_y - (float(placed[2]) + float(dy) + float(h) / 2), 5)
    for _, dy, _, h in pads
]
if clearances != [0.60, 0.60]:
    raise ValueError(f"Final connector hold-down clearances changed to {clearances}")
for row in errors:
    if not re.search(
        r"edge clearance 0\.5000 mm; actual 0\.3500 mm", row["description"]
    ):
        raise ValueError(row["description"])
(out / "edge-regression.json").write_text(
    json.dumps(
        {
            "returncode": result.returncode,
            "failed_J1_hold_downs": 2,
            "actual_mm": 0.35,
            "minimum_mm": 0.50,
            "source_change": "J1 center Y 107.625 to 107.875 mm",
            "final_J1_clearance_mm": min(clearances),
            "other_DRC_violations_in_modified_copy": len(report["violations"])
            - len(errors),
        },
        indent=2,
    )
    + "\n"
)
