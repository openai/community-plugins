"""Adversarial capability checks; controlled issuance is never live evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
import pytest

from devsecops.factory import locked_images as images
from devsecops.factory.state import StateError, _sha


@pytest.fixture
def issued(tmp_path):
    path = tmp_path / 'actual-acquisition.json'
    raw = b'{"controlled-test":"not-real-registry-evidence"}'
    path.write_bytes(raw)
    journal = SimpleNamespace(state={'run_id': 'controlled-run', 'identities': {'selected': 'hash'}, 'environment_id': 'owned-test'})
    context = SimpleNamespace(run_id='controlled-run', identities={'selected': 'hash'})
    now = datetime.now(timezone.utc)
    subject = {'observed_at': (now - timedelta(seconds=1)).isoformat(),
               'expires_at': (now + timedelta(seconds=30)).isoformat(), 'mapping_receipt_sha256': 'a' * 64}
    binding = {'run_id': context.run_id, 'identities': dict(context.identities), 'environment_id': 'owned-test',
               'reference': deepcopy(subject), 'mission_base': deepcopy(subject)}
    origin = object.__new__(images.VerifiedLockedImages)
    images._ORIGINS[origin] = {'pid': os.getpid(), 'operations': SimpleNamespace(journal=journal, _admit=lambda _: None),
                             'binding': binding, 'files': {path: (_sha(raw), len(raw))}}
    return origin, journal, context, path


def test_runtime_mapping_return_is_detached_and_rechecks_original_evidence(issued):
    origin, journal, context, path = issued
    binding = images.require_locked_images(origin, journal=journal, context=context)
    binding['reference']['mapping_receipt_sha256'] = 'b' * 64
    assert images.require_locked_images(origin, journal=journal, context=context)['reference']['mapping_receipt_sha256'] == 'a' * 64
    path.write_bytes(b'changed')
    with pytest.raises(StateError, match='changed'):
        images.require_locked_images(origin, journal=journal, context=context)


def test_original_origin_expires_without_silent_refresh(issued):
    origin, journal, context, _ = issued
    images._ORIGINS[origin]['binding']['reference']['expires_at'] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with pytest.raises(StateError, match='expired'):
        images.require_locked_images(origin, journal=journal, context=context)


def test_json_or_foreign_journal_cannot_restore_image_origin(issued):
    origin, _, context, _ = issued
    with pytest.raises(StateError):
        images.VerifiedLockedImages()
    for candidate, journal in (({}, object()), (origin, object())):
        with pytest.raises(StateError, match='same-process'):
            images.require_locked_images(candidate, journal=journal, context=context)


def test_historical_export_retains_expired_bytes_without_renewing_live_origin(issued):
    origin, journal, context, path = issued
    images._ORIGINS[origin]['binding']['reference']['expires_at'] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    assert images.locked_image_evidence_artifacts(origin, journal=journal) == (path,)
    with pytest.raises(StateError, match='expired'):
        images.require_locked_images(origin, journal=journal, context=context)


def test_historical_export_still_rejects_modified_bytes_and_different_run(issued):
    origin, journal, _, path = issued
    journal.state['run_id'] = 'different'
    with pytest.raises(StateError, match='another run'):
        images.locked_image_evidence_artifacts(origin, journal=journal)
    journal.state['run_id'] = 'controlled-run'
    path.write_bytes(b'changed')
    with pytest.raises(StateError, match='changed'):
        images.locked_image_evidence_artifacts(origin, journal=journal)


def test_historical_export_cannot_admit_serialized_or_foreign_process_origin(issued):
    origin, journal, _, _ = issued
    with pytest.raises(StateError, match='same-process'):
        images.locked_image_evidence_artifacts({}, journal=journal)
    images._ORIGINS[origin]['pid'] = -1
    with pytest.raises(StateError, match='same-process'):
        images.locked_image_evidence_artifacts(origin, journal=journal)
