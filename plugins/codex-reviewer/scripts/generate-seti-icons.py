"""Extract the vendored official Seti outlines without changing their shapes.

Regenerate with: uv run --with fonttools==4.67.0 python scripts/generate-seti-icons.py
The generated JSON is checked in; building Reviewer does not need Python.
"""

import json
from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont


assets = Path(__file__).resolve().parents[1] / "src/app/assets/seti"
theme = json.loads((assets / "vs-seti-icon-theme.json").read_text(encoding="utf-8"))
font = TTFont(assets / "seti.woff")
glyphs = font.getGlyphSet()
cmap = font.getBestCmap()
outlines = {}

for code in sorted({icon["fontCharacter"][1:] for icon in theme["iconDefinitions"].values()}):
    glyph = glyphs[cmap[int(code, 16)]]
    bounds = BoundsPen(glyphs)
    glyph.draw(bounds)
    x_min, y_min, x_max, y_max = bounds.bounds
    size = max(x_max - x_min, y_max - y_min) * 1.05
    left = (x_min + x_max - size) / 2
    top = (-y_min - y_max - size) / 2
    pen = SVGPathPen(glyphs)
    glyph.draw(TransformPen(pen, (1, 0, 0, -1, 0, 0)))
    outlines[code] = {
        "viewBox": " ".join(f"{value:.3f}" for value in [left, top, size, size]),
        "path": pen.getCommands(),
    }

(assets / "glyphs.json").write_text(json.dumps(outlines, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
print(f"Generated {len(outlines)} official Seti outlines")
