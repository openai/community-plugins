"""Regenerate every manufacturing export from the selected editable model."""
from pathlib import Path
import sys

from cad import export
from model import assembly, bridge, tray


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: cad run exports.py OUTPUT_DIRECTORY")
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    export(assembly(), output / "assembly.step")
    for name, build in (("tray", tray), ("bridge", bridge)):
        document = build()
        export(document, output / f"{name}.step")
        export(document, output / f"{name}.stl", tolerance=0.02)
