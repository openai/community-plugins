"""Controlled local preflight classification; no foundation or remote dispatch."""
from copy import deepcopy
from pathlib import Path

import pytest

from devsecops.factory import doctor, owned_linux as owned
from devsecops.factory.state import StateError, _canonical, _sha
from tests.test_argocd import ca, server  # noqa: F401
from tests.test_owned_linux import READ_TOKEN, execute
from tests import test_owned_linux

setup = test_owned_linux.setup


def select_foundation(setup, external=()):
    """Select schema-valid declarations; these pins never authorize dispatch."""
    selected = setup['values']['config']
    path = str(setup['ca'])
    pin = {'path': path, 'sha256': _sha(setup['ca'].read_bytes())}
    selected['runtime']['foundation'] = {
        'kind': 'retained-tcg-v1', 'host': 'controlled-host',
        'remote_lab': '/home/operator/.local/share/devsecops/labs/controlled',
        'private_state_directory': str(setup['ca'].parent/'unused-foundation-state'),
        'tool_image': 'sha256:'+'a'*64, 'provision_run_id': 'controlled-original',
        'original_ca_sha256': 'b'*64, 'source_receipt': deepcopy(pin),
        'transport': {**{name: deepcopy(pin) for name in
            ('config', 'known_hosts', 'certificate', 'identity', 'proxy')},
            'identity_agent': str(setup['ca'].parent/'unused-agent'),
            'host_key_sha256': 'c'*64, 'verification_basis': 'operator-verified'},
        'retained_dependencies': 'preserve',
    }
    setup['values']['environment'] = {
        'schema_version': 1, 'environment_id': selected['environment'],
        'services': [{'id': row['id'], 'disposition':
            'retained-foundation' if row['id'] == 'kubernetes' else
            'external-shared' if row['id'] in {*external, 'registry1'} else 'planned-owned'}
            for row in selected['services']],
    }


def forbidden(*args, **kwargs):
    pytest.fail('Deferred services must not construct transport or snapshot transport CA bytes')


def test_deferred_services_skip_transport_but_keep_local_input_checks_and_unverified_ownership(setup, monkeypatch):
    select_foundation(setup)
    monkeypatch.setattr(owned, 'HTTPSReadClient', forbidden)
    monkeypatch.setattr(owned, '_snapshot', forbidden)
    # Local inspection remains truthful even when an unavailable service is deferred.
    monkeypatch.delenv('DIAGNOSTIC_READ_GITLAB')
    inspected = []
    file_hash = doctor._file_hash
    def record_hash(path, **kwargs):
        inspected.append(path)
        return file_hash(path, **kwargs)
    monkeypatch.setattr(doctor, '_file_hash', record_hash)
    outcome, report, journal = execute(setup)
    assert outcome.status == 'incomplete' and outcome.mutation_effect == 'not_applied'
    assert not outcome.assertions and not report['canonical_success']
    assert not setup['server']['requests'] and setup['ca'] in inspected
    assert report['local_checks']['config_sha256'] == _sha(_canonical(setup['values']['config']))
    assert 'credential-missing:gitlab:read_auth' in {row['code'] for row in report['local_checks']['gaps']}
    rows = {row['service']: row for row in report['services']}
    for name, row in rows.items():
        assert row['ownership'] == 'unverified' and row['authentication'] == 'unverified'
        assert row['bootstrap'] == row['cleanup'] == 'not-performed'
        assert row['declaration_authority'] == 'operator-selected'
        assert not row['tls_destination_verified'] and not row['receipts'] and not row['observations']
        if name != 'registry1':
            assert row['observation_status'] == 'deferred' and row['credential_sent'] == 'not-sent'
            assert row['gaps'] == ['retained-foundation-readiness-unobserved' if name == 'kubernetes'
                                   else 'planned-owned-service-bootstrap-unimplemented']
    assert rows['registry1']['gaps'] == ['service-diagnostic-unimplemented']
    assert all(row['ownership'] == 'unverified' for row in report['service_inventory'])
    assert not journal.state['environment_reserved'] and journal.state['operations'] == []
    assert not Path(setup['values']['config']['runtime']['foundation']['private_state_directory']).exists()


def test_only_declared_external_services_receive_existing_bounded_reads(setup):
    select_foundation(setup, external=('gitlab', 'argocd', 'prometheus'))
    responses = setup['server']['responses']
    setup['server']['responses'] = [responses[index] for index in (0, 1, 5)]
    outcome, report, journal = execute(setup)
    assert [row['path'] for row in setup['server']['requests']] == [
        '/api/version', '/api/v4/projects/7', '/api/v1/status/buildinfo']
    assert all(row['authorization'] == 'Bearer '+READ_TOKEN for row in setup['server']['requests'])
    rows = {row['service']: row for row in report['services']}
    for name in ('gitlab', 'argocd', 'prometheus'):
        assert rows[name]['disposition'] == 'external-shared'
        assert rows[name]['observation_status'] == 'complete' and rows[name]['credential_sent'] == 'yes'
        assert rows[name]['ownership'] == rows[name]['authentication'] == rows[name]['read_grants'] == 'unverified'
    assert rows['kubernetes']['observation_status'] == rows['registry']['observation_status'] == 'deferred'
    assert outcome.status == 'incomplete' and outcome.mutation_effect == 'not_applied'
    assert not journal.state['operations'] and not journal.state['environment_reserved']


@pytest.mark.parametrize('change', [
    'omit', 'duplicate', 'conflicting-duplicate', 'unknown-service', 'unconfigured-service',
    'extra-command', 'missing-inventory',
    'kubernetes-shared', 'registry1-owned', 'other-retained', 'bad-disposition',
])
def test_invalid_complete_inventory_stops_before_local_inspection_or_any_external_request(setup, monkeypatch, change):
    select_foundation(setup, external=('gitlab', 'argocd', 'prometheus'))
    environment = setup['values']['environment']
    rows = environment['services']
    if change == 'omit':
        rows.pop()
    elif change == 'duplicate':
        rows.append(deepcopy(rows[0]))
    elif change == 'conflicting-duplicate':
        rows.append({'id': 'gitlab', 'disposition': 'planned-owned'})
    elif change == 'unknown-service':
        rows.append({'id': 'unconfigured', 'disposition': 'external-shared'})
    elif change == 'unconfigured-service':
        rows.append({'id': 'jenkins', 'disposition': 'external-shared'})
    elif change == 'extra-command':
        rows[-1]['command'] = 'untrusted command must never run'
    elif change == 'missing-inventory':
        environment.pop('services')
    else:
        name, disposition = {
            'kubernetes-shared': ('kubernetes', 'external-shared'),
            'registry1-owned': ('registry1', 'planned-owned'),
            'other-retained': ('gitlab', 'retained-foundation'),
            'bad-disposition': ('gitlab', 'observed-owned'),
        }[change]
        next(row for row in rows if row['id'] == name)['disposition'] = disposition
    monkeypatch.setattr(owned, 'inspect', lambda *a, **kw: pytest.fail('Inventory must be admitted before doctor'))
    monkeypatch.setattr(owned, 'HTTPSReadClient', forbidden)
    outcome, report, journal = execute(setup)
    assert report['preflight_stage'] == 'foundation-service-inventory'
    assert report['gaps'] == ['preflight-input-package-or-budget-unavailable']
    assert not report['services'] and 'service_inventory' not in report and 'local_checks' not in report
    assert outcome.status == 'incomplete' and outcome.mutation_effect == 'not_applied'
    assert not setup['server']['requests'] and not journal.state['operations']


@pytest.mark.parametrize('change', ['loader', 'content'])
def test_inventory_implementation_is_bound_to_installed_package_before_reads(setup, monkeypatch, change):
    select_foundation(setup)
    selected = setup['package']/'src/devsecops/factory/service_inventory.py'
    if change == 'loader':
        outside = setup['ca'].parent/'same-inventory-bytes.py'
        outside.write_bytes(selected.read_bytes())
        monkeypatch.setattr(owned.service_inventory, '__file__', str(outside))
    else:
        selected.write_bytes(selected.read_bytes()+b'\n# changed\n')
    monkeypatch.setattr(owned, 'inspect', lambda *a, **kw: pytest.fail('Package must be admitted before doctor'))
    outcome, report, _ = execute(setup)
    assert report['preflight_stage'] == 'package-validation' and not report['services']
    assert outcome.status == 'incomplete' and not setup['server']['requests']


def test_missing_required_service_is_not_filled_by_a_declaration(setup):
    select_foundation(setup)
    setup['values']['config']['services'] = [row for row in setup['values']['config']['services']
                                            if row['id'] != 'prometheus']
    setup['values']['environment']['services'] = [row for row in setup['values']['environment']['services']
                                                 if row['id'] != 'prometheus']
    outcome, report, _ = execute(setup)
    assert outcome.status == 'incomplete' and not setup['server']['requests']
    assert 'required-service-not-configured:prometheus' in report['gaps']
    assert 'service-missing:prometheus' in {row['code'] for row in report['local_checks']['gaps']}


def test_shared_validator_rejects_foreign_environment_before_projecting_declarations(setup):
    select_foundation(setup)
    environment = deepcopy(setup['values']['environment'])
    environment['environment_id'] = 'other-environment'
    with pytest.raises(StateError):
        owned.service_inventory.classify_foundation_services(environment, setup['values']['config'])
