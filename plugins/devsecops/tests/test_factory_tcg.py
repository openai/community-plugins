"""Controlled unit fixtures only: these tests do not claim a live guest pass."""
import copy
import base64
import hashlib
import json
import os
import shlex
import signal
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, replace
from contextlib import contextmanager
from pathlib import Path

import pytest

from devsecops.factory import tcg


def issued_certificate(**changes):
    """Controlled wire-format envelope; no fixture is a valid signed credential."""
    values = {'nonce': b'nonce', 'key': b'x' * 32, 'serial': 1, 'type': 1, 'key_id': b'operator',
              'principals': b'\x00\x00\x00\x08operator', 'valid_after': int(time.time()) - 30,
              'valid_before': int(time.time()) + 3600, 'critical': b'', 'extensions': b'',
              'reserved': b'', 'issuer': b'unit-ca', 'signature': b'unit-signature'}
    values.update(changes)
    def string(value):
        return struct.pack('>I', len(value)) + value
    algorithm = b'ssh-ed25519-cert-v01@openssh.com'
    blob = string(algorithm) + string(values['nonce']) + string(values['key'])
    blob += struct.pack('>QI', values['serial'], values['type'])
    blob += string(values['key_id']) + string(values['principals'])
    blob += struct.pack('>QQ', values['valid_after'], values['valid_before'])
    blob += b''.join(string(values[name]) for name in ('critical', 'extensions', 'reserved', 'issuer', 'signature'))
    return algorithm + b' ' + base64.b64encode(blob) + b' unit-fixture\n'


@pytest.fixture
def spec():
    return tcg.TCGSpec('unit-run', 'unit-environment', {k: '1' * 64 for k in tcg.IDENTITIES},
                       'unit-host', '/home/operator/.local/share/devsecops/labs/unit-lab',
                       'sha256:' + '2' * 64, '3' * 64, 'unit-provision', '4' * 64,
                       {**{name: {'path': '/unit-fixture/' + name, 'sha256': '5' * 64}
                           for name in ('config', 'known_hosts', 'certificate', 'identity', 'proxy')},
                        'identity_agent': '/unit-fixture/agent', 'host_key_sha256': '6' * 64,
                        'verification_basis': 'authenticated-control-plane'})


@pytest.fixture
def driver(tmp_path, spec):
    return tcg.RetainedTCGDriver(tmp_path.resolve() / 'private', spec)


def resources(spec):
    labels = {tcg.LABEL: spec.run_id, tcg.BINDING_LABEL: spec.digest, tcg.OPERATION_LABEL: 'unit-operation'}
    network = {'Id': 'a' * 64, 'Name': 'unit-net', 'Labels': labels, 'Internal': True,
               'IPAM': {'Config': [{'Subnet': '172.18.0.0/16', 'Gateway': '172.18.0.1'}]}}
    container = {'Id': 'b' * 64, 'Image': spec.tool_image, 'Config': {'Labels': labels,
                 'User': f'{os.getuid()}:{os.getgid()}', 'Cmd': tcg._qemu()},
                 'State': {'Running': True}, 'Mounts': [{'Type': 'bind', 'Source': spec.remote_lab, 'Destination': '/lab'}],
                 'HostConfig': {'Privileged': False, 'ReadonlyRootfs': True, 'CapDrop': ['ALL'],
                 'SecurityOpt': ['no-new-privileges'], 'Memory': 34 * 1024**3, 'MemorySwap': 34 * 1024**3,
                 'NanoCpus': 8_000_000_000, 'PidsLimit': 256, 'NetworkMode': 'unit-net',
                 'PortBindings': {'2222/tcp': [{'HostIp': '127.0.0.1', 'HostPort': ''}]}},
                 'NetworkSettings': {'Ports': {'2222/tcp': None}, 'Networks': {'unit-net': {
                     'IPAddress': '172.18.0.2', 'NetworkID': 'a' * 64}}}}
    return container, network


@pytest.mark.parametrize('field', sorted(tcg.IDENTITIES))
def test_each_input_identity_prevents_resume(driver, spec, field):
    identities = {**spec.identities, field: 'f' * 64}
    with pytest.raises(tcg.TCGError, match='Changed run'):
        tcg.RetainedTCGDriver(driver.directory, replace(spec, identities=identities))


@pytest.mark.parametrize('field,value', [('run_id', 'other-run'), ('environment_id', 'other-env'),
                                       ('original_ca_sha256', 'f' * 64), ('tool_image', 'sha256:' + 'f' * 64)])
def test_other_binding_changes_prevent_resume(driver, spec, field, value):
    with pytest.raises(tcg.TCGError, match='Changed run'):
        tcg.RetainedTCGDriver(driver.directory, replace(spec, **{field: value}))


def test_private_file_refuses_fifo_symlink_and_duplicate_json(tmp_path):
    root = tmp_path.resolve()
    fifo = root / 'fifo'
    os.mkfifo(fifo, 0o600)
    started = time.monotonic()
    with pytest.raises(tcg.TCGError):
        tcg._read(fifo)
    assert time.monotonic() - started < 1
    link = root / 'link'
    link.symlink_to(fifo)
    with pytest.raises(tcg.TCGError):
        tcg._read(link)
    with pytest.raises(tcg.TCGError, match='Duplicate'):
        tcg._json(b'{"a":1,"a":2}')


@pytest.mark.parametrize('field,value', [('Privileged', True), ('ReadonlyRootfs', False),
    ('CapDrop', []), ('CapAdd', ['NET_ADMIN']), ('Memory', 0), ('MemorySwap', -1),
    ('NanoCpus', 0), ('PidsLimit', 0), ('NetworkMode', 'host'), ('PidMode', 'host'),
    ('IpcMode', 'host'), ('Devices', [{'PathOnHost': '/dev/kvm'}])])
def test_runtime_rejects_unsafe_isolation(spec, field, value):
    container, network = resources(spec)
    container['HostConfig'][field] = value
    with pytest.raises(tcg.TCGError, match='isolation'):
        tcg._runtime(container, network, spec)


def test_runtime_checks_ownership_network_and_exact_qemu_command(spec):
    container, network = resources(spec)
    assert tcg._runtime(container, network, spec) == '172.18.0.2'
    for variant in ('extra-mount', 'foreign-label', 'public-address', 'other-network', 'modified-command'):
        changed = copy.deepcopy(container)
        if variant == 'extra-mount':
            changed['Mounts'].append({'Source': '/var/run/docker.sock'})
        elif variant == 'foreign-label':
            changed['Config']['Labels'][tcg.BINDING_LABEL] = 'f' * 64
        elif variant == 'public-address':
            changed['NetworkSettings']['Networks']['unit-net']['IPAddress'] = '8.8.8.8'
        elif variant == 'other-network':
            changed['NetworkSettings']['Networks']['unit-net']['NetworkID'] = 'f' * 64
        else:
            changed['Config']['Cmd'].append('-enable-kvm')
        with pytest.raises(tcg.TCGError):
            tcg._runtime(changed, network, spec)


@pytest.mark.parametrize('code', [124, 255])
def test_host_identity_failure_never_becomes_polling(code):
    with pytest.raises(tcg.TCGError, match='identity'):
        tcg._ssh_pending(subprocess.CompletedProcess([], code, b'', b'Host key verification failed; connection timed out'))
    assert tcg._ssh_pending(subprocess.CompletedProcess([], code, b'', b'Connection timed out'))


def test_guest_ssh_uses_pinned_identity_and_exact_container_loopback(tmp_path):
    root = tmp_path.resolve()
    lab = tcg._private(root / 'lab', create=True)
    tcg._private(lab / 'ssh', create=True)
    tcg._write(lab / 'ssh/host.pub', b'ssh-ed25519 unit-public-key\n')
    directory = tcg._private(root / 'retained', create=True)
    argv = tcg._guest_ssh(lab, '172.18.0.2', directory, 'b' * 64, write_hosts=True)
    proxy = next(value for value in argv if value.startswith('ProxyCommand='))
    assert ('docker exec -i ' + 'b' * 64 + ' python3 -I -c ') in proxy
    assert shlex.split(proxy.removeprefix('ProxyCommand='))[-1] == tcg.GUEST_RELAY
    assert 'StrictHostKeyChecking=yes' in argv and 'IdentityAgent=none' in argv
    # Real OpenSSH option parsing; no network connection or authentication.
    parsed = subprocess.run([*argv[:1], '-G', *argv[1:]], capture_output=True, timeout=5, check=False)
    assert parsed.returncode == 0, parsed.stderr
    with pytest.raises(tcg.TCGError, match='exact owned container'):
        tcg._guest_ssh(lab, '172.18.0.2', directory, 'bad; command')


@pytest.mark.parametrize('gateway', ['8.8.8.8', '127.0.0.1', '172.19.0.1', '172.18.0.2'])
def test_guest_reply_route_rejects_non_owned_gateway(spec, gateway):
    _, network = resources(spec)
    network['IPAM']['Config'][0]['Gateway'] = gateway
    with pytest.raises(tcg.TCGError, match='exact guest reply route'):
        tcg._reply_route_target(network, '172.18.0.2')


def test_guest_reply_route_requires_one_exact_gateway(spec):
    _, network = resources(spec)
    assert tcg._reply_route_target(network, '172.18.0.2') == '172.18.0.1'
    network['IPAM']['Config'].append(network['IPAM']['Config'][0])
    with pytest.raises(tcg.TCGError, match='ambiguous'):
        tcg._reply_route_target(network, '172.18.0.2')


def remote_observation(state='running_unready'):
    return {'state': state, 'address': '172.18.0.2', 'observed_mutation_effect':
            'not_applied' if state == 'absent' else 'applied', 'pending': None}


def tunnel_observation(driver):
    return {'pid': 12345, 'port': 12345, 'process_sha256': 'e'*64,
            'control': str(driver.directory / 'ctl-fixture'), 'address': '172.18.0.2',
            'operation_id': 'unit-up'}


@pytest.fixture
def api_driver(tmp_path, spec):
    ca = b'controlled-api-ca'
    driver = tcg.RetainedTCGDriver(tmp_path.resolve() / 'api-private', replace(spec, original_ca_sha256=tcg._sha(ca)))
    for name, contents in [('ca.crt', ca), ('client.crt', b'controlled-public-cert'), ('client.key', b'controlled-key')]:
        tcg._write(driver.directory / name, contents)
    return driver


def tls_receipt(**changes):
    failure = {'category': 'handshake-eof', 'exception': 'SSLEOFError', 'phase': 'tls-handshake',
               'reason': 'UNEXPECTED_EOF_WHILE_READING', 'verify_code': None, 'peer_authenticated': False}
    failure.update(changes)
    return {'outcome': 'tls', 'status': None, 'body_bytes': 0, 'tls_failure': failure}


def failing_api_client(monkeypatch, receipt, code='tls'):
    from devsecops.adapters import http

    class Client:
        receipts = [receipt]

        def __init__(self, *args, **kwargs):
            pass

        def get_bytes(self, *args, **kwargs):
            raise http.APIError(code)

    monkeypatch.setattr(http, 'HTTPSReadClient', Client)


@pytest.mark.parametrize('code', ['tls', 'deadline'])
def test_only_observed_handshake_eof_is_retryable_without_readiness(api_driver, monkeypatch, code):
    receipt = tls_receipt()
    receipt['outcome'] = code
    failing_api_client(monkeypatch, receipt, code)
    observation = api_driver._api(12345, time.monotonic() + 10)
    assert observation['ready'] is False and observation['retryable'] is True
    assert observation['transport'] == [receipt] and observation['code'] == code
    assert not (api_driver.directory / 'cluster-fingerprint.json').exists()


@pytest.mark.parametrize('changes,code', [
    ({'category': 'certificate-verification', 'exception': 'SSLCertVerificationError',
      'reason': 'CERTIFICATE_VERIFY_FAILED', 'verify_code': 62}, 'tls'),
    ({'category': 'certificate-verification', 'exception': 'SSLCertVerificationError'}, 'deadline'),
    ({'category': 'tls-protocol', 'exception': 'SSLError', 'reason': 'TLSV13_ALERT_CERTIFICATE_REQUIRED'}, 'tls'),
    ({'phase': 'response-body'}, 'tls'), ({'peer_authenticated': True}, 'tls'),
    ({'exception': 'SSLError'}, 'tls'), ({'reason': 'OTHER'}, 'tls'),
    ({'reason': 'OTHER'}, 'deadline'), ({'phase': 'response-body'}, 'deadline'),
    ({'peer_authenticated': True}, 'deadline'),
])
def test_other_tls_failures_remain_fatal_with_diagnostics(api_driver, monkeypatch, changes, code):
    receipt = tls_receipt(**changes)
    receipt['outcome'] = code
    failing_api_client(monkeypatch, receipt, code)
    with pytest.raises(tcg.TCGAPIError) as failure:
        api_driver._api(12345, time.monotonic() + 10)
    assert failure.value.observation['retryable'] is False
    assert failure.value.observation['transport'] == [receipt]


def test_missing_tls_diagnostics_cannot_enable_a_startup_retry(api_driver, monkeypatch):
    failing_api_client(monkeypatch, {'outcome': 'tls'})
    with pytest.raises(tcg.TCGAPIError):
        api_driver._api(12345, time.monotonic() + 10)


def test_fatal_api_receipts_survive_up_and_reconciliation(api_driver, monkeypatch):
    driver = api_driver
    receipt = tls_receipt(category='certificate-verification', exception='SSLCertVerificationError',
                          reason='CERTIFICATE_VERIFY_FAILED', verify_code=62)
    failing_api_client(monkeypatch, receipt)
    monkeypatch.setattr(driver, '_remote', lambda *args: remote_observation())
    monkeypatch.setattr(driver, '_credentials', lambda *args: None)
    monkeypatch.setattr(driver, '_tunnel', lambda *args: tunnel_observation(driver))
    result = driver.up('unit-up', deadline=time.monotonic() + 10)
    recorded = tcg._json(tcg._read(result.receipt))
    assert recorded['api_observations'][0]['transport'] == [receipt]
    assert recorded['api_observations'][0]['retryable'] is False
    assert result.state != 'running_ready' and result.mutation_effect == 'unknown'
    tcg._write(driver.directory / 'tunnel.json', tcg._canonical({
        'pid': 12345, 'process_sha256': 'owned-process', 'address': '172.18.0.2', 'port': 12345}))
    monkeypatch.setattr(driver, '_process_identity', lambda *args: 'owned-process')
    reconciled = driver.reconcile('unit-up', deadline=time.monotonic() + 10)
    assert reconciled.mutation_effect == 'applied'
    assert tcg._json(tcg._read(reconciled.receipt))['api_observation']['transport'] == [receipt]


def test_startup_retry_does_not_extend_the_original_twenty_minute_gate(driver, monkeypatch):
    clock = [100.0]
    deadlines = []
    monkeypatch.setattr(tcg.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(tcg.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(driver, '_remote', lambda *args: remote_observation())
    monkeypatch.setattr(driver, '_credentials', lambda *args: None)
    monkeypatch.setattr(driver, '_tunnel', lambda *args: tunnel_observation(driver))

    def not_ready(port, deadline):
        deadlines.append(deadline)
        clock[0] = min(deadline, clock[0] + 400)
        return {'ready': False, 'retryable': True, 'code': 'tls', 'transport': [tls_receipt()]}

    monkeypatch.setattr(driver, '_api', not_ready)
    observation = driver.up('unit-up', deadline=9999)
    record = tcg._json(tcg._read(observation.receipt))
    assert deadlines == [1300, 1300, 1300] and clock[0] == 1300
    assert len(record['api_observations']) == 3 and 'readiness gate expired' in record['error']
    assert observation.state != 'running_ready'


def test_fatal_tls_failure_keeps_earlier_startup_observations(driver, monkeypatch):
    monkeypatch.setattr(driver, '_remote', lambda *args: remote_observation())
    monkeypatch.setattr(driver, '_credentials', lambda *args: None)
    monkeypatch.setattr(driver, '_tunnel', lambda *args: tunnel_observation(driver))
    monkeypatch.setattr(tcg.time, 'sleep', lambda seconds: None)
    attempts = []
    startup = {'ready': False, 'retryable': True, 'transport': [tls_receipt()]}
    fatal = {'ready': False, 'retryable': False, 'transport': [tls_receipt(category='certificate-verification')]}

    def api(*args):
        attempts.append(None)
        if len(attempts) == 1:
            return startup
        raise tcg.TCGAPIError(fatal)

    monkeypatch.setattr(driver, '_api', api)
    result = driver.up('unit-up', deadline=time.monotonic() + 10)
    assert tcg._json(tcg._read(result.receipt))['api_observations'] == [startup, fatal]


def test_remote_success_cannot_claim_ready_without_real_api_path(driver, monkeypatch):
    monkeypatch.setattr(driver, '_remote', lambda *args: remote_observation())
    monkeypatch.setattr(driver, '_credentials', lambda *args: None)
    monkeypatch.setattr(driver, '_tunnel', lambda *args: tunnel_observation(driver))
    def certificate_failure(*args):
        raise ssl.SSLCertVerificationError('unit-fixture invalid certificate')
    monkeypatch.setattr(driver, '_api', certificate_failure)
    result = driver.up('unit-up', deadline=time.monotonic() + 10)
    receipt = json.loads(result.receipt.read_text())
    assert result.state != 'running_ready'
    assert result.mutation_effect == 'unknown'
    assert 'TLS verification failed' in receipt['error']
    assert (driver.directory / 'active.json').exists()
    with pytest.raises(tcg.TCGError, match='reconciliation'):
        driver.up('unit-blind-retry', deadline=time.monotonic() + 10)


def test_failed_mutation_requires_matching_read_only_reconciliation_before_cleanup(driver, monkeypatch):
    def failed(*args):
        raise tcg.TCGError('Unit fixture lost reply')
    monkeypatch.setattr(driver, '_remote', failed)
    result = driver.up('unit-up', deadline=time.monotonic() + 10)
    assert result.mutation_effect == 'unknown'
    with pytest.raises(tcg.TCGError, match='identify the unresolved'):
        driver.reconcile('wrong-operation', deadline=time.monotonic() + 10)
    monkeypatch.setattr(driver, '_remote', lambda *args: remote_observation('absent'))
    result = driver.reconcile('unit-up', deadline=time.monotonic() + 10)
    assert result.mutation_effect == 'not_applied'
    monkeypatch.setattr(driver, '_close_tunnel', lambda *args: {'owned_tunnel_absent': True})
    result = driver.down('unit-down', deadline=time.monotonic() + 10)
    assert result.state == 'absent'
    assert not (driver.directory / 'active.json').exists()


def test_three_api_observations_and_fingerprint_continuity_are_required(driver, monkeypatch):
    monkeypatch.setattr(driver, '_remote', lambda *args: remote_observation())
    monkeypatch.setattr(driver, '_credentials', lambda *args: None)
    monkeypatch.setattr(driver, '_tunnel', lambda *args: tunnel_observation(driver))
    fingerprint = {'namespaces': {'default': 'unit-default-uid', 'kube-system': 'unit-system-uid'},
                   'nodes': {'unit-node': 'unit-node-uid'}, 'ca_sha256': driver.spec.original_ca_sha256}
    monkeypatch.setattr(driver, '_api', lambda *args: {'ready': True, 'fingerprint': fingerprint})
    monkeypatch.setattr(tcg.time, 'sleep', lambda seconds: None)
    result = driver.up('unit-up', deadline=time.monotonic() + 10)
    assert result.state == 'running_ready'
    receipt = json.loads(result.receipt.read_text())
    assert len(receipt['api_observations']) == 3
    assert receipt['canonical_platform_qualified'] is False
    assert receipt['tunnel_identity'] == tunnel_observation(driver)
    assert tcg._sha(result.receipt.read_bytes()) == result.receipt_sha256
    changed = {**fingerprint, 'nodes': {'unit-node': 'new-uid'}}
    monkeypatch.setattr(driver, '_api', lambda *args: {'ready': True, 'fingerprint': changed})
    result = driver.up('unit-second-up', deadline=time.monotonic() + 10)
    assert result.state != 'running_ready'
    assert 'recreation is not durability' in json.loads(result.receipt.read_text())['error']


@pytest.mark.parametrize('failure', ['tls', 'fingerprint'])
def test_api_identity_failure_cannot_erase_reconciled_owned_cleanup_evidence(driver, monkeypatch, failure):
    tcg._write(driver.directory / 'active.json', tcg._canonical({'operation_id': 'unit-up', 'action': 'up'}))
    tcg._write(driver.directory / 'tunnel.json', tcg._canonical({
        'pid': 12345, 'process_sha256': 'owned-process', 'address': '172.18.0.2', 'port': 12345}))
    tcg._write(driver.directory / 'cluster-fingerprint.json', tcg._canonical({'nodes': {'old': 'uid'}}))
    monkeypatch.setattr(driver, '_remote', lambda *args: remote_observation())
    monkeypatch.setattr(driver, '_process_identity', lambda *args: 'owned-process')

    def api(*args):
        if failure == 'tls':
            raise tcg.TCGError('Pinned API transport or identity verification failed')
        return {'ready': True, 'fingerprint': {'nodes': {'changed': 'uid'}}}

    monkeypatch.setattr(driver, '_api', api)
    observation = driver.reconcile('unit-up', deadline=time.monotonic() + 10)
    receipt = json.loads(observation.receipt.read_text())
    assert observation.state == 'running_unready'
    assert observation.mutation_effect == 'applied'
    assert receipt['error']
    assert receipt['canonical_platform_qualified'] is False
    assert (driver.directory / 'reconciliation.json').is_file()
    monkeypatch.setattr(driver, '_remote', lambda *args: remote_observation('absent'))
    monkeypatch.setattr(driver, '_close_tunnel', lambda *args: {'owned_tunnel_absent': True})
    cleanup = driver.down('unit-down', deadline=time.monotonic() + 10)
    assert cleanup.state == 'absent'
    assert not (driver.directory / 'active.json').exists()


def test_tunnel_cleanup_rejects_reused_pid(driver, monkeypatch):
    tcg._write(driver.directory / 'tunnel.json', tcg._canonical({'pid': 12345, 'process_sha256': 'old',
               'control': str(driver.directory / 'control')}))
    monkeypatch.setattr(driver, '_process_identity', lambda *args: 'new')
    with pytest.raises(tcg.TCGError, match='different process'):
        driver._close_tunnel(time.monotonic() + 10)


def test_tunnel_cannot_adopt_unresolved_creation(driver):
    tcg._write(driver.directory / 'tunnel-intent.json', tcg._canonical({'unit': 'fixture'}))
    with pytest.raises(tcg.TCGError, match='Unresolved tunnel'):
        driver._close_tunnel(time.monotonic() + 10)


def test_spec_does_not_accept_paths_or_partial_identities(spec):
    for change in ({'remote_lab': '/etc'}, {'host': '-oProxyCommand=bad'},
                   {'identities': {'package_sha256': 'a' * 64}}, {'tool_image': 'ubuntu:latest'}):
        with pytest.raises(tcg.TCGError):
            tcg.TCGSpec(**{**asdict(spec), **change}).validate()


def test_subprocess_output_is_capped_while_running_and_child_reaped(tmp_path, monkeypatch):
    monkeypatch.setattr(tcg, 'LIMIT', 10000)
    pid_file = tmp_path.resolve() / 'child-pid'
    program = ('import os,pathlib,sys\n'
               f'pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n'
               'while True: sys.stdout.buffer.write(b"x"*65536); sys.stdout.buffer.flush()\n')
    started = time.monotonic()
    with pytest.raises(tcg.TCGError, match='output exceeds'):
        tcg._run([sys.executable, '-c', program], time.monotonic() + 10)
    assert time.monotonic() - started < 2
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)


def test_subprocess_expected_timeout_is_bounded_and_not_a_success():
    result = tcg._run([sys.executable, '-c', 'import time; time.sleep(10)'],
                      time.monotonic() + .1, timeout_ok=True, check=False)
    assert result.returncode == 124


def test_timeout_cancels_a_helper_that_ignores_term_after_its_parent_exits(tmp_path):
    import fcntl

    child_file = tmp_path.resolve() / 'helper-acquired.json'
    lock_path = tmp_path.resolve() / 'helper-lifetime.lock'
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    identity = os.fstat(descriptor)
    # Only this controlled helper owns this unique lock. It announces AFTER
    # acquiring it and never unlocks voluntarily before its fixed sleep exits.
    # Reacquisition therefore observes release of its own resource, without
    # reading process tables or treating denied ps access as process absence.
    helper = (
        'import fcntl,json,os,pathlib,signal,time; '
        'signal.signal(signal.SIGTERM,signal.SIG_IGN); '
        f'fd=os.open({str(lock_path)!r},os.O_RDWR|os.O_NOFOLLOW); '
        'fcntl.flock(fd,fcntl.LOCK_EX); st=os.fstat(fd); '
        f'pathlib.Path({str(child_file)!r}).write_text(json.dumps('
        '{"pid":os.getpid(),"device":st.st_dev,"inode":st.st_ino})); '
        'time.sleep(30)'
    )
    program = f'import subprocess,time; subprocess.Popen([{sys.executable!r}, "-c", {helper!r}]); time.sleep(30)'
    released = False
    announced = None
    try:
        result = tcg._run([sys.executable, '-c', program], time.monotonic() + 1.0,
                          timeout_ok=True, check=False)
        assert result.returncode == 124
        announced = json.loads(child_file.read_text())
        assert (announced['device'], announced['inode']) == (identity.st_dev, identity.st_ino)
        for _ in range(40):
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                released = True
                break
            except BlockingIOError:
                time.sleep(.025)
        assert released, 'The cancelled command left its controlled helper holding its lifetime lock'
    finally:
        # On a failing implementation, stop only the still-lock-owning helper
        # announced by this test. A released lock never authorizes a PID signal.
        if not released and announced is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                try:
                    os.kill(announced['pid'], signal.SIGKILL)
                except ProcessLookupError:
                    pass
        os.close(descriptor)


@pytest.fixture
def transport_inputs(tmp_path):
    root = tmp_path.resolve()
    public = root / 'host.pub'
    blob = b'unit-independent-public-host-key'
    public.write_bytes(b'ssh-ed25519 ' + base64.b64encode(blob) + b' unit-host\n')
    proxy = root / 'proxy'
    proxy.write_text('#!/bin/sh\nexit 1\n')
    proxy.chmod(0o700)
    cert = root / 'certificate.pub'
    cert.write_bytes(issued_certificate())
    cert.chmod(0o600)
    with tempfile.TemporaryDirectory(prefix='tcg-agent-', dir='/tmp') as short, socket.socket(socket.AF_UNIX) as agent:
        agent_path = Path(short).resolve() / 'agent'
        agent.bind(str(agent_path))
        yield {'directory': root / 'transport', 'hostname': 'unit-host.example.test', 'user': 'operator',
               'host_public_key': public, 'host_key_sha256': hashlib.sha256(blob).hexdigest(),
               'proxy_executable': proxy, 'proxy_arguments': ('--namespace', 'unit', '%h', '%p'),
               'certificate_file': cert, 'identity_agent': agent_path,
               'verification_basis': 'authenticated-control-plane'}


def test_transport_snapshot_excludes_ambient_ssh_configuration_and_control_masters(transport_inputs, spec):
    transport = tcg.create_transport_snapshot(**transport_inputs)
    argv = tcg._ssh(replace(spec, transport=transport))
    assert argv[1:3] == ['-F', transport['config']['path']]
    assert argv[-3:] == ['-S', 'none', 'retained']
    assert 'ControlMaster=no' in argv and 'StrictHostKeyChecking=yes' in argv
    config = tcg._read(transport['config']['path']).decode()
    assert 'Include ' not in config and 'Match ' not in config
    assert 'IdentityFile "' + transport['identity']['path'] + '"' in config and 'ForwardAgent no' in config
    assert 'UpdateHostKeys no' in config and 'GlobalKnownHostsFile /dev/null' in config
    assert str(transport_inputs['identity_agent']) in config
    assert tcg._read(transport['known_hosts']['path']).startswith(b'unit-host.example.test ssh-ed25519 ')
    for name in ('config', 'known_hosts', 'certificate', 'identity'):
        assert os.stat(transport[name]['path']).st_mode & 0o777 == 0o600


@pytest.mark.parametrize('field', ['config', 'known_hosts', 'certificate', 'identity', 'proxy'])
def test_transport_artifact_tampering_is_rejected_before_execution(transport_inputs, spec, field):
    transport = tcg.create_transport_snapshot(**transport_inputs)
    with open(transport[field]['path'], 'ab') as stream:
        stream.write(b'changed')
    with pytest.raises(tcg.TCGError, match='transport changed'):
        tcg._ssh(replace(spec, transport=transport))


@pytest.fixture
def renewable_driver(transport_inputs, spec, tmp_path):
    transport = tcg.create_transport_snapshot(**transport_inputs)
    return tcg.RetainedTCGDriver(tmp_path.resolve() / 'private-renewal', replace(spec, transport=transport))


def test_renewal_activates_only_after_certificate_probe_and_keeps_owner(renewable_driver, tmp_path, monkeypatch):
    driver = renewable_driver
    original_binding = tcg._read(driver.directory / 'binding.json')
    candidate = tmp_path.resolve() / 'issued.pub'
    candidate.write_bytes(issued_certificate(serial=2, valid_before=int(time.time()) + 7200))
    commands = []

    def probe(argv, deadline, **kwargs):
        commands.append(argv)
        assert not (driver.directory / 'transport-generation.json').exists()
        assert 'PubkeyAcceptedAlgorithms=ssh-ed25519-cert-v01@openssh.com' in argv
        assert 'PreferredAuthentications=publickey' in argv
        assert argv[-2:] == ['retained', 'true']
        return subprocess.CompletedProcess(argv, 0, b'', b'')

    monkeypatch.setattr(tcg, '_run', probe)
    receipt = driver.renew_certificate(candidate, 'renew-1', deadline=time.monotonic() + 10)
    record = tcg._json(tcg._read(receipt))
    assert record['status'] == 'authenticated' and record['certificate_only_probe']
    assert tcg._read(driver.directory / 'binding.json') == original_binding
    transport = driver._transport_spec().transport
    for key in driver.spec.transport:
        if key not in ('certificate', 'config'):
            assert transport[key] == driver.spec.transport[key]
    assert tcg._read(transport['certificate']['path']) == candidate.read_bytes()
    assert len(commands) == 1
    resumed = tcg.RetainedTCGDriver(driver.directory, driver.spec)
    assert resumed._transport_spec().transport == transport
    with pytest.raises(tcg.TCGError, match='already used'):
        driver.renew_certificate(candidate, 'renew-1', deadline=time.monotonic() + 10)


@pytest.mark.parametrize('changed', ['key', 'key_id', 'principals', 'critical', 'extensions', 'reserved', 'issuer'])
def test_renewal_rejects_every_grant_change_before_network(renewable_driver, tmp_path, monkeypatch, changed):
    driver = renewable_driver
    candidate = tmp_path.resolve() / 'issued.pub'
    candidate.write_bytes(issued_certificate(**{changed: b'changed'}, serial=2))
    monkeypatch.setattr(tcg, '_run', lambda *a, **k: pytest.fail('changed grant reached SSH'))
    with pytest.raises(tcg.TCGError, match='prior generation retained'):
        driver.renew_certificate(candidate, 'changed-grant', deadline=time.monotonic() + 10)
    assert not (driver.directory / 'transport-generation.json').exists()
    record = json.loads((driver.directory / 'receipts/transport-renewal-changed-grant.json').read_bytes())
    assert record['status'] == 'rejected' and 'grant' in record['error']


@pytest.mark.parametrize('validity', [(-500, -10), (30, 3600), (-30, 100)])
def test_renewal_checks_real_lifetime_before_probe(renewable_driver, tmp_path, monkeypatch, validity):
    candidate = tmp_path.resolve() / 'issued.pub'
    now = int(time.time())
    candidate.write_bytes(issued_certificate(serial=2, valid_after=now + validity[0], valid_before=now + validity[1]))
    monkeypatch.setattr(tcg, '_run', lambda *a, **k: pytest.fail('invalid lifetime reached SSH'))
    with pytest.raises(tcg.TCGError, match='prior generation retained'):
        renewable_driver.renew_certificate(candidate, 'bad-lifetime', deadline=time.monotonic() + 10)


def test_failed_renewal_keeps_prior_generation_and_audit(renewable_driver, tmp_path, monkeypatch):
    driver = renewable_driver
    candidate = tmp_path.resolve() / 'issued.pub'
    candidate.write_bytes(issued_certificate(serial=2))
    monkeypatch.setattr(tcg, '_run', lambda *a, **k: subprocess.CompletedProcess([], 0, b'', b''))
    driver.renew_certificate(candidate, 'good', deadline=time.monotonic() + 10)
    pointer = tcg._read(driver.directory / 'transport-generation.json')
    old = driver._transport_spec().transport
    candidate.write_bytes(issued_certificate(serial=3))

    def failure(*args, **kwargs):
        raise tcg.TCGError('Pinned host authentication failed')

    monkeypatch.setattr(tcg, '_run', failure)
    with pytest.raises(tcg.TCGError, match='prior generation retained'):
        driver.renew_certificate(candidate, 'bad', deadline=time.monotonic() + 10)
    assert tcg._read(driver.directory / 'transport-generation.json') == pointer
    assert driver._transport_spec().transport == old
    assert (driver.directory / 'receipts/transport-renewal-bad.json').is_file()


def test_renewal_chain_retains_prior_audit_and_rejects_tampering(renewable_driver, tmp_path, monkeypatch):
    driver = renewable_driver
    candidate = tmp_path.resolve() / 'issued.pub'
    monkeypatch.setattr(tcg, '_run', lambda *a, **k: subprocess.CompletedProcess([], 0, b'', b''))
    candidate.write_bytes(issued_certificate(serial=2))
    first = driver.renew_certificate(candidate, 'first', deadline=time.monotonic() + 10)
    candidate.write_bytes(issued_certificate(serial=3))
    second = driver.renew_certificate(candidate, 'second', deadline=time.monotonic() + 10)
    assert json.loads(second.read_bytes())['previous']['sha256'] == tcg._sha(first.read_bytes())
    assert tcg._certificate_grant(tcg._read(driver._transport_spec().transport['certificate']['path']))['serial'] == 3
    record = json.loads(first.read_bytes())
    record['binding_sha256'] = '0' * 64
    tcg._write(first, tcg._canonical(record), replace=True)
    with pytest.raises(tcg.TCGError, match='audit changed'):
        driver._transport_spec()


def test_certificate_parser_matches_real_openssh_issued_public_certificate(tmp_path):
    root = tmp_path.resolve()
    for name in ('ca', 'client'):
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(root / name)],
                       check=True, capture_output=True, timeout=10)
    subprocess.run(['ssh-keygen', '-q', '-s', str(root / 'ca'), '-I', 'unit-test-only', '-n', 'operator',
                    '-z', '42', '-V', '-1m:+1h', str(root / 'client.pub')], check=True, capture_output=True, timeout=10)
    grant = tcg._certificate_grant((root / 'client-cert.pub').read_bytes())
    assert grant['serial'] == 42
    assert grant['valid_after'] < time.time() < grant['valid_before']
    assert 3500 < grant['valid_before'] - time.time() < 3700
    assert tcg._certificate_identity((root / 'client-cert.pub').read_bytes()).strip().split() == (root / 'client.pub').read_bytes().strip().split()[:2]


def test_certificate_comment_may_contain_spaces_without_changing_grant():
    raw = issued_certificate()
    changed = raw.replace(b'unit-fixture\n', b'normal issuer comment with spaces\n')
    assert tcg._certificate_grant(changed) == tcg._certificate_grant(raw)
    with pytest.raises(tcg.TCGError):
        tcg._certificate_grant(changed + b'second-certificate-line\n')


@pytest.mark.parametrize('raw', [issued_certificate()[:-50], b'invalid', issued_certificate(type=2),
                              issued_certificate(valid_after=100, valid_before=50)])
def test_malformed_or_host_certificate_cannot_be_renewed(raw):
    with pytest.raises(tcg.TCGError):
        tcg._certificate_grant(raw)


@pytest.mark.parametrize('change', [
    {'verification_basis': 'trust-on-first-use'}, {'host_key_sha256': 'f' * 64},
    {'hostname': 'unit-host\nProxyCommand malicious'}, {'proxy_arguments': ('x;evil',)},
])
def test_transport_snapshot_rejects_unverified_or_injected_routes(transport_inputs, change):
    with pytest.raises(tcg.TCGError):
        tcg.create_transport_snapshot(**{**transport_inputs, **change})


@pytest.fixture
def scoped_spec(transport_inputs, spec, tmp_path):
    root = tmp_path.resolve()
    for name, kind in [('ca', 'ed25519'), ('signer', 'ecdsa')]:
        subprocess.run(['ssh-keygen', '-q', '-t', kind, '-N', '', '-f', str(root / name)],
                       check=True, capture_output=True, timeout=10)
    subprocess.run(['ssh-keygen', '-q', '-s', str(root / 'ca'), '-I', 'context-unit-only', '-n', 'operator',
                    '-V', '-1m:+1h', str(root / 'signer.pub')], check=True, capture_output=True, timeout=10)
    transport_inputs['certificate_file'].write_bytes((root / 'signer-cert.pub').read_bytes())
    transport = tcg.create_transport_snapshot(**transport_inputs, agent_protocol='certificate-view-v1')
    return replace(spec, transport=transport)


@pytest.mark.parametrize('body_failure', [False, True])
def test_scoped_context_selects_only_certificate_and_removes_private_view(scoped_spec, tmp_path, monkeypatch, body_failure):
    """Real private sockets and ssh -G; controlled upstream signing transcript."""
    from devsecops.factory.certificate_agent import CertificateAgent, receive, wire

    def exchange(adapter, body):
        if body[:1] == b'\x1b':
            return b'\x05'  # Preserve an agent's unsupported extension response.
        if body == b'\x0b':
            return b'\x0c' + struct.pack('>I', 1) + wire(adapter.key) + wire(b'unit-signer')
        return b'\x0e' + wire(wire(b'ecdsa-sha2-nistp256') + wire(b'unit-signature'))

    monkeypatch.setattr(CertificateAgent, 'exchange', exchange)
    audit = tcg._private(tmp_path.resolve() / 'audit', create=True)
    before = {name: tcg._read(scoped_spec.transport[name]['path'])
              for name in ('config', 'identity', 'certificate', 'known_hosts')}
    deadline = time.monotonic() + 10
    try:
        with tcg._ssh_context(scoped_spec, deadline, audit) as argv:
            config = Path(argv[argv.index('-F') + 1])
            assert config != Path(scoped_spec.transport['config']['path'])
            actual = tcg._run([argv[0], '-G', *argv[1:]], deadline).stdout.decode().splitlines()
            assert [line for line in actual if line.startswith('identityfile ')] == [
                'identityfile ' + scoped_spec.transport['certificate']['path']]
            assert 'certificatefile none' in actual
            assert 'pubkeyauthentication host-bound' in actual
            assert 'pubkeyacceptedalgorithms ecdsa-sha2-nistp256-cert-v01@openssh.com' in actual
            socket_path = next(line.removeprefix('identityagent ') for line in actual if line.startswith('identityagent '))
            cert = base64.b64decode(before['certificate'].split()[1])
            host = base64.b64decode(before['known_hosts'].split()[2])
            session = b's' * 32
            bind = b'\x1b' + wire(b'session-bind@openssh.com') + wire(host) + wire(session) + wire(b'unit-host-signature') + b'\x00'
            data = wire(session) + b'\x32' + wire(b'operator') + wire(b'ssh-connection')
            data += wire(b'publickey-hostbound-v00@openssh.com') + b'\x01'
            data += wire(b'ecdsa-sha2-nistp256-cert-v01@openssh.com') + wire(cert) + wire(host)
            with socket.socket(socket.AF_UNIX) as client:
                client.settimeout(2)
                client.connect(socket_path)
                for request, answer in [(bind, b'\x05'), (b'\x0b', b'\x0c'),
                                        (b'\x0d' + wire(cert) + wire(data) + b'\x00' * 4, b'\x0e')]:
                    client.sendall(wire(request))
                    assert receive(client, deadline).startswith(answer)
            if body_failure:
                raise RuntimeError('controlled command failure')
    except RuntimeError as error:
        assert body_failure and str(error) == 'controlled command failure'
    assert not config.exists() and not config.parent.exists() and not Path(socket_path).exists()
    assert {name: tcg._read(scoped_spec.transport[name]['path']) for name in before} == before
    receipt = tcg._json(tcg._read(next(audit.glob('ssh-agent-*.json'))))
    assert receipt['agent_audit']['sign_attempts'] == receipt['agent_audit']['signatures'] == 1
    assert receipt['temporary_agent_removed']
    assert receipt['status'] == ('failed' if body_failure else 'command_completed')


def test_scoped_context_cannot_claim_success_without_signing(scoped_spec, tmp_path):
    audit = tcg._private(tmp_path.resolve() / 'audit', create=True)
    with pytest.raises(tcg.TCGError, match='one observed host-bound signature'):
        with tcg._ssh_context(scoped_spec, time.monotonic() + 5, audit) as argv:
            config = Path(argv[argv.index('-F') + 1])
    assert not config.exists() and not config.parent.exists()
    assert tcg._json(tcg._read(next(audit.glob('ssh-agent-*.json'))))['status'] == 'failed'


def test_local_control_never_contacts_adapter_or_authenticates(scoped_spec, tmp_path, monkeypatch):
    from devsecops.factory import certificate_agent
    monkeypatch.setattr(certificate_agent, 'CertificateAgent', lambda *a, **k: pytest.fail('control opened agent'))
    control = tmp_path.resolve() / 'control'
    with tcg._ssh_context(scoped_spec, time.monotonic() + 5, tmp_path, control=control, control_only=True) as argv:
        assert 'PubkeyAuthentication=no' in argv
        assert argv[-3:] == ['-S', str(control), 'retained']
    with pytest.raises(tcg.TCGError, match='control requires'):
        with tcg._ssh_context(scoped_spec, time.monotonic() + 5, tmp_path, control_only=True):
            pytest.fail('control admitted absent socket path')


def test_agent_protocol_change_requires_new_binding(renewable_driver):
    changed = replace(renewable_driver.spec, transport={**renewable_driver.spec.transport,
                                                       'agent_protocol': 'certificate-view-v1'})
    assert changed.digest != renewable_driver.spec.digest
    with pytest.raises(tcg.TCGError, match='Changed run'):
        tcg.RetainedTCGDriver(renewable_driver.directory, changed)


@pytest.mark.parametrize('mode', ['raw-fallback', True, None])
def test_unknown_agent_protocol_is_rejected_before_execution(spec, mode):
    with pytest.raises(tcg.TCGError, match='agent protocol'):
        replace(spec, transport={**spec.transport, 'agent_protocol': mode}).validate()


def test_all_outer_ssh_sites_enter_and_exit_the_command_context(renewable_driver, tmp_path, monkeypatch):
    """Controlled process transcript verifies startup and cleanup use the context."""
    from devsecops.factory.certificate_agent import socket_root
    events = []
    alive = [True]

    @contextmanager
    def context(spec, deadline, audit, **options):
        events.append(('enter', options))
        if options.get('forwarding'):
            options['control'].touch()
        try:
            yield ['ssh', '-S', str(options.get('control', 'none')), 'retained']
        finally:
            events.append(('exit', options))

    def run(argv, *args, **kwargs):
        if '-O' in argv:
            alive[0] = False
        response = {'binding_sha256': driver.spec.digest, 'operation_id': 'observe',
                    'observation': {'state': 'absent'}}
        return subprocess.CompletedProcess(argv, 0, tcg._canonical(response), b'')

    class Process:
        pid = 123456789

        def poll(self):
            return None

    monkeypatch.setattr(tcg, '_ssh_context', context)
    monkeypatch.setattr(tcg, '_run', run)
    monkeypatch.setattr(tcg.subprocess, 'Popen', lambda *a, **k: Process())
    candidate = tmp_path.resolve() / 'issued.pub'
    candidate.write_bytes(issued_certificate(serial=2))
    with tempfile.TemporaryDirectory(prefix='tcg-context-', dir=socket_root()) as short:
        driver = tcg.RetainedTCGDriver(Path(short), renewable_driver.spec)
        monkeypatch.setattr(driver, '_process_identity', lambda *a: 'unit-process' if alive[0] else None)
        driver.renew_certificate(candidate, 'renew', deadline=time.monotonic() + 5)
        assert driver._remote('reconcile', 'observe', time.monotonic() + 5)['state'] == 'absent'
        driver._tunnel('172.18.0.2', 'tunnel', time.monotonic() + 5)
        assert driver._close_tunnel(time.monotonic() + 5)['owned_tunnel_absent']
    assert [event[0] for event in events] == ['enter', 'exit'] * 4
    assert events[4][1]['forwarding'] is True
    assert events[6][1]['control_only'] is True


def remote_fixture(tmp_path, spec, monkeypatch, *, container_present=True, network_present=True, pending=None):
    """Controlled Docker transcripts, with real private state files/locks."""
    lab = tmp_path.resolve() / 'lab'
    tcg._private(lab, create=True)
    tcg._private(lab / 'operator', create=True)
    tcg._write(lab / 'operator/api-ca.crt', b'unit-ca')
    tcg._write(lab / 'probe.qcow2', b'unit-disk-fixture')
    spec = replace(spec, original_ca_sha256=tcg._sha(b'unit-ca'))
    tcg._write(lab / 'state.json', tcg._canonical({'run_id': spec.provision_run_id, 'image_id': spec.tool_image}))
    tcg._write(lab / 'user-data', b'#cloud-config\n' + tcg._canonical({
        'users': [{'name': 'demo', 'groups': ['sudo'], 'shell': '/bin/bash', 'lock_passwd': True,
                   'sudo': ['ALL=(ALL) NOPASSWD:ALL'], 'ssh_authorized_keys': ['ssh-ed25519 unit']}],
        'ssh_pwauth': False, 'disable_root': True, 'package_update': False, 'package_upgrade': False,
        'ssh_deletekeys': True, 'ssh_genkeytypes': [],
        'ssh_keys': {'ed25519_private': '-----BEGIN OPENSSH PRIVATE KEY----- fixture', 'ed25519_public': 'ssh-ed25519 unit'},
        'runcmd': [['touch', '/run/devsecops-probe-ready']]}))
    retained = tcg._private(lab / 'retained', create=True)
    tcg._write(retained / 'binding.json', tcg._canonical({'spec': asdict(spec), 'driver_sha256': 'd' * 64}))
    tcg._write(retained / 'lock', b'')
    state = {'history': ['unit-operation'], 'pending': pending,
             'container_id': 'b' * 64, 'network_id': 'a' * 64}
    tcg._write(retained / 'state.json', tcg._canonical(state))
    actual_private = tcg._private
    monkeypatch.setattr(tcg, '_private', lambda path, **kw:
                        lab if str(path) == spec.remote_lab else actual_private(path, **kw))
    container, network = resources(spec)
    fixtures = {'container': container if container_present else None, 'network': network if network_present else None}
    commands = []
    def run(argv, *args, **kwargs):
        commands.append(argv)
        assert argv[0] == 'docker' and argv[2] in ('inspect', 'ls'), 'Reconcile must not execute writes'
        item = fixtures[argv[1]]
        if argv[2] == 'ls':
            return subprocess.CompletedProcess(argv, 0, ((item['Id'] + '\n').encode() if item else b''), b'')
        if item:
            return subprocess.CompletedProcess(argv, 0, json.dumps([item]).encode(), b'')
        return subprocess.CompletedProcess(argv, 1, b'', b'No such object')
    monkeypatch.setattr(tcg, '_run', run)
    payload = {'spec': asdict(spec), 'driver_sha256': 'd' * 64, 'action': 'reconcile',
               'operation_id': pending['operation_id'] if pending else 'unit-operation', 'timeout': 30}
    return payload, retained, state, fixtures, commands


@pytest.mark.parametrize('kind', ['container', 'network'])
def test_reconcile_rejects_replacement_id_even_with_matching_name_and_labels(tmp_path, spec, monkeypatch, kind):
    payload, _, _, fixtures, _ = remote_fixture(tmp_path, spec, monkeypatch)
    fixtures[kind]['Id'] = 'f' * 64
    with pytest.raises(tcg.TCGError, match='Docker ID differs'):
        tcg._remote_action(payload)


def test_reconcile_is_read_only_and_absent_interrupted_down_is_applied(tmp_path, spec, monkeypatch):
    pending = {'action': 'down', 'operation_id': 'unit-down',
               'before': {'container': 'b' * 64, 'network': 'a' * 64}}
    payload, retained, _, _, commands = remote_fixture(tmp_path, spec, monkeypatch,
        container_present=False, network_present=False, pending=pending)
    before = {p.name: p.read_bytes() for p in retained.iterdir()}
    result = tcg._remote_action(payload)
    assert result['state'] == 'absent'
    disk = (retained.parent / 'probe.qcow2').stat()
    assert result['disk_identity'] == {'device': disk.st_dev, 'inode': disk.st_ino}
    assert result['observed_mutation_effect'] == 'applied'
    assert {p.name: p.read_bytes() for p in retained.iterdir()} == before
    assert commands


def test_partial_interrupted_down_records_applied_effect_without_cleanup_success(tmp_path, spec, monkeypatch):
    pending = {'action': 'down', 'operation_id': 'unit-down',
               'before': {'container': 'b' * 64, 'network': 'a' * 64}}
    payload, _, _, _, _ = remote_fixture(tmp_path, spec, monkeypatch,
        container_present=False, network_present=True, pending=pending)
    result = tcg._remote_action(payload)
    assert result['observed_mutation_effect'] == 'applied'
    assert result['mutation_completion'] == 'partial'
    assert result['remaining_owned_runtime'] is True
    assert result['state'] != 'absent'


def test_disconnected_create_cannot_be_declared_not_applied_from_absence(tmp_path, spec, monkeypatch):
    pending = {'action': 'up', 'operation_id': 'unit-up', 'before': {'container': None, 'network': None}}
    payload, retained, state, _, _ = remote_fixture(tmp_path, spec, monkeypatch,
        container_present=False, network_present=True, pending=pending)
    state['inflight'] = {'kind': 'container', 'verb': 'create'}
    tcg._write(retained / 'state.json', tcg._canonical(state), replace=True)
    assert tcg._remote_action(payload)['observed_mutation_effect'] == 'unknown'


def test_cross_operation_reconciliation_is_rejected(tmp_path, spec, monkeypatch):
    pending = {'action': 'up', 'operation_id': 'unit-up', 'before': {'container': None, 'network': None}}
    payload, _, _, _, _ = remote_fixture(tmp_path, spec, monkeypatch, pending=pending)
    payload['operation_id'] = 'other-operation'
    with pytest.raises(tcg.TCGError, match='different pending operation'):
        tcg._remote_action(payload)


def test_up_preflight_queries_original_owner_using_docker_label_filter(tmp_path, spec, monkeypatch):
    payload, _, _, _, _ = remote_fixture(tmp_path, spec, monkeypatch,
        container_present=False, network_present=False)
    payload['action'] = 'up'
    payload['operation_id'] = 'next-up'
    original_run = tcg._run
    checked = []
    def docker(argv, *args, **kwargs):
        if '--filter' in argv and 'tcg-probe.run' in argv[-1]:
            # Docker accepts label=<key>=<value>, not <key>=<value> as a
            # top-level filter. Both container and network cleanup proof are
            # needed before a new runtime may be created.
            assert argv[-1] == f'label=io.devsecops.tcg-probe.run={spec.provision_run_id}'
            checked.append(argv[1])
            return subprocess.CompletedProcess(argv, 0, b'', b'')
        if argv[1:3] == ['image', 'inspect']:
            raise tcg.TCGError('Stop fixture after original-runtime preflight')
        return original_run(argv, *args, **kwargs)
    monkeypatch.setattr(tcg, '_run', docker)
    with pytest.raises(tcg.TCGError, match='Stop fixture'):
        tcg._remote_action(payload)
    assert checked == ['container', 'network']


def test_adoption_establishes_only_exact_ephemeral_guest_reply_route(tmp_path, spec, monkeypatch):
    payload, retained, _, fixtures, _ = remote_fixture(tmp_path, spec, monkeypatch)
    payload.update(action='up', operation_id='next-up', timeout=650)
    lab = retained.parent
    tcg._private(lab / 'ssh', create=True)
    tcg._write(lab / 'ssh/host.pub', b'ssh-ed25519 unit-key\n')
    previous_run = tcg._run
    commands = []

    def run(argv, *args, **kwargs):
        if argv[0] != 'ssh':
            return previous_run(argv, *args, **kwargs)
        commands.append(argv)
        assert 'docker exec -i ' + fixtures['container']['Id'] in next(a for a in argv if a.startswith('ProxyCommand='))
        output = b''
        if len(commands) == 2:
            identity_args = shlex.split(argv[-1])
            assert identity_args[:4] == ['sudo', 'python3', '-I', '-c']
            assert identity_args[-2] == payload['spec']['original_ca_sha256']
            output = tcg._canonical({'node_ip': '10.0.2.15', 'node_name': 'devsecops-rke2-screen',
                'ca_sha256': identity_args[-2], 'seed_sha256': identity_args[-1],
                'unit_sha256': '30cb868bfbc40d69b3c3b7553493c50c17538f3a550c89ff2300d13bb66a61b1',
                'migration': 'not-needed'})
        if len(commands) == 4:
            assert argv[-1].endswith('172.18.0.1')
            assert "'route','add',target" in argv[-1].replace("'\"'\"'", "'")
            output = tcg._canonical({'destination': '172.18.0.1/32', 'gateway': '10.0.2.2',
                                     'device': 'enp0s2', 'ephemeral': True, 'already_present': False})
        return subprocess.CompletedProcess(argv, 0, output, b'')

    monkeypatch.setattr(tcg, '_run', run)
    result = tcg._remote_action(payload)
    assert [commands[i][-1] for i in (0, 2)] == ['true', 'sudo test -f /etc/rancher/rke2/rke2.yaml']
    assert result['guest_rke2_identity']['node_ip'] == '10.0.2.15'
    route = result['guest_reply_route']
    assert route['destination'] == '172.18.0.1/32' and route['ephemeral']
    assert route['network_id'] == fixtures['network']['Id']
    assert route['container_id'] == fixtures['container']['Id']
    assert result['state'] == 'running_unready'
    assert json.loads((retained / 'next-up-ssh-observation.json').read_bytes())['attempts'] == 1


def test_failed_guest_identity_gate_retains_diagnostics_and_pending_ownership(tmp_path, spec, monkeypatch):
    payload, retained, _, _, _ = remote_fixture(tmp_path, spec, monkeypatch)
    payload.update(action='up', operation_id='identity-failure', timeout=650)
    lab = retained.parent
    tcg._private(lab / 'ssh', create=True)
    tcg._write(lab / 'ssh/host.pub', b'ssh-ed25519 unit-key\n')
    previous_run = tcg._run
    calls = []
    def run(argv, *args, **kwargs):
        if argv[0] != 'ssh':
            return previous_run(argv, *args, **kwargs)
        calls.append(argv)
        if argv[-1] == 'true':
            return subprocess.CompletedProcess(argv, 0, b'', b'')
        assert 'assure(sys.argv' in argv[-1] and kwargs['check'] is False
        return subprocess.CompletedProcess(argv, 2, b'{"error":"unit-overrides"}\n', b'private-fixture-diagnostic')
    monkeypatch.setattr(tcg, '_run', run)
    with pytest.raises(tcg.TCGError, match='RKE2 identity gate failed'):
        tcg._remote_action(payload)
    state = json.loads((retained / 'state.json').read_bytes())
    assert state['pending']['operation_id'] == 'identity-failure'
    assert state['container_id'] == 'b' * 64 and state['network_id'] == 'a' * 64
    assert state['guest_identity_attempt']['returncode'] == 2
    assert (retained / 'identity-failure-guest-identity.stderr.log').read_bytes() == b'private-fixture-diagnostic'
    assert len(calls) == 2 and 'guest_reply_route' not in state and 'guest_rke2_identity' not in state


def test_clipped_remote_budget_never_dispatches_guest_config_mutation(tmp_path, spec, monkeypatch):
    payload, retained, _, _, _ = remote_fixture(tmp_path, spec, monkeypatch)
    payload.update(action='up', operation_id='short-budget', timeout=30)
    tcg._private(retained.parent / 'ssh', create=True)
    tcg._write(retained.parent / 'ssh/host.pub', b'ssh-ed25519 unit-key\n')
    previous_run = tcg._run
    calls = []
    def run(argv, *args, **kwargs):
        if argv[0] != 'ssh':
            return previous_run(argv, *args, **kwargs)
        calls.append(argv)
        assert argv[-1] == 'true'
        return subprocess.CompletedProcess(argv, 0, b'', b'')
    monkeypatch.setattr(tcg, '_run', run)
    with pytest.raises(tcg.TCGError, match='cannot cover the fixed guest identity step'):
        tcg._remote_action(payload)
    assert len(calls) == 1


def test_reconciled_cleanup_observes_retained_seed_credentials_disk_ca_and_image(tmp_path, spec, monkeypatch):
    pending = {'operation_id': 'unit-operation', 'action': 'down',
               'before': {'container': 'b'*64, 'network': 'a'*64}}
    payload, retained, state, fixtures, commands = remote_fixture(tmp_path, spec, monkeypatch,
        container_present=False, network_present=False, pending=pending)
    lab = retained.parent
    tcg._private(lab/'ssh', create=True)
    for name in ('seed.iso', 'ssh/operator', 'ssh/host'):
        tcg._write(lab/name, ('controlled-'+name).encode())
    protected = {}
    for name in ('seed.iso', 'user-data', 'ssh/operator', 'ssh/host', 'operator/api-ca.crt'):
        item = (lab/name).stat()
        protected[name] = {'sha256': tcg._sha(tcg._read(lab/name)), 'device': item.st_dev, 'inode': item.st_ino}
    state['protected_inputs'] = protected
    tcg._write(retained/'state.json', tcg._canonical(state), replace=True)
    fixtures['image'] = {'Id': spec.tool_image}
    observed = tcg._remote_action(payload)
    assert observed['protected_resources']['verified'] is True
    assert observed['protected_resources']['files'] == protected
    assert observed['disk_identity'] == observed['protected_resources']['disk_identity']
    tcg._write(lab/'seed.iso', b'changed-original-seed', replace=True)
    changed = tcg._remote_action(payload)
    assert changed['protected_resources']['verified'] is False
    assert all(command[2] in ('inspect', 'ls') for command in commands)
