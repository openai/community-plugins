"""Real CLI/TLS composition and interruption/output safety; not live qualification."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time

import pytest
from test_argocd import ca, server
from test_runtime_context import inputs
from test_runtime_collection import queue_phase, setup

from devsecops.cli import main
from devsecops.runtime import start_runtime_collection
from devsecops.runtime.context import canonical_bytes, digest, file_hash, seal
from devsecops.runtime.presentation import render_html

__all__ = ['ca', 'server']
ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.write_bytes(canonical_bytes(value))
    return path


def assert_manifest(output):
    manifest = json.loads((output / 'runtime-manifest.json').read_text())
    assert manifest['finished'] and manifest['decision'] == 'unknown'
    assert manifest['input_authentication'] == 'not-verified'
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    for name, artifact in manifest['artifacts'].items():
        raw = (output / name).read_bytes()
        assert artifact == {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        assert stat.S_IMODE((output / name).stat().st_mode) == 0o600
    return manifest


def test_invalid_input_yields_private_unknown_error_without_echo(tmp_path, capsys):
    invalid = tmp_path / 'invalid.json'
    invalid.write_text('{"password":"SECRET-DO-NOT-EXPORT"')
    output = tmp_path / 'result'
    code = main(['collect-runtime', '--config', str(invalid), '--lock', str(invalid), '--intent', str(invalid), '--output', str(output)])
    assert code == 2
    captured = capsys.readouterr()
    assert 'SECRET-DO-NOT-EXPORT' not in captured.out + captured.err
    assert json.loads(captured.out)['execution_status'] == 'error'
    assert_manifest(output)
    assert all('SECRET-DO-NOT-EXPORT' not in p.read_text() for p in output.iterdir())
    assert 'RECOVERY UNKNOWN' in (output / 'report.html').read_text()


@pytest.mark.parametrize('symlink', [False, True])
def test_existing_output_and_symlink_ancestor_never_overwrite_or_read(tmp_path, monkeypatch, symlink):
    destination = tmp_path / 'existing'
    destination.mkdir()
    sentinel = destination / 'sentinel'
    sentinel.write_bytes(b'keep')
    if symlink:
        link = tmp_path / 'linked'
        link.symlink_to(destination, target_is_directory=True)
        output = link / 'new'
    else:
        output = destination
    def forbidden(*_args, **_kwargs):
        pytest.fail('Input collection must not run for an invalid output path')
    monkeypatch.setattr('devsecops.runtime.cli.freeze_runtime_context', forbidden)
    assert main(['collect-runtime', '--config', 'missing', '--lock', 'missing', '--intent', 'missing', '--output', str(output)]) == 2
    assert list(destination.iterdir()) == [sentinel] and sentinel.read_bytes() == b'keep'


def pending_files(tmp_path):
    now = datetime.now(timezone.utc)
    def future(config, lock, _intent):
        config['expires_at'] = (now + timedelta(hours=1)).isoformat()
        lock['observation_window_seconds'] = 120
    context, clients = setup(now=now, mutate=future)
    authority = context.authority
    for name, value in authority.items():
        write(tmp_path / (name + '.json'), value)
    pending = start_runtime_collection(context, authority=authority, clients=clients, now=now)
    assert pending['stage'] == 'pending'
    return write(tmp_path / 'context.json', context), write(tmp_path / 'pending.json', pending), pending


def authority_args(tmp_path):
    return [item for name in ('config', 'lock', 'intent') for item in ('--' + name, str(tmp_path / (name + '.json')))]


def test_early_resume_no_network_preserves_window_and_raw_hashes(tmp_path, monkeypatch, capsys):
    context, pending, original = pending_files(tmp_path)
    def forbidden(*_args, **_kwargs):
        pytest.fail('An early resume must not create provider clients')
    monkeypatch.setattr('devsecops.runtime.collection._Clients', forbidden)
    output = tmp_path / 'early'
    assert main(['resume-runtime', '--context', str(context), '--pending', str(pending), *authority_args(tmp_path), '--output', str(output)]) == 2
    summary = json.loads(capsys.readouterr().out)
    assert summary['stage'] == 'pending' and summary['next_eligible_at'] == original['window']['next_eligible_at']
    result = json.loads((output / 'runtime-collection.json').read_text())
    assert result['window'] == original['window'] and result['pending_sha256'] == original['bundle_sha256']
    assert result['execution_mode'] == 'controlled-test'
    manifest = assert_manifest(output)
    assert manifest['inputs']['pending']['sha256'] == hashlib.sha256(pending.read_bytes()).hexdigest()
    assert all(not target['after'] for target in result['targets'].values())


def test_unexpected_interruption_keeps_context_but_no_terminal_manifest(tmp_path, monkeypatch):
    context, pending, _ = pending_files(tmp_path)
    def interrupted(*_args, **_kwargs):
        raise RuntimeError('synthetic process interruption')
    monkeypatch.setattr('devsecops.runtime.cli.finish_runtime_collection', interrupted)
    output = tmp_path / 'interrupted'
    with pytest.raises(RuntimeError):
        main(['resume-runtime', '--context', str(context), '--pending', str(pending), *authority_args(tmp_path), '--output', str(output)])
    assert (output / 'runtime-context.json').exists()
    assert not (output / 'runtime-manifest.json').exists()


def test_directory_swap_cannot_redirect_writes_or_publish_manifest(tmp_path, monkeypatch):
    from devsecops.runtime import cli
    output, moved, foreign = (tmp_path / name for name in ('result', 'moved', 'foreign'))
    foreign.mkdir()
    original_render = cli.render_html
    def swap(*args):
        output.rename(moved)
        output.symlink_to(foreign, target_is_directory=True)
        return original_render(*args)
    monkeypatch.setattr(cli, 'render_html', swap)
    assert main(['collect-runtime', '--config', 'missing', '--lock', 'missing', '--intent', 'missing', '--output', str(output)]) == 2
    assert not list(foreign.iterdir())
    assert (moved / 'runtime-error.json').exists()
    assert not (moved / 'runtime-manifest.json').exists()


def test_terminal_write_directory_swap_withdraws_manifest_and_prints_no_paths(tmp_path, monkeypatch, capsys):
    from devsecops.runtime import cli
    output, moved, foreign = (tmp_path / name for name in ('result', 'moved', 'foreign'))
    foreign.mkdir()
    original_write = cli._write
    def swap(directory, name, value):
        if name == 'runtime-manifest.json':
            output.rename(moved)
            output.symlink_to(foreign, target_is_directory=True)
        return original_write(directory, name, value)
    monkeypatch.setattr(cli, '_write', swap)
    assert main(['collect-runtime', '--config', 'missing', '--lock', 'missing', '--intent', 'missing', '--output', str(output)]) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert not list(foreign.iterdir())
    assert not (moved / 'runtime-manifest.json').exists()


def test_resealed_context_cannot_select_new_origin_or_credential(tmp_path, server, monkeypatch, capsys):
    now = datetime.now(timezone.utc)
    before = now - timedelta(seconds=3)
    def mutate(config, lock, _intent):
        config['expires_at'] = (now + timedelta(hours=1)).isoformat()
        lock.update(observation_window_seconds=2, scrape_interval_seconds=1, required_fresh_scrapes=3)
    original, clients = setup(now=before, mutate=mutate)
    for name, value in original.authority.items():
        write(tmp_path / (name + '.json'), value)
    pending = start_runtime_collection(original, authority=original.authority, clients=clients, now=before)
    context = deepcopy(original)
    for service in context['factory_config']['services']:
        if service['id'] in {'kubernetes', 'argocd', 'prometheus'}:
            service.update(origin=server['origin'], ca_file=str(server['ca']),
                           read_auth={'mode': 'bearer', 'token_env': 'UNAPPROVED_REVIEW_TOKEN'})
            context['ca_artifacts'][service['id']] = {'state': 'frozen', 'sha256': file_hash(server['ca'])}
    context['intent']['identities']['config_sha256'] = digest(context['factory_config'])
    for name, key in [('config', 'factory_config'), ('intent', 'intent')]:
        context['input_artifacts'][name].update(sha256=digest(context[key]), canonical_sha256=digest(context[key]), path=None)
    context = seal(context, 'context_sha256')
    pending.update(context_snapshot=context, context_sha256=context['context_sha256'],
                   identities=deepcopy(context['intent']['identities']))
    pending = seal(pending, 'bundle_sha256')
    context_path = write(tmp_path / 'forged-context.json', context)
    pending_path = write(tmp_path / 'forged-pending.json', pending)
    monkeypatch.setenv('UNAPPROVED_REVIEW_TOKEN', 'synthetic-only-do-not-send')
    output = tmp_path / 'rejected'
    assert main(['resume-runtime', '--context', str(context_path), '--pending', str(pending_path),
                 *authority_args(tmp_path), '--output', str(output)]) == 2
    assert not server['requests']
    assert json.loads(capsys.readouterr().out)['execution_status'] == 'error'
    assert (output / 'runtime-error.json').exists()
    assert all('synthetic-only-do-not-send' not in path.read_text() for path in output.iterdir())


def test_cli_actual_tls_collect_then_resume_with_real_wall_clock(tmp_path, server):
    config, lock, intent, receipts = inputs()
    now = datetime.now(timezone.utc)
    config['expires_at'] = (now + timedelta(minutes=10)).isoformat()
    lock.update(observation_window_seconds=2, scrape_interval_seconds=1, required_fresh_scrapes=3)
    for service in config['services']:
        if service['id'] in {'argocd', 'kubernetes', 'prometheus'}:
            service.update(origin=server['origin'], ca_file=str(server['ca']),
                           read_auth={'mode': 'bearer', 'token_env': 'RUNTIME_CLI_TEST_TOKEN'})
    intent['identities'].update(config_sha256=digest(config), lock_sha256=digest(lock))
    paths = {name: write(tmp_path / (name + '.json'), value) for name, value in
             (('config', config), ('lock', lock), ('intent', intent))}
    args = ['collect-runtime']
    for name, path in paths.items():
        args.extend(['--' + name, str(path)])
    for name, value in receipts.items():
        args.extend(['--receipt', name + '=' + str(write(tmp_path / ('receipt-' + name + '.json'), value))])
    before = tmp_path / 'before'
    args.extend(['--output', str(before)])
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'), RUNTIME_CLI_TEST_TOKEN='synthetic-cli-secret',
               HTTPS_PROXY='http://127.0.0.1:1')
    queue_phase(server, before=True, at=now)
    first = subprocess.run([sys.executable, '-m', 'devsecops.cli', *args], env=env, cwd=ROOT,
                           capture_output=True, text=True, timeout=20)
    assert first.returncode == 2, first.stderr
    pending = json.loads((before / 'runtime-collection.json').read_text())
    assert pending['stage'] == 'pending' and len(server['requests']) == 24
    assert pending['execution_mode'] == 'direct'
    assert_manifest(before)
    delay = max(0, pending['window']['end'] - time.time())
    assert delay < 4
    time.sleep(delay)
    end = datetime.fromtimestamp(pending['window']['end'], timezone.utc)
    queue_phase(server, before=False, at=end, window=pending['window'])
    after = tmp_path / 'after'
    second = subprocess.run([sys.executable, '-m', 'devsecops.cli', 'resume-runtime',
                             '--context', str(before / 'runtime-context.json'), '--pending', str(before / 'runtime-collection.json'),
                             *authority_args(tmp_path),
                             '--output', str(after)], env=env, cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert second.returncode == 2, second.stderr
    measured = json.loads((after / 'runtime-collection.json').read_text())
    assert measured['stage'] == 'measured' and measured['decision'] == 'unknown'
    assert measured['execution_mode'] == 'direct'
    assert measured['window'] == pending['window']
    assert len(server['requests']) == 52 and not server['responses']
    assert measured['targets']['mission']['prometheus']['decision'] == 'pass'
    assert_manifest(after)
    assert all(request['authorization'] == 'Bearer synthetic-cli-secret' for request in server['requests'])
    assert all('synthetic-cli-secret' not in path.read_text() for directory in (before, after) for path in directory.iterdir())


def test_report_escapes_observations_and_does_not_call_window_samples_current():
    result = {'stage': 'measured', 'targets': {'<script>bad</script>': {'before': {}, 'after': {}, 'prometheus': {
        'observations': {'scrape': {'fresh_sample_count': 3}, 'collection_freshness': {'decision': 'unknown'}}}}},
        'gaps': [{'code': '<img src=x onerror=bad>'}], 'decision': 'unknown'}
    html = render_html(result, 'runtime-collection.json')
    assert '<script>' not in html and '<img' not in html
    assert '3 samples in recorded window' in html and 'freshness at collection: unknown' in html
    assert 'Pod readiness is separate from application HTTP health' in html
    assert 'default-src' in html and 'RECOVERY UNKNOWN' in html


def test_diagnosis_cli_is_offline_and_preserves_raw_input_identities(tmp_path, monkeypatch, capsys):
    import socket
    from test_runtime_diagnosis import measured
    baseline = write(tmp_path / 'baseline.json', measured()[0])
    fault = write(tmp_path / 'fault.json', measured('fault', 60, up=0)[0])
    def forbidden(*_args, **_kwargs):
        pytest.fail('Diagnosis must not open network connections')
    monkeypatch.setattr(socket, 'socket', forbidden)
    output = tmp_path / 'diagnosis'
    assert main(['diagnose-gitops', '--baseline', str(baseline), '--fault', str(fault), '--output', str(output)]) == 2
    summary = json.loads(capsys.readouterr().out)
    assert summary['confirmed_failure'] and summary['decision'] == 'unknown'
    manifest = assert_manifest(output)
    assert manifest['inputs']['fault']['sha256'] == hashlib.sha256(fault.read_bytes()).hexdigest()
    html = (output / 'report.html').read_text()
    assert 'What changed' in html and 'Alternatives to investigate' in html
    assert 'Controlled test evidence' in html
    assert 'does not measure causal confidence' in html and 'Retained failure evidence' in html


def test_historical_failure_label_does_not_claim_current_failure():
    result = {'kind': 'runtime-diagnosis', 'confirmed_failure': True, 'comparison_status': 'incomplete',
              'retained_failures': [{'input': 'fault', 'target_id': 'mission', 'fact': 'scrape-down',
                                     'freshness': 'historical-window-only'}]}
    html = render_html(result, 'diagnosis.json')
    assert 'Historical window only' in html and 'historical window only' in html
    assert 'too old to describe current health' in html
