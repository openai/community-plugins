"""Build repeatable multi-part CAD handoffs from an editable assembly recipe."""
from __future__ import annotations

import csv
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.parse import unquote

from cad_backend import environment, executable, runtime_versions
from engineering import (PACKAGE, Refused, digest, hashes, read_json, relative,
                         verify_hashes, write_json)


RECEIPT = 'manifest.json'
REQUIRED = {'assembly.step', 'assembly.glb', 'preview.png', 'viewer.html', 'bom.csv',
            'bom.xlsx', 'boards.md', 'measurements.json', 'rebuild.sh', 'README.md',
            'source/design/assembly.json'}
SCRIPTS = ('engineering.py', 'cad_backend.py', 'assembly.py', 'verify_assembly.py', 'verify_geometry.py',
           'rebuild_assembly.sh', 'requirements.txt')
RECIPE_FILES = ('assembly.json', 'handoff.md', 'boards.md', 'catalog/parts.csv')


def configuration(recipe):
    config = read_json(relative(recipe, 'assembly.json'))
    if not isinstance(config, dict) or not isinstance(config.get('id'), str):
        raise Refused('Expected an assembly recipe object with a design id')
    if (config.get('schema') != 1 or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', config.get('id', ''))
            or not all(isinstance(config.get(key), dict) for key in ('display', 'bom', 'inspection'))):
        raise Refused('Expected a schema 1 assembly recipe with an id, display, bom and inspection')
    if config['inspection'].get('method', 'declared') not in ('declared', 'whoop-65'):
        raise Refused('Unsupported assembly inspection method')
    return config


def copy_sources(source, destination):
    for name in hashes(source):
        if Path(name).name == '.DS_Store':
            continue
        dest = destination / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, dest)


def copy_recipe(recipe, destination):
    for name in RECIPE_FILES:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(relative(recipe, name), target)
    if not relative(recipe, 'geometry/build.py').is_file():
        raise Refused('The recipe requires geometry/build.py')
    copy_sources(recipe / 'geometry', destination / 'geometry')
    if (recipe / 'boards').exists():
        copy_sources(relative(recipe, 'boards'), destination / 'boards')


def initialize(template, output):
    check_destination(output)
    config = configuration(template)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
        stage = Path(tmp) / 'recipe'
        copy_recipe(template, stage)
        stage.rename(output)
    return {'status': 'initialized', 'design': config['id'], 'recipe': str(output)}


def run_stage(runtime, script, output, env, *args):
    try:
        proc = subprocess.run([str(executable(runtime, 'python')), '-E', '-s', '-B', str(script),
                               str(output), *map(str, args)], cwd=output, env=env,
                              capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired as exc:
        raise Refused(f'{script.parent.name}/{script.name} timed out after 10 minutes') from exc
    if proc.returncode:
        detail = (proc.stderr.strip() or proc.stdout.strip())[-2600:]
        raise Refused(f'{script.parent.name}/{script.name} failed ({proc.returncode})\n{detail}')


def source_copy(recipe, destination, method):
    scripts = destination / 'scripts'
    scripts.mkdir(parents=True)
    extra = ('verify_whoop.py',) if method == 'whoop-65' else ()
    for name in (*SCRIPTS, *extra):
        shutil.copyfile(PACKAGE / 'scripts' / name, scripts / name)
    for folder in ('assembly_catalog', 'assembly_display'):
        copy_sources(PACKAGE / 'scripts' / folder, scripts / folder)
    copy_recipe(recipe, destination / 'design')
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copyfile(PACKAGE / name, destination / name)


def inspect_receipt(output):
    receipt = read_json(relative(output, RECEIPT))
    if (receipt.get('schema') != 2 or receipt.get('status') != 'pass'
            or not REQUIRED.issubset(receipt.get('sha256', {}))):
        raise Refused('Expected a complete assembly handoff manifest')
    # Finder and Excel can add these files merely by opening a delivered folder.
    # Validate every managed artifact even if a sidecar happens to share its name.
    sidecars = {p.relative_to(output).as_posix() for p in output.rglob('.DS_Store') if p.is_file()}
    verify_hashes(output, receipt['sha256'], exact=True, exclude=sidecars | {RECEIPT, '~$bom.xlsx'})
    if configuration(output / 'source/design')['id'] != receipt.get('design'):
        raise Refused('The manifest and recipe refer to different designs')
    return receipt


def check_report(path):
    report = read_json(path)
    checks = report.get('checks', [])
    if (report.get('status') != 'pass' or not checks or
            any(c.get('status') != 'pass' for c in checks)):
        raise Refused('Assembly measurement failed or is incomplete')
    return report


def check_delivery_links(output):
    with (output / 'bom.csv').open(encoding='utf-8', newline='') as stream:
        links = [row['Source'] for row in csv.DictReader(stream)]
    for name in ('README.md', 'boards.md'):
        links.extend(re.findall(r'\[[^\]]*\]\(([^)]+)\)', (output / name).read_text()))
    for link in links:
        target = unquote(link.split('#', 1)[0].strip('<>'))
        if target and not target.startswith(('https://', 'http://')) and not relative(output, target).is_file():
            raise Refused(f'Broken delivered file link: {link}')


def measure(scripts, recipe, method, output, runtime, env, report):
    if method == 'whoop-65':
        run_stage(runtime, scripts / 'verify_whoop.py', output, env, '--report', report)
    else:
        run_stage(runtime, scripts / 'verify_assembly.py', output, env, recipe, '--report', report)
    return check_report(report)


def verify(output, runtime):
    inspect_receipt(output)
    runtime_versions(runtime)
    recipe = output / 'source/design'
    method = configuration(recipe)['inspection'].get('method', 'declared')
    scripts = PACKAGE / 'scripts'
    with tempfile.TemporaryDirectory(prefix='assembly-verify-') as tmp:
        env = environment(Path(tmp) / 'state')
        fresh = measure(scripts, recipe, method, output, runtime, env, Path(tmp) / 'measurements.json')
        run_stage(runtime, scripts / 'assembly_catalog/build.py', output, env, recipe, '--check')
        if fresh != check_report(output / 'measurements.json'):
            raise Refused('New measured result differs from the delivered report')
        check_delivery_links(output)
    return {'status': 'pass', 'handoff': str(output), 'checks': len(fresh['checks'])}


def check_destination(output):
    if output.is_symlink() or (output.exists() and (not output.is_dir() or any(output.iterdir()))):
        raise Refused('Choose a new or empty output directory. The existing handoff is preserved.')


def compare(baseline, rebuilt):
    previous = inspect_receipt(baseline)
    if check_report(baseline / 'measurements.json')['checks'] != check_report(rebuilt / 'measurements.json')['checks']:
        raise Refused('Rebuilt assembly differs from the baseline measurements')
    stable_exports = ['assembly.glb', 'viewer.html', 'preview.png', 'bom.xlsx', 'bom.csv', 'boards.md']
    stable_exports.extend(name for name in previous['sha256'] if name.startswith('print/'))
    for name in stable_exports:
        if previous['sha256'][name] != digest(rebuilt / name):
            raise Refused(f'Rebuilt {name} differs from the baseline')
    expected_source = {name.removeprefix('source/'): value for name, value in previous['sha256'].items()
                       if name.startswith('source/')}
    if expected_source != hashes(rebuilt / 'source'):
        raise Refused('Rebuild used different source than the baseline')
    return {'status': 'pass', 'baseline_manifest_sha256': digest(baseline / RECEIPT),
            'compared': ['exported geometry checks', 'print meshes', 'colored assembly', 'interactive viewer',
                         'rendered preview', 'BOM workbook and CSV', 'board reference', 'editable source']}


def build(recipe, output, runtime, baseline=None):
    if output.is_relative_to(PACKAGE) or output.is_relative_to(recipe) or (baseline and output.is_relative_to(baseline)):
        raise Refused('Choose an output directory outside the plugin, recipe and baseline')
    config = configuration(recipe)
    method = config['inspection'].get('method', 'declared')
    check_destination(output)
    versions = runtime_versions(runtime)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.assembly-', dir=output.parent) as tmp:
        stage = Path(tmp) / 'delivery'
        source_copy(recipe, stage / 'source', method)
        src = stage / 'source/design'
        scripts = stage / 'source/scripts'
        env = environment(Path(tmp) / 'state')
        run_stage(runtime, src / 'geometry/build.py', stage, env)
        if (src / 'boards').is_dir():
            copy_sources(src / 'boards', stage / 'boards')
        run_stage(runtime, scripts / 'assembly_catalog/build.py', stage, env, src)
        run_stage(runtime, scripts / 'assembly_display/build.py', stage, env, src)
        checks = measure(scripts, src, method, stage, runtime, env, stage / 'measurements.json')['checks']
        run_stage(runtime, scripts / 'assembly_catalog/build.py', stage, env, src, '--check')
        shutil.copyfile(src / 'handoff.md', stage / 'README.md')
        shutil.copyfile(scripts / 'rebuild_assembly.sh', stage / 'rebuild.sh')
        check_delivery_links(stage)
        files = hashes(stage)
        missing = REQUIRED.difference(files)
        if missing:
            raise Refused('Missing handoff files: ' + ', '.join(sorted(missing)))
        receipt = {'design': config['id'], 'schema': 2, 'status': 'pass', 'runtime': versions, 'sha256': files}
        if baseline:
            receipt['reproduction'] = compare(baseline, stage)
        write_json(stage / RECEIPT, receipt)
        # Same-filesystem rename publishes the complete handoff and cannot replace
        # a populated directory. An interrupted build never locks out its retry.
        check_destination(output)
        stage.rename(output)
    return {'status': 'pass', 'design': config['id'], 'handoff': str(output), 'checks': len(checks),
            'viewer': str(output / 'viewer.html'), 'bom': str(output / 'bom.xlsx'),
            **({'reproduction': receipt['reproduction']} if baseline else {})}
