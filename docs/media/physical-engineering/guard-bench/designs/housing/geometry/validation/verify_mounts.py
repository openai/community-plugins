"""Verify BM-64 directly on the delivered base STEP using the Open Cascade kernel."""
import argparse
import hashlib
import json
import math
from pathlib import Path

import build123d as bd
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Cylinder


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect_base(solid, req):
    low, high = req["ear_z_mm"]
    mid = (low + high) / 2
    holes = []
    for face in solid.faces():
        surf = BRepAdaptor_Surface(face.wrapped)
        if surf.GetType() != GeomAbs_Cylinder:
            continue
        cyl = surf.Cylinder()
        axis = cyl.Axis()
        point, direction = axis.Location(), axis.Direction()
        x, y = point.X(), point.Y()
        if abs(x) <= req["outer_wall_x_mm"] or abs(direction.Z()) < .99999:
            continue
        # An outward ear end also has a cylindrical face. A through bore must
        # have a clear center and material all around its boundary.
        if solid.is_inside((x, y, mid)):
            continue
        r = cyl.Radius()
        rim = r + req["minimum_radial_rim_mm"]
        radial_material = [solid.is_inside((x + rim * math.cos(t * math.pi/8),
                                           y + rim * math.sin(t * math.pi/8), mid))
                           for t in range(16)]
        if not all(radial_material):
            continue
        box = face.bounding_box()
        clear = all(not solid.is_inside((x, y, z)) for z in [low+.01,mid,high-.01])
        holes.append({"axis_xy_mm":[round(x,5),round(y,5)], "diameter_mm":round(2*r,5),
                      "face_z_mm":[round(box.min.Z,5),round(box.max.Z,5)],
                      "clear_through":clear,"continuous_radial_material":all(radial_material)})
    # A kernel may split one full cylindrical surface at its parameter seam.
    found = {tuple(h["axis_xy_mm"]):h for h in holes}
    holes = [found[k] for k in sorted(found)]
    # Additional points establish that both ear necks actually fuse to the walls.
    bridges = all(solid.is_inside((s*x,y,mid)) for s in [-1,1] for x in [25,26,27] for y in [-3.4,0,3.4])
    return holes,bridges


def inspect_file(shape, req):
    solids=shape.solids()
    if len(solids)!=req["solid_count"]:
        return {"checks":{"single_valid_connected_base":False},"count":len(solids),"holes":[]}
    base=solids[0]
    holes,bridges=inspect_base(base,req)
    pitch=round(math.dist(*[h["axis_xy_mm"] for h in holes]),5) if len(holes)==2 else None
    correct_pair=len(holes)==len(req["axes_mm"])
    checks={
        "single_valid_connected_base":bool(base.is_valid),
        "ear_necks_join_both_walls":bridges,
        "two_external_bores":correct_pair,
        "drawing_axis_positions":correct_pair and all(
            abs(a-b)<=req["axis_coordinate_tolerance_mm"]
            for hole,target in zip(holes,req["axes_mm"]) for a,b in zip(hole["axis_xy_mm"],target)),
        "drawing_pitch":pitch is not None and abs(pitch-req["pitch_mm"])<=req["pitch_tolerance_mm"],
        "drawing_bore_diameters":correct_pair and all(
            abs(h["diameter_mm"]-req["bore_diameter_mm"])<=req["bore_diameter_tolerance_mm"] for h in holes),
        "through_entire_ear":correct_pair and all(h["clear_through"] and all(
            abs(a-b)<=req["ear_z_tolerance_mm"] for a,b in zip(h["face_z_mm"],req["ear_z_mm"])) for h in holes),
        "radial_material_around_both_bores":correct_pair and all(h["continuous_radial_material"] for h in holes)}
    return {"checks":checks,"count":len(solids),"holes":holes,"pitch_mm":pitch}


def require_exported_base(step_path):
    """Release gate for the authored interface, independent of recipe parameters."""
    schema=Path(__file__).with_name("bench-interface.json")
    req=json.loads(schema.read_text())
    measured=inspect_file(bd.import_step(step_path),req)
    failure=[name for name,ok in measured["checks"].items() if not ok]
    if failure:
        raise ValueError(f"{req['drawing']}: export has axes {[h['axis_xy_mm'] for h in measured['holes']]} "
                         f"and measured pitch {measured.get('pitch_mm')} mm; drawing requires "
                         f"{req['axes_mm']} and {req['pitch_mm']} mm. Failed {', '.join(failure)}.")
    return measured


def main():
    args=argparse.ArgumentParser(description=__doc__)
    args.add_argument("handoff",type=Path)
    args.add_argument("--report",required=True,type=Path)
    opt=args.parse_args()
    schema=Path(__file__).with_name("bench-interface.json")
    req=json.loads(schema.read_text())
    standalone=opt.handoff/"components/base.step"
    installed=opt.handoff/"assembly.step"
    component=inspect_file(bd.import_step(standalone),req)
    assembly=bd.import_step(installed)
    bases=[child for child in assembly.children if child.label=="base"]
    if len(bases)!=1:
        raise ValueError("Expected exactly one installed base named base")
    placed=inspect_file(bases[0],req)
    passed=all(component["checks"].values()) and component==placed
    report={"drawing":req["drawing"],"drawing_sha256":digest(schema),"checker_sha256":digest(Path(__file__)),
            "status":"pass" if passed else "fail","component_step":{"sha256":digest(standalone),**component},
            "installed_step":{"sha256":digest(installed),**placed},"component_and_installed_agree":component==placed}
    opt.report.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({"status":report["status"],"component_and_installed_agree":report["component_and_installed_agree"],
                      "axes":[h["axis_xy_mm"] for h in component["holes"]],"pitch_mm":component.get("pitch_mm"),
                      "failed":[key for key,value in component["checks"].items() if not value]}))
    raise SystemExit(0 if passed else 1)


if __name__=="__main__":
    main()
