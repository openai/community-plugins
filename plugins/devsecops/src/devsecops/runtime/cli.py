"""Explicit operator entry points with private, immutable artifact directories."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

from devsecops.factory.contracts import read_json
from .context import MAX_BUNDLE, canonical_bytes, freeze_runtime_context
from .collection import start_runtime_collection, finish_runtime_collection
from .presentation import render_html, render_markdown


def add_commands(commands):
    collect = commands.add_parser('collect-runtime', help='Begin a bounded read-only runtime observation; save its fixed window')
    collect.add_argument('--config', required=True, type=Path, help='Trusted external operator config, never candidate-controlled')
    collect.add_argument('--lock', required=True, type=Path)
    collect.add_argument('--intent', required=True, type=Path, help='Trusted scoped runtime intent bound to config and lock bytes')
    collect.add_argument('--receipt', action='append', default=[], metavar='ID=PATH', help='Explicit receipt document; repeat per ID')
    collect.add_argument('--delivery-binding', type=Path, help='Delivery binding for recovery observations; not authentication by itself')
    resume = commands.add_parser('resume-runtime', help='Resume the original observation window without waiting or broadening scope')
    resume.add_argument('--context', required=True, type=Path, help='Original operator-created runtime-context.json')
    resume.add_argument('--pending', required=True, type=Path, help='Original pending runtime-collection.json')
    resume.add_argument('--config', required=True, type=Path, help='Separately trusted operator config; never taken from saved evidence')
    resume.add_argument('--lock', required=True, type=Path, help='Original separately trusted release lock')
    resume.add_argument('--intent', required=True, type=Path, help='Original separately trusted scoped intent')
    resume.add_argument('--delivery-binding', type=Path, help='Explicit original delivery binding when the pending collection used one')
    diagnose = commands.add_parser('diagnose-gitops', help='Compare recorded baseline/fault evidence offline; rank grounded alternatives')
    diagnose.add_argument('--baseline', type=Path, help='Baseline runtime collection; omission remains an evidence gap')
    diagnose.add_argument('--fault', required=True, type=Path)
    recovery = commands.add_parser('verify-gitops-recovery', help='Check independent recorded recovery conditions; missing source authentication remains unknown')
    recovery.add_argument('--baseline', required=True, type=Path)
    recovery.add_argument('--fault', required=True, type=Path)
    recovery.add_argument('--recovered', required=True, type=Path, help='Independent post-delivery runtime collection')
    recovery.add_argument('--contract', required=True, type=Path, help='Explicit approved recovery contract, never inferred from evidence')
    recovery.add_argument('--evidence', action='append', default=[], metavar='NAME=PATH', help='Explicit independent artifact; repeat for closed names and http:CASE-ID')
    for command in (collect, resume, diagnose, recovery):
        command.add_argument('--output', required=True, type=Path, help='New private artifact directory; existing paths are never overwritten')


@contextmanager
def _new_directory(path):
    """Use directory descriptors so ancestor replacement cannot redirect writes."""
    path = Path(os.path.abspath(path))
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent = os.open(path.anchor, flags)
    try:
        for index, component in enumerate(path.parts[1:]):
            try:
                os.mkdir(component, 0o700, dir_fd=parent)
            except FileExistsError:
                if index == len(path.parts) - 2:
                    raise ValueError('Output directory already exists; select a new run directory') from None
            child = os.open(component, flags, dir_fd=parent)
            os.close(parent)
            parent = child
        if len(path.parts) == 1:
            raise ValueError('Output must be a new directory')
        os.fchmod(parent, 0o700)
        yield parent
    finally:
        os.close(parent)


def _write(directory, name, value):
    raw = value.encode('utf-8') if isinstance(value, str) else canonical_bytes(value)
    if len(raw) > MAX_BUNDLE:
        raise ValueError('Output artifact exceeds its byte bound')
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    with os.fdopen(fd, 'wb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}


def _verify_output_path(path, directory):
    current = os.open(Path(path.absolute()).anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in Path(os.path.abspath(path)).parts[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = child
        original, actual = os.fstat(directory), os.fstat(current)
        if (original.st_dev, original.st_ino) != (actual.st_dev, actual.st_ino):
            raise ValueError('Output directory identity changed during collection')
    finally:
        os.close(current)


def _load(path, inputs, name):
    value, sha = read_json(path, limit=MAX_BUNDLE)
    inputs[name] = {'sha256': sha, 'encoding': 'raw-json', 'path': str(path.absolute()), 'authentication': 'not-verified'}
    return value


def _receipts(arguments):
    if len(arguments) > 300:
        raise ValueError('Too many receipt references')
    result = {}
    for argument in arguments:
        identifier, separator, path = argument.partition('=')
        if not separator or not re.fullmatch(r'[a-z][a-z0-9-]{0,62}', identifier) or not path or identifier in result:
            raise ValueError('Receipt arguments must have unique valid IDs and explicit paths')
        result[identifier] = Path(path)
    return result


def _recovery_evidence(arguments):
    from .recovery import BASE_ARTIFACTS
    if len(arguments) > 44:
        raise ValueError('Too many recovery artifacts')
    result = {}
    for argument in arguments:
        name, separator, path = argument.partition('=')
        if (not separator or not path or name in result or
                name not in BASE_ARTIFACTS and not re.fullmatch(r'http:[a-z0-9][a-z0-9-]{0,62}', name)):
            raise ValueError('Recovery evidence requires unique closed names and explicit paths')
        result[name] = Path(path)
    return result


def _execute(args, inputs, save):
    if args.command == 'verify-gitops-recovery':
        from .recovery import verify_recovery
        result = verify_recovery(args.baseline, args.fault, args.recovered,
                                 contract=args.contract, evidence=_recovery_evidence(args.evidence))
        inputs.update({name: {**artifact, 'authentication': 'not-verified'}
                       for name, artifact in result['inputs'].items()})
        return result, 'recovery.json'
    if args.command == 'collect-runtime':
        context = freeze_runtime_context(args.config, args.lock, args.intent, receipt_documents=_receipts(args.receipt))
        inputs.update({key: {**artifact, 'authentication': 'not-verified'} for key, artifact in context['input_artifacts'].items()})
        save('runtime-context.json', context)
        result = start_runtime_collection(context, authority={'config': args.config, 'lock': args.lock, 'intent': args.intent},
                                          delivery_receipt=args.delivery_binding)
        if result.get('delivery'):
            inputs['delivery'] = {**result['delivery']['artifact'], 'authentication': 'not-verified'}
        return result, 'runtime-collection.json'
    if args.command == 'resume-runtime':
        context = _load(args.context, inputs, 'context')
        pending = _load(args.pending, inputs, 'pending')
        from .context import verify_context
        verify_context(context)
        save('runtime-context.json', context)
        result = finish_runtime_collection(context, pending,
            authority={'config': args.config, 'lock': args.lock, 'intent': args.intent}, delivery_receipt=args.delivery_binding)
        for name in ('config', 'lock', 'intent'):
            inputs['operator_' + name] = {**context['input_artifacts'][name],
                'path': str(getattr(args, name).absolute()), 'authentication': 'not-verified',
                'read_authority': 'explicit-operator-input'}
        if result.get('delivery'):
            inputs['delivery'] = {**result['delivery']['artifact'], 'authentication': 'not-verified'}
        return result, 'runtime-collection.json'
    from .diagnosis import diagnose_runtime
    # Diagnosis owns bounded loading and preserves missing/invalid inputs as gaps.
    # It never reopens the historical paths embedded in a collection document.
    result = diagnose_runtime(args.baseline, args.fault)
    inputs.update({name: {**artifact, 'authentication': 'not-verified'}
                   for name, artifact in result['inputs'].items()})
    return result, 'diagnosis.json'


def run(args):
    """Independent read/evidence commands never return native-handoff exit 3."""
    inputs = {}
    with _new_directory(args.output) as directory:
        artifact_receipts = {}
        def save(name, value):
            artifact_receipts[name] = _write(directory, name, value)
        try:
            result, structured_name = _execute(args, inputs, save)
        except (ValueError, OSError, UnicodeError, RecursionError, KeyboardInterrupt) as error:
            canceled = isinstance(error, KeyboardInterrupt)
            result = {
                'schema_version': 1, 'kind': 'runtime-command-error', 'execution_status': 'error',
                'decision': 'unknown', 'confirmed_failure': False,
                'observed_at': datetime.now(timezone.utc).isoformat(),
                'gaps': [{'code': 'command-canceled' if canceled else 'runtime-input-unavailable-or-invalid', 'required': True}],
            }
            structured_name = 'runtime-error.json'
        artifacts = {structured_name: result, 'report.html': render_html(result, structured_name),
                     'report.md': render_markdown(result, structured_name)}
        decision = result.get('decision', 'unknown')
        manifest = {'schema_version': 1, 'kind': 'runtime-command-manifest', 'command': args.command,
                    'finished': True, 'finished_at': datetime.now(timezone.utc).isoformat(),
                    'decision': decision, 'execution_status': result['execution_status'],
                    'input_authentication': 'not-verified', 'inputs': inputs, 'artifacts': artifact_receipts}
        for name, value in artifacts.items():
            save(name, value)
        _verify_output_path(args.output, directory)
        # A crash before this last durable write leaves an incomplete artifact set.
        _write(directory, 'runtime-manifest.json', manifest)
        os.fsync(directory)
        try:
            _verify_output_path(args.output, directory)
        except (OSError, ValueError):
            # Withdraw only this invocation's just-created terminal marker.
            os.unlink('runtime-manifest.json', dir_fd=directory)
            os.fsync(directory)
            raise
        window = result.get('window') or {}
        print(json.dumps({'decision': decision, 'execution_status': result['execution_status'],
                      'execution_mode': result.get('execution_mode'),
                      'stage': result.get('stage'), 'confirmed_failure': result['confirmed_failure'],
                      'recovery': result.get('recovery', 'unknown'),
                      'next_eligible_at': window.get('next_eligible_at'), 'expires_at': window.get('expires_at'),
                      'report': str(args.output.absolute() / 'report.html'),
                      'structured_report': str(args.output.absolute() / structured_name),
                      'manifest': str(args.output.absolute() / 'runtime-manifest.json')}))
    return {'pass': 0, 'fail': 1}.get(decision, 2)
