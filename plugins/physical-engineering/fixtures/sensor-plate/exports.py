"""Regenerate the selected single part's STEP and mesh exports."""
from pathlib import Path
import sys

from cad import export
from model import component, design


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: cad run exports.py OUTPUT_DIRECTORY")
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    name = design()["part_name"]
    document = component()
    export(document, output / "assembly.step")
    export(document, output / f"{name}.step")
    export(document, output / f"{name}.stl", tolerance=.02)
