"""Controlled lifecycle publication checks; these are not live E2E evidence."""
import json
import time

import pytest

from devsecops.factory.state import StateError, _canonical, _sha
from devsecops.factory.terminal_evidence import publish_terminal, verify_terminal
from tests.test_factory_lifecycle import setup, complete

__all__ = ['setup']


def publish(engine):
    with engine.journal.lifecycle_lock():
        return publish_terminal(engine.journal)


def test_final_publication_is_bound_controlled_and_idempotent(setup):
    engine, _, _, _ = setup
    complete(engine)
    observed = publish(engine)
    root = engine.journal.run_dir
    result = observed['result']
    assert result['terminal'] and not result['canonical_success']
    assert result['execution_mode'] == 'controlled-test'
    assert len(result['phase_records']) == 33
    assert result['cleanup']['environment_released'] is True
    assert result['native_identity'] is None
    members = {row['path'] for row in observed['manifest']['files']}
    assert 'state.json' in members and 'journal/00000001.json' in members
    assert 'e2e-result.json' not in members and 'evidence-manifest.json' not in members
    before = {name: (root/name).stat().st_ino for name in ('e2e-result.json','evidence-manifest.json')}
    assert publish(engine) == observed
    assert verify_terminal(root, expected_result_sha256=_sha((root/'e2e-result.json').read_bytes())) == observed
    assert before == {name: (root/name).stat().st_ino for name in before}


def test_nonterminal_or_unlocked_run_cannot_publish(setup):
    engine, _, _, _ = setup
    with pytest.raises(StateError, match='lock'):
        publish_terminal(engine.journal)
    with pytest.raises(StateError, match='finalized'):
        publish(engine)
    assert not (engine.journal.run_dir/'e2e-result.json').exists()


def test_verification_never_repairs_stale_projection(setup):
    engine, _, _, _ = setup
    complete(engine)
    publish(engine)
    path = engine.journal.run_dir/'state.json'
    state = json.loads(path.read_text())
    state['updated_at'] = '2000-01-01T00:00:00+00:00'
    raw = _canonical(state)
    path.write_bytes(raw)
    with pytest.raises(StateError, match='projection'):
        verify_terminal(engine.journal.run_dir)
    assert path.read_bytes() == raw


@pytest.mark.parametrize('target', ['artifact','event','manifest','result'])
def test_tampered_evidence_is_rejected(setup, target):
    engine, _, _, _ = setup
    complete(engine)
    doc = publish(engine)
    root = engine.journal.run_dir
    if target == 'artifact':
        path = root / doc['result']['cleanup']['validation']['path']
    elif target == 'event':
        path = root/'journal/00000001.json'
    elif target == 'manifest':
        path = root/'evidence-manifest.json'
    else:
        path = root/'e2e-result.json'
    path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(StateError):
        verify_terminal(root)


def test_manifest_only_interruption_can_finish_without_replacement(setup):
    engine, _, _, _ = setup
    complete(engine)
    original = publish(engine)
    root=engine.journal.run_dir
    inode=(root/'evidence-manifest.json').stat().st_ino
    (root/'e2e-result.json').unlink()
    assert publish(engine)==original
    assert (root/'evidence-manifest.json').stat().st_ino==inode


def test_conflicting_partial_publication_is_not_overwritten(setup):
    engine, _, _, _ = setup
    complete(engine)
    path=engine.journal.run_dir/'evidence-manifest.json'
    (engine.journal.run_dir/'e2e-result.json').unlink()
    path.write_bytes(b'{}\n')
    with pytest.raises(StateError, match='differs'):
        publish(engine)
    assert path.read_bytes()==b'{}\n'
    assert not (engine.journal.run_dir/'e2e-result.json').exists()


def test_symlink_and_deadline_fail_closed(setup, tmp_path):
    engine, _, _, _ = setup
    complete(engine)
    publish(engine)
    alias=tmp_path/'alias'
    alias.symlink_to(engine.journal.run_dir, target_is_directory=True)
    with pytest.raises(OSError):
        verify_terminal(alias)
    with pytest.raises(TimeoutError):
        verify_terminal(engine.journal.run_dir, deadline=time.monotonic()-1)
