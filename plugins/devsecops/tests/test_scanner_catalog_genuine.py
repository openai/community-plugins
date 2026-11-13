"""Opt-in retained genuine DB regression; never downloads or issues authority."""
import hashlib
import json
import os
from pathlib import Path
import time

import pytest

from devsecops.adapters import artifacts as a
from devsecops.adapters import vulnerability_policy as p
from test_artifacts import oci_archive, tools
from test_scanner_catalog import CANARY, RELEASE
from test_vulnerability_policy import encode, policy

__all__ = ['tools']
PACKAGE = os.environ.get('DEVSECOPS_GENUINE_GRYPE_PACKAGE')
LISTING_SHA256 = '69fd0add9813d7e0eecc349752376aba30678cbed27184d24051316a62496460'
ARCHIVE_SHA256 = 'dcca24722e16c2bcf4a542a3f7fdd2147baf9215c17b973f5a7d4243fbddacea'


@pytest.mark.skipif(not PACKAGE, reason='Explicit retained official package required; no acquisition in tests')
def test_retained_official_database_preserves_real_distro_and_findings(tmp_path, tools):
    package = Path(PACKAGE)
    listing_raw = (package/'latest.json').read_bytes()
    assert hashlib.sha256(listing_raw).hexdigest() == LISTING_SHA256
    acquisition = json.loads((package/'receipt.json').read_bytes())
    assert acquisition['http_status'] == 200 and acquisition['archive']['sha256'] == ARCHIVE_SHA256
    root = Path(__file__).resolve().parents[1]
    source_files = ('src/devsecops/adapters/artifacts.py', 'src/devsecops/adapters/scanner_catalog.py',
        'src/devsecops/adapters/vulnerability_policy.py', 'src/devsecops/adapters/database_zstd.py',
        'src/devsecops/adapters/signatures.py', 'src/devsecops/process.py', 'supplychain.lock.json',
        'tests/test_scanner_catalog_genuine.py', 'tests/test_artifacts.py')
    implementation = {name: a._hash((root/name).read_bytes()) for name in source_files}
    image, manifest = oci_archive(tmp_path, os_release=RELEASE, env=CANARY,
        labels={'private.example/config': CANARY}, package_name='urllib3', package_version='1.26.5')
    archive_sha256 = a._hash(image.read_bytes())
    report = a.scan_local_artifact(source=image, source_kind='oci-archive', expected_sha256=archive_sha256,
        expected_image_digest=manifest, architecture='amd64', output=tmp_path/'scan',
        database_snapshot=package/'database.tar.zst', database_sha256=ARCHIVE_SHA256, timeout=300, **tools)
    observed = report['observations']
    assert observed['database']['decision'] == 'pass'
    assert observed['database']['schema_version'] == 'v6.1.10'
    selected = policy()
    selected['database'].update(archive_sha256=ARCHIVE_SHA256, listing_sha256=LISTING_SHA256,
        acquired_at=acquisition['acquired_at'], trust_reference='retained official acquisition, controlled image qualification only')
    deadline = time.monotonic()+30
    listed = p.database_listing(listing_raw, selected, deadline=deadline)
    result = p.evaluate_scan((tmp_path/'scan/sbom.spdx.json').read_bytes(), (tmp_path/'scan/grype.json').read_bytes(),
        observed, {'archive_sha256': archive_sha256, 'manifest_digest': manifest,
                   'config_digest': observed['image']['config_digest'], 'architecture': 'amd64'}, selected, listed, deadline=deadline)
    assert result['decision'] == 'fail' and result['gaps'] == []
    assert result['counts']['High'] == 7 and result['counts']['Medium'] == 3 and result['reported_matches'] == 10
    retained = json.loads((tmp_path/'scan/grype.json').read_bytes())
    assert retained['distro']['name'] == 'ubuntu' and retained['distro']['version'] == '24.04'
    for path in (tmp_path/'scan').iterdir():
        assert CANARY.encode() not in path.read_bytes()
    assert 'config' not in retained['source']['target'] and 'configuration' not in retained['descriptor']
    assert all(a._hash((root/name).read_bytes()) == value for name, value in implementation.items())
    (tmp_path/'policy-evaluation.json').write_bytes(encode(result))
    (tmp_path/'qualification.json').write_bytes(encode({'scope': 'genuine-database-controlled-image-only',
        'implementation_sources': implementation, 'database_sha256': ARCHIVE_SHA256, 'listing_sha256': LISTING_SHA256,
        'acquisition_receipt': str(package/'receipt.json'), 'archive_sha256': archive_sha256, 'manifest_digest': manifest,
        'distro': retained['distro'], 'counts': result['counts'], 'coverage_gaps': result['gaps'],
        'mission_authority': 'not-issued', 'canonical_release': 'not-attempted'}))
