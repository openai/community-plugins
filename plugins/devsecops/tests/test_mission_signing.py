"""Real local Cosign operations on controlled assessment records, never P1 trust."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import pickle
import shutil
import time

import pytest

from devsecops.adapters import local_signing, signatures
from devsecops.factory import mission_security as security
from devsecops.factory import mission_signing as signing
from devsecops.runtime.context import verify_seal
from test_artifacts import tools as scanner_tools
from test_mission_security import case as security_case
from test_signatures import lab

__all__ = ['lab']


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return json.dumps(value, sort_keys=True).encode()


@pytest.fixture(scope='module')
def scanned(tmp_path_factory):
    path = tmp_path_factory.mktemp('controlled-signing-scan').resolve()
    source = security_case.__wrapped__(path, scanner_tools.__wrapped__())
    report, cap = security.assess_staged_mission(**source['args'])
    assert cap is None and report['checks']['scanner'] == 'pass'
    root = source['args']['output']
    observation = json.loads((root / 'scanner-receipt.json').read_bytes())['observations']
    binding = {**deepcopy(source['binding']), 'archive_path': str(root / 'image.oci.tar'),
        'policy_sha256': source['args']['expected_policy_sha256'], 'policy_id': source['policy']['id'],
        'policy_version': source['policy']['version'], 'database': report['database'], 'tools': observation['tools'],
        'sbom': {'path': str(root / 'sbom.spdx.json'), **report['artifacts']['sbom.spdx.json']},
        'vulnerabilities': {'path': str(root / 'grype.json'), **report['artifacts']['grype.json']},
        'scanner_receipt_sha256': report['artifacts']['scanner-receipt.json']['sha256'], 'applied_exceptions': []}
    return binding


@pytest.fixture
def case(scanned, lab, tmp_path, monkeypatch):
    source, cosign = lab
    inputs = tmp_path / 'inputs'
    inputs.mkdir(mode=0o700)
    binding = deepcopy(scanned)
    scan_root = Path(binding['archive_path']).parent
    for filename in ('image.oci.tar', 'sbom.spdx.json', 'grype.json', 'scanner-receipt.json'):
        shutil.copyfile(scan_root / filename, inputs / filename)
        (inputs / filename).chmod(0o400)
    binding['archive_path'] = str(inputs / 'image.oci.tar')
    for name, filename in [('sbom', 'sbom.spdx.json'), ('vulnerabilities', 'grype.json')]:
        binding[name]['path'] = str(inputs / filename)
    for filename in ('lab.key', 'lab.pub', 'wrong.key', 'wrong.pub'):
        shutil.copyfile(source / filename, inputs / filename)
        (inputs / filename).chmod(0o600)
    now = datetime.now(timezone.utc)
    binding['expires_at'] = (now+timedelta(minutes=10)).isoformat()
    authority = object.__new__(security.AssessedMission)
    security._ASSESSED[authority] = {'pid': os.getpid(), 'expires_monotonic': time.monotonic()+600, 'binding': binding}
    selected = {'schema_version': 1, 'kind': 'mission-application-signing-trust', 'id': 'controlled-lab-signer',
        'version': '1.0', 'issuer': 'controlled-fixture-issuer', 'authority': 'controlled-test',
        'public_key_sha256': sha((inputs / 'lab.pub').read_bytes()), 'origin': binding['origin'],
        'repository': binding['repository'], 'status': 'active', 'valid_from': (now-timedelta(hours=1)).isoformat(),
        'expires_at': (now+timedelta(hours=1)).isoformat(), 'trust_reference': 'controlled-component-test-only'}
    trust_path = inputs / 'trust.json'
    trust_path.write_bytes(encode(selected))
    config = {'schema_version': 1, 'kind': 'mission-local-signing-configuration',
        'private_key_path': str(inputs / 'lab.key'), 'public_key_path': str(inputs / 'lab.pub'),
        'password_env': 'MISSION_SIGNING_PASSWORD', 'trust_policy_path': str(trust_path),
        'trust_policy_sha256': sha(trust_path.read_bytes()), 'cosign_path': str(cosign)}
    config_path = inputs / 'signing.json'
    config_path.write_bytes(encode(config))
    monkeypatch.setenv('MISSION_SIGNING_PASSWORD', '')
    args = {'assessed': authority, **{key: binding[key] for key in ('run_scope', 'source_commit', 'architecture',
        'origin', 'repository', 'policy_sha256')}, 'config': config_path, 'expected_config_sha256': sha(config_path.read_bytes()),
        'output': tmp_path / 'signed'}
    return {'args': args, 'binding': binding, 'config': config, 'trust': selected, 'inputs': inputs}


def update(case, *, config=False, trust=False):
    if trust:
        path = Path(case['config']['trust_policy_path'])
        path.write_bytes(encode(case['trust']))
        case['config']['trust_policy_sha256'] = sha(path.read_bytes())
    if trust or config:
        case['args']['config'].write_bytes(encode(case['config']))
        case['args']['expected_config_sha256'] = sha(case['args']['config'].read_bytes())


def test_actual_signing_and_independent_verification_preserve_subject_and_review(case, monkeypatch):
    for name in ('COSIGN_PASSWORD', 'AWS_SECRET_ACCESS_KEY', 'HTTP_PROXY', 'SIGSTORE_ID_TOKEN'):
        monkeypatch.setenv(name, 'UNSELECTED-SECRET-CANARY')
    actual_run = local_signing.run
    environments = []
    def isolated(argv, **kwargs):
        environments.append(kwargs['env'])
        assert 'UNSELECTED-SECRET-CANARY' not in kwargs['env'].values()
        assert not set(kwargs['env']) & {'AWS_SECRET_ACCESS_KEY', 'HTTP_PROXY', 'SIGSTORE_ID_TOKEN'}
        return actual_run(argv, **kwargs)
    monkeypatch.setattr(local_signing, 'run', isolated)
    monkeypatch.setattr(signatures, 'run', isolated)
    result, cap = signing.sign_assessed_mission(**case['args'])
    assert len(environments) == 5
    assert cap is None and result['decision'] == 'unknown' and result['factory_decision'] == 'unknown'
    assert result['checks'] == {'assessment': 'pass', 'signer_trust': 'unknown', 'artifact': 'pass', 'sign': 'pass', 'verify': 'pass'}
    assert result['gaps'] == ['controlled-evidence-cannot-issue-local-signing-authority']
    assert result['downstream'] == dict.fromkeys(('publish', 'registry_readback', 'promote'), 'not-started')
    root = case['args']['output']
    payload = json.loads((root / 'payload.json').read_bytes())
    assert payload['critical']['image']['docker-manifest-digest'] == case['binding']['manifest_digest']
    evidence = payload['optional']['devsecops']['assessment']
    for key in ('run_scope', 'source_commit', 'policy_sha256', 'context_sha256', 'base', 'database', 'tools'):
        assert evidence[key] == case['binding'][key]
    assert evidence['sbom']['sha256'] == case['binding']['sbom']['sha256']
    assert str(case['inputs']) not in json.dumps(payload)
    assert payload['optional']['devsecops']['issuer']['publisher_authority'] == 'not-claimed'
    verifier = json.loads((root / 'signature-verification.json').read_bytes())
    assert verifier['observations']['verification']['decision'] == 'pass'
    assert not any('key' in name and name != 'public-key.pem' for name in result['artifacts'])
    assert not any('stderr_sha256' in item for item in result['receipts'])
    verify_seal(result, 'signing_sha256')


@pytest.mark.parametrize('mutation', ['dict', 'scope', 'policy', 'expired', 'foreign'])
def test_assessment_guard_rejects_before_key_read_or_output(case, monkeypatch, mutation):
    args = case['args']
    if mutation == 'dict':
        args['assessed'] = case['binding']
    elif mutation == 'scope':
        args['run_scope'] = {'run_id': 'other'}
    elif mutation == 'policy':
        args['policy_sha256'] = '0'*64
    elif mutation == 'expired':
        security._ASSESSED[args['assessed']]['expires_monotonic'] = 0
    else:
        security._ASSESSED[args['assessed']]['pid'] += 1
    monkeypatch.setattr(signing, 'sign_payload', lambda **kw: pytest.fail('must reject before signer'))
    with pytest.raises(ValueError):
        signing.sign_assessed_mission(**args)
    assert not args['output'].exists()


@pytest.mark.parametrize('mutation', ['config-pin', 'trust-pin', 'public-pin', 'revoked', 'expired', 'future', 'repository', 'publisher', 'archive', 'sbom', 'scan-receipt'])
def test_invalid_selected_inputs_prevent_signing(case, monkeypatch, mutation):
    if mutation == 'config-pin':
        case['args']['expected_config_sha256'] = '0'*64
    elif mutation == 'trust-pin':
        Path(case['config']['trust_policy_path']).write_bytes(b'changed')
    elif mutation == 'public-pin':
        Path(case['config']['public_key_path']).write_bytes(b'changed')
    elif mutation == 'revoked':
        case['trust']['status'] = 'revoked'
        update(case, trust=True)
    elif mutation in ('expired', 'future'):
        case['trust']['expires_at' if mutation == 'expired' else 'valid_from'] = (
            datetime.now(timezone.utc) + timedelta(hours=-1 if mutation == 'expired' else 1)).isoformat()
        update(case, trust=True)
    elif mutation in ('repository', 'publisher'):
        case['trust']['repository' if mutation == 'repository' else 'origin'] = 'other/image' if mutation == 'repository' else 'https://registry1.dso.mil'
        update(case, trust=True)
    else:
        filename = {'archive': 'image.oci.tar', 'sbom': 'sbom.spdx.json', 'scan-receipt': 'scanner-receipt.json'}[mutation]
        path = case['inputs'] / filename
        path.chmod(0o600)
        path.write_bytes(b'changed')
    monkeypatch.setattr(signing, 'sign_payload', lambda **kw: pytest.fail('must reject before signer'))
    result, cap = signing.sign_assessed_mission(**case['args'])
    assert result['decision'] == 'unknown' and cap is None and result['checks']['sign'] == 'not-started'


@pytest.mark.parametrize('mutation', ['wrong-key', 'public-mode', 'symlink', 'hardlink', 'password-missing', 'password-wrong', 'wrong-binary'])
def test_real_signer_rejects_key_or_tool_configuration(case, monkeypatch, mutation):
    path = Path(case['config']['private_key_path'])
    if mutation == 'wrong-key':
        shutil.copyfile(case['inputs'] / 'wrong.key', path)
    elif mutation == 'public-mode':
        path.chmod(0o644)
    elif mutation == 'symlink':
        path.unlink()
        path.symlink_to(case['inputs'] / 'wrong.key')
    elif mutation == 'hardlink':
        os.link(path, case['inputs'] / 'key-alias')
    elif mutation == 'password-missing':
        monkeypatch.delenv('MISSION_SIGNING_PASSWORD')
    elif mutation == 'password-wrong':
        monkeypatch.setenv('MISSION_SIGNING_PASSWORD', 'SECRET-PASSWORD-CANARY')
    else:
        fake = case['inputs'] / 'fake-cosign'
        fake.write_text('#!/bin/sh\nexit 0\n')
        fake.chmod(0o700)
        case['config']['cosign_path'] = str(fake)
        update(case, config=True)
    result, cap = signing.sign_assessed_mission(**case['args'])
    assert result['decision'] == 'unknown' and cap is None and result['checks']['verify'] == 'not-started'
    assert not (case['args']['output'] / 'bundle.json').exists()
    assert 'SECRET-PASSWORD-CANARY' not in json.dumps(result)


@pytest.mark.parametrize('mutation', ['signature', 'payload', 'expiry', 'late'])
def test_actual_verifier_or_admission_rejects_changes_after_sign(case, monkeypatch, mutation):
    original, actual_time = signing.sign_payload, signing.time.monotonic
    def changed(**kwargs):
        bundle, tool, receipts = original(**kwargs)
        if mutation == 'signature':
            document = json.loads(bundle)
            import base64
            raw = bytearray(base64.b64decode(document['messageSignature']['signature']))
            raw[-1] ^= 1
            document['messageSignature']['signature'] = base64.b64encode(raw).decode()
            bundle = encode(document)
        elif mutation == 'payload':
            path = case['args']['output'] / 'payload.json'
            path.write_bytes(path.read_bytes().replace(b'controlled-fixture-issuer', b'counterfeit-signer-issuer'))
        elif mutation == 'expiry':
            security._ASSESSED[case['args']['assessed']]['expires_monotonic'] = 0
        else:
            monkeypatch.setattr(signing.time, 'monotonic', lambda: actual_time()+400)
        return bundle, tool, receipts
    monkeypatch.setattr(signing, 'sign_payload', changed)
    result, cap = signing.sign_assessed_mission(**case['args'])
    assert result['decision'] == 'unknown' and cap is None
    assert result['checks']['verify'] != 'pass'


@pytest.mark.parametrize('mutation', ['expiry', 'bundle', 'route'])
def test_terminal_signing_receipt_withdrawn_on_expiry_or_changed_evidence(case, monkeypatch, mutation):
    original = signing._write
    root, moved = case['args']['output'], case['args']['output'].with_name('moved-signing')
    def changed(directory, name, value):
        result = original(directory, name, value)
        if name == 'mission-signing.json':
            if mutation == 'expiry':
                security._ASSESSED[case['args']['assessed']]['expires_monotonic'] = 0
            elif mutation == 'bundle':
                (root / 'bundle.json').write_bytes(b'{}')
            else:
                root.rename(moved)
                root.mkdir(mode=0o700)
        return result
    monkeypatch.setattr(signing, '_write', changed)
    with pytest.raises(ValueError):
        signing.sign_assessed_mission(**case['args'])
    assert not (root / 'mission-signing.json').exists() and not (moved / 'mission-signing.json').exists()


def test_generated_lab_credentials_private_retrievable_and_not_trusted(lab, tmp_path, monkeypatch):
    _, binary = lab
    output = tmp_path / 'private-credentials'
    result = local_signing.generate_lab_key(cosign=binary, output=output)
    assert output.stat().st_mode & 0o777 == 0o700
    assert result['trust_policy'] == 'not-selected' and result['publisher_authority'] == 'not-claimed'
    assert sha((output / 'application.pub').read_bytes()) == result['public_key_sha256']
    password = (output / 'password.txt').read_text()
    assert len(password) >= 32
    for path in output.iterdir():
        assert path.stat().st_mode & 0o777 == 0o600
    assert password not in json.dumps(result)
    assert sha((output / 'application.key').read_bytes()) not in json.dumps(result)
    monkeypatch.setenv('GENERATED_LAB_PASSWORD', password)
    bundle, _, _ = local_signing.sign_payload(payload=b'controlled-lab-key-proof', public_key=(output/'application.pub').read_bytes(),
        private_key=output/'application.key', password_env='GENERATED_LAB_PASSWORD', cosign=binary, deadline=time.monotonic()+30)
    assert json.loads(bundle)['messageSignature']['messageDigest']['algorithm'] == 'SHA2_256'
    with pytest.raises(ValueError, match='already exists'):
        local_signing.generate_lab_key(cosign=binary, output=output)


@pytest.mark.parametrize('mutation', ['password', 'route', 'deadline'])
def test_generated_credential_receipt_withdrawn_on_terminal_change(lab, tmp_path, monkeypatch, mutation):
    output, moved = tmp_path / 'credential-output', tmp_path / 'moved-credentials'
    actual_write, actual_time = local_signing._write, local_signing.time.monotonic
    def changed(directory, name, value):
        result = actual_write(directory, name, value)
        if name == 'retrieval.json':
            if mutation == 'password':
                (output / 'password.txt').write_bytes(b'changed')
            elif mutation == 'route':
                output.rename(moved)
                output.mkdir(mode=0o700)
            else:
                monkeypatch.setattr(local_signing.time, 'monotonic', lambda: actual_time()+200)
        return result
    monkeypatch.setattr(local_signing, '_write', changed)
    with pytest.raises(ValueError):
        local_signing.generate_lab_key(cosign=lab[1], output=output)
    assert not (output / 'retrieval.json').exists() and not (moved / 'retrieval.json').exists()


def test_constructor_and_serialization_closed(case):
    with pytest.raises(TypeError):
        signing.LocallySignedMission()
    selector = {key: case['args'][key] for key in ('run_scope', 'source_commit', 'architecture', 'origin', 'repository', 'policy_sha256')}
    with pytest.raises(ValueError):
        signing.require_locally_signed_mission(case['binding'], trust_policy_sha256='a'*64, **selector)
    cap = object.__new__(signing.LocallySignedMission)
    with pytest.raises(TypeError):
        pickle.dumps(cap)


@pytest.mark.skipif(os.environ.get('DEVSECOPS_TEST_NETWORK_SANDBOX') != '1', reason='Explicit host macOS network-denial qualification')
def test_actual_signing_and_verification_with_os_network_denied(case, monkeypatch):
    original = local_signing.run
    def denied(argv, **kwargs):
        return original(['/usr/bin/sandbox-exec', '-p', '(version 1)(allow default)(deny network*)', *argv], **kwargs)
    monkeypatch.setattr(local_signing, 'run', denied)
    monkeypatch.setattr(signatures, 'run', denied)
    result, cap = signing.sign_assessed_mission(**case['args'])
    assert result['checks']['sign'] == result['checks']['verify'] == 'pass' and cap is None
