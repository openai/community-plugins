"""Guard coupon test fixture. Station and sensor dimensions are in millimeters."""
import argparse
import math
from pathlib import Path
import subprocess
import sys

import build123d as cad


# Coupon dimensions use a station-local datum at the center of the arc.
ORIGIN_X, ORIGIN_Y = -48.0, -4.0
ROOT_SCREW = (-31.5, -12.0)
STOP_SCREW = (-51.0, 2.0)
SENSOR_FASTENERS = ((-2.0, 0.0), (62.0, 0.0))
# (global X, global Y, pin diameter)
ROOT_PINS = ((-36.5, -9.0, 2.5), (-26.5, -15.0, 3.0))
STOP_PINS = ((-54.5, 1.0, 2.5), (-47.5, 4.3, 2.5))


def cylinder(x, y, z, radius, height):
    return cad.Solid.make_cylinder(radius, height, cad.Plane(origin=(x, y, z)))


def block(xmin, xmax, ymin, ymax, zmin, zmax):
    return cad.Solid.make_box(xmax - xmin, ymax - ymin, zmax - zmin,
                              cad.Plane(origin=(xmin, ymin, zmin)))


def rounded_plate(cx, cy, z, width, depth, height, corner_radius):
    profile = cad.Pos(cx, cy, z) * cad.RectangleRounded(width, depth, corner_radius)
    return cad.extrude(profile, amount=height)


def hex_prism(x, y, z, across_flats, height):
    profile = cad.Pos(x, y, z) * cad.RegularPolygon(across_flats / math.sqrt(3), 6)
    return cad.extrude(profile, amount=height)


def as_part(shape, name, color):
    solids = shape.clean().solids()
    if len(solids) != 1 or not solids[0].is_valid:
        raise RuntimeError(f"{name} must contain one valid solid")
    part = solids[0]
    part.label = name
    part.color = color
    return part


def plate():
    slab = rounded_plate(0, 0, 0, 160, 84, 8, 6)
    # Pedestal raises the coupon's free span 3 mm clear of the slab.
    pedestal = rounded_plate(-31.5, -11, 8, 17, 16, 3, 1.4)
    slab = slab.fuse(pedestal)
    for pins, seat, height in ((ROOT_PINS, 11, 3.0), (STOP_PINS, 8, 2.5)):
        for x, y, diameter in pins:
            slab = slab.fuse(cylinder(x, y, seat, diameter / 2, height))
    for x, y in (*SENSOR_FASTENERS, ROOT_SCREW, STOP_SCREW):
        # An M3 nut is supported at Z4, with its flats accessible underneath.
        slab = slab.cut(hex_prism(x, y, -1, 5.8, 5), cylinder(x, y, -1, 1.7, 18))
    return as_part(slab, "plate", "#536a76")


def coupon(thickness, name, color):
    x, y = ORIGIN_X, ORIGIN_Y
    floor, height = 11, 5.5
    sector = cad.Solid.make_cylinder(16.30 + thickness, height, cad.Plane(origin=(x, y, floor)), 90)
    arc = sector.cut(cylinder(x, y, floor - 1, 16.30, height + 2))
    # The rectangular root and tip join the two radial end faces tangentially.
    # Each version has the same tip face at Y11, with the finger load applied at Y15.
    root = block(x + 9, x + 24, y - 14, y, floor, floor + height)
    tip = block(x - 6, x, y + 15, y + 19, floor, floor + height)
    specimen = arc.fuse(root, tip)
    for px, py, diameter in ROOT_PINS:
        specimen = specimen.cut(cylinder(px, py, floor - 1, (diameter + 0.30) / 2, height + 2))
    specimen = specimen.cut(cylinder(*ROOT_SCREW, floor - 1, 1.7, height + 2))
    return as_part(specimen, name, color)


def stop():
    shoe = block(-56, -46, -2, 6.4, 8, 12.5)
    # A narrow finger on the load axis defines contact at Y9. Its base sits on the plate.
    contact = block(-51.5, -50.5, 5.5, 9, 8, 15)
    shape = shoe.fuse(contact)
    for x, y, diameter in STOP_PINS:
        shape = shape.cut(cylinder(x, y, 7, (diameter + 0.30) / 2, 8))
    shape = shape.cut(cylinder(*STOP_SCREW, 7, 1.7, 9))
    return as_part(shape, "travel_stop", "#e5ab4f")


def sensor_proxy():
    # External test envelope only. Interior dimensions and electronics are not modeled.
    base = rounded_plate(30, 0, 8, 52, 36, 15, 2.5)
    for x, y in SENSOR_FASTENERS:
        bridge = block(min(x, 30), max(x, 30), -4.5, 4.5, 8, 11)
        base = base.fuse(bridge, cylinder(x, y, 8, 4.5, 3))
        base = base.cut(cylinder(x, y, 7, 1.7, 6))
    lid = rounded_plate(30, 0, 23, 52, 36, 2.2, 2.5)
    for x in (9, 51):
        for y in (-13, 13):
            lid = lid.fuse(cylinder(x, y, 25.2, 1.78, 1.45))
    return (as_part(base, "sensor_base_proxy", "#3f8b87"),
            as_part(lid, "sensor_lid_proxy", "#86bac2"))


def fasteners(xy, seat, length, prefix):
    x, y = xy
    shaft = cylinder(x, y, seat - length, 1.50, length)
    head = cylinder(x, y, seat, 2.75, 3.0)
    screw = shaft.fuse(head).cut(hex_prism(x, y, seat + 1.5, 2.5, 2))
    nut = hex_prism(x, y, 1.6, 5.5, 2.4).cut(cylinder(x, y, 1.2, 1.51, 3.3))
    if prefix in ("root", "stop"):
        return (as_part(screw, prefix + "_screw", "#4c5056"),
                as_part(nut, prefix + "_nut", "#8c8d91"))
    return (as_part(screw, "sensor_screw_" + prefix, "#4c5056"),
            as_part(nut, "sensor_nut_" + prefix, "#8c8d91"))


def step(shape, target):
    if not cad.export_step(shape, target):
        raise RuntimeError(f"Failed to export {target.name}")


def print_mesh(shape, target):
    box = shape.bounding_box()
    on_bed = cad.Pos(-(box.min.X + box.max.X) / 2, -(box.min.Y + box.max.Y) / 2, -box.min.Z) * shape
    if not cad.export_stl(on_bed, target, tolerance=0.012, angular_tolerance=0.10):
        raise RuntimeError(f"Failed to export {target.name}")


def export_fixture(folder):
    components, prints, alternate = (folder / name for name in ("components", "print", "alternate"))
    for path in (components, prints, alternate):
        path.mkdir(parents=True, exist_ok=True)

    active = coupon(1.00, "coupon_100", "#df7456")
    thick = coupon(1.40, "coupon_140", "#aa6cbd")
    base = plate()
    hard_stop = stop()
    mounted = [base, hard_stop, *sensor_proxy(),
               *fasteners(ROOT_SCREW, 16.5, 16, "root"),
               *fasteners(STOP_SCREW, 12.5, 12, "stop"),
               *fasteners(SENSOR_FASTENERS[0], 11, 10, "left"),
               *fasteners(SENSOR_FASTENERS[1], 11, 10, "right")]
    for part in [*mounted, active]:
        step(part, components / (part.label + ".step"))
    for part in (base, active, hard_stop):
        print_mesh(part, prints / (part.label + ".stl"))
    step(thick, alternate / "coupon_140.step")
    print_mesh(thick, alternate / "coupon_140.stl")
    secondary = cad.Compound(children=[*mounted, thick], label="guard_bench")
    step(secondary, alternate / "assembly_140.step")
    primary = cad.Compound(children=[*mounted, active], label="guard_bench")
    step(primary, folder / "assembly.step")
    if not cad.export_gltf(primary, folder / "assembly.glb", binary=True,
                           linear_deflection=0.035, angular_deflection=0.16):
        raise RuntimeError("Failed to export the fixture GLB")


def build(folder):
    export_fixture(folder)
    subprocess.run([sys.executable, "-B", str(Path(__file__).with_name("drawing_check.py")),
                    str(folder), "--report", str(folder / "drawing-measurements.json")], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    build(parser.parse_args().folder)
