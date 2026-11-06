#!/usr/bin/env python3
"""Capture real construction stages by instrumenting the qualified fixture AST.

This executes the original tray/bridge functions with their original design
snapshot. Only @model decorators, the corresponding cad import, and inserted
snapshot calls differ. Intermediate parts are explanations, not qualified parts.
"""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import subprocess
import time

import build123d as bd
from prepare_assets import bounds, digest, stl_readback


MODEL_PATH = "plugins/physical-engineering/fixtures/drone-module/model.py"
SUPPORTED_MODEL_SHA256 = "ab5d870b60273b86452621fa128ac4714578a2f42429a853f0c7be7543ba6628"
STAGES = {
    "tray": [
        ("tray-01-base", 5, "Rounded tray floor"),
        ("tray-02-mounts-seats", 7, "Frame mounting pads, tapered connections and bridge seats"),
        ("tray-03-strap-guides", 8, "Webbing guide ears, slots and rounded mouths"),
        ("tray-04-rails-gussets", 13, "Rails, rear stop, upper rounds and external gussets"),
        ("tray-05-final", 18, "Floor relief and exact frame/bridge mounting bores"),
    ],
    "bridge": [
        ("bridge-01-deck", 0, "Rounded electronics deck"),
        ("bridge-02-posts", 1, "Four cylindrical support posts"),
        ("bridge-03-final", 8, "Upper edge rounds, relief windows and attachment/board bores"),
    ],
}


def total_volume(value):
    if value is None:
        return 0.0
    if isinstance(value, bd.Shape):
        return abs(value.volume)
    return sum(total_volume(item) for item in value)


def compare(final, reference):
    actual, expected = bounds(final), bounds(reference)
    result = {
        "source_volume_mm3": total_volume(final),
        "baseline_step_volume_mm3": total_volume(reference),
        "absolute_volume_difference_mm3": abs(total_volume(final) - total_volume(reference)),
        "source_bounds_mm": actual,
        "baseline_step_bounds_mm": expected,
        "maximum_bounds_difference_mm": max(abs(a-b) for axis in ("min", "max")
            for a, b in zip(actual[axis], expected[axis])),
        "source_minus_baseline_mm3": total_volume(final.cut(reference)),
        "baseline_minus_source_mm3": total_volume(reference.cut(final)),
        "limits": {"volume_mm3": .001, "bounds_mm": .00001, "symmetric_difference_mm3": .001},
    }
    result["symmetric_difference_mm3"] = result["source_minus_baseline_mm3"] + result["baseline_minus_source_mm3"]
    result["status"] = "pass" if (result["absolute_volume_difference_mm3"] <= .001 and
        result["maximum_bounds_difference_mm"] <= .00001 and result["symmetric_difference_mm3"] <= .001) else "fail"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True, help="Study containing the qualified baseline source")
    parser.add_argument("--baseline-run", required=True)
    parser.add_argument("--assets", type=Path, required=True, help="Existing asset directory with unchanged manifest.json")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[4])
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.baseline_run):
        raise ValueError("Baseline run must be an ID, not a path")
    if args.assets.is_symlink():
        raise ValueError("Assets cannot be a symlink")
    assets = args.assets.resolve(strict=True)
    main_manifest_path = assets / "manifest.json"
    main_manifest_hash = digest(main_manifest_path)
    main_manifest = json.loads(main_manifest_path.read_text())
    baseline = main_manifest["sources"]["baseline_a"]
    if baseline["run_id"] != args.baseline_run or baseline["status"] != "pass":
        raise ValueError("Requested run is not this presentation's passing A baseline")
    commit = main_manifest["qualified_plugin_commit"]
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ValueError("Qualified source commit is missing")
    run = args.study.resolve(strict=True) / "runs" / args.baseline_run
    source = run / "src/model.py"
    design = run / "src/design.json"
    manifest = json.loads((run / "manifest.json").read_text())
    if manifest["status"] != "pass":
        raise ValueError("Baseline run did not pass")
    for relative, expected in manifest["sha256"].items():
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts or not (run / relative).resolve().is_relative_to(run.resolve()):
            raise ValueError("Unsafe frozen source path")
        if digest(run / relative) != expected:
            raise ValueError("Frozen source hash changed: " + relative)
    source_files = ("manifest.json", "src/model.py", "src/design.json", "exports/tray.step", "exports/bridge.step")
    original_hashes = {name: digest(run / name) for name in source_files}
    for relative, actual in original_hashes.items():
        if actual != baseline["files_sha256"][relative]:
            raise ValueError("Baseline evidence mismatch: " + relative)
    committed = subprocess.run(["git", "show", f"{commit}:{MODEL_PATH}"], cwd=args.repo.resolve(),
                               capture_output=True, check=True).stdout
    if digest(source) != SUPPORTED_MODEL_SHA256 or hashlib.sha256(committed).hexdigest() != digest(source):
        raise ValueError("Construction checkpoints support only the qualified source layout")
    config = json.loads(design.read_text())
    if config["battery"]["selected"] != "A" or config["frame"]["pitch_mm"] != [100, 60]:
        raise ValueError("Expected the original A/100 baseline design")
    stage_order = [stage[0] for stages in STAGES.values() for stage in stages]
    for name in [*(key + ".stl" for key in stage_order), "construction-manifest.json"]:
        if (assets / name).exists() or (assets / name).is_symlink():
            raise ValueError("Construction output already exists: " + name)

    text = source.read_text()
    original_ast = ast.parse(text)
    transformed = copy.deepcopy(original_ast)
    stage_definitions = {}
    for node in transformed.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        node.decorator_list = [decorator for decorator in node.decorator_list
                               if not (isinstance(decorator, ast.Name) and decorator.id == "model")]
        if node.name not in STAGES:
            continue
        expected_count = 20 if node.name == "tray" else 10
        if len(node.body) != expected_count or not isinstance(node.body[-1], ast.Return):
            raise ValueError("Qualified function layout changed")
        checkpoints = {index: (key, description) for key, index, description in STAGES[node.name]}
        new_body = []
        previous = -1
        for index, statement in enumerate(node.body):
            new_body.append(statement)
            if index not in checkpoints:
                continue
            key, description = checkpoints[index]
            first_line = node.body[previous + 1].lineno
            last_line = statement.end_lineno
            segment = "\n".join(text.splitlines()[first_line - 1:last_line])
            stage_definitions[key] = {
                "part": node.name, "description": description,
                "source_function": node.name,
                "after_statement_index": index,
                "source_lines": [first_line, last_line],
                "source_operations_sha256": hashlib.sha256(segment.encode()).hexdigest(),
            }
            new_body.append(ast.Expr(ast.Call(func=ast.Name(id="__construction_snapshot__", ctx=ast.Load()),
                args=[ast.Constant(key), ast.Name(id="part", ctx=ast.Load())], keywords=[])))
            previous = index
        node.body = new_body
    transformed.body = [node for node in transformed.body if not (
        isinstance(node, ast.ImportFrom) and node.module == "cad" and
        len(node.names) == 1 and node.names[0].name == "model")]
    ast.fix_missing_locations(transformed)
    stages = {}
    started = {}

    def snapshot(key, shape):
        if key in stages:
            raise ValueError("A construction checkpoint ran more than once")
        if not shape.is_valid or len(shape.solids()) != 1 or shape.volume <= 0:
            raise ValueError("Construction checkpoint is not one valid positive solid: " + key)
        target = assets / (key + ".stl")
        if not bd.export_stl(shape, target, tolerance=.02, angular_tolerance=.1):
            raise ValueError("STL export failed: " + key)
        mesh = stl_readback(target)
        stages[key] = {
            **stage_definitions[key], "path": target.name, "sha256": digest(target), "bytes": target.stat().st_size,
            "cad_volume_mm3": shape.volume, "cad_bounds_mm": bounds(shape),
            "solid_count": len(shape.solids()), "solid_valid": shape.is_valid,
            "translation_mm": [0, 0, 0], "rotation_xyz_radians": [0, 0, 0], "scale": [1, 1, 1],
            "elapsed_seconds_in_source_function": round(time.monotonic() - started[stage_definitions[key]["part"]], 6),
            **mesh,
        }
        stages[key]["mesh_volume_relative_error"] = abs(mesh["mesh_volume_mm3"] - shape.volume) / shape.volume
        if stages[key]["mesh_volume_relative_error"] > .005:
            raise ValueError("Construction mesh exceeds source tessellation volume tolerance")

    namespace = {"__name__": "qualified_construction_source", "__file__": str(source),
                 "__construction_snapshot__": snapshot}
    exec(compile(transformed, str(source), "exec"), namespace)
    final_comparisons = {}
    for part in STAGES:
        started[part] = time.monotonic()
        final = namespace[part]()
        reference = bd.import_step(run / f"exports/{part}.step")
        final_comparisons[part] = compare(final, reference)
        if final_comparisons[part]["status"] != "pass":
            raise ValueError("Raw final geometry differs from the qualified baseline STEP: " + part)
    if list(stages) != stage_order:
        raise ValueError("Construction checkpoint order changed")
    for relative, expected in manifest["sha256"].items():
        if digest(run / relative) != expected:
            raise ValueError("Qualified baseline changed during construction")
    if digest(main_manifest_path) != main_manifest_hash:
        raise ValueError("Main presentation evidence changed during construction")
    result = {
        "schema": 1, "status": "pass",
        "purpose": "Later construction explanation from genuine original source operations; intermediate solids are not acceptance-qualified deliverables",
        "baseline_manifest_sha256": main_manifest_hash,
        "qualified_plugin_commit": commit, "baseline_run_id": args.baseline_run,
        "source_files_sha256": original_hashes,
        "generator_sha256": digest(__file__),
        "instrumented_ast_sha256": hashlib.sha256(ast.dump(transformed, include_attributes=False).encode()).hexdigest(),
        "instrumentation": "Remove only @model decorators and their cad import; inject snapshot callback after named original top-level operations; retain original function bodies and design inputs",
        "coordinate_frame": {"units": "mm", "up": "+Z", "handedness": "right", "all_mesh_vertices": "assembly world coordinates"},
        "stage_order": stage_order, "stages": stages, "final_comparisons": final_comparisons,
        "runtime": {name: importlib.metadata.version(name) for name in ("build123d", "cadquery-ocp-novtk")},
        "qualified_inputs_unchanged": True, "main_evidence_manifest_unchanged": True,
        "geometry_changed_by_instrumentation": False,
        "interpretation": "Stage timing belongs to this AST derivation; the animation is not a continuous live-screen recording. The fresh original-CLI cross-check has a separate receipt.",
    }
    (assets / "construction-manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": "pass", "stage_count": len(stages), "construction_manifest_sha256": digest(assets / "construction-manifest.json"),
                      "final_comparisons": final_comparisons}, indent=2))


if __name__ == "__main__":
    main()
