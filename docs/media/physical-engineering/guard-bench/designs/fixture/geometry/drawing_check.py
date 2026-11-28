"""Independent dimensional inspection of the exported guard-coupon bench (mm)."""
import argparse
import hashlib
import json
import math
from pathlib import Path

import build123d as bd
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Cylinder
from OCP.TopAbs import TopAbs_REVERSED


# These limits are numerical CAD comparisons, independent of print and test tolerances.
LENGTH_TOL = 0.05
ANGLE_TOL = 1e-5
VOLUME_TOL = 1e-5


def near(value, nominal):
    return abs(value - nominal) <= LENGTH_TOL + 1e-9


def clean(value):
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items()}
    return value


def record(checks, key, passed, actual, expected):
    checks.append({"id": key, "status": "pass" if passed else "fail",
                   "actual": clean(actual), "expected": expected})


def geometry_volume(a, b):
    common = a.intersect(b)
    return sum(shape.volume for shape in common) if common else 0.0


def rectangular_prism(x_min, x_max, y_min, y_max, z_min, z_max):
    return bd.Solid.make_box(x_max - x_min, y_max - y_min, z_max - z_min,
                             bd.Plane(origin=(x_min, y_min, z_min)))


def assembly_nodes(shape, parent_location=None, parent_path=()):
    placement = (parent_location or bd.Location()) * shape.location
    path = (*parent_path, shape.label)
    yield "/".join(path), shape.located(placement)
    for child in shape.children:
        yield from assembly_nodes(child, placement, path)


def cylinders(shape):
    result = []
    for face in shape.faces():
        surface = BRepAdaptor_Surface(face.wrapped)
        if surface.GetType() != GeomAbs_Cylinder:
            continue
        cylinder = surface.Cylinder()
        axis = cylinder.Axis()
        bounds = face.bounding_box()
        result.append({"radius_mm": cylinder.Radius(), "center_xy_mm": list(axis.Location().Coord()[:2]),
                       "direction": list(axis.Direction().Coord()), "z_mm": [bounds.min.Z, bounds.max.Z],
                       "internal": face.wrapped.Orientation() == TopAbs_REVERSED,
                       "sweep_deg": math.degrees(surface.LastUParameter() - surface.FirstUParameter())})
    return result


def vertical(surface):
    return math.hypot(*surface["direction"][:2]) <= ANGLE_TOL


def load_step(delivery, filename, hashes):
    file = delivery / filename
    with file.open("rb") as stream:
        hashes[filename] = hashlib.file_digest(stream, "sha256").hexdigest()
    return bd.import_step(file)


def cylindrical_feature(surfaces, at_xy, diameter, internal):
    candidates = [s for s in surfaces if s["internal"] == internal and vertical(s)
                  and math.dist(at_xy, s["center_xy_mm"]) < 0.6 and s["sweep_deg"] > 350]
    if not candidates:
        raise ValueError(f"Missing vertical {'bore' if internal else 'shaft'} near {at_xy}")
    return min(candidates, key=lambda s: (abs(s["radius_mm"] * 2 - diameter), -s["sweep_deg"]))


def diam(surf):
    return 2 * surf["radius_mm"]


def plane_at(part, axis_index, sign, transverse):
    """Find outward planar faces covering the witness point along the selected axis."""
    hits = []
    for face in part.faces():
        if face.geom_type != bd.GeomType.PLANE or sign * tuple(face.normal_at())[axis_index] < 1 - ANGLE_TOL:
            continue
        plane_coord = tuple(face.center())[axis_index]
        point = list(transverse)
        point.insert(axis_index, plane_coord)
        if face.distance_to(bd.Vertex(*point)) < 1e-5:
            hits.append((plane_coord, face))
    return hits


def inspect_arc(checks, key, coupon, plate, thickness):
    surfaces = [s for s in cylinders(coupon) if vertical(s) and
                math.dist(s["center_xy_mm"], (-48, -4)) < 0.6 and s["sweep_deg"] >= 45]
    inner = min((s for s in surfaces if s["internal"]), key=lambda s: abs(s["radius_mm"] - 16.30))
    outer = min((s for s in surfaces if not s["internal"]), key=lambda s: abs(s["radius_mm"] - 16.30 - thickness))
    wall = outer["radius_mm"] - inner["radius_mm"]
    heights = [s["z_mm"][1] - s["z_mm"][0] for s in (inner, outer)]
    valid = (near(inner["radius_mm"], 16.30) and near(wall, thickness)
             and all(near(h, 5.50) for h in heights)
             and math.dist(outer["center_xy_mm"], inner["center_xy_mm"]) <= LENGTH_TOL)
    record(checks, key + ".arc", valid, {"inner_radius_mm": inner["radius_mm"],
           "outer_radius_mm": outer["radius_mm"], "radial_wall_mm": wall, "heights_mm": heights,
           "center_xy_mm": inner["center_xy_mm"], "analytic_sweeps_deg": [inner["sweep_deg"],outer["sweep_deg"]]},
           {"inner_radius_mm": 16.30, "height_mm": 5.50, "wall_mm": thickness, "coaxial": True})
    free_gaps = []
    for angle in (20, 45, 75):
        r = (inner["radius_mm"] + outer["radius_mm"]) / 2
        xy = [-48 + r * math.cos(math.radians(angle)), -4 + r * math.sin(math.radians(angle))]
        underside = min(v for v, _ in plane_at(coupon, 2, -1, xy))
        bed = max(v for v, _ in plane_at(plate, 2, 1, xy))
        free_gaps.append(underside - bed)
    overlap = geometry_volume(coupon, plate)
    record(checks, key + ".free-span", all(near(g, 3.0) for g in free_gaps) and overlap <= VOLUME_TOL,
           {"underside_to_plate_mm": free_gaps, "sample_angles_deg": [20,45,75], "plate_overlap_mm3": overlap},
           {"nominal_clearance_mm": 3.0, "coupon_plate_volume_overlap_mm3": 0})


def inspect_stop(checks, key, coupon, stop):
    # Probe the center of the one-millimetre-wide stop nose in the XZ contact projection.
    witness = (-51, 13)
    moving_faces = plane_at(coupon, 1, -1, witness)
    stopped_faces = plane_at(stop, 1, 1, witness)
    pairs = [(y - fixed_y, y, fixed_y, face, stop_face) for y, face in moving_faces
             for fixed_y, stop_face in stopped_faces if y >= fixed_y]
    distance, moving_y, fixed_y, moving, fixed = min(pairs, key=lambda p: p[0])
    at_contact = (bd.Pos(0, -distance, 0) * moving).intersect(fixed)
    area = sum(piece.area for piece in at_contact) if at_contact else 0.0
    load_y = max(v for v, _ in plane_at(coupon, 1, 1, witness))
    overlap = geometry_volume(coupon, stop)
    record(checks, key + ".travel-stop", near(distance, 2.0) and area > 0.1 and near(load_y,15)
           and overlap <= VOLUME_TOL, {"load_direction": [0,-1,0], "witness_xz_mm": witness,
           "load_face_y_mm": load_y, "coupon_stop_face_y_mm": moving_y, "stop_face_y_mm": fixed_y,
           "travel_mm": distance, "projected_contact_area_mm2": area, "rest_overlap_mm3": overlap},
           {"nominal_face_separation_along_negative_Y_mm": 2.00, "load_face_y_mm": 15.00,
            "opposed_planar_faces_with_positive_projected_contact": True})


def inspect_root(checks, key, coupon, plate):
    holes, pins = cylinders(coupon), cylinders(plate)
    pairs = []
    for xy, hole_d, pin_d in [((-36.5,-9),2.80,2.50), ((-26.5,-15),3.30,3.00)]:
        hole = cylindrical_feature(holes, xy, hole_d, True)
        pin = cylindrical_feature(pins, xy, pin_d, False)
        fit = diam(hole) - diam(pin)
        offset = math.dist(hole["center_xy_mm"], pin["center_xy_mm"])
        height = min(pin["z_mm"][1], hole["z_mm"][1]) - max(pin["z_mm"][0], hole["z_mm"][0])
        pairs.append({"bore_xy_mm": hole["center_xy_mm"], "pin_xy_mm": pin["center_xy_mm"],
                      "bore_diameter_mm": diam(hole), "pin_diameter_mm": diam(pin),
                      "axis_offset_mm": offset, "diametral_gap_mm": fit, "axial_overlap_mm": height,
                      "within_drawing": near(diam(hole),hole_d) and near(diam(pin),pin_d)
                      and offset <= LENGTH_TOL and near(fit,.30) and height > .5})
    # Plane-to-plane witness on the solid root between its center screw and locator bores.
    root_witness = (-33.7,-12)
    floor = min(z for z, _ in plane_at(coupon, 2, -1, root_witness))
    ledge = max(z for z, _ in plane_at(plate, 2, 1, root_witness))
    record(checks, key + ".root-location", all(p["within_drawing"] for p in pairs) and near(floor-ledge,0),
           {"locators": pairs, "root_seat_gap_mm": floor-ledge, "root_witness_xy_mm": root_witness},
           "Two distinct engaged round locators with 0.30 mm diametral gaps and a seated root")


def inspect_hardware(checks, key, parts, coupon):
    plate = parts["plate"]
    plate_holes = cylinders(plate)
    locations = [
        ("sensor-left", (-2,0), "sensor_screw_left", "sensor_nut_left", parts["sensor_base_proxy"], 11, 10),
        ("sensor-right", (62,0), "sensor_screw_right", "sensor_nut_right", parts["sensor_base_proxy"], 11, 10),
        ("coupon-root", (-31.5,-12), "root_screw", "root_nut", coupon, 16.5, 16),
        ("stop", (-51,2), "stop_screw", "stop_nut", parts["travel_stop"], 12.5, 12),
    ]
    for label, xy, screw_name, nut_name, clamped, seat, length in locations:
        screw, nut = parts[screw_name], parts[nut_name]
        screw_surfaces = cylinders(screw)
        shaft = cylindrical_feature(screw_surfaces, xy, 3.0, False)
        head = cylindrical_feature(screw_surfaces, xy, 5.5, False)
        clearance = cylindrical_feature(cylinders(clamped), xy, 3.4, True)
        plate_bore = cylindrical_feature(plate_holes, xy, 3.4, True)
        nut_bore = cylindrical_feature(cylinders(nut), xy, 3.0, True)
        offsets = [math.dist(shaft["center_xy_mm"], axis["center_xy_mm"]) for axis in
                   (clearance, plate_bore, nut_bore, head)]
        nut_bounds = nut.bounding_box()
        tip, shoulder = shaft["z_mm"]
        nut_overlap = min(shoulder,nut_bounds.max.Z) - max(tip,nut_bounds.min.Z)
        # Check that the bearing ring of the head actually touches the clamped part.
        head_faces = [f for f in screw.faces() if f.geom_type == bd.GeomType.PLANE
                      and f.normal_at().Z < -0.9999 and near(f.center().Z,head["z_mm"][0])]
        seat_faces = [f for f in clamped.faces() if f.geom_type == bd.GeomType.PLANE
                      and f.normal_at().Z > .9999 and near(f.center().Z,seat)]
        bearing = 0
        for a in head_faces:
            for b in seat_faces:
                common = a.intersect(b)
                if common:
                    bearing += sum(shape.area for shape in common)
        accepted = (max(offsets) <= LENGTH_TOL and near(head["z_mm"][0],seat)
                    and near(shoulder-tip,length) and near(diam(head),5.5) and near(head["z_mm"][1]-head["z_mm"][0],3)
                    and diam(clearance) - diam(shaft) > .05 and diam(plate_bore)-diam(shaft) > .05
                    and nut_overlap >= 2.35 and tip > 0 and bearing > 1)
        record(checks, key + ".fastener-" + label, accepted, {"center_xy_mm": shaft["center_xy_mm"],
               "max_axis_offset_mm": max(offsets), "shank_length_mm": shoulder-tip,
               "bearing_z_mm": head["z_mm"][0], "head_od_mm": diam(head), "head_height_mm": head["z_mm"][1]-head["z_mm"][0],
               "shaft_od_mm": diam(shaft), "plate_bore_mm": diam(plate_bore), "part_bore_mm": diam(clearance),
               "tip_z_mm": tip, "nut_axial_engagement_envelope_mm": nut_overlap,
               "bearing_contact_area_mm2": bearing},
               {"nominal_shank_length_mm": length, "bearing_z_mm": seat, "head_mm": [5.5,3.0],
                "coaxial_clearance_holes_and_positive_head_seat": True,"nominal_nut_engagement_mm": 2.4})


def inspect_sensor(checks, key, parts):
    base, lid = parts["sensor_base_proxy"], parts["sensor_lid_proxy"]
    base_bores = cylinders(base)
    axes = [cylindrical_feature(base_bores, point, 3.4, True) for point in ((-2,0),(62,0))]
    xy = [a["center_xy_mm"] for a in axes]
    centers = [math.dist(actual,target) for actual,target in zip(xy,[(-2,0),(62,0)])]
    spans = [a["z_mm"] for a in axes]
    plate = parts["plate"]
    seat_gap = base.distance_to(plate)
    blockage = []
    for axis in axes:
        x,y = axis["center_xy_mm"]
        probe = bd.Solid.make_cylinder(axis["radius_mm"]-.001,20,bd.Plane(origin=(x,y,6)))
        blockage.append(geometry_volume(base,probe))
    pitch = math.dist(*xy)
    ok = (max(centers) <= LENGTH_TOL and near(pitch,64) and all(near(diam(a),3.4) for a in axes)
          and all(near(z[0],8) and near(z[1],11) for z in spans)
          and max(blockage)<=VOLUME_TOL and near(seat_gap,0))
    record(checks, key + ".sensor-mount",ok, {"centers_xy_mm":xy, "pitch_mm": pitch,
           "bore_diameters_mm":[diam(a) for a in axes],"bore_z_mm":spans, "gauge_blocked_mm3":blockage,
           "base_to_plate_gap_mm":seat_gap},
           "Two Ø3.40 mm vertical through bores, 64.00 mm apart, with the ears seated at Z8")
    # Conservative rectangular service prism encloses the lid and its head proxies during
    # a straight 25 mm lift. Release all four lid screws before lifting. No lateral maneuver.
    bounds = lid.bounding_box()
    service = rectangular_prism(bounds.min.X,bounds.max.X,bounds.min.Y,bounds.max.Y,bounds.min.Z,bounds.max.Z+25)
    fixed = {n:p for n,p in parts.items() if n != "sensor_lid_proxy"}
    blocked = {n:geometry_volume(service,p) for n,p in fixed.items()}
    heads = []
    for x in (9,51):
        for y in (-13,13):
            head = cylindrical_feature(cylinders(lid), (x,y), 3.56, False)
            start = head["z_mm"][1]
            probe = bd.Solid.make_cylinder(3.0,30.0,bd.Plane(origin=(x,y,start)))
            hits = sum(geometry_volume(probe,p) for p in fixed.values())
            heads.append({"xy_mm":head["center_xy_mm"],"top_z_mm":start,"blocked_mm3":hits})
    dimensions = [bounds.size.X,bounds.size.Y,bounds.min.Z,bounds.max.Z]
    passed = (all(near(v,n) for v,n in zip(dimensions,(52,36,23,26.65))) and max(blocked.values()) <= VOLUME_TOL
              and all(near(h["top_z_mm"],26.65) and h["blocked_mm3"]<=VOLUME_TOL for h in heads))
    record(checks,key+".lid-service",passed,{"lid_width_depth_bottom_top_mm":dimensions,
           "straight_lift_mm":25,"interference_by_obstacle_mm3":blocked,
           "vertical_driver_diameter_mm":6,"vertical_driver_length_mm":30,"driver_corridors":heads},
           "52 × 36 mm lid with screw heads; 25 mm upward lift and four Ø6 × 30 mm driver corridors free of fixture material")


def symmetric_volume(a,b):
    return (a-b).volume + (b-a).volume


def verify_coupon_mesh(delivery, filename, coupon, hashes, checks, key):
    import struct
    import numpy as np
    data = (delivery/filename).read_bytes()
    hashes[filename] = hashlib.sha256(data).hexdigest()
    count, = struct.unpack_from("<I",data,80)
    if count == 0 or len(data) != 84 + 50 * count:
        raise ValueError("Invalid binary STL size")
    dtype = np.dtype([("normal","<f4",3),("xyz","<f4",(3,3)),("attribute","<u2")])
    triangles = np.frombuffer(data,dtype=dtype,count=count,offset=84)["xyz"].astype(float)
    vertices,index = np.unique(triangles.reshape(-1,3),axis=0,return_inverse=True)
    corner = index.reshape((-1,3))
    directed = np.vstack([corner[:,(0,1)],corner[:,(1,2)],corner[:,(2,0)]])
    _,counts = np.unique(np.sort(directed,axis=1),axis=0,return_counts=True)
    unique_directed = len(np.unique(directed,axis=0))
    signed_volume = np.einsum("ij,ij->i",triangles[:,0],np.cross(triangles[:,1],triangles[:,2])).sum()/6
    mesh_volume = abs(float(signed_volume))
    error = abs(mesh_volume - coupon.volume)/coupon.volume
    bounds = coupon.bounding_box()
    span_error = np.max(np.abs(np.ptp(vertices,axis=0) - list(bounds.size)))
    bed = float(vertices[:,2].min())
    closed = bool(np.all(counts==2)) and len(directed)==unique_directed
    record(checks,key+".coupon-print",closed and near(bed,0) and span_error<=.10 and error<=.01,
           {"triangles":count,"closed_oriented_edges":closed,"bed_z_mm":bed,
            "span_error_mm":float(span_error),"mesh_volume_relative_error":error},
           "Closed oriented STL at Z0; spans within 0.1 mm and volume within 1% of the measured coupon STEP")


def inspect_delivery(delivery):
    checks,hashes = [],{}
    report = {"status":"fail","inputs":hashes,"checks":checks,
              "numerical_tolerances":{"nominal_length_mm":LENGTH_TOL,"intersection_volume_mm3":VOLUME_TOL},
              "scope":"Digital geometry and rigid-body access envelopes. Release the lid screws before lifting the lid."}
    variants = [("100","assembly.step","components/coupon_100.step","print/coupon_100.stl",1.00),
                ("140","alternate/assembly_140.step","alternate/coupon_140.step","alternate/coupon_140.stl",1.40)]
    installed = {}
    try:
        for key,assembly_path,coupon_path,print_path,thickness in variants:
            assembly = load_step(delivery,assembly_path,hashes)
            parts = {path.rsplit("/",1)[-1]: part for path,part in assembly_nodes(assembly) if path.count("/") == 1}
            coupon = parts["coupon_"+key]
            installed[key] = parts
            component = load_step(delivery,coupon_path,hashes)
            error = symmetric_volume(component,coupon)
            record(checks,key+".coupon-export",error<=VOLUME_TOL and len(coupon.solids())==1 and coupon.is_valid,
                   {"installed_solids":len(coupon.solids()),"symmetric_difference_mm3":error},
                   "One valid coupon solid; installed occurrence agrees with separately delivered component STEP")
            for routine, args in [(inspect_arc,(coupon,parts["plate"],thickness)),
                                  (inspect_stop,(coupon,parts["travel_stop"])),
                                  (inspect_root,(coupon,parts["plate"])),
                                  (inspect_hardware,(parts,coupon)),(inspect_sensor,(parts,))]:
                try:
                    routine(checks,key,*args)
                except Exception as exc:
                    record(checks,key+"."+routine.__name__,False,{"error":repr(exc)},"Complete STEP measurement")
            verify_coupon_mesh(delivery,print_path,coupon,hashes,checks,key)
        # The common root region ends before the curved wall begins. Native booleans
        # include the clamp seat, screw bore, both keyed bores and the two end walls.
        clip = rectangular_prism(-42,-20,-20,-6,8,20)
        roots = []
        for k in ("100","140"):
            region = installed[k]["coupon_"+k].intersect(clip)
            if not region or len(region) != 1:
                raise ValueError("Expected one solid clipped coupon root")
            roots.append(region[0])
        difference = symmetric_volume(*roots)
        record(checks,"common-root",difference<=VOLUME_TOL and min(s.volume for s in roots)>150,
               {"comparison_xyz_limits_mm":[[-42,-20],[-20,-6],[8,20]],"material_volumes_mm3":[s.volume for s in roots],
                "symmetric_difference_mm3":difference},"Identical positive root geometry in installed coordinates")
        fixed_a = {n for n in installed["100"] if n != "coupon_100"}
        fixed_b = {n for n in installed["140"] if n != "coupon_140"}
        fixed = {n:symmetric_volume(installed["100"][n],installed["140"][n]) for n in sorted(fixed_a & fixed_b)}
        record(checks,"fixed-fixture",fixed_a==fixed_b and len(fixed)==12 and max(fixed.values())<=VOLUME_TOL,
               {"symmetric_difference_mm3_by_component":fixed,"same_named_inventory":fixed_a==fixed_b},
               "Same twelve fixed fixture pieces and installed positions in both exports")
        if checks and all(c["status"]=="pass" for c in checks):
            report["status"] = "pass"
    except Exception as exc:
        record(checks,"inspection-complete",False,{"error":repr(exc)},"All named measurable features available")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("delivery",type=Path)
    parser.add_argument("--report",type=Path,required=True)
    args = parser.parse_args()
    result = inspect_delivery(args.delivery)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(result,indent=2,allow_nan=False)+"\n")
    print(result["status"],len(result["checks"]),"checks",args.report)
    raise SystemExit(0 if result["status"]=="pass" else 1)
