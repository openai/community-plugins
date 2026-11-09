#!/usr/bin/env python3
"""Produce reference fixture data from pinned KiCad documents, without running source code.

This deliberately narrow S-expression reader preserves PCB coordinates, copper
segments, pads and cutouts. It is a fixture preparation utility, not an ERC/DRC
engine. CLI comparisons are recorded separately by dataset-verify.py.
"""
from pathlib import Path
import copy
import json
import math
import re

ROOT = Path(__file__).resolve().parents[1] / "fixtures"

def parse(text):
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|\(|\)|[^\s()]+', text)
    stack, root = [], None
    for token in tokens:
        if token == '(':
            value = []
            if stack:
                stack[-1].append(value)
            else:
                if root is not None:
                    raise ValueError("multiple root expressions")
                root = value
            stack.append(value)
        elif token == ')':
            if not stack:
                raise ValueError("unexpected closing parenthesis")
            stack.pop()
        else:
            if not stack:
                raise ValueError("token outside expression")
            stack[-1].append(json.loads(token) if token.startswith('"') else token)
    if stack or root is None:
        raise ValueError("unterminated/empty expression")
    return root

def children(node, key):
    return [item for item in node if isinstance(item, list) and item and item[0] == key]

def child(node, key, default=None):
    matches = children(node, key)
    return matches[0] if matches else (default if default is not None else [key])

def vals(node, key, default=()):
    return child(node, key, [key, *default])[1:]

def number(value):
    return round(float(value), 6)

def point(node, key):
    return [float(v) for v in vals(node, key)[:2]]

def props(node):
    result = {p[1]: p[2] for p in children(node, "property")}
    for p in children(node, "fp_text"):
        result.setdefault(p[1].capitalize(), p[2])
    return result

def refkey(ref):
    return [int(v) if v.isdigit() else v for v in re.split(r'(\d+)', ref)]

def local_to_global(fp, local):
    at = vals(fp, "at", (0, 0, 0))
    angle = math.radians(float(at[2]) if len(at) > 2 else 0)
    # KiCad's board Y axis points down; positive footprint angles are CCW.
    x, y = local
    return [float(at[0]) + math.cos(angle) * x + math.sin(angle) * y,
            float(at[1]) - math.sin(angle) * x + math.cos(angle) * y]

def fixture(name):
    source = ROOT / name
    manifest = json.loads((source / "source-manifest.json").read_text())
    board_path = next(source / f["path"] for f in manifest["files"] if f["path"].endswith(".kicad_pcb"))
    pcb = parse(board_path.read_text())
    edge_items = [n for n in pcb if isinstance(n, list) and n and n[0] in ("gr_rect", "gr_line", "gr_poly", "gr_arc", "gr_circle") and vals(n, "layer") == ["Edge.Cuts"]]
    edge_points = []
    for item in edge_items:
        if item[0] in ("gr_rect", "gr_line"):
            edge_points.extend([point(item, "start"), point(item, "end")])
        elif item[0] == "gr_poly":
            edge_points.extend([[float(p[1]), float(p[2])] for p in children(child(item, "pts"), "xy")])
        else:
            raise ValueError(f"unsupported board edge {item[0]}")
    min_x = min(p[0] for p in edge_points)
    max_x = max(p[0] for p in edge_points)
    min_y = min(p[1] for p in edge_points)
    max_y = max(p[1] for p in edge_points)
    width, height = number(max_x-min_x), number(max_y-min_y)
    def xy(p):
        return [number(p[0]-min_x), number(max_y-p[1])]
    visual = {"outline": [[0, 0], [width, 0], [width, height], [0, height], [0, 0]], "cutouts": [], "tracks": [], "pads": [], "footprints": [], "holes": []}
    for item in edge_items:
        if item[0] == "gr_poly":
            polygon = [xy([float(p[1]), float(p[2])]) for p in children(child(item, "pts"), "xy")]
            visual["cutouts"].append(polygon + [polygon[0]])
    for segment in children(pcb, "segment"):
        start, end = xy(point(segment, "start")), xy(point(segment, "end"))
        visual["tracks"].append({"x1": start[0], "y1": start[1], "x2": end[0], "y2": end[1], "width": number(vals(segment, "width")[0]), "side": "bottom" if vals(segment, "layer") == ["B.Cu"] else "top"})
    bom, placements, excluded = [], [], []
    for fp in children(pcb, "footprint") + children(pcb, "module"):
        p = props(fp)
        ref, value = p.get("Reference", ""), p.get("Value", "")
        attrs = vals(fp, "attr")
        package = fp[1]
        pads = children(fp, "pad")
        include_item = not ("board_only" in attrs or "exclude_from_bom" in attrs or ref.startswith("JP") or not pads)
        if not include_item:
            excluded.append({"ref": ref, "reason": "non-purchased copper/silkscreen feature or upstream excluded-from-BOM", "package": package})
        if not pads:
            continue
        category = ("fiducial" if ref.startswith("FID") else "mechanical" if (ref.startswith(("ST", "H")) or "MountingHole" in package) else "smt" if "smd" in attrs else "tht")
        populated = category in ("smt", "tht") and "dnp" not in attrs
        if name == "opulo-ftp":
            populated = ref.startswith("R")
        at = vals(fp, "at", (0, 0, 0))
        location = xy([float(at[0]), float(at[1])])
        angle = number((float(at[2]) if len(at) > 2 else 0) % 360)
        side = "bottom" if vals(fp, "layer") == ["B.Cu"] else "top"
        if include_item:
            bom.append({"ref": ref, "partId": f"{package}:{value}", "value": value, "package": package, "category": category, "populated": populated, "mpn": None})
            placements.append({"ref": ref, "xMm": location[0], "yMm": location[1], "rotationDeg": angle, "side": side, "package": package})
        local_bounds = []
        for pad in pads:
            pad_at = vals(pad, "at", (0, 0, 0))
            global_pad = xy(local_to_global(fp, [float(pad_at[0]), float(pad_at[1])]))
            size = [float(v) for v in vals(pad, "size")]
            pad_layers = vals(pad, "layers")
            pad_side = "bottom" if "B.Cu" in pad_layers and "F.Cu" not in pad_layers else side
            # Pad angle in KiCad is stored as an absolute board angle.
            pad_angle = number((float(pad_at[2]) if len(pad_at) > 2 else angle) % 360)
            visual["pads"].append({"ref": ref, "pad": pad[1], "xMm": global_pad[0], "yMm": global_pad[1], "widthMm": number(size[0]), "heightMm": number(size[1]), "rotationDeg": pad_angle, "shape": pad[3], "side": pad_side})
            drill = vals(pad, "drill")
            if drill:
                diam = next((float(v) for v in drill if not isinstance(v, list) and re.fullmatch(r'[\d.]+', v)), 0)
                if diam:
                    visual["holes"].append({"xMm": global_pad[0], "yMm": global_pad[1], "diameterMm": number(diam)})
            local_bounds.extend([[float(pad_at[0])-size[0]/2, float(pad_at[1])-size[1]/2], [float(pad_at[0])+size[0]/2, float(pad_at[1])+size[1]/2]])
        # Use the upstream courtyard for the envelope when available.
        courtyard = []
        for drawing in fp:
            if not isinstance(drawing, list) or not drawing or vals(drawing, "layer") not in (["F.CrtYd"], ["B.CrtYd"]):
                continue
            if drawing[0] in ("fp_line", "fp_rect"):
                courtyard.extend([point(drawing, "start"), point(drawing, "end")])
            if drawing[0] == "fp_poly":
                courtyard.extend([[float(v[1]), float(v[2])] for v in children(child(drawing, "pts"), "xy")])
        bounds = courtyard or local_bounds
        if bounds:
            lx, rx = min(v[0] for v in bounds), max(v[0] for v in bounds)
            ly, ry = min(v[1] for v in bounds), max(v[1] for v in bounds)
            center = xy(local_to_global(fp, [(lx+rx)/2, (ly+ry)/2]))
            visual["footprints"].append({"ref": ref, "xMm": center[0], "yMm": center[1], "widthMm": number(rx-lx), "heightMm": number(ry-ly), "rotationDeg": angle, "side": side, "envelopeSource": "courtyard" if courtyard else "pad-bounds"})
    bom.sort(key=lambda v: refkey(v["ref"]))
    placements.sort(key=lambda v: refkey(v["ref"]))
    if name == "sparkfun-stcc4":
        source_findings = [
            {"id": "UPSTREAM-SENSOR-NAME", "severity": "warning", "message": "README identifies SHT41 while the pinned schematic and PCB identify SHT40. Identity remains unresolved; this demo does not approve a substitution.", "refs": ["U3"]},
            {"id": "UPSTREAM-PART-ID", "severity": "warning", "message": "U3 has placeholder PROD_ID IC-...; U2 differs between schematic IC-28635 and PCB IC-.... Procurement MPNs remain unknown.", "refs": ["U2", "U3"]},
            {"id": "UPSTREAM-HEADER-VALUE", "severity": "warning", "message": "J3's Conn_01x06 value conflicts with its four-pin symbol/footprint. The source is preserved, not silently corrected.", "refs": ["J3"]},
            {"id": "SCOPE-DIGITAL", "severity": "warning", "message": "Package consistency only. No electrical function, fabrication, solder quality, EMC, thermal or production qualification is claimed.", "refs": []},
        ]
        board_name, revision = "Qwiic CO₂ environmental sensor assembly", "v10"
    else:
        source_findings = [
            {"id": "SOURCE-NO-PROCUREMENT-BOM", "severity": "warning", "message": "FTP source uses generic R_Small/LED_Small values. No supplier MPN or qualified procurement BOM is supplied.", "refs": []},
            {"id": "SCENARIO-RESISTOR-VARIANT", "severity": "warning", "message": "Data-only resistor variant populates R1–R40 and retains LEDs, headers and fiducials as non-populated context. It does not execute OpenPnP or validate machine behavior.", "refs": []},
        ]
        board_name, revision = "FTP placement reference assembly", "REV00-v4.1.0"
    result = {
        "schemaVersion": 1,
        "board": {"id": name, "name": board_name, "revision": revision, "widthMm": width, "heightMm": height},
        "variant": "standard" if name == "sparkfun-stcc4" else "resistors-only",
        "quantity": 10,
        "conventions": {"units": "mm", "coordinateFrame": "board-bottom-left", "rotation": "ccw", "bottomView": "top"},
        "bom": bom, "placements": placements, "authoritativePlacements": copy.deepcopy(placements),
        "source": {**manifest, "exportMethod": "Pinned KiCad source parsed by dataset-normalize.py; CLI qualification recorded separately; not an ERC/DRC result", "sourceCoordinateTransform": {"xOffsetMm": min_x, "yOriginMm": max_y, "formula": "x=sourceX-xOffsetMm; y=yOriginMm-sourceY; rotation=sourceAngle mod 360"}, "excludedFeatures": excluded},
        "sourceFindings": source_findings,
        "visual": visual,
    }
    if name == "opulo-ftp":
        result["source"]["referencePlacementExport"] = "source/ftp-top.pos"
        active_refs = {row["ref"] for row in bom if row["populated"] or row["category"] in ("fiducial", "mechanical")}
        result["placements"] = [row for row in placements if row["ref"] in active_refs]
        result["source"]["populationFilter"] = {"description": "Data-only resistor assembly variant. Supplied assembly placements retain R1–R40 and fiducial/mechanical context; LEDs D1–D40 and header J1 remain in authoritative source geometry but are not populated.", "excludedRefs": [row["ref"] for row in placements if row["ref"] not in active_refs], "upstreamFilesModified": False}
    return result

def main():
    for name in ("sparkfun-stcc4", "opulo-ftp"):
        result = fixture(name)
        target = ROOT / name / "clean.json"
        target.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        print(name, len(result["bom"]), "BOM lines,", sum(v["populated"] for v in result["bom"]), "populated,", len(result["visual"]["tracks"]), "copper segments", flush=True)
        if name == "sparkfun-stcc4":
            broken = copy.deepcopy(result)
            broken["placements"] = [v for v in broken["placements"] if v["ref"] != "U3"]
            broken["scenario"] = {"id": "missing-u3-placement", "synthetic": True, "description": "Demonstration fault: omit populated U3 from the supplied placement file. Upstream source and authoritative placements remain intact.", "changedRefs": ["U3"]}
            (ROOT / name / "missing-u3.json").write_text(json.dumps(broken, indent=2, ensure_ascii=False) + "\n")

if __name__ == "__main__":
    main()
