#!/usr/bin/env python3
"""Independent readback for the declared drone fixture, never generated parameters.

Measures exported STEP with OpenCascade and validates the exact binary STL files.
This is a bounded fixture checker, not structural or general printability analysis.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import struct
import sys


def _kernel():
    import build123d as bd
    return bd


def world_parts(node, parent=None):
    """Yield leaves in world coordinates, composing each ancestor exactly once."""
    bd = _kernel()
    location = (bd.Location() if parent is None else parent) * node.location
    if node.children:
        for child in node.children:
            yield from world_parts(child, location)
    else:
        yield node.located(location)


def read_parts(step_path):
    shape = _kernel().import_step(str(Path(step_path).resolve(strict=True)))
    return _parts(shape)


def _parts(shape):
    result = {}
    for part in world_parts(shape):
        if part.label not in {"tray", "bridge"}:
            raise ValueError(f"unsupported printable leaf: {part.label!r}")
        if part.label in result:
            raise ValueError(f"duplicate printable leaf: {part.label}")
        result[part.label] = part
    if set(result) != {"tray", "bridge"}:
        raise ValueError("expected exactly one tray and one bridge")
    return result


def _finite(value):
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)) and not math.isfinite(value):
        raise ValueError("nonfinite requirement value")
    if isinstance(value, dict):
        for v in value.values():
            _finite(v)
    if isinstance(value, (list, tuple)):
        for v in value:
            _finite(v)


def _validate_requirements(req):
    _finite(req)
    if req.get("schema_version")!=1 or req.get("fixture_id")!="drone-module-v1":
        raise ValueError("unsupported fixture schema or identifier")
    def vector(value,count,positive=False):
        if (not isinstance(value,list) or len(value)!=count or
            any(isinstance(v,bool) or not isinstance(v,(int,float)) or
                (positive and v<=0) for v in value)):
            raise ValueError("invalid fixture dimensional vector")
    vector(req["envelope_max_mm"],3,True)
    vector(req["frame"]["pitch_mm"],2,True)
    battery=req["battery"];board=req["board"]
    if battery["selected"] not in ("A","B"):
        raise ValueError("unsupported battery selection")
    for name in ("A","B"):
        vector(battery["envelopes_mm"][name],3,True)
    for value in (battery["seated_center_mm"],battery["insertion_translation_mm"],
                  board["center_mm"],board["component_keepout_center_mm"]):
        vector(value,3)
    for value in (board["envelope_mm"],board["component_keepout_mm"]):
        vector(value,3,True)
    vector(board["mount_pitch_mm"],2,True)
    vector(battery["lateral_clearance_per_side_mm"],2,True)
    if battery["lateral_clearance_per_side_mm"][0]>battery["lateral_clearance_per_side_mm"][1]:
        raise ValueError("inverted clearance range")
    for value in (req["frame"]["hole_diameter_mm"],board["hole_diameter_mm"],
                  battery["required_support_area_mm2"],board["required_support_area_mm2"],
                  req["bridge_attachment"]["hole_diameter_mm"],
                  req["bridge_attachment"]["minimum_total_contact_area_mm2"],
                  req["manufacturing"]["minimum_declared_planar_wall_mm"],
                  req["manufacturing"]["density_g_per_cm3"],req["manufacturing"]["polymer_mass_max_g"]):
        if isinstance(value,bool) or not isinstance(value,(int,float)) or value<=0:
            raise ValueError("fixture acceptance values must be positive numbers")
    centers=req["bridge_attachment"]["hole_centers_xy_mm"]
    if len(centers)!=4 or len({tuple(v) for v in centers})!=4:
        raise ValueError("four unique bridge attachment positions required")
    for center in centers:
        vector(center,2)


def _report():
    return {"schema_version": 1, "status": "unknown", "units": "mm",
            "checks": [], "measurements": {},
            "scope": "synthetic fixture geometry and declared planar walls; no strength, physical print or flight qualification"}


def _check(report, name, passed, actual, expected):
    report["checks"].append({"id": name, "status": "pass" if passed else "fail",
                             "actual": actual, "expected": expected})


def _finish(report):
    statuses = {c["status"] for c in report["checks"]}
    report["status"] = "unknown" if "unknown" in statuses or not statuses else "fail" if "fail" in statuses else "pass"
    return report


def _unknown(report, name, exc):
    report["checks"].append({"id": name, "status": "unknown", "error": str(exc)})
    return _finish(report)


def _cylinders(part):
    """Read complete inward cylindrical bores, excluding bosses and edge rounds.

    A cylindrical surface is not necessarily a hole. Rounded mounting pads have
    convex cylinders and internal edge fillets have only a partial revolution.
    Required bores still need their exact position, diameter and through span;
    a missing or interrupted bore cannot satisfy the interface matcher.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder
    holes = []
    for face in part.faces():
        surface = BRepAdaptor_Surface(face.wrapped, True)
        if surface.GetType() != GeomAbs_Cylinder:
            continue
        cylinder = surface.Cylinder()
        axis = cylinder.Axis()
        point, direction = axis.Location(), axis.Direction()
        bounds = face.bounding_box()
        probe=face.position_at(.5,.5)
        direction_vector=_kernel().Vector(direction.X(),direction.Y(),direction.Z())
        radial=probe-_kernel().Vector(point.X(),point.Y(),point.Z())
        radial=radial-direction_vector*radial.dot(direction_vector)
        inward=face.normal_at(.5,.5).dot(radial.normalized())<-.999999
        angular_span=surface.LastUParameter()-surface.FirstUParameter()
        if not inward or abs(angular_span-2*math.pi)>1e-5:
            continue
        holes.append({"x_mm": point.X(), "y_mm": point.Y(),
                      "diameter_mm": 2*cylinder.Radius(),
                      "axis": [direction.X(), direction.Y(), direction.Z()],
                      "z_min_mm": bounds.min.Z, "z_max_mm": bounds.max.Z,
                      "surface_area_mm2": face.area,"inward_bore":inward})
    return sorted(holes, key=lambda h: (h["z_min_mm"], h["x_mm"], h["y_mm"]))


def _plane_faces(part, axis, coordinate=None, normal=None, tolerance=.01):
    bd = _kernel()
    found = []
    for face in part.faces():
        if face.geom_type != bd.GeomType.PLANE:
            continue
        box = face.bounding_box()
        low, high = tuple(box.min)[axis], tuple(box.max)[axis]
        if abs(high-low) > tolerance:
            continue
        if coordinate is not None and abs((high+low)/2-coordinate) > tolerance:
            continue
        if normal is not None and tuple(face.normal_at())[axis]*normal < .999999:
            continue
        found.append(face)
    return found


def _covering_plane(part, axis, ranges, normal=None, side=None):
    found = []
    for face in _plane_faces(part, axis, normal=normal):
        if side is not None and _coord(face,axis)*side<=0:
            continue
        box = face.bounding_box()
        if all(tuple(box.min)[i] <= a+.01 and tuple(box.max)[i] >= b-.01
               for i, (a,b) in ranges.items()):
            # A U-shaped or perforated face can have the right bounding box.
            # Require exact coverage of the whole declared rectangle.
            axes=list(ranges)
            center=[0.,0.,0.];center[axis]=_coord(face,axis)
            for i in axes:
                center[i]=sum(ranges[i])/2
            direction=[0.,0.,0.];direction[axis]=1
            x_direction=[0.,0.,0.];x_direction[axes[0]]=1
            size=[ranges[i][1]-ranges[i][0] for i in axes]
            bd=_kernel()
            patch=bd.Face.make_rect(*size,bd.Plane(center,x_dir=x_direction,z_dir=direction))
            intersection=face.intersect(patch,include_touched=True)
            area=sum(item.area for item in intersection) if intersection else 0
            if abs(area-size[0]*size[1])<=.001:
                found.append(face)
    if len(found) != 1:
        raise ValueError(f"expected one covering plane on axis {axis}, found {len(found)}")
    return found[0]


def _coord(face, axis):
    box = face.bounding_box()
    return (tuple(box.min)[axis]+tuple(box.max)[axis])/2


def _box(size, center):
    bd = _kernel()
    return bd.Pos(*center)*bd.Box(*size)


def _overlap(a, b):
    from cad.geometry import overlap_volume
    return sum(overlap_volume(x,y) for x in a.solids() for y in b.solids())


def _contact(a, b, z):
    """Area on the explicit coincident horizontal support plane, with no overlap."""
    area = 0.
    for fa in _plane_faces(a, 2, z, 1):
        for fb in _plane_faces(b, 2, z, -1):
            result = fa.intersect(fb, include_touched=True)
            if result:
                area += sum(item.area for item in result)
    return area


def _hole_match(holes, expected, diameter, span, tolerance):
    remaining = list(holes)
    chosen = []
    for x,y in expected:
        matches = [h for h in remaining if math.hypot(h["x_mm"]-x,h["y_mm"]-y) <= tolerance]
        if len(matches) != 1:
            return False, chosen
        h = matches[0]
        remaining.remove(h)
        chosen.append(h)
        height=h["z_max_mm"]-h["z_min_mm"]
        if (not h["inward_bore"] or abs(h["diameter_mm"]-diameter)>tolerance or
            abs(h["axis"][0])>1e-5 or abs(h["axis"][1])>1e-5 or
            abs(abs(h["axis"][2])-1)>1e-5 or
            abs(h["z_min_mm"]-span[0])>tolerance or abs(h["z_max_mm"]-span[1])>tolerance or
            abs(h["surface_area_mm2"]-math.pi*h["diameter_mm"]*height)>.01):
            return False, chosen
    return True, chosen


def _through_hole_match(part, holes, expected, diameter, span, tolerance):
    """Match bore surfaces and independently prove the whole bore is open.

    A nearly full-height cylinder can still be a blind pocket: a thin end
    membrane may be smaller than the dimensional tolerance. Probe the actual
    bore's interior over the entire required span, using only a numerical
    radial inset. Extend through the actual part's Z bounds as well as the
    required span: accepted position/height tolerances must not hide an end cap
    just beyond the nominal span. Acceptance tolerances never excuse material.
    """
    matched, selected = _hole_match(holes, expected, diameter, span, tolerance)
    probes = []
    if not matched:
        return False, selected, probes
    bd = _kernel()
    bounds = part.bounding_box()
    bottom, top = min(span[0], bounds.min.Z), max(span[1], bounds.max.Z)
    for hole in selected:
        radius = hole["diameter_mm"] / 2
        inset = min(1e-5, radius / 1000)
        probe = bd.Pos(hole["x_mm"], hole["y_mm"], (bottom+top)/2) * bd.Cylinder(radius-inset, top-bottom)
        overlap = _overlap(part, probe)
        maximum = min(1e-5, probe.volume * 1e-6)
        probes.append({"x_mm": hole["x_mm"], "y_mm": hole["y_mm"], "radius_inset_mm": inset,
                       "z_span_mm": [bottom, top], "overlap_mm3": overlap, "maximum_overlap_mm3": maximum})
        matched = matched and math.isfinite(overlap) and 0 <= overlap <= maximum
    return matched, selected, probes


def _pitch(holes):
    if len(holes)!=4:
        return None
    return [max(h[k] for h in holes)-min(h[k] for h in holes) for k in ("x_mm","y_mm")]


def verify_shape(shape, requirements):
    """Measure a BRep tree. Public entry for transformed-assembly contract tests."""
    report=_report()
    try:
        _validate_requirements(requirements)
        if requirements.get("units") != "mm":
            _check(report,"units",False,requirements.get("units"),"mm")
            return _finish(report)
        return _verify_parts(_parts(shape),requirements,report)
    except Exception as exc:
        return _unknown(report,"geometry_readback",exc)


def verify(step_path, requirements):
    """Verify the actual exported STEP. requirements is a dict, never design inputs."""
    report=_report()
    try:
        _validate_requirements(requirements)
        if requirements.get("units") != "mm":
            _check(report,"units",False,requirements.get("units"),"mm")
            return _finish(report)
        return _verify_parts(read_parts(step_path),requirements,report)
    except Exception as exc:
        return _unknown(report,"geometry_readback",exc)


def _verify_parts(parts, req, report):
    bd=_kernel()
    tol=req["tolerances"]["dimension_mm"]
    itol=req["tolerances"]["interface_mm"]
    vtol=req["tolerances"]["positive_overlap_mm3"]
    if not (0 < tol <= .01 and 0 < itol <= .05 and 0 < vtol <= .001):
        raise ValueError("tolerances exceed supported fixture limits")
    measurements=report["measurements"]
    measurements["parts"]={}
    for label,part in parts.items():
        valid=len(part.solids())==1 and part.is_valid and part.volume>0
        _check(report,f"{label}_solid",valid,{"count":len(part.solids()),"valid":part.is_valid,"volume_mm3":part.volume},"one valid positive-volume solid")
        if not valid:
            return _finish(report)
        bounds=part.bounding_box()
        measurements["parts"][label]={"volume_mm3":part.volume,"bounds_mm":list(bounds.size),
            "bounds_min_mm":list(bounds.min),"bounds_max_mm":list(bounds.max),
            "center_mm":list(part.center(bd.CenterOf.MASS)),"holes":_cylinders(part)}
    tray,bridge=parts["tray"],parts["bridge"]
    all_min=[min(v["bounds_min_mm"][i] for v in measurements["parts"].values()) for i in range(3)]
    all_max=[max(v["bounds_max_mm"][i] for v in measurements["parts"].values()) for i in range(3)]
    envelope=[b-a for a,b in zip(all_min,all_max)]
    measurements["polymer_envelope_mm"]=envelope
    _check(report,"polymer_envelope",all(a<=b+tol for a,b in zip(envelope,req["envelope_max_mm"])),envelope,req["envelope_max_mm"])
    frame=req["frame"]
    fxy=[(sx*frame["pitch_mm"][0]/2,sy*frame["pitch_mm"][1]/2) for sx in (-1,1) for sy in (-1,1)]
    tray_holes=measurements["parts"]["tray"]["holes"]
    bridge_holes=measurements["parts"]["bridge"]["holes"]
    match,chosen,probes=_through_hole_match(tray,tray_holes,fxy,frame["hole_diameter_mm"],(0,3),itol)
    _check(report,"frame_interface",match,chosen,{"centers_xy_mm":fxy,"diameter_mm":frame["hole_diameter_mm"]})
    report["checks"][-1]["interior_probes"]=probes
    measurements["frame_pitch_mm"]=_pitch(chosen)
    board=req["board"]
    bxy=[(sx*board["mount_pitch_mm"][0]/2,sy*board["mount_pitch_mm"][1]/2) for sx in (-1,1) for sy in (-1,1)]
    match,chosen,probes=_through_hole_match(bridge,bridge_holes,bxy,board["hole_diameter_mm"],(30,33),itol)
    _check(report,"board_interface",match,chosen,{"centers_xy_mm":bxy,"diameter_mm":board["hole_diameter_mm"]})
    report["checks"][-1]["interior_probes"]=probes
    measurements["board_pitch_mm"]=_pitch(chosen)
    attach=req["bridge_attachment"]
    for label,holes,span in (("tray",tray_holes,(0,3)),("bridge",bridge_holes,(3,33))):
        match,chosen,probes=_through_hole_match(parts[label],holes,attach["hole_centers_xy_mm"],attach["hole_diameter_mm"],span,itol)
        _check(report,f"{label}_attachment_interface",match,chosen,attach["hole_centers_xy_mm"])
        report["checks"][-1]["interior_probes"]=probes
    _check(report,"hole_feature_count",len(tray_holes)==8 and len(bridge_holes)==8,[len(tray_holes),len(bridge_holes)],[8,8])

    # The declared rail faces cover the battery length and rail height. Mounted
    # crossbar extents are deliberately excluded from this cavity measurement.
    rail_ranges={0:(-50,50),2:(3,10)}
    left_inside=_covering_plane(tray,1,rail_ranges,1,-1)
    right_inside=_covering_plane(tray,1,rail_ranges,-1,1)
    ly,ry=_coord(left_inside,1),_coord(right_inside,1)
    if ly>=ry:
        raise ValueError("inside rail planes have inverted ordering")
    outside=[f for f in _plane_faces(tray,1) if all(tuple(f.bounding_box().min)[i]<=a+tol and tuple(f.bounding_box().max)[i]>=b-tol for i,(a,b) in rail_ranges.items())]
    left_outer=[f for f in outside if _coord(f,1)<ly-tol]
    right_outer=[f for f in outside if _coord(f,1)>ry+tol]
    if len(left_outer)!=1 or len(right_outer)!=1:
        raise ValueError("expected unambiguous outside rail planes")
    battery=req["battery"]
    battery_size=battery["envelopes_mm"][battery["selected"]]
    by=battery["seated_center_mm"][1]
    gaps=[by-battery_size[1]/2-ly,ry-(by+battery_size[1]/2)]
    measurements["cavity_width_mm"]=ry-ly
    measurements["cavity_inside_y_mm"]=[ly,ry]
    measurements["lateral_clearance_mm"]=gaps
    lo,hi=battery["lateral_clearance_per_side_mm"]
    _check(report,"battery_lateral_fit",all(lo-tol<=v<=hi+tol for v in gaps),gaps,[lo,hi])

    floor_bottom=_covering_plane(tray,2,{0:(-40,40),1:(-10,10)},-1)
    floor_top=_covering_plane(tray,2,{0:(-40,40),1:(-10,10)},1)
    rear_inside=_covering_plane(tray,0,{1:(-10,10),2:(3,10)},-1)
    rear_outside=_covering_plane(tray,0,{1:(-10,10),2:(3,10)},1)
    deck_bottom=_covering_plane(bridge,2,{0:(-10,10),1:(-10,10)},-1)
    deck_top=_covering_plane(bridge,2,{0:(-10,10),1:(-10,10)},1)
    walls={"tray_floor":_coord(floor_top,2)-_coord(floor_bottom,2),
           "tray_left_rail":ly-_coord(left_outer[0],1),
           "tray_right_rail":_coord(right_outer[0],1)-ry,
           "tray_backstop":_coord(rear_outside,0)-_coord(rear_inside,0),
           "bridge_deck":_coord(deck_top,2)-_coord(deck_bottom,2)}
    measurements["wall_thickness_mm"]=walls
    measurements["wall_planes_mm"]={"tray_floor_z":[_coord(floor_bottom,2),_coord(floor_top,2)],
        "tray_left_y":[_coord(left_outer[0],1),ly],"tray_right_y":[ry,_coord(right_outer[0],1)],
        "tray_backstop_x":[_coord(rear_inside,0),_coord(rear_outside,0)],
        "bridge_deck_z":[_coord(deck_bottom,2),_coord(deck_top,2)]}
    wall_min=req["manufacturing"]["minimum_declared_planar_wall_mm"]
    _check(report,"declared_planar_walls",all(v>=wall_min-tol for v in walls.values()),walls,{"minimum_mm":wall_min,"scope":"five declared planar walls only"})

    battery_shape=_box(battery_size,battery["seated_center_mm"])
    board_shape=_box(board["envelope_mm"],board["center_mm"])
    keepout=_box(board["component_keepout_mm"],board["component_keepout_center_mm"])
    delta=battery["insertion_translation_mm"]
    if delta[1:]!=[0,0] or delta[0]>=0:
        raise ValueError("only straight +X battery insertion is supported")
    sweep_size=[battery_size[0]+abs(delta[0]),battery_size[1],battery_size[2]]
    sweep_center=[battery["seated_center_mm"][0]+delta[0]/2,*battery["seated_center_mm"][1:]]
    sweep=_box(sweep_size,sweep_center)
    overlaps={"assembly":_overlap(tray,bridge)}
    for label,reference in (("battery",battery_shape),("insertion",sweep),("board",board_shape),("board_keepout",keepout)):
        overlaps[label]=sum(_overlap(p,reference) for p in parts.values())
    measurements["overlap_mm3"]=overlaps
    for label,value in overlaps.items():
        _check(report,f"{label}_interference",value<=vtol,value,{"maximum_mm3":vtol})
    contacts={"battery":_contact(tray,battery_shape,battery["support_z_mm"]),
              "board":_contact(bridge,board_shape,board["support_z_mm"]),
              "bridge":_contact(tray,bridge,attach["tray_contact_z_mm"])}
    measurements["contact_area_mm2"]=contacts
    minimums={"battery":battery["required_support_area_mm2"],"board":board["required_support_area_mm2"],"bridge":attach["minimum_total_contact_area_mm2"]}
    for label,value in contacts.items():
        _check(report,f"{label}_support",value>=minimums[label]-tol,value,{"minimum_area_mm2":minimums[label]})
    # The declared subsystem envelope includes reference component/keep-out space.
    refs=[*parts.values(),battery_shape,board_shape,keepout]
    occupied=[max(tuple(p.bounding_box().max)[i] for p in refs)-min(tuple(p.bounding_box().min)[i] for p in refs) for i in range(3)]
    measurements["occupied_envelope_mm"]=occupied
    _check(report,"occupied_envelope",all(a<=b+tol for a,b in zip(occupied,req["envelope_max_mm"])),occupied,req["envelope_max_mm"])
    density=req["manufacturing"]["density_g_per_cm3"]
    if density<=0:
        raise ValueError("density must be positive")
    mass=sum(p.volume for p in parts.values())*density/1000
    measurements["polymer_mass_g"]=mass
    _check(report,"polymer_mass",mass<=req["manufacturing"]["polymer_mass_max_g"],mass,{"maximum_g":req["manufacturing"]["polymer_mass_max_g"],"basis":"solid uniform density; purchased items excluded"})
    return _finish(report)


def _dot(a, b):
    return sum(x*y for x, y in zip(a, b))


def _sub(a, b):
    return tuple(x-y for x, y in zip(a, b))


def _cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def _point_triangle_distance_squared(point, triangle):
    """Exact point-to-triangle distance, including edges and degenerate faces."""
    a, b, c = triangle
    ab, ac, ap = _sub(b, a), _sub(c, a), _sub(point, a)
    normal = _cross(ab, ac)
    normal_squared = _dot(normal, normal)
    if normal_squared and all(_dot(_cross(_sub(end, start), _sub(point, start)), normal) >= 0
                              for start, end in ((a, b), (b, c), (c, a))):
        return _dot(ap, normal)**2 / normal_squared
    best = math.inf
    for start, end in ((a, b), (b, c), (c, a)):
        edge, delta = _sub(end, start), _sub(point, start)
        length_squared = _dot(edge, edge)
        t = min(1., max(0., _dot(delta, edge)/length_squared)) if length_squared else 0.
        best = min(best, sum((delta[i]-t*edge[i])**2 for i in range(3)))
    return best


def _triangle_tree(triangles):
    """AABB hierarchy keeps reverse readback local, without extra dependencies."""
    low = tuple(min(point[i] for triangle in triangles for point in triangle) for i in range(3))
    high = tuple(max(point[i] for triangle in triangles for point in triangle) for i in range(3))
    if len(triangles) <= 12:
        return low, high, triangles, ()
    axis = max(range(3), key=lambda i: high[i]-low[i])
    ordered = sorted(triangles, key=lambda triangle: sum(point[axis] for point in triangle))
    middle = len(ordered)//2
    return low, high, (), (_triangle_tree(ordered[:middle]), _triangle_tree(ordered[middle:]))


def _mesh_covers(point, tree, tolerance_squared):
    low, high, triangles, children = tree
    if sum(max(low[i]-point[i], 0., point[i]-high[i])**2 for i in range(3)) > tolerance_squared:
        return False
    if children:
        return any(_mesh_covers(point, child, tolerance_squared) for child in children)
    return any(_point_triangle_distance_squared(point, triangle) <= tolerance_squared for triangle in triangles)


def _candidate_triangle_distances_squared(points, triangles):
    """Exact distances for points[N,3] and candidate triangles[N,K,3,3].

    Interior projections use plane distance; otherwise the closest point lies
    on one of the three closed edges. Degenerate triangles use those edges too.
    This batches the same geometric calculation as the scalar fallback.
    """
    import numpy as np
    a, b, c = (triangles[:, :, index, :] for index in range(3))
    points = points[:, None, :]
    normal = np.cross(b-a, c-a)
    normal_squared = np.sum(normal*normal, axis=-1)
    inside = normal_squared > 0
    best = np.full(normal_squared.shape, np.inf)
    for start, end in ((a, b), (b, c), (c, a)):
        edge, delta = end-start, points-start
        inside &= np.sum(np.cross(edge, delta)*normal, axis=-1) >= 0
        length_squared = np.sum(edge*edge, axis=-1)
        parameter = np.zeros(length_squared.shape)
        np.divide(np.sum(delta*edge, axis=-1), length_squared,
                  out=parameter, where=length_squared > 0)
        parameter = np.clip(parameter, 0., 1.)
        offset = delta-parameter[:, :, None]*edge
        best = np.minimum(best, np.sum(offset*offset, axis=-1))
    plane_distance = np.full(normal_squared.shape, np.inf)
    projection = np.sum((points-a)*normal, axis=-1)
    np.divide(projection*projection, normal_squared, out=plane_distance, where=inside)
    return np.minimum(best, plane_distance)


def _nearby_triangle_witnesses(points, triangles, tolerance):
    """Positive-only fast path: a close candidate proves surface coverage.

    A nearest centroid need not belong to the nearest triangle. Failure to find
    a witness among these candidates therefore never rejects a point: callers
    must search the complete mesh. Batches bound candidate-array memory.
    """
    import numpy as np
    from scipy.spatial import cKDTree
    points = np.asarray(points, dtype=float)
    triangles = np.asarray(triangles, dtype=float)
    centers = cKDTree(np.mean(triangles, axis=1))
    ranks = list(range(1, min(8, len(triangles))+1))
    covered = np.zeros(len(points), dtype=bool)
    for start in range(0, len(points), 1024):
        batch = points[start:start+1024]
        _, candidates = centers.query(batch, k=ranks)
        distances = _candidate_triangle_distances_squared(batch, triangles[candidates])
        covered[start:start+len(batch)] = np.any(distances <= tolerance*tolerance, axis=1)
    return covered


def _reverse_mesh_coverage(reference_part, triangles, linear_tolerance):
    """Sample every reference face, so an absent mesh feature cannot disappear.

    This is bounded surface sampling, not an exact Hausdorff certificate. The
    independent reference tessellation is four times finer than the allowance;
    its deflection is added explicitly when comparing to the delivered mesh.
    Complete empty bores additionally get the triangle-intersection check below.
    """
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    import numpy as np
    from scipy.spatial import cKDTree
    deflection = linear_tolerance/4
    # build123d.mesh uses relative deflection. Establish an absolute mm mesh
    # first; tessellate then reads that triangulation without rescaling it.
    BRepMesh_IncrementalMesh(reference_part.wrapped, deflection, False, .1, True)
    vertices, indices = reference_part.tessellate(deflection)
    if not indices or len(indices) > 1_000_000:
        raise ValueError("reference tessellation is empty or exceeds the triangle limit")
    points = {tuple(point) for point in vertices}
    points.update(tuple(sum(tuple(vertices[index])[axis] for index in triangle)/3 for axis in range(3))
                  for triangle in indices)
    points = sorted(points)
    tolerance = linear_tolerance + deflection + 1e-5
    # Most independently tessellated curved-face samples already coincide with
    # a delivered vertex or centroid. These are valid surface witnesses, so a
    # batched nearest-point fast path avoids repeated triangle traversal. SciPy
    # is already pinned in the CAD runtime; no additional package is needed.
    witnesses = {point for triangle in triangles for point in triangle}
    witnesses.update(tuple(sum(point[axis] for point in triangle)/3 for axis in range(3))
                     for triangle in triangles)
    distances, _ = cKDTree(list(witnesses)).query(points, distance_upper_bound=tolerance)
    covered = np.isfinite(distances)
    unmatched = np.flatnonzero(~covered)
    if len(unmatched):
        covered[unmatched] = _nearby_triangle_witnesses([points[index] for index in unmatched], triangles, tolerance)
    tree = None
    result = {"checked_points": 0, "total_points": len(points), "uncovered_point_mm": None,
              "reference_deflection_mm": deflection, "maximum_distance_mm": tolerance,
              "candidate_triangle_queries": len(unmatched), "triangle_queries": 0}
    for point, has_witness in zip(points, covered):
        result["checked_points"] += 1
        if has_witness:
            continue
        if tree is None:
            tree = _triangle_tree(triangles)
        result["triangle_queries"] += 1
        if not _mesh_covers(point, tree, tolerance*tolerance):
            result["uncovered_point_mm"] = list(point)
            break
    return result


def _clip_axial(polygon, coordinate, keep_above):
    """Clip a triangle/polygon to one closed axial half-space."""
    clipped = []
    for start, end in zip(polygon, polygon[1:]+polygon[:1]):
        a, b = start[2]-coordinate, end[2]-coordinate
        inside_a = a >= 0 if keep_above else a <= 0
        inside_b = b >= 0 if keep_above else b <= 0
        if inside_a:
            clipped.append(start)
        if inside_a != inside_b:
            t = a/(a-b)
            clipped.append(tuple(start[i]+t*(end[i]-start[i]) for i in range(3)))
    return clipped


def _radial_distance_squared(polygon):
    """Minimum distance from a convex polygon's XY projection to the axis.

    Projected vertices alone are insufficient: a cap can surround the axis,
    and a narrow off-axis web can cross the cylinder between its vertices.
    """
    crosses = []
    best = math.inf
    for a, b in zip(polygon, polygon[1:]+polygon[:1]):
        crosses.append(a[0]*b[1]-a[1]*b[0])
        dx, dy = b[0]-a[0], b[1]-a[1]
        length_squared = dx*dx+dy*dy
        t = min(1., max(0., -(a[0]*dx+a[1]*dy)/length_squared)) if length_squared else 0.
        best = min(best, (a[0]+t*dx)**2+(a[1]+t*dy)**2)
    if abs(sum(crosses)) > 1e-16 and (all(value >= 0 for value in crosses) or all(value <= 0 for value in crosses)):
        return 0.
    return best


def _mesh_bore_coverage(reference_part, triangles, linear_tolerance):
    """Reject any triangle entering a proven-empty reference bore interior.

    Axial clipping tests the whole triangle, including arbitrarily thin caps
    and off-axis webs, rather than selected rays or heights. The radial inset
    permits chordal tessellation of the cylindrical wall. Extending the span
    catches end caps just outside the reference surface within export tolerance.
    A BRep volume probe must first prove that entire inset cylinder is empty;
    blind pockets or other occupied spans are left to surface consistency.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder
    bd = _kernel()
    result = {"checked_bores": 0, "skipped_nonempty_spans": 0, "intruding_triangles": 0,
              "first_intrusion": None}
    for face in reference_part.faces():
        surface = BRepAdaptor_Surface(face.wrapped, True)
        if surface.GetType() != GeomAbs_Cylinder or abs(surface.LastUParameter()-surface.FirstUParameter()-2*math.pi) > 1e-5:
            continue
        cylinder = surface.Cylinder()
        location = cylinder.Location()
        direction = cylinder.Axis().Direction()
        origin = (location.X(), location.Y(), location.Z())
        axis = (direction.X(), direction.Y(), direction.Z())
        radial = _sub(tuple(face.position_at(.5, .5)), origin)
        radial = _sub(radial, tuple(value*_dot(radial, axis) for value in axis))
        if _dot(tuple(face.normal_at(.5, .5)), radial) >= 0:
            continue
        radius = cylinder.Radius()
        inset = min(linear_tolerance+1e-5, radius/4)
        bottom = surface.FirstVParameter()-linear_tolerance
        top = surface.LastVParameter()+linear_tolerance
        center = tuple(origin[i]+axis[i]*(bottom+top)/2 for i in range(3))
        probe = bd.Plane(origin=center, z_dir=axis) * bd.Cylinder(radius-inset, top-bottom)
        overlap = _overlap(reference_part, probe)
        if not math.isfinite(overlap) or overlap < 0:
            raise ValueError("invalid reference bore overlap")
        if overlap > min(1e-5, probe.volume*1e-6):
            result["skipped_nonempty_spans"] += 1
            continue
        result["checked_bores"] += 1
        direction_x = cylinder.Position().XDirection()
        direction_y = cylinder.Position().YDirection()
        x_axis = (direction_x.X(), direction_x.Y(), direction_x.Z())
        y_axis = (direction_y.X(), direction_y.Y(), direction_y.Z())
        for index, triangle in enumerate(triangles):
            local = []
            for point in triangle:
                delta = _sub(point, origin)
                local.append((_dot(delta, x_axis), _dot(delta, y_axis), _dot(delta, axis)))
            if min(p[2] for p in local) > top or max(p[2] for p in local) < bottom:
                continue
            polygon = _clip_axial(_clip_axial(local, bottom, True), top, False)
            if polygon and _radial_distance_squared(polygon) < (radius-inset)**2:
                result["intruding_triangles"] += 1
                result["first_intrusion"] = {"triangle_index": index, "axis_origin_mm": list(origin),
                    "axis_direction": list(axis), "axial_span_mm": [bottom, top], "radial_inset_mm": inset}
                return result
    return result


def verify_print(stl_path, reference_part, linear_tolerance=.02, volume_tolerance=.005):
    """Read an exact binary STL; topology, orientation and BRep consistency checks."""
    report=_report()
    try:
        data=Path(stl_path).read_bytes()
        if len(data)<84:
            raise ValueError("binary STL header is incomplete")
        count=struct.unpack_from("<I",data,80)[0]
        if not count or len(data)!=84+50*count or count>1_000_000:
            raise ValueError("binary STL triangle count/size mismatch")
        vertices=[]
        triangles=[]
        centroids=[]
        edges=defaultdict(list)
        neighbors=defaultdict(set)
        volume=0.
        degenerate=0
        # 1e-5 mm quantization tolerates float32 duplicate rounding, far below
        # the qualified export tolerance. STL itself contains no unit metadata.
        def key(p):
            return tuple(round(v,5) for v in p)
        for i in range(count):
            values=struct.unpack_from("<12fH",data,84+i*50)
            if not all(math.isfinite(v) for v in values[:12]):
                raise ValueError("nonfinite STL coordinate or normal")
            a,b,c=(tuple(values[j:j+3]) for j in (3,6,9))
            vertices.extend((a,b,c))
            triangles.append((a,b,c))
            centroids.append(tuple((a[j]+b[j]+c[j])/3 for j in range(3)))
            ab=[b[j]-a[j] for j in range(3)];ac=[c[j]-a[j] for j in range(3)]
            cross=[ab[1]*ac[2]-ab[2]*ac[1],ab[2]*ac[0]-ab[0]*ac[2],ab[0]*ac[1]-ab[1]*ac[0]]
            if sum(v*v for v in cross)<1e-16:
                degenerate+=1
            volume+=(a[0]*(b[1]*c[2]-b[2]*c[1])-a[1]*(b[0]*c[2]-b[2]*c[0])+a[2]*(b[0]*c[1]-b[1]*c[0]))/6
            for p,q in ((key(a),key(b)),(key(b),key(c)),(key(c),key(a))):
                edge=tuple(sorted((p,q)))
                edges[edge].append((i,1 if p<q else -1))
        bad_edges=0
        for uses in edges.values():
            if len(uses)!=2 or sum(sign for _,sign in uses)!=0:
                bad_edges+=1
            else:
                a,b=uses[0][0],uses[1][0]
                neighbors[a].add(b);neighbors[b].add(a)
        reached=set();pending=[0]
        while pending:
            i=pending.pop()
            if i not in reached:
                reached.add(i);pending.extend(neighbors[i]-reached)
        minimum=[min(p[i] for p in vertices) for i in range(3)]
        maximum=[max(p[i] for p in vertices) for i in range(3)]
        reference=reference_part.bounding_box()
        bounds_error=max(abs(a-b) for a,b in zip([*minimum,*maximum],[*reference.min,*reference.max]))
        volume_error=abs(volume-reference_part.volume)/reference_part.volume
        _check(report,"mesh_topology",not bad_edges and not degenerate and len(reached)==count and volume>0,
               {"triangles":count,"bad_edges":bad_edges,"degenerate_triangles":degenerate,"connected_triangles":len(reached),"signed_volume_mm3":volume},"one closed consistently oriented nondegenerate positive-volume shell")
        _check(report,"mesh_bounds",bounds_error<=linear_tolerance,bounds_error,{"maximum_error_mm":linear_tolerance})
        _check(report,"mesh_volume",volume_error<=volume_tolerance,volume_error,{"maximum_relative_error":volume_tolerance})
        # Bounds/volume alone miss a shifted hole. Every mesh vertex and
        # triangle centroid must lie on the selected BRep boundary within the
        # declared tessellation tolerance. Use the shell, not filled solid.
        bd=_kernel()
        shells=reference_part.shells()
        if len(shells)!=1:
            raise ValueError("one reference boundary shell required")
        reverse = _reverse_mesh_coverage(reference_part, triangles, linear_tolerance)
        bores = _mesh_bore_coverage(reference_part, triangles, linear_tolerance)
        covered = reverse["uncovered_point_mm"] is None and not bores["intruding_triangles"]
        surface_error=None
        checked=0
        points=set(vertices)
        points.update(centroids)
        # A known coverage failure needs no further BRep point-distance calls.
        # Preserve that distinction in the receipt instead of reporting zero.
        if covered:
            surface_error=0.
            for point in points:
                surface_error=max(surface_error,shells[0].distance_to(bd.Vertex(*point)))
                checked+=1
                if surface_error>linear_tolerance+1e-5:
                    break
        _check(report,"mesh_surface",covered and surface_error<=linear_tolerance+1e-5,
               {"maximum_observed_distance_mm":surface_error,"checked_points":checked,"total_points":len(points),
                "forward_skipped_after_coverage_failure":not covered,
                "reverse_coverage":reverse,"bore_coverage":bores},
               {"maximum_distance_mm":linear_tolerance,
                "sampling":"bidirectional vertices and triangle centroids; each direction stops at first failure",
                "bores":"no triangle may enter a proven-empty inset cylinder; closed axial clipping includes end caps"})
        report["measurements"]={"triangles":count,"volume_mm3":volume,"bounds_min_mm":minimum,"bounds_max_mm":maximum,"units":"mm by declared export contract; STL has no unit field"}
        return _finish(report)
    except Exception as exc:
        return _unknown(report,"mesh_readback",exc)


def verify_prints(step_path, print_paths, part_step_paths=None):
    report=_report()
    try:
        parts=read_parts(step_path)
        if set(print_paths)!={"tray","bridge"}:
            raise ValueError("exactly tray and bridge print paths required")
        for label,path in print_paths.items():
            result=verify_print(path,parts[label])
            for item in result["checks"]:
                report["checks"].append({**item,"id":f"{label}_{item['id']}"})
            report["measurements"][label]=result["measurements"]
            if part_step_paths is not None:
                exported=list(world_parts(_kernel().import_step(str(Path(part_step_paths[label]).resolve(strict=True)))))
                if len(exported)!=1 or exported[0].label!=label:
                    raise ValueError(f"expected one {label} in printable STEP")
                item=exported[0]
                valid=item.is_valid and len(item.solids())==1 and item.volume>0
                difference=max(0.,item.volume+parts[label].volume-2*_overlap(item,parts[label])) if valid else None
                _check(report,f"{label}_step_consistency",valid and difference<=.001,
                       {"valid":valid,"symmetric_difference_mm3":difference},
                       {"maximum_symmetric_difference_mm3":.001})
        return _finish(report)
    except Exception as exc:
        return _unknown(report,"print_readback",exc)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step")
    parser.add_argument("requirements")
    parser.add_argument("--out",required=True)
    parser.add_argument("--print-dir","--prints-dir",dest="prints_dir")
    args=parser.parse_args()
    try:
        requirements=json.loads(Path(args.requirements).read_text())
        report=verify(args.step,requirements)
        if args.prints_dir:
            root=Path(args.prints_dir)
            prints=verify_prints(args.step,{name:root/f"{name}.stl" for name in ("tray","bridge")},
                                {name:root/f"{name}.step" for name in ("tray","bridge")})
            report["print_checks"]=prints
            if prints["status"]!="pass":
                report["status"]=prints["status"]
        destination=Path(args.out)
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_text(json.dumps(report,indent=2,allow_nan=False)+"\n")
        print(json.dumps({"status":report["status"],"checks":len(report["checks"])}))
        return {"pass":0,"fail":1,"unknown":2}[report["status"]]
    except Exception as exc:
        print(f"geometry verification unavailable: {exc}",file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
