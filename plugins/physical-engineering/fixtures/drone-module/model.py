"""Editable two-part civilian drone module. All geometry is in millimetres.

design.json is a design input snapshot, not the verification requirements. To
evaluate an old design against a new battery, retain this source and design.json.
"""
from pathlib import Path
import json
import math
import sys

import build123d as bd
from cad import model

HERE = Path(__file__).resolve().parent


def design():
    config = json.loads((HERE / "design.json").read_text())
    battery = config["battery"]["envelopes_mm"][config["battery"]["selected"]]
    pitch = config["frame"]["pitch_mm"]
    if config["units"] != "mm" or not all(math.isfinite(v) and v > 0 for v in (*battery, *pitch)):
        raise ValueError("Expected finite positive millimetre design inputs")
    return config


def block(x, y, z, at):
    return bd.Pos(*at) * bd.Box(x, y, z)


@model
def tray():
    config = design()
    width = config["battery"]["envelopes_mm"][config["battery"]["selected"]][1]
    pitch_x, pitch_y = config["frame"]["pitch_mm"]
    cavity = width + 1.5
    part = block(106, width + 5.5, 3, (0, 0, 1.5))
    for x in (-pitch_x / 2, pitch_x / 2):
        part += block(12, pitch_y + 12, 3, (x, 0, 1.5))
    for x in (-18, 18):
        part += block(10, 64, 3, (x, 0, 1.5))
    for y in (-(cavity / 2 + 1), cavity / 2 + 1):
        part += block(106, 2, 7, (0, y, 6.5))
    part += block(2, cavity, 7, (52, 0, 6.5))
    for x in (-pitch_x / 2, pitch_x / 2):
        for y in (-pitch_y / 2, pitch_y / 2):
            part -= bd.Pos(x, y, 1.5) * bd.Cylinder(1.6, 5)
    for x in (-18, 18):
        for y in (-28, 28):
            part -= bd.Pos(x, y, 1.5) * bd.Cylinder(1.6, 5)
    part.label = "tray"
    part.color = "#328BB5"
    return part


@model
def bridge():
    part = block(44, 64, 3, (0, 0, 31.5))
    for x in (-18, 18):
        for y in (-28, 28):
            part += block(8, 8, 27, (x, y, 16.5))
            part -= bd.Pos(x, y, 18) * bd.Cylinder(1.6, 32)
    for x in (-15, 15):
        for y in (-15, 15):
            part -= bd.Pos(x, y, 31.5) * bd.Cylinder(1.6, 5)
    part.label = "bridge"
    part.color = "#394B5B"
    return part


@model
def assembly():
    return bd.Compound(children=[tray().shape, bridge().shape], label="drone_module")


@model
def overview():
    config = design()
    battery_size = config["battery"]["envelopes_mm"][config["battery"]["selected"]]
    battery = block(*battery_size, config["battery"]["seated_center_mm"])
    battery.label, battery.color = "battery_surrogate", "#F1AD4C"
    board = block(*config["board"]["envelope_mm"], config["board"]["center_mm"])
    board.label, board.color = "board_surrogate", "#389D80"
    return bd.Compound(children=[tray().shape, bridge().shape, battery, board], label="module_overview")


@model
def insertion_view():
    config = design()
    battery_size = config["battery"]["envelopes_mm"][config["battery"]["selected"]]
    center = list(config["battery"]["seated_center_mm"])
    center[0] -= 100
    battery = block(*battery_size, center)
    battery.label, battery.color = "battery_surrogate", "#F1AD4C"
    return bd.Compound(children=[tray().shape, bridge().shape, battery], label="battery_insertion")


@model
def exploded_view():
    """Presentation state only; never used as an acceptance or print assembly."""
    config = design()
    battery_size = config["battery"]["envelopes_mm"][config["battery"]["selected"]]
    center = list(config["battery"]["seated_center_mm"])
    center[0] -= 120
    battery = block(*battery_size, center)
    battery.label, battery.color = "battery_surrogate", "#F1AD4C"
    lifted = bridge().shape.moved(bd.Pos(0, 0, 25))
    board_center = list(config["board"]["center_mm"])
    board_center[2] += 40
    board = block(*config["board"]["envelope_mm"], board_center)
    board.label, board.color = "board_surrogate", "#389D80"
    return bd.Compound(children=[tray().shape, lifted, battery, board], label="module_exploded")


if __name__ == "__main__":
    # A document ID is a lookup handle. Source/output hashes identify a revision.
    ids = {name: function().id for name, function in
           (("assembly", assembly), ("overview", overview), ("insertion", insertion_view),
            ("exploded", exploded_view))}
    if len(sys.argv) > 1:
        output = Path(sys.argv[1]).resolve()
        output.mkdir(parents=True, exist_ok=True)
        (output / "documents.json").write_text(json.dumps(ids, indent=2) + "\n")
    print("PE_DOCUMENTS " + json.dumps(ids))
