"""Execute the shipped CI shell/CLI contract with a recording stub; no services."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from scripts import build_plugin

ROOT = Path(__file__).resolve().parents[1]
RECIPE = 'demo/software-factory/ci/gitlab-ci.yml'
COMMAND = 'scripts/publish_and_propose_mission.py'
CONFIGS = ('build', 'staging', 'security', 'signing', 'publication', 'proposal')
ARTIFACTS = [
    'mission-proposal/mission-proposal-operation.json',
    'mission-proposal/publication/mission-publication-operation.json',
    'mission-proposal/publication/publication/mission-publication.json',
    'mission-proposal/proposal/deployment-proposal.json',
    'mission-proposal/proposal/proposal.patch',
]


def selected_recipe():
    recipe = yaml.safe_load((ROOT / RECIPE).read_bytes())
    return recipe, recipe['publish_verified_mission']


@pytest.fixture
def shell_case(tmp_path):
    plugin = tmp_path / 'approved plugin with spaces'
    (plugin / 'scripts').mkdir(parents=True)
    # Substitute only the test command at the operator-selected plugin path.
    # The real recipe shell and Python interpreter execute without any backend.
    (plugin / COMMAND).write_text('''import json, os, sys
with open(os.environ['RECIPE_TEST_CALLS'], 'a') as output:
    output.write(json.dumps({'argv': sys.argv, 'no_bytecode': sys.dont_write_bytecode}) + '\\n')
raise SystemExit(int(os.environ['RECIPE_TEST_EXIT']))
''')
    project = tmp_path / 'candidate source with spaces'
    project.mkdir()
    calls = tmp_path / 'calls.jsonl'
    env = {'PATH': '', 'DEVSECOPS_PYTHON': sys.executable, 'DEVSECOPS_PLUGIN_ROOT': str(plugin),
        'DEVSECOPS_BUILD_RUNTIME_ROOT': str(tmp_path / 'runtime with spaces'),
        'CI_PROJECT_DIR': str(project), 'CI_COMMIT_SHA': 'a' * 40,
        'RECIPE_TEST_CALLS': str(calls), 'RECIPE_TEST_EXIT': '0'}
    for index, name in enumerate(CONFIGS):
        # Expansion of an environment value must preserve literal shell syntax.
        path = tmp_path / (name + ' selected $literal `literal`.json')
        path.write_text('{}')
        env['DEVSECOPS_' + name.upper() + '_CONFIG'] = str(path)
        env['DEVSECOPS_' + name.upper() + '_CONFIG_SHA256'] = str(index + 1) * 64
    return {'env': env, 'project': project, 'plugin': plugin, 'calls': calls}


def run_recipe(case):
    _, job = selected_recipe()
    return subprocess.run(['/bin/sh', '-ec', '\n'.join(job['script'])], cwd=case['project'],
        env=case['env'], capture_output=True, text=True, timeout=10)


@pytest.mark.parametrize('exit_code', [0, 1, 2])
def test_recipe_passes_six_pins_and_source_to_supported_cli_once(shell_case, exit_code, monkeypatch):
    shell_case['env']['RECIPE_TEST_EXIT'] = str(exit_code)
    result = run_recipe(shell_case)
    assert result.returncode == exit_code, result.stderr
    calls = [json.loads(line) for line in shell_case['calls'].read_text().splitlines()]
    assert len(calls) == 1 and calls[0]['no_bytecode'] is True
    assert calls[0]['argv'][0] == str(shell_case['plugin'] / COMMAND)
    # Parse the captured expanded arguments with the actual installed command's
    # parser; the recording stub cannot hide unsupported or omitted options.
    monkeypatch.setattr(sys, 'path', list(sys.path))
    spec = importlib.util.spec_from_file_location('recipe_proposal_command', ROOT / COMMAND)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    args = vars(script.parser().parse_args(calls[0]['argv'][1:]))
    expected = {'output': shell_case['project'] / 'mission-proposal',
        'runtime_root': Path(shell_case['env']['DEVSECOPS_BUILD_RUNTIME_ROOT']),
        'expected_source_repository': shell_case['project'],
        'expected_source_revision': shell_case['env']['CI_COMMIT_SHA'], 'timeout': 3500}
    for name in CONFIGS:
        expected[name + '_config'] = Path(shell_case['env']['DEVSECOPS_' + name.upper() + '_CONFIG'])
        expected[name + '_config_sha256'] = shell_case['env']['DEVSECOPS_' + name.upper() + '_CONFIG_SHA256']
    assert args == expected


@pytest.mark.parametrize('name', CONFIGS)
@pytest.mark.parametrize('suffix', ['', '_SHA256'])
@pytest.mark.parametrize('state', ['missing', 'empty'])
def test_recipe_rejects_each_missing_or_empty_selection_before_invocation(shell_case, name, suffix, state):
    key = 'DEVSECOPS_' + name.upper() + '_CONFIG' + suffix
    if state == 'missing':
        shell_case['env'].pop(key)
    else:
        shell_case['env'][key] = ''
    result = run_recipe(shell_case)
    assert result.returncode != 0 and not shell_case['calls'].exists()


def test_recipe_retains_only_named_review_artifacts_and_preserves_failures():
    recipe, job = selected_recipe()
    assert recipe['stages'] == ['publish'] and job['stage'] == 'publish'
    assert job['tags'] == ['$DEVSECOPS_RUNNER_TAG'] and job['timeout'] == '1 hour'
    assert not job.get('allow_failure', False) and job.get('retry', 0) == 0
    assert job['artifacts'] == {'when': 'always', 'paths': ARTIFACTS, 'expire_in': '7 days'}
    assert job['rules'] == [
        {'if': '$CI_PIPELINE_SOURCE == "merge_request_event"', 'when': 'on_success'},
        {'when': 'never'},
    ]
    assert set(job) == {'stage', 'rules', 'tags', 'timeout', 'script', 'artifacts'}
    assert set(recipe) == {'stages', 'publish_verified_mission'}


def test_packaged_recipe_and_supported_command_match_reviewed_source():
    files = build_plugin.package_files()
    build_plugin.validate(files)
    assert files[RECIPE] == (ROOT / RECIPE).read_bytes()
    assert files[COMMAND] == (ROOT / COMMAND).read_bytes()
    assert yaml.safe_load(files[RECIPE])['publish_verified_mission']['artifacts']['paths'] == ARTIFACTS
