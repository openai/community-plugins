"""Two-page drawing from exported geometry and independent measured features.

No model module or design constants are imported. Hole callouts and dimension
anchors come from verify_geometry.py's readback of the delivered STEP.
"""
from pathlib import Path
import json
import sys

from cad import read_step
from cad.eng_drawing import Sheet, eng_drawing


def main(output: Path, measurements_path: Path, requirements_path: Path):
    report = json.loads(measurements_path.read_text())
    measurements = report["measurements"]
    parts = measurements["parts"]
    # Requirement metadata labels the drawing; every dimensional value below
    # still comes only from the independent STEP readback.
    revision = json.loads(requirements_path.read_text()).get("revision")
    if not isinstance(revision, str) or not revision.strip():
        raise ValueError("Drawing requires an explicit nonblank requirement revision")
    if report.get("status") not in ("pass", "fail"):
        raise ValueError("Cannot dimension an incomplete geometry readback")

    @eng_drawing(out=str(output / "module-drawing.pdf"))
    def drawings():
        sheets = []
        dimensions = {"units": "mm", "revision": revision,
                      "source": "independent STEP readback", "parts": {}}
        for number, name in enumerate(("tray", "bridge"), 1):
            measured = parts[name]
            body = read_step(output / f"{name}.step")
            sheet = Sheet("A3", title=f"DRONE MODULE / {name.upper()}",
                          part_number=f"PE-{number:03d}", revision=revision,
                          material="ILLUSTRATIVE POLYMER", units="mm", scale=1,
                          notes=["SYNTHETIC DEMO FIXTURE.", "NOMINAL GEOMETRY IN MM.",
                                 "PHYSICAL FIT NOT QUALIFIED."])
            top, front, right, iso = sheet.three_views(body, iso=True, gap=30)
            top.overall()
            front.overall()
            if name == "tray":
                left, right_y = measurements["cavity_inside_y_mm"]
                floor = measurements["wall_thickness_mm"]["tray_floor"]
                right.dim((0, left, floor), (0, right_y, floor), orientation="h")
            holes = measured["holes"]
            # The declared fixture has only vertical through bores. Identify the
            # frame bores by their measured outer X positions and board bores by
            # their measured short finite spans at the top of the bridge.
            if name == "tray":
                outer_x = max(abs(h["x_mm"]) for h in holes)
                primary = [h for h in holes if abs(abs(h["x_mm"]) - outer_x) < 0.01]
                secondary = [h for h in holes if h not in primary]
            else:
                primary = [h for h in holes if h["z_max_mm"] - h["z_min_mm"] < 5]
                secondary = [h for h in holes if h not in primary]
            if len(primary) != 4 or len(secondary) != 4:
                raise ValueError(f"Expected four primary and four attachment bores in {name}")
            xs = sorted({round(h["x_mm"], 8) for h in primary})
            ys = sorted({round(h["y_mm"], 8) for h in primary})
            radius = primary[0]["diameter_mm"] / 2
            z = measured["bounds_max_mm"][2]
            # Tangent points lie on the bore boundaries; distances still measure
            # the center pitches because both points use the same offset.
            top.dim((xs[0], ys[-1] + radius, z), (xs[-1], ys[-1] + radius, z))
            top.dim((xs[-1] + radius, ys[0], z), (xs[-1] + radius, ys[-1], z))
            for group, angle, label in ((primary, 45, "FRAME" if name == "tray" else "BOARD"),
                                         (secondary, -45, "BRIDGE ATTACHMENT")):
                hole = max(group, key=lambda item: (item["x_mm"], item["y_mm"] if angle > 0 else -item["y_mm"]))
                top.hole((hole["x_mm"], hole["y_mm"], hole["z_max_mm"]),
                         hole["diameter_mm"], thru=True, count=len(group), angle=angle,
                         label=label)
            dimensions["parts"][name] = {
                "bounds_mm": measured["bounds_mm"],
                "primary_pitch_mm": [xs[-1] - xs[0], ys[-1] - ys[0]],
                "primary_hole_diameter_mm": primary[0]["diameter_mm"],
                "attachment_hole_diameter_mm": secondary[0]["diameter_mm"],
                "volume_mm3": measured["volume_mm3"],
            }
            sheets.append(sheet)
        dimensions["cavity_width_mm"] = measurements["cavity_width_mm"]
        dimensions["wall_thickness_mm"] = measurements["wall_thickness_mm"]
        (output / "drawing-dimensions.json").write_text(json.dumps(dimensions, indent=2) + "\n")
        return sheets

    drawings()


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("usage: cad run drawing.py OUTPUT_DIRECTORY MEASUREMENTS_JSON REQUIREMENTS_JSON")
    main(*(Path(arg).resolve() for arg in sys.argv[1:]))
