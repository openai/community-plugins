"""Inspect an assembled STEP, its named occurrence BOM, preview and print/component exports."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import struct
import sys

import build123d as bd
import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepGProp import BRepGProp
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.GProp import GProp_GProps
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation

sys.dont_write_bytecode = True
import verify_geometry as geometry


def numbers(values):
    return [round(float(x), 4) for x in values]


def bounds(shape):
    box = shape.bounding_box()
    return np.array([tuple(box.min), tuple(box.max)])


def nodes(shape, parent=None, path=()):
    world = (bd.Location() if parent is None else parent) * shape.location
    key = (*path, shape.label)
    yield key, shape.located(world)
    for child in shape.children:
        yield from nodes(child, world, key)


def uncovered_sample(source_triangles, target_triangles, tolerance):
    def samples(triangles):
        vertices = np.unique(triangles.reshape(-1, 3), axis=0)
        return np.vstack((vertices, triangles.mean(axis=1)))

    points = samples(source_triangles)
    distances, _ = cKDTree(samples(target_triangles)).query(points, distance_upper_bound=tolerance)
    missing = points[~np.isfinite(distances)]
    if len(missing):
        covered = geometry._nearby_triangle_witnesses(missing, target_triangles, tolerance)
        missing = missing[~covered]
    if len(missing):
        tree = geometry._triangle_tree(target_triangles.tolist())
        for point in missing:
            if not geometry._mesh_covers(point, tree, tolerance**2):
                return numbers(point)
    return None


def compare_print_surface(reference, triangles):
    # Recover the bed translation from the STEP bounds, never from the STL's
    # center. Independently triangulate in millimeters at absolute deflection.
    lo, hi = bounds(reference)
    offset = np.array([-(hi[0]+lo[0])/2, -(hi[1]+lo[1])/2, -lo[2]])
    BRepMesh_IncrementalMesh(reference.wrapped, .02, False, .12, True)
    vertices, faces = reference.tessellate(.02, .12)
    reference_mesh = (np.asarray([tuple(v) for v in vertices]) + offset)[np.asarray(faces)]
    allowance = .1  # mm including both tessellations
    return {"allowance_mm": allowance,
            "unmatched_stl_point_mm": uncovered_sample(triangles, reference_mesh, allowance),
            "unmatched_step_point_mm": uncovered_sample(reference_mesh, triangles, allowance)}


def check_print(path, reference, check, *, coupon_spans=None):
    raw = path.read_bytes()
    count, = struct.unpack_from("<I", raw, 80)
    if not count or len(raw) != 84 + 50 * count:
        raise ValueError(f"Invalid binary STL length in {path.name}")
    rows = np.ndarray((count, 12), dtype="<f4", buffer=raw, offset=84, strides=(50, 4))
    if not np.isfinite(rows).all():
        raise ValueError(f"Nonfinite STL in {path.name}")
    triangles = rows[:, 3:].astype(float).reshape(-1, 3, 3)
    vertices, indices = np.unique(triangles.reshape(-1, 3), axis=0, return_inverse=True)
    faces = indices.reshape(-1, 3)
    edges = np.concatenate([faces[:, [i, (i+1) % 3]] for i in range(3)])
    _, inverse, counts = np.unique(np.sort(edges, axis=1), axis=0, return_inverse=True, return_counts=True)
    _, oriented_counts = np.unique(edges, axis=0, return_counts=True)
    shells = None
    if np.all(counts == 2):
        # Components must share an edge. Two shells meeting at a single vertex
        # can have two uses per edge and still be distinct print bodies.
        owners = (np.argsort(inverse.ravel()) % count).reshape(-1, 2)
        graph = coo_matrix((np.ones(len(owners)), (owners[:, 0], owners[:, 1])), shape=(count, count))
        shells = int(connected_components(graph, directed=False, return_labels=False))
    a, b, c = triangles.transpose(1, 0, 2)
    normals = np.cross(b-a, c-a)
    volume = np.einsum("ij,ij->", a, normals) / 6
    size = np.ptp(vertices, axis=0)
    result = {"triangles": count, "bad_edges": int(np.count_nonzero(counts != 2)),
              "duplicate_directed_edges": int(np.count_nonzero(oriented_counts != 1)),
              "degenerate": int(np.count_nonzero(np.linalg.norm(normals, axis=1) < 1e-11)),
              "connected_components": shells, "volume_mm3": round(volume, 3),
              "bed_z_mm": round(vertices[:, 2].min(), 4), "size_mm": numbers(size)}
    passed = (not any(result[key] for key in ("bad_edges", "duplicate_directed_edges", "degenerate"))
              and shells == 1 and volume > 0 and abs(vertices[:, 2].min()) <= .025)
    if reference is None:
        expected = coupon_spans[path.stem]
        size_error = max(abs(size - expected))
        result["expected_coupon_size_mm"] = expected
        passed = passed and size_error <= .1
    else:
        size_error = max(abs(size - np.ptp(bounds(reference), axis=0)))
        volume_error = abs(volume / reference.volume - 1)
        result.update(max_span_error_mm=round(size_error, 4), relative_volume_error=round(volume_error, 5))
        passed = passed and size_error <= .1 and volume_error <= .02
        if passed:
            surface = compare_print_surface(reference, triangles)
            result["surface"] = surface
            passed = surface["unmatched_stl_point_mm"] is None and surface["unmatched_step_point_mm"] is None
    check(f"print-{path.stem}", passed, result,
          "One closed, consistently wound mesh on Z=0 ± 0.025 mm. Spans within 0.1 mm. Installed parts within 2% volume and 0.1 mm at bidirectional STEP/STL surface samples.")


def glb_bounds(path):
    """Measure indexed triangles in the active GLB scene, composing all node placements."""
    raw = path.read_bytes()
    magic, version, total, json_len, kind = struct.unpack_from("<4sIII4s", raw)
    if (magic, version, total, kind) != (b"glTF", 2, len(raw), b"JSON"):
        raise ValueError("Invalid GLB header")
    data = json.loads(raw[20:20+json_len])
    size, kind = struct.unpack_from("<I4s", raw, 20+json_len)
    payload = memoryview(raw)[28+json_len:]
    if (len(payload), kind) != (size, b"BIN\0") or len(data["buffers"]) != 1:
        raise ValueError("Expected GLB with one embedded buffer")
    dtypes = {5121: "u1", 5123: "<u2", 5125: "<u4", 5126: "<f4"}

    def accessor(i, kind, allowed_types, width):
        value = data["accessors"][i]
        view = data["bufferViews"][value["bufferView"]]
        if (value["type"] != kind or value["componentType"] not in allowed_types or
                view["buffer"] != 0 or value["count"] < 1):
            raise ValueError("Unsupported GLB geometry accessor")
        dtype = np.dtype(dtypes[value["componentType"]])
        offset = value.get("byteOffset", 0)
        stride = view.get("byteStride", width * dtype.itemsize)
        last = offset + (value["count"]-1) * stride + width * dtype.itemsize
        start = view.get("byteOffset", 0)
        if min(start, offset) < 0 or stride < width * dtype.itemsize or last > view["byteLength"]:
            raise ValueError("GLB accessor exceeds buffer view")
        return np.ndarray((value["count"], width), dtype, payload, start+offset, (stride, dtype.itemsize))

    seen = set()
    objects = {}

    def walk(index, parent, names):
        if index in seen:
            raise ValueError("GLB node appears multiple times")
        seen.add(index)
        node = data["nodes"][index]
        key = (*names, node.get("name", ""))
        if "matrix" in node:
            local = np.asarray(node["matrix"]).reshape(4, 4).T
        else:
            local = np.eye(4)
            local[:3, :3] = Rotation.from_quat(node.get("rotation", [0, 0, 0, 1])).as_matrix()
            local[:3, :3] *= node.get("scale", [1, 1, 1])
            local[:3, 3] = node.get("translation", [0, 0, 0])
        transform = parent @ local
        if "mesh" in node:
            low, high = np.full(3, np.inf), np.full(3, -np.inf)
            for p in data["meshes"][node["mesh"]]["primitives"]:
                positions = accessor(p["attributes"]["POSITION"], "VEC3", {5126}, 3)
                indices = accessor(p["indices"], "SCALAR", {5121, 5123, 5125}, 1).ravel()
                if p.get("mode", 4) != 4 or len(indices) % 3 or indices.max() >= len(positions):
                    raise ValueError("Invalid indexed triangles")
                xyz = positions[np.unique(indices)] @ transform[:3, :3].T + transform[:3, 3]
                # glTF meters (Y up) to the study datum (millimeters, Z up).
                xyz = xyz[:, [0, 2, 1]] * [1000, -1000, 1000]
                low, high = np.minimum(low, xyz.min(axis=0)), np.maximum(high, xyz.max(axis=0))
            if key[1:] in objects or not np.isfinite([low, high]).all():
                raise ValueError("Duplicate named GLB leaf or nonfinite geometry")
            objects[key[1:]] = np.array([low, high])
        for child in node.get("children", []):
            walk(child, transform, key)

    for root in data["scenes"][data["scene"]]["nodes"]:
        walk(root, np.eye(4), ())
    return objects


def check_component(path, reference, check):
    exported = bd.import_step(path)
    observed, expected = exported.solids(), reference.solids()
    volume = sum(s.volume for s in observed)
    difference = abs(volume - sum(s.volume for s in expected))
    drift = float(np.max(abs(bounds(exported) - bounds(reference))))
    valid = exported.is_valid and reference.is_valid
    residual = 0.
    for left, right in ((exported, reference), (reference, exported)):
        cut = BRepAlgoAPI_Cut(left.wrapped, right.wrapped)
        if not cut.IsDone():
            raise ValueError(f"Incomplete solid difference for {path.name}")
        properties = GProp_GProps()
        BRepGProp.VolumeProperties_s(cut.Shape(), properties)
        residual += abs(properties.Mass())
    check(f"component-{path.stem}", valid and len(observed) == len(expected) > 0 and difference <= .01
          and drift <= .01 and residual <= .001,
          {"solid_count": len(observed), "valid": valid, "volume_mm3": round(volume, 3),
           "volume_difference_mm3": round(difference, 4), "bounds_error_mm": round(drift, 4),
           "symmetric_difference_mm3": round(residual, 5)},
          "Valid STEP with matching solid count, installed bounds within 0.01 mm and symmetric difference <= 0.001 mm³.")


def verify(folder, recipe):
    report = {"schema_version": 1, "status": "unknown", "checks": [], "inputs": {}}

    def source(name):
        path = folder / name
        if not path.is_file() or path.is_symlink() or folder.resolve() not in path.resolve().parents:
            raise ValueError(f"Expected regular exported file {name}")
        report["inputs"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
        return path

    def check(name, passed, actual, expected):
        report["checks"].append({"id": name, "status": "pass" if passed else "fail",
                                 "actual": actual, "expected": expected})

    try:
        recipe_bytes = (recipe / "assembly.json").read_bytes()
        config = json.loads(recipe_bytes)
        report["recipe_sha256"] = hashlib.sha256(recipe_bytes).hexdigest()
        if config["schema"] != 1:
            raise ValueError("Unsupported recipe schema")
        contract = dict(config["inspection"])
        if contract.pop("method", "declared") != "declared":
            raise ValueError("Expected declared inspection method")
        if set(contract) != {"parts", "unmodeled", "gaps"} or not contract["parts"]:
            raise ValueError("Expected parts, unmodeled and gaps in assembly inspection")
        scopes = config["bom"]["scopes"]
        if len(scopes) != 2 or len(set(scopes)) != 2:
            raise ValueError("BOM requires distinct installed and bench scopes")
        with source("bom.csv").open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        if len({r["ID"] for r in rows}) != len(rows) or any(r["Scope"] not in scopes for r in rows):
            raise ValueError("Duplicate IDs or unknown BOM scope")
        installed_rows = {r["ID"]: r for r in rows if r["Scope"] == scopes[0]}
        unmodeled = contract["unmodeled"]
        if not isinstance(unmodeled, dict) or any(not isinstance(reason, str) or not reason.strip()
                                                  for reason in unmodeled.values()):
            raise ValueError("Unmodeled installed items need an explanation")
        if set(unmodeled) & set(contract["parts"]) or set(installed_rows) != set(unmodeled) | set(contract["parts"]):
            raise ValueError("Each installed BOM item must be modeled or documented as unmodeled")
        tree = bd.import_step(source("assembly.step"))
        all_nodes = list(nodes(tree))[1:]
        parts = {"/".join(key[1:]): shape for key, shape in all_nodes}
        if not all_nodes or len(parts) != len(all_nodes) or any(not name or "/" in name
                                                              for key, _ in all_nodes for name in key[1:]):
            raise ValueError("Assembly requires unique, nonempty named paths")
        leaves = {path: shape for path, shape in parts.items() if not shape.children}
        ownership = {}
        files = set()
        installed = {}
        for key, occurrences in sorted(contract["parts"].items()):
            if not isinstance(occurrences, list) or not occurrences:
                raise ValueError(f"Empty or invalid occurrence list for {key}")
            for occurrence in occurrences:
                if (not {"node", "center_mm"} <= set(occurrence) or
                        set(occurrence) - {"node", "center_mm", "component", "print"}):
                    raise ValueError(f"Unexpected occurrence fields for {key}")
                name = occurrence["node"]
                if name not in parts:
                    raise ValueError(f"Required STEP occurrence is missing: {name}")
                part = parts[name]
                for leaf in leaves:
                    if leaf == name or leaf.startswith(name + "/"):
                        if leaf in ownership:
                            raise ValueError(f"STEP occurrence counted twice: {leaf}")
                        ownership[leaf] = key
                center = bounds(part).mean(axis=0)
                target = np.array(occurrence["center_mm"], dtype=float)
                if target.shape != (3,) or not np.isfinite(target).all():
                    raise ValueError(f"Invalid expected center for {name}")
                passed = abs(center-target).max() <= .05 and part.is_valid and bool(part.solids())
                check(f"placement-{name}", passed, {"center_mm": numbers(center), "bom_id": key},
                      {"center_mm": numbers(target), "tolerance_mm": .05, "valid_nonempty": True})
                for field, directory, extension in (("component", "components", ".step"), ("print", "print", ".stl")):
                    if field not in occurrence:
                        continue
                    file = Path(occurrence[field])
                    if file.parent != Path(directory) or file.suffix != extension or str(file) in files:
                        raise ValueError(f"Expected unique {directory} filename with {extension} suffix")
                    files.add(str(file))
                    path = source(str(file))
                    if field == "component":
                        check_component(path, part, check)
                    else:
                        check_print(path, part, check)
            installed[key] = {"step": len(occurrences), "bom": int(installed_rows[key]["Qty"])}
        if ownership.keys() != leaves.keys():
            raise ValueError(f"Unassigned STEP leaves: {sorted(leaves.keys()-ownership.keys())}")
        for directory in ("components", "print"):
            if (folder / directory).is_dir():
                actual = {p.relative_to(folder).as_posix() for p in (folder / directory).iterdir()}
                if actual != {p for p in files if p.startswith(directory + "/")}:
                    raise ValueError(f"Unexpected {directory} exports")
        check("installed-bom", all(v["step"] == v["bom"] > 0 for v in installed.values()),
              {"modeled": installed, "unmodeled": unmodeled, "bench_ids_excluded": sorted(
                  r["ID"] for r in rows if r["Scope"] == scopes[1])},
              "Each named STEP occurrence belongs to exactly one installed BOM line.")
        gap_ids = set()
        for gap in contract["gaps"]:
            if set(gap) != {"id", "nodes", "min_mm", "max_mm"} or not gap["id"] or gap["id"] in gap_ids:
                raise ValueError("Invalid or duplicate gap check")
            gap_ids.add(gap["id"])
            a, b = gap["nodes"]
            lo, hi = gap["min_mm"], gap["max_mm"]
            if a == b or not all(math.isfinite(v) for v in (lo, hi)) or not 0 < lo <= hi:
                raise ValueError("Gap checks require distinct parts and positive ordered limits")
            clearance = parts[a].distance_to(parts[b])
            check("gap-" + gap["id"], lo <= clearance <= hi,
                  {"nodes": [a, b], "measured_mm": round(clearance, 4)}, {"min_mm": lo, "max_mm": hi})
        preview = {"/".join(path): box for path, box in glb_bounds(source("assembly.glb")).items()}
        if preview.keys() != leaves.keys():
            raise ValueError("Active GLB meshes differ from the STEP occurrence tree")
        drift = max(float(np.max(abs(preview[name] - bounds(shape)))) for name, shape in leaves.items())
        check("glb-assembly", drift <= .15, {"named_meshes": len(leaves), "max_bounds_drift_mm": round(drift, 4)},
              "Indexed triangle bounds within 0.15 mm of every installed STEP leaf.")
        report["status"] = "pass" if all(c["status"] == "pass" for c in report["checks"]) else "fail"
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("recipe", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = verify(args.output, args.recipe)
    (args.report or args.output / "measurements.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"status": report["status"], "checks": len(report["checks"]),
                      "failed": [c["id"] for c in report["checks"] if c["status"] != "pass"],
                      **({"error": report["error"]} if "error" in report else {})}))
    return int(report["status"] != "pass")


if __name__ == "__main__":
    raise SystemExit(main())
