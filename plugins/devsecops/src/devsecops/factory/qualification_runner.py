"""Bounded installed qualification entry point, using only factory_demo.py.

No deployment, mutation reconciliation, model correction or lifecycle graph is
implemented here. A timed-out invocation remains uncertain and is never retried.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import io
import os
from pathlib import Path
import re
import sys
import time
import zipfile

from . import native_codex
from .lifecycle import PACKAGE_ROOT
from .qualification import QualificationError, qualify_and_write, _relative
from .state import MAX_ARTIFACT_BYTES, _canonical, _exclusive, _json, _read, _safe_path, _sha


def _now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _outside(path, author):
    path, author = _safe_path(Path(path)), _safe_path(Path(author))
    if path.is_relative_to(author):
        raise QualificationError('Qualification must operate outside the author checkout')
    return path


def _new_output(path, author):
    path = _outside(path, author)
    if path.exists():
        raise QualificationError('Qualification output must be new; inspect existing attempts before continuing')
    path.mkdir(mode=0o700)
    return path


def _identity(plugin_id, version):
    if plugin_id != 'devsecops@devsecops-local' or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,119}', version):
        raise QualificationError('Unsupported explicit local plugin identity or version')


def _environment():
    # Credentials remain inherited references used by the canonical operator
    # path; their values are never serialized. -I also isolates Python imports.
    return {key: value for key, value in os.environ.items()
            if key not in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'PYTHONINSPECT')}



def _engine_outside_sources(engine, *, author_checkout, installed_root):
    """Check the actual interpreter and import origins, not only its venv link."""
    author = _safe_path(Path(author_checkout))
    cache = installed_root.parents[2]
    try:
        runtime = engine['runtime']
        locations = [engine['resolved_executable'], runtime['executable'], runtime['prefix'], runtime['base_prefix']]
        imports = runtime['imports']
        if type(imports) is not list or len(imports) != 3:
            raise ValueError
        locations.extend(item['file'] for item in imports)
        for value in locations:
            if not isinstance(value, str):
                raise ValueError
            path = Path(value)
            if not path.is_absolute() or '..' in path.parts:
                raise ValueError
            # An explicitly selected venv executable may be a symlink. Check
            # its containing path and resolved target without changing argv.
            _safe_path(path.parent)
            resolved = path.resolve(strict=True)
            for candidate in (path, resolved):
                if candidate.is_relative_to(author) or candidate.is_relative_to(cache):
                    raise ValueError
    except (KeyError, TypeError, ValueError, OSError):
        raise QualificationError('Dependency interpreter or actual import origin is inside author source/cache or unavailable') from None


def run_canonical(*, installed_root, package_sha256, plugin_id, plugin_version, engine_python,
                  author_checkout, output, timeout, arguments):
    _identity(plugin_id, plugin_version)
    if not 1 <= timeout <= 2_592_000:
        raise QualificationError('Canonical invocation deadline is outside its supported bound')
    root = _outside(installed_root, author_checkout)
    if root != _safe_path(PACKAGE_ROOT):
        raise QualificationError('Run qualification using the script from the selected installed package')
    deadline = time.monotonic() + timeout
    package_raw, paths = native_codex._package(root, package_sha256, plugin_id, plugin_version, deadline)
    required = {'scripts/factory_demo.py', 'scripts/qualify_release.py',
                'src/devsecops/factory/terminal_evidence.py', 'src/devsecops/factory/qualification.py'}
    if not required <= paths.keys():
        raise QualificationError('Installed candidate lacks the canonical qualification entry points')
    from .cli import build_parser
    arguments = list(arguments)
    if arguments and arguments[0] == '--':
        arguments.pop(0)
    try:
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            args = build_parser().parse_args(arguments)
    except SystemExit:
        raise QualificationError('Arguments do not match the installed canonical factory command') from None
    if (args.command != 'e2e' or args.profile != 'bigbang-live' or args.operation_backend != 'owned-linux'
            or args.plugin_id != plugin_id or args.plugin_version != plugin_version
            or _safe_path(args.package_manifest) != root / 'SHA256SUMS'):
        raise QualificationError('Qualification requires the same installed canonical bigbang-live owned-linux command')
    for path in (args.config, args.lock, args.environment_inventory, args.output or args.resume):
        if not path.is_absolute():
            raise QualificationError('Canonical qualification input and run paths must be explicit and absolute')
    run_dir = _outside(args.output or args.resume, author_checkout)
    if run_dir.is_relative_to(root) or root.is_relative_to(run_dir):
        raise QualificationError('Run evidence must be separate from the installed package')
    output = _outside(output, author_checkout)
    if output.is_relative_to(run_dir) or run_dir.is_relative_to(output) or output.is_relative_to(root):
        raise QualificationError('Invocation evidence must be outside the run and installed package')
    python = Path(engine_python)
    if not python.is_absolute() or '..' in python.parts:
        raise QualificationError('Dependency interpreter must be an explicit absolute path')
    _outside(python.parent, author_checkout)
    environ = _environment()
    engine = native_codex._engine_identity(python, cwd=root, deadline=deadline, environ=environ, cancel=None)
    _engine_outside_sources(engine, author_checkout=author_checkout, installed_root=root)
    output = _new_output(output, author_checkout)
    started = _now()
    argv = [str(python), '-I', '-B', str(root / 'scripts/factory_demo.py'), *arguments]
    # A durable started receipt prevents a missing final receipt being interpreted
    # as nonexecution. A fresh invocation output is mandatory even for a resume.
    started_record = {'schema_version': 1, 'kind': 'canonical-qualification-invocation-started',
                      'started_at': started, 'run_directory': str(run_dir), 'package_sha256': _sha(package_raw),
                      'entrypoint_sha256': paths['scripts/factory_demo.py'], 'arguments_sha256': _sha(_canonical(arguments)),
                      'engine_identity': engine, 'effect': 'unobserved-until-terminal-receipt'}
    _exclusive(output / 'started.json', _canonical(started_record))
    try:
        result = native_codex._process(argv, cwd=output, stdin=b'', deadline=deadline, limit=16_000_000,
                                       environ=environ)
    except OSError:
        result = {'reason': 'process-launch-error', 'returncode': None, 'stdout': b'', 'stderr': b'', 'spawned': False}
    for name in ('stdout', 'stderr'):
        _exclusive(output / (name + '.bin'), result[name])
    status = ('uncertain-needs-reconciliation' if result['reason'] else
              'native-handoff-required' if result['returncode'] == 3 else 'completed-invocation')
    terminal = None
    if result['reason'] is None and result['returncode'] == 0:
        from .terminal_evidence import verify_terminal
        try:
            terminal = verify_terminal(run_dir)['result']
            native_codex._package(root, package_sha256, plugin_id, plugin_version, time.monotonic() + 120)
            if (terminal['canonical_success'] is not True or terminal['exit_code'] != 0
                    or terminal['identities']['package_sha256'] != package_sha256):
                terminal = None
        except (OSError, ValueError):
            terminal = None
        if terminal is None:
            status = 'terminal-result-not-qualified'
    final = {**started_record, 'kind': 'canonical-qualification-invocation', 'finished_at': _now(),
             'status': status, 'effect': 'canonical-journal-is-authority', 'spawned': result['spawned'],
             'process_exit_code': result['returncode'], 'process_reason': result['reason'],
             'canonical_success': terminal is not None,
             'stdout_sha256': _sha(result['stdout']), 'stderr_sha256': _sha(result['stderr']),
             'automatic_retry': False, 'release_qualified': False}
    _exclusive(output / 'invocation.json', _canonical(final))
    return final


def stage_candidate(*, archive, archive_sha256, package_sha256, plugin_id, plugin_version,
                    author_checkout, output, codex_executable, codex_sha256, codex_version, codex_home):
    """Prepare a hash-checked local marketplace and exact reviewable install plan.

    Installation is deliberately separate from staging. Existing CLI/plugin
    state, approvals and authentication cannot be inferred from an archive.
    """
    _identity(plugin_id, plugin_version)
    if codex_version not in native_codex.CLIENT_VERSIONS or not re.fullmatch(r'[a-f0-9]{64}', codex_sha256):
        raise QualificationError('A supported explicitly pinned local installer is required')
    executable = _safe_path(Path(codex_executable))
    if native_codex._file_digest(executable, time.monotonic() + 60) != codex_sha256:
        raise QualificationError('Local installer bytes differ from the declared client identity')
    archive = _safe_path(Path(archive))
    raw = _read(archive, native_codex.MAX_PACKAGE_BYTES)
    if _sha(raw) != archive_sha256:
        raise QualificationError('Candidate archive identity differs')
    home = _outside(codex_home, author_checkout)
    if home.exists():
        raise QualificationError('An isolated new Codex home must be selected before installing')
    files, total = {}, 0
    with zipfile.ZipFile(io.BytesIO(raw)) as zipped:
        entries = zipped.infolist()
        if len(entries) > native_codex.MAX_PACKAGE_FILES + 1:
            raise QualificationError('Candidate archive contains too many files')
        for entry in entries:
            if not entry.filename.startswith('devsecops/') or entry.is_dir() or entry.flag_bits & 1:
                raise QualificationError('Candidate archive membership is unsupported')
            name = entry.filename[len('devsecops/'):]
            _relative(Path('/candidate'), name)
            if name in files or (entry.external_attr >> 16) & 0o170000 not in (0, 0o100000):
                raise QualificationError('Candidate archive contains duplicate or nonregular members')
            if entry.file_size > MAX_ARTIFACT_BYTES or total + entry.file_size > native_codex.MAX_PACKAGE_BYTES:
                raise QualificationError('Candidate archive exceeds bounded extraction limits')
            files[name] = zipped.read(entry)
            total += len(files[name])
    if 'SHA256SUMS' not in files or _sha(files['SHA256SUMS']) != package_sha256:
        raise QualificationError('Candidate content-manifest identity differs')
    expected = {}
    try:
        for line in files['SHA256SUMS'].decode('ascii').splitlines():
            digest, name = line.split('  ', 1)
            if not re.fullmatch(r'[a-f0-9]{64}', digest) or name in expected or name == 'SHA256SUMS':
                raise ValueError
            expected[name] = digest
        if expected != {name: _sha(value) for name, value in files.items() if name != 'SHA256SUMS'}:
            raise ValueError
    except (ValueError, UnicodeError):
        raise QualificationError('Candidate archive differs from its exact content manifest') from None
    metadata = _json(files.get('.codex-plugin/plugin.json', b'{}'))
    if metadata.get('name') != 'devsecops' or metadata.get('version') != plugin_version:
        raise QualificationError('Candidate metadata differs from the selected plugin version')
    if not {'scripts/factory_demo.py', 'scripts/qualify_release.py', 'src/devsecops/factory/qualification.py'} <= files.keys():
        raise QualificationError('Candidate is missing canonical qualification entry points')
    output = _outside(output, author_checkout)
    if home.is_relative_to(output) or output.is_relative_to(home):
        raise QualificationError('Installer home and staged source must be separate')
    output = _new_output(output, author_checkout)
    for name, content in files.items():
        target = output / 'marketplace/plugins/devsecops' / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _exclusive(target, content)
    marketplace = output / 'marketplace'
    registry = marketplace / '.agents/plugins'
    registry.mkdir(mode=0o700, parents=True)
    _exclusive(registry / 'marketplace.json', _canonical({
        'name': 'devsecops-local', 'interface': {'displayName': 'DevSecOps Local'},
        'plugins': [{'name': 'devsecops', 'source': {'source': 'local', 'path': './plugins/devsecops'},
                     'policy': {'installation': 'AVAILABLE', 'authentication': 'ON_INSTALL'}, 'category': 'Developer Tools'}]}))
    commands = [[str(executable), '--version'],
                [str(executable), 'plugin', 'marketplace', 'add', str(marketplace), '--json'],
                [str(executable), 'plugin', 'add', plugin_id, '--json'],
                [str(executable), 'plugin', 'list', '--json']]
    plan = {'schema_version': 1, 'kind': 'canonical-qualification-install-plan', 'status': 'staged-not-installed',
            'archive_sha256': archive_sha256, 'package_sha256': package_sha256, 'plugin_id': plugin_id,
            'plugin_version': plugin_version, 'client_sha256': codex_sha256, 'client_version': codex_version,
            'environment': {'CODEX_HOME': str(home)}, 'commands': commands,
            'expected_installed_root': str(home / 'plugins/cache/devsecops-local/devsecops' / plugin_version),
            'required_next_steps': ['Review and execute the supported local installer under its required authorization.',
                                   'Verify the actual installed and enabled inventory and exact cache bytes.',
                                   'Prepare a locked dependency interpreter outside the author checkout and installed cache.',
                                   'Configure approved native authentication and explicitly pinned operation inputs.',
                                   'Invoke qualify_release.py run from the installed package; then verify actual run/media evidence.'],
            'release_qualified': False}
    _exclusive(output / 'install-plan.json', _canonical(plan))
    return plan


def main(argv=None):
    parser = argparse.ArgumentParser(prog='qualify-release')
    commands = parser.add_subparsers(dest='command', required=True)
    verify = commands.add_parser('verify', help='Read-only terminal/media consistency and recorded viewer decisions')
    verify.add_argument('--qualification', type=Path, required=True)
    verify.add_argument('--output', type=Path, required=True)
    stage = commands.add_parser('stage-install', help='Prepare candidate archive and exact supported local installer plan')
    for name in ('archive', 'author-checkout', 'output', 'codex-executable', 'codex-home'):
        stage.add_argument('--' + name, type=Path, required=True)
    for name in ('archive-sha256', 'package-sha256', 'plugin-id', 'plugin-version', 'codex-sha256', 'codex-version'):
        stage.add_argument('--' + name, required=True)
    run = commands.add_parser('run', help='Invoke only the selected installed canonical factory_demo.py')
    for name in ('installed-root', 'engine-python', 'author-checkout', 'output'):
        run.add_argument('--' + name, type=Path, required=True)
    for name in ('package-sha256', 'plugin-id', 'plugin-version'):
        run.add_argument('--' + name, required=True)
    run.add_argument('--timeout', type=int, required=True)
    run.add_argument('arguments', nargs=argparse.REMAINDER)
    args = vars(parser.parse_args(argv))
    command = args.pop('command')
    try:
        if command == 'verify':
            report = qualify_and_write(args['qualification'], args['output'])
            result, code = report, report['exit_code']
        elif command == 'stage-install':
            result, code = stage_candidate(**args), 0
        else:
            result = run_canonical(**args)
            code = (3 if result['status'] == 'native-handoff-required' else
                    0 if result['canonical_success'] else 1 if result['process_exit_code'] == 1 else 2)
        print(_canonical(result).decode(), end='')
        return code
    except (OSError, ValueError, KeyError, UnicodeError, zipfile.BadZipFile):
        print(_canonical({'execution_status': 'incomplete', 'release_qualified': False,
                          'message': 'Qualification input, installed capability or immutable evidence is unavailable or invalid.'}).decode(),
              end='', file=sys.stderr)
        return 2

