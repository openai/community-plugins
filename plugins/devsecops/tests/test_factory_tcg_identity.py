"""Controlled guest filesystem/process fixtures; these are not live RKE2 proof."""
import copy
import hashlib
import json
import stat
from pathlib import Path
import subprocess

import pytest

from devsecops.factory import tcg


def closed_seed():
    return b'#cloud-config\n' + tcg._canonical({
        'users': [{'name': 'demo', 'groups': ['sudo'], 'shell': '/bin/bash', 'lock_passwd': True,
                   'sudo': ['ALL=(ALL) NOPASSWD:ALL'], 'ssh_authorized_keys': ['ssh-ed25519 unit']}],
        'ssh_pwauth': False, 'disable_root': True, 'package_update': False, 'package_upgrade': False,
        'ssh_deletekeys': True, 'ssh_genkeytypes': [],
        'ssh_keys': {'ed25519_private': '-----BEGIN OPENSSH PRIVATE KEY-----\nfixture only',
                     'ed25519_public': 'ssh-ed25519 unit'},
        'runcmd': [['touch', '/run/devsecops-probe-ready']],
    })


@pytest.fixture
def guest(tmp_path):
    # Execute exactly the script supplied to the guest, with its internal test
    # root and process seam. The production entrypoint never accepts these.
    namespace = {}
    exec(tcg.GUEST_RKE2_IDENTITY, namespace)
    root = tmp_path.resolve()
    def write(name, raw, mode=0o600):
        path = root / name.lstrip('/')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        path.chmod(mode)
        return path
    seed = closed_seed()
    write('/etc/rancher/rke2/config.yaml', tcg._canonical(namespace['LEGACY']))
    write('/var/lib/rancher/rke2/server/tls/server-ca.crt', b'fixture-ca')
    write('/usr/local/lib/systemd/system/rke2-server.service',
          (Path(__file__).parent / 'fixtures/rke2-server-v1.36.5.service').read_bytes(), 0o644)
    write('/usr/local/lib/systemd/system/rke2-server.env',
          (Path(__file__).parent / 'fixtures/rke2-server-v1.36.5.env').read_bytes(), 0o644)
    instance = write('/var/lib/cloud/instances/unit/user-data.txt', seed).parent
    (root / 'var/lib/cloud/instance').symlink_to(instance)
    state = {'calls': [], 'address': '10.0.2.15', 'props': {
        'FragmentPath': '/usr/local/lib/systemd/system/rke2-server.service',
        'DropInPaths': '', 'NeedDaemonReload': 'no', 'Environment': '', 'PassEnvironment': '',
        'EnvironmentFiles': [p + ' (ignore_errors=yes)' for p in (
            '/etc/default/rke2-server', '/etc/sysconfig/rke2-server', '/usr/local/lib/systemd/system/rke2-server.env')],
        'ExecStart': '{ path=/usr/local/bin/rke2 ; argv[]=/usr/local/bin/rke2 server ; ignore_errors=no ; pid=1 ; code=(null) ; status=0/0 }',
    }, 'restart_rc': 0}
    def run(argv, **kwargs):
        assert 0 < kwargs['timeout'] <= 5 and kwargs['capture_output'] and not kwargs['check']
        state['calls'].append(argv)
        code = 0
        if argv[0] == 'ip':
            raw = json.dumps([{'addr_info': [{'local': state['address'], 'prefixlen': 24}]}]).encode()
        elif argv[:2] == ['systemctl', 'show']:
            assert '--all' in argv
            fields = argv[-1].split('=', 1)[1].split(',')
            values = {k: state['props'].get(k, '1' if k.endswith('Monotonic') else 'activating') for k in fields}
            # Actual systemd255 serializes EnvironmentFiles array entries as
            # repeated property lines, not a space-joined scalar value.
            raw = ''.join(k + '=' + item + '\n' for k, v in values.items()
                          for item in (v if isinstance(v, list) else [v])).encode()
            if 'EnvironmentFiles' in fields and 'raw_effective' in state:
                raw = state['raw_effective']
        elif argv == ['systemctl', 'restart', '--no-block', 'rke2-server.service']:
            code, raw = state['restart_rc'], b''
        else:
            raise AssertionError(argv)
        return subprocess.CompletedProcess(argv, code, raw, b'')
    return namespace, root, write, state, run, tcg._sha(b'fixture-ca'), tcg._sha(seed)


def invoke(guest):
    ns, root, _, _, run, ca, seed = guest
    return ns['assure'](ca, seed, root=root, runner=run)


def restarts(guest):
    return [a for a in guest[3]['calls'] if a[:2] == ['systemctl', 'restart']]


def test_exact_legacy_migration_preserves_original_bytes_identity_and_timing(guest):
    ns, root, _, _, _, ca, seed = guest
    config = root / 'etc/rancher/rke2/config.yaml'
    before = config.read_bytes()
    result = invoke(guest)
    assert json.loads(config.read_bytes()) == ns['PINNED']
    assert (root / 'var/lib/devsecops/rke2-config-before.json').read_bytes() == before
    assert result['migration'] == 'restart-requested'
    assert result['ca_sha256'] == ca and result['seed_sha256'] == seed
    assert result['config_before_sha256'] == tcg._sha(before)
    assert result['config_after_sha256'] == tcg._sha(config.read_bytes())
    assert result['environment_files'] == [{
        'path': '/usr/local/lib/systemd/system/rke2-server.env', 'bytes': 11,
        'sha256': '2584cb4fcddb07782af3e50f2ee4200d0b84e64856f3db458bc0b1da53a70b79',
        'admission': 'bundled-default',
    }]
    assert 'HOME=' not in json.dumps(result) and '/root' not in json.dumps(result)
    assert set(result['timings']) == {'cloud-config.service', 'cloud-final.service', 'rke2-server.service'}
    assert result['timings']['cloud-final.service']['ActiveState'] == 'activating'
    assert len(restarts(guest)) == 1
    assert config.stat().st_mode & 0o777 == 0o600
    assert (root / 'var/lib/rancher/rke2/server/tls/server-ca.crt').read_bytes() == b'fixture-ca'
    # A second boot observes the prior completed migration and adds no restart.
    second = invoke(guest)
    assert second['migration'] == 'not-needed' and len(restarts(guest)) == 1


def test_already_pinned_config_never_restarts_or_rewrites_it(guest):
    ns, root, write, *_ = guest
    config = write('/etc/rancher/rke2/config.yaml', tcg._canonical(ns['PINNED']))
    before = config.stat()
    assert invoke(guest)['migration'] == 'not-needed'
    after = config.stat()
    assert (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns)
    assert not restarts(guest)
    assert not (root / 'var/lib/devsecops/rke2-node-ip.json').exists()


@pytest.mark.parametrize('raw', [None, b'', b'\n \t\n# optional environment\n  # comment\n'])
def test_empty_comment_only_or_absent_environment_files_remain_supported(guest, raw):
    _, root, write, *_ = guest
    paths = ['/etc/default/rke2-server', '/etc/sysconfig/rke2-server',
             '/usr/local/lib/systemd/system/rke2-server.env']
    for name in paths:
        path = root / name.lstrip('/')
        if raw is None:
            path.unlink(missing_ok=True)
        else:
            write(name, raw)
    result = invoke(guest)
    assert result['environment_files'] == ([] if raw is None else [
        {'path': name, 'bytes': len(raw), 'sha256': tcg._sha(raw), 'admission': 'empty-or-comments'}
        for name in paths
    ])


@pytest.mark.parametrize('name,raw', [
    ('/etc/default/rke2-server', b'HOME=/root\n'),
    ('/etc/sysconfig/rke2-server', b'HOME=/root\n'),
    ('/usr/local/lib/systemd/system/rke2-server.env', b'HOME=/other\n'),
    ('/usr/local/lib/systemd/system/rke2-server.env', b'HOME=/root\nHOME=/root\n'),
    ('/usr/local/lib/systemd/system/rke2-server.env', b'HOME=/root\nRKE2_CONFIG_FILE=/other\n'),
    ('/usr/local/lib/systemd/system/rke2-server.env', b'HOME=/root\nPATH=/other\n'),
    ('/usr/local/lib/systemd/system/rke2-server.env', b'HOME="/root"\n'),
    ('/usr/local/lib/systemd/system/rke2-server.env', b'export HOME=/root\n'),
    ('/usr/local/lib/systemd/system/rke2-server.env', b'HOME=/root'),
    ('/usr/local/lib/systemd/system/rke2-server.env', b'HOME=/root\r\n'),
    ('/usr/local/lib/systemd/system/rke2-server.env', b'# added comment\nHOME=/root\n'),
    ('/usr/local/lib/systemd/system/rke2-server.env', b'HOME=$(touch /should-not-execute)\n'),
])
def test_only_exact_bundled_environment_bytes_at_vendor_path_are_admitted(guest, name, raw):
    ns, root, write, *_ = guest
    write(name, raw)
    config = root / 'etc/rancher/rke2/config.yaml'
    before = config.read_bytes()
    with pytest.raises(ns['GuardError'], match='^environment-override$'):
        invoke(guest)
    assert config.read_bytes() == before and not restarts(guest)
    assert not any(a[0] in ('sh', 'bash', 'env') for a in guest[3]['calls'])
    assert not (root / 'var/lib/devsecops/rke2-node-ip.json').exists()


def test_systemd255_source_derived_array_shape_is_accepted_and_kept_private(guest):
    # Source-derived formatter fixture, not a claimed capture from a live guest.
    # v255 systemctl-show.c:1168-1177 emits one line per EnvironmentFiles tuple;
    # systemctl.c:574-594 includes empty selected properties even without --all.
    raw = (Path(__file__).parent / 'fixtures/systemd255-rke2-show.txt').read_bytes()
    guest[3]['raw_effective'] = raw
    assert invoke(guest)['migration'] == 'restart-requested'
    stored = guest[1] / 'var/lib/devsecops/rke2-node-ip-rke2-server.service-effective.log'
    assert stored.read_bytes() == raw and stored.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('change', ['scalar-repeat', 'unknown-key', 'missing-key', 'extra-env',
                                  'swapped-env', 'joined-env', 'duplicate-env', 'wrong-ignore'])
def test_only_exact_ordered_environment_array_is_permitted(guest, change):
    ns, root, _, state, *_ = guest
    raw = (Path(__file__).parent / 'fixtures/systemd255-rke2-show.txt').read_bytes()
    if change == 'scalar-repeat':
        raw += b'Environment=PRIVATE_UNIT_VALUE\n'
    elif change == 'unknown-key':
        raw += b'PRIVATE_KEY=PRIVATE_UNIT_VALUE\n'
    elif change == 'missing-key':
        raw = raw.replace(b'PassEnvironment=\n', b'')
    elif change == 'extra-env':
        raw += b'EnvironmentFiles=/other (ignore_errors=yes)\n'
    elif change == 'swapped-env':
        raw = raw.replace(b'/etc/default/rke2-server', b'/SWAP').replace(
            b'/etc/sysconfig/rke2-server', b'/etc/default/rke2-server').replace(b'/SWAP', b'/etc/sysconfig/rke2-server')
    elif change == 'joined-env':
        raw = raw.replace(b'\nEnvironmentFiles=', b' ', 2)
    elif change == 'duplicate-env':
        raw += b'EnvironmentFiles=/etc/default/rke2-server (ignore_errors=yes)\n'
    else:
        raw = raw.replace(b'ignore_errors=yes', b'ignore_errors=no', 1)
    state['raw_effective'] = raw
    before = (root / 'etc/rancher/rke2/config.yaml').read_bytes()
    with pytest.raises(ns['GuardError']) as observed:
        invoke(guest)
    assert (root / 'etc/rancher/rke2/config.yaml').read_bytes() == before and not restarts(guest)
    stored = root / 'var/lib/devsecops/rke2-node-ip-rke2-server.service-effective.log'
    assert stored.read_bytes() == raw and stored.stat().st_mode & 0o777 == 0o600
    if change in ('scalar-repeat', 'unknown-key', 'missing-key'):
        diagnostic = observed.value.diagnostic
        assert diagnostic['unit'] == 'rke2-server.service' and diagnostic['phase'] == 'effective'
        assert 'PRIVATE_' not in json.dumps(diagnostic)
        assert diagnostic['raw_sha256'] == tcg._sha(raw) and diagnostic['raw_bytes'] == len(raw)
        normalized = tcg._guest_property_failure(tcg._canonical({'error': str(observed.value), 'diagnostic': diagnostic}))
        assert normalized['raw_sha256'] == tcg._sha(raw)
        assert 'PRIVATE_' not in json.dumps(normalized)


def test_untrusted_property_failure_values_cannot_escape_private_diagnostics():
    value = {'error': 'unit-properties', 'diagnostic': {
        'unit': 'rke2-server.service', 'phase': 'effective', 'reason': 'duplicate-property',
        'property': 'Environment', 'raw_sha256': 'a' * 64, 'raw_bytes': 20,
        'private_output': '/PRIVATE_PATH', 'requested_keys': ['PRIVATE_KEY'], 'extra': 'PRIVATE_VALUE'}}
    assert 'PRIVATE_' not in json.dumps(tcg._guest_property_failure(tcg._canonical(value)))
    for key in ('unit', 'phase', 'reason', 'property', 'raw_sha256', 'raw_bytes'):
        changed = copy.deepcopy(value)
        changed['diagnostic'][key] = 'PRIVATE_VALUE'
        assert tcg._guest_property_failure(tcg._canonical(changed)) is None


@pytest.mark.parametrize('change', [
    'unknown-key', 'wrong-node', 'wrong-ip', 'duplicate-key', 'yaml', 'config-symlink',
    'config-directory', 'wrong-ca', 'wrong-address', 'unit-bytes', 'unit-symlink',
    'dropin', 'reload-needed', 'environment', 'pass-environment', 'extra-exec',
    'env-file', 'env-file-list', 'seed-mismatch', 'seed-symlink', 'vendor-data',
])
def test_unrecognized_guest_state_fails_before_config_change_or_restart(guest, change):
    ns, root, write, state, *_ = guest
    config = root / 'etc/rancher/rke2/config.yaml'
    if change in ('unknown-key', 'wrong-node', 'wrong-ip'):
        value = copy.deepcopy(ns['LEGACY'])
        value[{'unknown-key': 'data-dir', 'wrong-node': 'node-name', 'wrong-ip': 'node-ip'}[change]] = 'other'
        write('/etc/rancher/rke2/config.yaml', tcg._canonical(value))
    elif change == 'duplicate-key':
        write('/etc/rancher/rke2/config.yaml', b'{"node-ip":"a","node-ip":"b"}')
    elif change == 'yaml':
        write('/etc/rancher/rke2/config.yaml', b'node-ip: 10.0.2.15\n')
    elif change == 'config-symlink':
        config.rename(config.with_suffix('.save'))
        config.symlink_to(config.with_suffix('.save'))
    elif change == 'config-directory':
        write('/etc/rancher/rke2/config.yaml.d/extra.yaml', b'node-ip: other')
    elif change == 'wrong-ca':
        write('/var/lib/rancher/rke2/server/tls/server-ca.crt', b'other-ca')
    elif change == 'wrong-address':
        state['address'] = '10.0.2.16'
    elif change == 'unit-bytes':
        unit = root / 'usr/local/lib/systemd/system/rke2-server.service'
        write(str(unit.relative_to(root)), unit.read_bytes().replace(b'ExecStartPre=-/sbin/modprobe overlay', b'ExecStartPre=/bin/false'))
    elif change == 'unit-symlink':
        unit = root / 'usr/local/lib/systemd/system/rke2-server.service'
        unit.rename(unit.with_suffix('.save'))
        unit.symlink_to(unit.with_suffix('.save'))
    elif change == 'dropin':
        state['props']['DropInPaths'] = '/etc/systemd/system/rke2-server.service.d/override.conf'
    elif change == 'reload-needed':
        state['props']['NeedDaemonReload'] = 'yes'
    elif change == 'environment':
        state['props']['Environment'] = 'RKE2_CONFIG_FILE=/other'
    elif change == 'pass-environment':
        state['props']['PassEnvironment'] = 'RKE2_CONFIG_FILE'
    elif change == 'extra-exec':
        state['props']['ExecStart'] = state['props']['ExecStart'].replace('rke2 server ;', 'rke2 server --node-ip=other ;')
    elif change == 'env-file':
        write('/etc/default/rke2-server', b'RKE2_CONFIG_FILE=/other\n')
    elif change == 'env-file-list':
        state['props']['EnvironmentFiles'] += ['/other (ignore_errors=yes)']
    elif change == 'seed-mismatch':
        write('/var/lib/cloud/instances/unit/user-data.txt', b'other')
    elif change == 'seed-symlink':
        p = root / 'var/lib/cloud/instances/unit/user-data.txt'
        p.rename(p.with_suffix('.save'))
        p.symlink_to(p.with_suffix('.save'))
    else:
        write('/var/lib/cloud/instances/unit/vendor-data.txt', b'#cloud-config\nwrite_files: []')
    before = config.read_bytes()
    with pytest.raises((ns['GuardError'], ValueError)):
        invoke(guest)
    assert config.read_bytes() == before and not restarts(guest)


def test_failed_restart_preserves_incomplete_receipt_and_blocks_automatic_retry(guest):
    ns, root, _, state, *_ = guest
    state['restart_rc'] = 1
    with pytest.raises(ns['GuardError'], match='command-failed'):
        invoke(guest)
    receipt = json.loads((root / 'var/lib/devsecops/rke2-node-ip.json').read_bytes())
    assert receipt['migration'] == 'intent'
    assert json.loads((root / 'etc/rancher/rke2/config.yaml').read_bytes()) == ns['PINNED']
    state['restart_rc'] = 0
    with pytest.raises(ns['GuardError'], match='incomplete-or-changed'):
        invoke(guest)
    assert len(restarts(guest)) == 1


def test_command_deadline_is_absolute_and_never_waits_for_cloud_final(guest, monkeypatch):
    ns, _, _, _, run, *_ = guest
    clock = [0.0]
    monkeypatch.setattr(ns['time'], 'monotonic', lambda: clock[0])
    def late(argv, **kwargs):
        result = run(argv, **kwargs)
        clock[0] += 36
        return result
    with pytest.raises(ns['GuardError'], match='deadline'):
        ns['assure'](guest[5], guest[6], root=guest[1], runner=late)
    assert not restarts(guest)
    assert not any('status' in argv or 'wait' in argv for argv in guest[3]['calls'])


@pytest.mark.parametrize('expiry_point', ['before-backup', 'during-config-flush'])
def test_expired_write_budget_never_replaces_the_original_config(guest, monkeypatch, expiry_point):
    ns, root, *_ = guest
    clock = [0.0]
    monkeypatch.setattr(ns['time'], 'monotonic', lambda: clock[0])
    fsync = ns['os'].fsync
    def expires(fd):
        fsync(fd)
        directory = root / 'var/lib/devsecops'
        info = ns['os'].fstat(fd)
        temporary = root / ('etc/rancher/rke2/.config.yaml-node-ip-' + str(ns['os'].getpid()))
        before_backup = (stat.S_ISDIR(info.st_mode) and (directory / 'rke2-node-ip-observation.json').exists()
                         and not (directory / 'rke2-config-before.json').exists())
        during_config = (temporary.exists() and (info.st_dev, info.st_ino) ==
                         (temporary.stat().st_dev, temporary.stat().st_ino))
        if before_backup if expiry_point == 'before-backup' else during_config:
            clock[0] = 36
    monkeypatch.setattr(ns['os'], 'fsync', expires)
    config = root / 'etc/rancher/rke2/config.yaml'
    before = config.read_bytes()
    with pytest.raises(ns['GuardError'], match='deadline'):
        invoke(guest)
    assert config.read_bytes() == before and not restarts(guest)


@pytest.mark.parametrize('field,value', [
    ('write_files', []), ('bootcmd', [['sh', '-c', 'true']]), ('runcmd', [['touch', '/other']]),
    ('package_update', True), ('users', []), ('ssh_keys', {}),
])
def test_seed_guard_rejects_cloud_config_writers_and_unrecognized_recipes(field, value):
    raw = closed_seed()
    assert tcg._guest_seed_sha256(raw) == hashlib.sha256(raw).hexdigest()
    data = json.loads(raw[len(b'#cloud-config\n'):])
    data[field] = value
    with pytest.raises(tcg.TCGError, match='closed provisioner recipe'):
        tcg._guest_seed_sha256(b'#cloud-config\n' + tcg._canonical(data))


def test_guest_command_has_only_fixed_entrypoint_and_two_hashes():
    import shlex
    args = shlex.split(tcg._guest_identity_command('a' * 64, 'b' * 64))
    assert args[:4] == ['sudo', 'python3', '-I', '-c']
    assert args[-2:] == ['a' * 64, 'b' * 64]
    assert 'assure(sys.argv[1],sys.argv[2])' in args[4]
    assert 'root=' not in args[4].rsplit('try:', 1)[-1]
