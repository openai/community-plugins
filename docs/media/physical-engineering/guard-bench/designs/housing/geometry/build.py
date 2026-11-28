"""Editable millimeter model of a vented tabletop sensor enclosure and board envelope."""
import argparse
import json
from pathlib import Path

import build123d as bd
from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
from validation.verify_mounts import require_exported_base
from validation.inspect_integration import check_assembly
from finish_glb import apply_materials


LID_CORNERS = [(-21, -13), (21, -13), (21, 13), (-21, 13)]
BOARD_CORNERS = [(-14, -8), (14, -8), (14, 8), (-14, 8)]
BENCH_BORE_PITCH_MM = 64.0
EAR_CENTER_X_MM = 32.0
EAR_ROOT_X_MM = 24.5
EAR_RADIUS_MM = 5.5
EAR_THICKNESS_MM = 3.2
BENCH_HOLE_DIAMETER_MM = 3.4


def panel(width, height, radius, bottom, thickness):
    return bd.Pos(0, 0, bottom) * bd.extrude(bd.RectangleRounded(width, height, radius), thickness)


def cylinder(x, y, bottom, radius, height):
    return bd.Solid.make_cylinder(radius, height, bd.Plane(origin=(x, y, bottom)))


def named(part, name, color):
    part.label, part.color = name, color
    return part


def add_mounting_ears(base):
    """Fuse each round-ended external lug to the side wall above the bench face."""
    neck_length = EAR_CENTER_X_MM - EAR_ROOT_X_MM
    neck_center = (EAR_CENTER_X_MM + EAR_ROOT_X_MM) / 2
    for side in (-1, 1):
        neck = bd.Pos(side * neck_center, 0, EAR_THICKNESS_MM / 2) * bd.Box(
            neck_length, 2 * EAR_RADIUS_MM, EAR_THICKNESS_MM)
        rounded_end = cylinder(side * EAR_CENTER_X_MM, 0, 0, EAR_RADIUS_MM, EAR_THICKNESS_MM)
        base = base.fuse(neck, rounded_end)
        bore = cylinder(side * BENCH_BORE_PITCH_MM / 2, 0, -0.1,
                        BENCH_HOLE_DIAMETER_MM / 2, EAR_THICKNESS_MM + 0.2)
        base = base.cut(bore)
    return base


def case_parts():
    base = panel(52, 36, 4, 0, 15) - panel(48, 32, 2.5, 2, 15)
    base = add_mounting_ears(base)
    lid = panel(52, 36, 4, 15, 2.2)
    for x, y in LID_CORNERS:
        base = base.fuse(cylinder(x, y, 1.9, 2.55, 13.1))
        base = base.cut(cylinder(x, y, 6.2, .85, 9))
        lid = lid.cut(cylinder(x, y, 14.9, 1.15, 2.5))
    for x, y in BOARD_CORNERS:
        base = base.fuse(cylinder(x, y, 1.9, 2.2, 3.1))
        base = base.cut(cylinder(x, y, 1.3, .85, 4))
    # A rounded opening through the front wall follows the side-entry JST interface.
    # Plane.XZ uses a -Y normal, so this cut traverses the wall from the cavity.
    port = bd.Plane.XZ * bd.RectangleRounded(13.0, 7.4, 1.0)
    opening = bd.Pos(0, -14, 8.2) * bd.extrude(port, amount=6)
    base = base.cut(opening)
    for x in (-13.3, -9.5, -5.7, -1.9, 1.9, 5.7, 9.5, 13.3):
        slot = bd.Pos(x, 0, 14.9) * bd.extrude(bd.SlotOverall(16.5, 1.8, rotation=90), amount=2.5)
        lid = lid.cut(slot)
    return named(base.clean(), "base", "#226B68"), named(lid.clean(), "lid", "#E7EBDA")


def board_assembly():
    # The delivered ECAD STEP is registered to the enclosure center and board bottom.
    source = Path(__file__).parent / "assets" / "instrument-pcb.step"
    native = bd.import_step(source)
    positions = json.loads(source.with_name("placement.json").read_text())["component_model_centers"]
    members = []
    assigned = set()
    for child in native.children:
        if child.label.endswith("_PCB"):
            label = "substrate"
        elif child.label.startswith("bench-accelerometer_"):
            label = child.label.removeprefix("bench-accelerometer_")
            if label not in {"copper", "pad", "via"}:
                raise ValueError(f"Unknown KiCad fabrication layer {label}")
        else:
            center = child.bounding_box().center()
            label = min(positions, key=lambda key: (positions[key][0]-center.X)**2+(positions[key][1]-center.Y)**2)
            desired = positions[label]
            if (desired[0]-center.X)**2+(desired[1]-center.Y)**2 > .25**2:
                raise ValueError(f"ECAD STEP shape {child.label} does not match a PCB placement")
        if label in assigned: raise ValueError(f"Duplicate ECAD occurrence at {label}")
        assigned.add(label)
        if label.startswith(("C", "R")):
            # KiCad instances of identical passives share their underlying BRep.
            # Give each component an independent BRep identity for STEP refdes labels.
            leaf = bd.Solid(BRepBuilderAPI_Copy(child.solids()[0].wrapped).Shape())
            leaf.color = child.color
            child = leaf
        placed = bd.Pos(0, 0, 5.0) * child
        placed.label = label
        members.append(placed)
    if assigned != set(positions) | {"substrate", "copper", "pad", "via"}: raise ValueError("ECAD placement inventory differs")
    return bd.Compound(children=members, label="sensor_board")



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
        path = component_dir / f"{part.label}.step"
        if not bd.export_step(part, path):
            raise RuntimeError(f"Failed to export {part.label}")
        if part.label == "base":
            require_exported_base(path)
    for part in (base, lid):
        on_bed = bd.Pos(0, 0, -part.bounding_box().min.Z) * part
        if not bd.export_stl(on_bed, print_dir / f"{part.label}.stl", tolerance=.015, angular_tolerance=.12):
            raise RuntimeError(f"Failed to export print for {part.label}")
    assembly = bd.Compound(children=[base, lid, board, board_screws, lid_screws], label="Guard_station_recorder")
    if not bd.export_step(assembly, output / "assembly.step"):
        raise RuntimeError("Failed to export assembly STEP")
    check_assembly(output, (output / "integration.json"))
    if not bd.export_gltf(assembly, output / "assembly.glb", binary=True, linear_deflection=.035, angular_deflection=.15):
        raise RuntimeError("Failed to export assembly GLB")
    apply_materials(output / "assembly.glb")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    build(parser.parse_args().output)
