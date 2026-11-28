"""Cross-check KiCad's independent manufacturing, placement, BOM and netlist exports."""

import csv
from pathlib import Path
import re
from xml.etree import ElementTree


def rows(path):
    with path.open() as stream:
        return list(csv.DictReader(stream))


def verify_board(board: Path, procurement: Path):
    sources = {}
    for row in rows(procurement):
        refs = [ref.strip() for ref in row["References"].split(",")]
        if len(refs) != int(row["Qty per assembled PCB"]):
            raise ValueError(
                "Procurement quantity disagrees with reference designators"
            )
        for reference in refs:
            if reference in sources:
                raise ValueError(f"Duplicate procurement designator {reference}")
            sources[reference] = row["MPN"]
    parts = {row["Reference"]: row["MPN"] for row in rows(board / "kicad-bom.csv")}
    positions = {row["Ref"] for row in rows(board / "placements.csv")}
    if sources != parts or positions != parts.keys() or len(parts) != 7:
        raise ValueError("Placed designators or MPNs disagree with the sourced PCB BOM")

    nets = {
        item.get("name"): {
            (node.get("ref"), node.get("pin")) for node in item.findall("node")
        }
        for item in ElementTree.parse(board / "netlist.xml").findall(".//nets/net")
    }
    for net, pins in {
        "/I2C_SDA": {("J1", "3"), ("U1", "6"), ("R2", "2")},
        "/I2C_SCL": {("J1", "4"), ("U1", "4"), ("R1", "2")},
        "/DRDY": {("U1", "11"), ("TP1", "1")},
    }.items():
        if nets.get(net) != pins:
            raise ValueError("Unexpected native net connectivity for " + net)
    gerbers = sorted(p for p in (board / "gerbers").iterdir() if p.suffix != ".gbrjob")
    if len(gerbers) != 9 or any(
        "M02*" not in p.read_text() or "%MOMM*%" not in p.read_text() for p in gerbers
    ):
        raise ValueError("Expected nine complete metric Gerber layer exports")
    drills = list((board / "drill").glob("*.drl"))
    if len(drills) != 2:
        raise ValueError("Expected separate plated and non-plated drill files")
    mount = next(d for d in drills if d.name.endswith("-NPTH.drl")).read_text()
    holes = sorted(
        (float(x), float(y))
        for x, y in re.findall(r"^X([\d.]+)Y([\d.]+)$", mount, re.M)
    )
    if "T1C2.300" not in mount or holes != [(3, 3), (3, 19), (31, 3), (31, 19)]:
        raise ValueError(
            "NPTH drill differs from four 2.30 mm mounting holes on 28 × 16 mm pitch"
        )
    return {
        "placements": len(positions),
        "unique_fitted_MPNs": len(set(parts.values())),
        "plotted_layers": len(gerbers),
        "drill_files": len(drills),
        "NPTH_mount_holes": len(holes),
        "netlist_connections": "SDA, SCL and DRDY matched",
    }
