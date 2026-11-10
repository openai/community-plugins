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


def rounded_plate(length, width, radius, height=3, at=(0, 0, 0)):
    return bd.Pos(*at) * bd.extrude(bd.RectangleRounded(length, width, radius), amount=height)


def slot(length, width, height, at):
    return bd.Pos(*at) * bd.extrude(bd.SlotOverall(length, width), amount=height)


@model
def tray():
    config = design()
    width = config["battery"]["envelopes_mm"][config["battery"]["selected"]][1]
    pitch_x, pitch_y = config["frame"]["pitch_mm"]
    cavity = width + 1.5
    outside_y = cavity / 2 + 2
    part = rounded_plate(106, width + 5.5, 3)
    # Round fastener pads join the tray through deliberate tapered load paths.
    for sx in (-1, 1):
        x = sx * pitch_x / 2
        root_x = sx * min(pitch_x / 2 - 6, 45)
        for sy in (-1, 1):
            y = sy * pitch_y / 2
            root_y = sy * (outside_y - 4)
            outline = bd.Polygon((root_x-7, root_y), (root_x+7, root_y),
                                 (x+6, y), (x-6, y), align=None)
            part += bd.extrude(outline, amount=3, dir=(0, 0, 1))
            part += bd.Pos(x, y, 1.5) * bd.Cylinder(6, 3)
    # Dedicated bridge seats retain a flat datum around each through bolt.
    for x in (-18, 18):
        for sy in (-1, 1):
            y = sy * 28
            start = outside_y - 2
            part += block(11, 28-start, 3, (x, sy*(28+start)/2, 1.5))
            part += bd.Pos(x, y, 1.5) * bd.Cylinder(5.5, 3)
    # Two webbing stations are outside both the cavity and installed bridge.
    # The removable purchased strap is deliberately not part of this solid.
    for x in (-34, 34):
        for sy in (-1, 1):
            y = sy * (outside_y + 3)
            part += rounded_plate(18.2, 9.2, 2.5, at=(x, y, 0))
            part -= slot(13, 3.5, 5, (x, y, -1))
            # Wider guide lands retain at least 2 mm around a softened mouth.
            # Both lips are rounded; physical webbing abrasion still needs a test.
            mouth_edges = []
            for edge in part.edges():
                bounds = edge.bounding_box()
                if (abs(bounds.max.Z-bounds.min.Z) < 1e-7 and
                    min(abs(bounds.min.Z), abs(bounds.min.Z-3)) < 1e-7 and
                    bounds.min.X >= x-6.501 and bounds.max.X <= x+6.501 and
                    bounds.min.Y >= y-1.751 and bounds.max.Y <= y+1.751):
                    mouth_edges.append(edge)
            part = part.fillet(0.5, mouth_edges)
    for y in (-(cavity / 2 + 1), cavity / 2 + 1):
        part += block(106, 2, 8, (0, y, 7))
    part += block(2, cavity, 8, (52, 0, 7))
    # Rounds sit above Z10 so the complete functional rail faces remain planar.
    top_edges = [edge for edge in part.edges()
                 if abs(edge.bounding_box().min.Z-11) < 1e-7 and
                    abs(edge.bounding_box().max.Z-11) < 1e-7]
    part = part.fillet(0.8, top_edges)
    for sx in (-1, 1):
        root_x = sx * min(pitch_x / 2 - 6, 45)
        for sy in (-1, 1):
            plane = bd.Plane(origin=(root_x-2, 0, 0), x_dir=(0, 1, 0), z_dir=(1, 0, 0))
            section = plane * bd.Polygon((sy*(outside_y-.5), 3),
                                         (sy*(outside_y-.5), 8), (sy*26, 3), align=None)
            part += bd.extrude(section, amount=4, dir=(1, 0, 0))
    # Peripheral relief leaves the central 80 x 20 datum and >3000 mm² battery support.
    for x in (-30, 30):
        for y in (-13.5, 13.5):
            part -= slot(18, 4, 5, (x, y, -1))
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
    part = rounded_plate(45, 65, 4.5, at=(0, 0, 30))
    for x in (-18, 18):
        for y in (-28, 28):
            part += bd.Pos(x, y, 16.5) * bd.Cylinder(4.5, 27)
    top_edges = [edge for edge in part.edges()
                 if abs(edge.bounding_box().min.Z-33) < 1e-7 and
                    abs(edge.bounding_box().max.Z-33) < 1e-7]
    part = part.fillet(0.6, top_edges)
    # Four rounded ventilation/weight-relief windows leave the central 20 x 20
    # datum and board fastener seats intact; support is verified from STEP.
    for y in (-25, -14, 14, 25):
        part -= slot(20, 4, 5, (0, y, 29))
    for x in (-18, 18):
        for y in (-28, 28):
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
