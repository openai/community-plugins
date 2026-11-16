"""Exact version selection and explicit provisioning; no implicit fallback."""

from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import tarfile

import pytest

from devsecops.schema_catalog import select_schema_set

ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads((ROOT / "toolchain.lock.json").read_bytes())


def test_legacy_default_and_explicit_134_selection_remain_identical():
    source, schemas = select_schema_set(LOCK)
    assert source == LOCK["schema_source"] and schemas == LOCK["schemas"]
    assert select_schema_set(LOCK, "1.34.0") == (source, schemas)
    schemas[0]["sha256"] = "0" * 64
    assert select_schema_set(LOCK)[1] == LOCK["schemas"]


def test_additional_1365_has_same24_kinds_at_exact_independent_commit():
    source, schemas = select_schema_set(LOCK, "1.36.5")
    assert source["commit"] == "a6f9a32d2ccb64b6e4f5b41419b9c2e8ee0cce18"
    assert source["kubernetes_version"] == "1.36.5" and len(schemas) == 24
    assert {Path(item["path"]).name for item in schemas} == {
        Path(item["path"]).name for item in LOCK["schemas"]
    }


@pytest.mark.parametrize("version", ["1.36.0", "1.36", "v1.36.5", "1.36.5+rke2r1", "latest", "../1.34.0"])
def test_unqualified_version_has_no_fallback(version):
    with pytest.raises(ValueError):
        select_schema_set(LOCK, version)


@pytest.mark.parametrize(
    "change", ["mixed-version", "wrong-commit-url", "duplicate", "external-url", "bad-sha", "shadow-default"]
)
def test_changed_or_ambiguous_catalog_is_rejected(change):
    lock = deepcopy(LOCK)
    selected = lock["schema_sets"]["1.36.5"]
    if change == "mixed-version":
        selected["schemas"][0]["path"] = LOCK["schemas"][0]["path"]
    elif change == "wrong-commit-url":
        selected["schema_source"]["commit"] = "0" * 40
    elif change == "duplicate":
        selected["schemas"].append(selected["schemas"][0])
    elif change == "external-url":
        selected["schemas"][0]["url"] = "https://untrusted.example/schema.json"
    elif change == "bad-sha":
        selected["schemas"][0]["sha256"] = "unresolved"
    elif change == "shadow-default":
        lock["schema_sets"]["1.34.0"] = selected
    with pytest.raises(ValueError):
        select_schema_set(lock, "1.36.5")


@pytest.fixture
def bootstrap(monkeypatch):
    spec = importlib.util.spec_from_file_location("bootstrap_schemas_test", ROOT / "scripts/bootstrap.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(module.platform, "machine", lambda: "x86_64")
    calls = []

    def download(item):
        calls.append(item)
        if "member" not in item:
            return b'{"description":"controlled schema dispatch fixture"}'
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w:gz") as tar:
            entry = tarfile.TarInfo(item["member"])
            entry.size = len(b"controlled-tool-bytes")
            tar.addfile(entry, io.BytesIO(b"controlled-tool-bytes"))
        return raw.getvalue()

    monkeypatch.setattr(module, "verified_download", download)
    return module, calls


@pytest.mark.parametrize("version", [None, "1.36.5"])
def test_bootstrap_provisions_only_selected_version(bootstrap, tmp_path, monkeypatch, version):
    module, calls = bootstrap
    cache = tmp_path / "cache"
    argv = ["bootstrap.py", "--cache", str(cache)]
    if version is not None:
        argv += ["--kubernetes-version", version]
    monkeypatch.setattr(module.sys, "argv", argv)
    module.main()
    source, schemas = select_schema_set(LOCK, version)
    assert {p.relative_to(cache / "schemas").as_posix() for p in (cache / "schemas").rglob("*.json")} == {
        s["path"] for s in schemas
    }
    assert len(calls) == 26
    assert json.loads((cache / "provisioning.json").read_bytes())["schema_source"] == source


def test_bootstrap_unknown_version_stops_before_download_or_output(bootstrap, tmp_path, monkeypatch):
    module, calls = bootstrap
    cache = tmp_path / "not-created"
    monkeypatch.setattr(
        module.sys, "argv", ["bootstrap.py", "--cache", str(cache), "--kubernetes-version", "1.36.0"]
    )
    with pytest.raises(SystemExit):
        module.main()
    assert calls == [] and not cache.exists()
