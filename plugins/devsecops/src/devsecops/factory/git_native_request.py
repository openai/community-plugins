"""Bounded local request bytes from current issuer handles; never delivery authority.

The caller's receive advertisement is untrusted. Matching it to the selected
base is only a local byte-consistency check, not authenticated receive freshness.
The native sender is intentionally cancelled after producing bytes, before any
server status. No network dispatcher, status fabrication or write grant exists.
"""
from contextlib import contextmanager, ExitStack
from copy import deepcopy
import hashlib
import math
import os
from pathlib import Path
import re
import shutil
import stat
import time
import uuid

from devsecops.adapters.artifacts import _open_directory
from devsecops.adapters.vulnerability_policy import checkpoint
from devsecops.factory.deployment_commit import commit_configuration, _object_id, _output_outside_repository
from devsecops.factory.git_base_possession import require_observed_git_base, _OBSERVATIONS
from devsecops.factory.git_transport import (
    _open_request_source, _bind_consumer_audit, open_transport_consumer_output, _directory_at,
    _identity, _snapshot_to, MAX_OBJECT_BYTES, MAX_CLOSURE_RAW,
)
from devsecops.factory.git_transport_process import open_duplex, run
from devsecops.factory.git_request_framing import receive_advertisement, command_pack, read_body
from devsecops.factory.mission_security import _read_retained
from devsecops.runtime.cli import _verify_output_path
from devsecops.runtime.context import canonical_bytes

MAX_REQUEST = 8 * 1024 * 1024
MAX_NATIVE = 32 * 1024 * 1024
OID = re.compile(r'[a-f0-9]{40}')


def _pin(raw):
    return {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}


def _contract(prepared, binding, observed):
    """Classification of locally emitted objects; does not grant remote omission."""
    records = deepcopy(prepared['objects'])
    if (not 1 <= len(records) <= 18 or prepared['commit'] != binding['new_commit']
            or prepared['parent'] != binding['base_commit']
            or prepared['commit'] not in records or observed['new_commit'] != prepared['commit']):
        raise ValueError('Request prepared object scope differs')
    total = 0
    for oid, row in records.items():
        if (not OID.fullmatch(oid) or row['kind'] not in ('commit', 'tree', 'blob')
                or type(row['bytes']) is not int or not 0 <= row['bytes'] <= MAX_OBJECT_BYTES
                or not re.fullmatch(r'[a-f0-9]{64}', row['sha256'])):
            raise ValueError('Request prepared object bound differs')
        total += row['bytes']
    if total > MAX_CLOSURE_RAW:
        raise ValueError('Request aggregate object bound exceeded')
    optional = set()
    for oid in set(records) & set(binding['closure']['objects']):
        row = records[oid]
        remote = observed['objects'].get(oid)
        if (row['kind'] not in ('tree', 'blob') or remote is None
                or row != binding['closure']['objects'][oid]
                or row != {key: remote[key] for key in ('kind', 'bytes', 'sha256')}):
            raise ValueError('Shared object lacks exact current base observation')
        optional.add(oid)
    required = set(records) - optional
    if prepared['commit'] not in required:
        raise ValueError('New commit must be required')
    return records, required, optional


@contextmanager
def _workspace(output, config, audit, deadline, commands):
    """Private sibling scratch anchored to an already-admitted output parent."""
    parent = _open_directory(output.parent)
    parent_identity = _identity(parent)
    name = '.git-request-' + uuid.uuid4().hex
    path = output.parent / name
    root = None
    created = False
    with ExitStack() as stack:
        try:
            audit()
            _verify_output_path(output.parent, parent)
            os.mkdir(name, 0o700, dir_fd=parent)
            created = True
            root = _directory_at(parent, name)
            root_identity = _identity(root)
            tools = {}
            for selected in ('git', 'git-pack-objects'):
                pin = _snapshot_to(Path(config['git_path']), root, selected, 200_000_000, deadline)
                if pin['sha256'] != config['git_sha256']:
                    raise ValueError('Selected native Git changed')
                fd = os.open(selected, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root)
                stack.callback(os.close, fd)
                os.fchmod(fd, 0o500)
                tools[selected] = (pin, fd)
            fixed = ['--no-optional-locks', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null',
                     '-c', 'core.attributesFile=/dev/null', '-c', 'credential.helper=',
                     '-c', 'protocol.allow=never', '-c', 'push.negotiate=false', '-c', 'push.gpgSign=false',
                     '-c', 'pack.window=0', '-c', 'pack.depth=0', '-c', 'pack.threads=1', '-c', 'gc.auto=0']
            env = {'PATH': '', 'HOME': str(path), 'XDG_CONFIG_HOME': str(path), 'LANG': 'C', 'LC_ALL': 'C',
                   'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_TERMINAL_PROMPT': '0',
                   'GIT_NO_REPLACE_OBJECTS': '1', 'GIT_NO_LAZY_FETCH': '1', 'GIT_ALLOW_PROTOCOL': '',
                   'GIT_EXEC_PATH': str(path)}
            consumed = 0
            stores = {}

            def guard():
                audit()
                if _identity(parent) != parent_identity or _identity(root) != root_identity:
                    raise ValueError('Request scratch identity changed')
                _verify_output_path(output.parent, parent)
                _verify_output_path(path, root)
                for n, (pin, fd) in tools.items():
                    _read_retained(root, n, pin, deadline)
                    if _identity(fd) != _identity_at(root, n):
                        raise ValueError('Request tool pathname changed')
                for n, (fd, config_pin) in stores.items():
                    _verify_output_path(path / n, fd)
                    # Sterile bare stores only; no replacement, alternate, graft,
                    # shallow, hook or global configuration can be introduced.
                    if set(os.listdir(fd)) != {'HEAD', 'config', 'objects', 'refs'}:
                        raise ValueError('Private request store membership changed')
                    _read_retained(fd, 'config', config_pin, deadline)
                    for part in ('objects/info', 'refs'):
                        sub = _directory_at(fd, *part.split('/'))
                        try:
                            if os.listdir(sub):
                                raise ValueError('Private request store has unissued metadata')
                        finally:
                            os.close(sub)
                checkpoint(deadline)

            def native(*args, store=None, data=None, limit=64000):
                nonlocal consumed
                guard()
                cap = min(limit, MAX_NATIVE-consumed)
                if cap <= 0:
                    raise ValueError('Native request aggregate budget exceeded')
                code, raw, err, receipt = run([str(path/'git'), *fixed, *args],
                    cwd_fd=root if store is None else stores[store][0],
                    executable_fd=tools['git'][1], executable_sha256=config['git_sha256'],
                    scratch_fd=root, timeout=min(30, deadline-time.monotonic()), limit=cap,
                    env=env, stdin_bytes=data)
                consumed += len(raw)+len(err)
                if (code == 0 and args[:2] == ('index-pack', '--stdin') and store is not None
                        and re.fullmatch(rb'pack\t[a-f0-9]{40}\n', raw)):
                    pack_dir = _directory_at(stores[store][0], 'objects', 'pack')
                    try:
                        for suffix in ('pack', 'idx'):
                            fd = os.open('pack-'+raw[5:].strip().decode()+'.'+suffix,
                                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=pack_dir)
                            try:
                                info = os.fstat(fd)
                                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
                                    raise ValueError('Generated pack type or ownership differs')
                                os.fchmod(fd, 0o400)
                            finally:
                                os.close(fd)
                    finally:
                        os.close(pack_dir)
                commands.append({'operation': args[0], **receipt})
                guard()
                if code or consumed > MAX_NATIVE:
                    raise ValueError('Native request validation failed')
                return raw

            def new_store(n):
                native('init', '--bare', '--quiet', '--template=', '--object-format=sha1', n)
                fd = _directory_at(root, n)
                stack.callback(os.close, fd)
                # git init makes empty refs/heads and refs/tags. Retain no refs.
                for child in ('heads', 'tags'):
                    refs = _directory_at(fd, 'refs')
                    try:
                        os.rmdir(child, dir_fd=refs)
                    finally:
                        os.close(refs)
                cfg = os.open('config', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    raw = os.read(cfg, 64001)
                    if len(raw) > 64000:
                        raise ValueError('Private Git config exceeds bound')
                    os.fchmod(cfg, 0o400)
                finally:
                    os.close(cfg)
                stores[n] = (fd, _pin(raw))
                guard()

            @contextmanager
            def sender(args):
                nonlocal consumed
                guard()
                cap = min(MAX_REQUEST+256*1024, MAX_NATIVE-consumed)
                if cap <= 0:
                    raise ValueError('Native request aggregate budget exceeded')
                with open_duplex([str(path/'git'), *fixed, *args],
                        cwd_fd=stores['objects.git'][0], executable_fd=tools['git'][1],
                        executable_sha256=config['git_sha256'], timeout=min(60, deadline-time.monotonic()),
                        stdout_limit=cap, stderr_limit=64000, input_limit=2*1024*1024,
                        combined_output_limit=cap, env=env) as child:
                    yield child
                    # Request-only preparation deliberately stops before server
                    # status. close() verifies owned-group cleanup; no exit0 claim.
                    child.close()
                    consumed += cap  # Conservatively reserve the entire sender allowance.
                guard()

            def verify_pack_pin(store, checksum, expected):
                guard()
                if not OID.fullmatch(checksum):
                    raise ValueError('Invalid private pack checksum')
                directory = _directory_at(stores[store][0], 'objects', 'pack')
                try:
                    _read_retained(directory, 'pack-'+checksum+'.pack', expected, deadline)
                finally:
                    os.close(directory)
                guard()

            yield native, new_store, sender, guard, verify_pack_pin
            guard()
        finally:
            stack.close()
            try:
                if created:
                    if root is not None:
                        _verify_output_path(path, root)
                    _verify_output_path(output.parent, parent)
                    shutil.rmtree(name, dir_fd=parent)
                    os.fsync(parent)
            finally:
                if root is not None:
                    os.close(root)
                os.close(parent)


def _identity_at(fd, name):
    row = os.stat(name, dir_fd=fd, follow_symlinks=False)
    return row.st_dev, row.st_ino


def _emitted_contract(native, pack, records, required, optional, verify_pack_pin):
    pin = _pin(pack)
    indexed = native('index-pack', '--stdin', store='emitted.git', data=pack)
    if not re.fullmatch(rb'pack\t[a-f0-9]{40}\n', indexed):
        raise ValueError('Emitted pack index identity differs')
    checksum = indexed[5:].strip().decode()
    verify_pack_pin('emitted.git', checksum, pin)
    raw = native('verify-pack', '-v', 'objects/pack/pack-'+checksum+'.idx', store='emitted.git')
    inventory = {}
    for line in raw.splitlines():
        parts = line.split()
        if parts and re.fullmatch(rb'[a-f0-9]{40}', parts[0]):
            oid = parts[0].decode()
            if len(parts) != 5 or oid in inventory:
                raise ValueError('Emitted pack has delta or duplicate objects')
            inventory[oid] = (parts[1].decode(), int(parts[2]))
    actual = set(inventory)
    if not required <= actual <= set(records):
        raise ValueError('Emitted pack object set differs')
    present = native('cat-file', '--batch-all-objects', '--batch-check=%(objectname)', store='emitted.git')
    if present.splitlines() != [oid.encode() for oid in sorted(actual)]:
        raise ValueError('Emitted-only store inventory differs')
    for oid in sorted(actual):
        row = records[oid]
        if inventory[oid] != (row['kind'], row['bytes']):
            raise ValueError('Emitted pack object kind or size differs')
        raw = native('cat-file', row['kind'], oid, store='emitted.git', limit=max(1, row['bytes']))
        if _pin(raw) != {k: row[k] for k in ('sha256', 'bytes')} or _object_id(row['kind'], raw) != oid:
            raise ValueError('Emitted raw object identity differs')
    verify_pack_pin('emitted.git', checksum, pin)
    return {'required': sorted(required), 'optional_shared': sorted(optional), 'emitted': sorted(actual),
            'omitted_shared': sorted(optional-actual), 'pack': pin,
            'scope': 'local-object-classification-only', 'omission_authorized': False}


def prepare_native_git_request(*, transport, observed_base, run_scope, source_commit,
        deployment_commit, proposal_configuration_sha256, configuration_sha256,
        expected_destination_sha256, advertisement, output, timeout=120):
    """Return (local evidence report, bytes), after owned sender/scratch cleanup.

    Returned bytes and retained JSON are data only. A future dispatcher must
    independently admit current principal/grants, receive freshness, ref lease,
    one-attempt durable intent and effect reconciliation. There is no dispatcher.
    """
    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 120
            or type(advertisement) is not bytes or not isinstance(output, Path)
            or not output.is_absolute() or '..' in output.parts):
        raise ValueError('Bounded local request arguments required')
    deadline = time.monotonic()+timeout
    started_wall = time.time()
    started_mono = time.monotonic()
    scope = dict(run_scope=run_scope, source_commit=source_commit, deployment_commit=deployment_commit,
                 proposal_configuration_sha256=proposal_configuration_sha256, configuration_sha256=configuration_sha256)
    with ExitStack() as source_stack:
        binding, prepared, source_fd, source_audit = source_stack.enter_context(
            _open_request_source(transport, scope=scope, deadline=deadline))
        base_args = dict(transport=transport, **scope, expected_config_sha256=expected_destination_sha256, deadline=deadline)
        observed = require_observed_git_base(observed_base, **base_args)
        base_record = _OBSERVATIONS[observed_base]
        base_path, base_identity = base_record['output'], base_record['output_identity']
        selected = observed['selection']
        heads = receive_advertisement(advertisement, base_ref=selected['base_ref'],
            base_commit=selected['base_commit'], target_ref=selected['target_ref'])
        records, required, optional = _contract(prepared, binding, observed)

        def clock_guard():
            mono, wall = time.monotonic()-started_mono, time.time()-started_wall
            if min(mono, wall) < 0 or max(mono, wall) >= timeout or abs(mono-wall) > 2:
                raise ValueError('Request clock or deadline bound exceeded')

        def observation_guard():
            clock_guard()
            if _output_outside_repository(output, base_path, deadline) != base_identity:
                raise ValueError('Base observation source route changed')
            if require_observed_git_base(observed_base, **base_args) != observed:
                raise ValueError('Current base observation changed')
            clock_guard()

        def input_audit():
            source_audit()
            observation_guard()

        input_audit()
        config = commit_configuration(_read_retained(source_fd, 'commit-config.json',
            binding['artifacts']['commit-config.json'], deadline, materialize=True))
        with open_transport_consumer_output(transport, **scope, output=output, deadline=deadline) as admitted:
            _bind_consumer_audit(admitted, observation_guard)

            def audit():
                input_audit()
                admitted.audit()

            commands = []
            with _workspace(output, config, audit, deadline, commands) as (native, new_store, sender, guard, verify_pack_pin):
                new_store('objects.git')
                for name in ('base.pack', 'prepared.pack'):
                    raw = _read_retained(source_fd, name, binding['artifacts'][name], deadline, materialize=True)
                    result = native('index-pack', '--stdin', store='objects.git', data=raw)
                    if not re.fullmatch(rb'pack\t[a-f0-9]{40}\n', result):
                        raise ValueError('Pinned transport pack import failed')
                actual = native('cat-file', '--batch-all-objects', '--batch-check=%(objectname)', store='objects.git')
                if actual.splitlines() != [x.encode() for x in binding['closure']['scratch_object_ids']]:
                    raise ValueError('Request store object membership differs')
                argv = ['send-pack', '--stateless-rpc', '--helper-status', '--no-progress', '--no-thin', '--no-signed',
                        '--force-with-lease='+selected['target_ref']+':',
                        selected['origin']+selected['repository_path'], prepared['commit']+':'+selected['target_ref']]
                with sender(argv) as child:
                    child.write(heads)
                    body = read_body(child)
                    pack = command_pack(body, new_commit=prepared['commit'], target_ref=selected['target_ref'])
                    if len(body) > MAX_REQUEST:
                        raise ValueError('Request evidence exceeds bounded output')
                new_store('emitted.git')
                contract = _emitted_contract(native, pack, records, required, optional, verify_pack_pin)
                guard()
            audit()  # owned native cleanup and private scratch removal preceded evidence.
            report = {'kind': 'mission-local-native-git-request', 'decision': 'request-prepared',
                'factory_decision': 'unknown', 'delivery_authority': 'not-issued', 'omission_authorized': False,
                'scope': 'local-request-bytes-only', 'network_requests': 0, 'write_attempts': 0,
                'mutation_effect': 'not-attempted', 'sender_outcome': 'cancelled-before-server-status',
                'native_delivery_exit': None, 'owned_sender_cleanup': True, 'private_scratch_removed': True,
                'selection': deepcopy(selected), 'new_commit': prepared['commit'],
                'advertisement': {**_pin(advertisement), 'authenticated_receive_freshness': False},
                'prepared_pack': deepcopy(binding['prepared_pack']), 'request': _pin(body),
                'object_contract': contract, 'commands': commands}
            admitted.write_bytes('request.body', body)
            admitted.write_bytes('native-request.json', canonical_bytes(report), terminal=True)
            audit()
            # Run the source context's final audit and close while admission
            # still owns terminal withdrawal on every exception.
            source_stack.close()
            if require_observed_git_base(observed_base, **base_args) != observed:
                raise ValueError('Base observation changed at terminal release')
        return report, body
