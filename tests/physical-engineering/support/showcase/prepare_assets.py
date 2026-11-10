#!/usr/bin/env python3
"""Derive presentation meshes from frozen CAD, without launching the CAD server."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.metadata
import json
import math
import re
from pathlib import Path
import shutil
import struct
import subprocess
import sys

import build123d as bd


MODEL_PATH = "plugins/physical-engineering/fixtures/drone-module/model.py"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def box(size, center):
    return bd.Pos(*center) * bd.Box(*size)


def intersect(first, second):
    common = first.intersect(second)
    if common is None:
        return None
    return common if isinstance(common, bd.Shape) else bd.Compound(children=list(common))


def volume(shape):
    return 0.0 if shape is None else abs(shape.volume)


def bounds(shape):
    bb = shape.bounding_box()
    return {"min": list(bb.min), "max": list(bb.max), "size": list(bb.size)}


def stl_readback(path):
    """Inspect actual binary mesh vertices; no assumption about export transforms."""
    data = path.read_bytes()
    if len(data) < 84:
        raise ValueError(f"STL is too short: {path.name}")
    count = struct.unpack_from("<I", data, 80)[0]
    if len(data) != 84 + count * 50 or not count:
        raise ValueError(f"Expected a nonempty binary STL: {path.name}")
    low, high = [float("inf")] * 3, [float("-inf")] * 3
    signed_volume = 0.0
    for index in range(count):
        values = struct.unpack_from("<12f", data, 84 + index * 50)
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f"STL has nonfinite coordinates: {path.name}")
        a, b, c = values[3:6], values[6:9], values[9:12]
        for p in (a, b, c):
            for axis in range(3):
                low[axis], high[axis] = min(low[axis], p[axis]), max(high[axis], p[axis])
        signed_volume += (a[0]*(b[1]*c[2]-b[2]*c[1]) +
                          a[1]*(b[2]*c[0]-b[0]*c[2]) +
                          a[2]*(b[0]*c[1]-b[1]*c[0])) / 6
    return {"triangle_count": count, "bounds_mm": {"min": low, "max": high,
            "size": [b-a for a, b in zip(low, high)]}, "mesh_volume_mm3": abs(signed_volume)}


def mesh_name(key):
    if key.startswith(("tray_", "bridge_")):
        part, letter = key.split("_")
        return f"{letter}-{part}"
    return key.replace("_", "-")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True, help="Existing frozen drone-module study")
    parser.add_argument("--output", type=Path, required=True, help="New presentation asset directory")
    parser.add_argument("--baseline-run", required=True, help="Passing battery A run ID")
    parser.add_argument("--failed-run", required=True, help="Failed unchanged A geometry evaluated with battery B")
    parser.add_argument("--corrected-run", required=True, help="Passing corrected battery B run ID")
    parser.add_argument("--reproduced-study", type=Path,
                        help="Optional actual reproduction study; never inferred from a local fixed path")
    parser.add_argument("--reproduced-run", help="Optional reproduction run ID; defaults to selected_run, then latest_run")
    parser.add_argument("--source-commit", required=True, help="Qualified plugin Git commit, 7-40 hexadecimal digits")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[4],
                        help="Repository containing the qualified source commit")
    args = parser.parse_args()
    repo = args.repo.resolve(strict=True)
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", args.source_commit):
        raise ValueError("Source commit must be a hexadecimal Git commit ID")
    qualified_commit = subprocess.run(["git", "rev-parse", "--verify", args.source_commit + "^{commit}"],
                                     cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    runs = {"baseline_a": args.baseline_run, "unchanged_a_with_b": args.failed_run,
            "corrected_b": args.corrected_run}
    if any(not re.fullmatch(r"[A-Za-z0-9_-]+", run_id) for run_id in runs.values()):
        raise ValueError("Run IDs cannot contain paths")
    study = args.study.resolve(strict=True)
    run_roots = {name: study for name in runs}
    reproduction = None
    reproduction_path = None
    reproduction_hash = None
    if args.reproduced_run and not args.reproduced_study:
        raise ValueError("A reproduced run requires --reproduced-study")
    if args.reproduced_study:
        reproduced_study = args.reproduced_study.resolve(strict=True)
        state = json.loads((reproduced_study / "study.json").read_text())
        reproduced_run = args.reproduced_run or state.get("selected_run") or state.get("latest_run")
        if not isinstance(reproduced_run, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", reproduced_run):
            raise ValueError("Select an actual recorded reproduction run")
        reproduction_path = reproduced_study / "reproduction.json"
        reproduction_hash = digest(reproduction_path)
        reproduction = json.loads(reproduction_path.read_text())
        if (reproduction.get("status") != "pass" or reproduction.get("differences") != [] or
                reproduction.get("source_run") != runs["corrected_b"] or
                reproduction.get("reproduced_run") != reproduced_run or
                reproduction.get("independent_cad_state") is not True):
            raise ValueError("Reproduction record does not support this corrected/reproduced pair")
        tolerance = reproduction.get("measurement_tolerance")
        if not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance) or tolerance <= 0:
            raise ValueError("Reproduction comparison lacks a positive finite tolerance")
        runs["reproduced_b"] = reproduced_run
        run_roots["reproduced_b"] = reproduced_study
    output = args.output.resolve()
    if output.exists():
        raise ValueError("Choose a new asset output directory; existing evidence is never overwritten")
    commit_model = subprocess.run(["git", "show", f"{qualified_commit}:{MODEL_PATH}"],
                                  cwd=repo, capture_output=True, check=True).stdout
    qualified_model_hash = hashlib.sha256(commit_model).hexdigest()
    source_runs = {}
    for name, run_id in runs.items():
        run = run_roots[name] / "runs" / run_id
        if run.is_symlink():
            raise ValueError("Run evidence cannot be a symlink")
        manifest = json.loads((run / "manifest.json").read_text())
        for relative, expected in manifest["sha256"].items():
            if Path(relative).is_absolute() or ".." in Path(relative).parts:
                raise ValueError("Manifest evidence path escaped its run")
            if not (run / relative).resolve(strict=True).is_relative_to(run.resolve()):
                raise ValueError("Manifest evidence escaped its run through a symlink")
            if digest(run / relative) != expected:
                raise ValueError(f"Frozen run hash mismatch: {run_id}/{relative}")
        if digest(run / "src/model.py") != qualified_model_hash:
            raise ValueError(f"Run model differs from qualified commit: {run_id}")
        source_runs[name] = {"path": run, "manifest": manifest,
                             "requirements": json.loads((run / "requirements.json").read_text()),
                             "checks": json.loads((run / "checks.json").read_text())}
    for name, status, battery in (("baseline_a", "pass", "A"),
                                  ("unchanged_a_with_b", "fail", "B"),
                                  ("corrected_b", "pass", "B")):
        if (source_runs[name]["manifest"]["status"] != status or
                source_runs[name]["requirements"]["battery"]["selected"] != battery):
            raise ValueError(f"Run does not have the expected role: {name}")
    if reproduction:
        reproduced = source_runs["reproduced_b"]
        if (reproduced["manifest"]["status"] != "pass" or reproduced["checks"]["status"] != "pass" or
                reproduced["requirements"] != source_runs["corrected_b"]["requirements"]):
            raise ValueError("Actual reproduced run does not match the corrected requirement snapshot")
    for key in ("tray.stl", "bridge.stl"):
        if digest(source_runs["baseline_a"]["path"] / "exports" / key) != digest(source_runs["unchanged_a_with_b"]["path"] / "exports" / key):
            raise ValueError("Fail demonstration did not retain the original manufacturing mesh")
    output.mkdir(parents=True)
    result = {
        "schema": 1,
        "purpose": "Post-recording explanatory CAD meshes derived from verified STEP and STL; presentation only",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "qualified_plugin_commit": qualified_commit,
        "qualified_model_sha256": qualified_model_hash,
        "generator_sha256": digest(__file__),
        "runtime": {"python": sys.version.split()[0], **{name: importlib.metadata.version(name)
                    for name in ("cad", "cad-server", "build123d", "cadquery-ocp-novtk")}},
        "coordinate_frame": {"units": "mm", "handedness": "right", "up": "+Z",
            "origin": "frame-plane center", "x": "battery length; insertion moves from negative X toward zero",
            "y": "battery width", "z": "height above frame datum", "all_mesh_vertices": "assembly world coordinates",
            "default_object_translation_mm": [0, 0, 0], "default_object_rotation_xyz_radians": [0, 0, 0],
            "default_object_scale": [1, 1, 1],
            "threejs": "Keep Z-up with camera.up.set(0,0,1). Do not center individual mesh geometry. Alternatively rotate one common parent by -pi/2 about X for Y-up (x,y,z -> x,z,-y). Scale that parent by 0.001 only if the scene uses metres."},
        "materials": {
            "tray": {"color": "#328BB5", "metalness": 0, "roughness": 0.38},
            "bridge": {"color": "#394B5B", "metalness": 0, "roughness": 0.38},
            "battery_envelope": {"color": "#F1AD4C", "metalness": 0, "roughness": 0.4},
            "board_envelope": {"color": "#389D80", "metalness": 0, "roughness": 0.5},
            "collision": {"color": "#EF4056", "metalness": 0, "roughness": 0.45},
        },
        "material_note": "Display colors and rendering parameters only; no physical material or structural claim.",
        "limits": ["Battery and board are requirements envelope surrogates, not purchased component geometry.",
                   "Exploded, sectioned, translucent and collision-overlay states are later explanations, not footage from the original recording.",
                   "No flight, structural, durability, thermal or retention validation is established by these meshes.",
                   "Do not interpolate between A and B trays as if intermediate shapes were verified. Crossfade or replace discrete verified geometry."],
        "sources": {}, "meshes": {}, "scenes": {}, "measurements": {},
    }
    for name, item in source_runs.items():
        run = item["path"]
        result["sources"][name] = {
            "run_id": runs[name], "status": item["manifest"]["status"],
            "revision": item["manifest"]["requirement_revision"],
            "files_sha256": {relative: digest(run / relative) for relative in
               ("manifest.json", "requirements.json", "checks.json", "src/model.py", "src/design.json",
                "exports/assembly.step", "exports/tray.step", "exports/bridge.step", "exports/tray.stl", "exports/bridge.stl")},
        }

    if reproduction:
        result["reproduction"] = {key: reproduction[key] for key in
            ("status", "source_run", "reproduced_run", "measurement_tolerance", "differences", "independent_cad_state")}
        result["reproduction"]["source_files_sha256"] = {
            "reproduction.json": reproduction_hash,
            "reproduced_checks.json": digest(source_runs["reproduced_b"]["path"] / "checks.json"),
            "corrected_checks.json": digest(source_runs["corrected_b"]["path"] / "checks.json"),
        }
        result["reproduction"]["geometry_note"] = "Reproduced meshes are copied from the actual independently rebuilt run, not duplicates of the corrected scene mesh."

    def add_mesh(name, material, shape=None, copy_from=None, sources=None, role="qualified polymer geometry"):
        path = output / f"{name}.stl"
        if copy_from is not None:
            shutil.copyfile(copy_from, path)
        elif shape is not None:
            if not bd.export_stl(shape, path, tolerance=0.02, angular_tolerance=0.1):
                raise ValueError(f"Mesh export failed: {name}")
        else:
            raise ValueError("Mesh must have a real source")
        readback = stl_readback(path)
        metadata = {"path": path.name, "sha256": digest(path), "bytes": path.stat().st_size,
                    "material": material, "role": role, "source": sources,
                    "translation_mm": [0, 0, 0], "rotation_xyz_radians": [0, 0, 0], "scale": [1, 1, 1],
                    "copied_original_bytes": copy_from is not None, **readback}
        if shape is not None:
            metadata["cad_volume_mm3"] = volume(shape)
            metadata["cad_bounds_mm"] = bounds(shape)
            metadata["mesh_volume_relative_error"] = abs(readback["mesh_volume_mm3"]-volume(shape))/volume(shape)
            metadata["mesh_bounds_max_error_mm"] = max(abs(a-b) for axis in ("min", "max")
                for a, b in zip(readback["bounds_mm"][axis], metadata["cad_bounds_mm"][axis]))
            if metadata["mesh_bounds_max_error_mm"] > 0.02:
                raise ValueError(f"Derived mesh bounds disagree with source tolerance: {name}")
            if metadata["mesh_volume_relative_error"] > 0.005:
                raise ValueError(f"Derived mesh does not preserve volume within source tolerance: {name}")
        result["meshes"][name] = metadata

    shapes = {}
    for state, suffix in (("baseline_a", "a"), ("corrected_b", "b")):
        run = source_runs[state]["path"]
        for part in ("tray", "bridge"):
            shape = bd.import_step(run / f"exports/{part}.step")
            shapes[f"{part}_{suffix}"] = shape
            add_mesh(f"{suffix}-{part}", part, shape=shape, copy_from=run / f"exports/{part}.stl",
                     sources={"run": state, "file": f"exports/{part}.stl"})

    if reproduction:
        run = source_runs["reproduced_b"]["path"]
        for part in ("tray", "bridge"):
            shape = bd.import_step(run / f"exports/{part}.step")
            add_mesh(f"reproduced-{part}", part, shape=shape, copy_from=run / f"exports/{part}.stl",
                     sources={"run": "reproduced_b", "file": f"exports/{part}.stl"},
                     role="actual independently reproduced polymer geometry")

    requirements = source_runs["unchanged_a_with_b"]["requirements"]
    for letter in ("A", "B"):
        shape = box(requirements["battery"]["envelopes_mm"][letter], requirements["battery"]["seated_center_mm"])
        shapes[f"battery_{letter.lower()}"] = shape
        add_mesh(f"battery-{letter.lower()}", "battery_envelope", shape=shape,
                 sources={"run": "unchanged_a_with_b", "file": "requirements.json", "fields": [f"battery.envelopes_mm.{letter}", "battery.seated_center_mm"]},
                 role="display-only exact battery envelope surrogate")
    shapes["board"] = box(requirements["board"]["envelope_mm"], requirements["board"]["center_mm"])
    add_mesh("board", "board_envelope", shape=shapes["board"],
             sources={"run": "unchanged_a_with_b", "file": "requirements.json", "fields": ["board.envelope_mm", "board.center_mm"]},
             role="display-only exact board envelope surrogate; no invented electronics")

    failed_assembly = bd.import_step(source_runs["unchanged_a_with_b"]["path"] / "exports/assembly.step")
    collision = intersect(failed_assembly, shapes["battery_b"])
    expected_collision = source_runs["unchanged_a_with_b"]["checks"]["measurements"]["overlap_mm3"]["battery"]
    if abs(volume(collision)-expected_collision) > 1e-5:
        raise ValueError("Actual Boolean collision volume disagrees with frozen acceptance evidence")
    add_mesh("failed-b-intersection", "collision", shape=collision,
             sources={"run": "unchanged_a_with_b", "operation": "intersection(exports/assembly.step, exact battery B seated envelope)"},
             role="actual interfering solid volume at an attempted invalid seated pose")
    for side, solid in zip(("negative-y", "positive-y"), sorted(collision.solids(), key=lambda s: s.center().Y)):
        add_mesh(f"collision-{side}", "collision", shape=solid,
                 sources={"run": "unchanged_a_with_b", "operation": "separate connected solid from failed-b-intersection"}, role="actual interfering solid")
    travel = requirements["battery"]["insertion_translation_mm"]
    size = requirements["battery"]["envelopes_mm"]["B"]
    center = requirements["battery"]["seated_center_mm"]
    sweep = box([size[0]+abs(travel[0]), size[1], size[2]], [center[0]+travel[0]/2, center[1], center[2]])
    swept_collision = intersect(failed_assembly, sweep)
    expected_sweep = source_runs["unchanged_a_with_b"]["checks"]["measurements"]["overlap_mm3"]["insertion"]
    if abs(volume(swept_collision)-expected_sweep) > 1e-5:
        raise ValueError("Actual swept intersection disagrees with frozen acceptance evidence")
    add_mesh("collision-insertion-sweep", "collision", shape=swept_collision,
             sources={"run": "unchanged_a_with_b", "operation": "intersection(exports/assembly.step, complete battery B translation envelope)"},
             role="total swept-path interfering volume; not an instantaneous collision state")

    # A 2 mm-thick CAD-derived section is useful for a readable, true fit diagram.
    section_tool = box([2, 160, 120], [0, 0, 30])
    collision_section = intersect(collision, section_tool)
    add_mesh("failed-b-intersection-section", "collision", shape=collision_section,
             sources={"mesh": "failed-b-intersection", "operation": "CAD intersection with X[-1,1] mm slab"},
             role="true explanatory section of the actual invalid seated-pose collision; not manufacturing output")
    for key in ("tray_a", "tray_b", "battery_a", "battery_b", "bridge_a", "board"):
        section = intersect(shapes[key], section_tool)
        material = "battery_envelope" if key.startswith("battery") else ("board_envelope" if key == "board" else key.split("_")[0])
        add_mesh(mesh_name(key)+"-section", material, shape=section,
                 sources={"mesh": mesh_name(key), "operation": "CAD intersection with X[-1,1] mm slab"},
                 role="later explanatory CAD section; not manufacturing output")

    for name, item in source_runs.items():
        measured, req = item["checks"]["measurements"], item["requirements"]
        chosen = req["battery"]["selected"]
        width = req["battery"]["envelopes_mm"][chosen][1]
        by = req["battery"]["seated_center_mm"][1]
        cavity = measured["cavity_inside_y_mm"]
        result["measurements"][name] = {
            "battery": chosen, "battery_size_mm": req["battery"]["envelopes_mm"][chosen],
            "battery_bounds_y_mm": [by-width/2, by+width/2], "cavity_inside_y_mm": cavity,
            "cavity_width_mm": measured["cavity_width_mm"], "clearance_each_side_mm": measured["lateral_clearance_mm"],
            "clearance_witness_segments_mm": [[[0, cavity[0], 7], [0, by-width/2, 7]], [[0, by+width/2, 7], [0, cavity[1], 7]]],
            "witness_note": "Negative reported clearance means the segment is an overlap, not free space.",
            "polymer_mass_g": measured["polymer_mass_g"], "overlap_mm3": measured["overlap_mm3"],
            "frame_pitch_mm": measured["frame_pitch_mm"], "board_pitch_mm": measured["board_pitch_mm"],
            "failed_checks": [check["id"] for check in item["checks"]["checks"] if check["status"] != "pass"],
        }
        suffix = "reproduced" if name == "reproduced_b" else ("b" if name == "corrected_b" else "a")
        result["scenes"][name] = {"meshes": [f"{suffix}-tray", f"{suffix}-bridge", f"battery-{chosen.lower()}", "board"],
            "status": item["manifest"]["status"], "source_run": runs[name], "all_object_transforms": "identity; vertices already have seated world coordinates"}
    result["scenes"]["unchanged_a_with_b"]["collision_overlay"] = "failed-b-intersection"
    result["scenes"]["unchanged_a_with_b"]["collision_section_overlay"] = "failed-b-intersection-section"
    result["presentation_transforms"] = {
        "insertion": {"component": "battery", "from_translation_mm": travel, "to_translation_mm": [0, 0, 0],
                      "service_state": requirements["battery"]["service_state"]},
        "exploded": {"tray": [0, 0, 0], "bridge": [0, 0, 25], "battery": [-120, 0, 0], "board": [0, 0, 40],
                     "note": "Offsets relative to exported seated geometry, matching the model's display-only exploded view."},
        "section": {"x_min_mm": -1, "x_max_mm": 1, "view_direction": "+X", "suggested_camera_up": [0, 0, 1]},
    }
    # Read all frozen hashes once more after derivation to prove inputs stayed intact.
    for item in source_runs.values():
        for relative, expected in item["manifest"]["sha256"].items():
            if digest(item["path"] / relative) != expected:
                raise ValueError("Frozen input changed during derivative preparation")
    if reproduction_path and digest(reproduction_path) != reproduction_hash:
        raise ValueError("Reproduction comparison changed during derivation")
    result["verification"] = {"all_frozen_source_hashes_match_before_and_after": True,
        "failed_run_uses_baseline_stl_bytes": True, "cad_seated_collision_volume_mm3": volume(collision),
        "frozen_seated_collision_volume_mm3": expected_collision, "cad_insertion_sweep_collision_volume_mm3": volume(swept_collision),
        "frozen_insertion_sweep_collision_volume_mm3": expected_sweep,
        "derivative_mesh_linear_tolerance_mm": 0.02, "derivative_mesh_angular_tolerance_radians": 0.1,
        "all_derivative_mesh_volume_errors_under_source_tolerance": True,
        "all_mesh_bounds_under_source_tolerance": True,
        "seated_collision_section_volume_mm3": volume(collision_section),
        "actual_reproduced_meshes_included": reproduction is not None}
    (output / "manifest.json").write_text(json.dumps(result, indent=2)+"\n")
    (output / "README.md").write_text(f"""# CAD presentation assets

If reproduced-*.stl files are present, they are exact copies from the actual independent rebuild, supported by the reproduction comparison and source hashes in manifest.json.

These are later explanatory assets derived from the qualified source commit and frozen refined-study runs, not footage from the recording. Read manifest.json for hashes and all exact measurements.

Load STL with THREE.STLLoader. All vertices are millimetres in the same right-handed Z-up world frame; use identity mesh transforms. Set camera.up to (0,0,1), or rotate one common parent -pi/2 about X. Never center meshes individually. Battery and board are explicitly synthetic envelope boxes.

The original a-tray/a-bridge and b-tray/b-bridge STL bytes are copied unchanged. The failed B scene uses a-tray/a-bridge plus battery-b. failed-b-intersection is the real intersection at an attempted invalid seated pose ({volume(collision):.7f} mm³), while collision-insertion-sweep is the union of material encountered along the complete translational path. Do not present the sweep mesh as an instantaneous collision. Corrected B has zero positive intersection under the original acceptance threshold.

Signed clearances (negative means overlap) are baseline A {result["measurements"]["baseline_a"]["clearance_each_side_mm"]} mm, unchanged A tray with B {result["measurements"]["unchanged_a_with_b"]["clearance_each_side_mm"]} mm, and corrected B {result["measurements"]["corrected_b"]["clearance_each_side_mm"]} mm. Optional *-section files are true CAD intersections with the 2 mm-thick X[-1,1] slice; they make the widths readable without changing original meshes. failed-b-intersection-section.stl is the same X[-1,1] CAD section of the true interfering volume. Section geometry is explanatory, not an additional printable part.

For insertion move only the battery by an offset from (-156,0,0) to (0,0,0). For an exploded explanation, offsets from seated coordinates are battery (-120,0,0), bridge (0,0,25), board (0,0,40), tray (0,0,0). A tray and B tray are discrete verified models: use a labelled crossfade or replace, not an unqualified geometric morph. Rendering colors, transparency and finish are presentation choices. No structural, flight, durability or physical retention result is claimed.
""")
    qa = {
        "schema": 1, "status": "pass", "purpose": "Numeric mesh readback and provenance QA for later presentation assets",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "source_commit": qualified_commit,
        "manifest_sha256": digest(output / "manifest.json"), "meshes_checked": len(result["meshes"]),
        "total_mesh_bytes": sum(item["bytes"] for item in result["meshes"].values()),
        "maximum_mesh_to_cad_bounds_delta_mm": max(item["mesh_bounds_max_error_mm"] for item in result["meshes"].values()),
        "maximum_mesh_volume_relative_error": max(item["mesh_volume_relative_error"] for item in result["meshes"].values()),
        "allowed_mesh_bounds_delta_mm": .02, "allowed_mesh_volume_relative_error": .005,
        "bridge_a_b_bytes_identical": result["meshes"]["a-bridge"]["sha256"] == result["meshes"]["b-bridge"]["sha256"],
        "cad_seated_collision_difference_from_frozen_mm3": abs(volume(collision)-expected_collision),
        "cad_swept_collision_difference_from_frozen_mm3": abs(volume(swept_collision)-expected_sweep),
        "collision_section_volume_mm3": volume(collision_section), "frozen_sources_unchanged": True,
        "note": "No CAD server, screenshots, image edits, new dependencies, or qualified-source modifications were used.",
    }
    qa["hash_failures"] = [name for name, item in result["meshes"].items()
                           if digest(output / item["path"]) != item["sha256"]]
    if qa["hash_failures"]:
        raise ValueError("Output mesh changed after its readback")
    (output / "qa.json").write_text(json.dumps(qa, indent=2)+"\n")
    print(json.dumps({"status": "pass", "meshes": len(result["meshes"]), "seated_collision_mm3": volume(collision),
                      "insertion_sweep_collision_mm3": volume(swept_collision), "manifest_sha256": digest(output/"manifest.json")}, indent=2))


if __name__ == "__main__":
    main()
