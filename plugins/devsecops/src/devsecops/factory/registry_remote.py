"""Fixed registry Docker operations, transported with verified stdlib support.

Only `execute` is called by the installed component. There is no command field,
image acquisition, credential creation, port publication or host configuration.
"""
from __future__ import annotations

import base64
import fcntl
import hashlib
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
import stat
import time

LABEL = 'io.devsecops.registry.binding'
OPERATION = 'io.devsecops.registry.operation'



def _purge_owned_payload(root, expected_identity, deadline):
    """Delete only inventoried owned payload trees, never metadata or parents.

    A full no-follow, same-device inventory precedes the first unlink. Open
    directory descriptors keep traversal inside the observed generation. Hard
    links, devices, sockets, symlinks, mounts and changed inodes stop cleanup.
    """
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open(root, flags)
    try:
        info = os.fstat(directory)
        if (info.st_dev, info.st_ino) != expected_identity or info.st_uid != os.getuid():
            raise ValueError('Registry purge generation changed')
        nodes = []
        def inventory(fd, parts=()):
            if len(parts) > 20 or len(nodes) > 20000 or time.monotonic() >= deadline:
                raise ValueError('Registry purge inventory exceeds bound')
            for name in sorted(os.listdir(fd)):
                if len(nodes) >= 20000 or time.monotonic() >= deadline:
                    raise ValueError('Registry purge inventory exceeds bound')
                current = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if (current.st_dev != info.st_dev or current.st_uid != os.getuid()
                        or not (stat.S_ISREG(current.st_mode) or stat.S_ISDIR(current.st_mode))
                        or stat.S_ISREG(current.st_mode) and current.st_nlink != 1):
                    raise ValueError('Registry purge encountered unowned or linked data')
                entry = (parts+(name,), current.st_dev, current.st_ino, stat.S_ISDIR(current.st_mode))
                nodes.append(entry)
                if entry[3]:
                    child = os.open(name, flags, dir_fd=fd)
                    try:
                        actual = os.fstat(child)
                        if (actual.st_dev, actual.st_ino) != entry[1:3]:
                            raise ValueError('Registry purge inventory changed')
                        inventory(child, entry[0])
                    finally:
                        os.close(child)
        roots = ('data', 'config', 'probe-input', 'probe-output')
        for name in roots:
            try:
                current = os.stat(name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if (not stat.S_ISDIR(current.st_mode) or current.st_dev != info.st_dev
                    or current.st_uid != os.getuid()):
                raise ValueError('Registry purge root is not the owned directory')
            nodes.append(((name,), current.st_dev, current.st_ino, True))
            child = os.open(name, flags, dir_fd=directory)
            try:
                actual = os.fstat(child)
                if (actual.st_dev, actual.st_ino) != (current.st_dev, current.st_ino):
                    raise ValueError('Registry purge root changed')
                inventory(child, (name,))
            finally:
                os.close(child)
        for parts, device, inode, is_directory in reversed(nodes):
            if time.monotonic() >= deadline:
                raise ValueError('Registry purge deadline expired')
            parent = os.dup(directory)
            try:
                for name in parts[:-1]:
                    child = os.open(name, flags, dir_fd=parent)
                    os.close(parent)
                    parent = child
                current = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
                if (current.st_dev, current.st_ino) != (device, inode):
                    raise ValueError('Registry purge target changed')
                (os.rmdir if is_directory else os.unlink)(parts[-1], dir_fd=parent)
                os.fsync(parent)
            finally:
                os.close(parent)
        if any(name in os.listdir(directory) for name in roots):
            raise ValueError('Registry private payload remains')
        os.fsync(directory)
    finally:
        os.close(directory)


def execute(payload, support, recipes):
    """The support module is the package's existing bounded TCG transport code."""
    recipe = recipes.validate(payload['recipe'])
    action, operation = payload['action'], payload['operation_id']
    if action not in ('up', 'down', 'reconcile') or not re.fullmatch('[a-f0-9-]{36}', operation):
        raise ValueError('Unsupported fixed registry operation')
    expected = recipes.binding(recipe, run_id=payload['run_id'], identities=payload['identities'],
                               package_modules=payload['package_modules'])
    if (payload['binding'] != expected or type(payload['timeout']) not in (int, float)
            or not 0 < payload['timeout'] <= 240):
        raise ValueError('Registry operation binding or deadline differs')
    expiry = datetime.fromisoformat(payload['expires_at'].replace('Z', '+00:00'))
    if expiry.tzinfo is None or action == 'up' and datetime.now(timezone.utc) >= expiry:
        raise ValueError('Registry provisioning window expired')
    if action == 'up' and (os.getuid() == 0 or os.geteuid() == 0):
        raise ValueError('Registry provisioning requires a non-root operator UID')
    deadline = time.monotonic()+payload['timeout']
    root = Path(recipe['remote_directory'])
    parent = support._private(root.parent)
    if not root.exists():
        if action != 'up':
            return {'bootstrap': 'absent', 'resources': [], 'storage_qualification': 'not-observed',
                    'cleanup': 'not-observed', 'mutation_effect': 'unknown'}
        support._private(root, create=True)
        support._write(root/'owner.json', recipes.canonical({'binding': expected, 'created_by': operation}))
        support._write(root/'lock', b'')
    support._private(root)
    root_identity = root.stat()
    if support._json(support._read(root/'owner.json'))['binding'] != expected:
        raise ValueError('Registry private directory belongs to another binding')
    locked = support._lock_descriptor(root/'lock')
    fcntl.flock(locked, (fcntl.LOCK_SH if action == 'reconcile' else fcntl.LOCK_EX) | fcntl.LOCK_NB)
    report = {'bootstrap': 'unknown', 'storage_qualification': 'not-observed', 'cleanup': 'incomplete',
              'resources': [], 'mutation_effect': 'unknown', 'canonical_success': False}
    state_path = root/'state.json'
    state = support._json(support._read(state_path)) if state_path.exists() else {
        'history': [], 'inflight': None, 'ids': {}, 'storage_identity': None,
        'probe_settings_sha256': None, 'bootstrap': 'not-started'}
    prefix = 'devsecops-registry-'+expected[:20]
    names = {'network': prefix+'-net', 'registry': prefix+'-zot', 'probe': prefix+'-probe'}
    if (not isinstance(state, dict) or not isinstance(state.get('history'), list)
            or len(state['history']) > 128 or not isinstance(state.get('ids'), dict)
            or set(state['ids']) - set(names)
            or any(not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value) for value in state['ids'].values())):
        os.close(locked)
        raise ValueError('Registry retained state is invalid')
    inflight = state.get('inflight')
    if inflight is not None and (not isinstance(inflight, dict)
            or set(inflight) != {'operation_id', 'role', 'verb'}
            or inflight['operation_id'] not in state['history']
            or inflight['role'] not in names or inflight['verb'] not in ('create', 'start', 'stop', 'remove')):
        os.close(locked)
        raise ValueError('Registry retained inflight intent is invalid')

    purge = state.get('private_purge')
    if purge is not None and (not isinstance(purge, dict)
            or set(purge) != {'operation_id', 'state', 'root_identity'}
            or purge['operation_id'] not in state['history'] or purge['state'] not in ('unknown', 'absent')
            or purge['root_identity'] != {'device': root_identity.st_dev, 'inode': root_identity.st_ino}
            or recipe.get('cleanup') != 'remove-owned-private-material'):
        os.close(locked)
        raise ValueError('Registry purge ownership generation changed')

    def check():
        if time.monotonic() >= deadline:
            raise ValueError('Registry operation deadline expired')
        support._private(parent)
        support._private(root)
        current = root.stat()
        if (current.st_dev, current.st_ino) != (root_identity.st_dev, root_identity.st_ino):
            raise ValueError('Registry private directory was replaced')
        if action == 'up' and datetime.now(timezone.utc) >= datetime.fromisoformat(payload['expires_at'].replace('Z', '+00:00')):
            raise ValueError('Registry provisioning window expired')
        purging = state.get('private_purge') is not None
        if state['storage_identity'] is not None and not purging:
            data = support._private(root/'data').stat()
            if state['storage_identity'] != {'device': data.st_dev, 'inode': data.st_ino}:
                raise ValueError('Registry storage directory was replaced')
        # Private files are immutable selected inputs, not merely trusted by
        # their original creation. Verify their bytes before each Docker call.
        for key, filename in (('ca', 'ca.crt'), ('certificate', 'certificate.pem'),
                              ('private_key', 'private-key.pem'), ('htpasswd', 'htpasswd')):
            path = root/'config'/filename
            if path.exists() and hashlib.sha256(support._read(path, 1000000)).hexdigest() != recipe['materials'][key]['sha256']:
                raise ValueError('Registry mounted material changed')
        configured = root/'config/config.json'
        if configured.exists() and support._read(configured) != recipes.canonical(recipes.configuration(recipe)):
            raise ValueError('Registry mounted configuration changed')
        zipped = root/'probe-input/probe.zip'
        if zipped.exists() and hashlib.sha256(support._read(zipped)).hexdigest() != payload['probe_zip_sha256']:
            raise ValueError('Registry qualification code changed')
        settings = root/'probe-input/qualification.json'
        settings_sha = state.get('probe_settings_sha256')
        if settings.exists() or settings_sha is not None and not purging:
            if (not isinstance(settings_sha, str) or not re.fullmatch('[a-f0-9]{64}', settings_sha)
                    or hashlib.sha256(support._read(settings, 16384)).hexdigest() != settings_sha):
                raise ValueError('Registry mounted qualification settings changed or lack a retained pin')
            if hashlib.sha256(support._read(root/'probe-input/ca.crt', 1000000)).hexdigest() != recipe['materials']['ca']['sha256']:
                raise ValueError('Registry mounted qualification CA changed')
        if support._json(support._read(root/'owner.json'))['binding'] != expected:
            raise ValueError('Registry directory ownership changed')

    def save():
        check()
        support._write(state_path, recipes.canonical(state), replace=state_path.exists())

    def command(argv, seconds=30, check_status=True):
        check()
        result = support._run(argv, min(deadline, time.monotonic()+seconds), check=check_status)
        check()
        return result

    def inspect(kind, identifier):
        result = command(['docker', kind, 'inspect', identifier], check_status=False)
        if result.returncode:
            if b'No such' in result.stderr or b'not found' in result.stderr:
                return None
            raise ValueError('Registry Docker observation failed')
        value = support._json(result.stdout)
        if not isinstance(value, list) or len(value) != 1:
            raise ValueError('Unexpected registry Docker inspection')
        return value[0]

    def observed():
        found = {}
        for role in names:
            kind = 'network' if role == 'network' else 'container'
            item = inspect(kind, state['ids'].get(role, names[role]))
            extras = command(['docker', kind, 'ls', *(['-a'] if kind == 'container' else []), '-q', '--no-trunc',
                '--filter', 'label='+LABEL+'='+expected, '--filter', 'label=io.devsecops.registry.role='+role]).stdout.decode().split()
            if item is not None:
                labels = item.get('Labels', {}) if role == 'network' else item.get('Config', {}).get('Labels', {})
                ident = item.get('Id')
                creation = labels.get(OPERATION)
                if (not isinstance(ident, str) or not re.fullmatch('[a-f0-9]{64}', ident)
                        or labels.get(LABEL) != expected or labels.get('io.devsecops.registry.role') != role
                        or creation not in state['history'] or extras != [ident]
                        or item.get('Name', '').removeprefix('/') != names[role]
                        or state['ids'].get(role, ident) != ident):
                    raise ValueError('Registry resource identity or ownership differs')
                if role == 'network':
                    if item.get('Internal') is not True or item.get('Driver') != 'bridge':
                        raise ValueError('Registry network isolation differs')
                else:
                    host, config = item.get('HostConfig', {}), item.get('Config', {})
                    image = recipe['image']['image_id'] if role == 'registry' else recipe['tool_image']
                    networks = item.get('NetworkSettings', {}).get('Networks', {})
                    mounts = item.get('Mounts', [])
                    wanted = [('config', '/config', False), ('data', '/data', True)] if role == 'registry' else [
                        ('probe-input', '/input', False), ('probe-output', '/evidence', True)]
                    actual_mounts = sorted((m.get('Source'), m.get('Destination'), m.get('RW')) for m in mounts)
                    wanted_mounts = sorted((str(root/name), destination, writable) for name, destination, writable in wanted)
                    entrypoint = [recipe['image']['entrypoint']] if role == 'registry' else ['/usr/bin/python3']
                    args = ['serve', '/config/config.json'] if role == 'registry' else ['-I', '-B', '/input/probe.zip']
                    if (item.get('Image') != image or config.get('User') != f'{os.getuid()}:{os.getgid()}'
                            or config.get('Entrypoint') != entrypoint or config.get('Cmd') != args
                            or config.get('Healthcheck', {}).get('Test') != ['NONE']
                            or host.get('Privileged') is not False or host.get('ReadonlyRootfs') is not True
                            or host.get('CapAdd') or host.get('CapDrop') != ['ALL']
                            or host.get('SecurityOpt') not in (['no-new-privileges'], ['no-new-privileges=true'])
                            or host.get('PortBindings') or host.get('PidMode') not in ('', None)
                            or host.get('IpcMode') not in ('private', '') or host.get('Devices')
                            or host.get('NetworkMode') != names['network'] or host.get('Binds')
                            or host.get('PidsLimit') != 128 or host.get('Memory') != 1024**3
                            or host.get('MemorySwap') != 1024**3 or host.get('NanoCpus') != 10**9
                            or host.get('RestartPolicy', {}).get('Name') not in ('no', '')
                            or set(networks) != {names['network']} or actual_mounts != wanted_mounts):
                        raise ValueError('Registry container isolation differs')
                    network = found.get('network')
                    attached = networks[names['network']].get('NetworkID')
                    created = item.get('State', {}).get('Status') == 'created' and not item['State'].get('Running')
                    if network is None or attached != network['Id'] and not (created and attached in ('', None)):
                        raise ValueError('Registry container network identity differs')
                found[role] = item
            elif extras:
                raise ValueError('Unexpected owned registry resources require reconciliation')
            else:
                found[role] = None
        return found

    def mutate(argv, role, verb, seconds=30):
        state['inflight'] = {'operation_id': operation, 'role': role, 'verb': verb}
        save()
        result = command(argv, seconds)
        # Store acknowledgement only after a separate inspect by the caller.
        return result

    def retain_material(name, raw, maximum=1000000):
        if not isinstance(raw, bytes) or not 1 <= len(raw) <= maximum:
            raise ValueError('Registry material exceeds its bound')
        path = root/name
        if path.exists():
            if support._read(path, maximum) != raw:
                raise ValueError('Registry private material changed')
        else:
            support._write(path, raw)

    try:
        resources = observed()
        if action == 'reconcile':
            report['inflight'] = state['inflight']
        elif action == 'up':
            if state['inflight'] or operation in state['history']:
                raise ValueError('Registry operation already attempted; reconcile without retry')
            if any(resources.values()):
                raise ValueError('Existing registry requires read-only observation, not recreation')
            # Independent local cache identity checks. Docker never pulls images.
            for selected, reference in ((recipe['image']['image_id'], recipe['image']['reference']),
                                        (recipe['tool_image'], None)):
                image = inspect('image', selected)
                if (image is None or image.get('Id') != selected or image.get('Os') != 'linux'
                        or image.get('Architecture') != 'amd64'
                        or reference is not None and (reference not in image.get('RepoDigests', [])
                            or image.get('Config', {}).get('Entrypoint') != [recipe['image']['entrypoint']])):
                    raise ValueError('Pinned Linux amd64 image must already exist in the local cache')
            if shutil.disk_usage(root).free < 2*1024**3:
                raise ValueError('Registry storage headroom is insufficient')
            for name in ('config', 'data', 'probe-input', 'probe-output'):
                support._private(root/name, create=True)
            info = (root/'data').stat()
            state['storage_identity'] = {'device': info.st_dev, 'inode': info.st_ino}
            for key, filename in (('ca', 'ca.crt'), ('certificate', 'certificate.pem'),
                                  ('private_key', 'private-key.pem'), ('htpasswd', 'htpasswd')):
                raw = base64.b64decode(payload['materials'][key], validate=True)
                if hashlib.sha256(raw).hexdigest() != recipe['materials'][key]['sha256']:
                    raise ValueError('Registry material pin differs')
                retain_material('config/'+filename, raw)
            retain_material('config/config.json', recipes.canonical(recipes.configuration(recipe)))
            retain_material('probe-input/ca.crt', base64.b64decode(payload['materials']['ca'], validate=True))
            probe = base64.b64decode(payload['probe_zip'], validate=True)
            if hashlib.sha256(probe).hexdigest() != payload['probe_zip_sha256']:
                raise ValueError('Registry probe package differs')
            retain_material('probe-input/probe.zip', probe)
            settings = payload['probe_settings']
            if settings['origin'] != recipe['origin'] or settings['repository'] != recipe['repository']+'/qualification' or settings['binding'] != expected:
                raise ValueError('Registry probe scope differs')
            if settings.get('expires_at') != payload['expires_at']:
                raise ValueError('Registry probe window differs')
            settings_raw = recipes.canonical(settings)
            retain_material('probe-input/qualification.json', settings_raw, 16384)
            state['probe_settings_sha256'] = hashlib.sha256(settings_raw).hexdigest()
            state['history'].append(operation)
            save()
            def labels(role):
                return ['--label', LABEL+'='+expected, '--label', OPERATION+'='+operation,
                        '--label', 'io.devsecops.registry.role='+role]
            network = mutate(['docker', 'network', 'create', '--internal', *labels('network'), names['network']], 'network', 'create')
            state['ids']['network'] = network.stdout.decode().strip()
            if not re.fullmatch('[a-f0-9]{64}', state['ids']['network']):
                raise ValueError('Registry network creation did not return an exact ID')
            resources = observed()
            state['inflight'] = None
            save()
            for role in ('registry', 'probe'):
                mounts = [('config', '/config', True), ('data', '/data', False)] if role == 'registry' else [
                    ('probe-input', '/input', True), ('probe-output', '/evidence', False)]
                argv = ['docker', 'create', '--pull=never', '--name', names[role], *labels(role),
                    '--user', f'{os.getuid()}:{os.getgid()}', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                    '--read-only', '--no-healthcheck', '--pids-limit', '128', '--cpus', '1', '--memory', str(1024**3),
                    '--memory-swap', str(1024**3), '--ulimit', 'core=0', '--network', names['network']]
                for source, target, readonly in mounts:
                    argv += ['--mount', f'type=bind,src={root/source},dst={target}'+(',readonly' if readonly else '')]
                if role == 'registry':
                    argv += ['--network-alias', recipes.hostname(recipe), '--entrypoint', recipe['image']['entrypoint'],
                             recipe['image']['image_id'], 'serve', '/config/config.json']
                else:
                    # Python admission is native execution in the exact tool
                    # image, before this fixed entry point can attempt uploads.
                    argv += ['--entrypoint', '/usr/bin/python3', recipe['tool_image'], '-I', '-B', '/input/probe.zip']
                created = mutate(argv, role, 'create')
                state['ids'][role] = created.stdout.decode().strip()
                if not re.fullmatch('[a-f0-9]{64}', state['ids'][role]):
                    raise ValueError('Registry creation did not return an exact ID')
                resources = observed()
                state['inflight'] = None
                save()
                mutate(['docker', 'start', state['ids'][role]], role, 'start')
                resources = observed()
                state['inflight'] = None
                if role == 'registry':
                    if not resources[role]['State']['Running']:
                        raise ValueError('Registry did not start')
                    state['bootstrap'] = 'container-running-readiness-unverified'
                save()
            # Wait on only the owned probe. No automatic re-run on failure.
            result = command(['docker', 'wait', state['ids']['probe']], 100)
            resources = observed()
            if result.stdout.strip() != b'0':
                report['storage_qualification'] = 'unknown'
            elif (root/'probe-output/qualification.json').exists():
                raw = support._read(root/'probe-output/qualification.json', 1000000)
                qualification = support._json(raw)
                if (qualification.get('binding_sha256') != expected or qualification.get('origin') != recipe['origin']
                        or qualification.get('repository') != recipe['repository']+'/qualification'
                        or qualification.get('kind') != 'registry-storage-qualification-only'
                        or qualification.get('canonical_success') is not False):
                    raise ValueError('Registry qualification output identity differs')
                report['qualification'] = qualification
                report['storage_qualification'] = qualification['status']
            state['qualification_attempted'] = True
            save()
        elif inflight is not None and inflight['verb'] == 'create' and resources[inflight['role']] is None:
            # A timed-out client does not cancel a Docker daemon request. An
            # absent target can still appear later; preserve the original
            # creation intent and do not remove its network or replace it with
            # a cleanup intent. Only later observation of that exact creation
            # can establish a target that this component may remove.
            report['cleanup'] = 'unknown-create-still-inflight'
        else:
            # Cleanup may follow a failed creation; reconcile each exact object
            # before removing it. A named match without owned intent is rejected.
            if inflight is not None and inflight['verb'] == 'create':
                target = resources[inflight['role']]
                labels = target['Labels'] if inflight['role'] == 'network' else target['Config']['Labels']
                if labels.get(OPERATION) != inflight['operation_id']:
                    raise ValueError('Observed creation differs from unresolved registry intent')
            if operation not in state['history']:
                state['history'].append(operation)
                save()
            for role in ('probe', 'registry'):
                item = resources[role]
                if item:
                    if item['State']['Running']:
                        mutate(['docker', 'stop', '--time', '10', item['Id']], role, 'stop', 20)
                        resources = observed()
                    mutate(['docker', 'rm', item['Id']], role, 'remove')
                    resources = observed()
                    if resources[role] is not None:
                        raise ValueError('Registry container deletion is not observed')
            if resources['network']:
                if resources['network'].get('Containers'):
                    raise ValueError('Registry network contains an undeleted member')
                mutate(['docker', 'network', 'rm', resources['network']['Id']], 'network', 'remove')
                resources = observed()
            # Storage and credentials are retained intentionally in this first
            # component. It cannot release the factory environment or promise
            # irreversible data deletion from a reusable service directory.
            state['inflight'] = None
            save()
            report['cleanup'] = 'owned-runtime-absent-private-material-retained'
            if recipe.get('cleanup') == 'remove-owned-private-material':
                if state.get('private_purge') is not None:
                    raise ValueError('Private material purge already attempted; reconcile without retry')
                state['private_purge'] = {'operation_id': operation, 'state': 'unknown',
                    'root_identity': {'device': root_identity.st_dev, 'inode': root_identity.st_ino}}
                save()
                _purge_owned_payload(root, (root_identity.st_dev, root_identity.st_ino), deadline)
                state['private_purge']['state'] = 'absent'
                save()
                report['cleanup'] = 'owned-runtime-and-private-material-absent'
        report['bootstrap'] = 'container-running' if resources.get('registry') and resources['registry']['State']['Running'] else 'absent'
        for role, item in resources.items():
            report['resources'].append({'role': role, 'id': item['Id'] if item else None,
                'state': 'present' if item else 'absent', 'ownership': 'verified' if item else 'not-observed', 'disposition': 'remove'})
        purged = (state.get('private_purge', {}).get('operation_id') == operation
            and not any((root/name).exists() or (root/name).is_symlink()
                        for name in ('data', 'config', 'probe-input', 'probe-output'))
            and not any(resources.values()) and state['inflight'] is None)
        if action == 'reconcile' and purged:
            report['cleanup'] = 'owned-runtime-and-private-material-absent'
        report['resources'].append({'role': 'private-storage-and-credentials', 'path': str(root),
            'storage_identity': state['storage_identity'], 'state': 'absent' if purged else 'retained',
            'disposition': 'remove' if recipe.get('cleanup') == 'remove-owned-private-material' else 'preserve'})
        report['resources'].append({'role': 'ownership-and-reconciliation-evidence', 'path': str(root),
            'identity': {'device': root_identity.st_dev, 'inode': root_identity.st_ino},
            'state': 'preserved', 'disposition': 'preserve'})
        for role, image in (('zot-image-cache', recipe['image']['image_id']), ('probe-tool-image-cache', recipe['tool_image'])):
            report['resources'].append({'role': role, 'identity': image, 'disposition': 'preserve',
                                        'ownership': 'operator-selected-prerequisite', 'created_by_component': False})
        report['inflight'] = state['inflight']
        check()
        return report
    finally:
        os.close(locked)
