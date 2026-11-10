"""Editable sensor plate starter. Replace this source for another single part.

The checker reads exports against the saved requirements, never this design.
This starter understands one dimensions check and vertical through-hole groups.
Keepouts and volume limits are acceptance conditions, not geometry generators.
"""
from pathlib import Path
import json
import sys

import build123d as bd
from cad import model

HERE = Path(__file__).resolve().parent


def design():
    return json.loads((HERE / "design.json").read_text())


@model
def component():
    config = design()
    overall = next(check for check in config["checks"] if check["type"] == "dimensions")
    size, center = overall["size_mm"], overall["center_mm"]
    # A rounded outline makes this reusable bracket/fixture starter legible.
    radius = min(2., min(size[:2]) / 8)
    part = bd.Pos(center[0], center[1], center[2]-size[2]/2) * bd.extrude(
        bd.RectangleRounded(size[0], size[1], radius), amount=size[2])
    for check in config["checks"]:
        if check["type"] == "through_holes":
            bottom, top = check["z_span_mm"]
            for x, y in check["centers_xy_mm"]:
                part -= bd.Pos(x, y, (bottom+top)/2) * bd.Cylinder(check["diameter_mm"]/2, top-bottom+2)
    part.label = config["part_name"]
    part.color = "#147D92"
    return part


if __name__ == "__main__":
    identifiers = {"component": component().id}
    if len(sys.argv) > 1:
        output = Path(sys.argv[1]).resolve()
        output.mkdir(parents=True, exist_ok=True)
        (output / "documents.json").write_text(json.dumps(identifiers, indent=2) + "\n")
    print("PE_DOCUMENTS " + json.dumps(identifiers))
