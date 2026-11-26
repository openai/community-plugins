"""Build an offline 3D viewer and a matching still from an assembly recipe."""

import argparse
import base64
import html
import json
import math
from pathlib import Path
import sys

import numpy as np

sys.dont_write_bytecode = True
from display_mesh import primitive_groups
from preview import render


def validate_display(display, names):
    for field in ("title", "subtitle", "focus", "focus_label"):
        if not isinstance(display.get(field), str) or not display[field].strip():
            raise ValueError(f"Assembly display needs a nonempty {field}")
    if display["focus"] not in names:
        raise ValueError(f"Display focus group is absent from the GLB: {display['focus']}")
    offsets = display.get("explode")
    if not isinstance(offsets, dict) or set(offsets) - names:
        raise ValueError("Display explode must map GLB group names to vertical offsets")
    for value in offsets.values():
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("Display explode offsets must be finite numbers")


def framing(lo, hi):
    """Fit bounds already expressed in normalized assembly coordinates."""
    return {"center": ((hi + lo) / 2).tolist(), "zoom": 2 / float(np.max(hi - lo))}


def build(output, recipe):
    display = json.loads((recipe / "assembly.json").read_text(encoding="utf-8"))["display"]
    glb = output / "assembly.glb"
    vertices, indices, groups = [], [], []
    vertex_count = index_count = 0
    bounds = {}
    for name, primitives in primitive_groups(glb):
        first = index_count
        low, high = np.full(3, np.inf), np.full(3, -np.inf)
        for xyz, normals, rgb, faces in primitives:
            colors = np.broadcast_to(np.power(rgb, 1 / 2.2), xyz.shape)
            vertices.append(np.hstack((xyz, normals, colors)).astype("<f4"))
            flat_faces = faces.reshape(-1).astype("<u4")
            indices.append(flat_faces + vertex_count)
            vertex_count += len(xyz)
            index_count += len(flat_faces)
            low = np.minimum(low, xyz.min(axis=0))
            high = np.maximum(high, xyz.max(axis=0))
        if index_count == first or name in bounds:
            raise ValueError(f"Expected a unique nonempty top-level GLB group: {name}")
        bounds[name] = np.stack((low, high))
        groups.append({"name": name, "first": first, "count": index_count - first})
    if not groups:
        raise ValueError("The assembly has no component groups")
    validate_display(display, set(bounds))
    packed = np.concatenate(vertices)
    xyz = packed[:, :3]
    lo, hi = xyz.min(axis=0), xyz.max(axis=0)
    center = (lo + hi) / 2
    scale = 2 / np.max(hi - lo)
    packed[:, :3] = (xyz - center) * scale
    exploded = []
    for group in groups:
        name = group["name"]
        group["explode"] = display["explode"].get(name, 0)
        bounds[name] = (bounds[name] - center) * scale
        exploded.append(bounds[name] + np.array([0, group["explode"], 0]))
    exploded = np.stack(exploded)
    overview = {"center": [0, 0, 0], "zoom": 1}
    assets = {
        "groups": groups,
        "focus": display["focus"],
        "views": {
            "assembly": overview,
            "underside": overview,
            "focus": framing(*bounds[display["focus"]]),
            "exploded": framing(exploded[:, 0].min(axis=0), exploded[:, 1].max(axis=0)),
        },
        "vertices": base64.b64encode(packed.tobytes()).decode(),
        "indices": base64.b64encode(np.concatenate(indices).astype("<u4").tobytes()).decode(),
    }
    template = Path(__file__).with_name("viewer.html").read_text(encoding="utf-8")
    # Escape authored text independently in its two contexts: markup and inline JSON.
    from re import sub

    markup = sub(
        r"\{\{(TITLE|SUBTITLE|FOCUS_LABEL)\}\}",
        lambda m: html.escape(display[m[1].lower()]),
        template,
    )
    payload = json.dumps(assets, separators=(",", ":"), allow_nan=False).replace("<", "\\u003c")
    (output / "viewer.html").write_text(
        markup.replace("/*__ASSEMBLY__*/null", payload), encoding="utf-8"
    )
    render(glb, output / "preview.png")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Directory containing the generated assembly.glb")
    parser.add_argument("recipe", type=Path, help="Directory containing assembly.json")
    args = parser.parse_args()
    build(args.output, args.recipe)
