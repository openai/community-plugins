"""Measure the exported ECAD/mechanical assembly in installed coordinates."""
import argparse
import hashlib
import json
from pathlib import Path

import build123d as bd
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Cylinder


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def bbox(shape):
    box = shape.bounding_box()
    return [[round(v, 5) for v in box.min], [round(v, 5) for v in box.max]]


def vertical_holes(shape, diameter, wanted, tolerance):
    result = {}
    for face in shape.faces():
        surface = BRepAdaptor_Surface(face.wrapped)
        if surface.GetType() != GeomAbs_Cylinder:
            continue
        cylinder = surface.Cylinder()
        center = cylinder.Location()
        x, y = center.X(), center.Y()
        if abs(cylinder.Radius()*2 - diameter) > tolerance or abs(cylinder.Axis().Direction().Z()) < .9999:
            continue
        if min(((x-a)**2+(y-b)**2)**.5 for a,b in wanted) > tolerance:
            continue
        z = face.bounding_box()
        key = round(x, 5), round(y, 5)
        record = [round(z.min.Z, 5), round(z.max.Z, 5)]
        if key in result:
            record = [min(record[0],result[key][0]), max(record[1],result[key][1])]
        result[key] = record
    return [{'xy':list(key),'z':result[key]} for key in sorted(result)]


def traverse(shape):
    yield shape
    for child in shape.children:
        yield from traverse(child)


def cross_section(base, x, y, z):
    """Locate both material edges along each centerline at a fixed wall section."""
    if base.is_inside((x,y,z)):
        return None
    def edge(dx, dz):
        lo, hi = 0., 12.
        # The Z section must stop before reaching open air over the wall.
        if dz > 0: hi = 14.8 - z
        if dz < 0: hi = z - .4
        assert base.is_inside((x+hi*dx,y,z+hi*dz)), (x,y,z,dx,dz,hi)
        for _ in range(28):
            mid = (lo+hi)/2
            if base.is_inside((x+mid*dx,y,z+mid*dz)): hi=mid
            else: lo=mid
        return (hi+lo)/2
    return {
        'width_mm':round(edge(1,0)+edge(-1,0),4),
        'height_mm':round(edge(0,1)+edge(0,-1),4),
        'bottom_z_mm':round(z-edge(0,-1),4),
        'top_z_mm':round(z+edge(0,1),4)
    }


def overlap_volume(a, b):
    overlap = a.intersect(b)
    if overlap is None:
        return 0.
    if isinstance(overlap, (list,tuple)):
        return round(sum(s.volume for s in overlap),6)
    return round(overlap.volume,6)


def insertion_gauge(g, end_y):
    start_y = g['outside_y']
    return bd.Pos(g['center_x'],(start_y+end_y)/2,g['center_z']) * bd.Box(g['width'],end_y-start_y,g['height'])


def check_assembly(folder, report_path):
    drawing_path=Path(__file__).with_name('integration-drawing.json')
    d=json.loads(drawing_path.read_text());tol=d['cad_tolerance']; axes=d['board_axes']
    assembly=bd.import_step(folder/'assembly.step')
    parts={child.label:child for child in assembly.children}
    base,lid,board=parts['base'],parts['lid'],parts['sensor_board']
    pilots=vertical_holes(base,d['pilot_diameter'],axes,tol)
    holes=vertical_holes(board,d['board_hole_diameter'],axes,tol)
    connector=[n for n in traverse(board) if n.label=='J1']
    if len(connector)!=1: raise ValueError(f'Expected one registered J1 connector, found {len(connector)}')
    jb=bbox(connector[0])
    # Solder hold-downs can extend farther forward than the socket opening.
    # Measure the full-width, full-height molded body rather than the terminal tips.
    full_bodies=[s for s in connector[0].solids() if s.bounding_box().size.X>4.5 and s.bounding_box().size.Z>2.2]
    if len(full_bodies)!=1: raise ValueError('Expected one full-width JST molded body')
    molded=bbox(full_bodies[0])
    # This is an external access gauge, ahead of the first metal or plastic.
    # Preserve a second measurement at the mouth to show where the rectangular
    # proxy ceases to represent the keyed form of a real mating plug.
    gauge_end=jb[0][1]-.05
    nominal_mouth_gauge=insertion_gauge(d['service_gauge'],molded[0][1]-.05)
    gauge=insertion_gauge(d['service_gauge'],gauge_end)
    allowance=insertion_gauge(d['outside_service_allowance'],d['outside_service_allowance']['inside_y'])
    port=d['port']
    section=cross_section(base,port['center_x'],port['wall_plane_y'],port['center_z'])
    gap=board.distance_to(lid)
    # A vertical lift of a lid whose lowest Z is above the whole PCB cannot enter it.
    board_box=bbox(board); lid_box=bbox(lid)
    vertical_lift_margin=lid_box[0][2]-board_box[1][2]
    body_overlap=overlap_volume(board,base)
    screw_overlap=overlap_volume(board,parts['board_screws'])
    interference={name:overlap_volume(gauge,part) for name,part in [('base',base),('lid',lid),('board',board)]}
    xy_registered=(len(pilots)==len(holes)==len(axes) and all(
        max(abs(x-y) for x,y in zip(h['xy'],p['xy']))<=tol for h,p in zip(holes,pilots)))
    checks={
      'four_board_holes_align_to_case_pilots':xy_registered,
      'four_post_seats_meet_pcb_bottom':xy_registered and all(
          abs(p['z'][1]-d['post_seat_z'])<=tol and abs(p['z'][1]-h['z'][0])<=tol for h,p in zip(holes,pilots)),
      'board_holes_span_ecad_fr4_core':len(holes)==4 and all(abs(h['z'][1]-h['z'][0]-d['rendered_fr4_core_thickness'])<=tol for h in holes),
      'component_sits_on_ecad_stackup':abs(molded[0][2]-(d['post_seat_z']+d['stackup_component_seat_above_core_bottom']))<=tol,
      'populated_board_has_no_solid_intersection_with_case':body_overlap<=.001,
      'populated_board_has_no_solid_intersection_with_mounting_screws':screw_overlap<=.001,
      'populated_board_clears_lid':gap>=d['minimum_board_to_lid'],
      'lid_has_clear_vertical_service_path':vertical_lift_margin>=d['minimum_board_to_lid'],
      'front_opening_matches_drawing':section is not None and max(abs(section['width_mm']-port['width']),abs(section['height_mm']-port['height']))<=tol,
      'outside_service_allowance_clears_case':overlap_volume(allowance,base)<=.001,
      'gauge_reaches_header_exterior_without_collision':gauge_end>port['wall_plane_y'] and max(interference.values())<=.001,
      'header_faces_front_opening':jb[0][0] < d['service_gauge']['center_x'] < jb[1][0] and
         jb[0][2] <= d['service_gauge']['center_z'] <= jb[1][2],
    }
    measured={'pilot_axes_and_depths_mm':pilots,'pcb_bores_mm':holes,'board_bbox_mm':board_box,'connector_bbox_mm':jb,'connector_molding_bbox_mm':molded,
      'board_base_intersection_mm3':body_overlap,'board_screw_intersection_mm3':screw_overlap,
      'lid_clearance_mm':round(gap,5),'minimum_vertical_lift_margin_mm':round(vertical_lift_margin,5),
      'wall_opening':section,'gauge_contact_end_y_mm':round(gauge_end,5),'molding_face_y_mm':molded[0][1],'rectangular_gauge_near_mouth_board_overlap_mm3':overlap_volume(nominal_mouth_gauge,board),'gauge_overlap_mm3':interference,'outside_service_overlap_mm3':overlap_volume(allowance,base)}
    report={'status':'pass' if all(checks.values()) else 'fail','drawing_sha256':file_hash(drawing_path),
            'assembly_step_sha256':file_hash(folder/'assembly.step'),'checks':checks,'measured':measured}
    report_path.write_text(json.dumps(report,indent=2)+'\n')
    if report['status']!='pass':raise ValueError('Board/housing export failed '+str([k for k,v in checks.items() if not v])+' '+str(measured))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('handoff',type=Path);parser.add_argument('report',type=Path)
    a=parser.parse_args();print(json.dumps(check_assembly(a.handoff,a.report)['measured'],indent=2))
