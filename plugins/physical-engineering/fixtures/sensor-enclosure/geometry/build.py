"""Editable millimeter model of a vented tabletop sensor enclosure and board envelope."""
import argparse
from pathlib import Path

import build123d as bd


LID_CORNERS = [(-21, -13), (21, -13), (21, 13), (-21, 13)]
BOARD_CORNERS = [(-14, -8), (14, -8), (14, 8), (-14, 8)]


def panel(width, height, radius, bottom, thickness):
    return bd.Pos(0, 0, bottom) * bd.extrude(bd.RectangleRounded(width, height, radius), thickness)


def cylinder(x, y, bottom, radius, height):
    return bd.Solid.make_cylinder(radius, height, bd.Plane(origin=(x, y, bottom)))


def named(part, name, color):
    part.label, part.color = name, color
    return part


def case_parts():
    base = panel(52, 36, 4, 0, 15) - panel(48, 32, 2.5, 2, 15)
    lid = panel(52, 36, 4, 15, 2.2)
    for x, y in LID_CORNERS:
        base = base.fuse(cylinder(x, y, 1.9, 2.55, 13.1))
        base = base.cut(cylinder(x, y, 6.2, .85, 9))
        lid = lid.cut(cylinder(x, y, 14.9, 1.15, 2.5))
    for x, y in BOARD_CORNERS:
        base = base.fuse(cylinder(x, y, 1.9, 2.2, 3.1))
        base = base.cut(cylinder(x, y, 1.3, .85, 4))
    # Air inlet below board edge and eight narrow, rounded openings in the lid.
    base = base.cut(bd.Pos(0, -17, 6) * bd.Box(15, 4, 3))
    for x in (-13.3, -9.5, -5.7, -1.9, 1.9, 5.7, 9.5, 13.3):
        slot = bd.Pos(x, 0, 14.9) * bd.extrude(bd.SlotOverall(16.5, 1.8, rotation=90), amount=2.5)
        lid = lid.cut(slot)
    return named(base.clean(), "base", "#226B68"), named(lid.clean(), "lid", "#E7EBDA")


def board_assembly():
    board = panel(34, 22, 1.5, 5, 1.6)
    for x, y in BOARD_CORNERS:
        board = board.cut(cylinder(x, y, 4.9, 1.15, 1.8))
    pcb = named(board, "substrate", "#18604F")
    sensor = named(bd.Pos(-4.5, 1, 7.7) * bd.Box(5, 5, 2.2), "sensor_package", "#263038")
    shield = named(bd.Pos(4.6, -.3, 7.25) * bd.Box(6.5, 6.5, 1.3), "shield", "#9BA5AA")
    pads = [cylinder(-5 + 2*i, -7.8, 6.6, .52, .06) for i in range(6)]
    contacts = named(bd.Compound(pads), "contact_pads", "#D6AE52")
    return bd.Compound(children=[pcb, sensor, shield, contacts], label="sensor_board")


def screws(positions, seat, length, label):
    members = []
    for index, (x, y) in enumerate(positions, 1):
        shank = cylinder(x, y, seat-length, .98, length)
        head = cylinder(x, y, seat, 1.78, 1.45)
        recess = bd.Pos(x, y, seat + 1) * bd.extrude(bd.RegularPolygon(.75, 6), .6)
        screw = named(shank.fuse(head).cut(recess), f"screw_{index}", "#52626C")
        members.append(screw)
    return bd.Compound(children=members, label=label)


def build(output):
    component_dir = output / "components"
    print_dir = output / "print"
    component_dir.mkdir(parents=True, exist_ok=True)
    print_dir.mkdir(exist_ok=True)
    base, lid = case_parts()
    board = board_assembly()
    board_screws = screws(BOARD_CORNERS, 6.6, 4, "board_screws")
    lid_screws = screws(LID_CORNERS, 17.2, 8, "lid_screws")
    for part in (base, lid, board):
        if not bd.export_step(part, component_dir / f"{part.label}.step"):
            raise RuntimeError(f"Failed to export {part.label}")
    for part in (base, lid):
        on_bed = bd.Pos(0, 0, -part.bounding_box().min.Z) * part
        if not bd.export_stl(on_bed, print_dir / f"{part.label}.stl", tolerance=.015, angular_tolerance=.12):
            raise RuntimeError(f"Failed to export print for {part.label}")
    assembly = bd.Compound(children=[base, lid, board, board_screws, lid_screws], label="Sensor_enclosure")
    if not bd.export_step(assembly, output / "assembly.step"):
        raise RuntimeError("Failed to export assembly STEP")
    if not bd.export_gltf(assembly, output / "assembly.glb", binary=True, linear_deflection=.035, angular_deflection=.15):
        raise RuntimeError("Failed to export assembly GLB")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    build(parser.parse_args().output)
