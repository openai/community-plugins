"""Actual loopback HTTPS GET routes with synthetic actors and controlled time."""
from copy import deepcopy
import json

import pytest

from test_argocd import ca, server  # noqa: F401
from test_probe_observations import actors
from test_runtime_recovery import at
from devsecops.adapters.http import APIError
from devsecops.factory.state import StateError
from devsecops.runtime import probe_observations as module
from devsecops.runtime.context import canonical_bytes, digest, freeze_runtime_context, parse_time, seal


def prepared(server, monkeypatch):  # noqa: F811
    context, old, _, _, namespace, services, _, receipts = actors()[0]
    authority = {'config': deepcopy(context['factory_config']), 'lock': deepcopy(context['factory_lock']),
                 'intent': deepcopy(context['intent'])}
    service = next(s for s in authority['config']['services'] if s['id'] == 'kubernetes')
    service.update(origin=server['origin'], ca_file=str(server['ca']),
                   read_auth={'mode': 'bearer', 'token_env': 'CANONICAL_PROBE_READ'})
    service['scope']['namespaces'].append('monitoring')
    monkeypatch.setenv('CANONICAL_PROBE_READ', 'CONTROLLED-READ-CANARY')
    authority['intent']['identities']['config_sha256'] = digest(authority['config'])
    context = freeze_runtime_context(authority['config'], authority['lock'], authority['intent'])
    batch = json.loads(json.loads(old.configmap)['data']['batch.json'])
    for item, receipt in zip(batch['requests'], receipts):
        item['request']['identities'] = deepcopy(authority['intent']['identities'])
        receipt['request']['identities'] = deepcopy(authority['intent']['identities'])
        receipt['request_artifact']['canonical_sha256'] = digest(receipt['request'])
    receipts = [seal(row, 'receipt_sha256') for row in receipts]
    plan = module.prepare_probe_actor(context, source_id='baseline-allowed', namespace='monitoring', name='allowed',
        image='example.test/mission@sha256:'+'a'*64, pod_labels={'app': 'prometheus'},
        requests=batch['requests'], not_after=batch['not_after'], services=services)
    pod, cm = module.validate_probe_plan(plan, context)
    pod['metadata']['uid'] = 'pod-allowed'
    cm['metadata']['uid'] = 'cm-allowed'
    pod['status'] = {'containerStatuses': [{'name': 'probe', 'restartCount': 0, 'imageID': 'containerd://sha256:'+'a'*64}]}
    responses = [{'json': value} for value in (pod, cm, namespace, services['mission'], services['reference'])]
    return context, authority, plan, responses, b''.join(canonical_bytes(r) for r in receipts)


def test_original_actor_readback_and_logs_use_only_exact_authenticated_get_routes(server, monkeypatch):  # noqa: F811
    context, authority, plan, responses, logs = prepared(server, monkeypatch)
    monkeypatch.setattr(module, 'clock', lambda: parse_time(at(0)))
    server['responses'] = deepcopy(responses)
    origin = module.start_probe_observation(context, authority=authority, plan=plan)
    monkeypatch.setattr(module, 'clock', lambda: parse_time(at(30)))
    server['responses'] = [{'raw': logs, 'content_type': 'text/plain'}, *deepcopy(responses)]
    result = module.finish_probe_observation(origin)
    source, retained = module.probe_observation_artifacts(origin)
    assert result == retained and len(result) == 4 and source['uid'] == 'pod-allowed'
    paths = [row['path'] for row in server['requests']]
    assert len(paths) == 11
    assert all(path.startswith('/api/v1/namespaces/') for path in paths)
    assert paths[5].startswith('/api/v1/namespaces/monitoring/pods/allowed/log?')
    assert all('/exec' not in path and '/attach' not in path for path in paths)
    assert all(row['authorization'] == 'Bearer CONTROLLED-READ-CANARY' for row in server['requests'])
    assert 'CONTROLLED-READ-CANARY' not in json.dumps(result)
    with pytest.raises(StateError, match='already complete'):
        module.finish_probe_observation(origin)


def test_denied_read_never_produces_original_actor_observation(server, monkeypatch):  # noqa: F811
    context, authority, plan, _, _ = prepared(server, monkeypatch)
    monkeypatch.setattr(module, 'clock', lambda: parse_time(at(0)))
    server['responses'] = [{'status': 403, 'raw': b'PRIVATE-DENIAL-DIAGNOSTIC'}]
    with pytest.raises(APIError) as caught:
        module.start_probe_observation(context, authority=authority, plan=plan)
    assert caught.value.status == 403 and 'PRIVATE-DENIAL-DIAGNOSTIC' not in str(caught.value)
    assert len(server['requests']) == 1
