"""Controlled bundle bytes; no authenticated CI or live job claim."""
from copy import deepcopy
import pytest
from devsecops.factory.ci_evidence_qualification import verify_ci_bundles
from devsecops.factory.qualification import QualificationError
from devsecops.factory.state import _canonical, _sha


@pytest.fixture
def bundle(tmp_path):
    raw = _canonical({'pipeline': {'status': 'running'}, 'transport': {'status': 200}})
    document = {'label': 'poll-0001.json', 'path': '/not-opened/poll-0001.json',
                'sha256': _sha(raw), 'bytes': len(raw), 'utf8': raw.decode()}
    value = {'schema_version': 1, 'kind': 'original-ci-evidence-bundle', 'run_id': 'controlled-run',
             'operation_id': 'controlled-op', 'authority': 'historical-evidence-only', 'documents': [document]}
    def check():
        frozen = _canonical(value)
        (tmp_path / '0').write_bytes(frozen)
        reference = {'path': '0', 'bytes': len(frozen), 'sha256': _sha(frozen)}
        result = {'run_id': 'controlled-run', 'phase_records': [{'artifacts': [reference]}]}
        return verify_ci_bundles(tmp_path / 'e2e-result.json', result, {'files': [reference]})
    return value, check


def test_embedded_original_bytes_validate_without_opening_reported_path(bundle):
    _, check = bundle
    result = check()
    assert result[0]['documents'] == 1 and result[0]['authority'] == 'historical-evidence-only'


@pytest.mark.parametrize('change', [
    lambda b: b.update(run_id='another-run'),
    lambda b: b.update(authority='live-ci-origin'),
    lambda b: b['documents'][0].update(utf8='{}'),
    lambda b: b['documents'][0].pop('utf8'),
    lambda b: b['documents'][0].update(path='/not-opened/../poll-0001.json'),
    lambda b: b['documents'].append(deepcopy(b['documents'][0])),
])
def test_scope_path_only_hash_mismatch_and_duplicate_members_reject(bundle, change):
    value, check = bundle
    change(value)
    with pytest.raises(QualificationError):
        check()
