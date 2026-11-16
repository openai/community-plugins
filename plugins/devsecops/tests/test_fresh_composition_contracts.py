"""Pure fresh/retained admission and cleanup distinctions; no guest actions."""
from copy import deepcopy

import pytest

from devsecops.factory.contracts import validate_document
from devsecops.factory.owned_cleanup import OwnedCleanupCoordinator
from devsecops.factory.service_inventory import classify_foundation_services
from devsecops.factory.state import StateError
from tests.test_factory_contracts import config
from tests.test_fresh_foundation import lab  # noqa: F401


def test_fresh_config_and_inventory_select_owned_cluster_only(lab):  # noqa: F811
    value = config()
    value['runtime']['foundation'] = lab.recipe
    validate_document(value, 'factory-config')
    environment = {'schema_version': 1, 'environment_id': value['environment'],
        'services': [{'id': row['id'], 'disposition': 'external-shared' if row['id'] == 'registry1'
                      else 'planned-owned'} for row in value['services']]}
    assert all(row['ownership'] == 'unverified' for row in classify_foundation_services(environment, value))
    kube = next(row for row in environment['services'] if row['id'] == 'kubernetes')
    kube['disposition'] = 'retained-foundation'
    with pytest.raises(StateError):
        classify_foundation_services(environment, value)
    value['runtime'].pop('foundation')
    assert next(row for row in classify_foundation_services(environment, value)
                if row['id'] == 'kubernetes')['disposition'] == 'retained-foundation'


@pytest.mark.parametrize('change', ['asset-pin', 'source-alias', 'architecture', 'retained-field'])
def test_fresh_semantics_checked_before_any_driver_dispatch(lab, change):  # noqa: F811
    value = config()
    value['runtime']['foundation'] = deepcopy(lab.recipe)
    recipe = value['runtime']['foundation']
    if change == 'asset-pin':
        recipe['boot_image']['sha256'] = '0' * 64
    elif change == 'source-alias':
        recipe['ssh_pki']['operator_public']['path'] = recipe['ssh_pki']['host_public']['path']
    elif change == 'architecture':
        value['runtime']['architecture'] = 'arm64'
    else:
        recipe['retained_dependencies'] = 'preserve'
    with pytest.raises(ValueError):
        validate_document(value, 'factory-config')


@pytest.mark.parametrize('change', [None, 'live-disk', 'changed-source', 'unknown-operation', 'missing-resources', 'unknown-disposition'])
def test_fresh_cleanup_requires_removed_owned_resources_and_preserved_sources(change):
    report = {'kind': 'owned-fresh-rke2-foundation', 'complete_cleanup': True,
        'mutation_effect': 'applied', 'unresolved_operations': [],
        'resources': [{'kind': 'guest-disk', 'disposition': 'remove', 'state': 'absent'},
                      {'kind': 'source-assets', 'disposition': 'preserve', 'state': 'preserved'}]}
    if change == 'live-disk':
        report['resources'][0]['state'] = 'present'
    elif change == 'changed-source':
        report['resources'][1]['state'] = 'changed'
    elif change == 'unknown-operation':
        report['unresolved_operations'] = ['unobserved']
    elif change == 'missing-resources':
        report['resources'] = []
    elif change == 'unknown-disposition':
        report['resources'][0]['disposition'] = 'unclassified'
    assert OwnedCleanupCoordinator._resolved('foundation', report) is (change is None)
