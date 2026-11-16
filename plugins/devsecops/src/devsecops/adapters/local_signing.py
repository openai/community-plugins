"""Pinned local P-256 Cosign operations without registry, OIDC, TUF or KMS use."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import math
import os
from pathlib import Path
import platform
import re
import secrets
import stat
import tempfile
import time

from devsecops.adapters.http import _json
from devsecops.adapters.signatures import ROOT, VERSION, MAX_BINARY, MAX_PAYLOAD, _bundle, _public_key, _snapshot
from devsecops.adapters.vulnerability_policy import checkpoint
from devsecops.process import run
from devsecops.runtime.cli import _new_directory, _verify_output_path, _write


def _public_der(raw):
    _public_key(raw)
    return base64.b64decode(b''.join(raw.strip().splitlines()[1:-1]), validate=True)


def _private_copy(source, destination, deadline):
    """Only an owned single-link private regular file; never retain its hash."""
    if not isinstance(source, Path) or not source.is_absolute() or '..' in source.parts:
        raise ValueError('An explicit local private key is required')
    directory = os.open(source.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in source.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or before.st_nlink != 1
                or before.st_mode & 0o077 or not 1 <= before.st_size <= 16384):
            raise ValueError('Private key must be an owned, private regular file')
        checkpoint(deadline)
        raw = stream.read(16385)
        after = os.fstat(stream.fileno())
        fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_mode', 'st_uid', 'st_nlink')
        if any(getattr(before, key) != getattr(after, key) for key in fields) or len(raw) != before.st_size:
            raise ValueError('Private key changed during copy')
        if not raw.startswith((b'-----BEGIN ENCRYPTED SIGSTORE PRIVATE KEY-----', b'-----BEGIN ENCRYPTED COSIGN PRIVATE KEY-----')):
            raise ValueError('Only encrypted local Cosign keys are supported')
        checkpoint(deadline)
        _private_write(destination, raw)
        return raw


def _private_write(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


@contextmanager
def _cosign(cosign, deadline):
    with tempfile.TemporaryDirectory(prefix='devsecops-local-signing-') as temporary:
        scratch = Path(temporary).resolve()
        for name in ('home', 'empty-path', 'cache', 'docker', 'tmp'):
            (scratch / name).mkdir(mode=0o700)
        env = {'PATH': str(scratch / 'empty-path'), 'HOME': str(scratch / 'home'),
            'XDG_CACHE_HOME': str(scratch / 'cache'), 'DOCKER_CONFIG': str(scratch / 'docker'), 'TMPDIR': str(scratch / 'tmp')}
        lock_ref = _snapshot(ROOT / 'cosign.lock.json', scratch / 'lock.json', 64000, deadline=deadline)
        lock = _json((scratch / 'lock.json').read_bytes())
        arch = {'aarch64': 'arm64', 'x86_64': 'amd64'}.get(platform.machine(), platform.machine())
        target = platform.system().lower() + '-' + arch
        if lock.get('schema_version') != 1 or lock.get('version') != VERSION or target not in lock.get('tools', {}):
            raise ValueError('Pinned local signer is unavailable for this platform')
        binary = _snapshot(cosign, scratch / 'cosign', MAX_BINARY, deadline=deadline)
        pinned = lock['tools'][target]
        if binary != {'sha256': pinned['sha256'], 'bytes': pinned['bytes']}:
            raise ValueError('Local signer differs from pinned release')
        os.chmod(scratch / 'cosign', 0o500)
        tool = {'name': 'cosign', 'version': VERSION, 'platform': target,
                'binary_sha256': binary['sha256'], 'lock_sha256': lock_ref['sha256']}
        receipts = []
        def command(arguments, operation, *, password=None):
            checkpoint(deadline)
            selected_env = {**env, **({'COSIGN_PASSWORD': password} if password is not None else {})}
            code, out, _, receipt = run([str(scratch / 'cosign'), *arguments], cwd=scratch,
                timeout=deadline-time.monotonic(), limit=64000, env=selected_env)
            # Diagnostics from credential operations are never retained, even as
            # hashes. Receipt fields describe execution only, not secret content.
            receipts.append({'operation': operation, **{key: receipt[key] for key in ('exit_code', 'duration_ms')}})
            checkpoint(deadline)
            if code != 0:
                raise ValueError('Local signing credential or tool operation failed')
            return out
        version = _json(command(['version', '--json'], 'version'))
        if version.get('gitVersion') != 'v' + VERSION:
            raise ValueError('Local signer version differs from selected release')
        yield scratch, tool, receipts, command


def sign_payload(*, payload, public_key, private_key, password_env, cosign, deadline):
    """Sign caller-constructed bounded bytes; returns no trust or release grant."""
    if (not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_PAYLOAD
            or not isinstance(public_key, bytes) or not 1 <= len(public_key) <= 16384
            or not isinstance(password_env, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,127}', password_env)
            or password_env not in os.environ):
        raise ValueError('Bounded payload, public key and explicit password reference are required')
    password = os.environ[password_env]
    if len(password.encode('utf-8')) > 8192 or '\x00' in password:
        raise ValueError('Signing password reference is unsupported')
    expected = _public_der(public_key)
    with _cosign(cosign, deadline) as (scratch, tool, receipts, command):
        _private_copy(private_key, scratch / 'signer.key', deadline)
        derived = command(['public-key', '--key', 'signer.key'], 'derive-public-key', password=password)
        if _public_der(derived) != expected:
            raise ValueError('Private key differs from independently trusted public key')
        _private_write(scratch / 'payload.json', payload)
        command(['sign-blob', '--key', 'signer.key', '--use-signing-config=false', '--tlog-upload=false',
            '--oidc-disable-ambient-providers=true', '--signing-algorithm=ecdsa-sha2-256-nistp256',
            '--bundle', 'bundle.json', '--yes', 'payload.json'], 'sign-blob', password=password)
        # Freeze the actual tool output before parsing; no caller-supplied bundle.
        _snapshot(scratch / 'bundle.json', scratch / 'frozen-bundle.json', MAX_PAYLOAD, deadline=deadline)
        bundle = (scratch / 'frozen-bundle.json').read_bytes()
        if base64.b64decode(_bundle(bundle)).hex() != hashlib.sha256(payload).hexdigest():
            raise ValueError('Generated bundle does not bind signed payload')
        checkpoint(deadline)
        return bundle, tool, receipts


def generate_lab_key(*, cosign, output, timeout=30):
    """Explicit lab credential setup; does not create or approve a trust policy."""
    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 120
            or not isinstance(output, Path) or not output.is_absolute() or '..' in output.parts):
        raise ValueError('A new private credential directory and bounded timeout are required')
    deadline = time.monotonic() + timeout
    password = secrets.token_urlsafe(32)
    with _cosign(cosign, deadline) as (scratch, tool, receipts, command):
        command(['generate-key-pair', '--output-key-prefix', 'application'], 'generate-owned-lab-key', password=password)
        key, public = scratch / 'application.key', scratch / 'application.pub'
        _snapshot(public, scratch / 'frozen-public.pem', 16384, deadline=deadline)
        public = scratch / 'frozen-public.pem'
        public_bytes = public.read_bytes()
        _public_key(public_bytes)
        derived = command(['public-key', '--key', 'application.key'], 'confirm-generated-key-pair', password=password)
        if _public_der(derived) != _public_der(public_bytes):
            raise ValueError('Generated private/public key pair differs')
        with _new_directory(output) as directory:
            def route():
                checkpoint(deadline)
                _verify_output_path(output, directory)
            route()
            private_bytes = _private_copy(key, output / 'application.key', deadline)
            public_ref = _snapshot(public, output / 'application.pub', 16384, deadline=deadline)
            os.chmod(output / 'application.pub', 0o600)
            _private_write(output / 'password.txt', password.encode('utf-8'))
            def audit():
                route()
                for name, expected in (('application.key', private_bytes), ('application.pub', public_bytes),
                                       ('password.txt', password.encode('utf-8'))):
                    checkpoint(deadline)
                    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                    with os.fdopen(fd, 'rb') as stream:
                        before = os.fstat(stream.fileno())
                        if (not stat.S_ISREG(before.st_mode) or before.st_size != len(expected)
                                or before.st_uid != os.getuid() or before.st_nlink != 1 or before.st_mode & 0o777 != 0o600):
                            raise ValueError('Retained credential ownership or size changed')
                        raw = stream.read(len(expected)+1)
                        after = os.fstat(stream.fileno())
                        fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_mode', 'st_uid', 'st_nlink')
                        if not secrets.compare_digest(raw, expected) or any(getattr(before, item) != getattr(after, item) for item in fields):
                            raise ValueError('Retained credential bytes changed')
                route()
            report = {'schema_version': 1, 'kind': 'owned-lab-application-key', 'scope': 'local-credential-generation-only',
                'private_key': 'application.key', 'password': 'password.txt', 'public_key': 'application.pub',
                'public_key_sha256': public_ref['sha256'], 'algorithm': 'ecdsa-sha2-256-nistp256',
                'tool': tool, 'receipts': receipts, 'trust_policy': 'not-selected', 'publisher_authority': 'not-claimed'}
            audit()
            try:
                _write(directory, 'retrieval.json', report)
                os.fsync(directory)
                audit()
            except BaseException:
                try:
                    os.unlink('retrieval.json', dir_fd=directory)
                except FileNotFoundError:
                    pass
                os.fsync(directory)
                raise
            return report
