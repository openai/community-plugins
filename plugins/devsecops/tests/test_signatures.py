"""Real generated lab signatures; none authenticate an Iron Bank publisher."""
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import stat
import sys

import pytest

from devsecops.adapters import signatures as adapter
from devsecops.process import ToolError, run

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "registry.example.test/mission"
DIGEST = "sha256:" + "a" * 64
TRUST_GAPS = {"signer-authority-unverified", "key-lifecycle-unverified", "transparency-log-unverified",
              "official-image-origin-unverified", "required-attestations-unverified"}


def codes(report):
    return {gap["code"] for gap in report["gaps"]}


@pytest.fixture(scope="module")
def lab(tmp_path_factory):
    binary = Path(os.environ.get("DEVSECOPS_COSIGN", Path.home()/".cache/devsecops/cosign-v3.1.4/bin/cosign"))
    if not binary.is_file():
        pytest.skip("Real pinned Cosign unavailable; provision scripts/bootstrap_cosign.py explicitly")
    lock = json.loads((ROOT / "cosign.lock.json").read_text())
    arch = {"aarch64": "arm64", "x86_64": "amd64"}.get(platform.machine(), platform.machine())
    expected = lock["tools"][platform.system().lower()+"-"+arch]["sha256"]
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == expected, "Test tool must match official release lock"
    path = tmp_path_factory.mktemp("generated-lab-signatures").resolve()
    value = {"critical": {"identity": {"docker-reference": IMAGE},
             "image": {"docker-manifest-digest": DIGEST}, "type": "cosign container image signature"},
             "optional": {"private-note": "PAYLOAD-SENTINEL-NOT-FOR-EXPORT"}}
    (path / "payload.json").write_text(json.dumps(value))
    env = {"PATH": "/usr/bin:/bin", "HOME": str(path), "XDG_CACHE_HOME": str(path), "COSIGN_PASSWORD": ""}
    for args in (["generate-key-pair", "--output-key-prefix", "lab"],
                 ["generate-key-pair", "--output-key-prefix", "wrong"],
                 ["sign-blob", "--key", "lab.key", "--use-signing-config=false", "--tlog-upload=false",
                  "--bundle", "bundle.json", "payload.json"]):
        code, _, _, _ = run([str(binary), *args], cwd=path, env=env, timeout=15, limit=64_000)
        assert code == 0, "Generating a local disposable signature failed"
    bundle = json.loads((path / "bundle.json").read_text())
    (path / "signature.txt").write_text(bundle["messageSignature"]["signature"])
    return path, binary


@pytest.fixture
def inputs(lab, tmp_path):
    source, binary = lab
    target = tmp_path / "inputs with spaces"
    target.mkdir()
    for name in ("payload.json", "signature.txt", "bundle.json", "lab.pub", "wrong.pub"):
        shutil.copyfile(source / name, target / name)
    return target, binary


def verify(inputs, mode="signature", **kwargs):
    path, binary = inputs
    arguments = {"expected_repository": IMAGE, "expected_digest": DIGEST, "public_key": path/"lab.pub",
                 "payload": path/"payload.json", "cosign": binary,
                 mode: path/("signature.txt" if mode == "signature" else "bundle.json")}
    arguments.update(kwargs)
    return adapter.verify_image_signature(**arguments)


@pytest.mark.parametrize("mode", ["signature", "bundle"])
def test_real_signature_and_exact_subject_are_narrow_observations(inputs, mode):
    result = verify(inputs, mode)
    obs = result["observations"]
    assert obs["crypto"]["decision"] == obs["subject_binding"]["decision"] == obs["verification"]["decision"] == "pass"
    assert result["decision"] == "unknown" and result["execution_status"] == "partial" and not result["confirmed_failure"]
    assert codes(result) == TRUST_GAPS
    assert all(gap["required"] for gap in result["gaps"])
    assert [receipt["operation"] for receipt in obs["receipts"]] == ["version", "verify-blob"]
    assert all(receipt["exit_code"] == 0 for receipt in obs["receipts"])
    assert obs["tool"]["version"] == "3.1.4"
    assert obs["inputs"]["payload.json"]["sha256"] == hashlib.sha256((inputs[0]/"payload.json").read_bytes()).hexdigest()
    exported = json.dumps(result)
    assert "PAYLOAD-SENTINEL-NOT-FOR-EXPORT" not in exported
    assert "BEGIN PUBLIC KEY" not in exported and str(inputs[0]) not in exported and "Verified OK" not in exported


@pytest.mark.parametrize("mode", ["signature", "bundle"])
@pytest.mark.parametrize("tamper", ["payload", "signature", "key"])
def test_real_crypto_rejects_tampered_bytes_and_wrong_key(inputs, mode, tamper):
    path, _ = inputs
    kwargs = {}
    if tamper == "payload":
        value = json.loads((path/"payload.json").read_text())
        value["optional"]["private-note"] = "TAMPERED-SENTINEL-NOT-FOR-EXPORT"
        (path/"payload.json").write_text(json.dumps(value))
    elif tamper == "key":
        kwargs["public_key"] = path/"wrong.pub"
    else:
        value = json.loads((path/"bundle.json").read_text())
        raw = bytearray(base64.b64decode(value["messageSignature"]["signature"]))
        raw[-1] ^= 1
        encoded = base64.b64encode(raw).decode()
        value["messageSignature"]["signature"] = encoded
        (path/"bundle.json").write_text(json.dumps(value))
        (path/"signature.txt").write_text(encoded)
    result = verify(inputs, mode, **kwargs)
    assert result["observations"]["crypto"]["decision"] == "fail"
    assert result["observations"]["subject_binding"]["decision"] == "unknown"
    assert result["observations"]["receipts"][-1]["exit_code"] != 0
    assert "signature-verification-failed" in codes(result)
    assert result["decision"] == "unknown" and not result["confirmed_failure"]
    assert "TAMPERED-SENTINEL-NOT-FOR-EXPORT" not in json.dumps(result)


@pytest.mark.parametrize("mode", ["signature", "bundle"])
@pytest.mark.parametrize("subject", [{"expected_repository": "registry.example.test/other"},
                                    {"expected_repository": "mirror.example.test/mission"},
                                    {"expected_digest": "sha256:"+"b"*64}])
def test_real_valid_signature_cannot_bind_different_repository_or_digest(inputs, mode, subject):
    result = verify(inputs, mode, **subject)
    assert result["observations"]["crypto"]["decision"] == "pass"
    assert result["observations"]["subject_binding"]["decision"] == "fail"
    assert result["observations"]["verification"]["decision"] == "fail"
    assert "signed-subject-mismatch" in codes(result)


@pytest.mark.parametrize("change", [
    lambda b: b.update(mediaType="application/vnd.dev.sigstore.bundle.v0.2+json"),
    lambda b: b.update(dsseEnvelope={"payload": "private"}),
    lambda b: b["verificationMaterial"].update(x509CertificateChain={"certificates": []}),
    lambda b: b["verificationMaterial"].update(tlogEntries=[{"logId": "unqualified"}]),
    lambda b: b["verificationMaterial"]["publicKey"].update(hint="https://untrusted.example/key"),
    lambda b: b["messageSignature"]["messageDigest"].update(algorithm="SHA2_512"),
    lambda b: b.update(signature="unrecognized-field"),
])
def test_unsupported_bundle_material_never_runs_tool_or_claims_failure(inputs, monkeypatch, change):
    path, _ = inputs
    bundle = json.loads((path/"bundle.json").read_text())
    change(bundle)
    (path/"bundle.json").write_text(json.dumps(bundle))
    monkeypatch.setattr(adapter, "run", lambda *_a, **_k: pytest.fail("Unsupported shape must not execute"))
    result = verify(inputs, "bundle")
    assert result["observations"]["verification"]["decision"] == "unknown"
    assert not result["observations"]["receipts"] and codes(result) - TRUST_GAPS


@pytest.mark.parametrize("raw", [
    b'{"critical":{},"critical":{}}', b'{"critical":NaN}', b'{"critical":1e999}',
    b'{"payloadType":"application/vnd.in-toto+json","payload":"PRIVATE","signatures":[]}',
    b'[]', b'{' + b'"nested":{'*70 + b'"private":0' + b'}'*71,
])
def test_unsupported_or_ambiguous_payload_is_unknown(inputs, monkeypatch, raw):
    (inputs[0]/"payload.json").write_bytes(raw)
    monkeypatch.setattr(adapter, "run", lambda *_a, **_k: pytest.fail("Invalid payload must not execute"))
    result = verify(inputs)
    assert result["observations"]["crypto"]["decision"] == "unknown"
    assert not result["observations"]["receipts"] and codes(result) - TRUST_GAPS
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("kind", ["leaf-symlink", "parent-symlink", "fifo", "directory", "huge", "relative", "url"])
def test_local_input_bounds_and_paths_fail_closed(inputs, tmp_path, monkeypatch, kind):
    path, _ = inputs
    target = tmp_path/"input"
    if kind == "leaf-symlink":
        target.symlink_to(path/"payload.json")
    elif kind == "parent-symlink":
        target.symlink_to(path, target_is_directory=True)
        target = target/"payload.json"
    elif kind == "fifo":
        os.mkfifo(target)
    elif kind == "directory":
        target.mkdir()
    elif kind == "huge":
        with target.open("wb") as stream:
            stream.truncate(adapter.MAX_PAYLOAD+1)
    elif kind == "relative":
        target = Path("payload.json")
    else:
        target = "https://untrusted.example/payload"
    monkeypatch.setattr(adapter, "run", lambda *_a, **_k: pytest.fail("Invalid input must not execute"))
    result = verify(inputs, payload=target)
    assert result["observations"]["verification"]["decision"] == "unknown"
    assert not result["observations"]["receipts"]


def test_unpinned_executable_is_never_run(inputs, tmp_path, monkeypatch):
    tool = tmp_path/"cosign"
    tool.write_text("#!/bin/sh\necho FAKE-SUCCESS\n")
    tool.chmod(0o700)
    monkeypatch.setattr(adapter, "run", lambda *_a, **_k: pytest.fail("Unpinned binary must not execute"))
    result = verify(inputs, cosign=tool)
    assert "cosign-binary-mismatch" in codes(result)


@pytest.mark.parametrize("failure", [ToolError("PRIVATE time limit"), ToolError("PRIVATE output limit")])
def test_tool_failures_and_output_are_sanitized(inputs, monkeypatch, failure):
    def broken(*_args, **_kwargs):
        raise failure
    monkeypatch.setattr(adapter, "run", broken)
    result = verify(inputs)
    assert "signature-tool-budget-or-execution-failed" in codes(result)
    assert "PRIVATE" not in json.dumps(result) and result["observations"]["verification"]["decision"] == "unknown"


def test_real_verification_uses_frozen_copies_and_scrubbed_env(inputs, monkeypatch):
    path, _ = inputs
    for name in ("AWS_SECRET_ACCESS_KEY", "COSIGN_PASSWORD", "SIGSTORE_ROOT_FILE", "SIGSTORE_REKOR_PUBLIC_KEY",
                 "HTTP_PROXY", "HTTPS_PROXY", "DOCKER_CONFIG", "GOOGLE_APPLICATION_CREDENTIALS"):
        monkeypatch.setenv(name, "PRIVATE-ENV-SENTINEL")
    seen = []
    original_run = adapter.run
    def inspect(argv, *, cwd, **kwargs):
        env = kwargs["env"]
        assert "PRIVATE-ENV-SENTINEL" not in json.dumps(env)
        assert Path(env["PATH"]).is_dir() and not list(Path(env["PATH"]).iterdir())
        assert stat.S_IMODE(cwd.stat().st_mode) == 0o700
        for name in ("public-key.pem", "signature.txt", "payload.json", "lock.json"):
            assert stat.S_IMODE((cwd/name).stat().st_mode) == 0o400
        if argv[1] == "version":
            (path/"payload.json").write_text("changed original PRIVATE")
        seen.append(cwd)
        return original_run(argv, cwd=cwd, **kwargs)
    monkeypatch.setattr(adapter, "run", inspect)
    monkeypatch.setattr(socket, "create_connection", lambda *_a, **_k: pytest.fail("Python must never contact a registry"))
    result = verify(inputs)
    assert result["observations"]["verification"]["decision"] == "pass"
    assert len(seen) == 2 and seen[0] == seen[1] and not seen[0].exists()
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("repository", ["https://registry.example/mission", "user:password@registry.example/mission", "registry.example/mission:tag",
                                       "registry.example/mission@sha256:aaa", "bad..example/mission", "registry.example:/mission"])
def test_expected_repository_cannot_include_credentials_tags_or_url(repository):
    with pytest.raises(ValueError, match="exact image subject"):
        adapter.verify_image_signature(expected_repository=repository, expected_digest=DIGEST, public_key=Path("key"),
                                       payload=Path("payload"), cosign=Path("cosign"), signature=Path("sig"))


@pytest.mark.skipif(os.environ.get("DEVSECOPS_TEST_NETWORK_SANDBOX") != "1", reason="Explicit host macOS network-denial qualification")
@pytest.mark.parametrize("mode", ["signature", "bundle"])
def test_real_verification_with_os_network_denied(inputs, monkeypatch, mode):
    assert platform.system() == "Darwin"
    original_run = adapter.run
    def isolated(argv, **kwargs):
        return original_run(["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)(deny network*)", *argv], **kwargs)
    monkeypatch.setattr(adapter, "run", isolated)
    result = verify(inputs, mode)
    assert result["observations"]["verification"]["decision"] == "pass"
    assert all(r["tool"] == "sandbox-exec" and r["exit_code"] == 0 for r in result["observations"]["receipts"])


@pytest.mark.skipif(os.environ.get("DEVSECOPS_TEST_NETWORK_SANDBOX") != "1", reason="Explicit host macOS network-denial qualification")
def test_os_network_profile_denies_a_reachable_loopback_socket(tmp_path):
    assert platform.system() == "Darwin"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(2)
        port = listener.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass
        code = ("import socket\ntry:\n socket.create_connection(('127.0.0.1', " + str(port) + "),timeout=1)\n"
                "except PermissionError:\n raise SystemExit(0)\nraise SystemExit(3)\n")
        status, _, _, _ = run(["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)(deny network*)",
                              sys.executable, "-c", code], cwd=tmp_path, timeout=3)
    assert status == 0
