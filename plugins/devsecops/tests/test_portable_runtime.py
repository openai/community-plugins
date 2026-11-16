"""Portable observation boundaries, real TLS CLI transport and offline comparisons."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import threading
from urllib.parse import parse_qs, urlsplit
import zipfile

import pytest
from test_argocd import app, ca, SHA, NOW, SECRET
from test_kubernetes_adapter import Client as KubeClient, fixture as kube_fixture
from test_prometheus_adapter import Client as PromClient, fixture as prom_fixture, SCOPE

from devsecops.portable_runtime import (
    API_CONTRACT, CONFIG_SCHEMA, REPORT_SCHEMA, COMPARISON_SCHEMA, PROFILE,
    _conditions, _save, compare, main, observe,
)

__all__ = ["ca"]
ROOT = Path(__file__).resolve().parents[1]


def config(origin="https://example.test"):
    return {"schema_version": 1, "profile": PROFILE,
        "argocd": {"connection": {"origin": origin}, "project": "demo", "application_namespace": "argocd",
                   "application": "mission", "expected_revision": SHA},
        "kubernetes": {"connection": {"origin": origin}, "namespace": "demo", "deployment": "mission", "service": "mission"},
        "prometheus": {"connection": {"origin": origin}, "job": SCOPE["job"], "instance": SCOPE["instance"]}}


class ArgoClient:
    origin = "https://example.test"
    receipts = []

    def __init__(self, now=NOW, version=API_CONTRACT, revision=SHA):
        self.version = version
        self.data = app()
        self.data["status"]["reconciledAt"] = (now - timedelta(seconds=5)).isoformat()
        self.data["status"]["sync"]["revision"] = revision
        self.data["status"]["operationState"]["syncResult"]["revision"] = revision

    def get_json(self, path, params=None):
        return {"Version": self.version} if path == "/api/version" else deepcopy(self.data)


def clients(now=NOW, *, fault=False, version=API_CONTRACT, revision=SHA):
    data = prom_fixture(60)
    shift = int((now - NOW).total_seconds())
    for name, response in data.items():
        for pair in response["data"]["result"][0]["values"]:
            pair[0] += shift
            if name.endswith("timestamp"):
                pair[1] = str(float(pair[1]) + shift)
            elif fault:
                pair[1] = "0" if name == "up" else "10"
    kube, prom = KubeClient(), PromClient(data)
    kube.origin = prom.origin = "https://example.test"
    return {"argocd": ArgoClient(now, version, revision), "kubernetes": kube, "prometheus": prom}


def observation(phase="baseline", now=NOW, **options):
    return observe(config(), phase=phase, _clients=clients(now, **options), _now=now,
                   delivered_at=(now - timedelta(seconds=90)).isoformat() if phase == "recovered" else None)


def test_measured_success_does_not_become_qualified_recovery():
    report = observation()
    assert set(report["conditions"].values()) == {"pass"}
    assert report["decision"] == "unknown" and report["execution_status"] == "partial"
    assert report["transport_mode"] == "controlled-test"
    assert report["providers"]["prometheus"]["decision"] == "unknown"
    assert "prometheus:prometheus-missing-image-evidence" in {gap["code"] for gap in report["gaps"]}
    assert SECRET not in json.dumps(report)
    assert "sensitive-value-must-not-escape" not in json.dumps(report)
    assert "connection" not in report["scope"]["argocd"]


def test_healthy_controller_can_coexist_with_measured_functional_failure():
    report = observation(fault=True)
    assert report["conditions"] == {"controller_sync": "pass", "controller_health": "pass", "workload_ready": "pass",
                                    "scrape_fresh": "fail", "functional_progress": "fail"}
    assert report["decision"] == "unknown"


def test_version_drift_preserves_raw_status_but_cannot_pass_controller_conditions():
    report = observation(version="v3.6.0")
    assert report["providers"]["argocd"]["observations"]["health_status"] == "Healthy"
    assert report["conditions"]["controller_health"] == "unknown"
    assert report["conditions"]["controller_sync"] == "unknown"
    assert any(gap["code"] == "argocd:unsupported-version" for gap in report["gaps"])


def test_selected_revision_mismatch_is_not_accepted_as_controller_success():
    report = observation(revision="b" * 40)
    assert report["conditions"]["controller_sync"] == "unknown"
    assert any(gap["code"] == "argocd:selected-revision-mismatch" for gap in report["gaps"])


def test_source_samples_and_controller_cannot_age_out_during_other_reads():
    providers = observation()["providers"]
    conditions = _conditions(providers, NOW + timedelta(seconds=301))
    assert conditions["controller_sync"] == conditions["controller_health"] == "unknown"
    assert conditions["scrape_fresh"] == conditions["functional_progress"] == "unknown"
    assert any(gap["code"] == "completion-stale" for gap in providers["argocd"]["gaps"])


def test_recovery_requires_delivery_time_and_rejects_old_samples():
    with pytest.raises(ValueError, match="explicit delivery timestamp"):
        observe(config(), phase="recovered", _clients=clients(), _now=NOW)
    report = observe(config(), phase="recovered", delivered_at=(NOW - timedelta(seconds=20)).isoformat(),
                     _clients=clients(), _now=NOW)
    assert report["conditions"]["scrape_fresh"] == "unknown"
    assert report["conditions"]["functional_progress"] == "unknown"
    assert any("pre-delivery-source-sample" in gap["code"] for gap in report["gaps"])
    with pytest.raises(ValueError, match="future"):
        observe(config(), phase="recovered", delivered_at=(NOW + timedelta(seconds=1)).isoformat(), _clients=clients(), _now=NOW)


def test_recovery_does_not_inherit_controller_status_from_before_delivery():
    fake = clients()
    fake["argocd"].data["status"]["reconciledAt"] = (NOW - timedelta(seconds=100)).isoformat()
    report = observe(config(), phase="recovered", delivered_at=(NOW - timedelta(seconds=90)).isoformat(), _clients=fake, _now=NOW)
    assert report["conditions"]["controller_sync"] == "unknown"
    assert report["conditions"]["functional_progress"] == "pass"
    assert any(gap["code"] == "controller-pre-delivery" for gap in report["gaps"])


@pytest.mark.parametrize("mutation", ["profile", "secret", "http", "bad-limits"])
def test_bad_config_cannot_make_network_requests(mutation, monkeypatch):
    value = config()
    if mutation == "profile":
        value["profile"] = "ironbank-mission-recovery-v1"
    elif mutation == "secret":
        value["argocd"]["connection"]["password"] = "never-accepted"
    elif mutation == "http":
        value["kubernetes"]["connection"]["origin"] = "http://example.test"
    else:
        value["prometheus"]["window_seconds"] = 31
    monkeypatch.setattr("devsecops.portable_runtime.HTTPSReadClient", lambda **_: pytest.fail("Network client constructed"))
    with pytest.raises(ValueError):
        observe(value)


def bundles(tmp_path):
    return {phase: _save(observation(phase, NOW + timedelta(minutes=index * 5), fault=phase == "fault"),
                        tmp_path / phase, "collect", {})
            for index, phase in enumerate(("baseline", "fault", "recovered"))}


def test_offline_compare_reports_condition_changes_without_qualified_recovery(tmp_path):
    paths = bundles(tmp_path)
    result = compare(paths)
    rows = {row["condition"]: row for row in result["conditions"]}
    assert rows["functional_progress"]["fault_observed"]
    assert rows["functional_progress"]["recovery_observed"]
    assert not rows["controller_health"]["fault_observed"]
    assert result["decision"] == "unknown" and result["controlled_test_inputs"]
    assert all(len(digest) == 64 for digest in result["input_sha256"].values())
    assert main(["compare", "--baseline", str(paths["baseline"]), "--fault", str(paths["fault"]),
                 "--recovered", str(paths["recovered"]), "--output", str(tmp_path / "comparison")]) == 2
    assert "unknown" in (tmp_path / "comparison/report.html").read_text()


@pytest.mark.parametrize("damage", ["edited-report", "missing-manifest", "wrong-scope", "out-of-order", "wrong-phase"])
def test_compare_rejects_tamper_or_unrelated_observations(tmp_path, damage):
    paths = bundles(tmp_path)
    if damage == "edited-report":
        with paths["fault"].open("a") as stream:
            stream.write(" ")
    elif damage == "missing-manifest":
        (paths["fault"].parent / "portable-manifest.json").unlink()
    else:
        report = observation("fault", NOW + timedelta(minutes=5))
        if damage == "wrong-scope":
            changed = config()
            changed["prometheus"]["instance"] = "another:9090"
            report = observe(changed, phase="fault", _clients=clients(), _now=NOW)
        elif damage == "out-of-order":
            report = observation("fault", NOW - timedelta(minutes=5))
        elif damage == "wrong-phase":
            report["phase"] = "recovered"
            report["declared_delivery_at"] = (NOW - timedelta(minutes=1)).isoformat()
        paths["fault"] = _save(report, tmp_path / "replacement", "collect", {})
    with pytest.raises(ValueError):
        compare(paths)


def test_artifacts_are_private_exclusive_and_terminal(tmp_path):
    output = tmp_path / "private"
    path = _save(observation(), output, "collect", {})
    assert output.stat().st_mode & 0o777 == 0o700
    assert all(file.stat().st_mode & 0o777 == 0o600 for file in output.iterdir())
    assert json.loads((output / "portable-manifest.json").read_text())["artifacts"][path.name]["bytes"] == path.stat().st_size
    with pytest.raises(ValueError, match="already exists"):
        _save(observation(), output, "collect", {})
    (tmp_path / "alias").symlink_to(output, target_is_directory=True)
    with pytest.raises(OSError):
        _save(observation(), tmp_path / "alias/child", "collect", {})


def test_recovery_delivery_cannot_precede_the_completed_fault(tmp_path):
    paths = bundles(tmp_path)
    report = observation("recovered", NOW + timedelta(minutes=10))
    report["declared_delivery_at"] = (NOW + timedelta(minutes=4)).isoformat()
    paths["recovered"] = _save(report, tmp_path / "old-delivery", "collect", {})
    with pytest.raises(ValueError, match="follow the completed fault"):
        compare(paths)


def test_imported_bundle_cannot_declare_delivery_after_its_observation(tmp_path):
    paths = bundles(tmp_path)
    report = observation("recovered", NOW + timedelta(minutes=10))
    report["declared_delivery_at"] = (NOW + timedelta(minutes=11)).isoformat()
    paths["recovered"] = _save(report, tmp_path / "future-delivery", "collect", {})
    with pytest.raises(ValueError, match="cannot follow its observation"):
        compare(paths)


def test_failed_final_output_verification_removes_terminal_manifest(tmp_path, monkeypatch):
    calls = []

    def changed_path(*_):
        calls.append(True)
        if len(calls) == 2:
            raise ValueError("Output identity changed")

    monkeypatch.setattr("devsecops.portable_runtime._verify_output_path", changed_path)
    with pytest.raises(ValueError, match="Output identity"):
        _save(observation(), tmp_path / "interrupted", "collect", {})
    assert not (tmp_path / "interrupted/portable-manifest.json").exists()


@pytest.mark.parametrize("metadata", [[], None, {"portable-observation.json": []}])
def test_malformed_terminal_metadata_returns_sanitized_cli_error(tmp_path, metadata, capsys):
    paths = bundles(tmp_path)
    manifest_path = paths["fault"].parent / "portable-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifacts"] = metadata
    manifest_path.write_text(json.dumps(manifest))
    assert main(["compare", "--baseline", str(paths["baseline"]), "--fault", str(paths["fault"]),
                 "--output", str(tmp_path / "invalid")]) == 3
    assert "Traceback" not in capsys.readouterr().err
    assert not (tmp_path / "invalid").exists()


def test_exported_schemas_match_the_executed_contract():
    for name, schema in (("config", CONFIG_SCHEMA), ("observation", REPORT_SCHEMA), ("comparison", COMPARISON_SCHEMA)):
        assert json.loads((ROOT / f"schemas/portable-runtime-{name}.schema.json").read_text()) == schema


def test_package_rejects_missing_portable_entrypoint_or_schema():
    from scripts.build_plugin import package_files, validate
    files = package_files()
    validate(files)
    for missing in ("scripts/observe_gitops.py", "schemas/portable-runtime-observation.schema.json"):
        reduced = {name: value for name, value in files.items() if name != missing}
        with pytest.raises(ValueError, match="Portable runtime"):
            validate(reduced)


@pytest.fixture
def portable_server(ca):
    state = {"requests": [], "deny": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            parsed = urlsplit(self.path)
            state["requests"].append((parsed.path, self.headers.get("Authorization")))
            if parsed.path == "/api/version":
                data = {"Version": "v" + API_CONTRACT}
            elif parsed.path == "/api/v1/applications/mission":
                data = ArgoClient(datetime.now(UTC)).data
            elif parsed.path == "/api/v1/query_range":
                params = parse_qs(parsed.query)
                start, end, step = (int(params[key][0]) for key in ("start", "end", "step"))
                query = params["query"][0]
                timestamps = query.startswith("timestamp(")
                counter = "mission_events_total" in query
                labels = dict(SCOPE)
                if not timestamps:
                    labels["__name__"] = "mission_events_total" if counter else "up"
                data = {"status": "success", "data": {"resultType": "matrix", "result": [{"metric": labels,
                        "values": [[t, str(t if timestamps else t // 15 if counter else 1)] for t in range(start, end + 1, step)]}]}}
            else:
                data = kube_fixture()[parsed.path]
            body = json.dumps(data).encode()
            self.send_response(403 if state["deny"] else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(ca[0]), str(ca[1]))
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    thread = threading.Thread(target=lambda: httpd.serve_forever(poll_interval=.01), daemon=True)
    thread.start()
    state.update(origin=f"https://127.0.0.1:{httpd.server_port}", ca=str(ca[0]))
    try:
        yield state
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


@pytest.fixture(scope="module")
def extracted_portable(tmp_path_factory):
    from scripts.build_plugin import build
    directory = tmp_path_factory.mktemp("portable package with spaces")
    archive = directory / "candidate.zip"
    build(archive)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(directory / "extracted")
    return directory / "extracted/devsecops"


@pytest.mark.parametrize("denied", [False, True])
def test_cli_uses_real_verified_tls_gets_without_exporting_credentials(tmp_path, portable_server, extracted_portable, monkeypatch, denied):
    portable_server["deny"] = denied
    monkeypatch.setenv("PORTABLE_TEST_TOKEN", SECRET)
    value = config(portable_server["origin"])
    for provider in ("argocd", "kubernetes", "prometheus"):
        value[provider]["connection"].update(ca_bundle=portable_server["ca"], token_env="PORTABLE_TEST_TOKEN")
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(value))
    output = tmp_path / "observed"
    env = {key: value for key, value in os.environ.items() if key not in {"PYTHONHOME", "PYTHONPATH"}}
    result = subprocess.run([sys.executable, "-B", str(extracted_portable / "scripts/observe_gitops.py"),
        "collect", "--config", str(config_path), "--phase", "baseline", "--output", str(output)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 2, result.stderr
    report = json.loads((output / "portable-observation.json").read_text())
    assert report["transport_mode"] == "verified-https" and report["decision"] == "unknown"
    assert set(report["conditions"].values()) == ({"unknown"} if denied else {"pass"})
    assert all(auth == "Bearer " + SECRET for _, auth in portable_server["requests"])
    assert all("refresh" not in route and "watch" not in route for route, _ in portable_server["requests"])
    assert SECRET not in "".join(file.read_text() for file in output.iterdir())
    assert len(portable_server["requests"]) <= 16
    assert sum(len(provider["observations"]["receipts"]) for provider in report["providers"].values()) == len(portable_server["requests"])
