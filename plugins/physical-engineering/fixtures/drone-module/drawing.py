"""Two-page drawing from exported geometry and independent measured features.

No model module or design constants are imported. Hole callouts and dimension
anchors come from verify_geometry.py's readback of the delivered STEP.
"""
from pathlib import Path
from io import BytesIO
import json
import sys

from cad import read_step
from cad.eng_drawing import Sheet, eng_drawing


def measured_dimension_block(pdf_path: Path, report: dict, revision: str):
    """Add visible, selectable readback callouts in the reserved lower-left area.

    The upstream drawing renderer outlines its lettering. This separate visible
    block lets the delivered PDF be checked as text without pretending to OCR
    the dimension-line artwork. Its inputs are the independent STEP readback.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    from pypdf import PdfReader, PdfWriter

    measurements = report["measurements"]
    measured_checks = {item["id"]: item["actual"] for item in report["checks"]}
    overlay = BytesIO()
    with matplotlib.rc_context({"pdf.fonttype": 42, "font.family": "DejaVu Sans"}):
        with PdfPages(overlay, metadata={"CreationDate": None}) as pages:
            for name, interface in (("tray", "frame"), ("bridge", "board")):
                part = measurements["parts"][name]
                primary = measured_checks[f"{interface}_interface"]
                attachments = measured_checks[f"{name}_attachment_interface"]
                lines = [
                    "MEASURED STEP READBACK",
                    f"PART: {name.upper()}   REVISION: {revision}   UNITS: MM",
                    "OVERALL XYZ: " + " X ".join(f"{v:.3f}" for v in part["bounds_mm"]) + " MM",
                    f"{interface.upper()} PITCH XY: " + " X ".join(f"{v:.3f}" for v in measurements[f"{interface}_pitch_mm"]) + " MM",
                    f"{interface.upper()} HOLES: {len(primary)} X DIA {primary[0]['diameter_mm']:.3f} MM THRU",
                    f"BRIDGE ATTACHMENT HOLES: {len(attachments)} X DIA {attachments[0]['diameter_mm']:.3f} MM THRU",
                ]
                walls = measurements["wall_thickness_mm"]
                if name == "tray":
                    lines += [f"CAVITY WIDTH: {measurements['cavity_width_mm']:.3f} MM",
                              "DECLARED WALLS: " + " / ".join(
                                  f"{label} {walls[key]:.3f}" for label, key in
                                  (("FLOOR", "tray_floor"), ("LEFT", "tray_left_rail"),
                                   ("RIGHT", "tray_right_rail"), ("BACK", "tray_backstop"))) + " MM"]
                else:
                    lines += [f"DECK THICKNESS: {walls['bridge_deck']:.3f} MM"]
                figure = plt.figure(figsize=(420 / 25.4, 297 / 25.4))
                figure.patch.set_alpha(0)
                axis = figure.add_axes([0, 0, 1, 1])
                axis.set(xlim=(0, 420), ylim=(0, 297), aspect="equal")
                axis.axis("off")
                # A3 title block begins at x=230mm; notes stay above y=50mm.
                axis.add_patch(plt.Rectangle((13, 11.5), 205, 37, facecolor="white",
                                             edgecolor="#606060", linewidth=.45))
                for row, line in enumerate(lines):
                    axis.text(16, 44 - row * 4.3, line, fontsize=8 if row == 0 else 7.5,
                              weight="bold" if row == 0 else "normal", color="black",
                              ha="left", va="baseline")
                pages.savefig(figure, transparent=True)
                plt.close(figure)
    base, additions = PdfReader(pdf_path, strict=True), PdfReader(overlay, strict=True)
    if len(base.pages) != 2 or len(additions.pages) != 2:
        raise ValueError("Expected two A3 part drawings before adding measured callouts")
    writer = PdfWriter()
    for page, addition in zip(base.pages, additions.pages):
        writer.add_page(page)
        writer.pages[-1].merge_page(addition)
    temporary = pdf_path.with_name(".module-drawing-checked.pdf")
    try:
        with temporary.open("wb") as output:
            writer.write(output)
        temporary.replace(pdf_path)
    finally:
        temporary.unlink(missing_ok=True)


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
            # Interface groups come from independent readback. Rounded exterior
            # surfaces and lightening features are not mounting-bore callouts.
            check_values = {item["id"]: item["actual"] for item in report["checks"]}
            primary = check_values["frame_interface" if name == "tray" else "board_interface"]
            secondary = check_values[f"{name}_attachment_interface"]
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
    measured_dimension_block(output / "module-drawing.pdf", report, revision)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("usage: cad run drawing.py OUTPUT_DIRECTORY MEASUREMENTS_JSON REQUIREMENTS_JSON")
    main(*(Path(arg).resolve() for arg in sys.argv[1:]))
