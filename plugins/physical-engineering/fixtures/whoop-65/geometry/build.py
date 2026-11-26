"""Build the snap-canopy Whoop 65 assembly and print meshes from editable CAD."""

import argparse
import struct
import sys
from pathlib import Path

import build123d as bd

# The installed source is read-only, including when invoked directly.
sys.dont_write_bytecode = True

import frame
import hardware
import snap


def export_mesh(part, filename):
    """Center a single print on the bed, preserving the part's installed orientation."""
    bounds = part.bounding_box()
    offset = bd.Pos(
        -(bounds.min.X + bounds.max.X) / 2,
        -(bounds.min.Y + bounds.max.Y) / 2,
        -bounds.min.Z,
    )
    linear, angular = (0.015, 0.12) if filename.stem == "frame" else (0.018, 0.11)
    if not bd.export_stl(offset * part, filename, tolerance=linear, angular_tolerance=angular):
        raise RuntimeError(f"Could not export {filename}")

    # Edges coincident with the bed can differ by roundoff between adjacent faces.
    data = bytearray(filename.read_bytes())
    (count,) = struct.unpack_from("<I", data, 80)
    if len(data) != 84 + 50 * count:
        raise ValueError(f"Malformed binary STL export for {filename}")
    for index in range(count):
        for delta in (20, 32, 44):
            at = 84 + index * 50 + delta
            if abs(struct.unpack_from("<f", data, at)[0]) < 1e-7:
                struct.pack_into("<f", data, at, 0.0)
    filename.write_bytes(data)


def export_step(part, path):
    if not bd.export_step(part, path):
        raise RuntimeError(f"Could not export {path}")


def build(output):
    print_dir = output / "print"
    component_dir = output / "components"
    print_dir.mkdir(parents=True, exist_ok=True)
    component_dir.mkdir(parents=True, exist_ok=True)

    aircraft_frame = frame.component()
    dock = snap.clip_dock()
    dock.color = "#31A598"
    canopy = snap.clip_canopy(hardware.canopy())
    printed = {"frame": aircraft_frame, "dock": dock, "canopy": canopy}
    spring, catch = snap.coupons()
    for name, part in {**printed, "coupon-spring": spring, "coupon-catch": catch}.items():
        export_mesh(part, print_dir / f"{name}.stl")

    components = {
        **printed,
        **{part.label.replace("_", "-"): part for part in hardware.propulsion()},
        "flight-controller": hardware.flight_controller(),
        "fpv-camera": hardware.fpv_camera(),
        "battery-pack": hardware.battery_pack(),
        "battery-retention": hardware.battery_retention(),
        "wiring": hardware.wiring(),
        "stack-hardware": hardware.stack_hardware(),
    }
    for name, part in components.items():
        export_step(part, component_dir / f"{name}.step")
    assembly = bd.Compound(children=list(components.values()), label="Whoop_65_Snap")
    export_step(assembly, output / "assembly.step")
    if not bd.export_gltf(
        assembly,
        output / "assembly.glb",
        binary=True,
        linear_deflection=0.055,
        angular_deflection=0.19,
    ):
        raise RuntimeError("Could not export assembly.glb")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Output directory for CAD and print files")
    build(parser.parse_args().output)
