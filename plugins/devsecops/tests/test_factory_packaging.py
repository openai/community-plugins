"""The portable factory entry point must not depend on the author checkout."""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import zipfile

import pytest

from scripts import build_plugin


def test_report_version_must_match_packaged_release():
    files = build_plugin.package_files()
    files["src/devsecops/__init__.py"] = b'__version__ = "0.0.0"\n'
    with pytest.raises(ValueError, match="Runtime report version"):
        build_plugin.validate(files)


def test_missing_schema_selector_rejects_package():
    files = build_plugin.package_files()
    files.pop('src/devsecops/schema_catalog.py')
    with pytest.raises(ValueError, match='Schema bootstrap'):
        build_plugin.validate(files)


def test_package_contains_factory_assets_and_excludes_operator_config(tmp_path, monkeypatch):
    root = tmp_path / "source"
    (root / "demo/software-factory/config").mkdir(parents=True)
    (root / "profiles/platform-one/approved").mkdir(parents=True)
    for relative in ["demo/software-factory/config/demo-config.example.json",
                     "demo/software-factory/config/registry1-blob-download-policy.example.json",
                     "demo/software-factory/config/private.json",
                     "profiles/platform-one/approved/bigbang.example.json", "profiles/platform-one/approved/private.json"]:
        (root / relative).write_text("{}")
    monkeypatch.setattr(build_plugin, "ROOT", root)
    files = build_plugin.package_files()
    assert "demo/software-factory/config/demo-config.example.json" in files
    assert "demo/software-factory/config/registry1-blob-download-policy.example.json" in files
    assert "profiles/platform-one/approved/bigbang.example.json" in files
    assert not any("private.json" in path for path in files)


def test_portable_stack_packages_only_selected_public_assets(tmp_path, monkeypatch):
    root = tmp_path / "source"
    demo = root / "demo/portable-gitops"
    demo.mkdir(parents=True)
    names = {"README.md", "versions.json", "acquire.sh", "bundle_inputs.py", "pipeline.py", "scan_policy.py", "stack.py"}
    for name in names | {"owner.json", "private.env", ".env", "test_portable_stack.py"}:
        (demo / name).write_text("controlled input")
    (demo / "state").mkdir()
    (demo / "state/credentials.json").write_text("private fixture")
    monkeypatch.setattr(build_plugin, "ROOT", root)
    assert set(build_plugin.package_files()) == {"demo/portable-gitops/" + name for name in names}


@pytest.mark.parametrize("missing", ["acquire.sh", "scan_policy.py"])
def test_partial_portable_stack_rejects_package(missing):
    files = build_plugin.package_files()
    files.pop("demo/portable-gitops/" + missing)
    with pytest.raises(ValueError, match="Portable stack"):
        build_plugin.validate(files)


@pytest.mark.parametrize("missing", [
    "demo/software-factory/scenarios/ironbank-mission-recovery-v1/scenario.json",
    "src/devsecops/factory/state.py", "schemas/factory-state.schema.json",
    "src/devsecops/factory/lifecycle.py", "docs/interfaces/factory-lifecycle.md",
    "skills/run-software-factory-demo/SKILL.md",
    "skills/run-software-factory-demo/agents/openai.yaml",
    "skills/run-software-factory-demo/references/native-handoff.md",
    "schemas/native-request.schema.json", "schemas/native-result.schema.json",
    "demo/software-factory/charts/mission-status/values.schema.json",
    "demo/software-factory/charts/mission-status/templates/networkpolicy.yaml",
    "demo/software-factory/apps/mission-status/probe.py",
])
def test_missing_canonical_graph_or_lifecycle_contract_rejects_factory_package(missing):
    files = build_plugin.package_files()
    files.pop(missing)
    with pytest.raises(ValueError, match="Factory runtime"):
        build_plugin.validate(files)


@pytest.mark.parametrize('missing', [
    'src/devsecops/factory/platform_inputs.py',
    'src/devsecops/factory/owned_service_links.py',
    'src/devsecops/factory/service_link_remote.py',
    'src/devsecops/factory/service_link_local.py',
    'src/devsecops/factory/service_link_guest.py',
    'src/devsecops/adapters/owned_route.py',
    'src/devsecops/factory/qemu_acceleration.py',
    'docs/interfaces/qemu-acceleration.md',
])
def test_missing_original_service_wiring_rejects_portable_package(missing):
    files = build_plugin.package_files()
    build_plugin.validate(files)
    files.pop(missing)
    with pytest.raises(ValueError, match='Canonical production composition'):
        build_plugin.validate(files)


@pytest.mark.parametrize('missing', [
    'src/devsecops/factory/ci_publication.py',
    'src/devsecops/factory/git_native_request.py',
    'src/devsecops/factory/git_receive_http.py',
    'src/devsecops/factory/git_request_claim.py',
    'src/devsecops/factory/git_request_framing.py',
    'src/devsecops/factory/git_writer_identity.py',
    'src/devsecops/factory/owned_checkout.py',
    'src/devsecops/factory/owned_ci.py',
    'src/devsecops/factory/runner_install.py',
    'src/devsecops/factory/services_remote.py',
    'src/devsecops/runtime/probe_observations.py',
])
def test_missing_original_production_producer_rejects_portable_package(missing):
    files = build_plugin.package_files()
    build_plugin.validate(files)
    files.pop(missing)
    with pytest.raises(ValueError, match='Canonical production composition'):
        build_plugin.validate(files)


@pytest.mark.parametrize('missing', [
    'src/devsecops/runtime/diagnosis.py', 'src/devsecops/runtime/presentation.py',
    'schemas/runtime-collection.schema.json', 'schemas/runtime-diagnosis.schema.json',
    'src/devsecops/adapters/kubernetes.py', 'skills/diagnose-gitops-deployment/agents/openai.yaml',
])
def test_missing_runtime_dependency_rejects_package(missing):
    files = build_plugin.package_files()
    files.pop(missing)
    with pytest.raises(ValueError, match='Runtime evidence commands'):
        build_plugin.validate(files)


@pytest.mark.parametrize('missing', [
    'schemas/runtime-recovery.schema.json', 'schemas/runtime-recovery-contract.schema.json',
    'schemas/runtime-recovery-inventory.schema.json', 'schemas/runtime-git-delivery.schema.json',
    'schemas/runtime-native-execution.schema.json', 'docs/interfaces/runtime-recovery.md',
    'skills/verify-gitops-recovery/SKILL.md', 'skills/verify-gitops-recovery/agents/openai.yaml',
])
def test_missing_recovery_dependency_rejects_package(missing):
    files = build_plugin.package_files()
    files.pop(missing)
    with pytest.raises(ValueError, match='Recovery verifier|Native provenance'):
        build_plugin.validate(files)


@pytest.mark.parametrize('missing', [
    'cosign.lock.json', 'scripts/bootstrap_cosign.py', 'docs/interfaces/signatures.md',
])
def test_missing_signature_toolchain_rejects_package(missing):
    files = build_plugin.package_files()
    files.pop(missing)
    with pytest.raises(ValueError, match='Signature verifier'):
        build_plugin.validate(files)


@pytest.mark.parametrize('missing', [
    'supplychain.lock.json', 'scripts/bootstrap_supplychain.py', 'scripts/build_mission.py',
    'src/devsecops/factory/build.py', 'docs/interfaces/build-chain.md',
    'src/devsecops/adapters/database_zstd.py', 'docs/interfaces/database-archives.md',
    'src/devsecops/adapters/scanner_catalog.py',
    'demo/software-factory/ci/gitlab-ci.yml', 'demo/software-factory/ci/Jenkinsfile',
])
def test_missing_artifact_chain_dependency_rejects_package(missing):
    files = build_plugin.package_files()
    files.pop(missing)
    with pytest.raises(ValueError, match='Artifact chain'):
        build_plugin.validate(files)


@pytest.mark.parametrize('missing', [
    'schemas/http-probe-request.schema.json', 'schemas/http-probe-receipt.schema.json',
    'docs/interfaces/http-probes.md',
])
def test_missing_probe_contract_rejects_package(missing):
    files = build_plugin.package_files()
    files.pop(missing)
    with pytest.raises(ValueError, match='HTTP probe'):
        build_plugin.validate(files)


@pytest.mark.parametrize('missing', ['scripts/native_codex.py', 'docs/interfaces/native-codex.md'])
def test_missing_native_driver_component_rejects_package(missing):
    files = build_plugin.package_files()
    files.pop(missing)
    with pytest.raises(ValueError, match='Native Codex driver'):
        build_plugin.validate(files)


@pytest.mark.parametrize('missing', ['scripts/assess_publisher_trust.py', 'docs/interfaces/publisher-trust.md'])
def test_missing_publisher_trust_component_rejects_package(missing):
    files = build_plugin.package_files()
    files.pop(missing)
    with pytest.raises(ValueError, match='Publisher trust'):
        build_plugin.validate(files)


@pytest.mark.parametrize('missing', ['src/devsecops/factory/runtime_recipe.py', 'docs/interfaces/base-qualification.md'])
def test_missing_base_qualification_component_rejects_package(missing):
    files = build_plugin.package_files()
    files.pop(missing)
    with pytest.raises(ValueError, match='Base qualification'):
        build_plugin.validate(files)


def test_missing_native_provenance_guide_rejects_package():
    files = build_plugin.package_files()
    files.pop('docs/interfaces/native-provenance.md')
    with pytest.raises(ValueError, match='Native provenance'):
        build_plugin.validate(files)


@pytest.mark.parametrize('missing', ['docs/interfaces/mission-executor.md',
                                    'demo/software-factory/ci/mission-build.example.json'])
def test_missing_mission_executor_component_rejects_package(missing):
    files = build_plugin.package_files()
    files.pop(missing)
    with pytest.raises(ValueError, match='Mission executor'):
        build_plugin.validate(files)


@pytest.mark.parametrize('missing', ['src/devsecops/adapters/staging_registry.py',
    'src/devsecops/factory/mission_delivery.py', 'scripts/stage_mission.py',
    'docs/interfaces/mission-staging.md', 'demo/software-factory/ci/mission-staging.example.json'])
def test_missing_mission_staging_component_rejects_package(missing):
    files = build_plugin.package_files()
    files.pop(missing)
    with pytest.raises(ValueError, match='Mission staging'):
        build_plugin.validate(files)


@pytest.mark.parametrize('missing', ['src/devsecops/adapters/vulnerability_policy.py',
    'src/devsecops/factory/mission_security_operation.py', 'scripts/assess_mission.py',
    'docs/interfaces/mission-security.md', 'demo/software-factory/ci/mission-security.example.json',
    'demo/software-factory/ci/mission-security-policy.example.json'])
def test_missing_mission_security_component_rejects_package(missing):
    files = build_plugin.package_files()
    files.pop(missing)
    with pytest.raises(ValueError, match='Mission security'):
        build_plugin.validate(files)


@pytest.mark.parametrize('missing', ['src/devsecops/adapters/local_signing.py',
    'src/devsecops/factory/mission_signing_operation.py', 'scripts/sign_mission.py',
    'scripts/generate_lab_signing_key.py', 'docs/interfaces/mission-signing.md',
    'demo/software-factory/ci/mission-signing-trust.example.json'])
def test_missing_mission_signing_component_rejects_package(missing):
    files = build_plugin.package_files()
    files.pop(missing)
    with pytest.raises(ValueError, match='Mission signing'):
        build_plugin.validate(files)


@pytest.mark.parametrize('missing', ['src/devsecops/adapters/mission_evidence.py',
    'src/devsecops/factory/mission_publication_operation.py', 'scripts/publish_mission.py',
    'docs/interfaces/mission-publication.md', 'demo/software-factory/ci/mission-publication.example.json'])
def test_missing_mission_publication_component_rejects_package(missing):
    files = build_plugin.package_files()
    files.pop(missing)
    with pytest.raises(ValueError, match='Mission publication'):
        build_plugin.validate(files)


@pytest.mark.parametrize('missing', ['src/devsecops/adapters/mission_values.py',
                                    'src/devsecops/factory/mission_proposal_operation.py',
                                    'scripts/publish_and_propose_mission.py',
                                    'docs/interfaces/deployment-proposal.md'])
def test_missing_deployment_proposal_component_rejects_package(missing):
    files = build_plugin.package_files()
    files.pop(missing)
    with pytest.raises(ValueError, match='Deployment proposal'):
        build_plugin.validate(files)


@pytest.mark.parametrize('missing', ['src/devsecops/factory/deployment_proposal.py',
                                    'src/devsecops/factory/mission_publication_operation.py'])
def test_missing_publication_proposal_composition_dependency_rejects_package(missing):
    files = build_plugin.package_files()
    files.pop(missing)
    with pytest.raises(ValueError, match='Publication/proposal operation|Mission publication'):
        build_plugin.validate(files)


@pytest.mark.parametrize('missing', ['scripts/collect_gitlab_pipeline.py',
                                    'docs/interfaces/gitlab-pipeline.md'])
def test_missing_gitlab_pipeline_component_rejects_package(missing):
    files = build_plugin.package_files()
    files.pop(missing)
    with pytest.raises(ValueError, match='GitLab pipeline collection'):
        build_plugin.validate(files)


def test_extracted_factory_with_spaces_does_not_import_author_checkout(tmp_path):
    archive = tmp_path / "candidate.zip"
    build_plugin.build(archive)
    extracted = tmp_path / "clean package with spaces"
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(extracted)
    plugin = extracted / "devsecops"
    env = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}}
    result = subprocess.run([sys.executable, str(plugin / "scripts/factory_demo.py"), "doctor", "--profile", "bigbang-live",
                             "--config", str(plugin / "demo/software-factory/config/demo-config.example.json")],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["execution_status"] == "complete"
    assert report["live_qualification"] == "not_performed"
    assert report["readiness"] == "unknown"
    assert "lifecycle-implementation-pending" in {gap["code"] for gap in report["gaps"]}


def test_extracted_runtime_cli_with_spaces_handles_missing_evidence(tmp_path):
    archive = tmp_path / 'runtime candidate.zip'
    build_plugin.build(archive)
    extracted = tmp_path / 'clean runtime package with spaces'
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(extracted)
    plugin = extracted / 'devsecops'
    output = tmp_path / 'new result with spaces'
    env = {key: value for key, value in os.environ.items() if key not in {'PYTHONPATH', 'PYTHONHOME'}}
    result = subprocess.run([sys.executable, str(plugin / 'scripts/devsecops_review.py'), 'diagnose-gitops',
                             '--fault', str(tmp_path / 'missing.json'), '--output', str(output)],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 2, result.stderr
    summary = json.loads(result.stdout)
    assert summary['decision'] == 'unknown'
    diagnosis = json.loads((output / 'diagnosis.json').read_text())
    assert diagnosis['inputs']['baseline']['state'] == 'missing'
    assert diagnosis['inputs']['fault']['state'] == 'invalid'
    assert (output / 'runtime-manifest.json').exists()
    assert 'Incomplete GitOps evidence' in (output / 'report.html').read_text()


def test_extracted_recovery_cli_preserves_unverified_sources(tmp_path):
    from test_runtime_recovery_cli import command_inputs
    args, _ = command_inputs(tmp_path)
    archive = tmp_path / 'recovery candidate.zip'
    build_plugin.build(archive)
    extracted = tmp_path / 'clean recovery package with spaces'
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(extracted)
    plugin = extracted / 'devsecops'
    output = tmp_path / 'recovery result with spaces'
    env = {key: value for key, value in os.environ.items() if key not in {'PYTHONPATH', 'PYTHONHOME'}}
    result = subprocess.run([sys.executable, str(plugin / 'scripts/devsecops_review.py'), *args,
                             '--output', str(output)], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 2 and not result.stderr
    assert json.loads(result.stdout)['recovery'] == 'unknown'
    report = json.loads((output / 'recovery.json').read_bytes())
    assert not report['provenance']['verified'] and report['conditions']
    assert report['execution_mode'] == 'controlled-test'
    manifest = json.loads((output / 'runtime-manifest.json').read_bytes())
    assert manifest['command'] == 'verify-gitops-recovery'
    assert 'RECOVERY UNKNOWN' in (output / 'report.html').read_text()


def test_extracted_signature_verifier_uses_packaged_lock_and_real_cosign(tmp_path):
    binary = Path(os.environ.get('DEVSECOPS_COSIGN', Path.home() / '.cache/devsecops/cosign-v3.1.4/bin/cosign'))
    if not binary.is_file():
        pytest.skip('Real pinned Cosign unavailable; provision scripts/bootstrap_cosign.py explicitly')
    lock = json.loads((build_plugin.ROOT / 'cosign.lock.json').read_text())
    architecture = {'aarch64': 'arm64', 'x86_64': 'amd64'}.get(platform.machine(), platform.machine())
    expected = lock['tools'][platform.system().lower() + '-' + architecture]['sha256']
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == expected, 'Signing tool must match the release lock'
    archive = tmp_path / 'signature candidate.zip'
    build_plugin.build(archive)
    extracted = tmp_path / 'clean signature package with spaces'
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(extracted)
    plugin = extracted / 'devsecops'
    lab = tmp_path / 'disposable signing lab'
    lab.mkdir(mode=0o700)
    payload = {
        'critical': {'identity': {'docker-reference': 'registry.example.test/mission'},
                     'image': {'docker-manifest-digest': 'sha256:' + 'a' * 64},
                     'type': 'cosign container image signature'},
    }
    (lab / 'payload.json').write_text(json.dumps(payload))
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(lab), 'XDG_CACHE_HOME': str(lab), 'COSIGN_PASSWORD': ''}
    for args in (['generate-key-pair', '--output-key-prefix', 'lab'],
                 ['sign-blob', '--key', 'lab.key', '--use-signing-config=false', '--tlog-upload=false',
                  '--bundle', 'bundle.json', 'payload.json']):
        result = subprocess.run([str(binary), *args], cwd=lab, env=env, capture_output=True, timeout=15)
        assert result.returncode == 0, 'Disposable local signing failed'
    code = '''import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from devsecops.adapters import signatures
assert Path(signatures.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
lab = Path(sys.argv[2])
report = signatures.verify_image_signature(
    expected_repository="registry.example.test/mission", expected_digest="sha256:" + "a" * 64,
    public_key=lab/"lab.pub", payload=lab/"payload.json", bundle=lab/"bundle.json", cosign=Path(sys.argv[3]))
print(json.dumps(report))
'''
    result = subprocess.run([sys.executable, '-c', code, str(plugin / 'src'), str(lab), str(binary)],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, 'Extracted signature verifier failed'
    report = json.loads(result.stdout)
    assert report['observations']['verification']['decision'] == 'pass'
    assert report['decision'] == 'unknown' and report['execution_status'] == 'partial'
    assert 'signer-authority-unverified' in {gap['code'] for gap in report['gaps']}


def test_archive_and_install_source_have_identical_content_identity(tmp_path, monkeypatch):
    # Both distribution formats consume the same immutable source snapshot.
    # Concurrent developer edits are not a change in distribution semantics.
    snapshot = build_plugin.package_files()
    monkeypatch.setattr(build_plugin, 'package_files', lambda: dict(snapshot))
    archive = tmp_path / 'manifest candidate.zip'
    packed = build_plugin.build(archive)
    staged = build_plugin.stage_marketplace(tmp_path / 'source marketplace')
    manifest = (Path(staged['path']) / 'plugins/devsecops/SHA256SUMS').read_bytes()
    with zipfile.ZipFile(archive) as bundle:
        assert manifest == bundle.read('devsecops/SHA256SUMS')
        lines = manifest.decode().splitlines()
        assert len(lines) == packed['files'] - 1 == staged['files'] - 1
        for line in lines:
            expected, relative = line.split('  ', 1)
            assert relative != 'SHA256SUMS'
            assert hashlib.sha256(bundle.read('devsecops/' + relative)).hexdigest() == expected
            assert hashlib.sha256((Path(staged['path']) / 'plugins/devsecops' / relative).read_bytes()).hexdigest() == expected
    assert hashlib.sha256(manifest).hexdigest() == packed['package_sha256'] == staged['package_sha256']


def test_extracted_lifecycle_uses_installed_manifest_and_explicit_resume_inputs(tmp_path):
    from tests.test_factory_cli import arguments

    args = arguments(tmp_path)
    manifest_at = args.index('--package-manifest')
    del args[manifest_at:manifest_at + 2]
    archive = tmp_path / 'lifecycle candidate.zip'
    build_plugin.build(archive)
    extracted = tmp_path / 'installed candidate with spaces'
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(extracted)
    plugin = extracted / 'devsecops'
    env = {key: value for key, value in os.environ.items() if key not in {'PYTHONPATH', 'PYTHONHOME'}}
    run = tmp_path / 'lifecycle run with spaces'
    reports = []
    for location in ('--output', '--resume'):
        result = subprocess.run([sys.executable, str(plugin / 'scripts/factory_demo.py'), *args, location, str(run)],
                                cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20)
        assert result.returncode == 2 and not result.stderr
        report = json.loads(result.stdout)
        assert report['gaps'] == ['operation-driver-unavailable']
        assert not report['canonical_success'] and not report['terminal']
        assert report['identities']['package_sha256'] == hashlib.sha256((plugin / 'SHA256SUMS').read_bytes()).hexdigest()
        reports.append(report)
    assert reports[0]['run_id'] == reports[1]['run_id']
    assert reports[0]['identities'] == reports[1]['identities']
