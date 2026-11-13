"""Retain an already provisioned, owned RKE2 guest across native handoffs.

This operator driver does not install Big Bang, acquire images, or authenticate a
canonical scenario. ``up``, ``reconcile`` and ``down`` return measured receipts;
the lifecycle caller still owns its environment lock and mutation journal. A
timeout leaves an unresolved intent: reconcile it before another mutation.

The same stdlib-only module runs over strictly verified SSH on the Linux host.
The workstation alone parses its privately retrieved kubeconfig and performs
CA-verified, client-certificate-authenticated API reads through an owned tunnel.
"""
from __future__ import annotations

import base64
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import selectors
import shlex
import shutil
import signal
import socket
import ssl
import stat
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

IDENTITIES = frozenset({'scenario_sha256', 'assertions_sha256', 'package_sha256',
                       'lock_sha256', 'config_sha256', 'environment_sha256'})
LABEL = 'io.devsecops.tcg.run'
BINDING_LABEL = 'io.devsecops.tcg.binding'
OPERATION_LABEL = 'io.devsecops.tcg.operation'
LIMIT = 2_000_000
VERSION = 'v1.36.5+rke2r1'


class TCGError(RuntimeError):
    """Sanitized driver failure; a failed mutation may require reconciliation."""


class TCGAPIError(TCGError):
    """Carry sanitized failed transport receipts into the driver observation."""

    def __init__(self, observation):
        super().__init__('Pinned API transport or identity verification failed')
        self.observation = observation


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def _json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise TCGError('Duplicate JSON key')
            value[key] = item
        return value
    if len(raw) > LIMIT:
        raise TCGError('Document exceeds the size limit')
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(TCGError('Nonfinite JSON')))
    except (ValueError, UnicodeError, RecursionError) as error:
        raise TCGError('Invalid bounded JSON') from error


def _path(value):
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise TCGError('An absolute, nonsymlink path is required')
    return path


def _private(path, *, create=False):
    path = _path(path)
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise TCGError('Operator directory must be private and owned')
    return path


def _read(path, limit=LIMIT):
    fd = os.open(_path(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                or before.st_mode & 0o077 or before.st_nlink != 1 or before.st_size > limit):
            raise TCGError('Operator file must be private, owned and bounded')
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        if len(raw) > limit or (before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_ino, after.st_size, after.st_mtime_ns):
            raise TCGError('Operator file changed while being read')
        return raw


def _write(path, raw, *, replace=False):
    path = _path(path)
    temporary = path.parent / ('.' + path.name + '-' + uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            if path.exists():
                _read(path)
            os.replace(temporary, path)
        else:
            os.link(temporary, path, follow_symlinks=False)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def _now():
    return datetime.now(UTC).isoformat()


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}', value):
        raise TCGError('Invalid operation or environment identifier')
    return value


def _lock_descriptor(path):
    fd = os.open(_path(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077 or info.st_nlink != 1):
        os.close(fd)
        raise TCGError('Environment lock must be a private owned regular file')
    return fd


@dataclass(frozen=True)
class TCGSpec:
    run_id: str
    environment_id: str
    identities: dict
    host: str
    remote_lab: str
    tool_image: str
    original_ca_sha256: str
    provision_run_id: str
    source_receipt_sha256: str
    transport: dict = field(default_factory=dict)

    def validate(self):
        for value in (self.run_id, self.environment_id, self.host, self.provision_run_id):
            _id(value)
        if (set(self.identities) != IDENTITIES or any(not isinstance(v, str) or
                not re.fullmatch('[0-9a-f]{64}', v) for v in self.identities.values())):
            raise TCGError('All six immutable input identities are required')
        if not re.fullmatch(r'/home/[A-Za-z0-9_-]+/\.local/share/devsecops/labs/[A-Za-z0-9._-]+', self.remote_lab):
            raise TCGError('Retained guest must use an explicit private lab path')
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', self.tool_image):
            raise TCGError('Tool image must use an immutable image ID')
        for value in (self.original_ca_sha256, self.source_receipt_sha256):
            if not re.fullmatch('[0-9a-f]{64}', value):
                raise TCGError('Original CA and provision receipt hashes are required')
        expected = {'config', 'known_hosts', 'certificate', 'identity', 'proxy', 'identity_agent',
                    'host_key_sha256', 'verification_basis'}
        if 'agent_protocol' in self.transport:
            expected.add('agent_protocol')
        if self.transport.get('agent_protocol', 'native') not in ('native', 'certificate-view-v1'):
            raise TCGError('Unsupported explicit SSH agent protocol')
        if set(self.transport) != expected or self.transport['verification_basis'] not in (
                'authenticated-control-plane', 'operator-verified'):
            raise TCGError('An independently verified, immutable SSH transport is required')
        for name in ('config', 'known_hosts', 'certificate', 'identity', 'proxy'):
            item = self.transport[name]
            if (not isinstance(item, dict) or set(item) != {'path', 'sha256'}
                    or not isinstance(item['path'], str) or not item['path'].startswith('/')
                    or not re.fullmatch('[0-9a-f]{64}', item['sha256'])):
                raise TCGError('Invalid pinned SSH transport artifact')
        if (not isinstance(self.transport['identity_agent'], str) or not self.transport['identity_agent'].startswith('/')
                or not re.fullmatch('[0-9a-f]{64}', self.transport['host_key_sha256'])):
            raise TCGError('Invalid explicit SSH agent or host identity')
        return self

    @property
    def digest(self):
        self.validate()
        return _sha(_canonical(asdict(self)))


@dataclass(frozen=True)
class TCGObservation:
    state: str
    receipt: Path
    receipt_sha256: str
    mutation_effect: str


def _run(argv, deadline, *, check=True, data=None, timeout_ok=False):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TCGError('Fixed operation deadline expired')
    # New process group allows bounded cancellation of our subprocess and its
    # helper children, never an unrelated SSH/QEMU process.
    process = subprocess.Popen(argv, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    selector = selectors.DefaultSelector()
    output = {'stdout': bytearray(), 'stderr': bytearray()}
    for name in ('stdout', 'stderr'):
        stream = getattr(process, name)
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ, name)
    offset = 0
    if data is not None:
        os.set_blocking(process.stdin.fileno(), False)
        selector.register(process.stdin, selectors.EVENT_WRITE, 'stdin')
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(argv, 0)
            for key, _ in selector.select(min(remaining, .2)):
                if key.data == 'stdin':
                    try:
                        offset += os.write(key.fd, data[offset:offset + 65536])
                    except (BlockingIOError, InterruptedError):
                        continue
                    except BrokenPipeError:
                        offset = len(data)
                    if offset == len(data):
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                else:
                    try:
                        chunk = os.read(key.fd, 65536)
                    except (BlockingIOError, InterruptedError):
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                    elif len(output[key.data]) + len(chunk) > LIMIT:
                        raise TCGError('Command output exceeds the bound')
                    else:
                        output[key.data].extend(chunk)
        process.wait(timeout=max(.001, deadline - time.monotonic()))
        if time.monotonic() > deadline:
            raise subprocess.TimeoutExpired(argv, 0)
        stdout, stderr = bytes(output['stdout']), bytes(output['stderr'])
    except BaseException as error:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=3)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass
        finally:
            # A helper can outlive its leader and retain the pipes while
            # ignoring SIGTERM. Reaping the leader alone does not cancel the
            # process group that this call created.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=3)
        if timeout_ok and isinstance(error, subprocess.TimeoutExpired):
            return subprocess.CompletedProcess(argv, 124, bytes(output['stdout']), bytes(output['stderr']))
        if isinstance(error, TCGError):
            raise
        raise TCGError('Bounded command interrupted; reconcile before retrying') from None
    finally:
        selector.close()
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()
    if len(stdout) > LIMIT or len(stderr) > LIMIT:
        raise TCGError('Command output exceeds the bound')
    if check and process.returncode:
        raise TCGError('Bounded command failed; external effect may require reconciliation')
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def _public_read(path, limit=64_000_000):
    fd = os.open(_path(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_size > limit or before.st_mode & 0o022
                or before.st_uid not in (0, os.getuid())):
            raise TCGError('Transport source must be an owned bounded regular file')
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        if len(raw) > limit or (before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_ino, after.st_size, after.st_mtime_ns):
            raise TCGError('Transport source changed while being read')
        return raw


def _certificate_identity(raw):
    """Extract only the public key needed to select its existing agent signer.

    This is not certificate validation or grant creation. OpenSSH and the
    server validate the original certificate, its signer, scope and lifetime.
    """
    try:
        fields = raw.strip().split()
        blob = base64.b64decode(fields[1], validate=True)
        offset = 0
        def string():
            nonlocal offset
            if len(blob) - offset < 4:
                raise ValueError
            size = struct.unpack('>I', blob[offset:offset + 4])[0]
            offset += 4
            if size > LIMIT or len(blob) - offset < size:
                raise ValueError
            value = blob[offset:offset + size]
            offset += size
            return value
        algorithm = string()
        allowed = {b'ssh-ed25519': 1, b'ssh-rsa': 2,
                   b'ecdsa-sha2-nistp256': 2, b'ecdsa-sha2-nistp384': 2, b'ecdsa-sha2-nistp521': 2}
        suffix = b'-cert-v01@openssh.com'
        if algorithm != fields[0] or not algorithm.endswith(suffix) or algorithm[:-len(suffix)] not in allowed:
            raise ValueError
        public_algorithm = algorithm[:-len(suffix)]
        string()  # Certificate nonce is not part of the public key.
        values = [public_algorithm, *(string() for _ in range(allowed[public_algorithm]))]
        encoded = b''.join(struct.pack('>I', len(value)) + value for value in values)
        return public_algorithm + b' ' + base64.b64encode(encoded) + b'\n'
    except (ValueError, IndexError, struct.error):
        raise TCGError('Unsupported or malformed explicit SSH client certificate') from None


def _certificate_grant(raw):
    """Read the OpenSSH certificate envelope; the pinned SSH server validates it.

    Only nonce, serial, validity and signature may change during renewal. The
    complete grant and issuer remain byte-identical, including unknown options.
    """
    try:
        identity = _certificate_identity(raw)
        fields = raw.strip().split(None, 2)
        if len(raw) > LIMIT or len(fields) not in (2, 3) or len(raw.strip().splitlines()) != 1:
            raise ValueError
        blob = base64.b64decode(fields[1], validate=True)
        offset = 0

        def take(size):
            nonlocal offset
            if size > LIMIT or size > len(blob) - offset:
                raise ValueError
            value = blob[offset:offset + size]
            offset += size
            return value

        def number(format_, size):
            return struct.unpack(format_, take(size))[0]

        def string():
            return take(number('>I', 4))

        algorithm = string()
        string()  # CA nonce.
        public_count = 1 if algorithm.startswith(b'ssh-ed25519-') else 2
        for _ in range(public_count):
            string()
        serial = number('>Q', 8)
        certificate_type = number('>I', 4)
        key_id, principals = string(), string()
        valid_after, valid_before = number('>Q', 8), number('>Q', 8)
        critical, extensions, reserved, issuer, signature = (string() for _ in range(5))
        if offset != len(blob) or certificate_type != 1 or not issuer or not signature or valid_after >= valid_before:
            raise ValueError
        grant = [identity, struct.pack('>I', certificate_type), key_id, principals, critical,
                 extensions, reserved, issuer]
        digest = _sha(b''.join(struct.pack('>I', len(value)) + value for value in grant))
        return {'grant_sha256': digest, 'identity_sha256': _sha(identity), 'serial': serial,
                'valid_after': valid_after, 'valid_before': valid_before, 'algorithm': algorithm.decode('ascii')}
    except (ValueError, IndexError, struct.error, UnicodeError):
        raise TCGError('Unsupported or malformed SSH certificate grant') from None


def _renewed_transport(original, directory, raw):
    """Copy only issued certificate bytes and its one explicit config reference."""
    _ssh(type('PinnedTransport', (), {'transport': original})())
    directory = _private(directory, create=True)
    cert = directory / 'client-certificate.pub'
    config = directory / 'ssh-config'
    for path in (cert, config):
        if any(character in str(path) for character in ('\n', '\r', '"', '\\', '%')):
            raise TCGError('Renewal path contains unsupported metacharacters')
    original_config = _read(original['config']['path'])
    old = ('  CertificateFile "' + original['certificate']['path'] + '"\n').encode()
    if original_config.count(old) != 1:
        raise TCGError('Original certificate reference is not the generated explicit form')
    replacement = ('  CertificateFile "' + str(cert) + '"\n').encode()
    _write(cert, raw)
    _write(config, original_config.replace(old, replacement))
    return {**original, 'certificate': {'path': str(cert), 'sha256': _sha(raw)},
            'config': {'path': str(config), 'sha256': _sha(_read(config))}}


def create_transport_snapshot(directory, *, hostname, user, host_public_key, host_key_sha256,
                              proxy_executable, proxy_arguments, certificate_file, identity_agent,
                              verification_basis, agent_protocol='native'):
    """Freeze a human-approved, independently authenticated devbox SSH route.

    No host key is discovered by ssh-keyscan, a failed connection, or TOFU. The
    caller supplies the independently retrieved public key and its provenance.
    The existing client key remains in the explicitly selected agent socket.
    """
    if agent_protocol not in ('native', 'certificate-view-v1'):
        raise TCGError('Unsupported explicit SSH agent protocol')
    directory = _private(directory, create=True)
    if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', hostname)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', user)
            or verification_basis not in ('authenticated-control-plane', 'operator-verified')):
        raise TCGError('Invalid approved SSH route')
    public = _public_read(host_public_key, LIMIT).strip().split()
    if len(public) not in (2, 3) or public[0] != b'ssh-ed25519':
        raise TCGError('Expected the independently obtained Ed25519 server public key')
    blob = base64.b64decode(public[1], validate=True)
    if _sha(blob) != host_key_sha256:
        raise TCGError('Server host key does not match independent provenance')
    proxy = _path(proxy_executable)
    agent = _path(identity_agent)
    if not stat.S_ISSOCK(agent.stat().st_mode) or agent.stat().st_uid != os.getuid():
        raise TCGError('Explicit identity agent is not an owned Unix socket')
    if (not isinstance(proxy_arguments, tuple) or len(proxy_arguments) > 16 or
            any(not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_./:%-]{1,256}', value)
                for value in proxy_arguments)):
        raise TCGError('Proxy arguments must be an explicit bounded approved route')
    known = directory / 'known-hosts'
    cert = directory / 'client-certificate.pub'
    identity = directory / 'client-identity.pub'
    config = directory / 'ssh-config'
    certificate = _public_read(certificate_file, LIMIT)
    public_identity = _certificate_identity(certificate)
    _write(known, hostname.encode() + b' ' + b' '.join(public[:2]) + b'\n')
    _write(cert, certificate)
    _write(identity, public_identity)
    # The generated config has no Include, Match, LocalCommand, forwarding,
    # environment-supplied proxy, or ambient identity configuration.
    def quoted(value):
        if any(c in str(value) for c in ('\n', '\r', '"', '\\', '%')):
            raise TCGError('SSH path contains unsupported metacharacters')
        return '"' + str(value) + '"'
    text = '\n'.join([
        'Host retained', '  HostName ' + hostname, '  User ' + user, '  Port 22',
        '  IdentityFile ' + quoted(identity), '  IdentitiesOnly yes', '  IdentityAgent ' + quoted(agent),
        '  CertificateFile ' + quoted(cert), '  UserKnownHostsFile ' + quoted(known),
        '  GlobalKnownHostsFile /dev/null', '  HostKeyAlgorithms ssh-ed25519',
        '  StrictHostKeyChecking yes', '  UpdateHostKeys no', '  VerifyHostKeyDNS no',
        '  ForwardAgent no', '  PermitLocalCommand no', '  RequestTTY no',
        '  ControlPersist no', '  ProxyCommand ' + shlex.join([str(proxy), *proxy_arguments]), '',
    ])
    _write(config, text.encode())
    return {'config': {'path': str(config), 'sha256': _sha(_read(config))},
            'known_hosts': {'path': str(known), 'sha256': _sha(_read(known))},
            'certificate': {'path': str(cert), 'sha256': _sha(_read(cert))},
            'identity': {'path': str(identity), 'sha256': _sha(_read(identity))},
            'proxy': {'path': str(proxy), 'sha256': _sha(_public_read(proxy))},
            'identity_agent': str(agent), 'host_key_sha256': host_key_sha256,
            'verification_basis': verification_basis, 'agent_protocol': agent_protocol}


def _ssh(spec, *, control=None, forwarding=False):
    for name in ('config', 'known_hosts', 'certificate', 'identity', 'proxy'):
        item = spec.transport[name]
        raw = _public_read(item['path']) if name == 'proxy' else _read(item['path'])
        if _sha(raw) != item['sha256']:
            raise TCGError('Pinned SSH transport changed')
    agent = _path(spec.transport['identity_agent']).stat()
    if not stat.S_ISSOCK(agent.st_mode) or agent.st_uid != os.getuid():
        raise TCGError('Pinned identity agent changed')
    certificate = _read(spec.transport['certificate']['path'])
    _certificate_identity(certificate)
    algorithm = certificate.split()[0].decode('ascii')
    if algorithm == 'ssh-rsa-cert-v01@openssh.com':
        algorithm = 'rsa-sha2-512-cert-v01@openssh.com,rsa-sha2-256-cert-v01@openssh.com'
    return ['ssh', '-F', spec.transport['config']['path'], '-o', 'BatchMode=yes',
            '-o', 'ForwardAgent=no', '-o', 'PermitLocalCommand=no', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'UpdateHostKeys=no', '-o', 'ConnectTimeout=10',
            '-o', 'PreferredAuthentications=publickey', '-o', 'PasswordAuthentication=no',
            '-o', 'KbdInteractiveAuthentication=no', '-o', 'GSSAPIAuthentication=no',
            '-o', 'HostbasedAuthentication=no', '-o', 'PubkeyAcceptedAlgorithms=' + algorithm,
            '-o', 'ClearAllForwardings=' + ('no' if forwarding else 'yes'),
            '-o', 'ControlMaster=' + ('yes' if forwarding else 'no'),
            '-S', str(control) if control else 'none', 'retained']


@contextmanager
def _ssh_context(spec, deadline, audit_directory, *, control=None, forwarding=False, control_only=False):
    """Use an explicit per-command certificate view, never an auth fallback.

    The original snapshot and owner remain unchanged. Temporary configuration
    selects only its issued certificate; the existing key stays in the agent.
    Authentication is limited to 40 seconds even for a longer remote action.
    Local control commands cannot initiate public-key authentication.
    """
    argv = _ssh(spec)
    mode = spec.transport.get('agent_protocol', 'native')
    if mode not in ('native', 'certificate-view-v1'):
        raise TCGError('Unsupported explicit SSH agent protocol')
    if control_only:
        if control is None or forwarding:
            raise TCGError('Local SSH control requires an existing owned control path')
        argv = _ssh(spec, control=control)
        argv[1:1] = ['-o', 'PubkeyAuthentication=no']
        yield argv
        return
    if mode == 'native':
        yield _ssh(spec, control=control, forwarding=forwarding)
        return
    from devsecops.factory.certificate_agent import CertificateAgent

    audit_directory = _private(audit_directory)
    certificate = _read(spec.transport['certificate']['path'])
    identity = _read(spec.transport['identity']['path'])
    known = _read(spec.transport['known_hosts']['path']).split()
    if len(known) != 3 or known[1] != b'ssh-ed25519':
        raise TCGError('Certificate view requires the single pinned Ed25519 host')
    host_key = base64.b64decode(known[2], validate=True)
    if _sha(host_key) != spec.transport['host_key_sha256']:
        raise TCGError('Certificate view host identity changed')
    original = _read(spec.transport['config']['path'])
    users = re.findall(rb'^  User ([A-Za-z0-9][A-Za-z0-9_-]{0,63})$', original, re.MULTILINE)
    hosts = re.findall(rb'^  HostName ([A-Za-z0-9][A-Za-z0-9.-]{0,252})$', original, re.MULTILINE)
    if len(users) != 1 or hosts != [known[0]]:
        raise TCGError('Certificate view requires the generated explicit host/user configuration')
    handshake_deadline = min(deadline, time.monotonic() + 40)
    if handshake_deadline <= time.monotonic():
        raise TCGError('Certificate command deadline expired')
    record = {'kind': 'retained-tcg-certificate-agent', 'started_at': _now(),
              'agent_protocol': mode, 'certificate_sha256': _sha(certificate),
              'identity_sha256': _sha(identity), 'host_key_sha256': _sha(host_key),
              'certificate_only': True, 'status': 'failed'}
    adapter = None
    try:
        with CertificateAgent(certificate, identity, spec.transport['identity_agent'],
                              host_key, users[0].decode(), handshake_deadline) as adapter:
            config = adapter.directory / 'ssh-config'
            text = original
            for old, new in (
                ('  IdentityFile "' + spec.transport['identity']['path'] + '"\n',
                 '  IdentityFile "' + spec.transport['certificate']['path'] + '"\n'),
                ('  CertificateFile "' + spec.transport['certificate']['path'] + '"\n', ''),
                ('  IdentityAgent "' + spec.transport['identity_agent'] + '"\n',
                 '  IdentityAgent "' + str(adapter.path) + '"\n'),
            ):
                if text.count(old.encode()) != 1:
                    raise TCGError('Certificate view config differs from the generated explicit form')
                text = text.replace(old.encode(), new.encode())
            _write(config, text)
            try:
                transport = {**spec.transport, 'config': {'path': str(config), 'sha256': _sha(text)},
                             'identity_agent': str(adapter.path)}
                argv = _ssh(replace(spec, transport=transport), control=control, forwarding=forwarding)
                argv[1:1] = ['-o', 'CertificateFile=none', '-o', 'PubkeyAuthentication=host-bound']
                yield argv
                if adapter.audit['signatures'] != 1:
                    raise TCGError('Certificate command lacks its one observed host-bound signature')
                record['status'] = 'command_completed'
            finally:
                if _read(config) != text:
                    raise TCGError('Temporary certificate configuration changed')
                config.unlink()
    except (ValueError, OSError) as error:
        raise TCGError('Scoped certificate agent failed') from error
    finally:
        record.update(finished_at=_now(), agent_audit=dict(adapter.audit) if adapter else None,
                      temporary_agent_removed=bool(adapter and adapter.directory and not adapter.directory.exists()))
        _write(audit_directory / ('ssh-agent-' + uuid4().hex + '.json'), _canonical(record))


def _resource(info, spec, *, network=False):
    labels = info.get('Labels', {}) if network else info.get('Config', {}).get('Labels', {})
    if (labels.get(LABEL) != spec.run_id or labels.get(BINDING_LABEL) != spec.digest
            or not re.fullmatch('[0-9a-f]{64}', info.get('Id', ''))):
        raise TCGError('Resource does not match exact ownership and immutable binding')
    _id(labels.get(OPERATION_LABEL))
    return info['Id']


def _qemu():
    return ['qemu-system-x86_64', '-machine', 'q35,dump-guest-core=off', '-accel', 'tcg,thread=multi',
            '-cpu', 'max', '-smp', '8', '-m', '32768', '-display', 'none', '-monitor', 'none',
            '-serial', 'file:/lab/retained/serial.log',
            '-qmp', 'unix:/lab/retained/qmp.sock,server=on,wait=off',
            '-drive', 'file=/lab/probe.qcow2,if=virtio,format=qcow2',
            '-drive', 'file=/lab/seed.iso,format=raw,media=cdrom,readonly=on',
            '-netdev', 'user,id=net0,restrict=on,hostfwd=tcp:0.0.0.0:2222-:22,hostfwd=tcp:0.0.0.0:6443-:6443',
            '-device', 'virtio-net-pci,netdev=net0,romfile=']


def _runtime(info, network, spec):
    _resource(info, spec)
    _resource(network, spec, network=True)
    host = info['HostConfig']
    mounts = info.get('Mounts', [])
    binding = host.get('PortBindings', {})
    if (host.get('Privileged') or host.get('ReadonlyRootfs') is not True
            or host.get('CapDrop') != ['ALL'] or host.get('CapAdd')
            or 'no-new-privileges' not in host.get('SecurityOpt', [])
            or host.get('Memory') != 34 * 1024**3 or host.get('MemorySwap') != 34 * 1024**3
            or host.get('NanoCpus') != 8_000_000_000 or host.get('PidsLimit') != 256
            or host.get('NetworkMode') != network['Name'] or host.get('PidMode') == 'host'
            or host.get('IpcMode') == 'host' or host.get('Devices')
            or info['Config'].get('User') != f'{os.getuid()}:{os.getgid()}'
            or info.get('Image') != spec.tool_image or info['Config'].get('Cmd') != _qemu()
            or len(mounts) != 1 or mounts[0].get('Type') != 'bind'
            or mounts[0].get('Source') != spec.remote_lab or mounts[0].get('Destination') != '/lab'
            or set(binding) != {'2222/tcp'} or not binding['2222/tcp']
            or any(b.get('HostIp') != '127.0.0.1' for b in binding['2222/tcp'])
            or network.get('Internal') is not True):
        raise TCGError('Observed guest isolation differs from the bound contract')
    connections = info['NetworkSettings']['Networks']
    if set(connections) != {network['Name']}:
        raise TCGError('Unexpected network attachment')
    connection = connections[network['Name']]
    if not info['State']['Running']:
        return None
    address = ipaddress.ip_address(connection['IPAddress'])
    if (address.version != 4 or address.is_global or connection.get('NetworkID') != network['Id']
            or not any(address in ipaddress.ip_network(c['Subnet']) for c in network['IPAM']['Config'])):
        raise TCGError('Guest endpoint is outside the exact owned network')
    ports = info['NetworkSettings'].get('Ports', {})
    if set(ports) != {'2222/tcp'} or any(b.get('HostIp') != '127.0.0.1' for b in (ports['2222/tcp'] or [])):
        raise TCGError('Unexpected host port publication')
    return str(address)


GUEST_RELAY = """import os,select,socket,time
s=socket.create_connection(('127.0.0.1',2222),timeout=5)
end=time.monotonic()+60
try:
 while time.monotonic()<end:
  ready,_,_=select.select([0,s],[],[],min(5,end-time.monotonic()))
  if 0 in ready:
   data=os.read(0,65536)
   if not data:break
   s.sendall(data)
  if s in ready:
   data=s.recv(65536)
   if not data:break
   while data:
    written=os.write(1,data);data=data[written:]
finally:s.close()
"""


def _guest_ssh(lab, address, directory, container_id, *, write_hosts=False):
    if not re.fullmatch('[0-9a-f]{64}', container_id):
        raise TCGError('Guest relay requires an exact owned container ID')
    known = directory / 'guest-known-hosts'
    expected = b'[' + address.encode() + b']:2222 ' + _read(lab / 'ssh/host.pub')
    if write_hosts:
        _write(known, expected, replace=known.exists())
    elif _read(known) != expected:
        raise TCGError('Guest host-key binding changed')
    return ['ssh', '-F', '/dev/null', '-o', 'BatchMode=yes', '-o', 'ForwardAgent=no',
            '-o', 'PermitLocalCommand=no', '-o', 'IdentitiesOnly=yes', '-o', 'IdentityAgent=none',
            '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'StrictHostKeyChecking=yes',
            '-o', f'UserKnownHostsFile={known}', '-o', 'ConnectTimeout=5',
            # Restricted slirp omits a guest default route. Its accepted
            # loopback peer is translated to the reachable virtual host; an
            # outer Docker bridge peer would otherwise have no reply route.
            '-o', 'ProxyCommand=' + shlex.join(['docker', 'exec', '-i', container_id, 'python3', '-I', '-c', GUEST_RELAY]),
            '-i', str(lab / 'ssh/operator'), '-p', '2222', 'demo@' + address]


def _reply_route_target(network, address):
    address = ipaddress.IPv4Address(address)
    targets = []
    for config in network['IPAM']['Config']:
        subnet = ipaddress.ip_network(config['Subnet'])
        if address in subnet:
            gateway = ipaddress.IPv4Address(config['Gateway'])
            if (gateway not in subnet or not gateway.is_private or gateway.is_loopback or gateway.is_unspecified
                    or gateway.is_multicast or gateway.is_reserved or gateway == address
                    or gateway in (subnet.network_address, subnet.broadcast_address)
                    or gateway in ipaddress.ip_network('10.0.2.0/24')):
                raise TCGError('Owned Docker gateway is not a safe exact guest reply route')
            targets.append(str(gateway))
    if len(targets) != 1:
        raise TCGError('Owned Docker gateway is ambiguous')
    return targets[0]


GUEST_REPLY_ROUTE = """import ipaddress,json,subprocess,sys
gateway=str(ipaddress.IPv4Address(sys.argv[1]));target=gateway+'/32'
def command(args):
 return subprocess.run(['ip',*args],check=True,capture_output=True,timeout=5).stdout
addresses=json.loads(command(['-j','-4','address','show','dev','enp0s2']))
assert any(a.get('local')=='10.0.2.15' and a.get('prefixlen')==24 for item in addresses for a in item['addr_info'])
before=json.loads(command(['-j','-4','route','show','exact',target]))
if before:
 assert len(before)==1 and before[0].get('gateway')=='10.0.2.2' and before[0].get('dev')=='enp0s2'
else:
 command(['-4','route','add',target,'via','10.0.2.2','dev','enp0s2','proto','static'])
after=json.loads(command(['-j','-4','route','show','exact',target]))
assert len(after)==1 and after[0].get('gateway')=='10.0.2.2' and after[0].get('dev')=='enp0s2'
assert after[0]['dst'] in (gateway,target)
resolved=json.loads(command(['-j','-4','route','get',gateway]))
assert len(resolved)==1 and resolved[0].get('gateway')=='10.0.2.2' and resolved[0].get('dev')=='enp0s2'
print(json.dumps({'destination':target,'gateway':'10.0.2.2','device':'enp0s2','ephemeral':True,'already_present':bool(before)}))
"""


def _guest_seed_sha256(raw):
    """Only our closed cloud-init recipe may overlap the retained config check."""
    if not raw.startswith(b'#cloud-config\n'):
        raise TCGError('Retained seed is not the closed provisioner recipe')
    value = _json(raw[len(b'#cloud-config\n'):])
    try:
        user = value['users'][0]
        keys = value['ssh_keys']
        public = user['ssh_authorized_keys'][0]
        if (not isinstance(public, str) or not public.startswith('ssh-ed25519 ')
                or not isinstance(keys['ed25519_private'], str)
                or not keys['ed25519_private'].startswith('-----BEGIN OPENSSH PRIVATE KEY-----')
                or not isinstance(keys['ed25519_public'], str)
                or not keys['ed25519_public'].startswith('ssh-ed25519 ')):
            raise ValueError
        expected = {'users': [{'name': 'demo', 'groups': ['sudo'], 'shell': '/bin/bash', 'lock_passwd': True,
                              'sudo': ['ALL=(ALL) NOPASSWD:ALL'], 'ssh_authorized_keys': [public]}],
                    'ssh_pwauth': False, 'disable_root': True, 'package_update': False, 'package_upgrade': False,
                    'ssh_deletekeys': True, 'ssh_genkeytypes': [], 'ssh_keys': keys,
                    'runcmd': [['touch', '/run/devsecops-probe-ready']]}
        if set(keys) != {'ed25519_private', 'ed25519_public'} or value != expected:
            raise ValueError
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise TCGError('Retained seed is not the closed provisioner recipe') from error
    return _sha(raw)


# Runs only inside the exact pinned guest. The fixed entrypoint supplies two
# hashes, never paths or shell commands. Unit bytes are from the pinned release:
# https://raw.githubusercontent.com/rancher/rke2/v1.36.5%2Brke2r1/bundle/lib/systemd/system/rke2-server.service
GUEST_RKE2_IDENTITY = r'''import fcntl,hashlib,json,os,re,stat,subprocess,sys,time
from pathlib import Path
UNIT_SHA256='30cb868bfbc40d69b3c3b7553493c50c17538f3a550c89ff2300d13bb66a61b1'
# Exact bundled v1.36.5+rke2r1 file, not an operator environment override:
# https://raw.githubusercontent.com/rancher/rke2/v1.36.5%2Brke2r1/bundle/lib/systemd/system/rke2-server.env
VENDOR_ENV_PATH='/usr/local/lib/systemd/system/rke2-server.env'
VENDOR_ENV_SHA256='2584cb4fcddb07782af3e50f2ee4200d0b84e64856f3db458bc0b1da53a70b79'
LEGACY={'write-kubeconfig-mode':'0600','cni':'canal','node-name':'devsecops-rke2-screen',
        'disable':['rke2-traefik','rke2-ingress-nginx']}
PINNED={**LEGACY,'node-ip':'10.0.2.15'}
class GuardError(RuntimeError):
 def __init__(self,category,diagnostic=None):
  super().__init__(category);self.diagnostic=diagnostic
def require(ok, category):
 if not ok: raise GuardError(category)
def digest(raw): return hashlib.sha256(raw).hexdigest()
def decode(raw):
 def pairs(items):
  result={}
  for key,value in items:
   require(key not in result,'duplicate-json-key');result[key]=value
  return result
 return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:require(False,'nonfinite-json'))
def canonical(value): return (json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode()
def assure(ca_sha,seed_sha,root=Path('/'),runner=subprocess.run):
 end=time.monotonic()+35
 require(all(re.fullmatch('[0-9a-f]{64}',s) for s in (ca_sha,seed_sha)),'invalid-hash')
 def path(name):
  p=root/name.lstrip('/')
  require(not any(a.is_symlink() for a in (p,*p.parents)),'symlink-path')
  return p
 def read(name,private=False):
  p=path(name)
  fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
  with os.fdopen(fd,'rb') as f:
   info=os.fstat(f.fileno())
   require(stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid() and info.st_nlink==1
           and not info.st_mode & (0o077 if private else 0o022) and info.st_size<=65536,'unsafe-file')
   raw=f.read(65537);after=os.fstat(f.fileno())
   require(len(raw)<=65536 and (info.st_ino,info.st_size,info.st_mtime_ns)==
           (after.st_ino,after.st_size,after.st_mtime_ns),'changed-file')
   return raw
 def run(args):
  remaining=end-time.monotonic();require(remaining>0,'deadline')
  result=runner(args,capture_output=True,timeout=min(5,remaining),check=False)
  require(result.returncode==0 and len(result.stdout)<=65536 and len(result.stderr)<=65536,'command-failed')
  require(time.monotonic()<end,'deadline')
  return result.stdout
 def properties(unit,keys,phase):
  require(unit in ('rke2-server.service','cloud-config.service','cloud-final.service')
          and phase in ('effective','timing'),'unit-query-scope')
  raw=run(['systemctl','show',unit,'--all','--property='+','.join(keys)])
  output=private_directory()/('rke2-node-ip-'+unit+'-'+phase+'.log')
  atomic(output,raw)
  detail={'unit':unit,'phase':phase,'raw_sha256':digest(raw),'raw_bytes':len(raw),
          'private_output':str(output),'requested_keys':keys}
  def fail(reason,key=None):
   # Only requested property names and fixed categories leave the private log.
   raise GuardError('unit-properties',{**detail,'reason':reason,'property':key if key in keys else None})
  values={}
  for line in raw.decode().splitlines():
   key,sep,value=line.partition('=')
   if not sep or key not in keys:fail('unexpected-property',key)
   # systemd255 prints one EnvironmentFiles= line for each (path,ignore) tuple.
   # Every other requested property is scalar and must occur exactly once.
   if key=='EnvironmentFiles':values.setdefault(key,[]).append(value)
   elif key in values:fail('duplicate-property',key)
   else:values[key]=value
  if set(values)!=set(keys):fail('missing-property',next(k for k in keys if k not in values))
  return values
 def atomic(p,raw):
  require(time.monotonic()<end,'deadline')
  if p.exists() or p.is_symlink():read(str(p.relative_to(root)),True)
  tmp=p.parent/('.'+p.name+'-node-ip-'+str(os.getpid()))
  fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
  try:
   with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
   require(time.monotonic()<end,'deadline')
   os.replace(tmp,p)
   fd=os.open(p.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
   try:os.fsync(fd)
   finally:os.close(fd)
  finally:
   if tmp.exists():tmp.unlink()
 def private_directory():
  directory=path('/var/lib/devsecops');directory.mkdir(mode=0o700,exist_ok=True)
  info=directory.stat();require(info.st_uid==os.getuid() and not info.st_mode & 0o077,'private-migration-directory')
  return directory
 config=path('/etc/rancher/rke2/config.yaml');before=read(str(config.relative_to(root)),True)
 require(decode(before) in (LEGACY,PINNED),'unknown-rke2-config')
 ca_path='/var/lib/rancher/rke2/server/tls/server-ca.crt'
 require(digest(read(ca_path))==ca_sha,'original-ca-mismatch')
 addresses=decode(run(['ip','-j','-4','address','show','dev','enp0s2']))
 require(any(a.get('local')=='10.0.2.15' and a.get('prefixlen')==24
             for item in addresses for a in item['addr_info']),'guest-ipv4-mismatch')
 overrides=path('/etc/rancher/rke2/config.yaml.d')
 require(not overrides.exists() or (overrides.is_dir() and not list(overrides.iterdir())),'config-overrides')
 unit_path='/usr/local/lib/systemd/system/rke2-server.service'
 require(digest(read(unit_path))==UNIT_SHA256,'unit-bytes-mismatch')
 effective=properties('rke2-server.service',['FragmentPath','DropInPaths','NeedDaemonReload',
                       'Environment','PassEnvironment','EnvironmentFiles','ExecStart'],'effective')
 require(effective['FragmentPath']==unit_path and effective['DropInPaths']==''
         and effective['NeedDaemonReload']=='no' and effective['Environment']==''
         and effective['PassEnvironment']=='','unit-overrides')
 expected_env=[p+' (ignore_errors=yes)' for p in
   ('/etc/default/rke2-server','/etc/sysconfig/rke2-server','/usr/local/lib/systemd/system/rke2-server.env')]
 require(effective['EnvironmentFiles']==expected_env,'unit-environment-files')
 require(re.fullmatch(r'\{ path=/usr/local/bin/rke2 ; argv\[\]=/usr/local/bin/rke2 server ; ignore_errors=no ; [^{}]* \}',
                      effective['ExecStart']) is not None,'unit-exec-overrides')
 environment_files=[]
 for name in ('/etc/default/rke2-server','/etc/sysconfig/rke2-server',VENDOR_ENV_PATH):
  if path(name).exists():
   raw=read(name)
   vendor=name==VENDOR_ENV_PATH and raw==b'HOME=/root\n' and digest(raw)==VENDOR_ENV_SHA256
   require(vendor or all(not l.strip() or l.lstrip().startswith(b'#') for l in raw.splitlines()),'environment-override')
   # Never source/evaluate the file or expose values through the observation.
   environment_files.append({'path':name,'sha256':digest(raw),'bytes':len(raw),
                             'admission':'bundled-default' if vendor else 'empty-or-comments'})
 # cloud-init's instance link is expected; only its one direct instances child is accepted.
 instance=(root/'var/lib/cloud/instance').resolve(strict=True)
 require(instance.parent==path('/var/lib/cloud/instances'),'cloud-instance-path')
 seed=read(str((instance/'user-data.txt').relative_to(root)),True)
 require(digest(seed)==seed_sha,'cloud-seed-mismatch')
 for name in ('vendor-data.txt','vendor-data2.txt'):
  p=instance/name
  if p.exists():require(not read(str(p.relative_to(root))).strip(),'cloud-vendor-data')
 timing_keys=['ActiveState','SubState','ActiveEnterTimestampMonotonic','InactiveExitTimestampMonotonic',
              'ExecMainStartTimestampMonotonic','ExecMainExitTimestampMonotonic','After','Before']
 timings={u:properties(u,timing_keys,'timing') for u in ('cloud-config.service','cloud-final.service','rke2-server.service')}
 # Preserve timings even if later migration fails; no cloud-final wait is introduced.
 record={'node_ip':'10.0.2.15','node_name':LEGACY['node-name'],'ca_sha256':ca_sha,
         'seed_sha256':seed_sha,'unit_sha256':UNIT_SHA256,'config_before_sha256':digest(before),
         'config_after_sha256':digest(before),'migration':'not-needed','timings':timings,
         'environment_files':environment_files}
 directory=private_directory()
 lockpath=directory/'rke2-node-ip.lock'
 fd=os.open(lockpath,os.O_RDONLY|os.O_CREAT|os.O_NOFOLLOW,0o600)
 try:
  info=os.fstat(fd);require(stat.S_ISREG(info.st_mode) and info.st_nlink==1
       and info.st_uid==os.getuid() and not info.st_mode & 0o077,'private-migration-lock')
  fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
  require(read(str(config.relative_to(root)),True)==before,'concurrent-config-change')
  receipt=directory/'rke2-node-ip.json'
  if receipt.exists():
   prior=decode(read(str(receipt.relative_to(root)),True))
   require(prior.get('migration')=='restart-requested' and prior.get('ca_sha256')==ca_sha
           and prior.get('seed_sha256')==seed_sha and prior.get('unit_sha256')==UNIT_SHA256
           and prior.get('config_after_sha256')==digest(before) and decode(before)==PINNED,
           'incomplete-or-changed-migration')
  atomic(directory/'rke2-node-ip-observation.json',canonical(record))
  if decode(before)==LEGACY:
   require(not receipt.exists(),'existing-migration')
   record['config_after_sha256']=digest(canonical(PINNED));record['migration']='intent'
   # Store original bytes before changing the only admitted config key.
   backup=directory/'rke2-config-before.json'
   require(time.monotonic()<end,'deadline')
   out=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
   with os.fdopen(out,'wb') as f:f.write(before);f.flush();os.fsync(f.fileno())
   atomic(receipt,canonical(record))
   require(read(str(config.relative_to(root)),True)==before and digest(read(ca_path))==ca_sha,'concurrent-identity-change')
   atomic(config,canonical(PINNED))
   run(['systemctl','restart','--no-block','rke2-server.service'])
   record['migration']='restart-requested';atomic(receipt,canonical(record))
  require(decode(read(str(config.relative_to(root)),True))==PINNED and digest(read(ca_path))==ca_sha,'post-migration-identity')
  require(time.monotonic()<end,'deadline')
  return record
 finally:os.close(fd)
'''


def _guest_identity_command(ca_sha256, seed_sha256):
    entry = "\ntry:\n print(json.dumps(assure(sys.argv[1],sys.argv[2])))\nexcept GuardError as error:\n print(json.dumps({'error':str(error),'diagnostic':error.diagnostic}));sys.exit(2)\n"
    return shlex.join(['sudo', 'python3', '-I', '-c', GUEST_RKE2_IDENTITY + entry, ca_sha256, seed_sha256])


def _guest_property_failure(raw):
    """Publish fixed query shape only; raw unit/environment values stay private."""
    try:
        value = _json(raw)
        detail = value.get('diagnostic')
        if value.get('error') != 'unit-properties' or not isinstance(detail, dict):
            return None
        unit, phase, reason, key = (detail.get(k) for k in ('unit', 'phase', 'reason', 'property'))
        if (unit not in ('rke2-server.service', 'cloud-config.service', 'cloud-final.service')
                or phase not in ('effective', 'timing')
                or reason not in ('unexpected-property', 'duplicate-property', 'missing-property')
                or key not in (None, 'FragmentPath', 'DropInPaths', 'NeedDaemonReload', 'Environment',
                               'PassEnvironment', 'EnvironmentFiles', 'ExecStart', 'ActiveState', 'SubState',
                               'ActiveEnterTimestampMonotonic', 'InactiveExitTimestampMonotonic',
                               'ExecMainStartTimestampMonotonic', 'ExecMainExitTimestampMonotonic', 'After', 'Before')
                or not isinstance(detail.get('raw_sha256'), str)
                or not re.fullmatch('[0-9a-f]{64}', detail['raw_sha256'])
                or type(detail.get('raw_bytes')) is not int or not 0 <= detail['raw_bytes'] <= 65536):
            return None
        return {'unit': unit, 'phase': phase, 'reason': reason, 'property': key,
                'raw_sha256': detail['raw_sha256'], 'raw_bytes': detail['raw_bytes'],
                'private_guest_output': '/var/lib/devsecops/rke2-node-ip-' + unit + '-' + phase + '.log'}
    except (TCGError, AttributeError):
        return None


def _ssh_pending(result):
    error = result.stderr.lower()
    if any(marker in error for marker in (b'host identification has changed', b'host key verification failed',
                                         b'no matching host key', b'permission denied')):
        raise TCGError('Pinned guest SSH identity verification failed')
    if result.returncode == 0:
        return False
    if result.returncode == 124:
        return True
    if result.returncode == 255 and any(marker in error for marker in (
            b'connection refused', b'connection timed out', b'connection closed',
            b'connection reset', b'no route to host', b'connection to ')):
        return True
    raise TCGError('Unexpected guest SSH failure')


def _remote_action(payload):
    """Trusted code observations, not a consumer of caller-asserted live pass."""
    spec = TCGSpec(**payload['spec']).validate()
    action, operation = payload['action'], _id(payload['operation_id'])
    if action not in ('up', 'down', 'reconcile', 'credentials'):
        raise TCGError('Unsupported retained-guest action')
    deadline = time.monotonic() + min(float(payload['timeout']), 650 if action == 'up' else 240)
    lab = _private(Path(spec.remote_lab))
    original = _json(_read(lab / 'state.json'))
    if original.get('run_id') != spec.provision_run_id or original.get('image_id') != spec.tool_image:
        raise TCGError('Original provision identity changed')
    ca = _read(lab / 'operator/api-ca.crt')
    if _sha(ca) != spec.original_ca_sha256:
        raise TCGError('Original API CA changed')
    seed_sha256 = _guest_seed_sha256(_read(lab / 'user-data')) if action == 'up' else None
    directory = lab / 'retained'
    existing = directory.exists()
    if not existing and action == 'up':
        _private(directory, create=True)
        _write(directory / 'binding.json', _canonical({'spec': asdict(spec), 'driver_sha256': payload['driver_sha256']}))
        _write(directory / 'lock', b'')
    elif existing:
        _private(directory)
    binding = {'spec': asdict(spec), 'driver_sha256': payload['driver_sha256']}
    if directory.exists() and _json(_read(directory / 'binding.json')) != binding:
        raise TCGError('Retained environment belongs to a different run, package or input set')
    lock = None
    try:
        if directory.exists():
            lock = _lock_descriptor(directory / 'lock')
            fcntl.flock(lock, (fcntl.LOCK_SH if action in ('reconcile', 'credentials') else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        prefix = 'devsecops-retained-' + _sha(spec.run_id.encode())[:16]
        names = {'container': prefix + '-vm', 'network': prefix + '-net'}
        state_path = directory / 'state.json'
        state = _json(_read(state_path)) if state_path.exists() else {'history': [], 'pending': None}
        disk = _path(lab / 'probe.qcow2')
        disk_info = disk.stat()
        disk_identity = {'device': disk_info.st_dev, 'inode': disk_info.st_ino}
        if state.get('disk_identity') is not None and state['disk_identity'] != disk_identity:
            raise TCGError('Retained disk was replaced; recreation cannot establish durability')

        def command(argv, seconds=30, check=True, timeout_ok=False):
            return _run(argv, min(deadline, time.monotonic() + seconds), check=check, timeout_ok=timeout_ok)

        def inspect(kind, identifier):
            result = command(['docker', kind, 'inspect', identifier], check=False)
            if result.returncode:
                if b'No such' in result.stderr or b'not found' in result.stderr:
                    return None
                raise TCGError('Docker observation failed; absence is not established')
            value = _json(result.stdout)
            if not isinstance(value, list) or len(value) != 1:
                raise TCGError('Unexpected Docker inspection')
            return value[0]

        def mutate(argv, kind, verb, seconds=30):
            state['inflight'] = {'kind': kind, 'verb': verb}
            _write(state_path, _canonical(state), replace=True)
            result = command(argv, seconds)
            state['inflight'] = None
            _write(state_path, _canonical(state), replace=True)
            return result

        def observed():
            found = {}
            for kind in ('container', 'network'):
                ids = command(['docker', kind, 'ls', *(['-a'] if kind == 'container' else []),
                               '-q', '--no-trunc', '--filter', f'label={LABEL}={spec.run_id}']).stdout.decode().split()
                item = inspect(kind, names[kind])
                if item:
                    identifier = _resource(item, spec, network=kind == 'network')
                    recorded = state.get(kind + '_id')
                    if recorded is not None and identifier != recorded:
                        raise TCGError('Observed Docker ID differs from the immutable owned ID')
                    creation = (item.get('Labels', {}) if kind == 'network' else item['Config']['Labels'])[OPERATION_LABEL]
                    if recorded is None and creation != (state.get('pending') or {}).get('operation_id'):
                        raise TCGError('Unrecorded resource is not bound to the exact pending creation')
                    if creation not in state['history'] and creation != (state.get('pending') or {}).get('operation_id'):
                        raise TCGError('Resource lacks a previously recorded creation intent')
                    if ids != [identifier]:
                        raise TCGError('Unexpected extra owned resources require reconciliation')
                elif ids:
                    raise TCGError('Unrecorded owned resource names require reconciliation')
                found[kind] = item
            if found['container'] and not found['network']:
                raise TCGError('Owned container network identity is missing')
            return found

        resources = observed()
        unresolved = state.get('pending')
        if unresolved and action == 'reconcile' and operation != unresolved['operation_id']:
            raise TCGError('Reconciliation refers to a different pending operation')
        if not directory.exists() and action == 'down':
            return {'state': 'absent', 'container_id': None, 'network_id': None, 'address': None,
                    'disk_identity': disk_identity,
                    'observed_at': _now(), 'pending': None, 'ca_sha256': _sha(ca),
                    'graceful_shutdown_observed': None, 'runtime_isolation_verified': False,
                    'remaining_owned_runtime': False, 'observed_mutation_effect': 'not_applied'}
        if action in ('up', 'down'):
            if unresolved:
                if payload.get('reconciles_operation') != unresolved['operation_id']:
                    raise TCGError('Unresolved mutation requires a fresh read-only reconciliation')
                expected_ids = payload.get('reconciled_ids', {})
                for kind in ('container', 'network'):
                    actual_id = resources[kind]['Id'] if resources[kind] else None
                    if expected_ids.get(kind) != actual_id:
                        raise TCGError('Runtime changed after explicit reconciliation')
                    if actual_id is not None and state.get(kind + '_id') is None:
                        state[kind + '_id'] = actual_id
                state['history'].append(unresolved['operation_id'])
                state.setdefault('reconciled', []).append({**unresolved, 'superseded_by': operation})
            if operation in state['history']:
                raise TCGError('Operation already used or unresolved; reconcile before another mutation')
            state['pending'] = {'operation_id': operation, 'action': action, 'started_at': _now(), 'names': names,
                                'before': {k: resources[k]['Id'] if resources[k] else None for k in ('container', 'network')}}
            state.setdefault('disk_identity', disk_identity)
            _write(state_path, _canonical(state), replace=state_path.exists())
        if action == 'up' and not resources['container']:
            if resources['network']:
                raise TCGError('Partial runtime requires explicit reconciliation')
            for kind in ('container', 'network'):
                prior = command(['docker', kind, 'ls', *(['-a'] if kind == 'container' else []), '-q',
                                 '--filter', f'label=io.devsecops.tcg-probe.run={spec.provision_run_id}'])
                if prior.stdout.strip():
                    raise TCGError('The original provision runtime has not been removed')
            image = inspect('image', spec.tool_image)
            if image is None or image.get('Id') != spec.tool_image:
                raise TCGError('Pinned local tool image is unavailable; acquisition is a separate operation')
            state.pop('network_id', None)
            state.pop('container_id', None)
            _write(state_path, _canonical(state), replace=True)
            available = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                                 if line.startswith('MemAvailable:'))) * 1024
            if available < 44 * 1024**3 or shutil.disk_usage(lab).free < 310 * 1024**3:
                raise TCGError('Insufficient host memory or disk headroom')
            for name in ('probe.qcow2', 'ubuntu.img', 'seed.iso', 'ssh/operator'):
                path = _path(lab / name)
                info = path.stat()
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                    raise TCGError('Private retained guest artifact identity changed')
            labels = ['--label', f'{LABEL}={spec.run_id}', '--label', f'{BINDING_LABEL}={spec.digest}',
                      '--label', f'{OPERATION_LABEL}={operation}']
            network_id = mutate(['docker', 'network', 'create', '--internal', *labels, names['network']],
                                'network', 'create').stdout.decode().strip()
            state['network_id'] = _resource(inspect('network', network_id), spec, network=True)
            _write(state_path, _canonical(state), replace=True)
            argv = ['docker', 'create', '--pull=never', '--name', names['container'], *labels,
                    '--user', f'{os.getuid()}:{os.getgid()}', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                    '--read-only', '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=64m', '--pids-limit', '256',
                    '--ulimit', 'core=0', '--mount', f'type=bind,src={lab},dst=/lab', '--network', names['network'],
                    '--cpus', '8', '--memory', str(34 * 1024**3), '--memory-swap', str(34 * 1024**3),
                    '--publish', '127.0.0.1::2222', spec.tool_image, *_qemu()]
            container_id = mutate(argv, 'container', 'create').stdout.decode().strip()
            state['container_id'] = _resource(inspect('container', container_id), spec)
            _write(state_path, _canonical(state), replace=True)
            resources = observed()
        address = None
        graceful = None
        if resources['container']:
            address = _runtime(resources['container'], resources['network'], spec)
        if action == 'up':
            if not resources['container']['State']['Running']:
                mutate(['docker', 'start', resources['container']['Id']], 'container', 'start')
                resources = observed()
                address = _runtime(resources['container'], resources['network'], spec)
            ssh = _guest_ssh(lab, address, directory, resources['container']['Id'], write_hosts=True)
            boot_started = time.monotonic()
            boot_end = min(deadline - 5, time.monotonic() + 600)
            attempts = 0
            while time.monotonic() < boot_end:
                result = command([*ssh, 'true'], 30, check=False, timeout_ok=True)
                attempts += 1
                _write(directory / (operation + '-ssh-stderr.log'), result.stderr, replace=True)
                _write(directory / (operation + '-ssh-observation.json'), _canonical({
                    'container_id': resources['container']['Id'], 'operation_id': operation,
                    'elapsed_seconds': time.monotonic() - boot_started, 'attempts': attempts,
                    'returncode': result.returncode, 'stderr_sha256': _sha(result.stderr),
                    'transport': 'exact-owned-container-loopback', 'observed_at': _now()}), replace=True)
                if not _ssh_pending(result):
                    break
                time.sleep(min(3, max(0, boot_end - time.monotonic())))
            else:
                raise TCGError('Retained guest missed the fixed SSH boot gate')
            if deadline - time.monotonic() < 45:
                raise TCGError('Remote deadline cannot cover the fixed guest identity step')
            identity = command([*ssh, _guest_identity_command(spec.original_ca_sha256, seed_sha256)],
                               40, check=False, timeout_ok=True)
            _write(directory / (operation + '-guest-identity.stdout.json'), identity.stdout, replace=True)
            _write(directory / (operation + '-guest-identity.stderr.log'), identity.stderr, replace=True)
            state['guest_identity_attempt'] = {'operation_id': operation, 'returncode': identity.returncode,
                'stdout_sha256': _sha(identity.stdout), 'stderr_sha256': _sha(identity.stderr), 'observed_at': _now()}
            property_failure = _guest_property_failure(identity.stdout) if identity.returncode else None
            if property_failure:
                state['guest_identity_attempt']['property_failure'] = property_failure
            _write(state_path, _canonical(state), replace=True)
            if identity.returncode:
                if property_failure:
                    raise TCGError('Bounded guest RKE2 identity gate failed: unit-properties; '
                                   + property_failure['unit'] + '; ' + property_failure['reason'] + '; '
                                   + (property_failure['property'] or 'unrecognized property')
                                   + '; preserve its private diagnostic receipt')
                raise TCGError('Bounded guest RKE2 identity gate failed; preserve its private diagnostic receipt')
            identity = _json(identity.stdout)
            if (identity.get('node_ip') != '10.0.2.15' or identity.get('node_name') != 'devsecops-rke2-screen'
                    or identity.get('ca_sha256') != spec.original_ca_sha256 or identity.get('seed_sha256') != seed_sha256
                    or identity.get('unit_sha256') != '30cb868bfbc40d69b3c3b7553493c50c17538f3a550c89ff2300d13bb66a61b1'
                    or identity.get('migration') not in ('not-needed', 'restart-requested')):
                raise TCGError('Guest RKE2 identity observation did not match the retained provisioner')
            state['guest_rke2_identity'] = {**identity, 'container_id': resources['container']['Id'],
                                           'observed_at': _now()}
            _write(state_path, _canonical(state), replace=True)
            command([*ssh, 'sudo test -f /etc/rancher/rke2/rke2.yaml'], 20)
            gateway = _reply_route_target(resources['network'], address)
            route = _json(command([*ssh, shlex.join(['sudo', 'python3', '-I', '-c', GUEST_REPLY_ROUTE, gateway])], 30).stdout)
            if (route.get('destination') != gateway + '/32' or route.get('gateway') != '10.0.2.2'
                    or route.get('device') != 'enp0s2' or route.get('ephemeral') is not True):
                raise TCGError('Guest reply route did not match the exact owned Docker gateway')
            state['guest_reply_route'] = {**route, 'container_id': resources['container']['Id'],
                                        'network_id': resources['network']['Id'], 'observed_at': _now()}
            _write(state_path, _canonical(state), replace=True)
        if action == 'down':
            if resources['container']:
                identifier = resources['container']['Id']
                if resources['container']['State']['Running']:
                    ssh = _guest_ssh(lab, address, directory, identifier)
                    # A failed host-key check never becomes a shutdown command.
                    result = command([*ssh, 'true'], 20, check=False, timeout_ok=True)
                    try:
                        ssh_ready = not _ssh_pending(result)
                    except TCGError:
                        # Do not send a guest command after an identity failure.
                        # Exact owned Docker cleanup remains independently safe.
                        ssh_ready = False
                    if ssh_ready:
                        command([*ssh, 'sudo systemctl poweroff --no-block'], 20, check=False)
                        result = command(['docker', 'wait', identifier], 150, check=False, timeout_ok=True)
                        graceful = result.returncode == 0
                    else:
                        graceful = False
                    current = inspect('container', identifier)
                    _resource(current, spec)
                    if current['State']['Running']:
                        mutate(['docker', 'stop', '--time', '15', identifier], 'container', 'stop', 25)
                _resource(inspect('container', identifier), spec)
                mutate(['docker', 'container', 'rm', identifier], 'container', 'remove')
            if resources['network']:
                current = inspect('network', resources['network']['Id'])
                _resource(current, spec, network=True)
                if current.get('Containers'):
                    raise TCGError('Owned network still has attachments')
                mutate(['docker', 'network', 'rm', current['Id']], 'network', 'remove')
            resources = observed()
        if action == 'credentials':
            return {'private_kubeconfig_b64': base64.b64encode(_read(lab / 'operator/kubeconfig.yaml')).decode()}
        if action in ('up', 'down'):
            state.setdefault('completed', []).append(state['pending'])
            state['history'].append(operation)
            state['pending'] = None
            _write(state_path, _canonical(state), replace=True)
        container = resources['container']
        original_operation = (state.get('pending') or next(
            (item for item in state.get('completed', []) if item['operation_id'] == operation), {}))
        original_action = original_operation.get('action')
        completion = None
        if original_action == 'down':
            before = original_operation.get('before', {})
            remaining = {k: resources[k]['Id'] if resources[k] else None for k in ('container', 'network')}
            effect = 'not_applied' if remaining == before else 'applied'
            completion = 'complete' if not any(remaining.values()) else 'none' if remaining == before else 'partial'
        else:
            effect = 'applied' if container or resources['network'] else 'not_applied'
        # Fresh protection observations are separate from runtime absence.
        # Missing baseline files never become a positive preservation claim.
        protected = {}
        try:
            for relative in ('seed.iso', 'user-data', 'ssh/operator', 'ssh/host'):
                raw = _read(lab / relative, 4_000_000)
                item = (lab / relative).stat()
                protected[relative] = {'sha256': _sha(raw), 'device': item.st_dev, 'inode': item.st_ino}
            operator = _private(lab / 'operator')
            entries = sorted(operator.iterdir())
            if not entries or len(entries) > 32:
                raise TCGError('Protected credential inventory exceeds bound')
            for path in entries:
                raw = _read(path, 4_000_000)
                item = path.stat()
                protected['operator/' + path.name] = {'sha256': _sha(raw), 'device': item.st_dev, 'inode': item.st_ino}
            tool = inspect('image', spec.tool_image)
            if tool is None or tool.get('Id') != spec.tool_image:
                raise TCGError('Protected tool image no longer exists')
        except (OSError, ValueError, TCGError):
            protected = {}
        prior_protected = state.get('protected_inputs')
        if action == 'up' and protected and prior_protected is None:
            state['protected_inputs'] = protected
            _write(state_path, _canonical(state), replace=state_path.exists())
            prior_protected = protected
        protection = {'verified': bool(protected) and protected == prior_protected,
            'files': protected, 'tool_image': spec.tool_image,
            'original_ca_sha256': _sha(ca), 'disk_identity': disk_identity}
        inflight = state.get('inflight')
        if inflight and inflight['verb'] == 'create' and not resources[inflight['kind']]:
            # The Docker daemon may still finish a disconnected create request.
            # Absence at this instant cannot authorize another create or release.
            effect = 'unknown'
        return {'state': 'absent' if not container and not resources['network'] else
                'running_unready' if container and container['State']['Running'] else 'stopped',
                'container_id': container['Id'] if container else None,
                'network_id': resources['network']['Id'] if resources['network'] else None,
                'disk_identity': disk_identity, 'protected_resources': protection,
                'address': address, 'observed_at': _now(), 'pending': state.get('pending'),
                'ca_sha256': _sha(ca), 'graceful_shutdown_observed': graceful,
                'guest_reply_route': state.get('guest_reply_route'),
                'guest_rke2_identity': state.get('guest_rke2_identity'),
                'runtime_isolation_verified': bool(container), 'remaining_owned_runtime': bool(container or resources['network']),
                'inflight': inflight, 'observed_mutation_effect': effect, 'mutation_completion': completion}
    finally:
        if lock is not None:
            os.close(lock)


class RetainedTCGDriver:
    """Adopt a provisioned disk. Never instantiate this from model-authored JSON."""

    def __init__(self, private_state_dir: Path, spec: TCGSpec):
        self.spec = TCGSpec(**asdict(spec)).validate()
        self.directory = _private(private_state_dir, create=True)
        self.source = Path(__file__).read_bytes()
        self.source_sha256 = _sha(self.source)
        binding = {'spec': asdict(self.spec), 'driver_sha256': self.source_sha256}
        path = self.directory / 'binding.json'
        if path.exists():
            if _json(_read(path)) != binding:
                raise TCGError('Changed run, package, source or input identity')
        else:
            _write(path, _canonical(binding))
        _private(self.directory / 'receipts', create=True)

    def _transport_spec(self):
        """Resolve only an audited same-identity certificate generation."""
        pointer = self.directory / 'transport-generation.json'
        if not pointer.exists():
            return self.spec
        head = _json(_read(pointer))
        current = head
        latest = None
        following_old_certificate = None
        for _ in range(128):
            if (not isinstance(current, dict) or set(current) != {'receipt', 'sha256'}
                    or not re.fullmatch(r'transport-renewal-[A-Za-z0-9._-]+\.json', current['receipt'])):
                raise TCGError('Invalid transport generation reference')
            raw = _read(self.directory / 'receipts' / current['receipt'])
            record = _json(raw)
            if (_sha(raw) != current['sha256'] or record['status'] != 'authenticated'
                    or record['binding_sha256'] != self.spec.digest or record['driver_sha256'] != self.source_sha256
                    or (following_old_certificate is not None and following_old_certificate != record['transport']['certificate']['sha256'])):
                raise TCGError('Transport generation audit changed')
            transport = record['transport']
            for key in self.spec.transport:
                if key not in ('certificate', 'config') and transport.get(key) != self.spec.transport[key]:
                    raise TCGError('Renewal changed the approved SSH route or identity')
            if set(transport) != set(self.spec.transport):
                raise TCGError('Invalid transport generation fields')
            generation = self.directory / 'transport-generations' / _id(record['operation_id'])
            if (Path(transport['certificate']['path']) != generation / 'client-certificate.pub'
                    or Path(transport['config']['path']) != generation / 'ssh-config'):
                raise TCGError('Renewed transport escaped its immutable generation')
            original_config = _read(self.spec.transport['config']['path'])
            old = ('  CertificateFile "' + self.spec.transport['certificate']['path'] + '"\n').encode()
            new = ('  CertificateFile "' + transport['certificate']['path'] + '"\n').encode()
            if original_config.count(old) != 1 or _read(transport['config']['path']) != original_config.replace(old, new):
                raise TCGError('Renewed SSH config changed more than its certificate reference')
            grant = _certificate_grant(_read(transport['certificate']['path']))
            original_grant = _certificate_grant(_read(self.spec.transport['certificate']['path']))
            if grant['grant_sha256'] != original_grant['grant_sha256']:
                raise TCGError('Renewed certificate grant differs from the immutable owner')
            candidate = replace(self.spec, transport=transport)
            _ssh(candidate)  # Recheck each immutable artifact before use.
            latest = candidate if latest is None else latest
            following_old_certificate = record['previous_certificate_sha256']
            current = record['previous']
            if current is None:
                if following_old_certificate != self.spec.transport['certificate']['sha256']:
                    raise TCGError('Transport generation does not descend from the original owner')
                return latest
        raise TCGError('Transport generation history exceeds its bound')

    def renew_certificate(self, issued_certificate, operation_id, *, deadline, minimum_validity_seconds=1800):
        """Admit an already-issued certificate without changing owner authority.

        Issuance stays with the operator's existing credential helper. This API
        accepts no helper command or route override. A real certificate-only SSH
        probe must pass before the immutable generation becomes active.
        """
        operation = _id(operation_id)
        if (type(minimum_validity_seconds) is not int or not 60 <= minimum_validity_seconds <= 86400):
            raise TCGError('Renewal minimum validity must be 60 through 86400 seconds')
        with self._locked():
            if _json(_read(self.directory / 'binding.json')) != {
                    'spec': asdict(self.spec), 'driver_sha256': self.source_sha256}:
                raise TCGError('Run identity changed before certificate renewal')
            active = self._transport_spec()
            pointer = self.directory / 'transport-generation.json'
            previous = _json(_read(pointer)) if pointer.exists() else None
            receipt = self.directory / 'receipts' / ('transport-renewal-' + operation + '.json')
            generation = self.directory / 'transport-generations' / operation
            if receipt.exists() or generation.exists():
                raise TCGError('Certificate renewal operation already used')
            report = {'kind': 'retained-tcg-certificate-renewal', 'operation_id': operation,
                      'binding_sha256': self.spec.digest, 'driver_sha256': self.source_sha256,
                      'previous': previous, 'previous_certificate_sha256': active.transport['certificate']['sha256'],
                      'started_at': _now(), 'status': 'rejected', 'owner_binding_changed': False}
            try:
                raw = _public_read(issued_certificate, LIMIT)
                grant = _certificate_grant(raw)
                original = _certificate_grant(_read(self.spec.transport['certificate']['path']))
                if grant['grant_sha256'] != original['grant_sha256']:
                    raise TCGError('Renewal changed the client identity, issuer or certificate grant')
                if _sha(raw) == active.transport['certificate']['sha256']:
                    raise TCGError('Certificate was not renewed')

                def lifetime():
                    now = time.time()
                    if not grant['valid_after'] <= now or grant['valid_before'] - now < minimum_validity_seconds:
                        raise TCGError('Renewed certificate cannot cover the required operation lifetime')
                    if time.monotonic() >= deadline:
                        raise TCGError('Certificate renewal deadline expired')

                lifetime()
                _private(self.directory / 'transport-generations', create=True)
                transport = _renewed_transport(self.spec.transport, generation, raw)
                candidate = replace(self.spec, transport=transport)
                probe_deadline = min(deadline, time.monotonic() + 30)
                with _ssh_context(candidate, probe_deadline, self.directory / 'receipts') as argv:
                    _run([*argv, 'true'], probe_deadline)
                lifetime()
                report.update(status='authenticated', transport=transport, certificate=grant,
                              minimum_validity_seconds=minimum_validity_seconds, certificate_only_probe=True)
            except (TCGError, OSError, ValueError, KeyError, TypeError) as error:
                report['error'] = str(error) if isinstance(error, TCGError) else 'Bounded certificate renewal failed'
                report['finished_at'] = _now()
                _write(receipt, _canonical(report))
                raise TCGError('Certificate renewal rejected; prior generation retained; receipt ' + str(receipt)) from None
            report['finished_at'] = _now()
            _write(receipt, _canonical(report))
            _write(pointer, _canonical({'receipt': receipt.name, 'sha256': _sha(_read(receipt))}), replace=pointer.exists())
            return receipt

    def _remote(self, action, operation, deadline, reconciles_operation=None, reconciled_ids=None):
        payload = {'spec': asdict(self.spec), 'driver_sha256': self.source_sha256,
                   'action': action, 'operation_id': operation,
                   'timeout': max(0, deadline - time.monotonic() - 5),
                   'reconciles_operation': reconciles_operation, 'reconciled_ids': reconciled_ids}
        argument = base64.b64encode(_canonical(payload)).decode()
        with _ssh_context(self._transport_spec(), deadline, self.directory / 'receipts') as argv:
            result = _run([*argv, shlex.join(['python3', '-', argument])], deadline, data=self.source)
        response = _json(result.stdout)
        if response.get('error'):
            raise TCGError(response['error'])
        if response.get('binding_sha256') != self.spec.digest or response.get('operation_id') != operation:
            raise TCGError('Remote observation is stale or belongs to a different operation')
        return response['observation']

    def _credentials(self, operation, deadline):
        private = self._remote('credentials', operation, deadline)
        raw = base64.b64decode(private['private_kubeconfig_b64'], validate=True)
        import yaml
        value = yaml.safe_load(raw)
        try:
            if len(value['clusters']) != 1 or len(value['users']) != 1 or len(value['contexts']) != 1:
                raise ValueError
            cluster, user = value['clusters'][0]['cluster'], value['users'][0]['user']
            if cluster.get('server') != 'https://127.0.0.1:6443' or set(user) != {'client-certificate-data', 'client-key-data'}:
                raise ValueError
            ca = base64.b64decode(cluster['certificate-authority-data'], validate=True)
            certificate = base64.b64decode(user['client-certificate-data'], validate=True)
            key = base64.b64decode(user['client-key-data'], validate=True)
            if _sha(ca) != self.spec.original_ca_sha256:
                raise ValueError
        except (ValueError, KeyError, TypeError) as error:
            raise TCGError('Private kubeconfig identity is invalid') from error
        for name, contents in (('ca.crt', ca), ('client.crt', certificate), ('client.key', key), ('kubeconfig-source.yaml', raw)):
            path = self.directory / name
            if path.exists() and _read(path) != contents:
                raise TCGError('Retained client credential or API CA changed')
            if not path.exists():
                _write(path, contents)

    def _process_identity(self, pid, deadline):
        result = _run(['ps', '-p', str(pid), '-o', 'lstart=', '-o', 'command='], deadline, check=False)
        return _sha(result.stdout) if result.returncode == 0 and result.stdout.strip() else None

    def _tunnel(self, address, operation, deadline):
        state_path = self.directory / 'tunnel.json'
        if state_path.exists():
            saved = _json(_read(state_path))
            if self._process_identity(saved['pid'], deadline) != saved['process_sha256']:
                raise TCGError('Recorded tunnel process disappeared or changed; reconcile first')
            if saved['address'] != address:
                raise TCGError('Tunnel destination changed; explicit cleanup is required')
            return saved
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        control = self.directory / ('ctl-' + uuid4().hex[:8])
        if len(str(control).encode()) > 100:
            raise TCGError('Private tunnel directory is too long for a Unix socket')
        with _ssh_context(self._transport_spec(), deadline, self.directory / 'receipts',
                          control=control, forwarding=True) as argv:
            argv[-1:-1] = ['-N', '-o', 'ControlPersist=no', '-o', 'ExitOnForwardFailure=yes',
                           '-L', f'127.0.0.1:{port}:{address}:6443']
            intent = {'operation_id': operation, 'control': str(control), 'address': address, 'port': port,
                      'argv_sha256': _sha(_canonical(argv)), 'argv': argv}
            _write(self.directory / 'tunnel-intent.json', _canonical(intent))
            log_fd = os.open(self.directory / ('tunnel-' + operation + '.log'), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log_fd, stderr=log_fd, start_new_session=True)
            finally:
                os.close(log_fd)
            # Record the exact process before waiting for connection establishment.
            saved = {**intent, 'pid': process.pid, 'process_sha256': self._process_identity(process.pid, deadline)}
            _write(state_path, _canonical(saved))
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise TCGError('Strict SSH tunnel did not establish')
                if control.exists():
                    return saved
                time.sleep(.25)
            raise TCGError('Tunnel establishment exceeded the fixed deadline')

    def _close_tunnel(self, deadline):
        path = self.directory / 'tunnel.json'
        if not path.exists():
            if (self.directory / 'tunnel-intent.json').exists():
                observed = self._observe_tunnel_intent(deadline)
                if observed['state'] == 'running':
                    _write(path, _canonical(observed['saved']))
                elif observed['state'] == 'absent':
                    intent = _read(self.directory / 'tunnel-intent.json')
                    _write(self.directory / 'receipts' / ('tunnel-not-created-' + uuid4().hex + '.json'), intent)
                    (self.directory / 'tunnel-intent.json').unlink()
                    return {'owned_tunnel_absent': True}
                else:
                    raise TCGError('Unresolved tunnel creation requires reconciliation')
            else:
                return {'owned_tunnel_absent': True}
        saved = _json(_read(path))
        actual = self._process_identity(saved['pid'], deadline)
        if actual is not None:
            if actual != saved['process_sha256']:
                raise TCGError('Refusing to stop a different process at the recorded tunnel PID')
            control = _path(saved['control'])
            if control.parent != self.directory:
                raise TCGError('Tunnel control path escaped its owner directory')
            if control.exists():
                with _ssh_context(self._transport_spec(), deadline, self.directory / 'receipts',
                                  control=control, control_only=True) as argv:
                    argv[-1:-1] = ['-O', 'exit']
                    _run(argv, deadline)
            else:
                os.kill(saved['pid'], signal.SIGTERM)
            while self._process_identity(saved['pid'], deadline) is not None and time.monotonic() < deadline:
                time.sleep(.2)
            if self._process_identity(saved['pid'], deadline) is not None:
                raise TCGError('Owned tunnel shutdown was not observed')
        # Keep immutable completed tunnel metadata, freeing only its active projection.
        _write(self.directory / 'receipts' / ('tunnel-closed-' + uuid4().hex + '.json'), _canonical(saved))
        path.unlink()
        (self.directory / 'tunnel-intent.json').unlink()
        return {'owned_tunnel_absent': True, 'closed_pid': saved['pid']}

    def _observe_tunnel_intent(self, deadline):
        """Read-only recovery of Popen succeeding before its PID was persisted."""
        intent = _json(_read(self.directory / 'tunnel-intent.json'))
        if (set(intent) != {'operation_id', 'control', 'address', 'port', 'argv_sha256', 'argv'}
                or _sha(_canonical(intent['argv'])) != intent['argv_sha256']
                or _path(intent['control']).parent != self.directory):
            return {'state': 'unknown'}
        # Search only the unique owned control path, then compare the exact
        # expected command. No process is signalled by this observation.
        found = _run(['pgrep', '-f', re.escape(intent['control'])], deadline, check=False)
        if found.returncode not in (0, 1):
            return {'state': 'unknown'}
        pids = found.stdout.decode().split()
        if not pids:
            return {'state': 'absent'}
        if len(pids) != 1 or not pids[0].isdigit():
            return {'state': 'unknown'}
        pid = int(pids[0])
        result = _run(['ps', '-p', str(pid), '-o', 'uid=', '-o', 'command='], deadline, check=False)
        fields = result.stdout.decode().strip().split(maxsplit=1)
        if fields != [str(os.getuid()), ' '.join(intent['argv'])]:
            return {'state': 'unknown'}
        identity = self._process_identity(pid, deadline)
        return {'state': 'running', 'saved': {**intent, 'pid': pid, 'process_sha256': identity}}

    def _api(self, port, deadline):
        for name in ('ca.crt', 'client.crt', 'client.key'):
            _read(self.directory / name)
        from devsecops.adapters.http import APIError, HTTPSReadClient
        if _sha(_read(self.directory / 'ca.crt')) != self.spec.original_ca_sha256:
            raise TCGError('Local API CA differs from the original pinned CA')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TCGError('API observation deadline expired')
        client = None
        try:
            client = HTTPSReadClient(f'https://127.0.0.1:{port}', ca_bundle=self.directory / 'ca.crt',
                                     client_certificate=(self.directory / 'client.crt', self.directory / 'client.key'),
                                     timeout=min(8, remaining), total_timeout=min(60, remaining), max_requests=6)
            ready = client.get_bytes('/readyz', accept=('text/plain',)).body.strip() == b'ok'
            version = client.get_json('/version')['gitVersion']
            nodes = client.get_json('/api/v1/nodes')['items']
            namespaces = {name: client.get_json('/api/v1/namespaces/' + name)['metadata']['uid']
                          for name in ('default', 'kube-system')}
            api = client.get_json('/api')
        except APIError as error:
            receipts = client.receipts if client is not None else []
            last = receipts[-1] if receipts else {}
            tls = last.get('tls_failure', {})
            handshake_eof = (error.code in ('tls', 'deadline') and last.get('outcome') == error.code
                             and tls.get('peer_authenticated') is False
                             and tls.get('category') == 'handshake-eof'
                             and tls.get('exception') == 'SSLEOFError'
                             and tls.get('phase') == 'tls-handshake'
                             and tls.get('reason') == 'UNEXPECTED_EOF_WHILE_READING')
            observation = {'ready': False, 'observed_at': _now(), 'code': error.code,
                           'status': error.status, 'transport': receipts,
                           'retryable': handshake_eof or (
                               error.code in ('transport', 'deadline', 'http-error') and 'tls_failure' not in last)}
            if observation['retryable']:
                return observation
            raise TCGAPIError(observation) from None
        if time.monotonic() >= deadline:
            raise TCGError('API observation completed after its fixed deadline')
        fingerprint = {'namespaces': namespaces, 'nodes': {n['metadata']['name']: n['metadata']['uid'] for n in nodes},
                       'ca_sha256': self.spec.original_ca_sha256}
        good = (ready and version == VERSION and len(nodes) == 1 and
                nodes[0]['status']['nodeInfo']['kubeletVersion'] == VERSION and
                any(c['type'] == 'Ready' and c['status'] == 'True' for c in nodes[0]['status']['conditions'])
                and api.get('kind') == 'APIVersions' and 'v1' in api.get('versions', []))
        return {'ready': good, 'server_version': version, 'fingerprint': fingerprint,
                'fingerprint_sha256': _sha(_canonical(fingerprint)), 'observed_at': _now(),
                'tls_hostname_and_ca_verified': True, 'authenticated_api_read': '/api', 'transport': client.receipts}

    def _perform(self, action, operation_id, deadline):
        operation = _id(operation_id)
        if _json(_read(self.directory / 'binding.json')) != {
                'spec': asdict(self.spec), 'driver_sha256': self.source_sha256}:
            raise TCGError('Run identity changed after driver construction')
        end = min(deadline, time.monotonic() + (1200 if action == 'up' else 240 if action == 'down' else 60))
        receipt_path = self.directory / 'receipts' / (operation + '-' + action + '-' + uuid4().hex[:8] + '.json')
        active_path = self.directory / 'active.json'
        active = _json(_read(active_path)) if active_path.exists() else None
        reconciles = None
        reconciled_ids = None
        if action in ('up', 'down'):
            if any((self.directory / 'receipts').glob(operation + '-*.json')):
                raise TCGError('Operation ID already used; replay is forbidden')
            if active:
                reconciled = self.directory / 'reconciliation.json'
                if not reconciled.exists():
                    raise TCGError('Unresolved mutation requires read-only reconciliation')
                proof = _json(_read(reconciled))
                receipt = _json(_read(self.directory / 'receipts' / proof['receipt_name']))
                if (proof['operation_id'] != active['operation_id'] or receipt['operation_id'] != active['operation_id']
                        or proof['receipt_sha256'] != _sha(_read(self.directory / 'receipts' / proof['receipt_name']))
                        or receipt['binding_sha256'] != self.spec.digest or receipt['mutation_effect'] == 'unknown'
                        or receipt['run_id'] != self.spec.run_id or receipt['identities'] != self.spec.identities
                        or receipt['driver_sha256'] != self.source_sha256 or receipt['action'] != 'reconcile'):
                    raise TCGError('Reconciliation does not resolve the active mutation')
                reconciles = active['operation_id']
                reconciled_ids = {kind: receipt['remote'].get(kind + '_id') for kind in ('container', 'network')}
            _write(active_path, _canonical({'operation_id': operation, 'action': action}), replace=active_path.exists())
        elif active and operation != active['operation_id']:
            raise TCGError('Reconciliation must identify the unresolved operation')
        report = {'schema_version': 1, 'kind': 'retained-tcg-observation', 'action': action,
                  'run_id': self.spec.run_id, 'operation_id': operation, 'environment_id': self.spec.environment_id,
                  'identities': self.spec.identities, 'binding_sha256': self.spec.digest,
                  'driver_sha256': self.source_sha256, 'started_at': _now(),
                  'qualification': 'rke2_retained_guest_only', 'state': 'reconciliation_required',
                  'mutation_effect': 'unknown', 'canonical_platform_qualified': False}
        try:
            if action == 'down':
                report['tunnel_cleanup'] = self._close_tunnel(min(end, time.monotonic() + 15))
            remote = self._remote(action, operation, end, reconciles, reconciled_ids)
            report['remote'] = remote
            report['state'] = remote['state']
            if action == 'reconcile':
                # Exact Docker ownership and mutation effect are independently
                # authenticated by the remote driver. A later API identity or
                # readiness failure must not erase that cleanup evidence.
                report['mutation_effect'] = remote['observed_mutation_effect']
            if action == 'up':
                self._credentials(operation, min(end, time.monotonic() + 30))
                tunnel = self._tunnel(remote['address'], operation, min(end, time.monotonic() + 30))
                report['tunnel_identity'] = {key: tunnel[key] for key in
                    ('pid', 'process_sha256', 'control', 'address', 'port', 'operation_id')}
                report['endpoint'] = f'https://127.0.0.1:{tunnel["port"]}'
                observations = []
                # Keep all attempts, including a later fatal TLS observation.
                report['api_observations'] = observations
                consecutive = 0
                while time.monotonic() < end:
                    try:
                        observation = self._api(tunnel['port'], end)
                    except ssl.SSLCertVerificationError:
                        raise TCGError('Pinned API TLS verification failed') from None
                    except urllib.error.URLError as error:
                        if isinstance(error.reason, ssl.SSLCertVerificationError):
                            raise TCGError('Pinned API TLS verification failed') from None
                        observation = {'ready': False, 'observed_at': _now(), 'code': 'api_not_yet_reachable'}
                    except (TimeoutError, ConnectionError):
                        observation = {'ready': False, 'observed_at': _now(), 'code': 'api_not_yet_reachable'}
                    observations.append(observation)
                    consecutive = consecutive + 1 if observation['ready'] else 0
                    if observation['ready']:
                        fingerprint_path = self.directory / 'cluster-fingerprint.json'
                        if fingerprint_path.exists():
                            if _json(_read(fingerprint_path)) != observation['fingerprint']:
                                raise TCGError('Cluster UID fingerprint changed; recreation is not durability')
                        else:
                            _write(fingerprint_path, _canonical(observation['fingerprint']))
                    if consecutive == 3:
                        break
                    time.sleep(min(15, max(0, end - time.monotonic())))
                report['api_observations'] = observations
                if consecutive != 3:
                    raise TCGError('Fixed retained-cluster readiness gate expired')
                report['state'] = 'running_ready'
            elif action == 'reconcile' and remote['state'] == 'running_unready':
                if (self.directory / 'tunnel-intent.json').exists() and not (self.directory / 'tunnel.json').exists():
                    report['tunnel_observation'] = self._observe_tunnel_intent(end)
                path = self.directory / 'tunnel.json'
                if path.exists():
                    tunnel = _json(_read(path))
                    if self._process_identity(tunnel['pid'], end) == tunnel['process_sha256'] and tunnel['address'] == remote['address']:
                        try:
                            report['api_observation'] = self._api(tunnel['port'], end)
                        except (urllib.error.URLError, TimeoutError, ConnectionError, ssl.SSLError):
                            report['api_observation'] = {'ready': False}
                        if report['api_observation']['ready']:
                            if _json(_read(self.directory / 'cluster-fingerprint.json')) != report['api_observation']['fingerprint']:
                                raise TCGError('Cluster UID fingerprint changed')
                            report['state'] = 'running_ready'
            if action != 'reconcile':
                report['mutation_effect'] = 'applied'
        except (TCGError, OSError, ValueError, KeyError, TypeError) as error:
            if isinstance(error, TCGAPIError):
                if action == 'up':
                    report.setdefault('api_observations', []).append(error.observation)
                else:
                    report['api_observation'] = error.observation
            report['error'] = str(error) if isinstance(error, TCGError) else 'Bounded driver observation failed'
        report['finished_at'] = _now()
        _write(receipt_path, _canonical(report))
        if action == 'reconcile' and active and report['mutation_effect'] != 'unknown':
            _write(self.directory / 'reconciliation.json', _canonical({'operation_id': operation,
                   'receipt_name': receipt_path.name, 'receipt_sha256': _sha(_read(receipt_path))}),
                   replace=(self.directory / 'reconciliation.json').exists())
        elif action in ('up', 'down') and report['mutation_effect'] == 'applied':
            active_path.unlink()
        return TCGObservation(report['state'], receipt_path, _sha(_read(receipt_path)), report['mutation_effect'])

    @contextmanager
    def _locked(self):
        path = self.directory / 'lock'
        if not path.exists():
            _write(path, b'')
        fd = _lock_descriptor(path)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)

    def up(self, operation_id, *, deadline):
        with self._locked():
            return self._perform('up', operation_id, deadline)

    def reconcile(self, operation_id, *, deadline):
        with self._locked():
            return self._perform('reconcile', operation_id, deadline)

    def down(self, operation_id, *, deadline):
        with self._locked():
            return self._perform('down', operation_id, deadline)


if __name__ == '__main__':
    os.umask(0o077)
    def interrupted(signum, frame):
        raise TCGError('Remote operation interrupted; pending mutation must be reconciled')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    request = _json(base64.b64decode(sys.argv[1], validate=True))
    try:
        result = _remote_action(request)
        print(json.dumps({'binding_sha256': TCGSpec(**request['spec']).digest,
                          'operation_id': request['operation_id'], 'observation': result}))
    except TCGError as error:
        print(json.dumps({'error': str(error)}))
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps({'error': 'Remote operation failed; preserve ownership and reconcile before retrying'}))
