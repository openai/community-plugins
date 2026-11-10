#!/usr/bin/env python3
"""Prepare a real revision-mismatch example from pinned Libre Solar BMS sources.

Upstream files remain unmodified. The current published BOM defines the assembly
population; R59 is source-only geometry and its missing BOM entry stays a warning.
The older revision lacks the two D11/D12 placements added in revision 0.2.
"""
from pathlib import Path
import copy
import csv
import hashlib
import importlib.util
import json
import math

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent / "fixtures" / "libresolar-bms"
spec = importlib.util.spec_from_file_location("pcba_source_reader", HERE / "dataset-normalize.py")
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)
children, vals, point, props = reader.children, reader.vals, reader.point, reader.props
n = reader.number


def board(revision):
    pcb = reader.parse((ROOT / "source" / revision / "kicad/bms-8s50-ic.kicad_pcb").read_text())
    lines = [item for item in children(pcb, "gr_line") if vals(item, "layer") == ["Edge.Cuts"]]
    pts = [point(item, key) for item in lines for key in ("start", "end")]
    x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
    y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
    def xy(p):
        return [n(p[0] - x0), n(y1 - p[1])]
    pieces = [[point(item, "start"), point(item, "end")] for item in lines]
    for arc in children(pcb, "gr_arc"):
        if vals(arc, "layer") != ["Edge.Cuts"]:
            continue
        center, start = point(arc, "start"), point(arc, "end")
        angle = math.radians(float(vals(arc, "angle")[0]))
        dx, dy = start[0]-center[0], start[1]-center[1]
        pieces.append([[center[0] + dx*math.cos(angle*i/18)-dy*math.sin(angle*i/18),
                        center[1] + dx*math.sin(angle*i/18)+dy*math.cos(angle*i/18)] for i in range(19)])
    outline = pieces.pop(0)
    while pieces:
        for i, edge in enumerate(pieces):
            if math.dist(outline[-1], edge[0]) < 0.001:
                outline.extend(edge[1:]); pieces.pop(i); break
            if math.dist(outline[-1], edge[-1]) < 0.001:
                outline.extend(list(reversed(edge))[1:]); pieces.pop(i); break
        else:
            raise ValueError("Source outline does not form one connected loop")
    assert math.dist(outline[-1], outline[0]) < 0.001
    visual = {"outline": [xy(p) for p in outline], "cutouts": [], "tracks": [], "pads": [], "footprints": [], "holes": []}
    for segment in children(pcb, "segment"):
        a, b = xy(point(segment, "start")), xy(point(segment, "end"))
        visual["tracks"].append({"x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1], "width": n(vals(segment, "width")[0]), "side": "bottom" if vals(segment, "layer") == ["B.Cu"] else "top"})
    parts, placements = {}, []
    for fp in children(pcb, "module") + children(pcb, "footprint"):
        p, pads = props(fp), children(fp, "pad")
        ref = p.get("Reference")
        if not ref or not pads:
            continue
        at = vals(fp, "at", (0, 0, 0)); angle = n(float(at[2]) % 360) if len(at) > 2 else 0
        side = "bottom" if vals(fp, "layer") == ["B.Cu"] else "top"
        location = xy([float(at[0]), float(at[1])])
        smt = "smd" in vals(fp, "attr")
        parts[ref] = {"ref": ref, "value": p.get("Value", ""), "package": fp[1], "category": "smt" if smt else "tht"}
        if smt:
            placements.append({"ref": ref, "xMm": location[0], "yMm": location[1], "rotationDeg": angle, "side": side, "package": fp[1]})
        bounds = []
        for pad in pads:
            pad_at = vals(pad, "at", (0, 0, 0)); size = [float(v) for v in vals(pad, "size")]
            loc = xy(reader.local_to_global(fp, [float(pad_at[0]), float(pad_at[1])]))
            pad_angle = n(float(pad_at[2]) % 360) if len(pad_at) > 2 else angle
            layers = vals(pad, "layers")
            visual["pads"].append({"ref": ref, "pad": pad[1], "xMm": loc[0], "yMm": loc[1], "widthMm": size[0], "heightMm": size[1], "rotationDeg": pad_angle, "shape": pad[3], "side": "bottom" if "B.Cu" in layers and "F.Cu" not in layers else side})
            drill = vals(pad, "drill")
            if drill:
                diameter = next((float(v) for v in drill if isinstance(v, str) and v.replace('.', '', 1).isdigit()), 0)
                if diameter:
                    visual["holes"].append({"xMm": loc[0], "yMm": loc[1], "diameterMm": diameter})
            bounds.extend([[float(pad_at[0])-size[0]/2, float(pad_at[1])-size[1]/2], [float(pad_at[0])+size[0]/2, float(pad_at[1])+size[1]/2]])
        if bounds:
            left, right = min(p[0] for p in bounds), max(p[0] for p in bounds)
            bottom, top = min(p[1] for p in bounds), max(p[1] for p in bounds)
            center = xy(reader.local_to_global(fp, [(left+right)/2, (bottom+top)/2]))
            visual["footprints"].append({"ref": ref, "xMm": center[0], "yMm": center[1], "widthMm": n(right-left), "heightMm": n(top-bottom), "rotationDeg": angle, "side": side, "envelopeSource": "pad-bounds"})
    placements.sort(key=lambda r: reader.refkey(r["ref"]))
    return {"parts": parts, "placements": placements, "visual": visual, "widthMm": n(x1-x0), "heightMm": n(y1-y0), "transform": {"xOffsetMm": x0, "yOriginMm": y1, "formula": "x=sourceX-xOffsetMm; y=yOriginMm-sourceY; rotation=sourceAngle mod 360"}}


def write_csv(target, rows, columns):
    with target.open("w", newline="") as out:
        w = csv.DictWriter(out, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
        w.writeheader(); w.writerows(rows)


def main():
    manifest = json.loads((ROOT / "source-manifest.json").read_text())
    for f in manifest["files"]:
        assert hashlib.sha256((ROOT / f["path"]).read_bytes()).hexdigest() == f["sha256"], f["path"]
    current, previous = board("current"), board("previous")
    bom = []
    with (ROOT / "source/current/build/bms-8s50-ic_bom.csv").open(newline="") as inp:
        for row in csv.DictReader(inp):
            assert not row["DNF"] and not row["Config"], "Review source population annotations"
            for ref in row["References"].split():
                source = current["parts"][ref]
                assert source["package"].split(":")[-1] == row["Footprint"], ref
                assert source["value"] == row["Value"], ref
                bom.append({**source, "partId": row["PartNumber"] or f'{source["package"]}:{source["value"]}', "mpn": row["PartNumber"] or None, "populated": True, "manufacturer": row["Manufacturer"] or None})
    bom.sort(key=lambda r: reader.refkey(r["ref"]))
    smt = {r["ref"] for r in bom if r["category"] == "smt"}
    source_only = {r["ref"] for r in current["placements"]} - smt
    assert source_only == {"R59"}, "Review new upstream BOM/board discrepancies"
    for parsed in [current, previous]:
        parsed["placements"] = [r for r in parsed["placements"] if r["ref"] in smt]
    old = {r["ref"]: r for r in previous["placements"]}; new = {r["ref"]: r for r in current["placements"]}
    assert set(new)-set(old) == {"D11", "D12"} and not set(old)-set(new)
    assert all(old[ref] == new[ref] for ref in old)
    assert (len(old), len(new)) == (145, 147)
    brief = {
        "schemaVersion": 1,
        "board": {"id": "libresolar-bms-8s50-ic", "name": "Drone BMS assembly review · Libre Solar 8S50", "revision": "v0.2", "widthMm": current["widthMm"], "heightMm": current["heightMm"]},
        "variant": "standard", "quantity": 10,
        "conventions": {"units": "mm", "coordinateFrame": "board-bottom-left", "rotation": "ccw", "bottomView": "top"},
        "source": {**manifest, "exportMethod": "Placement CSVs derived from unmodified, pinned KiCad revisions; values and identities from the upstream v0.2 BOM.", "sourceCoordinateTransform": current["transform"], "assemblyExportRevision": "v0.1.1", "authoritativeRevision": "v0.2", "populationSelection": "Use the published v0.2 BOM. Filter both placement projections to that BOM's SMT references; preserve all source geometry.", "sourceOnlyReferences": ["R59"]},
        "visual": current["visual"], "sourceFindings": [{"id": "SOURCE-R59-BOM", "severity": "warning", "message": "R59 exists in the PCB but is absent from the upstream published BOM. Both placement projections follow that BOM; this does not establish a DNP decision. Engineering disposition remains open.", "refs": ["R59"]}],
    }
    packet = {**brief, "bom": bom, "placements": previous["placements"], "authoritativePlacements": current["placements"]}
    clean = {**copy.deepcopy(packet), "placements": current["placements"]}
    for name, value in [("revision-mismatch.json", packet), ("clean.json", clean), ("brief.json", brief)]:
        (ROOT / name).write_text(json.dumps(value, indent=2, ensure_ascii=False)+"\n")
    write_csv(ROOT / "bom-v0.2.csv", [{**r, "populated": "true", "mpn": r["mpn"] or ""} for r in bom], ["ref", "partId", "value", "package", "category", "populated", "mpn"])
    columns = ["ref", "xMm", "yMm", "rotationDeg", "side", "package"]
    write_csv(ROOT / "placements-v0.1.1.csv", previous["placements"], columns)
    write_csv(ROOT / "source-placements-v0.2.csv", current["placements"], columns)
    mapping = {"bom": {"ref": "ref", "partId": "partId", "value": "value", "package": "package", "category": "category", "populated": "populated", "mpn": "mpn"}, "placements": {"ref": "ref", "x": "xMm", "y": "yMm", "rotation": "rotationDeg", "side": "side", "package": "package"}}
    (ROOT / "columns.json").write_text(json.dumps(mapping, indent=2)+"\n")
    receipt = {"status": "passed", "bomReferences": len(bom), "requiredSmt": len(smt), "olderPlacements": len(old), "addedInSourceRevision": [new[ref] for ref in ["D11", "D12"]], "commonPlacementsUnchanged": True, "sourceFilesUnmodified": True, "generatorSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "scope": "Assembly-record reconciliation. Hardware selection and flight qualification are separate from this example."}
    (ROOT / "preparation-receipt.json").write_text(json.dumps(receipt, indent=2)+"\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
