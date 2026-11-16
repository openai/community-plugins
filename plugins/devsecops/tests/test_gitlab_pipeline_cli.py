"""Real TLS command execution, including a clean extracted plugin installation."""
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from test_gitlab_pipeline import case, server, ca, SECRET, SHA, job

__all__ = ['case', 'server', 'ca']
ROOT = Path(__file__).resolve().parents[1]


def arguments(case):
    value = case['args']
    result = []
    for name in ('origin', 'project_id', 'pipeline_id', 'expected_sha', 'token_env', 'ca_bundle', 'output'):
        result.extend(('--' + name.replace('_', '-'), str(value[name])))
    for artifact in value['artifact_requests']:
        result.extend(('--artifact', str(artifact['job_id']) + ':' + artifact['path']))
    result.extend(('--timeout', '30'))
    return result


def execute(case, tmp_path, *, script=None, argv=None, env=None):
    selected = script or ROOT / 'scripts/collect_gitlab_pipeline.py'
    environment = {key: value for key, value in os.environ.items() if key not in {'PYTHONPATH', 'PYTHONHOME'}}
    if env is not None:
        environment.update(env)
    return subprocess.run([sys.executable, '-B', str(selected), *(arguments(case) if argv is None else argv)],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=15)


@pytest.mark.parametrize('status,code', [('success', 0), ('failed', 1), ('identity-mismatch', 2)])
def test_actual_tls_cli_has_narrow_exit_decisions_and_no_secret_output(case, tmp_path, status, code):
    if status == 'identity-mismatch':
        case['server']['responses'][0]['json']['sha'] = 'b'*40
    else:
        for index in (0, 8):
            case['server']['responses'][index]['json']['status'] = status
    result = execute(case, tmp_path)
    assert result.returncode == code, result.stderr
    report = json.loads(result.stdout)
    assert report['decision'] == {0: 'pass', 1: 'fail', 2: 'unknown'}[code]
    assert report['selection'] == {'project_id': 7, 'pipeline_id': 42, 'expected_sha': SHA}
    assert report['factory_decision'] == 'unknown' and report['origin_authority'] == 'not-issued'
    assert SECRET not in result.stdout + result.stderr
    assert report == json.loads((case['args']['output'] / 'gitlab-pipeline.json').read_bytes())
    assert case['server']['requests'] and all(row['authorization'] == 'Bearer ' + SECRET for row in case['server']['requests'])


def test_repeated_artifact_option_selects_two_exact_files(case, tmp_path):
    case['args']['artifact_requests'] += ({'job_id': 99, 'path': 'build/receipt.json'},)
    case['server']['responses'][6:6] = [{'json': job()}, {'raw': b'second selected file', 'content_type': 'application/octet-stream'}, {'json': job()}]
    result = execute(case, tmp_path)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert [item['path'] for item in report['artifacts']] == ['publication/report.json', 'build/receipt.json']
    assert (case['args']['output'] / report['artifacts'][1]['file']).read_bytes() == b'second selected file'
    assert len(case['server']['requests']) == 12


def test_clean_extracted_package_runs_actual_tls_collection_without_author_checkout(case, tmp_path):
    from scripts import build_plugin
    archive = tmp_path / 'plugin.zip'
    build_plugin.build(archive)
    installed = tmp_path / 'clean installed plugin with spaces'
    with zipfile.ZipFile(archive) as package:
        package.extractall(installed)
    script = installed / 'devsecops/scripts/collect_gitlab_pipeline.py'
    result = execute(case, tmp_path, script=script)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['decision'] == 'pass' and len(case['server']['requests']) == 9
    artifact = report['artifacts'][0]
    assert (case['args']['output'] / artifact['file']).read_bytes() == b'\x00raw selected artifact\xff'
    assert SECRET not in result.stdout + result.stderr


@pytest.mark.parametrize('extra', [
    ['--artifact', '99:../PRIVATE-GITLAB-CANARY'],
    ['--artifact', 'PRIVATE-GITLAB-CANARY'],
    ['--project-id', 'PRIVATE-GITLAB-CANARY'],
    ['--token', 'PRIVATE-GITLAB-CANARY'],
])
def test_argument_errors_do_not_echo_values_or_make_network_calls(case, tmp_path, extra):
    result = execute(case, tmp_path, argv=[*arguments(case), *extra])
    assert result.returncode == 2 and SECRET not in result.stdout + result.stderr
    assert not case['server']['requests'] and not case['args']['output'].exists()


@pytest.mark.parametrize('extra', [
    ['--artifact', '99:publication/report.json'],
    ['--timeout', 'nan'], ['--timeout', '121'],
    ['--token-env', 'MISSING_EXPLICIT_READ_TOKEN'],
])
def test_api_limits_and_credential_reference_errors_stay_safe(case, tmp_path, extra):
    result = execute(case, tmp_path, argv=[*arguments(case), *extra])
    assert result.returncode == 2
    assert json.loads(result.stderr)['decision'] == 'unknown'
    assert SECRET not in result.stdout + result.stderr
    assert not case['server']['requests'] and not case['args']['output'].exists()


def test_nine_artifacts_cannot_expand_the_api_budget(case, tmp_path):
    case['args']['artifact_requests'] = tuple({'job_id': 99, 'path': f'evidence/{index}.json'} for index in range(9))
    result = execute(case, tmp_path)
    assert result.returncode == 2 and json.loads(result.stderr)['decision'] == 'unknown'
    assert not case['server']['requests']
