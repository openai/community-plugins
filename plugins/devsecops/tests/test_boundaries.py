"""Execution-boundary regressions, including real Helm negative controls."""

from __future__ import annotations

from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import gzip
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import threading
import time

import pytest

from devsecops import cli, engine, process, render, snapshot
from devsecops.profiles import load_profile, relative_path


CACHE = Path.home() / ".cache/devsecops/v0.1"
HELM = Path(os.environ.get("DEVSECOPS_HELM", str(CACHE / "bin/helm")))
KUBECONFORM = Path(os.environ.get("DEVSECOPS_KUBECONFORM", str(CACHE / "bin/kubeconform")))
SCHEMAS = Path(os.environ.get("DEVSECOPS_SCHEMAS", str(CACHE / "schemas")))
REAL_TOOLS = pytest.mark.skipif(not all(path.exists() for path in (HELM, KUBECONFORM, SCHEMAS)), reason="Provisioned real Helm, kubeconform, and local schemas required")


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def commit(repo):
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Synthetic boundary fixture")
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Boundary fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    return repo


def configmap(name="settings"):
    return {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": name}, "data": {"mode": "fixture"}}


def target(kind="manifests", path="manifests", name="fixture"):
    value = {"name": name, "type": kind, "path": path, "namespace": "fixture", "kubernetes_version": "1.34.0"}
    if kind == "helm":
        value.update(release="fixture", values=[], mode="upgrade", api_versions=[])
    return value


def profile(targets=None):
    return {"schema_version": 1, "id": "fixture", "revision": "1", "targets": targets or [target()], "policy": {}}


def write_profile(tmp_path, data=None):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data or profile()))
    return path


def local_review(repo, tmp_path, baseline, data=None):
    path = write_profile(tmp_path, data)
    args = cli.build_parser().parse_args([
        "review", "--repo", str(repo), "--base", baseline, "--head", "WORKTREE",
        "--profile", str(path), "--output", str(tmp_path / "output"),
        "--helm", str(HELM), "--kubeconform", str(KUBECONFORM), "--schema-location", str(SCHEMAS),
    ])
    return engine.review(args)


def chart(root, template=None):
    root.mkdir(parents=True)
    (root / "templates").mkdir()
    (root / "Chart.yaml").write_text("apiVersion: v2\nname: boundary-fixture\nversion: 0.1.0\n")
    (root / "values.yaml").write_text("{}\n")
    (root / "templates/configmap.yaml").write_text(template or "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: settings\ndata:\n  mode: fixture\n")
    return root


def archive(entries):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w:gz") as output:
        for name, content, kind in entries:
            entry = tarfile.TarInfo(name)
            if kind == "link":
                entry.type = tarfile.SYMTYPE
                entry.linkname = "../../outside"
                output.addfile(entry)
            else:
                entry.size = len(content)
                output.addfile(entry, io.BytesIO(content))
    return result.getvalue()


def test_git_snapshots_distinguish_commit_worktree_and_ignored_content(repository, tmp_path):
    root = repository / "manifests"
    root.mkdir()
    (root / "app.yaml").write_text("baseline")
    (repository / ".gitignore").write_text("*.ignored\n")
    baseline = commit(repository)
    (root / "app.yaml").write_text("staged")
    git(repository, "add", "manifests/app.yaml")
    (root / "app.yaml").write_text("working-tree")
    (root / "selected-untracked.yaml").write_text("included")
    (root / "token.ignored").write_text("SENSITIVE_IGNORED")
    (repository / "unselected.yaml").write_text("excluded")
    index_before = (repository / ".git/index").read_bytes()
    baseline_dir, candidate_dir = tmp_path / "base", tmp_path / "candidate"
    first = snapshot.freeze_commit(repository, baseline, ["manifests"], baseline_dir)
    second = snapshot.freeze_worktree(repository, ["manifests"], candidate_dir)
    assert (baseline_dir / "manifests/app.yaml").read_text() == "baseline"
    assert (candidate_dir / "manifests/app.yaml").read_text() == "working-tree"
    assert (candidate_dir / "manifests/selected-untracked.yaml").read_text() == "included"
    assert not (candidate_dir / "manifests/token.ignored").exists()
    assert not (candidate_dir / "unselected.yaml").exists()
    assert first["sha256"] != second["sha256"]
    assert (repository / ".git/index").read_bytes() == index_before
    assert (root / "app.yaml").read_text() == "working-tree"


def test_worktree_tracked_deletion_is_not_resurrected_from_index(repository, tmp_path):
    (repository / "app.yaml").write_text("baseline")
    commit(repository)
    (repository / "app.yaml").unlink()
    receipt = snapshot.freeze_worktree(repository, ["app.yaml"], tmp_path / "candidate")
    assert receipt["file_count"] == 0


def test_nested_symlink_in_selected_worktree_is_rejected(repository, tmp_path):
    folder = repository / "manifests/nested"
    folder.mkdir(parents=True)
    (folder / "app.yaml").write_text("baseline")
    commit(repository)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "app.yaml").write_text("SENSITIVE_EXTERNAL")
    (folder / "app.yaml").unlink()
    folder.rmdir()
    folder.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        snapshot.freeze_worktree(repository, ["manifests"], tmp_path / "candidate")
    assert not (tmp_path / "candidate/manifests/nested/app.yaml").exists()


def test_commit_symlink_and_submodule_modes_are_rejected(repository, tmp_path):
    (repository / "ordinary").write_text("file")
    (repository / "link").symlink_to("ordinary")
    baseline = commit(repository)
    with pytest.raises(ValueError, match="symlink or submodule"):
        snapshot.freeze_commit(repository, baseline, ["link"], tmp_path / "link-snapshot")
    git(repository, "update-index", "--add", "--cacheinfo", f"160000,{baseline},vendor")
    git(repository, "commit", "-qm", "Synthetic gitlink")
    head = git(repository, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="symlink or submodule"):
        snapshot.freeze_commit(repository, head, ["vendor"], tmp_path / "gitlink-snapshot")


def test_worktree_change_during_snapshot_is_rejected(repository, tmp_path, monkeypatch):
    responses = iter([{"app.yaml": b"first"}, {"app.yaml": b"second"}])
    monkeypatch.setattr(snapshot, "_worktree_files", lambda *_: next(responses))
    with pytest.raises(ValueError, match="changed during snapshot"):
        snapshot.freeze_worktree(repository, ["app.yaml"], tmp_path / "candidate")


def test_lfs_pointer_is_not_treated_as_materialized_input(repository, tmp_path):
    (repository / "app.yaml").write_text("version https://git-lfs.github.com/spec/v1\noid sha256:" + "a" * 64 + "\nsize 1234\n")
    baseline = commit(repository)
    with pytest.raises(ValueError, match="(?i)LFS"):
        snapshot.freeze_commit(repository, baseline, ["app.yaml"], tmp_path / "base")
    with pytest.raises(ValueError, match="(?i)LFS"):
        snapshot.freeze_worktree(repository, ["app.yaml"], tmp_path / "candidate")


@pytest.mark.parametrize("path", ["../outside", "/absolute", "a/../b", ".git/config", "a/.git/config", "a//b", "a/./b", "--option", "a\\b"])
def test_untrusted_input_paths_cannot_escape(path):
    with pytest.raises(ValueError):
        relative_path(path)


def test_profile_requires_baseline_trust_or_explicit_external_data_only_input(repository, tmp_path):
    inside = repository / "policy.json"
    inside.write_text(json.dumps(profile()))
    with pytest.raises(ValueError, match="baseline"):
        load_profile(inside, repository)
    malformed = profile()
    malformed["command"] = "SENSITIVE_SENTINEL"
    with pytest.raises(ValueError) as error:
        load_profile(write_profile(tmp_path, malformed), repository)
    assert "SENSITIVE_SENTINEL" not in str(error.value)


@pytest.mark.parametrize("deleted", [False, True])
def test_repository_profile_uses_baseline_despite_candidate_edit_or_deletion(repository, tmp_path, deleted):
    baseline_profile = profile()
    baseline_profile["policy"]["require_image_digest"] = True
    selected = repository / "profile.json"
    raw = json.dumps(baseline_profile).encode()
    selected.write_bytes(raw)
    baseline = commit(repository)
    if deleted:
        selected.unlink()
    else:
        candidate = deepcopy(baseline_profile)
        candidate["policy"]["require_image_digest"] = False
        selected.write_text(json.dumps(candidate))
    loaded, digest = load_profile(selected, repository, base_sha=baseline)
    assert loaded["policy"]["require_image_digest"] is True
    assert digest == hashlib.sha256(raw).hexdigest()


def test_candidate_symlink_cannot_reclassify_baseline_profile_as_trusted_external(repository, tmp_path):
    data = profile()
    data["policy"]["require_image_digest"] = True
    selected = repository / "profile.json"
    selected.write_text(json.dumps(data))
    baseline = commit(repository)
    weaker = deepcopy(data)
    weaker["policy"]["require_image_digest"] = False
    external = tmp_path / "weaker.json"
    external.write_text(json.dumps(weaker))
    selected.unlink()
    selected.symlink_to(external)
    try:
        loaded, _ = load_profile(selected, repository, base_sha=baseline)
    except ValueError:
        return  # Refusing candidate symlinks is also a safe outcome.
    assert loaded["policy"]["require_image_digest"] is True


def test_profile_rejects_duplicate_keys_and_unconfigured_example(repository, tmp_path):
    path = tmp_path / "profile.json"
    path.write_text('{"id":"first","id":"second"}')
    with pytest.raises(ValueError, match="Duplicate"):
        load_profile(path, repository)
    data = profile()
    data["configured"] = False
    with pytest.raises(ValueError, match="unconfigured"):
        load_profile(write_profile(tmp_path, data), repository)


def test_profile_digest_pins_original_bytes_and_selected_target(repository, tmp_path):
    data = profile([target(name="first"), target(name="second", path="other")])
    path = write_profile(tmp_path, data)
    selected, digest = load_profile(path, repository, ["second"])
    assert [entry["name"] for entry in selected["targets"]] == ["second"]
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()


def minimal_report(repo):
    return {"schema_version": 1, "plugin_version": "0.1.0", "status": "complete", "decision": "pass", "scope": {"repo": str(repo)}, "targets": [], "summary": {}, "gaps": [], "ci": None}


def test_atomic_report_manifest_digests_and_refuses_overwrite(repository, tmp_path):
    output = tmp_path / "review"
    cli.write_report(minimal_report(repository), output)
    manifest = json.loads((output / "run-manifest.json").read_text())
    assert manifest["finished"] is True
    for name, entry in manifest["artifacts"].items():
        assert entry["sha256"] == hashlib.sha256((output / name).read_bytes()).hexdigest()
        assert entry["bytes"] == (output / name).stat().st_size
    assert not list(output.glob("*.tmp"))
    with pytest.raises(ValueError, match="already exists"):
        cli.write_report(minimal_report(repository), output)


def test_output_rejects_checkout_and_symlink_parents(repository, tmp_path):
    with pytest.raises(ValueError, match="outside"):
        cli.check_output(repository / "review", repository)
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        cli.check_output(link / "run", repository)


def test_interrupted_report_has_no_terminal_success_manifest(repository, tmp_path, monkeypatch):
    original = cli.os.replace
    def interrupt(source, destination):
        if Path(destination).name == "report.md":
            raise OSError("Synthetic output failure")
        return original(source, destination)
    monkeypatch.setattr(cli.os, "replace", interrupt)
    output = tmp_path / "interrupted"
    with pytest.raises(OSError):
        cli.write_report(minimal_report(repository), output)
    assert not (output / "run-manifest.json").exists()


def test_cli_error_writes_terminal_unknown_manifest_without_overwriting(repository, tmp_path, capsys):
    output = tmp_path / "error-run"
    command = ["review", "--repo", str(repository), "--base", "missing-revision", "--profile", str(tmp_path / "absent.json"), "--output", str(output)]
    assert cli.main(command) == 2
    report = json.loads((output / "report.json").read_text())
    manifest = json.loads((output / "run-manifest.json").read_text())
    assert report["decision"] == manifest["decision"] == "unknown"
    assert report["run_id"] == manifest["run_id"]
    assert report["finished_at"] == manifest["finished_at"]
    assert report["run_id"] and manifest["finished"] is True
    before = (output / "run-manifest.json").read_bytes()
    assert cli.main(command) == 2
    assert (output / "run-manifest.json").read_bytes() == before
    assert json.loads(capsys.readouterr().err.splitlines()[0])["decision"] == "unknown"


def test_subprocess_output_limit_and_errors_never_echo_secrets(tmp_path):
    secret = "SENSITIVE_STDERR_SENTINEL"
    with pytest.raises(process.ToolError) as error:
        process.run([sys.executable, "-c", f"import sys; sys.stderr.write({secret!r}*1000)"], cwd=tmp_path, limit=100)
    assert secret not in str(error.value)
    assert "output limit" in str(error.value)


def test_subprocess_timeout_kills_descendants(tmp_path):
    marker = tmp_path / "child-completed"
    child = f"import time,pathlib; time.sleep(.4); pathlib.Path({str(marker)!r}).write_text('bad')"
    script = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(5)"
    with pytest.raises(process.ToolError, match="time limit"):
        process.run([sys.executable, "-c", script], cwd=tmp_path, timeout=.1)
    time.sleep(.5)
    assert not marker.exists()


def test_subprocess_cancellation_terminates_child(tmp_path, monkeypatch):
    marker = tmp_path / "cancelled-child-completed"
    script = f"import time,pathlib; time.sleep(.3); pathlib.Path({str(marker)!r}).write_text('bad')"
    def interrupt(_):
        raise KeyboardInterrupt
    with monkeypatch.context() as patch:
        patch.setattr(process.time, "sleep", interrupt)
        with pytest.raises(KeyboardInterrupt):
            process.run([sys.executable, "-c", script], cwd=tmp_path, timeout=5)
    time.sleep(.5)
    assert not marker.exists(), "Cancellation left the tool process running after review stopped"


def test_subprocess_receipt_does_not_contain_diagnostics_or_arguments(tmp_path):
    secret = "SENSITIVE_OUTPUT_SENTINEL"
    code, out, err, receipt = process.run([sys.executable, "-c", f"print({secret!r})"], cwd=tmp_path)
    assert code == 0 and secret.encode() in out and err == b""
    assert secret not in json.dumps(receipt)
    assert receipt["stdout_sha256"] == hashlib.sha256(out).hexdigest()


@pytest.mark.parametrize("archived", [False, True])
@pytest.mark.parametrize("reference", ["https://untrusted.invalid/schema.json", "file:///etc/passwd", "../schema.json"])
def test_chart_schema_external_refs_are_rejected_before_helm(tmp_path, archived, reference):
    root = chart(tmp_path / "chart")
    payload = json.dumps({"type": "object", "$defs": {"nested": {"$ref": reference}}}).encode()
    if archived:
        (root / "charts").mkdir()
        (root / "charts/child.tgz").write_bytes(archive([("child/values.schema.json", payload, "file")]))
    else:
        (root / "values.schema.json").write_bytes(payload)
    with pytest.raises(ValueError, match="external references"):
        render.inspect_chart(root)


@pytest.mark.parametrize("archived", [False, True])
@pytest.mark.parametrize("name", ["values.yaml", "Chart.yaml", "values.schema.json"])
def test_template_metadata_names_are_inspected_as_templates(tmp_path, archived, name):
    root = chart(tmp_path / "chart")
    raw = b'{{- if .Values.enabled }}\napiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: templated-values\n{{- end }}\n'
    if archived:
        (root / "charts").mkdir()
        (root / "charts/child.tgz").write_bytes(archive([("child/templates/" + name, raw, "file")]))
    else:
        (root / "templates" / name).write_bytes(raw)
    assert render.inspect_chart(root) is False


@pytest.mark.parametrize("archived", [False, True])
@pytest.mark.parametrize("name", ["values.yaml", "values.schema.json", "resource.json", "NOTES.txt"])
def test_lookup_is_detected_in_every_template_file_type(tmp_path, archived, name):
    root = chart(tmp_path / "chart")
    raw = b'{{ lookup "v1" "Secret" "default" "private" }}'
    if archived:
        (root / "charts").mkdir()
        (root / "charts/child.tgz").write_bytes(archive([("child/templates/" + name, raw, "file")]))
    else:
        (root / "templates" / name).write_bytes(raw)
    assert render.inspect_chart(root) is True


@pytest.mark.parametrize("name", ["values.yaml", "Chart.yaml"])
def test_actual_chart_metadata_still_rejects_template_syntax(tmp_path, name):
    root = chart(tmp_path / "chart")
    (root / name).write_text('{{ .Values.invalidMetadata }}\n')
    with pytest.raises(ValueError):
        render.inspect_chart(root)


@pytest.mark.parametrize("archived", [False, True])
@pytest.mark.parametrize("name", ["Chart.yaml", "values.yaml", "values.schema.json"])
def test_dependency_named_templates_cannot_hide_unsafe_metadata(tmp_path, archived, name):
    root = chart(tmp_path / "chart")
    child = chart(tmp_path / "child" if archived else root / "charts/templates")
    (child / "Chart.yaml").write_text("apiVersion: v2\nname: templates\nversion: 0.1.0\n")
    payload = ('{"$ref":"https://untrusted.invalid/schema.json"}'
               if name.endswith(".json") else '{{ .Values.invalidMetadata }}\n')
    (child / name).write_text(payload)
    if archived:
        (root / "charts").mkdir()
        entries = [("templates/" + str(p.relative_to(child)), p.read_bytes(), "file")
                   for p in child.rglob("*") if p.is_file()]
        (root / "charts/templates.tgz").write_bytes(archive(entries))
    with pytest.raises(ValueError):
        render.inspect_chart(root)


@REAL_TOOLS
def test_real_helm_renders_template_named_values_yaml(tmp_path):
    root = chart(tmp_path / "chart", '{{- if true }}\napiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: rendered-values\ndata:\n  mode: fixture\n{{- end }}\n')
    (root / "templates/configmap.yaml").rename(root / "templates/values.yaml")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = render.collect(tmp_path, target("helm", "chart"), HELM, scratch)
    assert not result["gaps"]
    assert len(result["records"]) == 1
    assert result["records"][0]["body"]["metadata"]["name"] == "rendered-values"


@REAL_TOOLS
def test_real_helm_crd_equality_enum_remains_a_string(tmp_path):
    chart(tmp_path / "chart", 'apiVersion: apiextensions.k8s.io/v1\nkind: CustomResourceDefinition\nmetadata:\n  name: fixtures.example.test\nspec:\n  group: example.test\n  scope: Namespaced\n  names:\n    plural: fixtures\n    singular: fixture\n    kind: Fixture\n  versions:\n    - name: v1\n      served: true\n      storage: true\n      schema:\n        openAPIV3Schema:\n          type: object\n          properties:\n            operator:\n              type: string\n              enum:\n                - =\n                - "!="\n')
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = render.collect(tmp_path, target("helm", "chart"), HELM, scratch)
    assert not result["gaps"]
    assert len(result["records"]) == 1
    schema = result["records"][0]["body"]["spec"]["versions"][0]["schema"]["openAPIV3Schema"]
    assert schema["properties"]["operator"]["enum"] == ["=", "!="]


@pytest.mark.parametrize("name,kind", [("../outside", "file"), ("/absolute", "file"), ("child/link", "link")])
def test_chart_archives_reject_links_and_traversal(tmp_path, name, kind):
    root = chart(tmp_path / "chart")
    (root / "charts").mkdir()
    (root / "charts/child.tgz").write_bytes(archive([(name, b"data", kind)]))
    with pytest.raises(ValueError, match="Unsafe"):
        render.inspect_chart(root)


def test_chart_archive_expansion_depth_is_bounded(tmp_path):
    root = chart(tmp_path / "chart")
    payload = archive([("leaf/values.yaml", b"{}", "file")])
    for _ in range(6):
        payload = archive([("child/charts/nested.tgz", payload, "file")])
    (root / "charts").mkdir()
    (root / "charts/nested.tgz").write_bytes(payload)
    with pytest.raises(ValueError, match="expansion limit"):
        render.inspect_chart(root)


def test_chart_archive_declared_expansion_size_is_bounded_before_read(tmp_path):
    root = chart(tmp_path / "chart")
    entry = tarfile.TarInfo("child/oversized.yaml")
    entry.size = 64_000_001
    (root / "charts").mkdir()
    (root / "charts/oversized.tgz").write_bytes(gzip.compress(entry.tobuf() + b"\0" * 1024))
    with pytest.raises(ValueError, match="oversized"):
        render.inspect_chart(root)


def test_corrupt_chart_archive_produces_sanitized_value_error(tmp_path):
    root = chart(tmp_path / "chart")
    (root / "charts").mkdir()
    (root / "charts/broken.tgz").write_bytes(b"SENSITIVE_CORRUPT_ARCHIVE")
    with pytest.raises(ValueError) as error:
        render.inspect_chart(root)
    assert "SENSITIVE_CORRUPT_ARCHIVE" not in str(error.value)


@REAL_TOOLS
@pytest.mark.parametrize("archived", [False, True])
def test_remote_schema_dialect_is_rejected_with_zero_loopback_requests(tmp_path, archived):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"type":"object"}')
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    worker.start()
    try:
        root = chart(tmp_path / "chart")
        schema = json.dumps({"$schema": f"http://127.0.0.1:{server.server_address[1]}/dialect", "type": "object"}).encode()
        if archived:
            (root / "charts").mkdir()
            (root / "charts/child.tgz").write_bytes(archive([("child/values.schema.json", schema, "file")]))
        else:
            (root / "values.schema.json").write_bytes(schema)
        scratch = tmp_path / "scratch"
        scratch.mkdir()
        with pytest.raises(ValueError, match="dialect"):
            render.collect(tmp_path, target("helm", "chart"), HELM, scratch)
        assert requests == []
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=1)


@pytest.mark.parametrize("bad_dialect", [[], {}])
def test_schema_dialect_invalid_type_is_sanitized(bad_dialect):
    with pytest.raises(ValueError):
        render.local_schema_only({"$schema": bad_dialect})


def test_manifest_sources_resolve_to_actual_files(tmp_path):
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    (manifests / "one.yaml").write_text(json.dumps(configmap("one")))
    (manifests / "two.yaml").write_text(json.dumps(configmap("two")))
    result = render.collect(tmp_path, target(), None, tmp_path)
    assert {record["source"] for record in result["records"]} == {"manifests/one.yaml", "manifests/two.yaml"}
    assert all(record.get("document_index", 0) == 0 for record in result["records"])


def test_git_repository_subdirectory_is_rejected(repository, tmp_path):
    (repository / "root.txt").write_text("fixture")
    baseline = commit(repository)
    subdirectory = repository / "subdirectory"
    subdirectory.mkdir()
    with pytest.raises(ValueError, match="Git worktree root"):
        local_review(subdirectory, tmp_path, baseline)


@REAL_TOOLS
def test_empty_manifest_target_cannot_pass(repository, tmp_path):
    (repository / "manifests").mkdir()
    (repository / "manifests/empty.yaml").write_text("# No resources\n")
    baseline = commit(repository)
    report = local_review(repository, tmp_path, baseline)
    assert report["decision"] == "unknown"
    assert report["summary"]["required_gaps"] > 0


@REAL_TOOLS
def test_incomplete_identity_inventory_suppresses_false_deletion(repository, tmp_path):
    folder = repository / "manifests"
    folder.mkdir()
    namespace = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "team"}}
    (folder / "namespace.yaml").write_text(json.dumps(namespace))
    baseline = commit(repository)
    (folder / "duplicate.yaml").write_text(json.dumps(namespace))
    report = local_review(repository, tmp_path, baseline)
    assert report["decision"] == "unknown"
    assert not report["targets"][0]["changes"]
    assert not report["targets"][0]["findings"]
    assert any(gap["code"] == "comparison-incomplete" for gap in report["targets"][0]["gaps"])


@REAL_TOOLS
def test_cross_target_identity_collision_is_unknown(repository, tmp_path):
    for name in ("first", "second"):
        folder = repository / name
        folder.mkdir()
        (folder / "config.yaml").write_text(json.dumps(configmap()))
    baseline = commit(repository)
    data = profile([target(name="first", path="first"), target(name="second", path="second")])
    report = local_review(repository, tmp_path, baseline, data)
    assert report["decision"] == "unknown"
    assert all(item["decision"] == "unknown" for item in report["targets"])
    assert all(any(gap["code"] == "cross-target-identity" for gap in item["gaps"]) for item in report["targets"])


@REAL_TOOLS
def test_real_helm_nondeterministic_resource_is_unknown(tmp_path):
    chart(tmp_path / "chart", 'apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: settings\ndata:\n  nonce: {{ randAlphaNum 24 | quote }}\n')
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = render.collect(tmp_path, target("helm", "chart"), HELM, scratch)
    assert any(gap["code"] == "helm-nondeterministic" for gap in result["gaps"])


@REAL_TOOLS
def test_real_helm_random_comments_do_not_change_semantic_output(tmp_path):
    chart(tmp_path / "chart", '# ignored nonce {{ randAlphaNum 24 }}\napiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: settings\ndata:\n  mode: fixture\n')
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = render.collect(tmp_path, target("helm", "chart"), HELM, scratch)
    assert not any(gap["code"] == "helm-nondeterministic" for gap in result["gaps"])
    assert len(result["records"]) == 1


@REAL_TOOLS
def test_real_helm_missing_vendored_dependency_is_explicit(tmp_path):
    root = chart(tmp_path / "chart")
    with (root / "Chart.yaml").open("a") as output:
        output.write("dependencies:\n- name: missing-child\n  version: 1.0.0\n  repository: https://unused.invalid\n")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = render.collect(tmp_path, target("helm", "chart"), HELM, scratch)
    assert any(gap["code"] == "helm-render-failed" for gap in result["gaps"])
    assert not result["records"]


@REAL_TOOLS
def test_real_helm_lookup_is_explicitly_incomplete(tmp_path):
    chart(tmp_path / "chart", '{{ $ignored := lookup "v1" "ConfigMap" "default" "example" }}\napiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: settings\n')
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = render.collect(tmp_path, target("helm", "chart"), HELM, scratch)
    assert any(gap["code"] == "helm-lookup" for gap in result["gaps"])
    assert len(result["records"]) == 1
