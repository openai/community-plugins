#!/usr/bin/env python3
"""Operator helper: actual CAD snapshots and PDF pages from a known fixture run.

Writes only a new study/media directory and that study's isolated .cad state.
It never rebuilds models, changes a frozen run, or registers the user's global CAD.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            result.update(block)
    return result.hexdigest()


def contained(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Run evidence path must stay inside its root")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Symlinked run evidence is not accepted")
    target = current.resolve()
    if not target.is_relative_to(root):
        raise ValueError("Run evidence escaped its root")
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--run", default="selected", help="selected, latest, or an exact recorded run ID")
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--plugin", type=Path, default=Path(__file__).resolve().parents[3] / "plugins/physical-engineering",
                        help="Installed Physical Engineering plugin directory; defaults to this repository's plugin")
    parser.add_argument("--output", type=Path, help="A new directory under STUDY/media")
    parser.add_argument("--views", nargs="+", choices=["assembly", "overview", "insertion", "exploded"],
                        default=["overview", "insertion", "exploded"])
    parser.add_argument("--pdf-only", action="store_true")
    parser.add_argument("--restart-owned-server", action="store_true",
                        help="Explicit operator intervention: stop and restart only STUDY/.cad")
    parser.add_argument("--pdftoppm", default=shutil.which("pdftoppm"))
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    args = parser.parse_args()
    if args.study.is_symlink():
        raise ValueError("Study root cannot be a symlink")
    study = args.study.resolve(strict=True)
    state = json.loads(contained(study, "study.json").read_text())
    if state.get("schema") != 1 or state.get("fixture") != "drone-module":
        raise ValueError("Expected this task's recorded Physical Engineering study")
    run_id = state.get(f"{args.run}_run") if args.run in {"selected", "latest"} else args.run
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
        raise ValueError("Select an existing recorded run")
    run = contained(study, f"runs/{run_id}")
    manifest_path = contained(run, "manifest.json")
    manifest = json.loads(manifest_path.read_text())
    for relative, expected in manifest["sha256"].items():
        artifact = contained(run, relative)
        if not artifact.is_file() or digest(artifact) != expected:
            raise ValueError(f"Frozen run artifact changed: {relative}")
    identifiers = json.loads(contained(run, "exports/documents.json").read_text())
    for name in args.views:
        identifiers[name] = str(uuid.UUID(identifiers[name]))
    destination = args.output or study / "media" / f"{run_id}-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    if destination.is_symlink():
        raise ValueError("Output cannot be a symlink")
    output = destination.resolve()
    media = contained(study, "media")
    if not output.is_relative_to(media) or output == media:
        raise ValueError("Choose a new output directory under STUDY/media")
    # Check every existing ancestor before creating the fresh output folder.
    contained(study, output.relative_to(study))
    output.mkdir(parents=True, exist_ok=False)
    backend_path = args.plugin.resolve() / "scripts/cad_backend.py"
    spec = importlib.util.spec_from_file_location("operator_cad_backend", backend_path)
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)
    cad_state = contained(study, ".cad")
    # Validate child state paths before any attempt to launch/stop a process.
    backend.environment(cad_state)
    receipt = {"schema": 1, "purpose": "Actual CAD snapshots and PDF previews from a hash-verified run",
               "run_id": run_id, "requirement_revision": manifest["requirement_revision"],
               "run_status": manifest["status"], "run_manifest_sha256": digest(manifest_path),
               "versions": backend.runtime_versions(args.runtime), "stages": [],
               "manual_interventions": [], "artifacts": [], "status": "unknown"}

    def save():
        (output / "capture-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")

    def stage(name, arguments):
        result = backend.run_cad(args.runtime, cad_state, arguments, study)
        result["stage"] = name
        receipt["stages"].append(result)
        save()
        return result

    def observed(path, kind, source_hash, **details):
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"Expected nonempty output: {path.name}")
        receipt["artifacts"].append({"path": path.name, "kind": kind, "sha256": digest(path),
                                     "bytes": path.stat().st_size, "source_sha256": source_hash,
                                     "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(), **details})
        save()

    try:
        pdf = contained(run, "exports/module-drawing.pdf")
        if not args.pdftoppm:
            raise RuntimeError("pdftoppm is required for actual PDF page previews")
        started = time.monotonic()
        command = [args.pdftoppm, "-png", "-scale-to", "2400", str(pdf), str(output / "drawing-page")]
        process = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
        receipt["stages"].append({"stage": "pdf-pages", "command": command, "returncode": process.returncode,
                                  "stdout": backend.sanitize(process.stdout), "stderr": backend.sanitize(process.stderr),
                                  "elapsed_seconds": round(time.monotonic()-started, 3)})
        if process.returncode:
            raise RuntimeError("PDF preview generation failed")
        pages = sorted(output.glob("drawing-page-*.png"))
        if not pages:
            raise RuntimeError("No PDF pages were rendered")
        for page in pages:
            observed(page, "actual PDF page preview", digest(pdf))
        # Render documented close-ups directly from the real PDF. These fixed
        # sheet coordinates belong to this fixture's qualified drawing layout.
        # Full sheets remain beside the crops, preserving context and labels.
        for name, region in [("drawing-frame-interface", (400, 180, 1000, 650)),
                             ("drawing-cavity", (1280, 865, 700, 300))]:
            x, y, width, height = region
            started = time.monotonic()
            command = [args.pdftoppm, "-f", "1", "-l", "1", "-singlefile", "-png", "-scale-to", "2400",
                       "-x", str(x), "-y", str(y), "-W", str(width), "-H", str(height),
                       str(pdf), str(output / name)]
            process = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
            receipt["stages"].append({"stage": name, "command": command, "returncode": process.returncode,
                                      "stdout": backend.sanitize(process.stdout), "stderr": backend.sanitize(process.stderr),
                                      "elapsed_seconds": round(time.monotonic()-started, 3)})
            if process.returncode:
                raise RuntimeError(f"PDF detail generation failed: {name}")
            observed(output / f"{name}.png", "actual PDF detail crop", digest(pdf),
                     page=1, scale_long_edge_px=2400, crop_xywh_px=list(region))
        if not args.pdf_only:
            if args.restart_owned_server:
                result = stage("stop-owned-server", ["stop"])
                receipt["manual_interventions"].append({"action": "Explicit restart of this study's isolated CAD server",
                                                         "returncode": result["returncode"]})
                if result["returncode"]:
                    raise RuntimeError("Owned CAD server did not stop cleanly")
            if stage("owned-runtime", ["doctor"])["returncode"]:
                raise RuntimeError("Owned CAD runtime did not start")
            for view in args.views:
                target = output / f"{view}.png"
                result = stage(f"snapshot-{view}", ["snapshot", identifiers[view], str(target),
                    "--camera", "iso", "--display", "shaded", "--width", str(args.width),
                    "--height", str(args.height), "--json"])
                if result["returncode"]:
                    raise RuntimeError(f"Snapshot {view} failed; preserve this attempt. If graphics access was denied, "
                                       "rerun the exact helper outside the sandbox with a new output and --restart-owned-server.")
                observed(target, f"actual CAD {view} snapshot", digest(contained(run, "src/model.py")))
        # Snapshots do not rewrite the frozen source, exports, drawing, or checks.
        for relative, expected in manifest["sha256"].items():
            if digest(contained(run, relative)) != expected:
                raise RuntimeError(f"Frozen run changed during capture: {relative}")
        receipt["status"] = "pass"
    except Exception as error:
        receipt["status"] = "failed"
        receipt["error"] = backend.sanitize(str(error))
    save()
    print(json.dumps({"status": receipt["status"], "output": str(output), "run_id": run_id,
                      "artifacts": receipt["artifacts"], "error": receipt.get("error")}, indent=2))
    return 0 if receipt["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
