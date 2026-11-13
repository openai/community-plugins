"""Actual native image catalog handoff, controlled DB unless explicitly selected."""
import base64
import copy
import hashlib
import json
import time

import pytest

from devsecops.adapters import artifacts as a
from devsecops.adapters import scanner_catalog as c
from devsecops.adapters.http import APIError, _json
from test_artifacts import database, oci_archive, tools
from test_vulnerability_policy import large_scanner_file_inventory

__all__ = ['tools']
RELEASE = b'NAME="Ubuntu"\nID=ubuntu\nVERSION_ID="24.04"\nVERSION_CODENAME=noble\n'
CANARY = 'PRIVATE_IMAGE_ENV_CANARY_4531'


@pytest.fixture
def case(tmp_path, tools, monkeypatch):
    source, digest = oci_archive(tmp_path, env=CANARY, labels={'private.example/config': CANARY}, os_release=RELEASE)
    captured, actual = {}, a.run
    def capture(argv, **kwargs):
        result = actual(argv, **kwargs)
        if 'syft-json' in argv:
            captured['native'] = result[1]
        elif 'sbom:sbom.syft.json' in argv:
            captured['grype'] = result[1]
        return result
    monkeypatch.setattr(a, 'run', capture)
    arguments = {'source': source, 'source_kind': 'oci-archive', 'expected_sha256': a._hash(source.read_bytes()),
        'expected_image_digest': digest, 'architecture': 'amd64', 'output': tmp_path/'scan', **tools, **database(tmp_path)}
    return arguments, captured


def test_actual_native_catalog_preserves_distro_without_publishing_configuration(case):
    args, captured = case
    result = a.scan_local_artifact(**args)
    obs = result['observations']
    assert obs['vulnerabilities']['decision'] == 'pass'
    native, original = json.loads(captured['native']), json.loads(captured['grype'])
    retained = json.loads((args['output']/'grype.json').read_bytes())
    assert native['distro']['id'] == 'ubuntu' and retained['distro'] == original['distro']
    assert retained['distro']['name'] == 'ubuntu' and retained['distro']['version'] == '24.04'
    assert retained['matches'] == original['matches']
    assert retained.get('ignoredMatches') == original.get('ignoredMatches')
    assert retained['source']['target'] == {'userInput': 'frozen-artifact:'+args['expected_sha256'],
        'manifestDigest': args['expected_image_digest'], 'imageID': obs['image']['config_digest'], 'os': 'linux', 'architecture': 'amd64'}
    assert set(retained['descriptor']) == {'name', 'version', 'db'}
    assert set(retained['descriptor']['db']['status']) == {'schemaVersion', 'built', 'valid'}
    encoded_config = native['source']['metadata']['config']
    assert CANARY.encode() in base64.b64decode(encoded_config)
    for path in args['output'].iterdir():
        raw = path.read_bytes()
        assert CANARY.encode() not in raw and encoded_config.encode() not in raw
    assert not (args['output']/'sbom.syft.json').exists()
    assert obs['catalog']['sha256'] == hashlib.sha256(captured['native']).hexdigest()
    assert obs['normalization']['original_stdout_sha256'] == hashlib.sha256(captured['grype']).hexdigest()
    assert obs['receipts'][-1]['stdout_sha256'] == obs['normalization']['original_stdout_sha256']
    assert obs['normalization']['method'] == c.NORMALIZATION
    assert obs['normalization']['original_retention'] == 'private-transient-output-only'
    assert result['decision'] == 'unknown'  # Controlled image/DB never become release authority.


@pytest.mark.parametrize('operation', ['convert', 'grype'])
def test_changed_native_catalog_is_rejected_after_each_consumer(case, monkeypatch, operation):
    args, _ = case
    actual = a.run
    def change(argv, **kwargs):
        result = actual(argv, **kwargs)
        if ('convert' in argv if operation == 'convert' else 'sbom:sbom.syft.json' in argv):
            path = kwargs['cwd']/'sbom.syft.json'
            path.chmod(0o600)
            path.write_bytes(path.read_bytes()+b' ')
        return result
    monkeypatch.setattr(a, 'run', change)
    result = a.scan_local_artifact(**args)
    assert 'scanner-catalog-changed' in {item['code'] for item in result['gaps']}
    assert not (args['output']/'grype.json').exists()


@pytest.mark.parametrize('phase,field', [
    ('native', 'manifestDigest'), ('native', 'imageID'), ('native', 'architecture'), ('native', 'config'),
    ('grype', 'manifestDigest'), ('grype', 'imageID'), ('grype', 'architecture'), ('grype', 'config'),
    ('grype', 'unknown-config-copy'),
])
def test_native_and_grype_image_identity_checked_before_projection(case, monkeypatch, capsys, phase, field):
    args, _ = case
    actual = a.run
    def change(argv, **kwargs):
        result = actual(argv, **kwargs)
        if ('syft-json' in argv if phase == 'native' else 'sbom:sbom.syft.json' in argv):
            value = json.loads(result[1])
            target = value['source']['metadata' if phase == 'native' else 'target']
            target[field] = CANARY
            raw = json.dumps(value).encode()
            return result[0], raw, result[2], {**result[3], 'stdout_sha256': a._hash(raw), 'stdout_bytes': len(raw)}
        return result
    monkeypatch.setattr(a, 'run', change)
    result = a.scan_local_artifact(**args)
    assert any(item['code'].startswith('scanner-image-') for item in result['gaps'])
    assert not (args['output']/'grype.json').exists()
    assert CANARY not in json.dumps(result) and CANARY not in capsys.readouterr().out
    for path in args['output'].iterdir():
        assert CANARY.encode() not in path.read_bytes()


def test_descriptor_configuration_copy_is_removed_as_a_whole(case, monkeypatch):
    args, _ = case
    actual = a.run
    def change(argv, **kwargs):
        result = actual(argv, **kwargs)
        if 'sbom:sbom.syft.json' in argv:
            value = json.loads(result[1])
            value['descriptor']['configuration']['nested'] = {'secret': CANARY, 'copy': value['source']['target']['config']}
            raw = json.dumps(value).encode()
            return result[0], raw, result[2], {**result[3], 'stdout_sha256': a._hash(raw), 'stdout_bytes': len(raw)}
        return result
    monkeypatch.setattr(a, 'run', change)
    report = a.scan_local_artifact(**args)
    assert report['observations']['vulnerabilities']['decision'] == 'pass'
    assert CANARY not in (args['output']/'grype.json').read_text()
    assert 'configuration' not in json.loads((args['output']/'grype.json').read_bytes())['descriptor']


def test_normalization_rejects_unknown_descriptor_or_database_envelopes(case):
    args, captured = case
    report = a.scan_local_artifact(**args)
    original = json.loads(captured['grype'])
    for route in [('descriptor',), ('descriptor', 'db'), ('descriptor', 'db', 'status')]:
        value = copy.deepcopy(original)
        selected = value
        for key in route:
            selected = selected[key]
        selected['newConfigurationCopy'] = CANARY
        with pytest.raises(a._Invalid) as error:
            c.normalize_image_result(json.dumps(value).encode(), image=report['observations']['image'],
                archive_sha256=args['expected_sha256'], grype_version=a.VERSIONS['grype'], deadline=time.monotonic()+1)
        assert CANARY not in str(error.value)


def test_catalog_and_projection_obey_original_deadline():
    for call, kwargs in ((c.inspect_image_catalog, {'syft_version': a.VERSIONS['syft']}),
                         (c.normalize_image_result, {'grype_version': a.VERSIONS['grype']})):
        with pytest.raises(a._Invalid) as error:
            call(b'{}', image={}, archive_sha256='a'*64, deadline=time.monotonic()-1, **kwargs)
        assert error.value.code == 'scan-deadline-exceeded'


@pytest.mark.parametrize('phase', ['native', 'spdx', 'grype'])
def test_controlled_large_scanner_documents_keep_bounded_admission(case, monkeypatch, phase):
    """Augmented tool output tests parser capacity; it is not genuine scan proof."""
    args, _ = case
    actual = a.run
    expanded = []

    def large_output(argv, **kwargs):
        code, raw, err, receipt = actual(argv, **kwargs)
        selected = ('syft-json' in argv if phase == 'native' else
                    'convert' in argv if phase == 'spdx' else 'sbom:sbom.syft.json' in argv)
        if selected:
            value = json.loads(raw)
            if phase == 'spdx':
                value['files'] = large_scanner_file_inventory()
            else:
                value['descriptor']['configuration']['controlled-file-inventory'] = large_scanner_file_inventory()
            raw = json.dumps(value).encode()
            assert len(raw) < 16_000_000
            with pytest.raises(APIError):
                _json(raw)  # Ordinary service responses retain their smaller budget.
            with pytest.raises(APIError):
                _json(raw, max_values=300_000)
            receipt = {**receipt, 'stdout_sha256': a._hash(raw), 'stdout_bytes': len(raw)}
            expanded.append(phase)
        return code, raw, err, receipt

    monkeypatch.setattr(a, 'run', large_output)
    report = a.scan_local_artifact(**args)
    assert expanded == [phase]
    assert report['execution_status'] == 'partial' and report['decision'] == 'unknown'
    assert 'artifact-scan-incomplete' not in {item['code'] for item in report['gaps']}
    assert report['observations']['sbom']['decision'] == 'pass'
    assert report['observations']['vulnerabilities']['decision'] == 'pass'
    for path in args['output'].iterdir():
        assert CANARY.encode() not in path.read_bytes()


@pytest.mark.parametrize('kind', ['values', 'bytes', 'depth', 'duplicate', 'nonfinite'])
def test_scanner_documents_keep_structural_limits(kind):
    raw = {
        'values': lambda: json.dumps({'files': [None] * 500_000}).encode(),
        'bytes': lambda: b'{"value":"' + b'x' * 16_000_000 + b'"}',
        'depth': lambda: b'{"value":' + b'[' * 65 + b'null' + b']' * 65 + b'}',
        'duplicate': lambda: b'{"value":1,"value":2}',
        'nonfinite': lambda: b'{"value":NaN}',
    }[kind]()
    with pytest.raises((APIError, a._Invalid)):
        c._document(raw, time.monotonic() + 30)
