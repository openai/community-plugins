"""Controlled expected scrape outage: absent counter is not absent up evidence."""
from copy import deepcopy

import pytest

from devsecops.runtime.context import seal
from test_runtime_recovery import fixture, run, condition


def missing_counter(phase='fault'):
    bundles, contract, evidence = fixture()
    bundle = bundles[phase]
    report = bundle['targets']['mission']['prometheus']
    report['observations'].pop('functional')
    report['observations']['collection_freshness']['decision'] = 'unknown'
    report['observations']['collection_freshness']['series']['mission_events_total'] = {
        'last_source_timestamp': None, 'age_seconds': None, 'fresh': None}
    report.update(execution_status='partial', decision='unknown')
    for code in ('prometheus-missing-or-multiple-series', 'runtime-prometheus-source-time-unavailable'):
        report['gaps'].append({'code': code, 'required': True})
        bundle['gaps'].append({'code': code, 'required': True, 'target_id': 'mission'})
    bundles[phase] = seal(bundle, 'bundle_sha256')
    return bundles, contract, evidence


def test_exact_fresh_fault_up_series_can_prove_outage_when_counter_disappears():
    result = run(missing_counter())
    outage = condition(result, 'telemetry-outage', 'fault', 'mission')
    assert outage['observed_state'] == 'recovered'
    assert not any(g.get('input') == 'fault' and g['code'] in {'telemetry-provider-coverage-incomplete',
        'source-scrapes-incomplete', 'unresolved-collection-evidence-gap', 'telemetry-samples-incomplete'} for g in result['gaps'])
    assert result['execution_mode'] == 'controlled-test' and result['recovery'] == 'unknown'


@pytest.mark.parametrize('damage', ['missing-up', 'old-up', 'wrong-target', 'denied', 'image-changed', 'wrong-window'])
def test_fault_counter_exception_never_hides_missing_or_denied_evidence(damage):
    value = missing_counter()
    bundle = value[0]['fault']
    report = bundle['targets']['mission']['prometheus']
    if damage == 'missing-up':
        report['observations']['scrape']['samples'].pop()
    elif damage == 'old-up':
        report['observations']['scrape']['samples'][0]['source_timestamp'] -= 60
    elif damage == 'wrong-target':
        report['scope']['instance'] = '10.0.0.99:9090'
    elif damage == 'denied':
        report['gaps'].append({'code': 'prometheus-denied', 'required': True})
    elif damage == 'image-changed':
        report['gaps'].append({'code': 'prometheus-image-identity-or-restart-changed', 'required': True})
    else:
        report['observations']['window']['start'] -= 60
    value[0]['fault'] = seal(bundle, 'bundle_sha256')
    result = run(value)
    assert any(g.get('input') == 'fault' and g['code'] == 'telemetry-provider-coverage-incomplete' for g in result['gaps'])


@pytest.mark.parametrize('phase', ['baseline', 'recovery'])
def test_counter_is_still_required_before_fault_and_after_repair(phase):
    value = deepcopy(missing_counter(phase))
    result = run(value)
    assert condition(result, 'functional-telemetry', phase, 'mission')['observed_state'] == 'unknown'
    assert any(g.get('input') == phase and g['code'] == 'telemetry-provider-coverage-incomplete' for g in result['gaps'])
