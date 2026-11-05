#!/usr/bin/env python3
"""A bounded, resumable engineering study. No CAD import until the pinned runtime runs."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

PACKAGE = Path(__file__).resolve().parents[1]
FIXTURE = PACKAGE / 'fixtures' / 'drone-module'
SCHEMA = 1


class Refused(ValueError):
    pass


def stamp():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, data):
    path = Path(path)
    if path.is_symlink():
        raise Refused(f'Refusing symlink: {path.name}')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def relative(root, name):
    """Resolve manifest paths without accepting absolute paths, '..', or symlinks."""
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        raise Refused('Expected a nonempty relative artifact path')
    if any(p in ('..', '.') for p in name.split('/')) or '\\' in name:
        raise Refused('Artifact path escape refused')
    root = Path(root).resolve()
    target = root / name
    for component in [target, *target.parents]:
        if component == root:
            break
        if component.is_symlink():
            raise Refused('Artifact symlink refused')
    if not target.resolve().is_relative_to(root):
        raise Refused('Artifact path escape refused')
    return target


def hashes(root, exclude=()):
    root = Path(root)
    if root.is_symlink():
        raise Refused('Artifact directory may not be a symlink')
    result = {}
    for path in sorted(root.rglob('*')):
        name = path.relative_to(root).as_posix()
        if '__pycache__' in path.parts or name in exclude:
            continue
        if path.is_symlink():
            raise Refused(f'Symlink in artifact tree: {name}')
        if path.is_file():
            result[name] = digest(path)
    return result


def verify_hashes(root, expected, exact=False, exclude=()):
    if not isinstance(expected, dict) or not expected:
        raise Refused('Missing artifact hashes')
    for name, wanted in expected.items():
        path = relative(root, name)
        if not re.fullmatch(r'[0-9a-f]{64}', str(wanted)) or not path.is_file() or digest(path) != wanted:
            raise Refused(f'Changed or missing artifact: {name}')
    if exact and hashes(root, exclude) != expected:
        raise Refused('Artifact file set changed')


def package_hashes():
    result = {}
    for folder in ('scripts', 'fixtures'):
        result.update({f'{folder}/{name}': value for name, value in hashes(PACKAGE / folder).items()})
    return result


def study_root(value):
    p = Path(value).expanduser().absolute()
    if p.is_symlink():
        raise Refused('Study directory may not be a symlink')
    return p.resolve()


def load_study(root):
    state = read_json(relative(root, 'study.json'))
    if state.get('schema') != SCHEMA or state.get('fixture') != 'drone-module':
        raise Refused('Unsupported study schema/fixture')
    if state.get('package_hashes') != package_hashes():
        raise Refused('Installed checker, fixture or runtime scripts changed since study creation; requalify in a new study')
    if not state.get('requirements_history'):
        raise Refused('Study has no requirement history')
    fixture = read_json(FIXTURE / 'requirements.json')
    previous_number = None
    for record in state['requirements_history']:
        ident = record.get('id', '')
        if not re.fullmatch(r'R[0-9]{3,}', ident):
            raise Refused('Invalid requirement revision identity')
        number = int(ident[1:])
        if number < 1 or (previous_number is not None and number != previous_number + 1):
            raise Refused('Requirement revision history is not sequential')
        previous_number = number
        actual = record['requirements']
        expected = deepcopy(fixture)
        battery = actual['battery']['selected']
        pitch = actual['frame']['pitch_mm'][0]
        if battery not in ('A', 'B') or pitch not in (100, 110):
            raise Refused('Unsupported requirement variant')
        expected['battery']['selected'] = battery
        expected['frame']['pitch_mm'][0] = pitch
        if actual != expected or not str(record.get('reason', '')).strip():
            raise Refused('Frozen requirements were edited outside an allowed revision')
    if state['active_requirement'] != state['requirements_history'][-1]['id']:
        raise Refused('Active requirement must be the latest preserved revision')
    return state


def active(state):
    matches = [r for r in state['requirements_history'] if r['id'] == state['active_requirement']]
    if len(matches) != 1:
        raise Refused('Active requirement is unresolved')
    return matches[0]


def save_study(root, state):
    write_json(root / 'study.json', state)
    req = active(state)
    context = [
        '# Resume this engineering study', '',
        '`study.json` is authoritative. This summary is generated.', '',
        f"Active requirement: **{req['id']}**; selected passing run: **{state.get('selected_run') or 'none'}**.",
        f"Latest run: **{state.get('latest_run') or 'none'}**. Failed runs remain preserved.", '',
        '## Frozen requirements', '```json', json.dumps(req['requirements'], indent=2), '```', '',
        '## Decisions', *[f"- {r['id']}: {r['reason']}" for r in state['requirements_history']], '',
        'Resume before edits. A changed requirement does not update existing geometry.',
        'Run verify to expose the mismatch, then build to revise, then handoff.',
    ]
    relative(root, 'context.md').write_text('\n'.join(context) + '\n')


@contextmanager
def writer(root):
    lock = relative(root, '.study.lock')
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise Refused('Another writer or an interrupted run owns .study.lock; inspect it before recovery') from exc
    try:
        os.write(fd, json.dumps({'pid': os.getpid(), 'started': stamp()}).encode())
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)


def initialize(root, requirements=None, revision='R001', source=None, name='Drone subsystem'):
    if root.is_symlink():
        raise Refused('Study directory may not be a symlink')
    if root.exists() and any(root.iterdir()):
        raise Refused('Start with a new or empty study directory')
    root.mkdir(parents=True, exist_ok=True)
    req = deepcopy(requirements or read_json(FIXTURE / 'requirements.json'))
    req.pop('revision', None)
    src = root / 'src'
    src.mkdir()
    if source:
        copy_tree(source, src)
    else:
        for filename in ('model.py', 'exports.py', 'drawing.py'):
            shutil.copy2(FIXTURE / filename, src / filename)
        write_json(src / 'design.json', {**req, 'revision': revision})
    state = {'schema': SCHEMA, 'fixture': 'drone-module', 'name': name, 'created': stamp(),
             'package_hashes': package_hashes(), 'active_requirement': revision,
             'requirements_history': [{'id': revision, 'requirements': req, 'reason': 'Initial public fixture' if not source else 'Reproduced from delivered source and requirements'}],
             'selected_run': None, 'latest_run': None, 'runs': []}
    save_study(root, state)
    return state


def runtime_path(value):
    if not value:
        raise Refused('Set PE_RUNTIME or pass --runtime to the dedicated Text-to-CAD environment')
    root = Path(value).expanduser().resolve()
    if not (root / 'bin' / 'cad').is_file():
        raise Refused('Runtime is missing; run setup --runtime PATH first')
    return root


def copy_tree(source, destination):
    if Path(source).is_symlink() or Path(destination).is_symlink():
        raise Refused('Artifact directory may not be a symlink')
    for name in hashes(source):
        target = relative(destination, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(relative(source, name), target)


def selected(root, state, key='selected_run'):
    run_id = state.get(key)
    if not isinstance(run_id, str) or not re.fullmatch(r'run-[0-9TZ-]+-[0-9a-f]{8}', run_id):
        raise Refused(f'No valid {key}')
    path = relative(root, f'runs/{run_id}')
    manifest = read_json(relative(path, 'manifest.json'))
    verify_hashes(path, manifest['sha256'], exact=True, exclude=('manifest.json',))
    if manifest.get('package_hashes') != state['package_hashes']:
        raise Refused('Run checker does not match study checker')
    return path, manifest


def check_source(root, run):
    if hashes(relative(root, 'src')) != hashes(relative(run, 'src')):
        raise Refused('Editable source changed after its export; build again before verify or handoff')


def run_checks(runtime, state_root, run):
    from cad_backend import run_cad
    output = relative(run, 'checks.json')
    if output.exists():
        raise Refused('Checker output already exists before fresh verification')
    args = ['run', str(PACKAGE / 'scripts' / 'verify_geometry.py'), str(run / 'exports' / 'assembly.step'),
            str(run / 'requirements.json'), '--out', str(run / 'checks.json'), '--print-dir', str(run / 'exports')]
    result = run_cad(runtime, state_root, args, run)
    if result['returncode'] not in (0, 1, 2):
        raise Refused('Geometry checker process failed: ' + str(result['returncode']))
    if not output.is_file() or output.is_symlink():
        raise Refused('Geometry checker produced no result: ' + result.get('stderr', '')[-1000:])
    checks = read_json(output)
    if {0: 'pass', 1: 'fail', 2: 'unknown'}[result['returncode']] != checks.get('status'):
        raise Refused('Checker status disagrees with its process exit')
    if checks['status'] == 'pass':
        required = {'tray_solid', 'bridge_solid', 'polymer_envelope', 'frame_interface', 'board_interface',
                    'tray_attachment_interface', 'bridge_attachment_interface', 'hole_feature_count',
                    'battery_lateral_fit', 'declared_planar_walls', 'assembly_interference',
                    'battery_interference', 'insertion_interference', 'board_interference', 'board_keepout_interference',
                    'battery_support', 'board_support', 'bridge_support', 'occupied_envelope', 'polymer_mass'}
        measured = checks.get('checks', [])
        prints = checks.get('print_checks', {})
        print_ids = {f'{part}_{check}' for part in ('tray', 'bridge') for check in
                     ('mesh_topology', 'mesh_bounds', 'mesh_volume', 'mesh_surface', 'step_consistency')}
        if ({item['id'] for item in measured} != required or
            any(item.get('status') != 'pass' for item in measured) or prints.get('status') != 'pass' or
            {item['id'] for item in prints.get('checks', [])} != print_ids or
            any(item.get('status') != 'pass' for item in prints['checks'])):
            raise Refused('Passing checker report lacks required successful measurements')
    return checks, result


def check_drawing(run, revision, checks):
    pdf = relative(run, 'exports/module-drawing.pdf')
    if not pdf.is_file() or not pdf.read_bytes().startswith(b'%PDF-'):
        raise Refused('Dimensioned PDF is missing or invalid')
    drawing = read_json(relative(run, 'exports/drawing-dimensions.json'))
    if drawing.get('revision') != revision or drawing.get('units') != 'mm':
        raise Refused('Drawing revision or units disagree with the selected geometry')
    measured = checks['measurements']
    expected = {'cavity_width_mm': measured['cavity_width_mm'], 'wall_thickness_mm': measured['wall_thickness_mm']}
    for key, value in expected.items():
        if compare_values(value, drawing.get(key)):
            raise Refused(f'Drawing {key} disagrees with geometry readback')
    for label, pitch in (('tray', 'frame_pitch_mm'), ('bridge', 'board_pitch_mm')):
        part = drawing.get('parts', {}).get(label, {})
        for key in ('bounds_mm', 'volume_mm3'):
            if compare_values(measured['parts'][label][key], part.get(key)):
                raise Refused(f'Drawing {label} {key} disagrees with geometry')
        if compare_values(measured[pitch], part.get('primary_pitch_mm')):
            raise Refused(f'Drawing {label} interface disagrees with geometry')


def report(run, revision, checks):
    lines = ['# Measured design report', '', f'Requirement revision: {revision}', f"Result: **{checks['status'].upper()}**", '',
             '| Check | Result | Measured |', '|---|---|---|']
    for item in checks.get('checks', []):
        value = json.dumps(item.get('actual', item.get('detail', '')), ensure_ascii=False)
        lines.append(f"| {item['id']} | {item['status']} | {value.replace('|', '/')} |")
    lines += ['', 'Polymer mass is a solid-material estimate using the declared density. Purchased items are separate.',
              'Declared planar walls are measured; this is not universal wall-thickness, structural, printability or flight qualification.',
              'Physical fabrication, bench measurements and a measurement-driven revision remain M2.', '']
    (run / 'report.md').write_text('\n'.join(lines))


def make_bom(run, revision, checks):
    import csv
    parts = checks.get('measurements', {}).get('parts', {})
    req = read_json(run / 'requirements.json')
    density = req['manufacturing']['density_g_per_cm3']
    with (run / 'bom.csv').open('w', newline='') as f:
        out = csv.writer(f)
        out.writerow(['revision', 'item', 'quantity', 'source', 'solid_polymer_mass_g', 'notes'])
        for label in ('tray', 'bridge'):
            volume = parts.get(label, {}).get('volume_mm3')
            out.writerow([revision, label, 1, f'exports/{label}.stl', '' if volume is None else round(volume*density/1000, 5), 'Print orientation reviewed separately'])
        for label, quantity, notes in [('M3 frame fastener set', 4, 'Length selected to suit host frame; frame not part of fixture'), ('M3 bridge fastener set', 4, 'Through 30 mm bridge and 3 mm tray; check hardware engagement'), ('M3 board fastener set', 4, 'Through 2 mm board and 3 mm deck; check hardware engagement'), ('Retention strap', 1, 'Around tray near x=-30 mm; released for insertion')]:
            out.writerow([revision, label, quantity, 'purchased', '', notes])


def execute_run(root, runtime, mode='build', refresh_design=True):
    from cad_backend import build_from_source, runtime_versions
    started = time.monotonic()
    state = load_study(root)
    req = active(state)
    if mode == 'verify':
        previous, old_manifest = selected(root, state)
        check_source(root, previous)
    elif refresh_design:
        write_json(relative(root, 'src/design.json'), {**req['requirements'], 'revision': req['id']})
    run_id = 'run-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    run = relative(root, f'runs/{run_id}')
    run.mkdir(parents=True)
    (run / 'src').mkdir()
    copy_tree(relative(root, 'src'), run / 'src')
    write_json(run / 'requirements.json', {**req['requirements'], 'revision': req['id']})
    frozen_inputs = {'requirements.json': digest(run / 'requirements.json'),
                     **{f'src/{name}': value for name, value in hashes(run / 'src').items()}}
    stages = []
    versions = {'status': 'unknown'}
    checks = {'status': 'unknown', 'checks': [], 'measurements': {}}
    error = None
    try:
        versions = runtime_versions(runtime)
        if mode == 'build':
            built = build_from_source(runtime, relative(root, '.cad'), run / 'src', run / 'exports', run / 'requirements.json')
            stages.extend(built.get('stages', []))
            if built.get('status') in ('error', 'unknown'):
                raise Refused('CAD build/export did not complete: ' + str(built.get('error', 'see tool log')))
        else:
            (run / 'exports').mkdir()
            copy_tree(previous / 'exports', run / 'exports')
        checks, stage = run_checks(runtime, relative(root, '.cad'), run)
        stages.append(stage)
        verify_hashes(run, frozen_inputs)
        if hashes(run / 'src') != {name[4:]: value for name, value in frozen_inputs.items() if name.startswith('src/')}:
            raise Refused('Frozen source file set changed during the run')
        # The package/fixture is immutable within a study and across every CAD invocation.
        if package_hashes() != state['package_hashes']:
            raise Refused('Checker or reference fixture changed during the run')
        if checks['status'] in ('pass', 'fail') and checks.get('measurements', {}).get('parts'):
            check_drawing(run, old_manifest['requirement_revision'] if mode == 'verify' else req['id'],
                          read_json(previous / 'checks.json') if mode == 'verify' else checks)
        report(run, req['id'], checks)
        make_bom(run, req['id'], checks)
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
        checks = {'status': 'unknown', 'checks': [{'id': 'execution', 'status': 'unknown', 'detail': error}], 'measurements': {}}
        report(run, req['id'], checks)
    write_json(run / 'checks.json', checks)
    log = json.dumps(stages, indent=2, default=str).replace(str(root), '<study>').replace(str(PACKAGE), '<plugin>').replace(str(runtime), '<runtime>')
    (run / 'tool.log').write_text(log + '\n')
    manifest = {'schema': SCHEMA, 'id': run_id, 'mode': mode, 'requirement_revision': req['id'], 'units': 'mm',
                'status': checks['status'], 'package_hashes': state['package_hashes'], 'created': stamp(),
                'elapsed_seconds': round(time.monotonic()-started, 3), 'retries': 0,
                'manual_interventions': None, 'usage': None, 'cost': None,
                'artifact_source_run': previous.name if mode == 'verify' else run_id,
                'error': error, 'sha256': hashes(run, exclude=('manifest.json',))}
    manifest['tool_versions'] = versions
    write_json(run / 'manifest.json', manifest)
    state['runs'].append({'id': run_id, 'mode': mode, 'requirement_revision': req['id'], 'status': checks['status']})
    state['latest_run'] = run_id
    if mode == 'build' and checks['status'] == 'pass':
        state['selected_run'] = run_id
    save_study(root, state)
    return run, manifest


def change(root, battery=None, frame_pitch_x=None, reason=''):
    if not reason.strip() or (battery is None and frame_pitch_x is None):
        raise Refused('A requirement change needs a reason and a supported change')
    state = load_study(root)
    req = deepcopy(active(state)['requirements'])
    if battery is not None:
        if battery not in ('A', 'B'):
            raise Refused('Supported battery variants are A and B')
        req['battery']['selected'] = battery
    if frame_pitch_x is not None:
        if frame_pitch_x not in (100, 110):
            raise Refused('Supported frame pitches are 100 or 110 mm')
        req['frame']['pitch_mm'][0] = frame_pitch_x
    if req == active(state)['requirements']:
        raise Refused('Change does not alter the active requirements')
    revision = f"R{int(state['requirements_history'][-1]['id'][1:]) + 1:03d}"
    state['requirements_history'].append({'id': revision, 'requirements': req, 'reason': reason.strip()})
    state['active_requirement'] = revision
    save_study(root, state)
    return state


def handoff(root, runtime):
    state = load_study(root)
    old, prior = selected(root, state)
    check_source(root, old)
    if prior['requirement_revision'] != state['active_requirement'] or prior['status'] != 'pass':
        raise Refused('Selected geometry has not passed the active requirement; revise and build first')
    run, manifest = execute_run(root, runtime, refresh_design=False)
    if manifest['status'] != 'pass':
        raise Refused('Regenerated handoff did not pass; failed run preserved')
    destination = root / 'handoff'
    if destination.exists():
        destination.rename(root / f'handoff-previous-{uuid.uuid4().hex[:8]}')
    destination.mkdir()
    for folder in ('src', 'exports'):
        (destination / folder).mkdir()
        copy_tree(run / folder, destination / folder)
    for name in ('requirements.json', 'checks.json', 'report.md', 'bom.csv'):
        shutil.copy2(run / name, destination / name)
    (destination / 'runtime').mkdir()
    for folder in ('scripts', 'fixtures'):
        (destination / 'runtime' / folder).mkdir()
        copy_tree(PACKAGE / folder, destination / 'runtime' / folder)
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
        if (PACKAGE / name).is_file():
            shutil.copy2(PACKAGE / name, destination / 'runtime' / name)
    (destination / 'assembly-notes.md').write_text('# Assembly and service notes\n\nSynthetic civilian drone subsystem. Fabricate and bench-test before use.\n\n1. Review the tray and bridge drawing, print orientation, supports and fit allowances. The bridge is exported in assembly coordinates; place its deck on the build plate in the slicer.\n2. Fit the tray to the declared frame pattern with four M3 fastener sets. Select hardware for the actual frame.\n3. Attach the bridge at its four support pillars, then attach the inert board surrogate.\n4. Release the strap near x=-30 mm and slide the inert battery surrogate in along +X. Close retention after seating.\n5. Check retention, clearances, mass and service access physically. No load, thermal, electrical or flight qualification is provided.\n\nPurchased hardware mass and fastener engagement remain physical checks.\n')
    (destination / 'reproduce.sh').write_text('#!/bin/sh\nset -eu\nPE_BUNDLE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n: "${PE_RUNTIME:?Set PE_RUNTIME to a dedicated pinned Text-to-CAD venv}"\n: "${1:?Pass a new empty destination study directory}"\nexec "$PE_RUNTIME/bin/python" "$PE_BUNDLE/runtime/scripts/engineering.py" reproduce "$PE_BUNDLE" "$1" --runtime "$PE_RUNTIME"\n')
    receipt = {'schema': SCHEMA, 'selected_run': run.name, 'requirement_revision': manifest['requirement_revision'],
               'units': 'mm', 'tool_versions': manifest['tool_versions'], 'package_hashes': manifest['package_hashes'],
               'status': 'digital-geometry-verified', 'physical_qualification': 'pending',
               'sha256': hashes(destination, exclude=('manifest.json',))}
    write_json(destination / 'manifest.json', receipt)
    return destination


def compare_values(a, b, path='', tolerance=.01):
    if isinstance(a, bool) or isinstance(b, bool):
        return [] if a == b else [path]
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return [] if abs(a-b) <= tolerance else [path]
    if isinstance(a, dict) and isinstance(b, dict):
        if a.keys() != b.keys():
            return [path + ':keys']
        return [p for key in a for p in compare_values(a[key], b[key], path + '.' + key, tolerance)]
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [p for i in range(len(a)) for p in compare_values(a[i], b[i], f'{path}[{i}]', tolerance)]
    return [] if a == b else [path]


def reproduce(bundle, destination, runtime):
    receipt = read_json(relative(bundle, 'manifest.json'))
    verify_hashes(bundle, receipt['sha256'], exact=True, exclude=('manifest.json',))
    if receipt['package_hashes'] != package_hashes():
        raise Refused('Use the reproduction script delivered inside this bundle')
    initialize(destination, read_json(bundle / 'requirements.json'), receipt['requirement_revision'], bundle / 'src', 'Reproduced subsystem')
    with writer(destination):
        run, manifest = execute_run(destination, runtime, refresh_design=False)
    differences = compare_values(read_json(bundle / 'checks.json')['measurements'], read_json(run / 'checks.json')['measurements'])
    result = {'status': 'pass' if manifest['status'] == 'pass' and not differences else 'fail',
              'source_run': receipt['selected_run'], 'reproduced_run': run.name, 'measurement_tolerance': .01,
              'differences': differences, 'independent_cad_state': True, 'tool_versions': manifest['tool_versions']}
    write_json(destination / 'reproduction.json', result)
    return result


def setup(value):
    if not value:
        raise Refused('setup requires --runtime PATH')
    target = Path(value).expanduser().absolute()
    if target.is_symlink():
        raise Refused('Runtime directory may not be a symlink')
    uv = shutil.which('uv')
    if not uv:
        raise Refused('Install uv first using its documented installation flow')
    if not target.exists():
        subprocess.run([uv, 'venv', '--python', '3.14', str(target)], check=True)
    elif not (target / 'bin' / 'python').is_file():
        raise Refused('Existing runtime directory is not a Python environment')
    subprocess.run([uv, 'pip', 'install', '--default-index', 'https://pypi.org/simple', '--python', str(target / 'bin' / 'python'), '-r', str(PACKAGE / 'scripts' / 'requirements.txt')], check=True)
    return {'runtime': str(target), 'status': 'installed', 'cad_state': 'Created per study on first build', 'telemetry': 'disabled by backend'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('setup', 'init', 'resume', 'change', 'build', 'verify', 'handoff', 'reproduce'):
        sub = commands.add_parser(name)
        sub.add_argument('--runtime', default=os.environ.get('PE_RUNTIME'))
        if name == 'reproduce':
            sub.add_argument('bundle')
            sub.add_argument('destination')
        elif name != 'setup':
            sub.add_argument('study')
        if name == 'init':
            sub.add_argument('--name', default='Drone subsystem')
        if name == 'change':
            sub.add_argument('--battery', choices=('A', 'B'))
            sub.add_argument('--frame-pitch-x', type=int, choices=(100, 110))
            sub.add_argument('--reason', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'setup':
            result = setup(args.runtime)
        elif args.command == 'reproduce':
            result = reproduce(study_root(args.bundle), study_root(args.destination), runtime_path(args.runtime))
        else:
            root = study_root(args.study)
            if args.command == 'init':
                state = initialize(root, name=args.name)
                result = {'status': 'initialized', 'requirement_revision': state['active_requirement']}
            elif args.command == 'resume':
                result = load_study(root)
            else:
                with writer(root):
                    if args.command == 'change':
                        state = change(root, args.battery, args.frame_pitch_x, args.reason)
                        result = {'status': 'changed', 'requirement_revision': state['active_requirement'], 'geometry': 'unchanged; run verify before build'}
                    elif args.command == 'handoff':
                        result = {'status': 'pass', 'handoff': str(handoff(root, runtime_path(args.runtime)))}
                    else:
                        run, manifest = execute_run(root, runtime_path(args.runtime), mode=args.command)
                        result = {'status': manifest['status'], 'run': str(run), 'error': manifest['error']}
        print(json.dumps(result, indent=2))
        return 0 if result.get('status') not in ('fail', 'unknown') else 2
    except (Refused, OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(json.dumps({'status': 'refused', 'reason': str(exc)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
