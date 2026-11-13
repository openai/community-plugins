"""Local contract tests; these are not claims of a live stack or CI run."""

import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import sys
import types
import hashlib
import io
import base64
import urllib.error
import urllib.parse
import tarfile

import pytest

HERE = Path(__file__).resolve().parents[1] / "demo/portable-gitops"


def module(name):
    spec = importlib.util.spec_from_file_location("portable_" + name, HERE / (name + ".py"))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


@pytest.mark.parametrize("driver", ["overlayfs", "vfs", "", "btrfs"])
def test_classic_store_doctor_rejects_incompatible_image_identity(monkeypatch, driver):
    m = module("stack")
    monkeypatch.setattr(m, "run", lambda *a, **k: driver.encode())
    with pytest.raises(ValueError, match="classic Docker overlay2"):
        m.require_classic_store()


@pytest.fixture
def base_import(tmp_path, monkeypatch):
    m = module("stack")
    (tmp_path / "inputs").mkdir()
    source = tmp_path / "inputs/base.oci.tar"
    config = b'{"architecture":"amd64","os":"linux","rootfs":{"type":"layers","diff_ids":[]}}'
    digest = hashlib.sha256(config).hexdigest()
    with tarfile.open(source, "w") as archive:
        for name, raw in [
            ("oci-layout", b'{"imageLayoutVersion":"1.0.0"}'),
            ("index.json", b'{"schemaVersion":2,"manifests":[]}'),
            ("blobs/sha256/" + digest, config),
        ]:
            entry = tarfile.TarInfo(name)
            entry.size = len(raw)
            entry.uid = 0
            entry.gid = 0
            archive.addfile(entry, io.BytesIO(raw))
    (tmp_path / "input-manifest.json").write_text(
        json.dumps(
            {
                "files": {
                    "base.oci.tar": {
                        "bytes": source.stat().st_size,
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    }
                }
            }
        )
    )
    data = types.SimpleNamespace(
        module=m,
        state=tmp_path,
        source=source,
        config=config,
        owner={"base_config_digest": "sha256:" + digest},
        calls=[],
        mode="valid",
    )

    def run(args, **kwargs):
        args = [str(x) for x in args]
        data.calls.append(args)
        if args[:2] == ["docker", "info"]:
            return b"overlay2\n"
        if args[:2] == ["skopeo", "copy"]:
            assert args[2].startswith("oci:")
            layout = Path(args[2][len("oci:") :])
            assert (layout / "blobs/sha256" / digest).read_bytes() == config
            target = args[3]
            assert target.startswith("docker-archive:") and target.endswith(":devsecops-base:retained")
            path = target[len("docker-archive:") : -len(":devsecops-base:retained")]
            tag = {
                "wrong-tag": "unexpected:tag",
                "short-tag": "devsecops-base:retained",
                "foreign-registry": "example.invalid/library/devsecops-base:retained",
            }.get(data.mode, "docker.io/library/devsecops-base:retained")
            config_name = digest + ".json"
            tags = [tag, "other:tag"] if data.mode == "extra-tag" else [tag]
            manifest = json.dumps([{"Config": config_name, "RepoTags": tags, "Layers": []}]).encode()
            with tarfile.open(path, "w") as archive:
                for name, raw in [("manifest.json", manifest), (config_name, data.config)]:
                    entry = tarfile.TarInfo(name)
                    entry.size = len(raw)
                    archive.addfile(entry, io.BytesIO(raw))
                if data.mode == "duplicate-manifest":
                    entry = tarfile.TarInfo("manifest.json")
                    entry.size = len(manifest)
                    archive.addfile(entry, io.BytesIO(manifest))
            return b""
        if args[:3] == ["docker", "image", "load"]:
            return b"Loaded image: devsecops-base:retained\n"
        if args[:3] == ["docker", "image", "inspect"]:
            return ("sha256:" + ("0" * 64 if data.mode == "loaded-wrong" else digest)).encode()
        raise AssertionError(args)

    monkeypatch.setattr(m, "run", run)
    return data


@pytest.mark.parametrize("mode", ["valid", "short-tag"])
def test_base_import_uses_local_archive_and_native_loader_with_original_config(base_import, mode):
    case = base_import
    case.mode = mode
    case.module.import_base(case.state, case.owner)
    assert any(x[:3] == ["docker", "image", "load"] for x in case.calls)
    assert not any("docker-daemon:" in arg for call in case.calls for arg in call)
    receipt = json.loads(next(case.state.glob("base-import-*/receipt.json")).read_text())
    assert receipt["base_config_digest"] == case.owner["base_config_digest"]
    assert receipt["docker_store"] == "overlay2"
    assert all(p.stat().st_uid == os.getuid() for p in case.state.glob("base-import-*/oci/blobs/sha256/*"))


@pytest.mark.parametrize("kind", ["path-escape", "symlink", "bad-content-address", "duplicate"])
def test_unsafe_oci_layout_cannot_reach_skopeo(base_import, kind):
    case = base_import
    with tarfile.open(case.source, "a") as archive:
        name = {
            "path-escape": "../escaped",
            "symlink": "blobs/sha256/" + "a" * 64,
            "bad-content-address": "blobs/sha256/" + "b" * 64,
            "duplicate": "index.json",
        }[kind]
        entry = tarfile.TarInfo(name)
        if kind == "symlink":
            entry.type = tarfile.SYMTYPE
            entry.linkname = "/etc/passwd"
            archive.addfile(entry)
        else:
            entry.size = 3
            archive.addfile(entry, io.BytesIO(b"bad"))
    (case.state / "input-manifest.json").write_text(
        json.dumps(
            {
                "files": {
                    "base.oci.tar": {
                        "bytes": case.source.stat().st_size,
                        "sha256": hashlib.sha256(case.source.read_bytes()).hexdigest(),
                    }
                }
            }
        )
    )
    with pytest.raises(ValueError):
        case.module.import_base(case.state, case.owner)
    assert not any(x[0] == "skopeo" for x in case.calls)
    assert not (case.state / "escaped").exists()


@pytest.mark.parametrize(
    "mode",
    ["wrong-config", "wrong-tag", "duplicate-manifest", "changed-input", "foreign-registry", "extra-tag"],
)
def test_bad_base_cannot_reach_native_docker_load(base_import, mode):
    case = base_import
    case.mode = mode
    if mode == "wrong-config":
        case.config = b'{"changed":true}'
    if mode == "changed-input":
        case.source.write_bytes(b"changed retained source")
    with pytest.raises(ValueError):
        case.module.import_base(case.state, case.owner)
    assert not any(x[:3] == ["docker", "image", "load"] for x in case.calls)
    assert not list(case.state.glob("base-import-*/receipt.json"))


def test_native_loaded_config_must_still_match(base_import):
    case = base_import
    case.mode = "loaded-wrong"
    with pytest.raises(ValueError, match="Imported base config differs"):
        case.module.import_base(case.state, case.owner)
    assert not list(case.state.glob("base-import-*/receipt.json"))


@pytest.fixture
def scan(tmp_path, monkeypatch):
    p = module("pipeline")
    state = tmp_path / "state"
    (state / "inputs").mkdir(parents=True)
    (tmp_path / "artifacts").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DEMO_STATE", str(state))
    monkeypatch.setenv("CI_COMMIT_SHA", "a" * 40)
    monkeypatch.setattr(sys, "argv", ["pipeline.py", "scan"])
    digest = "sha256:" + "b" * 64
    future = (dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)).isoformat()
    (state / "owner.json").write_text(json.dumps({"expires_at": future, "base_manifest_digest": digest}))
    (state / "inputs/latest.json").write_text(json.dumps({"built": dt.datetime.now(dt.UTC).isoformat()}))
    entries = [["CVE-test-" + str(n), "package" + str(n), "1.0"] for n in range(14)]
    selectors = [
        {
            "vulnerability": v,
            "namespace": "fixture:distro:1",
            "severity": "High",
            "type": "rpm",
            "name": n,
            "version": version,
            "purl": "pkg:rpm/fixture/" + n + "@" + version,
        }
        for v, n, version in entries
    ]
    exception = {
        "base_manifest_digest": digest,
        "expires_at": future,
        "allowed_high_findings": entries,
        "allowed_high_selectors": selectors,
    }
    (state / "inputs/exception.json").write_text(json.dumps(exception))
    (tmp_path / "ci").mkdir()
    binary = b"controlled executable identity fixture"
    (state / "inputs/grype").write_bytes(binary)
    (tmp_path / "ci/supplychain.lock.json").write_text(
        json.dumps(
            {
                "tools": {
                    "grype": {
                        "platforms": {
                            "linux-amd64": {
                                "binary_bytes": len(binary),
                                "binary_sha256": hashlib.sha256(binary).hexdigest(),
                            }
                        }
                    }
                }
            }
        )
    )
    (tmp_path / "artifacts/build.json").write_text(
        json.dumps(
            {"source_revision": "a" * 40, "base_manifest": digest, "image_config": "sha256:" + "c" * 64}
        )
    )
    (tmp_path / "artifacts/sbom.json").write_text(json.dumps({"artifacts": [{"name": "controlled-fixture"}]}))
    matches = [
        {
            "vulnerability": {"id": v, "severity": "High", "namespace": "fixture:distro:1"},
            "artifact": {
                "name": n,
                "version": vrs,
                "type": "rpm",
                "purl": "pkg:rpm/fixture/" + n + "@" + vrs,
            },
        }
        for v, n, vrs in entries
    ]
    calls = []

    def run(args, output=None, **kwargs):
        calls.append(args)
        Path(output).write_text(json.dumps({"matches": matches}))
        return b""

    monkeypatch.setattr(p, "run", run)
    return types.SimpleNamespace(
        p=p, state=state, matches=matches, exception=exception, calls=calls, root=tmp_path
    )


def test_exact_actual_scan_admits_only_reviewed_set(scan):
    scan.p.main()
    decision = json.loads((scan.root / "artifacts/scan-decision.json").read_text())
    assert decision["decision"] == "temporary-exception"
    assert decision["high_count"] == 14 and len(scan.calls) == 1


@pytest.mark.parametrize(
    "change", ["critical", "new-high", "unknown", "missing-high", "changed-package", "changed-version"]
)
def test_scan_rejects_other_actual_findings(scan, change):
    if change == "critical":
        scan.matches[0]["vulnerability"]["severity"] = "Critical"
    if change == "unknown":
        scan.matches[0]["vulnerability"]["severity"] = "Unknown"
    if change == "new-high":
        scan.matches[0]["vulnerability"]["id"] = "CVE-unreviewed"
    if change == "missing-high":
        scan.matches.pop()
    if change == "changed-package":
        scan.matches[0]["artifact"]["name"] = "other"
    if change == "changed-version":
        scan.matches[0]["artifact"]["version"] = "2.0"
    with pytest.raises(RuntimeError, match="findings exceed"):
        scan.p.main()
    assert json.loads((scan.root / "artifacts/scan-decision.json").read_text())["decision"] == "blocked"


@pytest.mark.parametrize("when", [-121, 1])
def test_expired_or_future_database_prevents_scanner_execution(scan, when):
    (scan.state / "inputs/latest.json").write_text(
        json.dumps({"built": (dt.datetime.now(dt.UTC) + dt.timedelta(hours=when)).isoformat()})
    )
    with pytest.raises(RuntimeError, match="database expired"):
        scan.p.main()
    assert not scan.calls


@pytest.mark.parametrize("change", ["expired", "wrong-base", "wrong-count"])
def test_unapproved_exception_rejected(scan, change):
    if change == "expired":
        scan.exception["expires_at"] = (dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)).isoformat()
    if change == "wrong-base":
        scan.exception["base_manifest_digest"] = "sha256:" + "c" * 64
    if change == "wrong-count":
        scan.exception["allowed_high_findings"].pop()
    (scan.state / "inputs/exception.json").write_text(json.dumps(scan.exception))
    with pytest.raises(RuntimeError):
        scan.p.main()


def test_units_record_intent_before_dispatch(tmp_path, monkeypatch):
    m = module("stack")
    owner = {"unit_prefix": "owned-prefix", "username": "operator"}

    def run(args, **kwargs):
        assert json.loads((tmp_path / "units.jsonl").read_text())["unit"] == "owned-prefix-k3s"
        raise RuntimeError("controlled failure")

    monkeypatch.setattr(m, "run", run)
    with pytest.raises(RuntimeError):
        m.unit(tmp_path, owner, "k3s", ["/fixed/bin", "server"])


def test_state_cannot_be_inside_public_plugin(monkeypatch):
    m = module("stack")
    args = types.SimpleNamespace(state=m.PLUGIN / "demo/portable-gitops/private-state")
    monkeypatch.setattr(m.sys, "platform", "linux")
    with pytest.raises(ValueError, match="outside plugin"):
        m.prepare(args)


def test_observer_contract_matches_current_schema(tmp_path):
    m = module("stack")
    (tmp_path / "phase-baseline.json").write_text(json.dumps({"expected_revision": "a" * 40}))
    m.observer_config(tmp_path, {}, "baseline")
    config = json.loads((tmp_path / "observer-baseline.json").read_text())
    import jsonschema

    schema = json.loads((m.PLUGIN / "schemas/portable-runtime-config.schema.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(config)
    assert config["prometheus"]["instance"] == "mission-api.mission-demo.svc:9090"
    assert config["argocd"]["project"] == "demo"


def test_changed_scanner_stops_before_execution(scan):
    (scan.state / "inputs/grype").write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="supply-chain lock"):
        scan.p.main()
    assert not scan.calls


def test_replaced_exception_during_scan_cannot_receive_admission(scan, monkeypatch):
    original = scan.p.run

    def swapped(*args, **kwargs):
        result = original(*args, **kwargs)
        scan.exception["extra_note"] = "different reviewed bytes"
        (scan.state / "inputs/exception.json").write_text(json.dumps(scan.exception))
        return result

    monkeypatch.setattr(scan.p, "run", swapped)
    with pytest.raises(RuntimeError, match="exception changed during scanning"):
        scan.p.main()
    assert not (scan.root / "artifacts/scan-decision.json").exists()


def test_replaced_exception_before_publish_is_rejected(scan, monkeypatch):
    scan.p.main()
    scan.calls.clear()
    scan.exception["extra_note"] = "different reviewed bytes"
    (scan.state / "inputs/exception.json").write_text(json.dumps(scan.exception))
    monkeypatch.setattr(sys, "argv", ["pipeline.py", "publish"])
    with pytest.raises(RuntimeError, match="identity changed before publication"):
        scan.p.main()
    assert not scan.calls


def test_changed_scanned_artifact_stops_before_publish(scan, monkeypatch):
    scan.p.main()
    (scan.root / "artifacts/sbom.json").write_text("{}")
    monkeypatch.setattr(sys, "argv", ["pipeline.py", "publish"])
    scan.calls.clear()
    with pytest.raises(RuntimeError, match="identity changed"):
        scan.p.main()
    assert not scan.calls


def test_changed_daemon_image_stops_before_publish(scan, monkeypatch):
    scan.p.main()
    monkeypatch.setattr(sys, "argv", ["pipeline.py", "publish"])
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return b"sha256:DIFFERENT\n"

    monkeypatch.setattr(scan.p, "run", run)
    with pytest.raises(RuntimeError, match="exact scanned build"):
        scan.p.main()
    assert len(calls) == 1 and calls[0][:3] == ["docker", "image", "inspect"]


@pytest.mark.parametrize(
    "category", ["http", "other-network-error", "connected-without-http-response", "dns-error"]
)
def test_reachable_or_ambiguous_failure_is_not_policy_denial(category):
    m = module("stack")
    allowed = {
        "resolved_ip": "10.43.1.2",
        "health": {"category": "http", "status": 200},
        "metrics": {"category": "http", "status": 200},
    }
    denied = {
        "resolved_ip": "10.43.1.2",
        "health": {"category": category, "status": 500},
        "metrics": {"category": category, "status": 500},
    }
    assert not m.expected_probes([{"result": allowed}, {"result": denied}], "baseline")


@pytest.mark.parametrize("phase", ["baseline", "fault", "recovered"])
def test_same_dns_positive_control_and_connection_denials(phase):
    m = module("stack")
    healthy = {"category": "http", "status": 200}
    blocked = {"category": "connect-timeout"}
    allowed = {
        "resolved_ip": "10.43.1.2",
        "health": healthy,
        "metrics": blocked if phase == "fault" else healthy,
    }
    denied = {"resolved_ip": "10.43.1.2", "health": blocked, "metrics": {"category": "connection-refused"}}
    for role, result in (("allowed", allowed), ("denied", denied)):
        result.update(
            role=role,
            phase=phase,
            startup_converged=True,
            elapsed_seconds=18,
            startup_elapsed_seconds=15,
            final_observations=[{"health": result["health"], "metrics": result["metrics"]} for _ in range(3)],
        )
    receipts = [{"role": "allowed", "result": allowed}, {"role": "denied", "result": denied}]
    assert m.expected_probes(receipts, phase)
    denied["resolved_ip"] = "10.43.1.3"
    assert not m.expected_probes(receipts, phase)


@pytest.fixture
def publishing(scan, monkeypatch):
    scan.p.main()
    monkeypatch.setattr(sys, "argv", ["pipeline.py", "publish"])
    owner = json.loads((scan.state / "owner.json").read_text())
    owner.update(gitlab_origin="https://127.0.0.1:8443", registry="127.0.0.1:5050")
    (scan.state / "owner.json").write_text(json.dumps(owner))
    (scan.state / "project.json").write_text(
        json.dumps({"id": 7, "path_with_namespace": "root/mission-demo"})
    )
    for key, value in {
        "CI_API_V4_URL": owner["gitlab_origin"] + "/api/v4",
        "CI_REGISTRY": owner["registry"],
        "CI_REGISTRY_IMAGE": owner["registry"] + "/root/mission-demo",
        "CI_REGISTRY_USER": "controlled-user",
        "CI_REGISTRY_PASSWORD": "controlled-placeholder",
        "DEPLOY_TOKEN": "controlled-placeholder",
        "CI_PROJECT_ID": "7",
        "CI_PIPELINE_ID": "8",
        "CI_JOB_ID": "9",
    }.items():
        monkeypatch.setenv(key, value)
    chart = scan.root / "charts/mission-status"
    chart.mkdir(parents=True)
    (chart / "Chart.yaml").write_text("name: fixture\n")
    data = types.SimpleNamespace(
        calls=[],
        commands=[],
        branch_exists=True,
        prior=None,
        expire_after_push=False,
        extra_chart_paths=[],
    )

    def command(args, **kwargs):
        data.commands.append(args)
        if args[:3] == ["docker", "image", "inspect"]:
            if "--format" in args:
                return ("sha256:" + "c" * 64).encode()
            return json.dumps(
                [{"RepoDigests": [owner["registry"] + "/root/mission-demo@sha256:" + "d" * 64]}]
            ).encode()
        if args[:2] == ["docker", "push"] and data.expire_after_push:
            actual_datetime = dt.datetime

            class ExpiredDatetime(actual_datetime):
                @classmethod
                def now(cls, tz=None):
                    return actual_datetime.now(tz) + dt.timedelta(hours=2)

            monkeypatch.setattr(scan.p.dt, "datetime", ExpiredDatetime)
        return b""

    def opened(req, **kwargs):
        data.calls.append((req.get_method(), req.full_url, json.loads(req.data) if req.data else None))
        path = urllib.parse.urlsplit(req.full_url).path
        if path.endswith("/branches/deploy"):
            if not data.branch_exists:
                raise urllib.error.HTTPError(req.full_url, 404, "controlled", None, None)
            value = {"commit": {"id": "e" * 40}}
        elif path.endswith("/repository/tree"):
            value = [
                {"path": "charts/mission-status/Chart.yaml", "type": "blob", "mode": "100644"},
                *({"path": name, "type": "blob", "mode": "100644"} for name in data.extra_chart_paths),
            ]
        elif path.endswith("/repository/branches"):
            value = {"commit": {"id": "e" * 40}}
        elif "/repository/files/" in path:
            decoded = urllib.parse.unquote(path.split("/repository/files/")[1])
            if decoded == "deploy/values.json":
                if data.prior is None:
                    raise urllib.error.HTTPError(req.full_url, 404, "controlled", None, None)
                value = {
                    "content": base64.b64encode(json.dumps(data.prior).encode()).decode(),
                    "last_commit_id": "e" * 40,
                    "commit_id": "e" * 40,
                }
            else:
                value = {"content": base64.b64encode((scan.root / decoded).read_bytes()).decode()}
        else:
            value = {"id": "f" * 40}
        return io.BytesIO(json.dumps(value).encode())

    monkeypatch.setattr(scan.p, "run", command)
    monkeypatch.setattr(scan.p.ssl, "create_default_context", lambda **kw: None)
    monkeypatch.setattr(
        scan.p.urllib.request, "build_opener", lambda *handlers: types.SimpleNamespace(open=opened)
    )
    data.scan = scan
    return data


@pytest.mark.parametrize("exists", [False, True])
def test_publish_reconciles_branch_after_partial_retry(publishing, exists):
    publishing.branch_exists = exists
    publishing.scan.p.main()
    branch_posts = [x for x in publishing.calls if x[0] == "POST" and x[1].endswith("/branches")]
    commits = [x for x in publishing.calls if x[1].endswith("/commits")]
    assert len(branch_posts) == (0 if exists else 1)
    assert len(commits) == 1 and commits[0][2]["actions"][0]["action"] == "create"


def test_publish_existing_values_uses_optimistic_update(publishing):
    publishing.prior = {"old": "controlled value"}
    publishing.scan.p.main()
    action = next(x[2]["actions"][0] for x in publishing.calls if x[1].endswith("/commits"))
    assert action["action"] == "update" and action["last_commit_id"] == "e" * 40


def test_publish_rejects_remote_template_deleted_from_current_source(publishing):
    publishing.extra_chart_paths.append("charts/mission-status/templates/removed-policy.yaml")
    with pytest.raises(RuntimeError, match="chart path set"):
        publishing.scan.p.main()
    assert not any(call[1].endswith("/commits") for call in publishing.calls)
    assert not (publishing.scan.root / "artifacts/deployment.json").exists()


def test_publish_stops_commit_when_lease_expires_during_push(publishing):
    publishing.expire_after_push = True
    with pytest.raises(RuntimeError, match="authorization expired"):
        publishing.scan.p.main()
    assert any(x[:2] == ["docker", "push"] for x in publishing.commands)
    assert not any(x[1].endswith("/commits") for x in publishing.calls)


def test_token_bearing_api_origin_cannot_be_overridden(publishing, monkeypatch):
    monkeypatch.setenv("CI_API_V4_URL", "https://unapproved.example/api/v4")
    with pytest.raises(RuntimeError, match="origin differs"):
        publishing.scan.p.main()
    assert not publishing.calls


@pytest.mark.parametrize(
    "key,value",
    [
        ("CI_PROJECT_ID", "99"),
        ("CI_REGISTRY_IMAGE", "unapproved.example/other"),
        ("CI_REGISTRY_IMAGE", "127.0.0.1:5050/other/repository"),
    ],
)
def test_project_and_push_destination_are_exactly_owned(publishing, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(RuntimeError, match="actual owned project"):
        publishing.scan.p.main()
    assert not publishing.calls
    assert not any(
        x[:2] in (["docker", "login"], ["docker", "tag"], ["docker", "push"]) for x in publishing.commands
    )


def test_failed_tool_retains_private_bounded_diagnostics(tmp_path, monkeypatch):
    m = module("pipeline")
    monkeypatch.setenv("DEMO_STATE", str(tmp_path))
    with pytest.raises(RuntimeError, match="private diagnostic"):
        m.run([sys.executable, "-c", "import sys;sys.stderr.write('controlled diagnostic');sys.exit(2)"])
    saved = list((tmp_path / "diagnostics").glob("ci-*/stderr"))
    assert len(saved) == 1 and saved[0].read_text() == "controlled diagnostic"
    assert saved[0].stat().st_mode & 0o777 == 0o600


def test_empty_policy_scan_passes_without_claiming_exception(scan):
    scan.exception["allowed_high_findings"] = []
    scan.exception["allowed_high_selectors"] = []
    scan.matches.clear()
    (scan.state / "inputs/exception.json").write_text(json.dumps(scan.exception))
    scan.p.main()
    decision = json.loads((scan.root / "artifacts/scan-decision.json").read_text())
    assert decision["decision"] == "passed"
    assert decision["high_count"] == decision["critical_count"] == decision["unknown_severity_count"] == 0
    assert decision["exception_sha256"] == scan.p.sha(scan.state / "inputs/exception.json")


def test_new_high_cannot_use_empty_policy(scan):
    scan.exception["allowed_high_findings"] = []
    scan.exception["allowed_high_selectors"] = []
    scan.matches[:] = scan.matches[:1]
    (scan.state / "inputs/exception.json").write_text(json.dumps(scan.exception))
    with pytest.raises(RuntimeError, match="findings exceed"):
        scan.p.main()
    decision = json.loads((scan.root / "artifacts/scan-decision.json").read_text())
    assert decision["decision"] == "blocked" and decision["unexpected_high_count"] == 1


@pytest.mark.parametrize("field", ["namespace", "type", "purl"])
def test_scan_binds_all_seven_selector_fields(scan, field):
    target = scan.matches[0]["vulnerability"] if field == "namespace" else scan.matches[0]["artifact"]
    target[field] = "different"
    with pytest.raises(RuntimeError, match="findings exceed"):
        scan.p.main()


def test_passed_label_cannot_replace_nonempty_exception_at_publish(publishing):
    decision_path = publishing.scan.root / "artifacts/scan-decision.json"
    decision = json.loads(decision_path.read_text())
    decision["decision"] = "passed"
    decision_path.write_text(json.dumps(decision))
    with pytest.raises(RuntimeError, match="admission label"):
        publishing.scan.p.main()
    assert not publishing.calls and not publishing.commands
