"""Controlled actor/log composition; these fixtures do not qualify a cluster."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from devsecops.factory.state import StateError
from devsecops.runtime.context import canonical_bytes, digest, parse_time, seal
from devsecops.runtime.probe_observations import (ProbeObservation, prepare_probe_actor,
    validate_probe_plan, _readback, parse_probe_logs, probe_observation_artifacts)
from test_runtime_recovery import fixture, at


def actors():
    bundles, contract, evidence = fixture()
    output = []
    for phase, offset in (('baseline', 0), ('fault', 60), ('recovery', 120)):
        context = bundles[phase]['context_snapshot']
        for source in evidence['inventory']['probe_sources']:
            selected = [c for c in contract['probe_cases'] if c['phase'] == phase and c['source_id'] == source['id']]
            if not selected:
                continue
            requests, receipts = [], []
            for case in selected:
                receipt = deepcopy(evidence['http:'+case['id']])
                receipt['execution_mode'] = 'operator'  # Deliberately synthetic input to pure parser.
                receipt['request']['limits']['total_seconds'] = .5
                receipt['request_artifact']['canonical_sha256'] = digest(receipt['request'])
                receipt = seal(receipt, 'receipt_sha256')
                template = deepcopy(receipt['request'])
                template['source']['expected']['uid'] = 'pending'
                requests.append({'request': template, 'run_at': receipt['started_at']})
                receipts.append(receipt)
            services = {row['target_id']: {'metadata': {'namespace': 'demo', 'name': row['target_id'], 'uid': row['uid']},
                'spec': {'clusterIPs': row['cluster_ips'], 'ports': [{'port': 8080}, {'port': 9090}]}}
                for row in evidence['inventory']['services']}
            plan = prepare_probe_actor(context, source_id=source['id'], namespace=source['namespace'],
                name=source['name'], image='example.test/mission@sha256:'+'a'*64, pod_labels=source['pod_labels'],
                requests=requests, not_after=at(offset+30), services=services)
            pod, cm = validate_probe_plan(plan, context)
            pod['metadata']['uid'] = source['uid']
            cm['metadata']['uid'] = 'cm-'+source['id']
            pod['status'] = {'containerStatuses': [{'name': 'probe', 'restartCount': 0, 'imageID': source['image_id']}]}
            ns = {'metadata': {'name': source['namespace'], 'uid': source['namespace_uid'], 'labels': source['namespace_labels']}}
            projected = _readback(plan, pod, cm, ns, services)
            output.append((context, plan, pod, cm, ns, services, projected, receipts))
    return output


def test_all_fourteen_positive_negative_probe_inputs_keep_original_scope_and_images():
    seen = {}
    for context, plan, pod, cm, ns, services, source, receipts in actors():
        raw = b''.join(canonical_bytes(r) for r in receipts)
        result = parse_probe_logs(raw, source=source, observed_before=parse_time(at(-1)), observed_after=parse_time(at(160)))
        seen.update(result)
        assert pod['spec']['containers'][0]['image'] == 'example.test/mission@sha256:'+'a'*64
        assert source['image_id'] == pod['status']['containerStatuses'][0]['imageID']
        assert cm['immutable'] and pod['spec']['automountServiceAccountToken'] is False
        assert {r['request']['phase'] for r in result.values()} == {context['intent']['phase']}
    assert len(seen) == 14
    denied = [r for name, r in seen.items() if 'denied' in name]
    assert len(denied) == 4 and all(r['observation']['status'] is None for r in denied)
    # Pure parser output is never accepted as a production capability.
    with pytest.raises(StateError):
        probe_observation_artifacts(seen)
    with pytest.raises(StateError):
        ProbeObservation()


@pytest.mark.parametrize('replace_uid', [False, True])
def test_pending_actor_observation_only_reads_until_actual_image_id_before_fixed_schedule(monkeypatch, replace_uid):
    import devsecops.runtime.probe_observations as module
    context, plan, pod, cm, ns, services, expected, _ = actors()[0]
    pending = deepcopy(pod)
    pending['status'] = {'phase': 'Pending'}
    calls = []
    class Client:
        def get_json(self, path):
            if '/pods/' in path:
                calls.append(path)
                result = deepcopy(pending if len(calls) == 1 else pod)
                if replace_uid and len(calls) > 1:
                    result['metadata']['uid'] = 'replacement'
                return result
            if '/configmaps/' in path:
                return cm
            if '/services/' in path:
                return next(v for v in services.values() if v['metadata']['name'] == path.rsplit('/', 1)[1])
            return ns
    monkeypatch.setattr(module, 'clock', lambda: parse_time(at(-1)))
    monkeypatch.setattr(module.time, 'sleep', lambda seconds: None)
    scope = {'namespaces': [ns['metadata']['name'], 'demo'], 'applications': ['mission', 'reference']}
    if replace_uid:
        with pytest.raises(StateError, match='replaced'):
            module._read_actor(plan, Client(), scope, await_image=True)
    else:
        assert module._read_actor(plan, Client(), scope, await_image=True) == expected
    assert len(calls) == 2


@pytest.mark.parametrize('damage', ['command', 'args', 'host-network', 'sidecar', 'runner', 'uid-env', 'image'])
def test_plan_cannot_smuggle_arbitrary_execution(damage):
    context, plan, *_ = actors()[0]
    pod, cm = json.loads(plan.pod), json.loads(plan.configmap)
    if damage == 'command':
        pod['spec']['containers'][0]['command'] = ['sh', '-c', 'id']
    elif damage == 'args':
        pod['spec']['containers'][0]['args'] = ['--extra']
    elif damage == 'host-network':
        pod['spec']['hostNetwork'] = True
    elif damage == 'sidecar':
        pod['spec']['containers'].append(deepcopy(pod['spec']['containers'][0]))
    elif damage == 'runner':
        cm['data']['runner.py'] += '\nprint("changed")\n'
    elif damage == 'uid-env':
        pod['spec']['containers'][0]['env'][-1] = {'name': 'DEVSECOPS_PROBE_POD_UID', 'value': 'forged'}
    else:
        pod['spec']['containers'][0]['image'] = 'example.test/mission:latest'
    with pytest.raises((StateError, ValueError)):
        validate_probe_plan(replace(plan, pod=canonical_bytes(pod), configmap=canonical_bytes(cm)), context)


@pytest.mark.parametrize('damage', ['restart', 'uid', 'labels', 'image-id', 'config', 'service-ip', 'sidecar'])
def test_observed_actor_replacement_or_mutation_is_not_accepted(damage):
    _, plan, pod, cm, ns, services, source, _ = actors()[0]
    if damage == 'restart':
        pod['status']['containerStatuses'][0]['restartCount'] = 1
    elif damage == 'uid':
        pod['metadata']['uid'] = ''
    elif damage == 'labels':
        pod['metadata']['labels']['app'] = 'different'
    elif damage == 'image-id':
        pod['status']['containerStatuses'][0]['imageID'] = 'tag:latest'
    elif damage == 'config':
        cm['data']['runner.py'] += '\nprint("different")'
    elif damage == 'service-ip':
        services['mission']['spec']['clusterIPs'] = ['10.96.0.99']
    else:
        pod['spec']['containers'].append(deepcopy(pod['spec']['containers'][0]))
    with pytest.raises(StateError):
        _readback(plan, pod, cm, ns, services)


@pytest.mark.parametrize('damage', ['incomplete', 'duplicate', 'other-run', 'other-uid', 'before-schedule', 'stale', 'changed-body'])
def test_probe_logs_keep_incompleteness_freshness_and_exact_request_binding(damage):
    *_, source, receipts = actors()[0]
    if damage == 'incomplete':
        receipts.pop()
    elif damage == 'duplicate':
        receipts.append(deepcopy(receipts[-1]))
    elif damage == 'other-run':
        receipts[0]['request']['run_id'] = 'other-run'
    elif damage == 'other-uid':
        receipts[0]['source_identity']['claims']['uid'] = 'replaced'
    elif damage == 'before-schedule':
        receipts[0]['started_at'] = at(-2)
    elif damage == 'stale':
        receipts[0]['finished_at'] = at(200)
    else:
        receipts[0]['request']['expectation']['body_sha256'] = 'e'*64
    receipts = [seal(r, 'receipt_sha256') for r in receipts]
    with pytest.raises((StateError, ValueError)):
        parse_probe_logs(b''.join(canonical_bytes(r) for r in receipts), source=source,
                         observed_before=parse_time(at(-1)), observed_after=parse_time(at(160)))
