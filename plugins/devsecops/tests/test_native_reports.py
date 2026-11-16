"""Real engine replay and journals; only native model execution is controlled.

Every resulting origin is permanently controlled. No model, external service,
credential, publication, or runtime provisioning is used here.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import pickle
import shutil
import sys
import threading
from time import monotonic

import pytest

from test_correction import correction as correction_case, git, require_tool
from test_factory_lifecycle import FixtureDriver, finish_native, inputs_for
from devsecops import __version__
from devsecops.engine import _schema_receipt
from devsecops.factory import native_reports as module, native_codex
from devsecops.factory.lifecycle import LifecycleEngine
from devsecops.factory.native_session import NativeCodexSelection
from devsecops.factory.state import RunJournal, StateError, _canonical, _sha, _read

__all__ = ["correction_case"]
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def installed(tmp_path, monkeypatch):
    home = tmp_path / "test-codex-home"
    package = home / "plugins/cache/devsecops-local/devsecops" / __version__
    package.mkdir(parents=True)
    for name in ("src", "schemas", "scripts", ".codex-plugin"):
        shutil.copytree(ROOT / name, package / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    manifest = "".join(
        f"{_sha(path.read_bytes())}  {path.relative_to(package)}\n"
        for path in sorted(package.rglob("*"))
        if path.is_file()
    ).encode()
    (package / "SHA256SUMS").write_bytes(manifest)
    monkeypatch.setenv("CODEX_HOME", str(home))
    binary = tmp_path / "explicit-controlled-native-boundary"
    binary.write_text("# Never executed: fixed controlled test boundary\n")
    binary.chmod(0o700)
    return NativeCodexSelection(
        installed_root=package,
        package_sha256=_sha(manifest),
        plugin_id="devsecops@devsecops-local",
        executable=binary,
        expected_client_sha256=_sha(binary.read_bytes()),
        expected_client_version=native_codex.CLIENT_VERSION,
        model="exact-controlled-model",
        reasoning_effort="ultra",
        engine_python=Path(sys.executable),
        total_timeout=120,
    )


@pytest.fixture
def diagnosis_scope(tmp_path):
    from test_runtime_diagnosis import measured

    paths = {}
    for name, value in [("baseline", measured()[0]), ("fault", measured("fault", 60, up=0)[0])]:
        paths[name] = tmp_path / (name + ".json")
        paths[name].write_bytes(_canonical(value))
    return module.prepare_diagnosis_scope(**paths, output=tmp_path / "scope", deadline=monotonic() + 30)


@pytest.fixture
def recovery_scope(tmp_path, monkeypatch):
    import test_runtime_recovery as fixtures

    # Synthetic times are set only while constructing explicitly controlled
    # inputs; both actual command executions still use their real current clock.
    anchor = datetime.now(timezone.utc) - timedelta(seconds=165)
    monkeypatch.setattr(fixtures, "NOW", anchor)
    original_setup = fixtures.setup

    def setup(*, now, mutate):
        def current_configuration(configuration, lock, intent):
            mutate(configuration, lock, intent)
            configuration["expires_at"] = (anchor + timedelta(hours=1)).isoformat()
            # Avoid a fixture accidentally aging out during package preflight;
            # explicit temporal tests still cross this bound by a full day.
            lock["freshness_seconds"] = 180

        return original_setup(now=now, mutate=current_configuration)

    monkeypatch.setattr(fixtures, "setup", setup)
    bundles, contract, evidence = fixtures.fixture()
    paths, refs = {}, {}
    for name, value in [
        ("baseline", bundles["baseline"]),
        ("fault", bundles["fault"]),
        ("recovered", bundles["recovery"]),
        ("contract", contract),
    ]:
        paths[name] = tmp_path / (name + ".json")
        paths[name].write_bytes(_canonical(value))
    for index, (name, value) in enumerate(evidence.items()):
        path = tmp_path / f"evidence-{index}"
        path.write_bytes(value if isinstance(value, bytes) else _canonical(value))
        refs[name] = path
    return module.prepare_recovery_scope(
        **paths,
        evidence=refs,
        contract_sha256=_sha(paths["contract"].read_bytes()),
        output=tmp_path / "scope",
        deadline=monotonic() + 30,
    )


@pytest.fixture
def review_scope(correction_case, tmp_path):
    args, _ = correction_case
    require_tool(args)
    (args.repo / "deploy/policy.yaml").write_text(
        (args.repo / "deploy/policy.yaml").read_text().replace("port: 8080", "port: 9090")
    )
    git(args.repo, "add", "--all")
    git(args.repo, "commit", "-qm", "Controlled candidate")
    return module.prepare_review_scope(
        repository=args.repo,
        base_commit=args.base,
        head_commit=git(args.repo, "rev-parse", "HEAD"),
        profile=args.profile,
        profile_sha256=_sha(args.profile.read_bytes()),
        tools={"kubeconform": (Path(args.kubeconform), _sha(Path(args.kubeconform).read_bytes()))},
        schemas=args.schema_location,
        schemas_sha256=_schema_receipt(args.schema_location)["sha256"],
        output=tmp_path / "scope",
        deadline=monotonic() + 30,
    )


class Driver(FixtureDriver):
    def __init__(self, scope):
        super().__init__()
        self.scope = scope

    def native_inputs(self, context):
        self.calls.append(context.phase["id"])
        return (
            self.scope.input_artifacts
            if context.phase["workflow"] == module._scope(self.scope)["workflow"]
            else {}
        )


def pending(tmp_path, scope, installed):
    inputs, identities = inputs_for(tmp_path)
    inputs["package"] = installed.installed_root / "SHA256SUMS"
    inputs["config"]["expires_at"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    identities["package_sha256"] = installed.package_sha256
    identities["config_sha256"] = _sha(_canonical(inputs["config"]))
    journal = RunJournal.create(
        tmp_path / "journal", identities=identities, environment_id=inputs["config"]["environment"]
    )
    driver = Driver(scope)
    engine = LifecycleEngine(journal, inputs=inputs, shared_lock_dir=tmp_path / "locks", _test_driver=driver)
    result = engine.advance()
    while journal.state["active_native"]["workflow"] != module._scope(scope)["workflow"]:
        result = finish_native(engine, result)
    return module.NativeReportCoordinator(engine, installed), driver


@pytest.fixture
def controlled_native(monkeypatch):
    state = {
        "calls": 0,
        "scope": None,
        "selection": None,
        "alter_report": None,
        "after": None,
        "companion": False,
        "result_change": None,
        "execution_change": None,
    }

    def invoke(request_path, **options):
        state["calls"] += 1
        journal_root = options["run_root"]
        request_raw = request_path.read_bytes()
        request = json.loads(request_raw)
        selected = module._scope(state["scope"])
        selection = state["selection"]
        work = journal_root / request["output_directory"]
        began = datetime.now(timezone.utc).isoformat()
        report = module._replay(selected, selection, work, monotonic() + 60, None)
        if state["alter_report"]:
            report = state["alter_report"](deepcopy(report))
        report_path = work / "actual-report.json"
        report_path.write_bytes(_canonical(report))
        artifacts = [
            {
                "name": "report",
                "kind": request["required_artifact_kind"],
                "path": str(report_path.relative_to(journal_root)),
                "sha256": _sha(report_path.read_bytes()),
                "bytes": report_path.stat().st_size,
            }
        ]
        if state["companion"]:
            from devsecops import presentation
            from devsecops.runtime import presentation as runtime_presentation

            html = (
                presentation.render_html(report)
                if selected["workflow"] == "review-kubernetes-change"
                else runtime_presentation.render_html(report, module._KINDS[selected["workflow"]][2])
            )
            html_path = work / "report.html"
            html_path.write_text(html)
            artifacts.append(
                {
                    "name": "html",
                    "kind": "supporting-evidence",
                    "path": str(html_path.relative_to(journal_root)),
                    "sha256": _sha(html_path.read_bytes()),
                    "bytes": html_path.stat().st_size,
                }
            )
        result = {
            "schema_version": 1,
            "request_id": request["request_id"],
            "run_id": request["run_id"],
            "step_id": request["step_id"],
            "request_sha256": options["request_sha256"],
            "status": "completed",
            "gaps": [],
            "artifacts": artifacts,
        }
        if state["result_change"]:
            state["result_change"](result)
        result_raw = _canonical(result)
        (journal_root / request["result_path"]).write_bytes(result_raw)
        trace = b"".join(
            _canonical(row)
            for row in [
                {"type": "thread.started", "thread_id": "00000000-0000-4000-a000-000000000001"},
                {"type": "turn.started"},
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "id": "final", "text": result_raw.decode()},
                },
                {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}},
            ]
        )
        engine_identity = native_codex._engine_identity(
            selection.engine_python,
            cwd=work,
            deadline=monotonic() + 30,
            environ=module._environment(work),
            cancel=None,
        )
        invocation = {
            "executable_sha256": selection.expected_client_sha256,
            "model_requested": selection.model,
            "reasoning_effort_requested": selection.reasoning_effort,
            "client_version": selection.expected_client_version,
            "engine_python": engine_identity,
        }
        frozen = {
            "request.json": request_raw,
            "native-result.json": result_raw,
            "trace.jsonl": trace,
            "invocation.json": _canonical(invocation),
            "prompt.txt": b"Controlled native boundary; no model",
            "stderr.log": b"",
            "result-schema.json": b"{}",
        }
        recorded = []
        for index, item in enumerate(artifacts):
            data = _read(journal_root / item["path"], 16_000_000)
            name = f"artifact-{index}.blob"
            frozen[name] = data
            recorded.append(
                {"path": item["path"], "sha256": _sha(data), "bytes": len(data), "retained": name}
            )
        execution = {
            "execution_status": "complete",
            "scope": "native-handoff",
            "workflow": request["workflow"],
            "exit_code": 0,
            "process_started": True,
            "gaps": [],
            "thread_id": "00000000-0000-4000-a000-000000000001",
            "trace_sha256": _sha(trace),
            "result_sha256": _sha(result_raw),
            "request_sha256": _sha(request_raw),
            "request_id": request["request_id"],
            "run_id": request["run_id"],
            "step_id": request["step_id"],
            "package_sha256": options["package_sha256"],
            "engine_python_identity_sha256": _sha(_canonical(engine_identity)),
            "started_at": began,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "artifacts": recorded,
        }
        if state["execution_change"]:
            state["execution_change"](execution)
        frozen["execution.json"] = _canonical(execution)
        output = options["output"]
        output.mkdir(mode=0o700)
        for name, raw in frozen.items():
            (output / name).write_bytes(raw)
            (output / name).chmod(0o600)
        (output / "manifest.json").write_bytes(
            _canonical(
                {
                    "schema_version": 1,
                    "files": {name: {"sha256": _sha(raw), "bytes": len(raw)} for name, raw in frozen.items()},
                }
            )
        )
        if state["after"]:
            state["after"](output)
        return execution

    monkeypatch.setattr(native_codex, "execute_native_codex", invoke)
    return state


def bind(control, scope, selection):
    control.update(scope=scope, selection=selection)


@pytest.mark.parametrize("fixture_name", ["review_scope", "diagnosis_scope", "recovery_scope"])
def test_real_replay_accepts_domain_report_without_runtime_authority(
    request, fixture_name, tmp_path, installed, controlled_native
):
    scope = request.getfixturevalue(fixture_name)
    coordinator, driver = pending(tmp_path, scope, installed)
    bind(controlled_native, scope, installed)
    controlled_native["companion"] = True
    active = coordinator.engine.journal.state["active_native"]
    before = list(driver.calls)
    result = coordinator.execute(scope)
    assert result.document["execution_mode"] == "controlled-test"
    assert result.document["runtime_authority"] == "not-issued"
    assert result.document["domain_decision"] in ("pass", "fail", "unknown")
    if fixture_name != "review_scope":
        assert result.document["domain_decision"] == "unknown"
    assert coordinator.engine.journal.state["active_native"] is None
    assert driver.calls == before  # No advancement, delivery, or cleanup.
    assert coordinator.report(step_id=active["step_id"], request_sha256=active["request"]["sha256"]) is result
    with pytest.raises(StateError):
        module.ValidatedNativeReport()
    with pytest.raises(StateError):
        pickle.dumps(result)
    with pytest.raises(StateError, match="cannot restore"):
        module.NativeReportCoordinator(coordinator.engine, installed).report(
            step_id=active["step_id"], request_sha256=active["request"]["sha256"]
        )


@pytest.mark.parametrize(
    "change",
    ["wrong-kind", "duplicate", "wrong-byte-count", "wrong-name-join", "retained-bytes", "second-report"],
)
def test_exact_artifact_join_refuses_tampering(
    tmp_path, installed, diagnosis_scope, controlled_native, change
):
    coordinator, _ = pending(tmp_path, diagnosis_scope, installed)
    bind(controlled_native, diagnosis_scope, installed)

    def mutate(result):
        row = result["artifacts"][0]
        if change == "wrong-kind":
            row["kind"] = "supporting-evidence"
        if change == "duplicate":
            result["artifacts"].append(dict(row))
        if change == "wrong-byte-count":
            row["bytes"] += 1
        if change == "wrong-name-join":
            row["name"] = "duplicate-name"
            result["artifacts"].append(dict(row))
        if change == "second-report":
            result["artifacts"].append({**row, "name": "other"})

    controlled_native["result_change"] = mutate
    if change == "retained-bytes":
        controlled_native["after"] = lambda path: (path / "artifact-0.blob").write_bytes(b"changed")
    with pytest.raises((StateError, ValueError)):
        coordinator.execute(diagnosis_scope)
    assert coordinator.engine.journal.state["active_native"] is not None
    with pytest.raises(StateError, match="already claimed"):
        coordinator.execute(diagnosis_scope)
    assert controlled_native["calls"] == 1


@pytest.mark.parametrize("change", ["input", "client", "package", "cancel"])
def test_preflight_changes_stop_before_native(
    tmp_path, installed, diagnosis_scope, controlled_native, change
):
    coordinator, _ = pending(tmp_path, diagnosis_scope, installed)
    bind(controlled_native, diagnosis_scope, installed)
    cancel = threading.Event()
    if change == "input":
        diagnosis_scope.input_artifacts["baseline"].write_bytes(b"{}")
    if change == "client":
        installed.executable.write_bytes(b"changed")
    if change == "package":
        (installed.installed_root / "scripts/devsecops_review.py").write_bytes(b"changed")
    if change == "cancel":
        cancel.set()
    with pytest.raises((StateError, ValueError)):
        coordinator.execute(diagnosis_scope, cancel=cancel)
    assert controlled_native["calls"] == 0


@pytest.mark.parametrize(
    "change", ["invented-pass", "lost-gap", "unknown-field", "error-report", "out-of-turn-time"]
)
def test_report_semantics_and_execution_errors_cannot_masquerade_as_unknown(
    tmp_path, installed, recovery_scope, controlled_native, change
):
    from devsecops.runtime.context import seal

    coordinator, _ = pending(tmp_path, recovery_scope, installed)
    bind(controlled_native, recovery_scope, installed)

    def alter(report):
        if change == "invented-pass":
            report.update(decision="pass", recovery="verified", execution_status="complete")
        elif change == "lost-gap":
            report["gaps"] = report["gaps"][1:]
        elif change == "unknown-field":
            report["fabricated_authority"] = True
        elif change == "error-report":
            return {"kind": "runtime-command-error", "decision": "unknown"}
        else:
            report["observed_at"] = "2020-01-01T00:00:00Z"
        return seal(report, "recovery_sha256")

    controlled_native["alter_report"] = alter
    with pytest.raises((StateError, ValueError, KeyError)):
        coordinator.execute(recovery_scope)
    assert coordinator.engine.journal.state["active_native"] is not None


def test_freshness_crossing_after_replay_blocks_locked_acceptance(
    tmp_path, installed, recovery_scope, controlled_native, monkeypatch
):
    from devsecops.runtime import recovery

    coordinator, _ = pending(tmp_path, recovery_scope, installed)
    bind(controlled_native, recovery_scope, installed)
    original = module._replay

    def replay(*args, **kwargs):
        value = original(*args, **kwargs)
        if args[2].name == "independent":
            actual = recovery.clock
            monkeypatch.setattr(recovery, "clock", lambda now: actual(now) + timedelta(days=1))
        return value

    monkeypatch.setattr(module, "_replay", replay)
    with pytest.raises(StateError, match="freshness"):
        coordinator.execute(recovery_scope)
    assert coordinator.engine.journal.state["active_native"] is not None


def test_domain_findings_global_gaps_and_scope_all_match(
    tmp_path, installed, review_scope, controlled_native
):
    coordinator, _ = pending(tmp_path, review_scope, installed)
    bind(controlled_native, review_scope, installed)

    def changed(report):
        report["scope"]["base"] = "f" * 40
        return report

    controlled_native["alter_report"] = changed
    with pytest.raises(StateError, match="independent fixed replay"):
        coordinator.execute(review_scope)


def test_publication_failure_never_returns_origin_or_retries(
    tmp_path, installed, diagnosis_scope, controlled_native, monkeypatch
):
    coordinator, _ = pending(tmp_path, diagnosis_scope, installed)
    bind(controlled_native, diagnosis_scope, installed)
    original = native_codex._write_private

    def failed(fd, name, raw):
        if name == "validated-report.json":
            raise OSError("controlled-terminal-failure")
        return original(fd, name, raw)

    monkeypatch.setattr(native_codex, "_write_private", failed)
    with pytest.raises(OSError):
        coordinator.execute(diagnosis_scope)
    assert coordinator.engine.journal.state["active_native"] is None
    assert not coordinator._reports
    with pytest.raises(StateError):
        coordinator.execute(diagnosis_scope)


def test_scopes_are_process_bound_and_not_deserializable(diagnosis_scope, monkeypatch):
    with pytest.raises(StateError):
        pickle.dumps(diagnosis_scope)
    with pytest.raises(StateError):
        module.NativeReportScope()
    current = os.getpid()
    monkeypatch.setattr(module.os, "getpid", lambda: current + 1)
    with pytest.raises(StateError, match="this process"):
        module._scope(diagnosis_scope)


def test_expired_preparation_does_not_write(tmp_path):
    path = tmp_path / "input"
    path.write_bytes(b"{}")
    with pytest.raises(StateError, match="deadline"):
        module.prepare_diagnosis_scope(
            baseline=path, fault=path, output=tmp_path / "scope", deadline=monotonic() - 1
        )
    assert not (tmp_path / "scope").exists()


def test_real_helm_fail_verdict_completes_native_execution(tmp_path, installed, controlled_native):
    import yaml

    cache = Path.home() / ".cache/devsecops/v0.1"
    tools = {
        name: (selected, _sha(selected.read_bytes()))
        for name in ("helm", "kubeconform")
        for selected in (Path(os.environ.get("DEVSECOPS_" + name.upper(), cache / "bin" / name)),)
    }
    schemas = Path(os.environ.get("DEVSECOPS_SCHEMAS", cache / "schemas"))
    repository = tmp_path / "helm-repository"
    shutil.copytree(ROOT / "tests/fixtures/helm-web", repository)
    git(repository, "init", "-q", "--template=")
    git(repository, "add", ".")
    git(repository, "commit", "-qm", "Controlled baseline")
    base = git(repository, "rev-parse", "HEAD")
    values = yaml.safe_load((repository / "values/staging.yaml").read_text())
    values.update(yaml.safe_load((repository / "values/risky.yaml").read_text()))
    (repository / "values/staging.yaml").write_text(yaml.safe_dump(values, sort_keys=False))
    git(repository, "add", ".")
    git(repository, "commit", "-qm", "Controlled risk")
    profile = tmp_path / "profile.json"
    profile.write_bytes((ROOT / "profiles/generic.example.json").read_bytes())
    scope = module.prepare_review_scope(
        repository=repository,
        base_commit=base,
        head_commit=git(repository, "rev-parse", "HEAD"),
        profile=profile,
        profile_sha256=_sha(profile.read_bytes()),
        tools=tools,
        schemas=schemas,
        schemas_sha256=_schema_receipt(schemas)["sha256"],
        output=tmp_path / "scope",
        deadline=monotonic() + 30,
    )
    coordinator, _ = pending(tmp_path, scope, installed)
    bind(controlled_native, scope, installed)
    result = coordinator.execute(scope)
    assert result.document["domain_decision"] == "fail"
    assert coordinator.engine.journal.state["active_native"] is None
    assert result.document["runtime_authority"] == "not-issued"


@pytest.mark.parametrize(
    "change", ["global-gap", "tool-pin", "source-manifest", "policy", "findings", "unknown-field"]
)
def test_full_review_comparison_rejects_omitted_or_forged_semantics(
    tmp_path, installed, review_scope, controlled_native, change
):
    coordinator, _ = pending(tmp_path, review_scope, installed)
    bind(controlled_native, review_scope, installed)

    def alter(report):
        if change == "global-gap":
            report["gaps"].append({"code": "invented", "required": True, "message": "fabricated"})
        elif change == "tool-pin":
            report["toolchain"]["kubeconform"]["sha256"] = "f" * 64
        elif change == "source-manifest":
            report["targets"][0]["inputs"]["base"]["sha256"] = "f" * 64
        elif change == "policy":
            report["scope"]["policy"]["require_image_digest"] = True
        elif change == "findings":
            report["targets"][0]["findings"] = []
        else:
            report["fabricated_recovery_claim"] = True
        return report

    controlled_native["alter_report"] = alter
    with pytest.raises((StateError, ValueError), match="independent fixed replay|schema"):
        coordinator.execute(review_scope)


def test_changed_selected_tool_refuses_before_native(
    tmp_path, installed, review_scope, controlled_native, monkeypatch
):
    coordinator, _ = pending(tmp_path, review_scope, installed)
    bind(controlled_native, review_scope, installed)
    original = native_codex._file_digest
    target = module._scope(review_scope)["options"]["tools"]["kubeconform"][0]
    monkeypatch.setattr(
        native_codex,
        "_file_digest",
        lambda path, deadline: "f" * 64 if path == target else original(path, deadline),
    )
    with pytest.raises(StateError, match="tool changed"):
        coordinator.execute(review_scope)
    assert controlled_native["calls"] == 0


@pytest.mark.parametrize("when", ["before-native", "after-terminal"])
def test_actual_utc_expiry_is_not_hidden_by_monotonic_budget(
    tmp_path, installed, diagnosis_scope, controlled_native, monkeypatch, when
):
    coordinator, _ = pending(tmp_path, diagnosis_scope, installed)
    bind(controlled_native, diagnosis_scope, installed)
    real_datetime = module.datetime

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime.now(timezone.utc) + timedelta(hours=2)

    original = native_codex._write_private

    def write(fd, name, raw):
        value = original(fd, name, raw)
        if name == "validated-report.json":
            monkeypatch.setattr(module, "datetime", Later)
        return value

    if when == "before-native":
        monkeypatch.setattr(module, "datetime", Later)
    else:
        monkeypatch.setattr(native_codex, "_write_private", write)
    with pytest.raises(StateError):
        coordinator.execute(diagnosis_scope)
    assert not coordinator._reports
    assert not list(coordinator.engine.journal.run_dir.glob("native-report-*/validated-report.json"))
    assert controlled_native["calls"] == (0 if when == "before-native" else 1)


def test_freshness_crossing_during_terminal_publication_withdraws_marker(
    tmp_path, installed, recovery_scope, controlled_native, monkeypatch
):
    from devsecops.runtime import recovery

    coordinator, _ = pending(tmp_path, recovery_scope, installed)
    bind(controlled_native, recovery_scope, installed)
    original = native_codex._write_private
    actual = recovery.clock

    def write(fd, name, raw):
        result = original(fd, name, raw)
        if name == "validated-report.json":
            monkeypatch.setattr(recovery, "clock", lambda now: actual(now) + timedelta(days=1))
        return result

    monkeypatch.setattr(native_codex, "_write_private", write)
    with pytest.raises(StateError, match="freshness"):
        coordinator.execute(recovery_scope)
    assert coordinator.engine.journal.state["active_native"] is None
    assert not coordinator._reports
    assert not list(coordinator.engine.journal.run_dir.glob("native-report-*/validated-report.json"))


def test_actual_replay_process_deadline_and_cancellation(tmp_path, installed, diagnosis_scope):
    # Test the actual fixed replay launcher with a controlled sleeping process;
    # production package identity tests above prevent such replacement in a run.
    (installed.installed_root / "scripts/devsecops_review.py").write_text("import time\ntime.sleep(30)\n")
    for mode in ("deadline", "cancel"):
        directory = tmp_path / mode
        directory.mkdir()
        event = threading.Event()
        timer = threading.Timer(0.15, event.set) if mode == "cancel" else None
        if timer:
            timer.start()
        started = monotonic()
        try:
            with pytest.raises(StateError, match="replay did not complete"):
                module._replay(
                    module._scope(diagnosis_scope),
                    installed,
                    directory,
                    monotonic() + (0.2 if mode == "deadline" else 10),
                    event,
                )
        finally:
            if timer:
                timer.join()
        assert monotonic() - started < 5
        assert not (directory / "report").exists()


def test_post_acceptance_evidence_tamper_blocks_direct_report_access(
    tmp_path, installed, diagnosis_scope, controlled_native
):
    coordinator, _ = pending(tmp_path, diagnosis_scope, installed)
    bind(controlled_native, diagnosis_scope, installed)
    active = coordinator.engine.journal.state["active_native"]
    result = coordinator.execute(diagnosis_scope)
    proof = module._issued(result)
    trace = next(path for path in proof["files"] if path.name == "trace.jsonl")
    trace.write_bytes(trace.read_bytes() + b"\n")
    with pytest.raises(StateError, match="evidence changed"):
        coordinator.report(step_id=active["step_id"], request_sha256=active["request"]["sha256"])
