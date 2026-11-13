"""The additive deadline constrains existing render subprocesses and publication."""
import json
from pathlib import Path
import sys
import time

import pytest

from devsecops import render
from devsecops.process import ToolError


def test_real_version_command_is_cancelled_at_remaining_deadline(tmp_path):
    start = time.monotonic()
    with pytest.raises(ToolError, match='time limit'):
        render.tool_identity(Path(sys.executable), ['-c', 'import time; time.sleep(60)'], tmp_path,
                             deadline=start+.1)
    assert time.monotonic()-start < 3


def test_version_cannot_pass_after_command_crosses_deadline(tmp_path, monkeypatch):
    binary = tmp_path / 'tool'
    binary.write_bytes(b'pinned controlled tool bytes')
    clock, timeouts = [10.0], []
    monkeypatch.setattr(render.time, 'monotonic', lambda: clock[0])
    def run(*args, **kwargs):
        timeouts.append(kwargs['timeout'])
        clock[0] = 12
        return 0, b'v1.2.3', b'', {}
    monkeypatch.setattr(render, 'run', run)
    with pytest.raises(ValueError, match='deadline'):
        render.tool_identity(binary, ['version'], tmp_path, deadline=11)
    assert timeouts == [1]


def test_helm_does_not_start_template_after_lint_exhausts_deadline(tmp_path, monkeypatch):
    chart = tmp_path / 'chart'
    chart.mkdir()
    (chart / 'Chart.yaml').write_text('apiVersion: v2\nname: controlled\nversion: 1.0.0\n')
    (chart / 'values.yaml').write_text('{}\n')
    target = {'type': 'helm', 'path': 'chart', 'namespace': 'demo', 'release': 'demo',
              'kubernetes_version': '1.34.0', 'name': 'controlled'}
    clock, calls = [10.0], []
    monkeypatch.setattr(render.time, 'monotonic', lambda: clock[0])
    def run(argv, **kwargs):
        calls.append((argv, kwargs['timeout']))
        clock[0] = 12
        return 0, b'', b'', {}
    monkeypatch.setattr(render, 'run', run)
    with pytest.raises(ValueError, match='deadline'):
        render.collect(tmp_path, target, Path('/approved/helm'), tmp_path, deadline=11)
    assert len(calls) == 1 and calls[0][0][1] == 'lint' and calls[0][1] == 1


def test_schema_success_after_deadline_is_not_accepted(tmp_path, monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(render.time, 'monotonic', lambda: clock[0])
    def run(*args, **kwargs):
        assert kwargs['timeout'] == 1
        clock[0] = 12
        return 0, json.dumps({'summary': {'valid': 1, 'invalid': 0, 'errors': 0, 'skipped': 0}}).encode(), b'', {}
    monkeypatch.setattr(render, 'run', run)
    with pytest.raises(ValueError, match='deadline'):
        render.validate([{'id': 'v1/Service/demo/example', 'body': {'apiVersion': 'v1'}}],
            {'kubernetes_version': '1.34.0'}, Path('/approved/kubeconform'), tmp_path, tmp_path, deadline=11)


def test_default_version_call_retains_existing_per_call_cap(tmp_path, monkeypatch):
    binary = tmp_path / 'tool'
    binary.write_bytes(b'controlled')
    def run(*args, **kwargs):
        assert kwargs['timeout'] == 45
        return 0, b'v1.2.3', b'', {}
    monkeypatch.setattr(render, 'run', run)
    assert render.tool_identity(binary, ['version'], tmp_path)['version'] == 'v1.2.3'
