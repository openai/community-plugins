"""Measure the installed Air 5IN1 substrate and mounting envelope directly from STEP."""
import argparse
import hashlib
import json
from pathlib import Path
import build123d as bd

def read_report(assembly_file, component_file):
    def index(shape):
        nodes = {shape.label: shape}
        for child in shape.children:
            nodes.update(index(child))
        return nodes

    def span(shape):
        b = shape.bounding_box()
        return [round(b.min.Z, 5), round(b.max.Z, 5)]

    def compare(name, actual, target, tolerance=.005):
        return {'id':name, 'actual_mm':actual,'target_mm':target,'tolerance_mm':tolerance,
                'status':'pass' if len(actual)==len(target) and all(abs(a-b)<=tolerance for a,b in zip(actual,target)) else 'fail'}

    aircraft=index(bd.import_step(assembly_file))
    component=index(bd.import_step(component_file))
    checks=[]
    for label, tree in [('installed',aircraft),('component',component)]:
        lo,hi=span(tree['pcb'])
        checks.append(compare(label+'-substrate-datums',[lo,hi],[3.50,4.30]))
        checks.append(compare(label+'-substrate-thickness',[round(hi-lo,5)],[.80]))
    fc=aircraft['flight_controller']
    top_parts=[s for s in fc.children if s.label.startswith('IC_') or s.label in
               ('surface_mount_passives','camera_input_socket','VTX_antenna_socket','RX_antenna_pad','BATT_plus_pad','BATT_minus_pad')]
    assert len(top_parts)==10, [s.label for s in top_parts]
    checks.append(compare('top-components-seated',[span(s)[0] for s in top_parts],[4.30]*len(top_parts)))
    checks.append(compare('underside-components-seated',
               [span(aircraft[k])[1] for k in ('underside_programming_socket','underside_motor_phase_pads')],[3.50,3.50]))
    grommets=[s for s in aircraft['stack_hardware'].children if s.label.startswith('FC_grommet_')]
    seat=[]
    for part in grommets:
        barrel=[s for s in part.faces() if s.geom_type==bd.GeomType.CYLINDER and abs(s.radius-1.46)<.001]
        if len(barrel)==1:seat.extend(span(barrel[0]))
    checks.append(compare('modelled-compressed-grommet-bands',seat,[3.50,4.30]*4))
    return {'source_sha256':{q.name:hashlib.sha256(q.read_bytes()).hexdigest() for q in (assembly_file,component_file)},
            'status':'pass' if all(c['status']=='pass' for c in checks) else 'fail','checks':checks}

def verify(assembly, component, report):
    result=read_report(assembly,component)
    report.write_text(json.dumps(result,indent=2)+'\n')
    return result['status']=='pass'

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for arg in ('assembly','component','report'):p.add_argument(arg,type=Path)
    args=p.parse_args()
    raise SystemExit(0 if verify(args.assembly,args.component,args.report) else 1)
