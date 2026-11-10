#!/usr/bin/env python3
"""Resumable, independently measured CAD studies with preserved requirement revisions."""
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

# The installed package is immutable input. Local helper imports must not leave
# interpreter-specific files there, even when the caller did not pass -B.
sys.dont_write_bytecode = True

PACKAGE = Path(__file__).resolve().parents[1]
FIXTURE = PACKAGE / 'fixtures' / 'drone-module'
PROFILES = {'drone-module': 'drone-module', 'mechanical-part': 'sensor-plate'}
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


def requirement_digest(requirements):
    """Detect accidental edits; this local digest is not a signed attestation."""
    return hashlib.sha256(json.dumps(requirements, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def validate_requirements(profile, requirements):
    if profile not in PROFILES or not isinstance(requirements, dict):
        raise Refused('Unsupported study profile or requirements')
    if profile == 'mechanical-part':
        from verify_mechanical import validate_requirements as validate
        validate(requirements)
        return
    expected = read_json(FIXTURE / 'requirements.json')
    battery = requirements['battery']['selected']
    pitch = requirements['frame']['pitch_mm'][0]
    if battery not in ('A', 'B') or pitch not in (100, 110):
        raise Refused('Unsupported requirement variant')
    expected['battery']['selected'] = battery
    expected['frame']['pitch_mm'][0] = pitch
    if requirements != expected:
        raise Refused('Frozen requirements were edited outside an allowed revision')


def load_study(root):
    state = read_json(relative(root, 'study.json'))
    profile = state.get('fixture')
    if state.get('schema') != SCHEMA or profile not in PROFILES:
        raise Refused('Unsupported study schema/profile')
    if state.get('package_hashes') != package_hashes():
        raise Refused('Installed checker, fixture or runtime scripts changed since study creation; requalify in a new study')
    if not state.get('requirements_history'):
        raise Refused('Study has no requirement history')
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
        validate_requirements(profile, actual)
        # Old drone studies have a closed template comparison above. Open
        # mechanical requirements additionally preserve their accepted digest.
        if (profile == 'mechanical-part' or 'requirements_sha256' in record) and record.get('requirements_sha256') != requirement_digest(actual):
            raise Refused('Preserved requirement revision was edited; use change with a reason')
        if not str(record.get('reason', '')).strip():
            raise Refused('Requirement revision lacks a reason')
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


def initialize(root, requirements=None, revision='R001', source=None, name='Engineering study', profile='drone-module'):
    if root.is_symlink():
        raise Refused('Study directory may not be a symlink')
    if root.exists() and any(root.iterdir()):
        raise Refused('Start with a new or empty study directory')
    if profile not in PROFILES:
        raise Refused('Unsupported study profile')
    fixture = PACKAGE / 'fixtures' / PROFILES[profile]
    req = deepcopy(requirements if requirements is not None else read_json(fixture / 'requirements.json'))
    if not isinstance(req, dict):
        raise Refused('Requirements must be a JSON object')
    req.pop('revision', None)
    validate_requirements(profile, req)
    if not re.fullmatch(r'R[0-9]{3,}', revision) or int(revision[1:]) < 1:
        raise Refused('Invalid requirement revision identity')
    root.mkdir(parents=True, exist_ok=True)
    src = root / 'src'
    src.mkdir()
    if source:
        copy_tree(source, src)
    else:
        filenames = ('model.py', 'exports.py', 'drawing.py') if profile == 'drone-module' else ('model.py', 'exports.py')
        for filename in filenames:
            shutil.copy2(fixture / filename, src / filename)
        write_json(src / 'design.json', {**req, 'revision': revision})
    record = {'id': revision, 'requirements': req, 'requirements_sha256': requirement_digest(req),
              'reason': 'Initial accepted requirements' if not source else 'Reproduced from delivered source and requirements'}
    state = {'schema': SCHEMA, 'fixture': profile, 'name': name, 'created': stamp(),
             'package_hashes': package_hashes(), 'active_requirement': revision,
             'requirements_history': [record], 'selected_run': None, 'latest_run': None, 'runs': []}
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


def run_checks(runtime, state_root, run, stages=None, profile='drone-module'):
    from cad_backend import run_cad
    output = relative(run, 'checks.json')
    if output.exists():
        raise Refused('Checker output already exists before fresh verification')
    if profile not in PROFILES:
        raise Refused('Unsupported verification profile')
    checker = 'verify_geometry.py' if profile == 'drone-module' else 'verify_mechanical.py'
    args = ['run', str(PACKAGE / 'scripts' / checker), str(run / 'exports' / 'assembly.step'),
            str(run / 'requirements.json'), '--out', str(run / 'checks.json'), '--print-dir', str(run / 'exports')]
    result = run_cad(runtime, state_root, args, run)
    # Preserve diagnostics even if the process times out, crashes, or returns an
    # incomplete report. A failed invocation is still part of the run evidence.
    if stages is not None:
        stages.append(result)
    if result['returncode'] not in (0, 1, 2):
        raise Refused('Geometry checker process failed: ' + str(result['returncode']))
    if not output.is_file() or output.is_symlink():
        raise Refused('Geometry checker produced no result: ' + result.get('stderr', '')[-1000:])
    checks = read_json(output)
    if {0: 'pass', 1: 'fail', 2: 'unknown'}[result['returncode']] != checks.get('status'):
        raise Refused('Checker status disagrees with its process exit')
    if checks['status'] == 'pass' and profile == 'mechanical-part':
        from verify_mechanical import expected_check_ids
        req = read_json(run / 'requirements.json')
        required = set(expected_check_ids(req))
        measured = checks.get('checks', [])
        prints = checks.get('print_checks', {})
        print_ids = {f"{req['part_name']}_{check}" for check in
                     ('mesh_topology', 'mesh_bounds', 'mesh_volume', 'mesh_surface', 'step_consistency')}
        if ({item['id'] for item in measured} != required or len(measured) != len(required) or
            any(item.get('status') != 'pass' for item in measured) or prints.get('status') != 'pass' or
            {item['id'] for item in prints.get('checks', [])} != print_ids or
            len(prints.get('checks', [])) != len(print_ids) or
            any(item.get('status') != 'pass' for item in prints['checks'])):
            raise Refused('Passing mechanical report lacks required successful measurements')
    elif checks['status'] == 'pass':
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


def check_drawing(run, revision, checks, runtime=None, stages=None):
    """Check actual selectable PDF callouts plus the independent JSON readback.

    Text-to-CAD outlines its dimension-line lettering. The drawing adds a visible
    measured block for this machine check; the outlined views still need visual
    review. Parsing text is not a general proof of legibility or graphic fidelity.
    """
    pdf = relative(run, 'exports/module-drawing.pdf')
    if not pdf.is_file() or not 100 <= pdf.stat().st_size <= 25_000_000:
        raise Refused('Dimensioned PDF is missing or invalid')
    data = pdf.read_bytes()
    if not data.startswith(b'%PDF-') or not data.rstrip().endswith(b'%%EOF'):
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
    check_values = {item['id']: item.get('actual') for item in checks['checks']}
    hole_diameters = {}
    for label, interface in (('tray', 'frame'), ('bridge', 'board')):
        hole_diameters[label] = []
        for field, group in (('primary_hole_diameter_mm', f'{interface}_interface'),
                             ('attachment_hole_diameter_mm', f'{label}_attachment_interface')):
            holes = check_values.get(group)
            if not isinstance(holes, list) or len(holes) != 4:
                raise Refused(f'Drawing {label} lacks four measured interface bores')
            diameter = holes[0]['diameter_mm']
            if any(compare_values(diameter, hole['diameter_mm']) for hole in holes):
                raise Refused(f'Drawing {label} hole group has inconsistent diameters')
            if compare_values(diameter, drawing['parts'][label].get(field)):
                raise Refused(f'Drawing {label} {field} disagrees with geometry')
            hole_diameters[label].append(diameter)
    # The CLI wrapper remains stdlib-only. Parse with the same isolated, pinned
    # runtime that built the CAD, never with a global pypdf installation.
    from cad_backend import executable, sanitize
    command = [str(executable(Path(runtime or sys.prefix), 'python')), '-I', '-B',
               str(PACKAGE / 'scripts/pdf_readback.py'), str(pdf)]
    started = time.monotonic()
    try:
        process = subprocess.run(command, cwd=run, capture_output=True, text=True,
                                 timeout=30, check=False)
        code, stdout, stderr = process.returncode, process.stdout, process.stderr
    except subprocess.TimeoutExpired as error:
        code, stdout, stderr = 124, error.stdout or '', error.stderr or ''
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors='replace')
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors='replace')
        stderr += '\nPDF readback timed out.'
    except OSError as error:
        code, stdout, stderr = 127, '', str(error)
    record = {'stage': 'pdf_readback', 'command': [sanitize(arg) for arg in command],
              'returncode': code, 'stdout': sanitize(stdout), 'stderr': sanitize(stderr),
              'elapsed_seconds': round(time.monotonic() - started, 3)}
    if stages is not None:
        stages.append(record)
    if code != 0:
        raise Refused('Dimensioned PDF cannot be parsed or its callouts cannot be read; see pdf_readback stage')
    try:
        document = json.loads(stdout)
        if document.get('schema') != 1 or document.get('status') != 'pass':
            raise ValueError('Invalid PDF readback result')
        if document['encrypted'] or document['page_count'] != 2 or len(document['pages']) != 2:
            raise Refused('Drawing must contain two unencrypted A3 part pages')
        pages = []
        for page in document['pages']:
            if (abs(page['width_pt'] - 420 * 72 / 25.4) > .1 or
                abs(page['height_pt'] - 297 * 72 / 25.4) > .1):
                raise Refused('Drawing pages must remain A3 landscape')
            if not page['has_contents']:
                raise Refused('Drawing page has no content')
            if not isinstance(page['text'], str):
                raise ValueError('Invalid PDF text readback')
            pages.append(page['text'])
    except Refused:
        raise
    except (ValueError, KeyError, TypeError) as error:
        raise Refused('Dimensioned PDF readback result is invalid') from error
    number = r'([-+]?(?:\d+(?:\.\d*)?|\.\d+))'
    for text, label, interface in zip(pages, ('tray', 'bridge'), ('frame', 'board')):
        if text.count('MEASURED STEP READBACK') != 1:
            raise Refused(f'Drawing {label} lacks one readable measured-dimension block')
        identities = re.findall(r'PART: ([A-Z]+) REVISION: (R[0-9]+) UNITS: ([A-Z]+)', text)
        if identities != [(label.upper(), revision, 'MM')]:
            raise Refused(f'Drawing PDF {label} identity, revision or units disagree with selected geometry')

        def dimension(pattern, values, description):
            matches = re.findall(pattern, text)
            if len(matches) != 1:
                raise Refused(f'Drawing PDF {label} is missing or repeats {description}')
            found = matches[0] if isinstance(matches[0], tuple) else (matches[0],)
            if compare_values([float(value) for value in found], list(values)):
                raise Refused(f'Drawing PDF {label} {description} disagrees with geometry')

        dimension(r'OVERALL XYZ: ' + number + r' X ' + number + r' X ' + number + r' MM',
                  measured['parts'][label]['bounds_mm'], 'overall dimensions')
        dimension(interface.upper() + r' PITCH XY: ' + number + r' X ' + number + r' MM',
                  measured[f'{interface}_pitch_mm'], 'mounting pitch')
        dimension(interface.upper() + r' HOLES: (\d+) X DIA ' + number + r' MM THRU',
                  [4, hole_diameters[label][0]], 'primary hole callout')
        dimension(r'BRIDGE ATTACHMENT HOLES: (\d+) X DIA ' + number + r' MM THRU',
                  [4, hole_diameters[label][1]], 'attachment hole callout')
        walls = measured['wall_thickness_mm']
        if label == 'tray':
            dimension(r'CAVITY WIDTH: ' + number + r' MM', [measured['cavity_width_mm']], 'cavity width')
            dimension(r'DECLARED WALLS: FLOOR ' + number + r' / LEFT ' + number +
                      r' / RIGHT ' + number + r' / BACK ' + number + r' MM',
                      [walls[name] for name in ('tray_floor', 'tray_left_rail', 'tray_right_rail', 'tray_backstop')],
                      'declared wall thicknesses')
        else:
            dimension(r'DECK THICKNESS: ' + number + r' MM', [walls['bridge_deck']], 'deck thickness')


def report(run, revision, checks, profile='drone-module'):
    lines = ['# Measured design report', '', f'Requirement revision: {revision}', f"Result: **{checks['status'].upper()}**", '',
             '| Check | Result | Measured |', '|---|---|---|']
    for item in checks.get('checks', []):
        value = json.dumps(item.get('actual', item.get('detail', '')), ensure_ascii=False)
        lines.append(f"| {item['id']} | {item['status']} | {value.replace('|', '/')} |")
    lines += ['', 'Mass is a solid-material estimate using the declared density. Purchased items are separate.',
              ('Declared planar walls are measured; this is not universal wall-thickness, structural, printability or flight qualification.'
               if profile == 'drone-module' else
               'Only the listed checks were measured. General wall thickness, manufacturing suitability, strength and physical fit remain unqualified.'),
              'Fabrication and bench measurements remain separate physical checks.', '']
    (run / 'report.md').write_text('\n'.join(lines))


def make_bom(run, revision, checks, profile='drone-module'):
    import csv
    parts = checks.get('measurements', {}).get('parts', {})
    req = read_json(run / 'requirements.json')
    density = req['manufacturing']['density_g_per_cm3'] if profile == 'drone-module' else req['density_g_per_cm3']
    with (run / 'bom.csv').open('w', newline='') as f:
        out = csv.writer(f)
        mass_column = 'solid_polymer_mass_g' if profile == 'drone-module' else 'solid_material_mass_g'
        out.writerow(['revision', 'item', 'quantity', 'source', mass_column, 'notes'])
        labels = ('tray', 'bridge') if profile == 'drone-module' else (req['part_name'],)
        for label in labels:
            volume = parts.get(label, {}).get('volume_mm3')
            out.writerow([revision, label, 1, f'exports/{label}.stl', '' if volume is None else round(volume*density/1000, 5), 'Print orientation reviewed separately' if profile == 'drone-module' else 'Manufacturing process and orientation reviewed separately'])
        if profile != 'drone-module':
            return
        for label, quantity, notes in [('M3 frame fastener set', 4, 'Length selected to suit host frame; frame not part of fixture'), ('M3 bridge fastener set', 4, 'Through 30 mm bridge and 3 mm tray; check hardware engagement'), ('M3 board fastener set', 4, 'Through 2 mm board and 3 mm deck; check hardware engagement'), ('12 mm retention strap', 2, 'At x=-34 and +34 mm through 13 x 3.5 mm guide slots; verify webbing thickness, routing and closure physically; released for insertion')]:
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
            built = build_from_source(runtime, relative(root, '.cad'), run / 'src', run / 'exports', run / 'requirements.json', profile=state['fixture'])
            stages.extend(built.get('stages', []))
            if built.get('status') in ('error', 'unknown'):
                raise Refused('CAD build/export did not complete: ' + str(built.get('error', 'see tool log')))
        else:
            (run / 'exports').mkdir()
            copy_tree(previous / 'exports', run / 'exports')
        checks, _ = run_checks(runtime, relative(root, '.cad'), run, stages, profile=state['fixture'])
        verify_hashes(run, frozen_inputs)
        if hashes(run / 'src') != {name[4:]: value for name, value in frozen_inputs.items() if name.startswith('src/')}:
            raise Refused('Frozen source file set changed during the run')
        # The package/fixture is immutable within a study and across every CAD invocation.
        if package_hashes() != state['package_hashes']:
            raise Refused('Checker or reference fixture changed during the run')
        if state['fixture'] == 'drone-module' and checks['status'] in ('pass', 'fail') and checks.get('measurements', {}).get('parts'):
            check_drawing(run, old_manifest['requirement_revision'] if mode == 'verify' else req['id'],
                          read_json(previous / 'checks.json') if mode == 'verify' else checks, runtime, stages)
        report(run, req['id'], checks, profile=state['fixture'])
        make_bom(run, req['id'], checks, profile=state['fixture'])
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
        checks = {'status': 'unknown', 'checks': [{'id': 'execution', 'status': 'unknown', 'detail': error}], 'measurements': {}}
        report(run, req['id'], checks, profile=state['fixture'])
    write_json(run / 'checks.json', checks)
    log = json.dumps(stages, indent=2, default=str).replace(str(root), '<study>').replace(str(PACKAGE), '<plugin>').replace(str(runtime), '<runtime>')
    (run / 'tool.log').write_text(log + '\n')
    manifest = {'schema': SCHEMA, 'fixture': state['fixture'], 'id': run_id, 'mode': mode, 'requirement_revision': req['id'], 'units': 'mm',
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


def change(root, battery=None, frame_pitch_x=None, reason='', requirements=None):
    if not reason.strip() or (battery is None and frame_pitch_x is None and requirements is None):
        raise Refused('A requirement change needs a reason and a supported change')
    state = load_study(root)
    req = deepcopy(active(state)['requirements'])
    if state['fixture'] == 'mechanical-part':
        if requirements is None or battery is not None or frame_pitch_x is not None:
            raise Refused('Mechanical studies change through --requirements JSON and --reason')
        req = deepcopy(requirements)
        if not isinstance(req, dict):
            raise Refused('Requirements must be a JSON object')
        req.pop('revision', None)
        validate_requirements(state['fixture'], req)
    elif requirements is not None:
        raise Refused('Drone changes use --battery or --frame-pitch-x')
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
    state['requirements_history'].append({'id': revision, 'requirements': req,
                                          'requirements_sha256': requirement_digest(req), 'reason': reason.strip()})
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
    current = relative(root, 'handoff')
    if current.exists() and not current.is_dir():
        raise Refused('Handoff destination must be a directory')
    # Build and hash the complete package before replacing the selected handoff.
    # Interrupted preparation leaves the current package intact and a diagnostic
    # staging directory. Promotion never exposes a partly copied handoff.
    destination = root / f'.handoff-{uuid.uuid4().hex}.tmp'
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
    if state['fixture'] == 'drone-module':
        (destination / 'assembly-notes.md').write_text('# Assembly and service notes\n\nSynthetic civilian drone subsystem. Fabricate and bench-test before use.\n\n1. Review the tray and bridge drawing, print orientation, supports and fit allowances. The bridge is exported in assembly coordinates; place its deck on the build plate in the slicer.\n2. Fit the tray to the declared frame pattern with four M3 fastener sets. Select hardware for the actual frame.\n3. Attach the bridge at its four support pillars, then attach the inert board surrogate.\n4. Release both straps at x=-34 and +34 mm and slide the inert battery surrogate in along +X. Route nominal 12 mm webbing through the paired 13 x 3.5 mm guide slots and close retention after seating. Check the actual strap thickness, bend clearance, closure and frame underside access; the strap is not a modeled qualified component.\n5. Check retention, clearances, mass and service access physically. No load, thermal, electrical or flight qualification is provided.\n\nPurchased hardware mass and fastener engagement remain physical checks.\n')
    else:
        (destination / 'assembly-notes.md').write_text(
            '# Mechanical-part handoff\n\n'
            'The editable source, accepted requirements and measured STEP/STL belong to one revision.\n'
            'Only checks explicitly listed in checks.json were evaluated. No drawing PDF is supplied by this profile.\n\n'
            'Review manufacturing process, tolerances, material, mounting hardware and service conditions before fabrication.\n'
            'Solid-volume mass excludes infill and purchased hardware. Strength, general wall thickness, process suitability and physical fit remain unqualified.\n'
            'Review supplied Python source as executable code before running the rebuild.\n')
    (destination / 'reproduce.sh').write_text('#!/bin/sh\nset -eu\nPE_BUNDLE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n: "${PE_RUNTIME:?Set PE_RUNTIME to a dedicated pinned Text-to-CAD venv}"\n: "${1:?Pass a new empty destination study directory}"\nexec "$PE_RUNTIME/bin/python" "$PE_BUNDLE/runtime/scripts/engineering.py" reproduce "$PE_BUNDLE" "$1" --runtime "$PE_RUNTIME"\n')
    verify_hashes(destination / 'runtime', manifest['package_hashes'])
    receipt = {'schema': SCHEMA, 'fixture': state['fixture'], 'selected_run': run.name, 'requirement_revision': manifest['requirement_revision'],
               'units': 'mm', 'tool_versions': manifest['tool_versions'], 'package_hashes': manifest['package_hashes'],
               'status': 'digital-geometry-verified', 'physical_qualification': 'pending',
               'sha256': hashes(destination, exclude=('manifest.json',))}
    write_json(destination / 'manifest.json', receipt)
    verify_hashes(destination, receipt['sha256'], exact=True, exclude=('manifest.json',))
    previous = root / f'handoff-previous-{uuid.uuid4().hex[:8]}'
    replaced = current.exists()
    if replaced:
        current.rename(previous)
    try:
        destination.rename(current)
    except OSError:
        if replaced and not current.exists():
            previous.rename(current)
        raise
    return current


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


def compare_rebuilt_geometry(bundle, run, destination, runtime, expected_parts):
    from cad_backend import run_cad
    from compare_geometry import MAX_SYMMETRIC_DIFFERENCE_MM3, MAX_BOUNDS_DRIFT_MM
    output = relative(destination, 'geometry-comparison.json')
    if output.exists():
        raise Refused('Geometry comparison output must be new')
    delivered = relative(bundle, 'exports/assembly.step')
    rebuilt = relative(run, 'exports/assembly.step')
    if not delivered.is_file() or not rebuilt.is_file():
        report = {'schema_version': 1, 'status': 'unknown',
                  'error': 'Delivered or rebuilt assembly STEP is missing; geometry identity cannot pass'}
        write_json(output, report)
        return report
    inputs = {'delivered': {'file': delivered.name, 'sha256': digest(delivered)},
              'rebuilt': {'file': rebuilt.name, 'sha256': digest(rebuilt)}}
    args = ['run', str(PACKAGE / 'scripts/compare_geometry.py'), str(delivered), str(rebuilt), '--out', str(output)]
    for name in expected_parts:
        args.extend(['--part', name])
    invocation = run_cad(runtime, relative(destination, '.cad'), args, destination)
    log = json.dumps(invocation, indent=2).replace(str(bundle), '<handoff>').replace(str(destination), '<reproduced-study>').replace(str(PACKAGE), '<plugin>').replace(str(runtime), '<runtime>')
    relative(destination, 'geometry-comparison-tool.json').write_text(log + '\n')
    try:
        report = read_json(output)
        if invocation['returncode'] not in (0, 1, 2) or {0: 'pass', 1: 'fail', 2: 'unknown'}[invocation['returncode']] != report.get('status'):
            raise Refused('Native geometry comparison did not complete consistently')
        if (report.get('schema_version') != 1 or report.get('units') != 'mm' or
                report.get('inputs') != inputs or report.get('expected_parts') != sorted(expected_parts) or
                report.get('tolerances') != {'symmetric_difference_mm3': MAX_SYMMETRIC_DIFFERENCE_MM3,
                                              'bounds_drift_mm': MAX_BOUNDS_DRIFT_MM}):
            raise Refused('Native comparison identity, input hashes or tolerances differ')
        if report['status'] == 'pass' and (set(report.get('parts', {})) != set(expected_parts) or
                any(part.get('status') != 'pass' for part in report['parts'].values())):
            raise Refused('Native comparison lacks passing geometry for every part')
        if digest(delivered) != inputs['delivered']['sha256'] or digest(rebuilt) != inputs['rebuilt']['sha256']:
            raise Refused('Geometry changed during native comparison')
        return report
    except (OSError, ValueError, KeyError, TypeError) as error:
        report = {'schema_version': 1, 'status': 'unknown', 'inputs': inputs, 'error': str(error)}
        write_json(output, report)
        return report


def reproduce(bundle, destination, runtime):
    receipt = read_json(relative(bundle, 'manifest.json'))
    verify_hashes(bundle, receipt['sha256'], exact=True, exclude=('manifest.json',))
    if receipt['package_hashes'] != package_hashes():
        raise Refused('Use the reproduction script delivered inside this bundle')
    requirements = read_json(bundle / 'requirements.json')
    profile = receipt.get('fixture', 'drone-module')
    initialize(destination, requirements, receipt['requirement_revision'], bundle / 'src',
               'Reproduced subsystem', profile=profile)
    with writer(destination):
        run, manifest = execute_run(destination, runtime, refresh_design=False)
        differences = compare_values(read_json(bundle / 'checks.json')['measurements'], read_json(run / 'checks.json')['measurements'])
        expected_parts = ['tray', 'bridge'] if profile == 'drone-module' else [requirements['part_name']]
        comparison = compare_rebuilt_geometry(bundle, run, destination, runtime, expected_parts)
        # The comparison cannot authorize files changed since either accepted run.
        verify_hashes(bundle, receipt['sha256'], exact=True, exclude=('manifest.json',))
        verify_hashes(run, manifest['sha256'], exact=True, exclude=('manifest.json',))
        if package_hashes() != receipt['package_hashes']:
            raise Refused('Runtime or checker changed during reproduction')
        result = {'status': 'pass' if manifest['status'] == 'pass' and not differences and comparison['status'] == 'pass' else 'fail',
                  'source_run': receipt['selected_run'], 'reproduced_run': run.name, 'measurement_tolerance': .01,
                  'differences': differences, 'independent_cad_state': True, 'tool_versions': manifest['tool_versions'],
                  'geometry_comparison': {'status': comparison['status'], 'file': 'geometry-comparison.json',
                                          'sha256': digest(destination / 'geometry-comparison.json'),
                                          'delivered_artifact': 'exports/assembly.step',
                                          'rebuilt_artifact': f'runs/{run.name}/exports/assembly.step'}}
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
            sub.add_argument('--name', default='Engineering study')
            sub.add_argument('--profile', choices=tuple(PROFILES), default='drone-module')
            sub.add_argument('--requirements', help='Accepted mechanical requirements JSON; defaults to the sensor-plate starter')
        if name == 'change':
            sub.add_argument('--requirements', help='New mechanical requirements JSON, preserved as a revision')
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
                if args.requirements and args.profile != 'mechanical-part':
                    raise Refused('--requirements is supported by the mechanical-part profile')
                state = initialize(root, read_json(args.requirements) if args.requirements else None,
                                   name=args.name, profile=args.profile)
                result = {'status': 'initialized', 'requirement_revision': state['active_requirement']}
            elif args.command == 'resume':
                result = load_study(root)
            else:
                with writer(root):
                    if args.command == 'change':
                        state = change(root, args.battery, args.frame_pitch_x, args.reason,
                                       read_json(args.requirements) if args.requirements else None)
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
