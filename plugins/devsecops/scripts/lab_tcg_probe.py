#!/usr/bin/env python3
"""Run one bounded, isolated Ubuntu/QEMU boot probe on an owned Linux devbox.

The default probe only boots Linux. --rke2-screen additionally installs the
pinned public RKE2 distribution inside a new guest and measures API/node
readiness. Neither mode qualifies Big Bang or Iron Bank. Remote credentials
and logs stay private. The Docker daemon remains privileged infrastructure;
the QEMU container is not.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1] if '__file__' in globals() and __file__ != '<stdin>' else None
IMAGE_URL = 'https://cloud-images.ubuntu.com/releases/noble/release-20261017/ubuntu-24.04-server-cloudimg-amd64.img'
IMAGE_SHA256 = '04e8f2461a16a44e78a370284471f29616cdd9baf1893df25807b71f985b6c94'
CLOUD_SIGNER = 'D2EB44626FDDC30B513D5BB71A5D6C4C7DB87C81'
LABEL = 'io.devsecops.tcg-probe.run'
GUEST_BYTES = 8 * 1024**3
CONTAINER_BYTES = 9 * 1024**3
BOOT_TIMEOUT = 600
RKE2_TIMEOUT = 1200
PACKAGES = {'qemu-system-x86': '1:8.2.2+ds-0ubuntu1.18',
            'qemu-utils': '1:8.2.2+ds-0ubuntu1.18', 'cloud-image-utils': '0.33-1'}


class ProbeError(RuntimeError):
    pass


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def write_private(path, data):
    data = data if isinstance(data, bytes) else data.encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)


def atomic_private(path, value):
    temporary = path.parent / ('.state-' + uuid4().hex)
    write_private(temporary, json.dumps(value, sort_keys=True, indent=2) + '\n')
    os.replace(temporary, path)


def private_dir(path):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ProbeError('Private lab paths must not contain symlinks')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ProbeError('Private lab directory must be owned by this user and mode 0700')
    return path


def cloud_config(client_public, host_private, host_public):
    return '#cloud-config\n' + json.dumps({
        'users': [{'name': 'demo', 'groups': ['sudo'], 'shell': '/bin/bash', 'lock_passwd': True,
                   'sudo': ['ALL=(ALL) NOPASSWD:ALL'], 'ssh_authorized_keys': [client_public.strip()]}],
        'ssh_pwauth': False, 'disable_root': True, 'package_update': False, 'package_upgrade': False,
        'ssh_deletekeys': True, 'ssh_genkeytypes': [],
        'ssh_keys': {'ed25519_private': host_private, 'ed25519_public': host_public.strip()},
        'runcmd': [['touch', '/run/devsecops-probe-ready']],
    }, indent=2) + '\n'


def require_owner(info, run_id, *, network=False):
    labels = info.get('Labels', {}) if network else info.get('Config', {}).get('Labels', {})
    if labels.get(LABEL) != run_id or not re.fullmatch(r'[0-9a-f]{64}', info.get('Id', '')):
        raise ProbeError('Resource identity or ownership label does not match this probe')
    return info['Id']


def validate_runtime(info, run_id, lab, network_name, *, container_bytes=CONTAINER_BYTES, cpus=4):
    require_owner(info, run_id)
    host = info['HostConfig']
    bindings = host.get('PortBindings', {})
    mounts = info.get('Mounts', [])
    if (host.get('Privileged') or host.get('ReadonlyRootfs') is not True
            or host.get('CapDrop') != ['ALL'] or host.get('CapAdd')
            or 'no-new-privileges' not in host.get('SecurityOpt', [])
            or host.get('Memory') != container_bytes or host.get('MemorySwap') != container_bytes
            or host.get('NanoCpus') != cpus * 1_000_000_000 or host.get('PidsLimit') != 256
            or host.get('NetworkMode') != network_name or host.get('PidMode') == 'host'
            or host.get('IpcMode') == 'host' or host.get('Devices')
            or info['Config'].get('User') != f'{os.getuid()}:{os.getgid()}'
            or len(mounts) != 1 or mounts[0].get('Type') != 'bind'
            or mounts[0].get('Source') != str(lab) or mounts[0].get('Destination') != '/lab'
            or set(bindings) != {'2222/tcp'}
            or any(binding.get('HostIp') != '127.0.0.1' for values in bindings.values() for binding in values)):
        raise ProbeError('Observed container controls differ from the isolated probe contract')


def now():
    return datetime.now(timezone.utc).isoformat()


def ssh_endpoint(info, network, run_id):
    """Prefer effective loopback publish; internal Docker bridges may omit it."""
    require_owner(info, run_id)
    require_owner(network, run_id, network=True)
    if network.get('Internal') is not True:
        raise ProbeError('The owned probe network must be internal')
    ports = info['NetworkSettings']['Ports']
    if set(ports) != {'2222/tcp'}:
        raise ProbeError('Unexpected effective published ports')
    bindings = ports['2222/tcp']
    if bindings:
        if len(bindings) != 1 or bindings[0].get('HostIp') != '127.0.0.1':
            raise ProbeError('SSH publish is not loopback-only')
        port = int(bindings[0]['HostPort'])
        if not 1024 <= port <= 65535:
            raise ProbeError('SSH publish is not an allocated high port')
        return '127.0.0.1', port, 'loopback-publish'
    connections = info['NetworkSettings']['Networks']
    if set(connections) != {network['Name']}:
        raise ProbeError('Container has an unexpected network attachment')
    connection = connections[network['Name']]
    address = ipaddress.ip_address(connection['IPAddress'])
    if (connection.get('NetworkID') != network['Id'] or address.version != 4 or address.is_global
            or not any(address in ipaddress.ip_network(config['Subnet']) for config in network['IPAM']['Config'])):
        raise ProbeError('SSH endpoint is outside the exact owned private network')
    return str(address), 2222, 'owned-internal-bridge-via-devbox'


def classify_ssh_probe(returncode, stderr):
    """Only startup transients are retryable; identity failures end the probe."""
    error = stderr.lower()
    if any(marker in error for marker in (b'host identification has changed', b'host key verification failed',
                                          b'host key is known for', b'no matching host key', b'permission denied')):
        raise ProbeError('Pinned SSH host or client identity verification failed')
    if returncode == 0:
        return 'ready'
    if returncode in (1, 124):
        return 'pending'
    if returncode == 255 and any(marker in error for marker in (
            b'connection refused', b'connection timed out', b'connection closed',
            b'connection reset', b'no route to host', b'connection to ')):
        return 'pending'
    raise ProbeError('SSH probe failed outside the allowed startup transient conditions')


def apt_config(root):
    """Read this through APT_CONFIG before apt loads its normal config parts."""
    paths = {'Dir::Etc::main': '/dev/null', 'Dir::Etc::parts': root / 'empty',
             'Dir::Etc::sourcelist': root / 'sources.list', 'Dir::Etc::sourceparts': root / 'empty',
             'Dir::State': root / 'state', 'Dir::State::status': root / 'status',
             'Dir::State::lists': root / 'lists', 'Dir::Cache': root / 'cache',
             'Dir::Cache::archives': root / 'archives', 'Dir::Log': root / 'log'}
    settings = {**{key: str(value) for key, value in paths.items()},
                'APT::Get::Assume-Yes': 'true', 'APT::Install-Recommends': 'false',
                'APT::Install-Suggests': 'false', 'Acquire::Retries': '0',
                'Acquire::http::Timeout': '30', 'Acquire::https::Timeout': '30',
                'Acquire::AllowInsecureRepositories': 'false', 'APT::Get::AllowUnauthenticated': 'false'}
    return ''.join(key + ' ' + json.dumps(value) + ';\n' for key, value in settings.items())


def validate_rke2_pins(pins):
    """Only this operator-screen tuple is accepted; no arbitrary install URL."""
    if (pins.get('schema_version') != 1 or pins.get('rke2_version') != 'v1.36.5+rke2r1'
            or pins.get('bigbang', {}).get('version') != '3.35.1'
            or pins.get('bigbang', {}).get('kube_version') != '>=1.34.0-0'):
        raise ProbeError('RKE2 pins differ from the reviewed compatibility tuple')
    expected = {'rke2-images.linux-amd64.tar.zst', 'rke2-images.linux-amd64.txt',
                'rke2.linux-amd64.tar.gz', 'sha256sum-amd64.txt', 'install.sh'}
    assets = pins.get('assets', [])
    if len(assets) != len(expected) or {item['name'] for item in assets} != expected:
        raise ProbeError('RKE2 pin set is incomplete or contains unexpected artifacts')
    for item in assets:
        url = ('https://raw.githubusercontent.com/rancher/rke2/v1.36.5+rke2r1/install.sh'
               if item['name'] == 'install.sh' else
               'https://github.com/rancher/rke2/releases/download/v1.36.5%2Brke2r1/' + item['name'])
        if (item['url'] != url or not re.fullmatch('[0-9a-f]{64}', item['sha256'])
                or type(item['bytes']) is not int or not 0 < item['bytes'] < 2 * 1024**3):
            raise ProbeError('RKE2 artifact URL, digest, or size is outside the reviewed source contract')
    return pins


def verify_release_checksums(pins, checksum_text):
    for item in pins['assets']:
        if item['name'] in ('install.sh', 'sha256sum-amd64.txt'):
            continue
        matches = [row.split()[0] for row in checksum_text.splitlines()
                   if len(row.split()) == 2 and row.split()[1].lstrip('*') == item['name']]
        if matches != [item['sha256']]:
            raise ProbeError('RKE2 release checksums do not bind each exact reviewed asset')


def download_opener(hosts):
    class ApprovedRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            target = urlsplit(newurl)
            if (target.scheme != 'https' or target.hostname not in hosts or target.port not in (None, 443)
                    or target.username or target.password):
                raise ProbeError('Acquisition redirect is outside the approved HTTPS origins')
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    return urllib.request.build_opener(ApprovedRedirect())


def rke2_observation(value, version):
    """A single Ready node plus authenticated readyz and exact version, not JSON self-attestation."""
    nodes = value.get('nodes', [])
    return (value.get('readyz') == 'ok' and value.get('server_version') == version
            and len(nodes) == 1 and nodes[0].get('ready') is True
            and nodes[0].get('kubelet_version') == version)


def screen_rke2(command, ssh, lab, report, pins, save):
    """Perform actual guest operations; only private logs receive raw output."""
    save('stage-rke2-in-guest')
    destination = '/home/demo/rke2-artifacts'
    command([*ssh, 'mkdir -m 700 ' + destination])
    scp = ['scp', '-O', *ssh[1:-3], '-P', ssh[-2]]
    for asset in pins['assets']:
        command([*scp, str(lab / 'rke2-artifacts' / asset['name']), ssh[-1] + ':' + destination + '/'], timeout=600)
    guest_check = """import hashlib,json,pathlib
assets=json.loads(%r)
for asset in assets:
 p=pathlib.Path('/home/demo/rke2-artifacts')/asset['name']; h=hashlib.sha256()
 with p.open('rb') as stream:
  while chunk:=stream.read(1048576): h.update(chunk)
 if h.hexdigest()!=asset['sha256'] or p.stat().st_size!=asset['bytes']: raise SystemExit(2)
print('verified')
""" % json.dumps(pins['assets'])
    verified = command([*ssh, shlex.join(['python3', '-c', guest_check])], timeout=300)
    if verified.stdout.strip() != b'verified':
        raise ProbeError('Guest-local RKE2 artifact verification did not complete')
    observations = []
    result = {'status': 'incomplete', 'limit_seconds': RKE2_TIMEOUT,
              'gate_starts_after_guest_artifact_verification': True,
              'guest_local_artifacts_verified': True, 'observations': observations,
              'required_consecutive_observations': 3, 'bigbang_installed': False,
              'registry1_or_ironbank_qualification': 'not_performed'}
    report['rke2'] = result
    save('install-and-start-rke2')
    start = time.monotonic()
    result['gate_started_at'] = now()
    config = {'write-kubeconfig-mode': '0600', 'cni': 'canal', 'node-name': 'devsecops-rke2-screen',
              'node-ip': '10.0.2.15',
              'disable': ['rke2-traefik', 'rke2-ingress-nginx']}
    result['configuration'] = config
    config_code = """import os,pathlib
p=pathlib.Path('/etc/rancher/rke2');p.mkdir(parents=True,mode=0o700,exist_ok=True)
fd=os.open(p/'config.yaml',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f:f.write(%r)
""" % (json.dumps(config) + '\n')
    probe_code = """import json,pathlib,subprocess
k=['/var/lib/rancher/rke2/bin/kubectl','--kubeconfig=/etc/rancher/rke2/rke2.yaml','--request-timeout=5s']
try:
 def get(args):return subprocess.check_output(k+args,stderr=subprocess.DEVNULL,timeout=10).decode()
 ready=get(['get','--raw=/readyz']).strip()
 version=json.loads(get(['version','-o','json']))['serverVersion']['gitVersion']
 raw=json.loads(get(['get','nodes','-o','json']))['items']
 nodes=[{'name':n['metadata']['name'],'kubelet_version':n['status']['nodeInfo']['kubeletVersion'],
         'ready':any(c['type']=='Ready' and c['status']=='True' for c in n['status']['conditions'])} for n in raw]
 print(json.dumps({'readyz':ready,'server_version':version,'nodes':nodes}))
except (OSError,subprocess.SubprocessError,KeyError,ValueError):
 print(json.dumps({'readyz':'not-ready','nodes':[]}))
"""
    consecutive = 0
    try:
        command([*ssh, shlex.join(['sudo', 'python3', '-c', config_code])], timeout=30)
        command([*ssh, shlex.join(['sudo', 'env', 'INSTALL_RKE2_METHOD=tar',
                 'INSTALL_RKE2_ARTIFACT_PATH=' + destination, 'INSTALL_RKE2_VERSION=' + pins['rke2_version'],
                 'sh', destination + '/install.sh'])], timeout=min(180, RKE2_TIMEOUT - (time.monotonic() - start)))
        command([*ssh, 'sudo systemctl enable rke2-server.service && sudo systemctl start --no-block rke2-server.service'], timeout=30)
        save('wait-rke2-api-and-node')
        while time.monotonic() - start < RKE2_TIMEOUT:
            remaining = RKE2_TIMEOUT - (time.monotonic() - start)
            response = command([*ssh, shlex.join(['sudo', 'python3', '-c', probe_code])],
                               timeout=min(45, remaining), check=False, timeout_ok=True)
            state = classify_ssh_probe(response.returncode, response.stderr)
            value = json.loads(response.stdout) if state == 'ready' else {'readyz': 'not-ready', 'nodes': []}
            value['elapsed_seconds'] = round(time.monotonic() - start, 3)
            observations.append(value)
            consecutive = consecutive + 1 if rke2_observation(value, pins['rke2_version']) else 0
            print(json.dumps({'event': 'rke2-progress', 'elapsed_seconds': value['elapsed_seconds'],
                              'ready_observations': consecutive}), flush=True)
            if consecutive == 3 and time.monotonic() - start <= RKE2_TIMEOUT:
                break
            time.sleep(min(15, max(0, RKE2_TIMEOUT - (time.monotonic() - start))))
        result['elapsed_seconds'] = round(time.monotonic() - start, 3)
        result['gate_finished_at'] = now()
        result['consecutive_ready_observations'] = consecutive
        if consecutive != 3 or time.monotonic() - start > RKE2_TIMEOUT:
            raise ProbeError('RKE2 did not pass the fixed twenty-minute API and node readiness gate')
        # This is a live authenticated API read. No application is deployed.
        smoke = command([*ssh, 'sudo /var/lib/rancher/rke2/bin/kubectl --kubeconfig=/etc/rancher/rke2/rke2.yaml '
                         '--request-timeout=10s get --raw=/api'], timeout=30)
        discovery = json.loads(smoke.stdout)
        if discovery.get('kind') != 'APIVersions' or 'v1' not in discovery.get('versions', []):
            raise ProbeError('Authenticated Kubernetes discovery smoke returned unexpected data')
        result['api_read_smoke'] = {'path': '/api', 'kind': discovery['kind'], 'versions': discovery['versions']}
        operator = private_dir(lab / 'operator')
        for name, guest_path in [('kubeconfig.yaml', '/etc/rancher/rke2/rke2.yaml'),
                                  ('server-token', '/var/lib/rancher/rke2/server/token')]:
            content = command([*ssh, shlex.join(['sudo', 'cat', guest_path])], timeout=30).stdout
            if not 0 < len(content) < 1024 * 1024:
                raise ProbeError('Issued guest credential has an unexpected size')
            write_private(operator / name, content)
        match = re.search(rb'^\s+certificate-authority-data:\s*([A-Za-z0-9+/=]+)\s*$',
                          (operator / 'kubeconfig.yaml').read_bytes(), re.MULTILINE)
        if not match:
            raise ProbeError('Guest kubeconfig did not contain an API CA')
        ca = base64.b64decode(match.group(1), validate=True)
        if not ca.startswith(b'-----BEGIN CERTIFICATE-----'):
            raise ProbeError('Guest API CA was not a PEM certificate')
        write_private(operator / 'api-ca.crt', ca)
        result['credentials'] = {'status': 'issued_by_observed_guest', 'directory': str(operator),
                                  'files': ['kubeconfig.yaml', 'server-token', 'api-ca.crt'],
                                  'mode': '0600', 'directory_mode': '0700', 'exported': False,
                                  'api_ca_sha256': hashlib.sha256(ca).hexdigest()}
        result['status'] = 'api_and_node_ready'
    finally:
        result.setdefault('elapsed_seconds', round(time.monotonic() - start, 3))
        result.setdefault('gate_finished_at', now())
        # Diagnostics and service shutdown are private and outside the success
        # gate; they cannot manufacture a pass or extend its deadline.
        command([*ssh, 'sudo journalctl -u rke2-server -n 160 --no-pager'], timeout=45, check=False, timeout_ok=True)
        command([*ssh, 'sudo systemctl stop --no-block rke2-server.service'], timeout=30, check=False, timeout_ok=True)


def worker(dockerfile, rke2_pins=None):
    os.umask(0o077)
    if rke2_pins is not None:
        validate_rke2_pins(rke2_pins)
    cpus, guest_bytes, container_bytes, disk_gib = (8, 32 * 1024**3, 34 * 1024**3, 160) if rke2_pins else (4, GUEST_BYTES, CONTAINER_BYTES, 40)
    run_id = uuid4().hex
    family = 'devsecops-v02-20261026'
    environment = family + ('-rke2-' if rke2_pins else '-probe-') + run_id[:8]
    lab = private_dir(Path.home() / '.local/share/devsecops/labs' / environment)
    logs = private_dir(lab / 'private')
    keys = private_dir(lab / 'ssh')
    state = {'schema_version': 1, 'run_id': run_id, 'environment': environment,
             'created_at': now(), 'private_lab_path': str(lab), 'container_ids': [],
             'network_id': None, 'image_id': None, 'stage': 'created'}
    report = {'schema_version': 1, 'kind': 'tcg-rke2-readiness-screen' if rke2_pins else 'tcg-linux-boot-probe', 'environment': environment,
              'run_id': run_id, 'status': 'unknown', 'live_platform_qualification': 'not_performed',
              'guest': {'vcpus': cpus, 'memory_bytes': guest_bytes, 'disk_virtual_bytes': disk_gib * 1024**3,
                        'accelerator': 'tcg', 'architecture': 'x86_64'},
              'private_lab_path': str(lab), 'started_at': now(), 'cleanup': {}, 'gaps': []}
    log_number = 0
    ssh = None

    def save(stage=None):
        if stage:
            state['stage'] = stage
            print(json.dumps({'event': stage, 'time': now(), 'environment': environment}), flush=True)
        atomic_private(lab / 'state.json', state)

    def command(argv, *, timeout=60, check=True, env=None, timeout_ok=False):
        nonlocal log_number
        log_number += 1
        path = logs / f'command-{log_number:03d}.log'
        # Arguments may contain only public identifiers/paths. Output can include
        # cloud-init information, so it remains in the private directory.
        try:
            result = subprocess.run(argv, capture_output=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired as error:
            write_private(path, (error.stdout or b'') + b'\nCommand exceeded its fixed timeout.\n' + (error.stderr or b''))
            if timeout_ok:
                return subprocess.CompletedProcess(argv, 124, error.stdout or b'', error.stderr or b'')
            raise ProbeError('A bounded operation timed out') from None
        write_private(path, result.stdout + b'\n--- stderr ---\n' + result.stderr)
        if check and result.returncode:
            raise ProbeError('Operation failed in stage ' + state['stage'] + '; private command log retained')
        return result

    def inspect_resource(kind, identifier):
        result = command(['docker', kind, 'inspect', identifier])
        return json.loads(result.stdout)[0]

    def create_container(name, args):
        state['pending_container_name'] = name
        save()
        result = command(['docker', 'create', '--name', name, '--label', f'{LABEL}={run_id}', *args])
        container_id = result.stdout.decode().strip()
        if not re.fullmatch(r'[0-9a-f]{64}', container_id):
            raise ProbeError('Docker did not return an exact container identity')
        state['container_ids'].append(container_id)
        state.pop('pending_container_name', None)
        save()
        return container_id

    def download(url, path, *, maximum, hosts=('cloud-images.ubuntu.com',)):
        digest = hashlib.sha256()
        total = 0
        started = time.monotonic()
        with download_opener(hosts).open(url, timeout=60) as response:
            parsed = urlsplit(response.url)
            if parsed.scheme != 'https' or parsed.hostname not in hosts or parsed.username or parsed.password:
                raise ProbeError('Unexpected image acquisition origin')
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as target:
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > maximum or time.monotonic() - started > 600:
                        raise ProbeError('Acquisition exceeds its size or time limit')
                    target.write(chunk)
                    digest.update(chunk)
        return digest.hexdigest()

    try:
        save('provenance')
        if sys.platform != 'linux' or os.uname().machine != 'x86_64':
            raise ProbeError('Probe requires the selected owned Linux x86_64 host')
        memory = {row.split(':')[0]: int(row.split()[1]) * 1024
                  for row in Path('/proc/meminfo').read_text().splitlines()}
        minimum_memory, minimum_disk = (44, 310) if rke2_pins else (20, 60)
        if memory['MemAvailable'] < minimum_memory * 1024**3 or shutil.disk_usage(lab).free < minimum_disk * 1024**3:
            raise ProbeError('Insufficient spare host resources for the bounded probe')
        report['host_before'] = {'available_memory_bytes': memory['MemAvailable'],
                                 'free_disk_bytes': shutil.disk_usage(lab).free, 'kvm_present': Path('/dev/kvm').exists()}
        # Docker validates the fetched image content; subsequent build uses only
        # the resolved official repository digest, never the mutable tag.
        command(['docker', 'pull', '--platform', 'linux/amd64', 'docker.io/library/ubuntu:24.04'], timeout=180)
        base = inspect_resource('image', 'docker.io/library/ubuntu:24.04')
        base_ref = next((v for v in base['RepoDigests'] if re.fullmatch(r'(?:docker.io/library/)?ubuntu@sha256:[0-9a-f]{64}', v)), None)
        if base_ref is None:
            raise ProbeError('Official Ubuntu image did not resolve to a pinned digest')
        report['toolchain'] = {'base_ref': base_ref, 'base_image_id': base['Id'],
                               'dockerfile_sha256': hashlib.sha256(dockerfile).hexdigest()}
        source = IMAGE_URL.rsplit('/', 1)[0]
        download(source + '/SHA256SUMS', lab / 'SHA256SUMS', maximum=1_000_000)
        download(source + '/SHA256SUMS.gpg', lab / 'SHA256SUMS.gpg', maximum=1_000_000)
        verified = command(['gpgv', '--status-fd', '1', '--keyring', '/usr/share/keyrings/ubuntu-cloudimage-keyring.gpg',
                            str(lab / 'SHA256SUMS.gpg'), str(lab / 'SHA256SUMS')])
        if not any(line.startswith('[GNUPG:] VALIDSIG ' + CLOUD_SIGNER + ' ') for line in verified.stdout.decode().splitlines()):
            raise ProbeError('Cloud image checksum signature does not match the pinned Ubuntu signer')
        image_name = IMAGE_URL.rsplit('/', 1)[1]
        matches = [line.split()[0] for line in (lab / 'SHA256SUMS').read_text().splitlines()
                   if len(line.split()) == 2 and line.split()[1].lstrip('*') == image_name]
        if matches != [IMAGE_SHA256]:
            raise ProbeError('Signed cloud image digest differs from the reviewed pin')
        save('acquire-verified-image')
        actual = download(IMAGE_URL, lab / 'ubuntu.img', maximum=4 * 1024**3)
        if actual != IMAGE_SHA256:
            raise ProbeError('Cloud image content digest differs from its signed checksum')
        report['cloud_image'] = {'url': IMAGE_URL, 'sha256': actual, 'signature_verified': True,
                                  'signer_fingerprint': CLOUD_SIGNER, 'checksum_sha256': sha(lab / 'SHA256SUMS')}
        if rke2_pins:
            save('acquire-pinned-rke2-artifacts')
            artifacts = private_dir(lab / 'rke2-artifacts')
            atomic_private(lab / 'rke2-release.json', rke2_pins)
            for item in rke2_pins['assets']:
                digest = download(item['url'], artifacts / item['name'], maximum=item['bytes'],
                                  hosts=('github.com', 'release-assets.githubusercontent.com', 'raw.githubusercontent.com'))
                if digest != item['sha256'] or (artifacts / item['name']).stat().st_size != item['bytes']:
                    raise ProbeError('Downloaded RKE2 artifact differs from its reviewed digest or size')
                print(json.dumps({'event': 'rke2-artifact-verified', 'name': item['name'], 'bytes': item['bytes']}), flush=True)
            verify_release_checksums(rke2_pins, (artifacts / 'sha256sum-amd64.txt').read_text())
            report['rke2_provenance'] = {'pins': rke2_pins, 'pins_sha256': sha(lab / 'rke2-release.json'),
                                        'all_asset_hashes_verified': True, 'release_checksums_verified': True,
                                        'cryptographic_release_signature_verified': False,
                                        'trust_basis': 'official HTTPS release metadata digests and checksum file'}
        save('build-pinned-tooling')
        context = private_dir(lab / 'build')
        write_private(context / 'Dockerfile', dockerfile)
        # Rootless apt uses only a private config, status, lists and cache. It
        # verifies official signed repository metadata and package hashes; no
        # host package installation or apt hook is run. Docker builds offline.
        apt = private_dir(lab / 'apt')
        for name in ('empty', 'state', 'lists', 'cache', 'archives', 'log'):
            private_dir(apt / name)
        write_private(apt / 'status', '')
        write_private(apt / 'config', apt_config(apt))
        write_private(apt / 'sources.list', '\n'.join(
            'deb [signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] ' + url + ' ' + suite + ' main universe'
            for url, suite in [('https://archive.ubuntu.com/ubuntu', 'noble'),
                               ('https://archive.ubuntu.com/ubuntu', 'noble-updates'),
                               ('https://security.ubuntu.com/ubuntu', 'noble-security')]) + '\n')
        apt_env = {**os.environ, 'APT_CONFIG': str(apt / 'config')}
        command(['apt-get', 'update', '--error-on=any'], timeout=180, env=apt_env)
        command(['apt-get', '--download-only', '--reinstall', 'install',
                 *[name + '=' + version for name, version in PACKAGES.items()]], timeout=300, env=apt_env)
        debs = private_dir(context / 'debs')
        packages = []
        for path in sorted((apt / 'archives').glob('*.deb')):
            shutil.copyfile(path, debs / path.name)
            packages.append({'name': path.name, 'sha256': sha(path), 'bytes': path.stat().st_size})
        if not packages:
            raise ProbeError('Signed package acquisition returned no package files')
        atomic_private(lab / 'package-artifacts.json', packages)
        report['toolchain']['package_artifacts_sha256'] = sha(lab / 'package-artifacts.json')
        report['toolchain']['package_count'] = len(packages)
        report['toolchain']['apt_inrelease_sha256'] = {path.name: sha(path) for path in (apt / 'lists').glob('*InRelease')}
        report['toolchain']['ubuntu_archive_keyring_sha256'] = sha('/usr/share/keyrings/ubuntu-archive-keyring.gpg')
        report['toolchain']['build_network'] = 'none'
        command(['docker', 'build', '--network=none', '--pull=false', '--build-arg', f'BASE_IMAGE={base_ref}',
                 '--label', f'{LABEL}={run_id}', '--iidfile', str(lab / 'image-id'), str(context)], timeout=600)
        image_id = (lab / 'image-id').read_text().strip()
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', image_id):
            raise ProbeError('Build did not produce an immutable tool image ID')
        state['image_id'] = image_id
        report['toolchain']['image_id'] = image_id
        save('prepare-private-guest')
        for name in ('operator', 'host'):
            command(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', environment + '-' + name,
                     '-f', str(keys / name)])
        for path in keys.iterdir():
            path.chmod(0o600)
        write_private(lab / 'user-data', cloud_config((keys / 'operator.pub').read_text(),
                                                     (keys / 'host').read_text(), (keys / 'host.pub').read_text()))
        write_private(lab / 'meta-data', json.dumps({'instance-id': environment, 'local-hostname': 'devsecops-probe'}))
        common = ['--user', f'{os.getuid()}:{os.getgid()}', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                  '--read-only', '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=64m', '--pids-limit', '256',
                  '--ulimit', 'core=0', '--mount', f'type=bind,src={lab},dst=/lab']
        helper = create_container(environment + '-seed', [*common, '--network', 'none', '--cpus', '1',
            '--memory', '512m', '--memory-swap', '512m', image_id, 'sh', '-c',
            'umask 077; cloud-localds /lab/seed.iso /lab/user-data /lab/meta-data && '
            f'qemu-img create -f qcow2 -F qcow2 -b /lab/ubuntu.img /lab/probe.qcow2 {disk_gib}G && '
            'cp /tool-packages.tsv /lab/tool-packages.tsv && qemu-system-x86_64 --version > /lab/qemu-version.txt'])
        command(['docker', 'start', '-a', helper], timeout=60)
        report['toolchain']['package_inventory_sha256'] = sha(lab / 'tool-packages.tsv')
        report['toolchain']['qemu_version'] = (lab / 'qemu-version.txt').read_text().splitlines()[0]
        installed = dict(line.split('\t') for line in (lab / 'tool-packages.tsv').read_text().splitlines())
        if any(installed.get(name) != version for name, version in PACKAGES.items()):
            raise ProbeError('Installed tooling differs from exact package version pins')
        report['toolchain']['requested_packages'] = PACKAGES
        # Recheck immediately before creating the larger guest's runtime.
        memory_available = int(next(row.split()[1] for row in Path('/proc/meminfo').read_text().splitlines()
                                    if row.startswith('MemAvailable:'))) * 1024
        report['host_before_runtime'] = {'available_memory_bytes': memory_available, 'free_disk_bytes': shutil.disk_usage(lab).free}
        if memory_available < minimum_memory * 1024**3 or shutil.disk_usage(lab).free < minimum_disk * 1024**3:
            raise ProbeError('Host headroom changed before runtime creation')
        # An internal network provides the one owned container with no Internet
        # route; no existing network or bridge is changed by this driver.
        network_name = environment + '-net'
        state['pending_network_name'] = network_name
        save()
        network = command(['docker', 'network', 'create', '--internal', '--label', f'{LABEL}={run_id}', network_name]).stdout.decode().strip()
        state['network_id'] = require_owner(inspect_resource('network', network), run_id, network=True)
        state.pop('pending_network_name', None)
        save()
        qemu = ['qemu-system-x86_64', '-machine', 'q35,dump-guest-core=off', '-accel', 'tcg,thread=multi',
                '-cpu', 'max', '-smp', str(cpus), '-m', str(guest_bytes // 1024**2), '-display', 'none', '-monitor', 'none',
                '-serial', 'file:/lab/private/serial.log', '-qmp', 'unix:/lab/private/qmp.sock,server=on,wait=off',
                '-drive', 'file=/lab/probe.qcow2,if=virtio,format=qcow2',
                '-drive', 'file=/lab/seed.iso,format=raw,media=cdrom,readonly=on',
                '-netdev', 'user,id=net0,hostfwd=tcp:0.0.0.0:2222-:22', '-device', 'virtio-net-pci,netdev=net0,romfile=']
        container = create_container(environment + '-vm', [*common, '--network', network_name, '--cpus', str(cpus),
                        '--memory', str(container_bytes), '--memory-swap', str(container_bytes),
                        '--publish', '127.0.0.1::2222', image_id, *qemu])
        validate_runtime(inspect_resource('container', container), run_id, lab, network_name, container_bytes=container_bytes, cpus=cpus)
        save('booting')
        boot_start = time.monotonic()
        command(['docker', 'start', container])
        info = inspect_resource('container', container)
        host, port, transport = ssh_endpoint(info, inspect_resource('network', network), run_id)
        write_private(keys / 'known_hosts', f'[{host}]:{port} ' + (keys / 'host.pub').read_text())
        state['ssh'] = {'host': host, 'port': port, 'user': 'demo', 'transport': transport,
                        'identity_file': str(keys / 'operator'), 'known_hosts': str(keys / 'known_hosts')}
        save()
        ssh = ['ssh', '-F', '/dev/null', '-o', 'BatchMode=yes', '-o', 'ForwardAgent=no', '-o', 'PermitLocalCommand=no',
               '-o', 'IdentitiesOnly=yes', '-o', 'IdentityAgent=none', '-o', 'GlobalKnownHostsFile=/dev/null',
               '-o', 'StrictHostKeyChecking=yes', '-o', f'UserKnownHostsFile={keys / "known_hosts"}',
               '-o', 'ConnectTimeout=5', '-i', str(keys / 'operator'), '-p', str(port), 'demo@' + host]
        ready = False
        attempts = 0
        while time.monotonic() - boot_start < BOOT_TIMEOUT:
            attempts += 1
            remaining = BOOT_TIMEOUT - (time.monotonic() - boot_start)
            probe = command([*ssh, 'test -f /run/devsecops-probe-ready'],
                            timeout=min(30, remaining), check=False, timeout_ok=True)
            outcome = classify_ssh_probe(probe.returncode, probe.stderr)
            if outcome == 'ready' and time.monotonic() - boot_start <= BOOT_TIMEOUT:
                ready = True
                break
            if attempts % 6 == 0:
                print(json.dumps({'event': 'boot-progress', 'elapsed_seconds': round(time.monotonic() - boot_start, 1)}), flush=True)
            time.sleep(min(3, max(0, BOOT_TIMEOUT - (time.monotonic() - boot_start))))
        report['boot'] = {'authenticated_ssh_and_cloud_init_marker': ready,
                          'elapsed_seconds': round(time.monotonic() - boot_start, 3),
                          'limit_seconds': BOOT_TIMEOUT, 'attempts': attempts, 'host_key_pinned': True}
        if not ready:
            raise ProbeError('Guest did not pass the fixed ten-minute authenticated boot gate')
        save('measure-guest')
        measure_started = time.time()
        guest_code = """import hashlib,json,os,pathlib,time
start=time.monotonic()
payload=b'x'*(8*1024*1024)
for _ in range(8): hashlib.sha256(payload).digest()
print(json.dumps({'kernel':os.uname().release,'machine':os.uname().machine,'cpu_count':os.cpu_count(),
'utc_epoch':time.time(),
'mem_total_kib':int(next(x.split()[1] for x in pathlib.Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemTotal:'))),
'uptime_seconds':float(pathlib.Path('/proc/uptime').read_text().split()[0]),
'sha256_64mib_seconds':round(time.monotonic()-start,4)}))"""
        measurement = command([*ssh, shlex.join(['python3', '-c', guest_code])], timeout=60)
        report['measurement'] = json.loads(measurement.stdout)
        measure_finished = time.time()
        report['measurement']['host_measurement_window'] = [measure_started, measure_finished]
        report['measurement']['clock_within_window_tolerance_seconds'] = 10
        report['measurement']['clock_coherent'] = measure_started - 10 <= report['measurement']['utc_epoch'] <= measure_finished + 10
        if not report['measurement']['clock_coherent']:
            raise ProbeError('Guest clock is outside the observed host measurement window')
        runtime = inspect_resource('container', container)
        validate_runtime(runtime, run_id, lab, network_name, container_bytes=container_bytes, cpus=cpus)
        report['isolation'] = {'runtime_config_verified': True, 'privileged': False, 'cap_drop': ['ALL'],
                               'no_new_privileges': True, 'read_only_rootfs': True,
                               'configured_host_publish': '127.0.0.1',
                               'effective_host_publish': '127.0.0.1' if transport == 'loopback-publish' else None,
                               'ssh_transport': transport,
                               'network_internal': inspect_resource('network', network)['Internal'],
                               'mounted_paths': [str(lab)], 'kvm_used': False, 'guest_key_exported': False}
        if report['isolation']['network_internal'] is not True:
            raise ProbeError('Observed network is not internal')
        report['cloud_image']['unchanged_after_boot'] = sha(lab / 'ubuntu.img') == IMAGE_SHA256
        if not report['cloud_image']['unchanged_after_boot']:
            raise ProbeError('Verified base image was changed during the probe')
        report['status'] = 'boot_probe_pass'
        if rke2_pins:
            screen_rke2(command, ssh, lab, report, rke2_pins, save)
            report['status'] = 'rke2_readiness_pass'
            report['live_platform_qualification'] = 'rke2_api_and_node_only'
    except Exception as error:
        report['status'] = 'rke2_readiness_incomplete' if rke2_pins else 'boot_probe_incomplete'
        report['gaps'].append({'stage': state['stage'], 'error_type': type(error).__name__,
                               'message': str(error) if type(error) is ProbeError else 'Probe could not complete; private logs retained'})
    finally:
        if ssh and report.get('boot', {}).get('authenticated_ssh_and_cloud_init_marker'):
            save('shutdown-guest')
            command([*ssh, 'sudo shutdown -h now'], timeout=15, check=False, timeout_ok=True)
            # Allow a failed readiness attempt to flush its disk too. Exact
            # owned-resource cleanup still follows when shutdown times out.
            waited = command(['docker', 'wait', state['container_ids'][-1]], timeout=60, check=False, timeout_ok=True)
            report['guest_shutdown_observed'] = waited.returncode == 0 and waited.stdout.strip() == b'0'
        save('cleanup')
        remaining = []
        # The exact recorded name is only used to reconcile a create whose ID
        # was not recorded; its immutable run label must still match.
        if state.get('pending_container_name'):
            found = command(['docker', 'container', 'inspect', state['pending_container_name']], check=False)
            if found.returncode == 0:
                try:
                    identifier = require_owner(json.loads(found.stdout)[0], run_id)
                    if identifier not in state['container_ids']:
                        state['container_ids'].append(identifier)
                except (ValueError, ProbeError):
                    remaining.append('unreconciled-container-create')
        for identifier in reversed(state['container_ids']):
            try:
                existing = inspect_resource('container', identifier)
                require_owner(existing, run_id)
                command(['docker', 'stop', '--time', '20', identifier], timeout=30, check=False)
                command(['docker', 'container', 'rm', identifier])
                if command(['docker', 'container', 'inspect', identifier], check=False).returncode == 0:
                    remaining.append(identifier)
            except (ProbeError, ValueError):
                remaining.append(identifier)
        if state.get('pending_network_name') and not state['network_id']:
            found = command(['docker', 'network', 'inspect', state['pending_network_name']], check=False)
            if found.returncode == 0:
                try:
                    state['network_id'] = require_owner(json.loads(found.stdout)[0], run_id, network=True)
                except (ProbeError, ValueError):
                    remaining.append('unreconciled-network-create')
        if state['network_id']:
            try:
                net = inspect_resource('network', state['network_id'])
                require_owner(net, run_id, network=True)
                if net.get('Containers'):
                    raise ProbeError('Owned network still has attachments')
                command(['docker', 'network', 'rm', state['network_id']])
                if command(['docker', 'network', 'inspect', state['network_id']], check=False).returncode == 0:
                    remaining.append(state['network_id'])
            except (ProbeError, ValueError):
                remaining.append(state['network_id'])
        report['cleanup'] = {'containers_and_network_removed': not remaining, 'remaining_runtime_resources': remaining,
                              'preserved_private_lab_path': str(lab), 'preserved_tool_image_id': state['image_id'],
                              'private_logs_exported': False, 'ssh_keys_exported': False,
                              'preserved_files': [name for name in ('ubuntu.img', 'probe.qcow2', 'seed.iso',
                                  'ssh/operator', 'ssh/host', 'user-data', 'meta-data', 'private', 'state.json',
                                  'SHA256SUMS', 'SHA256SUMS.gpg', 'package-artifacts.json', 'tool-packages.tsv',
                                  'rke2-release.json', 'rke2-artifacts', 'operator')
                                  if (lab / name).exists()]}
        if remaining:
            report['status'] = 'rke2_readiness_incomplete' if rke2_pins else 'boot_probe_incomplete'
        report['finished_at'] = now()
        state['stage'] = 'finished'
        state['report_status'] = report['status']
        state['remaining_runtime_resources'] = remaining
        save()
        atomic_private(lab / 'receipt.json', report)
        print(json.dumps({'event': 'result', 'report': report}), flush=True)
    return 0 if report['status'] in ('boot_probe_pass', 'rke2_readiness_pass') else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', help='Existing SSH alias for the owned Linux devbox')
    parser.add_argument('--receipt', type=Path, help='New local sanitized receipt path')
    parser.add_argument('--rke2-screen', action='store_true', help='Run the pinned offline RKE2 API/node screen in a new 8-vCPU/32-GiB guest')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--dockerfile-base64', help=argparse.SUPPRESS)
    parser.add_argument('--rke2-pins-base64', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        pins = json.loads(base64.b64decode(args.rke2_pins_base64, validate=True)) if args.rke2_pins_base64 else None
        return worker(base64.b64decode(args.dockerfile_base64, validate=True), pins)
    if not args.host or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', args.host) or args.receipt is None:
        parser.error('Supply an existing --host SSH alias and a new --receipt path')
    if args.receipt.exists():
        parser.error('The receipt path already exists; preserve the previous attempt')
    dockerfile = (ROOT / 'demo/software-factory/provision/tcg/Dockerfile').read_bytes()
    remote_args = ['python3', '-', '--worker', '--dockerfile-base64', base64.b64encode(dockerfile).decode()]
    if args.rke2_screen:
        pin_bytes = (ROOT / 'demo/software-factory/provision/tcg/rke2-release.json').read_bytes()
        validate_rke2_pins(json.loads(pin_bytes))
        remote_args += ['--rke2-pins-base64', base64.b64encode(pin_bytes).decode()]
    remote = shlex.join(remote_args)
    proc = subprocess.Popen(['ssh', '-o', 'BatchMode=yes', '-o', 'ForwardAgent=no', '-o', 'PermitLocalCommand=no',
                             args.host, remote], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    script = Path(__file__).read_text()
    proc.stdin.write(script)
    proc.stdin.close()
    report = None
    for line in proc.stdout:
        value = json.loads(line)
        if value.get('event') == 'result':
            report = value['report']
        else:
            print(json.dumps(value), flush=True)
    code = proc.wait()
    if report is None:
        raise ProbeError('Remote probe ended without a receipt; reconcile its private state before retrying')
    report['driver_sha256'] = hashlib.sha256(script.encode()).hexdigest()
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    write_private(args.receipt, json.dumps(report, indent=2) + '\n')
    print(json.dumps({'receipt': str(args.receipt.resolve()), 'status': report['status'],
                      'private_lab_path': report['private_lab_path'], 'cleanup': report['cleanup']}, indent=2))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
