"""Adversarial operator-input checks and truthful preflight acceptance."""
import copy
from datetime import datetime, timezone
import hashlib
import json
import subprocess
import sys

import pytest

from devsecops.factory.contracts import SCHEMAS, load_document, read_json, validate_document
from devsecops.factory.doctor import PACKAGE_ROOT, SCENARIO_ROOT, inspect


CONFIG = PACKAGE_ROOT / "demo/software-factory/config/demo-config.example.json"
NOW = datetime(2026, 10, 26, tzinfo=timezone.utc)


def write(tmp_path, value, name="input.json"):
    target = tmp_path / name
    target.write_text(json.dumps(value))
    return target


def config():
    return json.loads(CONFIG.read_text())


def scenario():
    return json.loads((SCENARIO_ROOT / "scenario.json").read_text())


@pytest.mark.parametrize("name", list(SCHEMAS))
def test_published_contract_is_runtime_contract(name):
    assert json.loads((PACKAGE_ROOT / f"schemas/{name}.schema.json").read_text()) == SCHEMAS[name]


@pytest.mark.parametrize("mutation", [
    lambda c: c.update(schema_version=2),
    lambda c: c.update(password="SECRET_SENTINEL"),
    lambda c: c["services"][0].update(read_auth={"mode": "bearer", "token": "SECRET_SENTINEL"}),
    lambda c: c["services"][0].update(origin="https://SECRET_SENTINEL@gitlab.example.org"),
    lambda c: c["services"][0].update(origin="https://gitlab.example.org?token=SECRET_SENTINEL"),
    lambda c: c["services"][0].update(origin="http://gitlab.example.org"),
    lambda c: c["services"].append(copy.deepcopy(c["services"][0])),
    lambda c: c["runtime"]["nodes"].append(copy.deepcopy(c["runtime"]["nodes"][0])),
    lambda c: c.update(expires_at="2026-10-26T12:00:00"),
    lambda c: c.update(expires_at="2026-10-26T12:00:00+00:60"),
    lambda c: c.update(expires_at="2026-10-26T12:00:00+00:99"),
    lambda c: c.update(expires_at="2026-10-26T12:00:00+24:00"),
    lambda c: c["services"][0].update(origin="https://@gitlab.example.org"),
    lambda c: c["services"][0].update(origin="https://gitlab.example.org:"),
    lambda c: c["services"][0].update(origin="https://gitlab.example.org\\evil"),
    lambda c: c["services"][0].update(origin="https://%65xample.org"),
    lambda c: c["services"][0].update(origin="https://[::1]evil"),
    lambda c: c["services"][0].update(origin="https://999.1.1.1"),
])
def test_untrusted_config_rejected_without_secret_echo(tmp_path, mutation):
    value = config()
    mutation(value)
    with pytest.raises(ValueError) as error:
        load_document(write(tmp_path, value), "factory-config")
    assert "SECRET_SENTINEL" not in str(error.value)


@pytest.mark.parametrize("text", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '{"x":"SECRET_SENTINEL"', '[]'])
def test_json_reader_rejects_ambiguous_or_invalid_data(tmp_path, text):
    path = tmp_path / "data.json"
    path.write_text(text)
    with pytest.raises(ValueError) as error:
        read_json(path)
    assert "SECRET_SENTINEL" not in str(error.value)


def test_bounded_reader_refuses_symlink_and_large_file(tmp_path):
    path = write(tmp_path, {"x": "x" * 100})
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(ValueError):
        read_json(link)
    with pytest.raises(ValueError):
        read_json(path, limit=20)


@pytest.mark.parametrize("change", ["missing-native", "wrong-order", "recursive-replay", "skip-predecessor", "wrong-case", "omit-cleanup", "missing-baseline-case", "missing-reset-case", "incomplete-final-cases", "wrong-primary-case"])
def test_graph_cannot_skip_qualification_work(change):
    value = scenario()
    if change == "missing-native":
        value["phases"][7]["operation"] = "verify"
        value["phases"][7].pop("workflow")
    elif change == "wrong-order":
        value["phases"][1]["operation"] = "up"
    elif change == "recursive-replay":
        next(p for p in value["phases"] if p["attempt"] == "replay")["case_ids"] = ["D6"]
    elif change == "skip-predecessor":
        value["phases"][3]["depends_on"] = ["preflight"]
    elif change == "wrong-case":
        next(p for p in value["phases"] if p["operation"] == "case")["case_ids"] = ["D1"]
    elif change == "missing-baseline-case":
        next(p for p in value["phases"] if p["operation"] == "baseline")["case_ids"] = []
    elif change == "missing-reset-case":
        next(p for p in value["phases"] if p["operation"] == "reset")["case_ids"] = []
    elif change == "incomplete-final-cases":
        value["phases"][-1]["case_ids"].remove("D4")
    elif change == "wrong-primary-case":
        next(p for p in value["phases"] if p["operation"] == "inject")["case_ids"] = ["D3"]
    else:
        value["phases"][-2]["operation"] = "verify"
    with pytest.raises(ValueError):
        validate_document(value, "factory-scenario")


def test_example_preflight_cannot_claim_live_readiness(tmp_path):
    report = inspect(CONFIG, profile="bigbang-live", environ={}, now=NOW)
    assert report["readiness"] == "unknown"
    assert report["live_qualification"] == "not_performed"
    assert report["mutations_performed"] is False
    codes = {gap["code"] for gap in report["gaps"]}
    assert {"lock-missing", "live-preflight-pending", "lifecycle-implementation-pending"} <= codes
    assert "credential-missing:registry1:read_auth" in codes


def test_present_credentials_are_not_authentication_proof_or_output(tmp_path):
    value = config()
    env = {v: "SECRET_SENTINEL" for service in value["services"]
           for role in ["read_auth", "operator_auth"]
           for k, v in service.get(role, {}).items() if k.endswith("_env")}
    report = inspect(write(tmp_path, value), profile="bigbang-live", environ=env, now=NOW)
    assert "SECRET_SENTINEL" not in json.dumps(report)
    assert report["readiness"] == "unknown"
    checks = [c for c in report["checks"] if c["id"].startswith("credential-presence")]
    assert all(c["status"] == "pass" and c["authentication_verified"] is False for c in checks)


def test_expired_scope_and_shared_write_credentials_remain_gaps(tmp_path):
    value = config()
    value["expires_at"] = "2025-01-01T00:00:00Z"
    value["services"][0]["operator_auth"] = value["services"][0]["read_auth"]
    report = inspect(write(tmp_path, value), profile="bigbang-live", environ={}, now=NOW)
    codes = {gap["code"] for gap in report["gaps"]}
    assert {"environment-expired", "identity-overlap:gitlab"} <= codes


def factory_lock():
    image = {"repository": "registry1.dso.mil/ironbank/opensource/nginx/nginx", "manifest_sha256": "a"*64,
             "platform_manifest_sha256": "b"*64, "config_sha256": "c"*64, "os": "linux", "architecture": "amd64",
             "required_predicates": ["spdxjson"], "trust_sha256": "d"*64}
    return {"schema_version":1, "profile":"bigbang-live", "scenario_id":"ironbank-mission-recovery-v1",
            "scenario_sha256":"0"*64, "assertions_sha256":"0"*64,
            "platform": {"os":"linux", "architecture":"amd64", "distribution":"rke2", "distribution_version":"1.2.3",
                         "kubernetes_version":"1.2.3", "bigbang_version":"1.2.3", "flux_version":"1.2.3", "argo_version":"1.2.3", "cni":"test-only", "mesh_mode":"test-only"},
            "tools":[{"name":"helm", "version":"1.2.3", "sha256":"0"*64}],
            "images":{"reference_web":image, "mission_base":image}, "freshness_seconds":60, "required_fresh_scrapes":3,
            "observation_window_seconds":120, "scrape_interval_seconds":15}


@pytest.mark.parametrize('override', [
    {'observation_window_seconds': 121}, {'observation_window_seconds': 15},
    {'observation_window_seconds': 3600, 'scrape_interval_seconds': 1},
    {'required_fresh_scrapes': 10}, {'freshness_seconds': 14},
    {'scrape_interval_seconds': 0}, {'observation_window_seconds': True},
    {'freshness_seconds': 301},
])
def test_lock_cannot_weaken_or_omit_observation_bounds(override):
    value = factory_lock()
    validate_document(value, 'factory-lock')
    value.update(override)
    with pytest.raises(ValueError):
        validate_document(value, 'factory-lock')


def test_lock_requires_recorded_observation_window():
    value = factory_lock()
    del value['observation_window_seconds']
    with pytest.raises(ValueError):
        validate_document(value, 'factory-lock')


def test_valid_lock_with_wrong_scenario_and_tool_digest_is_not_accepted(tmp_path):
    lock = factory_lock()
    value = config()
    value["tool_paths"]["helm"] = str(write(tmp_path, {"not": "an executable"}, "tool"))
    report = inspect(write(tmp_path, value), profile="bigbang-live", lock_path=write(tmp_path, lock, "lock.json"), environ={}, now=NOW)
    codes = {gap["code"] for gap in report["gaps"]}
    assert {"scenario-lock-mismatch", "assertions-lock-mismatch", "tool-unverified:helm", "live-preflight-pending"} <= codes
    assert report["readiness"] == "unknown"
    lock["required_fresh_scrapes"] = 2
    with pytest.raises(ValueError):
        validate_document(lock, "factory-lock")
    lock["required_fresh_scrapes"] = 3
    lock["images"]["reference_web"]["repository"] = "registry-1.docker.io/library/nginx"
    with pytest.raises(ValueError):
        validate_document(lock, "factory-lock")


def test_portable_wrapper_works_from_an_unrelated_directory(tmp_path):
    result = subprocess.run([sys.executable, str(PACKAGE_ROOT / "scripts/factory_demo.py"), "doctor",
                             "--profile", "bigbang-live", "--config", str(CONFIG)],
                            cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["scenario_sha256"] == hashlib.sha256((SCENARIO_ROOT / "scenario.json").read_bytes()).hexdigest()
    assert report["live_qualification"] == "not_performed"


@pytest.mark.parametrize("origin", ["https://gitlab.example.org", "https://gitlab.example.org:8443", "https://[::1]:8443", "https://10.0.0.12", "https://registry", "https://gitlab.example.org."])
def test_valid_private_service_origins_are_supported(origin):
    value = config()
    value["services"][0]["origin"] = origin
    validate_document(value, "factory-config")


@pytest.mark.parametrize("case", ["D1", "D2", "D3", "D4", "D5", "D6"])
def test_public_assertions_require_all_six_cases(case):
    assertions = json.loads((SCENARIO_ROOT / "assertions.json").read_text())
    assert set(assertions["cases"]) == {"D1", "D2", "D3", "D4", "D5", "D6"}
    validate_document(assertions, "scenario-assertions")
    del assertions["cases"][case]
    with pytest.raises(ValueError):
        validate_document(assertions, "scenario-assertions")
