"""Isolated-driver safety contract tests; these do not assert a guest was booted."""
import copy
import json
import os

import pytest

from scripts import lab_tcg_probe as probe


def runtime(tmp_path):
    return {'Id': 'a' * 64, 'Config': {'Labels': {probe.LABEL: 'unit-run'},
                                     'User': f'{os.getuid()}:{os.getgid()}'},
            'HostConfig': {'Privileged': False, 'ReadonlyRootfs': True, 'CapDrop': ['ALL'],
                           'SecurityOpt': ['no-new-privileges'], 'Memory': probe.CONTAINER_BYTES,
                           'MemorySwap': probe.CONTAINER_BYTES, 'NanoCpus': 4_000_000_000,
                           'PidsLimit': 256, 'NetworkMode': 'unit-net',
                           'PortBindings': {'2222/tcp': [{'HostIp': '127.0.0.1', 'HostPort': ''}]}},
            'Mounts': [{'Type': 'bind', 'Source': str(tmp_path), 'Destination': '/lab'}]}


@pytest.mark.parametrize('field,value', [
    ('Privileged', True), ('ReadonlyRootfs', False), ('CapDrop', []),
    ('CapAdd', ['NET_ADMIN']), ('SecurityOpt', []), ('Memory', 0),
    ('MemorySwap', -1), ('NanoCpus', 0), ('PidsLimit', 0),
    ('NetworkMode', 'host'), ('PidMode', 'host'), ('IpcMode', 'host'),
    ('Devices', [{'PathOnHost': '/dev/kvm'}]),
])
def test_unsafe_runtime_configuration_is_rejected(tmp_path, field, value):
    info = runtime(tmp_path)
    info['HostConfig'][field] = value
    with pytest.raises(probe.ProbeError, match='controls differ'):
        probe.validate_runtime(info, 'unit-run', tmp_path, 'unit-net')


def test_only_explicit_lab_mount_and_loopback_publish_are_allowed(tmp_path):
    good = runtime(tmp_path)
    probe.validate_runtime(good, 'unit-run', tmp_path, 'unit-net')
    for change in ('host-bind', 'docker-socket', 'extra-port', 'root-user'):
        info = copy.deepcopy(good)
        if change == 'host-bind':
            info['HostConfig']['PortBindings']['2222/tcp'][0]['HostIp'] = '0.0.0.0'
        elif change == 'docker-socket':
            info['Mounts'].append({'Type': 'bind', 'Source': '/var/run/docker.sock', 'Destination': '/var/run/docker.sock'})
        elif change == 'extra-port':
            info['HostConfig']['PortBindings']['6443/tcp'] = [{'HostIp': '127.0.0.1'}]
        else:
            info['Config']['User'] = '0:0'
        with pytest.raises(probe.ProbeError):
            probe.validate_runtime(info, 'unit-run', tmp_path, 'unit-net')


def test_cleanup_requires_exact_resource_identity_and_run_label(tmp_path):
    info = runtime(tmp_path)
    assert probe.require_owner(info, 'unit-run') == 'a' * 64
    with pytest.raises(probe.ProbeError):
        probe.require_owner(info, 'different-run')
    info['Id'] = 'name-prefix'
    with pytest.raises(probe.ProbeError):
        probe.require_owner(info, 'unit-run')
    network = {'Id': 'b' * 64, 'Labels': {probe.LABEL: 'unit-run'}}
    assert probe.require_owner(network, 'unit-run', network=True) == 'b' * 64


def test_internal_network_endpoint_is_bound_to_the_owned_container(tmp_path):
    info = runtime(tmp_path)
    network = {'Id': 'b' * 64, 'Name': 'unit-net', 'Labels': {probe.LABEL: 'unit-run'},
               'Internal': True, 'IPAM': {'Config': [{'Subnet': '172.18.0.0/16'}]}}
    info['NetworkSettings'] = {'Ports': {'2222/tcp': None},
                              'Networks': {'unit-net': {'NetworkID': 'b' * 64, 'IPAddress': '172.18.0.2'}}}
    assert probe.ssh_endpoint(info, network, 'unit-run') == ('172.18.0.2', 2222, 'owned-internal-bridge-via-devbox')
    for bad in ('8.8.8.8', '127.0.0.1', '10.0.0.2'):
        altered = copy.deepcopy(info)
        altered['NetworkSettings']['Networks']['unit-net']['IPAddress'] = bad
        with pytest.raises(probe.ProbeError):
            probe.ssh_endpoint(altered, network, 'unit-run')
    altered = copy.deepcopy(info)
    altered['NetworkSettings']['Networks']['unit-net']['NetworkID'] = 'c' * 64
    with pytest.raises(probe.ProbeError):
        probe.ssh_endpoint(altered, network, 'unit-run')
    info['NetworkSettings']['Ports']['2222/tcp'] = [{'HostIp': '127.0.0.1', 'HostPort': '32022'}]
    assert probe.ssh_endpoint(info, network, 'unit-run') == ('127.0.0.1', 32022, 'loopback-publish')
    info['NetworkSettings']['Ports']['2222/tcp'][0]['HostIp'] = '0.0.0.0'
    with pytest.raises(probe.ProbeError):
        probe.ssh_endpoint(info, network, 'unit-run')


@pytest.mark.parametrize('code,error', [(124, b''), (1, b''),
    (255, b'Connection timed out during banner exchange'),
    (255, b'Connection reset by peer'), (255, b'Connection refused')])
def test_ssh_startup_timeouts_and_not_ready_marker_can_retry(code, error):
    assert probe.classify_ssh_probe(code, error) == 'pending'


@pytest.mark.parametrize('error', [b'WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!',
    b'Host key verification failed.', b'No ED25519 host key is known for [172.18.0.2]:2222',
    b'Permission denied (publickey).'])
def test_ssh_identity_failures_are_fatal_even_if_timeout_or_transient_also_seen(error):
    for code in (124, 255):
        with pytest.raises(probe.ProbeError, match='identity verification failed'):
            probe.classify_ssh_probe(code, b'Connection reset\n' + error)


def test_unrecognized_ssh_failure_cannot_be_treated_as_startup():
    with pytest.raises(probe.ProbeError):
        probe.classify_ssh_probe(255, b'Unrecognized SSH failure')
    assert probe.classify_ssh_probe(0, b'') == 'ready'


def test_private_writes_refuse_replacement_and_symlinks(tmp_path):
    path = tmp_path / 'private'
    probe.write_private(path, 'unit-private-sentinel')
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        probe.write_private(path, 'replacement')
    linked = tmp_path / 'linked'
    linked.symlink_to(path)
    with pytest.raises(FileExistsError):
        probe.write_private(linked, 'replacement')
    assert path.read_text() == 'unit-private-sentinel'


def test_private_directory_refuses_shared_or_symlink_path(tmp_path):
    path = tmp_path / 'private'
    probe.private_dir(path)
    assert path.stat().st_mode & 0o777 == 0o700
    path.chmod(0o755)
    with pytest.raises(probe.ProbeError):
        probe.private_dir(path)
    linked = tmp_path / 'linked'
    linked.symlink_to(path)
    with pytest.raises(probe.ProbeError):
        probe.private_dir(linked)


def test_seed_requires_pinned_host_key_and_disables_password_login():
    # Deliberately not key material: test fixture strings only.
    raw = probe.cloud_config('unit-public', 'unit-private', 'unit-host-public')
    value = json.loads(raw.split('\n', 1)[1])
    assert value['ssh_pwauth'] is False and value['disable_root'] is True
    assert value['ssh_genkeytypes'] == []
    assert value['ssh_keys']['ed25519_private'] == 'unit-private'
    assert value['users'][0]['lock_passwd'] is True
    assert value['package_update'] is False and value['package_upgrade'] is False


def test_rootless_apt_does_not_use_host_status_or_hook_directories(tmp_path):
    root = tmp_path / 'private-apt'
    value = probe.apt_config(root)
    assert 'Dir::Etc::main "/dev/null";' in value
    for field, suffix in [('Dir::Etc::parts', 'empty'), ('Dir::Etc::sourceparts', 'empty'),
                          ('Dir::State::status', 'status'), ('Dir::State::lists', 'lists'),
                          ('Dir::Cache::archives', 'archives')]:
        assert field + ' ' + json.dumps(str(root / suffix)) + ';' in value
    assert 'Acquire::AllowInsecureRepositories "false";' in value
    assert 'APT::Get::AllowUnauthenticated "false";' in value


def test_cli_refuses_shell_like_alias_and_existing_receipt(tmp_path):
    with pytest.raises(SystemExit):
        probe.main(['--host', 'host; touch forbidden', '--receipt', str(tmp_path / 'receipt.json')])
    existing = tmp_path / 'receipt.json'
    existing.write_text('{}')
    with pytest.raises(SystemExit):
        probe.main(['--host', 'owned-alias', '--receipt', str(existing)])


def reviewed_rke2_pins():
    return json.loads((probe.ROOT / 'demo/software-factory/provision/tcg/rke2-release.json').read_text())


def test_rke2_pins_allow_only_reviewed_official_artifacts():
    good = reviewed_rke2_pins()
    assert probe.validate_rke2_pins(good) is good
    for mutation in ('wrong-version', 'missing', 'duplicate', 'arbitrary-url', 'bad-hash', 'unbounded'):
        pins = copy.deepcopy(good)
        if mutation == 'wrong-version':
            pins['rke2_version'] = 'latest'
        elif mutation == 'missing':
            pins['assets'].pop()
        elif mutation == 'duplicate':
            pins['assets'][-1] = pins['assets'][0]
        elif mutation == 'arbitrary-url':
            pins['assets'][0]['url'] = 'https://unreviewed.example/image.tar'
        elif mutation == 'bad-hash':
            pins['assets'][0]['sha256'] = 'not-a-digest'
        else:
            pins['assets'][0]['bytes'] = 1 << 40
        with pytest.raises(probe.ProbeError):
            probe.validate_rke2_pins(pins)


def test_release_checksums_bind_each_exact_artifact_and_reject_duplicate_entries():
    pins = reviewed_rke2_pins()
    lines = [x['sha256'] + '  ' + x['name'] for x in pins['assets']
             if x['name'] not in ('install.sh', 'sha256sum-amd64.txt')]
    probe.verify_release_checksums(pins, '\n'.join(lines))
    for bad in (lines[:-1], lines + [lines[0]], ['a' * 64 + line[64:] for line in lines]):
        with pytest.raises(probe.ProbeError):
            probe.verify_release_checksums(pins, '\n'.join(bad))


def test_larger_screen_requires_its_own_exact_resource_caps(tmp_path):
    info = runtime(tmp_path)
    with pytest.raises(probe.ProbeError):
        probe.validate_runtime(info, 'unit-run', tmp_path, 'unit-net', container_bytes=34 * 1024**3, cpus=8)
    info['HostConfig'].update(Memory=34 * 1024**3, MemorySwap=34 * 1024**3, NanoCpus=8_000_000_000)
    probe.validate_runtime(info, 'unit-run', tmp_path, 'unit-net', container_bytes=34 * 1024**3, cpus=8)
    with pytest.raises(probe.ProbeError):
        probe.validate_runtime(info, 'unit-run', tmp_path, 'unit-net')


def test_unit_observation_requires_exact_server_kubelet_and_ready_node():
    # Simulated parser inputs only. This unit test is not a live readiness receipt.
    value = {'readyz': 'ok', 'server_version': 'v1.36.5+rke2r1',
             'nodes': [{'name': 'unit-node', 'ready': True, 'kubelet_version': 'v1.36.5+rke2r1'}]}
    assert probe.rke2_observation(value, 'v1.36.5+rke2r1')
    for mutation in ('readyz', 'wrong-server', 'no-node', 'two-nodes', 'not-ready', 'wrong-kubelet'):
        bad = copy.deepcopy(value)
        if mutation == 'readyz':
            bad['readyz'] = 'not-ready'
        elif mutation == 'wrong-server':
            bad['server_version'] = 'v1.35.0'
        elif mutation == 'no-node':
            bad['nodes'] = []
        elif mutation == 'two-nodes':
            bad['nodes'] *= 2
        elif mutation == 'not-ready':
            bad['nodes'][0]['ready'] = 'True'
        else:
            bad['nodes'][0]['kubelet_version'] = 'v1.35.0'
        assert not probe.rke2_observation(bad, 'v1.36.5+rke2r1')


def test_acquisition_redirect_refuses_other_hosts_http_and_credentials():
    from urllib.request import HTTPRedirectHandler, Request
    opener = probe.download_opener(('github.com', 'release-assets.githubusercontent.com'))
    redirect = next(x for x in opener.handlers if isinstance(x, HTTPRedirectHandler))
    request = Request('https://github.com/rancher/rke2/releases/download/tag/archive')
    for url in ('http://release-assets.githubusercontent.com/archive', 'https://metadata.internal/archive',
                'https://user:password@github.com/archive', 'https://github.com:8443/archive'):
        with pytest.raises(probe.ProbeError):
            redirect.redirect_request(request, None, 302, 'Found', {}, url)
    allowed = redirect.redirect_request(request, None, 302, 'Found', {}, 'https://release-assets.githubusercontent.com/archive')
    assert allowed.full_url == 'https://release-assets.githubusercontent.com/archive'


def test_simulated_guest_hash_failure_never_reaches_installer(tmp_path):
    from subprocess import CompletedProcess
    calls = []

    def command(argv, **kwargs):
        calls.append(argv)
        return CompletedProcess(argv, 0, b'not-verified', b'')

    with pytest.raises(probe.ProbeError, match='artifact verification'):
        probe.screen_rke2(command, ['ssh', '-F', '/dev/null', '-p', '2222', 'demo@unit.invalid'],
                          tmp_path, {}, reviewed_rke2_pins(), lambda stage: None)
    assert not any('INSTALL_RKE2_METHOD' in str(call) for call in calls)


def test_simulated_late_third_ready_observation_cannot_extend_fixed_gate(tmp_path, monkeypatch):
    from subprocess import CompletedProcess
    from types import SimpleNamespace
    # Explicitly simulated command replies and clock. No VM or API is contacted.
    clock = [0.0]
    monkeypatch.setattr(probe, 'time', SimpleNamespace(
        monotonic=lambda: clock[0], sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds)))
    calls = []
    report = {}

    def command(argv, **kwargs):
        calls.append(argv)
        if 'assets=json.loads' in argv[-1]:
            output = b'verified'
        elif 'serverVersion' in argv[-1]:
            clock[0] += 450
            output = json.dumps({'readyz': 'ok', 'server_version': 'v1.36.5+rke2r1',
                                  'nodes': [{'ready': True, 'kubelet_version': 'v1.36.5+rke2r1'}]}).encode()
        else:
            output = b''
        return CompletedProcess(argv, 0, output, b'')

    with pytest.raises(probe.ProbeError, match='fixed twenty-minute'):
        probe.screen_rke2(command, ['ssh', '-F', '/dev/null', '-p', '2222', 'demo@unit.invalid'],
                          tmp_path, report, reviewed_rke2_pins(), lambda stage: None)
    assert report['rke2']['status'] == 'incomplete'
    assert report['rke2']['configuration']['node-ip'] == '10.0.2.15'
    assert any('10.0.2.15' in call[-1] and 'config.yaml' in call[-1] for call in calls)
    assert report['rke2']['elapsed_seconds'] > 1200
    assert report['rke2']['consecutive_ready_observations'] == 3
    assert 'credentials' not in report['rke2']
    assert any('stop --no-block rke2-server' in call[-1] for call in calls)
