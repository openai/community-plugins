"""Real certificate issuance and strict TLS handshakes for the disposable stack."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import ssl
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def openssl(argv, *, check=True):
    result = subprocess.run([str(x) for x in argv], capture_output=True, timeout=30, check=False)
    if check:
        assert result.returncode == 0, result.stderr.decode(errors="replace")
    return result


@pytest.fixture(scope="module")
def issued(tmp_path_factory):
    selected = os.environ.get("DEVSECOPS_TEST_OPENSSL") or shutil.which("openssl")
    if not selected:
        pytest.fail("Real portable TLS regression requires OpenSSL on PATH or DEVSECOPS_TEST_OPENSSL")
    executable = Path(selected).resolve(strict=True)
    version = openssl([executable, "version"]).stdout.decode().strip()
    for command, flag in (("req", "-addext"), ("verify", "-x509_strict")):
        help_result = openssl([executable, command, "-help"], check=False)
        if flag.encode() not in help_result.stdout + help_result.stderr:
            pytest.fail(
                f"Selected {version} lacks {flag}; select a compatible OpenSSL with DEVSECOPS_TEST_OPENSSL. "
                "Unsupported system LibreSSL is not silently treated as strict TLS coverage."
            )
    spec = importlib.util.spec_from_file_location(
        "portable_stack_tls", ROOT / "demo/portable-gitops/stack.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original = module.run

    def real_command(argv, **kwargs):
        assert argv[0] == "openssl", "Certificate issuance must not start a runtime or make network requests"
        return original([executable, *argv[1:]], **kwargs)

    module.run = real_command
    directory = tmp_path_factory.mktemp("portable-real-tls")
    directory.chmod(0o700)
    module.create_tls(directory, "10.99.1.4")
    return directory, executable


def handshake(directory, hostname, *, ca="ca.crt"):
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.minimum_version = ssl.TLSVersion.TLSv1_2
    server.load_cert_chain(directory / "server.crt", directory / "server.key")
    client = ssl.create_default_context(cafile=str(directory / ca)) if ca else ssl.create_default_context()
    client.minimum_version = ssl.TLSVersion.TLSv1_2
    client.verify_flags |= ssl.VERIFY_X509_STRICT
    assert client.check_hostname and client.verify_mode == ssl.CERT_REQUIRED
    incoming_client, outgoing_client, incoming_server, outgoing_server = (ssl.MemoryBIO() for _ in range(4))
    peer_client = client.wrap_bio(
        incoming_client, outgoing_client, server_side=False, server_hostname=hostname
    )
    peer_server = server.wrap_bio(incoming_server, outgoing_server, server_side=True)
    complete = [False, False]
    for _ in range(30):
        for index, peer in enumerate((peer_client, peer_server)):
            if not complete[index]:
                try:
                    peer.do_handshake()
                    complete[index] = True
                except (ssl.SSLWantReadError, ssl.SSLWantWriteError):
                    pass
        for outgoing, incoming in ((outgoing_client, incoming_server), (outgoing_server, incoming_client)):
            while outgoing.pending:
                incoming.write(outgoing.read())
        if all(complete):
            peer_client.write(b"actual verified TLS payload")
            incoming_server.write(outgoing_client.read())
            assert peer_server.read() == b"actual verified TLS payload"
            return peer_client.getpeercert()
    pytest.fail("The real in-memory TLS handshake did not finish within its fixed step limit")


def test_issued_chain_passes_real_openssl_strict_verification(issued):
    directory, executable = issued
    result = openssl(
        [
            executable,
            "verify",
            "-x509_strict",
            "-purpose",
            "sslserver",
            "-verify_hostname",
            "localhost",
            "-CAfile",
            directory / "ca.crt",
            directory / "server.crt",
        ]
    )
    assert b": OK" in result.stdout
    # The negative case may run first under an alternate pytest ordering.
    assert all(
        path.stat().st_mode & 0o777 == 0o600
        for path in directory.iterdir()
        if not path.name.startswith("legacy-")
    )


@pytest.mark.parametrize("hostname", ["localhost", "127.0.0.1", "10.99.1.4"])
def test_actual_strict_tls_accepts_only_issued_private_identities(issued, hostname):
    certificate = handshake(issued[0], hostname)
    assert {("DNS", "localhost"), ("IP Address", "127.0.0.1"), ("IP Address", "10.99.1.4")} <= set(
        certificate["subjectAltName"]
    )


def test_actual_strict_tls_rejects_another_hostname(issued):
    with pytest.raises(ssl.SSLCertVerificationError, match="(?i)(mismatch|not valid for)"):
        handshake(issued[0], "other.example.invalid")


def test_actual_strict_tls_does_not_accept_an_untrusted_ca(issued):
    with pytest.raises(ssl.SSLCertVerificationError):
        handshake(issued[0], "localhost", ca=None)


def test_ca_without_key_usage_reproduces_the_real_strict_failure(issued):
    directory, executable = issued
    # Reuse the actual issued CA key/subject so chain identity is valid; omit
    # only the CA key usage extension responsible for the observed failure.
    legacy = directory / "legacy-ca.crt"
    extensions = directory / "legacy-ca.ext"
    extensions.write_text(
        "basicConstraints=critical,CA:TRUE\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid:always\n"
    )
    request = directory / "legacy-ca.csr"
    openssl(
        [
            executable,
            "req",
            "-new",
            "-key",
            directory / "ca.key",
            "-subj",
            "/CN=Disposable DevSecOps Demo CA",
            "-out",
            request,
        ]
    )
    openssl(
        [
            executable,
            "x509",
            "-req",
            "-in",
            request,
            "-signkey",
            directory / "ca.key",
            "-days",
            "2",
            "-extfile",
            extensions,
            "-out",
            legacy,
        ]
    )
    result = openssl(
        [executable, "verify", "-x509_strict", "-CAfile", legacy, directory / "server.crt"], check=False
    )
    assert result.returncode != 0 and b"key usage" in (result.stdout + result.stderr).lower()
    with pytest.raises(ssl.SSLCertVerificationError, match="(?i)key usage"):
        handshake(directory, "localhost", ca=legacy.name)
