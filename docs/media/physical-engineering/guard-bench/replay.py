"""Replay the sourced aircraft, accelerometer, housing, guard fixture and registered station."""

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
from verify_board import verify_board
from check_housing import check_historical_opening

HERE = Path(__file__).resolve().parent
BOARD = "bench-accelerometer"


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(*args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, **kwargs)


def build_board(out, image):
    source = HERE / "ecad"
    if not image:
        run("sh", source / "rebuild.sh", out)
        return
    # Copy the source so Docker contexts on separate VMs do not need a shared host path.
    container = run(
        "docker",
        "create",
        "--network",
        "none",
        image,
        "sh",
        "/source/rebuild.sh",
        "/board",
        capture_output=True,
        text=True,
    ).stdout.strip()
    try:
        run("docker", "cp", source, container + ":/source")
        run("docker", "start", "-a", container)
        result = run(
            "docker",
            "inspect",
            "--format",
            "{{.State.ExitCode}}",
            container,
            capture_output=True,
            text=True,
        )
        if result.stdout.strip() != "0":
            raise RuntimeError(
                "The source-only KiCad rebuild returned " + result.stdout.strip()
            )
        run("docker", "cp", container + ":/board", out)
    finally:
        run("docker", "rm", "-f", container, stdout=subprocess.DEVNULL)


def materialize_housing(out, board):
    source = out / "inputs/housing"
    shutil.copytree(HERE / "designs/housing", source)
    data = source / "boards/accelerometer"
    shutil.copytree(board / "design", data)
    shutil.copytree(board / "licenses", data / "licenses")
    native = board / (BOARD + ".step")
    shutil.copyfile(native, source / "geometry/assets/instrument-pcb.step")
    for filename in (
        native.name,
        "schematic.pdf",
        "erc.json",
        "drc.json",
        "placements.csv",
        "kicad-bom.csv",
        "routed-trace.json",
    ):
        shutil.copyfile(board / filename, data / filename)
    registration = {
        "source_step_sha256": digest(native),
        "board_file_sha256": digest(data / (BOARD + ".kicad_pcb")),
        "transform": "KiCad XY origin at (100,100), reversed screen Y, installed at Z+5.0 mm.",
    }
    (data / "registration.json").write_text(json.dumps(registration, indent=2) + "\n")
    return source


def catalog(path):
    with path.open() as f:
        return list(csv.DictReader(f))


def assembly(engine, source, out):
    run(sys.executable, engine, "assembly-build", source, out)
    repeat = out.with_name(out.name + "-rebuild")
    run("sh", out / "rebuild.sh", repeat)
    run(
        sys.executable,
        repeat / "source/scripts/engineering.py",
        "assembly-verify",
        repeat,
    )
    result = read(repeat / "manifest.json")["reproduction"]
    if result["status"] != "pass" or result["baseline_manifest_sha256"] != digest(
        out / "manifest.json"
    ):
        raise RuntimeError("Delivered source failed to reproduce " + out.name)
    for d in (out, repeat):
        for item in (
            "assembly.step",
            "assembly.glb",
            "viewer.html",
            "preview.png",
            "bom.csv",
            "bom.xlsx",
        ):
            if (d / item).stat().st_size == 0:
                raise ValueError("Empty export " + item)
    return repeat, len(read(repeat / "measurements.json")["checks"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output", type=Path, help="Fresh directory for handoffs and replay evidence"
    )
    parser.add_argument(
        "--plugin", type=Path, default=HERE.parents[3] / "plugins/physical-engineering"
    )
    parser.add_argument(
        "--kicad-image",
        help="Local Docker image containing KiCad CLI, libraries and pcbnew",
    )
    args = parser.parse_args()
    if not os.getenv("PE_RUNTIME"):
        parser.error("Set PE_RUNTIME to the prepared Physical Engineering runtime")
    engine = args.plugin.resolve() / "scripts/engineering.py"
    runtime = Path(os.environ["PE_RUNTIME"]) / "bin/python"
    if not engine.is_file() or not runtime.is_file():
        parser.error("Check --plugin and PE_RUNTIME")
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    board = out / "board"
    build_board(board, args.kicad_image)
    pcb = read(board / "drc.json")
    erc = read(board / "erc.json")
    if any(pcb.get(k) for k in ("violations", "unconnected_items", "schematic_parity")):
        raise ValueError("PCB rules/parity failed")
    if any(s.get("violations") for s in erc["sheets"]):
        raise ValueError("ERC failed")
    traces = read(board / "routed-trace.json")["routed_paths"]
    sda = next(t for t in traces if t["net"] == "I2C_SDA")
    if (
        sda["from_pad"] != ["J1", "3"]
        or sda["to_pad"] != ["U1", "6"]
        or abs(sda["length_mm"] - 7.39326) > 0.001
        or sda["via_count"] != 2
    ):
        raise ValueError("SDA route changed")
    manufacture = verify_board(board, HERE / "sourcing/accelerometer-parts.csv")
    housing = materialize_housing(out, board)
    summaries = {}
    verified = {}
    for name, source in (
        ("aircraft", HERE / "designs/aircraft"),
        ("fixture", HERE / "designs/fixture"),
        ("housing", housing),
    ):
        replayed, count = assembly(engine, source, out / name)
        verified[name] = replayed
        summaries[name] = {
            "exported_checks": count,
            "baseline_manifest_sha256": digest(out / name / "manifest.json"),
            "rebuild_manifest_sha256": digest(replayed / "manifest.json"),
            "delivered_source_reproduction": "pass",
        }
    negative_housing = check_historical_opening(
        housing, out / "housing-regression", runtime
    )
    a = read(verified["aircraft"] / "fc-substrate.json")
    fx = read(verified["fixture"] / "drawing-measurements.json")
    housing_result = read(verified["housing"] / "integration.json")
    if [a["status"], fx["status"], housing_result["status"]] != ["pass"] * 3:
        raise ValueError("Engineering inspection failed")
    sourced = catalog(HERE / "sourcing/whoop-sourcing.csv")
    built = catalog(verified["aircraft"] / "bom.csv")
    if len(sourced) != 34 or [
        [r[k] for k in ("ID", "Scope", "Qty", "Unit")] for r in sourced
    ] != [[r[k] for k in ("ID", "Scope", "Qty", "Unit")] for r in built]:
        raise ValueError("Aircraft sourcing ledger differs from rebuilt BOM")
    station = out / "station"
    run(
        runtime,
        "-B",
        HERE / "build_station.py",
        verified["fixture"],
        verified["housing"],
        station,
    )
    reg = read(station / "registration.json")
    receipt = {
        "status": "pass",
        "kicad": (board / "kicad-version.txt").read_text().strip(),
        "source_assemblies": summaries,
        "pcb": {
            "ERC": 0,
            "DRC": 0,
            "unconnected": 0,
            "schematic_parity": 0,
            **manufacture,
            "SDA_routed_length_mm": sda["length_mm"],
            "SDA_vias": sda["via_count"],
            "negative_regression": read(board / "edge-regression.json"),
        },
        "aircraft": {
            "sourcing_rows": len(sourced),
            "substrate_checks": len(a["checks"]),
            "installed_FC_substrate_mm": next(
                c["actual_mm"][0]
                for c in a["checks"]
                if c["id"] == "installed-substrate-thickness"
            ),
        },
        "fixture": {
            "drawing_checks": len(fx["checks"]),
            "tip_to_stop_mm": reg["coupon_tip_to_stop_gap_mm"],
        },
        "housing": {
            "negative_regression": negative_housing,
            "integration_checks": len(housing_result["checks"]),
            "measured": housing_result["measured"],
        },
        "station": {
            "translation_mm": reg["translation_mm"],
            "housing_plate_overlap_mm3": reg["plate_housing_solid_overlap_mm3"],
            "coupon_to_housing_mm": reg["coupon_to_final_enclosure_gap_mm"],
        },
    }
    (out / "replay.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print("Source replay passed. See " + str(out / "replay.json"))


if __name__ == "__main__":
    main()
