"""Complete immutable GitLab chart comparisons; controlled API fixtures only."""

import base64
import importlib.util
from pathlib import Path
import urllib.parse

import pytest

ROOT = Path(__file__).resolve().parents[1]
REVISION = "a" * 40


@pytest.fixture
def chart(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    spec = importlib.util.spec_from_file_location(
        "portable_chart_inventory", ROOT / "demo/portable-gitops/pipeline.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / "charts/mission-status/templates").mkdir(parents=True)
    files = {
        "charts/mission-status/Chart.yaml": b"name: synthetic\n",
        "charts/mission-status/templates/policy.yaml": b"kind: NetworkPolicy\n",
    }
    for name, raw in files.items():
        Path(name).write_bytes(raw)
    return module, files


def api_fixture(files, entries=None):
    rows = (
        entries
        if entries is not None
        else [
            {"path": "charts/mission-status/templates", "type": "tree", "mode": "040000"},
            *({"path": name, "type": "blob", "mode": "100644"} for name in sorted(files)),
        ]
    )
    calls = []

    def api(path):
        url = urllib.parse.urlsplit(path)
        query = urllib.parse.parse_qs(url.query)
        assert query["ref"] == [REVISION], "Tree and contents must share the immutable revision"
        calls.append(path)
        if url.path.endswith("/repository/tree"):
            assert query["recursive"] == ["true"]
            assert query["path"] == ["charts/mission-status"]
            assert query["per_page"] == ["100"]
            if not isinstance(rows, list):
                return rows
            start = (int(query["page"][0]) - 1) * 100
            return rows[start : start + 100]
        name = urllib.parse.unquote(url.path.split("/repository/files/")[1])
        return {"content": base64.b64encode(files[name]).decode()}

    return api, calls


def test_complete_chart_keeps_content_checks_at_one_revision(chart):
    module, files = chart
    api, calls = api_fixture(files)
    module.validate_deployment_chart(api, "/projects/7", REVISION)
    assert len(calls) == 3


def test_tree_pagination_does_not_omit_templates_after_first_page(chart):
    module, files = chart
    for number in range(101):
        name = f"charts/mission-status/templates/part-{number}.yaml"
        files[name] = b"kind: ConfigMap\n"
        Path(name).write_bytes(files[name])
    api, calls = api_fixture(files)
    module.validate_deployment_chart(api, "/projects/7", REVISION)
    assert sum("/repository/tree?" in path for path in calls) == 2
    assert sum("/repository/files/" in path for path in calls) == len(files)


@pytest.mark.parametrize("change", ["extra", "missing", "changed-content"])
def test_extra_missing_or_changed_remote_chart_blocks(chart, change):
    module, files = chart
    remote = dict(files)
    if change == "extra":
        remote["charts/mission-status/templates/stale.yaml"] = b"kind: NetworkPolicy\n"
    elif change == "missing":
        remote.pop("charts/mission-status/Chart.yaml")
    else:
        remote["charts/mission-status/templates/policy.yaml"] = b"kind: Secret\n"
    api, _ = api_fixture(remote)
    with pytest.raises(RuntimeError, match="chart.*differs"):
        module.validate_deployment_chart(api, "/projects/7", REVISION)


@pytest.mark.parametrize("kind", ["malformed", "repeated", "symlink", "outside", "submodule"])
def test_incomplete_or_unsupported_tree_is_not_a_valid_chart(chart, kind):
    module, files = chart
    entry = {"path": next(iter(files)), "type": "blob", "mode": "100644"}
    rows = [entry]
    if kind == "malformed":
        rows = {"message": "not a tree"}
    elif kind == "repeated":
        rows.append(dict(entry))
    elif kind == "symlink":
        entry["mode"] = "120000"
    elif kind == "outside":
        entry["path"] = "other-project/Chart.yaml"
    else:
        entry.update(type="commit", mode="160000")
    api, _ = api_fixture(files, rows)
    with pytest.raises(RuntimeError):
        module.validate_deployment_chart(api, "/projects/7", REVISION)


def test_tree_walk_has_a_finite_entry_bound(chart):
    module, files = chart
    rows = [
        {"path": f"charts/mission-status/templates/{n}.yaml", "type": "blob", "mode": "100644"}
        for n in range(1025)
    ]
    api, calls = api_fixture(files, rows)
    with pytest.raises(RuntimeError, match="1024-entry bound"):
        module.validate_deployment_chart(api, "/projects/7", REVISION)
    assert len(calls) == 11
    assert all("/repository/tree?" in path for path in calls)
