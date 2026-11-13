"""The real-tool deployment fixture honors the canonical bootstrap selection."""
import hashlib
from pathlib import Path

import pytest

from test_deployment_proposal import tools


def test_deployment_tools_use_explicit_provisioned_paths_and_bind_selected_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "no-default-cache")
    selected = {}
    for name in ("helm", "kubeconform"):
        path = tmp_path / (name + "-selected")
        path.write_bytes((name + " explicit fixture identity").encode())
        selected[name] = path
        monkeypatch.setenv("DEVSECOPS_" + name.upper(), str(path))
    schemas = tmp_path / "selected-schemas"
    schemas.mkdir()
    monkeypatch.setenv("DEVSECOPS_SCHEMAS", str(schemas))
    try:
        result = tools.__wrapped__()
    except pytest.skip.Exception as error:
        pytest.fail("Explicit provisioned tools must not be discarded for a legacy cache: " + str(error))
    for name, path in selected.items():
        assert result[name + "_path"] == str(path)
        assert result[name + "_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result["schemas_path"] == str(schemas)


def test_missing_explicit_tool_never_falls_back_to_another_cached_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cache = tmp_path / ".cache/devsecops/v0.1/bin"
    cache.mkdir(parents=True)
    for name in ("helm", "kubeconform"):
        (cache / name).write_bytes(b"unselected fallback")
        monkeypatch.setenv("DEVSECOPS_" + name.upper(), str(tmp_path / (name + "-missing")))
    with pytest.raises(pytest.skip.Exception, match="Explicit pinned Helm/Kubeconform bootstrap required"):
        tools.__wrapped__()
