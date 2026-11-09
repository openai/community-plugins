#!/usr/bin/env python3
"""Verify source provenance and compare normalized placements to EDA exports.

Uses CSV/plain-text placement exports, independently of the S-expression
normalizer. Missing optional CLI exports are explicitly skipped, never passed.
"""
from pathlib import Path
import argparse
import contextlib
import csv
import hashlib
import importlib.util
import io
import json
import math
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1] / "fixtures"

def placement_rows(path):
    text = path.read_text()
    if path.suffix == ".csv":
        result = []
        for row in csv.DictReader(text.splitlines()):
            folded = {key.lower(): val for key, val in row.items()}
            result.append({"ref": folded["ref"], "xMm": float(folded["posx"]), "yMm": float(folded["posy"]), "rotationDeg": float(folded["rot"]) % 360, "side": {"top": "top", "bottom": "bottom", "front": "top", "back": "bottom"}[folded["side"]]})
        return result
    result = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) != 7:
            raise ValueError(f"Unexpected placement row {line!r}")
        ref, _, _, x, y, rotation, side = fields
        result.append({"ref": ref, "xMm": float(x), "yMm": float(y), "rotationDeg": float(rotation) % 360, "side": side})
    return result

def compare(expected, actual, tolerance=0.00011):
    lookup = {row["ref"]: row for row in actual}
    failures = []
    for row in expected:
        got = lookup.get(row["ref"])
        if not got:
            failures.append({"ref": row["ref"], "reason": "not in normalized fixture"})
            continue
        errors = {key: abs(row[key] - got[key]) for key in ("xMm", "yMm")}
        errors["rotationDeg"] = abs((row["rotationDeg"]-got["rotationDeg"]+180) % 360-180)
        if max(errors.values()) > tolerance or row["side"] != got["side"]:
            failures.append({"ref": row["ref"], "expected": row, "actual": got, "errors": errors})
    return failures

def verify_bms(report):
    """Reproduce the derived demo inputs without rewriting committed fixtures."""
    directory = ROOT / "libresolar-bms"
    source = json.loads((directory / "source-manifest.json").read_text())
    mismatches = [item["path"] for item in source["files"]
                  if hashlib.sha256((directory / item["path"]).read_bytes()).hexdigest() != item["sha256"]]
    report["sources"].append({"dataset": "libresolar-bms", "manifestSha256": hashlib.sha256((directory / "source-manifest.json").read_bytes()).hexdigest(), "files": source["files"]})
    report["checks"].append({"name": "libresolar-bms: pinned source hashes", "status": "fail" if mismatches else "pass", "fileCount": len(source["files"]), "mismatches": mismatches})
    if mismatches:
        return
    spec = importlib.util.spec_from_file_location("bms_preparation", Path(__file__).with_name("prepare-bms-demo.py"))
    preparation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(preparation)
    generated_files = ["brief.json", "clean.json", "revision-mismatch.json", "bom-v0.2.csv",
                       "placements-v0.1.1.csv", "source-placements-v0.2.csv", "columns.json", "preparation-receipt.json"]
    with tempfile.TemporaryDirectory(prefix="pcba-bms-provenance-") as temporary:
        preparation.ROOT = Path(temporary) / "fixture"
        shutil.copytree(directory, preparation.ROOT)
        with contextlib.redirect_stdout(io.StringIO()):
            preparation.main()
        changed = [name for name in generated_files
                   if (directory / name).read_bytes() != (preparation.ROOT / name).read_bytes()]
    report["checks"].append({"name": "libresolar-bms: all derived inputs and receipt reproduce from pinned source",
                             "status": "fail" if changed else "pass", "fileCount": len(generated_files), "mismatches": changed})

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-cli", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = {"schemaVersion": 1, "checks": [], "sources": [], "limits": ["Placement/geometry provenance checks do not establish circuit function or manufacturing acceptance.", "This verifier compares supplied source and placement data; it does not run KiCad or establish passing ERC, DRC, BOM or SVG export qualification."]}
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("fixture_normalizer", Path(__file__).with_name("dataset-normalize.py"))
    normalizer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(normalizer)
    for name in ("sparkfun-stcc4", "opulo-ftp"):
        directory = ROOT / name
        fixture = json.loads((directory / "clean.json").read_text())
        source = json.loads((directory / "source-manifest.json").read_text())
        report["sources"].append({"dataset": name, "repo": source["repo"], "commit": source["commit"], "manifestSha256": hashlib.sha256((directory / "source-manifest.json").read_bytes()).hexdigest(), "fixtureSha256": hashlib.sha256((directory / "clean.json").read_bytes()).hexdigest(), "files": source["files"]})
        hash_errors = []
        for item in source["files"]:
            actual = hashlib.sha256((directory / item["path"]).read_bytes()).hexdigest()
            if actual != item["sha256"]:
                hash_errors.append(item["path"])
        report["checks"].append({"name": f"{name}: pinned source hashes", "status": "fail" if hash_errors else "pass", "fileCount": len(source["files"]), "mismatches": hash_errors})
        regenerated = normalizer.fixture(name)
        report["checks"].append({"name": f"{name}: canonical data and geometry reproduce from pinned source", "status": "pass" if regenerated == fixture else "fail"})
        bom_refs = [r["ref"] for r in fixture["bom"]]
        pos_refs = [r["ref"] for r in fixture["placements"]]
        numeric = all(math.isfinite(row[key]) for row in fixture["placements"] for key in ("xMm", "yMm", "rotationDeg"))
        expected_refs = {r["ref"] for r in fixture["bom"] if r["populated"] or r["category"] in ("mechanical", "fiducial")}
        consistent = len(bom_refs) == len(set(bom_refs)) and len(pos_refs) == len(set(pos_refs)) and expected_refs == set(pos_refs) and numeric
        report["checks"].append({"name": f"{name}: unique finite canonical rows", "status": "pass" if consistent else "fail", "bomRows": len(bom_refs), "placementRows": len(pos_refs), "populatedRows": sum(r["populated"] for r in fixture["bom"])})
        if name == "opulo-ftp":
            expected = placement_rows(directory / "source/ftp-top.pos")
            errors = compare(expected, fixture["authoritativePlacements"])
            report["checks"].append({"name": "opulo-ftp: independent upstream position export", "status": "fail" if errors else "pass", "comparedRows": len(expected), "toleranceMm": 0.00011, "mismatches": errors})
        cli_path = directory / "exports/positions.csv"
        if cli_path.exists():
            expected = placement_rows(cli_path)
            errors = compare(expected, fixture["authoritativePlacements"])
            report["checks"].append({"name": f"{name}: independent KiCad CLI position export", "status": "fail" if errors else "pass", "comparedRows": len(expected), "toleranceMm": 0.00011, "mismatches": errors})
        else:
            report["checks"].append({"name": f"{name}: independent KiCad CLI position export", "status": "fail" if args.require_cli else "skipped", "reason": "CLI export is not present."})
    clean = json.loads((ROOT / "sparkfun-stcc4/clean.json").read_text())
    broken = json.loads((ROOT / "sparkfun-stcc4/missing-u3.json").read_text())
    expected = dict(clean)
    expected["placements"] = [row for row in clean["placements"] if row["ref"] != "U3"]
    stripped = {key: value for key, value in broken.items() if key != "scenario"}
    report["checks"].append({"name": "synthetic failure changes only U3 supplied placement", "status": "pass" if stripped == expected else "fail", "fixture": "sparkfun-stcc4/missing-u3.json", "sha256": hashlib.sha256((ROOT / "sparkfun-stcc4/missing-u3.json").read_bytes()).hexdigest()})
    verify_bms(report)
    report["status"] = "fail" if any(c["status"] == "fail" for c in report["checks"]) else "pass-with-explicit-skips" if any(c["status"] == "skipped" for c in report["checks"]) else "pass"
    output = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output)
    print(output)
    return 1 if report["status"] == "fail" else 0

if __name__ == "__main__":
    sys.exit(main())
