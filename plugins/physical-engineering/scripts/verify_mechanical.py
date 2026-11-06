#!/usr/bin/env python3
"""Read a single mechanical part's STEP and STL against declared measurements.

The model is editable; this installed checker is not. Bounds, complete vertical
bores, occupied keepouts, volume and solid-material mass are measured from BRep.
These checks do not establish loads, printability, toleranced fits or assembly.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import sys

import verify_geometry as geometry

BASE_IDS = {"part_solid", "envelope", "mass", "hole_feature_count"}
PRINT_SUFFIXES = {"mesh_topology", "mesh_bounds", "mesh_volume", "mesh_surface", "step_consistency"}
NAME = re.compile(r"[a-z][a-z0-9_-]{0,47}\Z")


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError("Missing or unsupported mechanical requirement fields")


def _number(value, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Requirement numbers must be finite")
    if positive and value <= 0:
        raise ValueError("Requirement dimensions and limits must be positive")


def _vector(value, length, *, positive=False):
    if not isinstance(value, list) or len(value) != length:
        raise ValueError("Invalid dimensional vector")
    for item in value:
        _number(item, positive=positive)


def _tolerance(value):
    _number(value, positive=True)
    if value > .5:
        raise ValueError("Measurement tolerance must be at most 0.5 mm")


def validate_requirements(req):
    """Validate the supported declarative contract without loading the CAD kernel."""
    _keys(req, {"schema_version", "profile_id", "units", "part_name", "envelope_max_mm",
                "density_g_per_cm3", "mass_max_g", "checks"}, {"revision"})
    if (type(req["schema_version"]) is not int or req["schema_version"] != 1 or
            req["profile_id"] != "mechanical-part-v1" or req["units"] != "mm"):
        raise ValueError("Expected mechanical-part-v1 schema 1 in millimetres")
    if not isinstance(req["part_name"], str) or not NAME.fullmatch(req["part_name"]):
        raise ValueError("Part name must be a safe lowercase identifier")
    if "revision" in req and (not isinstance(req["revision"], str) or not re.fullmatch(r"R[0-9]{3,}", req["revision"])):
        raise ValueError("Invalid requirement revision")
    _vector(req["envelope_max_mm"], 3, positive=True)
    _number(req["density_g_per_cm3"], positive=True)
    _number(req["mass_max_g"], positive=True)
    if not isinstance(req["checks"], list) or not 1 <= len(req["checks"]) <= 100:
        raise ValueError("Provide one to 100 explicit checks")
    identifiers = set(BASE_IDS)
    dimensions = []
    bore_positions = []
    for check in req["checks"]:
        if not isinstance(check, dict):
            raise ValueError("Each check must be an object")
        ident = check.get("id")
        if not isinstance(ident, str) or not NAME.fullmatch(ident) or ident in identifiers:
            raise ValueError("Check IDs must be unique safe identifiers, excluding reserved IDs")
        identifiers.add(ident)
        kind = check.get("type")
        if kind == "dimensions":
            _keys(check, {"id", "type", "size_mm", "center_mm", "tolerance_mm"})
            _vector(check["size_mm"], 3, positive=True)
            _vector(check["center_mm"], 3)
            _tolerance(check["tolerance_mm"])
            if check["tolerance_mm"] >= min(check["size_mm"]) / 2:
                raise ValueError("Dimension tolerance cannot erase a required dimension")
            if any(size > maximum for size, maximum in zip(check["size_mm"], req["envelope_max_mm"])):
                raise ValueError("Required dimensions exceed the declared envelope")
            dimensions.append(check)
        elif kind == "through_holes":
            _keys(check, {"id", "type", "centers_xy_mm", "diameter_mm", "z_span_mm", "tolerance_mm"})
            _number(check["diameter_mm"], positive=True)
            _vector(check["z_span_mm"], 2)
            _tolerance(check["tolerance_mm"])
            span = check["z_span_mm"]
            if span[1] <= span[0] or check["tolerance_mm"] >= min(check["diameter_mm"], span[1]-span[0]) / 2:
                raise ValueError("Invalid through-hole span or tolerance")
            centers = check["centers_xy_mm"]
            if not isinstance(centers, list) or not 1 <= len(centers) <= 100:
                raise ValueError("Declare one to 100 hole positions per group")
            for center in centers:
                _vector(center, 2)
                # Reusing the same measured bore cannot satisfy two declarations.
                for previous, tolerance in bore_positions:
                    if math.dist(center, previous) <= tolerance + check["tolerance_mm"]:
                        raise ValueError("Hole positions must be distinct across every group")
                bore_positions.append((center, check["tolerance_mm"]))
        elif kind == "keepout":
            _keys(check, {"id", "type", "size_mm", "center_mm", "max_overlap_mm3"})
            _vector(check["size_mm"], 3, positive=True)
            _vector(check["center_mm"], 3)
            _number(check["max_overlap_mm3"])
            if not 0 <= check["max_overlap_mm3"] <= .001:
                raise ValueError("Keepout numerical allowance must be between 0 and 0.001 mm3")
        elif kind == "volume":
            _keys(check, {"id", "type", "min_mm3", "max_mm3"})
            _number(check["min_mm3"], positive=True)
            _number(check["max_mm3"], positive=True)
            if check["min_mm3"] > check["max_mm3"]:
                raise ValueError("Inverted volume range")
        else:
            raise ValueError("Unsupported check type: " + str(kind))
    if len(dimensions) != 1:
        raise ValueError("Exactly one dimensions check is required")
    # This bounded feature recognizer verifies full-height vertical bores. A
    # shorter cylindrical pocket must not become a 'through hole' merely by
    # declaring its blind depth as the desired surface span.
    height = dimensions[0]["size_mm"][2]
    middle = dimensions[0]["center_mm"][2]
    extent = [middle-height/2, middle+height/2]
    for check in req["checks"]:
        if check["type"] == "through_holes" and any(
                abs(a-b) > 1e-6 for a, b in zip(check["z_span_mm"], extent)):
            raise ValueError("Through-hole spans must cover the full declared Z extent")
    return req


def expected_check_ids(req):
    validate_requirements(req)
    base = {"part_solid", "envelope", "mass"}
    if any(check["type"] == "through_holes" for check in req["checks"]):
        base.add("hole_feature_count")
    return base | {check["id"] for check in req["checks"]}


def _report():
    return {"schema_version": 1, "status": "unknown", "units": "mm", "checks": [], "measurements": {},
            "scope": "Single solid digital geometry; declared bounds, vertical bores, keepouts, volume and uniform-density mass. No structural, physical manufacturing or assembly qualification."}


def _part(shape, name):
    parts = list(geometry.world_parts(shape))
    if len(parts) != 1 or parts[0].label != name:
        raise ValueError(f"Expected exactly one leaf labelled {name}")
    return parts[0]


def _through_holes(part, holes, check):
    matched, selected, probes = geometry._through_hole_match(part, holes, check["centers_xy_mm"],
        check["diameter_mm"], check["z_span_mm"], check["tolerance_mm"])
    return matched, {"bores": selected, "interior_probes": probes}


def _verify_part(part, req, report):
    valid = part.is_valid and len(part.solids()) == 1 and part.volume > 0
    geometry._check(report, "part_solid", valid,
                    {"valid": part.is_valid, "solid_count": len(part.solids()), "volume_mm3": part.volume},
                    "one valid positive-volume solid")
    if not valid:
        return geometry._finish(report)
    bounds = part.bounding_box()
    size, center = list(bounds.size), list(bounds.center())
    mass = part.volume * req["density_g_per_cm3"] / 1000
    holes = geometry._cylinders(part)
    part_measurements = {"volume_mm3": part.volume, "mass_g": mass,
                         "bounds_min_mm": list(bounds.min), "bounds_max_mm": list(bounds.max),
                         "dimensions_mm": size, "center_mm": center, "holes": holes}
    report["measurements"] = {"parts": {req["part_name"]: part_measurements}, "material_mass_g": mass}
    geometry._check(report, "envelope", all(a <= b + 1e-6 for a, b in zip(size, req["envelope_max_mm"])),
                    size, req["envelope_max_mm"])
    geometry._check(report, "mass", mass <= req["mass_max_g"], mass, {"maximum_g": req["mass_max_g"]})
    for check in req["checks"]:
        kind = check["type"]
        if kind == "dimensions":
            actual = {"size_mm": size, "center_mm": center}
            passed = all(abs(a-b) <= check["tolerance_mm"] for a, b in
                         zip(size + center, check["size_mm"] + check["center_mm"]))
        elif kind == "through_holes":
            passed, actual = _through_holes(part, holes, check)
        elif kind == "keepout":
            actual = geometry._overlap(part, geometry._box(check["size_mm"], check["center_mm"]))
            passed = actual <= check["max_overlap_mm3"]
        elif kind == "volume":
            actual = part.volume
            passed = check["min_mm3"] <= actual <= check["max_mm3"]
        geometry._check(report, check["id"], passed, actual, check)
    expected_holes = sum(len(c["centers_xy_mm"]) for c in req["checks"] if c["type"] == "through_holes")
    if expected_holes:
        geometry._check(report, "hole_feature_count", len(holes) == expected_holes, len(holes), expected_holes)
    return geometry._finish(report)


def verify_shape(shape, requirements):
    report = _report()
    try:
        validate_requirements(requirements)
        return _verify_part(_part(shape, requirements["part_name"]), requirements, report)
    except Exception as exc:
        return geometry._unknown(report, "geometry_readback", exc)


def verify(step_path, requirements):
    report = _report()
    try:
        validate_requirements(requirements)
        shape = geometry._kernel().import_step(str(Path(step_path).resolve(strict=True)))
        return _verify_part(_part(shape, requirements["part_name"]), requirements, report)
    except Exception as exc:
        return geometry._unknown(report, "geometry_readback", exc)


def verify_prints(step_path, print_dir, requirements):
    report = _report()
    try:
        validate_requirements(requirements)
        name = requirements["part_name"]
        root = Path(print_dir)
        reference = _part(geometry._kernel().import_step(str(Path(step_path).resolve(strict=True))), name)
        mesh = geometry.verify_print(root / f"{name}.stl", reference)
        report["checks"] = [{**check, "id": f"{name}_{check['id']}"} for check in mesh["checks"]]
        report["measurements"][name] = mesh["measurements"]
        delivered = _part(geometry._kernel().import_step(str((root / f"{name}.step").resolve(strict=True))), name)
        valid = delivered.is_valid and len(delivered.solids()) == 1 and delivered.volume > 0
        difference = max(0., delivered.volume + reference.volume - 2*geometry._overlap(delivered, reference)) if valid else None
        geometry._check(report, f"{name}_step_consistency", valid and difference <= .001,
                        {"valid": valid, "symmetric_difference_mm3": difference},
                        {"maximum_symmetric_difference_mm3": .001})
        return geometry._finish(report)
    except Exception as exc:
        return geometry._unknown(report, "print_readback", exc)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step")
    parser.add_argument("requirements")
    parser.add_argument("--out", required=True)
    parser.add_argument("--print-dir", "--prints-dir", dest="prints_dir")
    args = parser.parse_args(argv)
    try:
        requirements = json.loads(Path(args.requirements).read_text())
        report = verify(args.step, requirements)
        if args.prints_dir:
            prints = verify_prints(args.step, args.prints_dir, requirements)
            report["print_checks"] = prints
            statuses = {report["status"], prints["status"]}
            report["status"] = "unknown" if "unknown" in statuses else "fail" if "fail" in statuses else "pass"
        destination = Path(args.out)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
        print(json.dumps({"status": report["status"], "checks": len(report["checks"])}))
        return {"pass": 0, "fail": 1, "unknown": 2}[report["status"]]
    except Exception as exc:
        print(f"mechanical verification unavailable: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
