"""Read back the Whoop 65 STEP, component exports, GLB and print meshes in the CAD runtime."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import sys

import build123d as bd
import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Cylinder
from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
from OCP.gp import gp_Dir, gp_Lin, gp_Pnt
from OCP.TopAbs import TopAbs_REVERSED

sys.dont_write_bytecode = True
from verify_assembly import bounds, check_print as shared_check_print, glb_bounds, nodes, numbers

CORNERS = {"FL": "CCW", "FR": "CW", "RL": "CW", "RR": "CCW"}
COMPONENTS = {
    "frame": "whoop_frame", "dock": "snap_dock", "canopy": "snap_canopy",
    "flight-controller": "flight_controller", "fpv-camera": "fpv_camera",
    "battery-pack": "battery_pack", "battery-retention": "battery_retention",
    "stack-hardware": "stack_hardware", "wiring": "wiring",
    **{f"propulsion-{key}": f"propulsion_{key}" for key in CORNERS},
}
COUPON_SPANS = {"coupon-spring": [9.60, 30.175, 6.75], "coupon-catch": [15.05, 10.85, 1.35]}
PRINTS = ("frame", "dock", "canopy", *COUPON_SPANS)


def cylinder_features(shape, radius, *, bore):
    """Complete vertical bores or external shanks in installed coordinates."""
    found = []
    for face in shape.faces():
        adaptor = BRepAdaptor_Surface(face.wrapped)
        if adaptor.GetType() != GeomAbs_Cylinder:
            continue
        cyl = adaptor.Cylinder()
        if (abs(cyl.Radius() - radius) > .0125 or abs(cyl.Axis().Direction().Z()) < .999999
                or adaptor.LastUParameter() - adaptor.FirstUParameter() < 2 * math.pi - .001
                or (face.wrapped.Orientation() == TopAbs_REVERSED) != bore):
            continue
        point, box = cyl.Location(), face.bounding_box()
        found.append((point.X(), point.Y(), box.min.Z, box.max.Z))
    return found


def unique(items, name):
    if len(items) != 1:
        raise ValueError(f"Expected one {name}, found {len(items)}")
    return items[0]


def axis_matches(reference, candidates):
    """Match every axis to a distinct nearest feature; reject partial or duplicate patterns."""
    if not reference or len(reference) != len(candidates):
        raise ValueError(f"Incomplete mounting pattern ({len(reference)} vs {len(candidates)})")
    matches = [min(range(len(candidates)), key=lambda index: math.dist(r[:2], candidates[index][:2]))
               for r in reference]
    if len(set(matches)) != len(reference):
        raise ValueError("Mounting pattern maps multiple axes to the same feature")
    return [candidates[index] for index in matches]


def engagement(a, b):
    return max(0, min(a[3], b[3]) - max(a[2], b[2]))


def check_latches(dock, canopy, check):
    def crossings(shape, point, direction, low, high):
        probe = IntCurvesFace_ShapeIntersector()
        probe.Load(shape.wrapped, 1e-7)
        probe.Perform(gp_Lin(gp_Pnt(*point), gp_Dir(*direction)), low, high)
        return sorted({round(probe.WParameter(i), 6) for i in range(1, probe.NbPnt() + 1)})

    for side, sign in (("left", -1), ("right", 1)):
        stem = crossings(dock, (0, 0, 8.55), (sign, 0, 0), 14., 16.1)
        catch = crossings(canopy, (0, 0, 8.55), (sign, 0, 0), 16., 18.)
        barb = crossings(dock, (0, 0, 9.80), (sign, 0, 0), 14., 17.15)
        shoulder = crossings(dock, (16.56 * sign, .11, 0), (0, 0, 1), 9.46, 9.91)
        ledge = crossings(canopy, (16.56 * sign, .11, 0), (0, 0, 1), 9.1, 9.65)
        if [len(v) for v in (stem, catch, barb, shoulder, ledge)] != [2, 2, 2, 1, 1]:
            raise ValueError(f"Missing or malformed {side} latch boundary")
        gap, capture, play = catch[0] - stem[1], barb[1] - catch[0], shoulder[0] - ledge[0]
        check(f"latch-{side}", .20 <= gap <= .45 and .65 <= capture <= .85 and .15 <= play <= .35,
              {"running_gap_mm": round(gap, 4), "radial_capture_mm": round(capture, 4),
               "vertical_play_mm": round(play, 4)},
              "Running gap 0.20–0.45 mm, radial capture 0.65–0.85 mm, vertical play 0.15–0.35 mm.")


def check_bom(path, parts, check):
    """Count purchase/print occurrences; child solids of a purchased assembly are one item."""
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len({r["ID"] for r in rows}) != len(rows):
        raise ValueError("Duplicate item ID in BOM")
    if any(r["Scope"] not in ("Aircraft", "Bench") for r in rows):
        raise ValueError("Unknown BOM scope")
    aircraft = {r["ID"]: r for r in rows if r["Scope"] == "Aircraft"}
    names = {"/".join(key) for key in parts}
    patterns = {
        "FR01": "whoop_frame", "DK01": "snap_dock", "CY01": "snap_canopy",
        "FC01": "flight_controller", "CA01": "fpv_camera", "BA01": "battery_pack",
        "MO01": r"propulsion_(FL|FR|RL|RR)/motor_\1",
        "MS01": r"propulsion_(FL|FR|RL|RR)/motor_screws_\1/M1_4x3_[0-9]+",
        "PR01": r"propulsion_(FL|FR|RL|RR)/prop_\1_CW", "PR02": r"propulsion_(FL|FR|RL|RR)/prop_\1_CCW",
        "GM01": r"stack_hardware/FC_grommet_[0-9]+", "DS01": r"stack_hardware/ST1_4x6_[0-9]+",
        "RB01": r"battery_retention/silicone_loop_[0-9]+", "RP01": r"battery_retention/foam_pad_[0-9]+",
    }
    # One purchased harness/antenna set can occupy several named CAD leaves.
    kits = {
        "WH01": [[f"{corner}_phase_{i}" for i in (1, 2, 3)] for corner in CORNERS],
        "WH02": [["camera_harness_1", "camera_harness_2", "camera_harness_3"]],
        "WH03": [["BT2_mating_plug", "battery_lead_plus", "battery_lead_minus"]],
        "WH04": [["ELRS_receiver_antenna"]], "WH05": [["VTX_coax", "VTX_whip"]],
    }
    installed = {key: sum(re.fullmatch(pattern, name) is not None for name in names)
                 for key, pattern in patterns.items()}
    wire_names = {key[1] for key in parts if len(key) == 2 and key[0] == "wiring"}
    for key, groups in kits.items():
        installed[key] = sum(set(group) <= wire_names for group in groups)
    unmodeled = {"CT01": "Camera retention tape is applied during assembly."}
    expected_ids = set(installed) | set(unmodeled)
    if set(aircraft) != expected_ids:
        raise ValueError(f"Unreconciled aircraft BOM IDs (missing {sorted(expected_ids-set(aircraft))}, "
                         f"unexpected {sorted(set(aircraft)-expected_ids)})")
    quantities = {key: {"step": qty, "bom": int(aircraft[key]["Qty"]), "unit": aircraft[key]["Unit"]}
                  for key, qty in sorted(installed.items())}
    passed = all(row["step"] == row["bom"] > 0 and row["unit"] == ("sets" if key == "WH01" else "ea")
                 for key, row in quantities.items())
    check("bom-installed-quantities", passed,
          {"modeled_aircraft": quantities, "unmodeled_aircraft": unmodeled,
           "bench_ids_excluded": sorted(r["ID"] for r in rows if r["Scope"] == "Bench")},
          "Each modeled aircraft BOM quantity equals the installed STEP occurrence or complete wire-set count.")


def check_fit(parts, check):
    frame, dock, canopy = [parts[(COMPONENTS[name],)] for name in ("frame", "dock", "canopy")]

    def leaf(group, name):
        return unique([s for path, s in parts.items() if path[0] == group and path[-1] == name], name)

    def fasteners(group, prefix, radius, bore=False):
        return [feature for path, shape in parts.items() if path[0] == group and path[-1].startswith(prefix)
                for feature in cylinder_features(shape, radius, bore=bore)]

    check_latches(dock, canopy, check)
    wire_names = {path[-1] for path in parts if path[0] == "wiring" and len(path) == 2}
    wire_set = {f"{corner}_phase_{i}" for corner in CORNERS for i in (1, 2, 3)}
    wire_set.update({"battery_lead_minus", "battery_lead_plus", "BT2_mating_plug", "camera_harness_1",
                     "camera_harness_2", "camera_harness_3", "ELRS_receiver_antenna", "VTX_coax", "VTX_whip"})
    check("harness", wire_set <= wire_names, {"missing": sorted(wire_set - wire_names),
          "modeled_connections": len(wire_set & wire_names)}, "All 21 reference harness parts installed")
    for label in ("imager_board", "M7_lens_barrel", "front_element"):
        leaf("fpv_camera", label)
    pouch = leaf("battery_pack", "pouch")
    pads = [leaf("battery_retention", f"foam_pad_{i}") for i in (1, 2)]
    gaps = [pad.distance_to(part) for pad in pads for part in (pouch, frame)]
    loop_bottoms = [leaf("battery_retention", f"silicone_loop_{i}").bounding_box().min.Z for i in (1, 2)]
    clearance = pouch.bounding_box().min.Z - max(loop_bottoms)
    check("battery-support", max(gaps) <= .025 and clearance >= .5, {"pad_gaps_mm": numbers(gaps),
          "pouch_above_loops_mm": round(clearance, 4)}, "Both pad gaps <= 0.025 mm; pouch >= 0.5 mm above loop bottoms")

    pilots = cylinder_features(frame, .625, bore=True)
    if len(pilots) != 4:
        raise ValueError(f"Expected four controller pilots, found {len(pilots)}")
    board = cylinder_features(leaf("flight_controller", "pcb"), 1.5, bore=True)
    deck = cylinder_features(dock, .86, bore=True)
    bolts = axis_matches(pilots, fasteners("stack_hardware", "ST", .67))
    sleeves = fasteners("stack_hardware", "FC_grommet", .78, bore=True)
    errors = [math.dist(p[:2], q[:2]) for group in (board, deck, bolts, sleeves)
              for p, q in zip(pilots, axis_matches(pilots, group))]
    min_depth = min(engagement(p, q) for p, q in zip(pilots, bolts))
    pitch = sorted(math.dist(a[:2], b[:2]) for i, a in enumerate(board) for b in board[i + 1:])[:4]
    check("controller-stack", max(errors) <= .025 and min_depth >= 1.5 and max(abs(p-26) for p in pitch) <= .025,
          {"count": len(pilots), "pitch_mm": numbers(pitch), "max_offset_mm": round(max(errors), 4),
           "min_screw_engagement_mm": round(min_depth, 4)},
          "Four 26 mm square PCB, dock, grommet and frame mounts within 0.025 mm; engagement >= 1.5 mm.")

    for target in ("fpv_camera", "flight_controller"):
        other = parts[(target,)]
        interference = canopy.intersect(other)
        overlap = sum(s.volume for s in interference.solids()) if interference else 0.
        check(f"canopy-fit-{target}", overlap <= .001, {"overlap_mm3": round(overlap, 5)}, "<= 0.001 mm³ seated overlap")

    frame_holes = cylinder_features(frame, .775, bore=True)
    guards = cylinder_features(frame, 16.3, bore=True)
    if len(frame_holes) != 12 or len(guards) != 4:
        raise ValueError("Expected twelve motor holes and four guard bores")
    shaft_axes = {}
    for corner, hand in CORNERS.items():
        group = "propulsion_" + corner
        shaft = unique(cylinder_features(leaf(group, "shaft_1mm"), .5, bore=False), "motor shaft cylinder")
        shaft_axes[corner] = shaft
        prop = leaf(group, f"prop_{corner}_{hand}")
        hub = unique(cylinder_features(prop, .5, bore=True), "propeller hub bore")
        base = leaf(group, "mounting_base")
        motor_bores = cylinder_features(base, .72, bore=True)
        local = [p for p in frame_holes if math.dist(p[:2], shaft[:2]) < 4.]
        bolts = axis_matches(motor_bores, fasteners(group, "M1_4x3_", .67))
        offsets = [math.dist(a[:2], b[:2]) for other in (bolts, axis_matches(motor_bores, local))
                   for a, b in zip(motor_bores, other)]
        seat = base.distance_to(frame)
        depth = min(engagement(a, b) for a, b in zip(motor_bores, bolts))
        check(f"mount-{corner}", len(local) == 3 and max(offsets) <= .025 and seat <= .025 and depth >= 1.5,
              {"axes": len(local), "max_offset_mm": round(max(offsets), 4), "seat_gap_mm": round(seat, 4),
               "min_engagement_mm": round(depth, 4)}, "Three axes and seat within 0.025 mm; engagement >= 1.5 mm.")

        box = prop.bounding_box()
        verts, _ = prop.tessellate(.015, .08)
        radius = max(math.hypot(v.X - shaft[0], v.Y - shaft[1]) for v in verts)
        disk = bd.Solid.make_cylinder(max(radius, 15.5), box.size.Z,
                                      bd.Plane(origin=(shaft[0], shaft[1], box.min.Z)))
        gaps = [disk.distance_to(shape) for shape in (frame, dock, canopy)]
        concentricity = min(math.dist(g[:2], shaft[:2]) for g in guards)
        hub_offset = math.dist(hub[:2], shaft[:2])
        depth = engagement(hub, shaft)
        check(f"rotor-{corner}", abs(radius - 15.5) <= .15 and concentricity <= .025 and hub_offset <= .025
              and depth >= 3 and min(gaps) >= .55,
              {"radius_mm": round(radius, 4), "guard_axis_offset_mm": round(concentricity, 4),
               "hub_axis_offset_mm": round(hub_offset, 4), "hub_engagement_mm": round(depth, 4),
               "frame_dock_canopy_clearances_mm": numbers(gaps)},
              "Radius 15.5 ± 0.15 mm; axes within 0.025 mm; hub engagement >= 3 mm; full disk gaps >= 0.55 mm.")
    diagonals = [math.dist(shaft_axes[a][:2], shaft_axes[b][:2]) for a, b in (("FL", "RR"), ("FR", "RL"))]
    check("wheelbase", all(abs(x-65) <= .05 for x in diagonals), numbers(diagonals), "Two diagonals 65 ± 0.05 mm")


def check_print(path, reference, check):
    return shared_check_print(path, reference, check, coupon_spans=COUPON_SPANS)


def verify(folder):
    report = {"schema_version": 1, "status": "unknown", "checks": [], "inputs": {}}

    def source(name):
        path = folder / name
        if not path.is_file() or path.is_symlink() or folder.resolve() not in path.resolve().parents:
            raise ValueError(f"Expected regular exported file {name}")
        report["inputs"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
        return path

    def check(key, passed, actual, expected):
        report["checks"].append({"id": key, "status": "pass" if passed else "fail", "actual": actual,
                                 "expected": expected})

    try:
        tree = bd.import_step(source("assembly.step"))
        all_parts = list(nodes(tree))
        parts = {path[1:]: shape for path, shape in all_parts[1:]}
        if len(parts) != len(all_parts)-1 or {k[0] for k in parts} != set(COMPONENTS.values()):
            raise ValueError("STEP component labels are missing, unexpected or duplicated")
        leaves = {path: shape for path, shape in parts.items() if not shape.children}
        check("assembly", True, {"subsystems": len(COMPONENTS), "named_leaves": len(leaves),
                                 "size_mm": numbers(np.ptp(bounds(tree), axis=0))}, "Complete named subsystem assembly")
        check_bom(source("bom.csv"), parts, check)
        check_fit(parts, check)
        preview = glb_bounds(source("assembly.glb"))
        if set(preview) != set(leaves):
            raise ValueError("STEP and GLB active scene have different named mesh occurrences")
        step_bounds = {key: bounds(shape) for key, shape in leaves.items()}
        errors = {key: float(np.max(abs(preview[key] - step_bounds[key]))) for key in leaves}
        error = max(errors.values())
        check("glb-assembly", error <= .15, {"named_meshes": len(preview), "max_bounds_drift_mm": round(error, 4)},
              "Indexed triangles for every STEP leaf, installed world bounds within 0.15 mm")
        for name, label in COMPONENTS.items():
            ref = parts[(label,)]
            exported = bd.import_step(source(f"components/{name}.step"))
            exported_solids, assembled_solids = exported.solids(), ref.solids()
            # build123d Compound.volume can omit nested children on a STEP wrapper.
            # The OCC solid iterator includes every actual solid occurrence.
            count = len(exported_solids)
            volume = sum(s.volume for s in exported_solids)
            volume_error = abs(volume - sum(s.volume for s in assembled_solids))
            bounds_error = float(np.max(abs(bounds(exported) - bounds(ref))))
            valid = exported.is_valid and ref.is_valid
            check(f"component-{name}", count == len(assembled_solids) and count > 0 and valid and
                  volume_error < .01 and bounds_error < .01,
                  {"solid_count": count, "valid": valid, "volume_mm3": round(volume, 3),
                   "max_bounds_error_mm": round(bounds_error, 4), "volume_difference_mm3": round(volume_error, 4)},
                  "Valid component with the assembled solid count, world bounds within 0.01 mm and volume within 0.01 mm³")
        for name in PRINTS:
            check_print(source(f"print/{name}.stl"), parts.get((COMPONENTS.get(name),)), check)
        report["status"] = "pass" if all(c["status"] == "pass" for c in report["checks"]) else "fail"
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = verify(args.folder)
    dest = args.report or args.folder / "measurements.json"
    dest.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    failed = [c["id"] for c in report["checks"] if c["status"] != "pass"]
    print(json.dumps({"status": report["status"], "checks": len(report["checks"]), "failed": failed,
                      **({"error": report["error"]} if "error" in report else {})}))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
