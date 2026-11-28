"""Rebuild a copy with the earlier 15 × 3 mm wall opening and verify the genuine rejection."""

import json
from pathlib import Path
import shutil
import subprocess
import sys


def check_historical_opening(source: Path, out: Path, runtime: Path):
    model = out / "geometry"
    results = out / "exports"
    shutil.copytree(source / "geometry", model)
    results.mkdir()
    build = model / "build.py"
    before = build.read_text()
    start = before.index("    port = bd.Plane.XZ * bd.RectangleRounded(")
    end = before.index("\n", before.index("    base = base.cut(opening)", start))
    build.write_text(
        before[:start]
        + "    base = base.cut(bd.Pos(0, -17, 6) * bd.Box(15, 4, 3))"
        + before[end:]
    )
    with (out / "rejection.log").open("w") as log:
        process = subprocess.run(
            [runtime, "-B", build, results], stdout=log, stderr=subprocess.STDOUT
        )
    diagnosis = json.loads((results / "integration.json").read_text())
    rejected = [name for name, passed in diagnosis["checks"].items() if not passed]
    if (
        process.returncode == 0
        or diagnosis["status"] != "fail"
        or "gauge_reaches_header_exterior_without_collision" not in rejected
    ):
        raise ValueError(
            "Expected the small historical opening to block the cable approach"
        )
    volume = diagnosis["measured"]["gauge_overlap_mm3"]["base"]
    if abs(volume - 28.7) > 0.01:
        raise ValueError(f"Unexpected historical plug-gauge interference {volume} mm³")
    return {
        "status": "rejected as expected",
        "exit_code": process.returncode,
        "failed_checks": rejected,
        "housing_gauge_interference_mm3": volume,
        "historical_opening_mm": [15.0, 3.0],
        "unchanged_inspection_sha256": diagnosis["drawing_sha256"],
    }


if __name__ == "__main__":
    print(
        json.dumps(
            check_historical_opening(
                Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
            ),
            indent=2,
        )
    )
