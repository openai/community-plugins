"""Measure the four duct rims on an exported frame, independently of the generator."""
import argparse
import json
from pathlib import Path

import build123d as bd
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Cylinder
from OCP.TopAbs import TopAbs_REVERSED


def measure(frame_file):
    frame = bd.import_step(frame_file)
    rims = {}
    for face in frame.faces():
        surface = BRepAdaptor_Surface(face.wrapped)
        if surface.GetType() != GeomAbs_Cylinder:
            continue
        cylinder = surface.Cylinder()
        radius = cylinder.Radius()
        if not 15 < radius < 20 or abs(cylinder.Axis().Direction().Z()) < 0.99999:
            continue
        x, y = (round(v, 5) for v in cylinder.Location().Coord()[:2])
        side = 'inner' if face.wrapped.Orientation() == TopAbs_REVERSED else 'outer'
        rims.setdefault((x, y), {}).setdefault(side, set()).add(round(radius, 5))
    if len(rims) != 4 or any(set(sides) != {'inner', 'outer'} or any(len(values) != 1 for values in sides.values()) for sides in rims.values()):
        raise ValueError('Expected four distinct concentric inner and outer vertical duct surfaces')
    rings = []
    for (x, y), sides in sorted(rims.items()):
        inner, outer = next(iter(sides['inner'])), next(iter(sides['outer']))
        rings.append({'axis_xy_mm':[x, y], 'inner_radius_mm':inner, 'outer_radius_mm':outer, 'radial_wall_mm':round(outer-inner, 5)})
    box=frame.bounding_box()
    return {'rings':rings,'frame_xy_span_mm':[round(box.size.X,5),round(box.size.Y,5)],'valid_continuous_frame':len(frame.solids())==1 and frame.is_valid}


def inspect(frame_file, report_file):
    actual=measure(frame_file)
    accepted=actual['valid_continuous_frame'] and all(abs(rim['radial_wall_mm']-1.40)<=0.01 and abs(rim['inner_radius_mm']-16.30)<=0.01 for rim in actual['rings'])
    report={'method':'STEP cylindrical faces, grouped by measured shaft-center XY','requirements':{'radial_wall_mm':1.40,'inner_radius_mm':16.30,'cad_tolerance_mm':0.01},'actual':actual,'status':'pass' if accepted else 'fail'}
    report_file.write_text(json.dumps(report,indent=2)+'\n')
    return accepted


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('frame',type=Path)
    parser.add_argument('report',type=Path)
    args=parser.parse_args()
    raise SystemExit(0 if inspect(args.frame,args.report) else 1)
