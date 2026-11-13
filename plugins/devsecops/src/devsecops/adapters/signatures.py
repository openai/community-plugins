"""Offline key-backed Cosign verification of an exact Simple Signing subject.

Inputs and the pinned verifier are copied into private scratch before execution.
No registry, key URL, KMS, certificate discovery, transparency service, or
credential helper is used. Key authority and image origin remain separate gaps.
"""
from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
import hashlib
import ipaddress
import math
import os
from pathlib import Path
import platform
import re
import stat
import tempfile
import time

from devsecops.process import ToolError, run
from .http import APIError, _json
from .oci import _digest, _repository

ROOT = Path(__file__).resolve().parents[3]
VERSION = "3.1.4"
MAX_PAYLOAD = 1_000_000
MAX_BINARY = 160_000_000
BUNDLE_MEDIA_TYPE = "application/vnd.dev.sigstore.bundle.v0.3+json"
P256_SPKI_PREFIX = bytes.fromhex("3059301306072a8648ce3d020106082a8648ce3d03010703420004")


class _Invalid(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__("Signature verification inputs are unavailable, changed or unsupported")


def _repository_ref(value):
    if not isinstance(value, str) or len(value) > 512 or "/" not in value:
        return False
    authority, repository = value.split("/", 1)
    if not _repository(repository) or authority.count(":") > 1:
        return False
    host, separator, port = authority.partition(":")
    if separator and (not re.fullmatch(r"[1-9][0-9]{0,4}", port) or int(port) > 65535):
        return False
    if not 1 <= len(host) <= 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                                             for label in host.split(".")):
        return False
    if re.fullmatch(r"[0-9.]+", host):
        try:
            return str(ipaddress.IPv4Address(host)) == host
        except ValueError:
            return False
    return True


def _snapshot(source, destination, limit, *, deadline=None):
    """Bounded regular files, descriptor-walked ancestors, stable read identity."""
    if not isinstance(source, Path) or not source.is_absolute() or ".." in source.parts:
        raise _Invalid("input-path-unsupported")
    directory = os.open(source.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in source.parts[1:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
            raise _Invalid("input-size-or-type-unsupported")
        copied, checksum = 0, hashlib.sha256()
        out = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(out, "wb") as output:
            while raw := stream.read(min(1_000_000, limit + 1 - copied)):
                if deadline is not None and time.monotonic() > deadline:
                    raise _Invalid("input-deadline-exceeded")
                copied += len(raw)
                if copied > limit:
                    raise _Invalid("input-size-or-type-unsupported")
                output.write(raw)
                checksum.update(raw)
            output.flush()
            os.fsync(output.fileno())
        after = os.fstat(stream.fileno())
        if deadline is not None and time.monotonic() > deadline:
            raise _Invalid("input-deadline-exceeded")
        keys = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, key) != getattr(after, key) for key in keys) or copied != before.st_size:
            raise _Invalid("input-changed-during-copy")
    os.chmod(destination, 0o400)
    return {"sha256": checksum.hexdigest(), "bytes": copied}


def _base64(value, *, minimum=1, maximum=16_384):
    if not isinstance(value, str) or not 1 <= len(value) <= maximum * 2:
        raise _Invalid("signature-encoding-unsupported")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise _Invalid("signature-encoding-unsupported") from None
    if not minimum <= len(raw) <= maximum or base64.b64encode(raw).decode() != value:
        raise _Invalid("signature-encoding-unsupported")
    return raw


def _public_key(raw):
    try:
        lines = raw.decode("ascii").strip().splitlines()
    except UnicodeError:
        raise _Invalid("public-key-format-unsupported") from None
    if len(lines) < 3 or lines[0] != "-----BEGIN PUBLIC KEY-----" or lines[-1] != "-----END PUBLIC KEY-----":
        raise _Invalid("public-key-format-unsupported")
    der = _base64("".join(lines[1:-1]), maximum=4096)
    if len(der) != 91 or not der.startswith(P256_SPKI_PREFIX):
        raise _Invalid("public-key-algorithm-unsupported")
    # Encoding/curve admission only. Actual signature verification is Cosign's.
    x, y = int.from_bytes(der[-64:-32]), int.from_bytes(der[-32:])
    prime = 2**256 - 2**224 + 2**192 + 2**96 - 1
    b = int("5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b", 16)
    if x >= prime or y >= prime or (y*y - x*x*x + 3*x - b) % prime:
        raise _Invalid("public-key-format-unsupported")


def _payload(raw):
    value = _json(raw)
    critical = value.get("critical")
    if (set(value) - {"critical", "optional"} or not isinstance(critical, dict)
            or set(critical) != {"identity", "image", "type"}
            or critical["type"] != "cosign container image signature"
            or not isinstance(critical["identity"], dict) or set(critical["identity"]) != {"docker-reference"}
            or not isinstance(critical["image"], dict) or set(critical["image"]) != {"docker-manifest-digest"}
            or value.get("optional") is not None and not isinstance(value["optional"], dict)):
        raise _Invalid("payload-shape-unsupported")
    repository = critical["identity"]["docker-reference"]
    digest = critical["image"]["docker-manifest-digest"]
    if not _repository_ref(repository) or not _digest(digest):
        raise _Invalid("signed-subject-unsupported")
    return {"repository": repository, "digest": digest}


def _bundle(raw):
    value = _json(raw)
    if set(value) != {"mediaType", "verificationMaterial", "messageSignature"} or value["mediaType"] != BUNDLE_MEDIA_TYPE:
        raise _Invalid("bundle-format-unsupported")
    material, message = value["verificationMaterial"], value["messageSignature"]
    if (not isinstance(material, dict) or set(material) != {"publicKey"}
            or not isinstance(material["publicKey"], dict) or set(material["publicKey"]) != {"hint"}
            or not isinstance(message, dict) or set(message) != {"messageDigest", "signature"}
            or not isinstance(message["messageDigest"], dict)
            or set(message["messageDigest"]) != {"algorithm", "digest"}
            or message["messageDigest"]["algorithm"] != "SHA2_256"):
        raise _Invalid("bundle-material-unsupported")
    _base64(material["publicKey"]["hint"], minimum=32, maximum=32)
    _base64(message["messageDigest"]["digest"], minimum=32, maximum=32)
    _base64(message["signature"], minimum=64, maximum=128)
    return message["messageDigest"]["digest"]


def _gap(report, code):
    report["gaps"].append({"code": code, "required": True,
                          "message": "The supplied signature evidence does not establish this required trust or verification condition"})


def verify_image_signature(*, expected_repository, expected_digest, public_key, payload, cosign,
                           signature=None, bundle=None, timeout=15.0, now=None):
    """Verify local P-256 Simple Signing bytes using the exact pinned Cosign.

    Exactly one detached base64 signature or key-backed Sigstore v0.3 bundle is
    required. This checks cryptography and subject equality, not key authority,
    image acquisition, tlog/certificate lifecycle, attestations or recovery.
    """
    if (not _repository_ref(expected_repository) or not _digest(expected_digest)
            or type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 60
            or (signature is None) == (bundle is None)):
        raise ValueError("An exact image subject and one bounded local signature mode are required")
    clock = now or datetime.now(timezone.utc)
    if not isinstance(clock, datetime) or clock.tzinfo is None:
        raise ValueError("An aware observation time is required")
    expected = {"repository": expected_repository, "digest": expected_digest}
    report = {"schema_version": 1, "provider": "signatures", "execution_status": "partial", "decision": "unknown",
        "confirmed_failure": False, "scope": expected, "observed_at": clock.isoformat(),
        "observations": {"format": "cosign-simple-signing-v1", "signature_format": "detached" if signature else "sigstore-bundle-v0.3",
            "crypto": {"decision": "unknown", "algorithm": "ecdsa-sha2-256-nistp256"},
            "subject_binding": {"decision": "unknown", "expected": expected},
            "verification": {"decision": "unknown"}, "inputs": {}, "receipts": [],
            "trust": {"key_authority": "not-verified", "key_lifecycle": "not-verified", "transparency_log": "not-verified",
                      "official_image_origin": "not-verified", "required_attestations": "not-verified"},
            "network_mode": "offline-key-only", "collection": {"started_at": clock.isoformat()}}, "gaps": []}
    for code in ("signer-authority-unverified", "key-lifecycle-unverified", "transparency-log-unverified",
                 "official-image-origin-unverified", "required-attestations-unverified"):
        _gap(report, code)
    obs = report["observations"]
    deadline = time.monotonic() + timeout
    try:
        with tempfile.TemporaryDirectory(prefix="devsecops-signature-") as directory:
            scratch = Path(directory).resolve()
            for name in ("home", "empty-path", "cache", "docker", "tmp"):
                (scratch / name).mkdir(mode=0o700)
            env = {"PATH": str(scratch / "empty-path"), "HOME": str(scratch / "home"),
                   "XDG_CACHE_HOME": str(scratch / "cache"), "DOCKER_CONFIG": str(scratch / "docker"),
                   "TMPDIR": str(scratch / "tmp")}
            lock_receipt = _snapshot(ROOT / "cosign.lock.json", scratch / "lock.json", 64_000)
            lock = _json((scratch / "lock.json").read_bytes())
            arch = {"aarch64": "arm64", "x86_64": "amd64"}.get(platform.machine(), platform.machine())
            target = platform.system().lower() + "-" + arch
            if lock.get("schema_version") != 1 or lock.get("version") != VERSION or target not in lock.get("tools", {}):
                raise _Invalid("cosign-lock-unsupported")
            binary_receipt = _snapshot(cosign, scratch / "cosign", MAX_BINARY)
            pinned = lock["tools"][target]
            if binary_receipt != {"sha256": pinned["sha256"], "bytes": pinned["bytes"]}:
                raise _Invalid("cosign-binary-mismatch")
            os.chmod(scratch / "cosign", 0o500)
            obs["tool"] = {"name": "cosign", "version": VERSION, "platform": target,
                           "binary_sha256": binary_receipt["sha256"], "lock_sha256": lock_receipt["sha256"]}
            for name, source, limit in (("public-key.pem", public_key, 16_384), ("payload.json", payload, MAX_PAYLOAD),
                                      ("signature.txt" if signature else "bundle.json", signature or bundle, 16_384 if signature else MAX_PAYLOAD)):
                obs["inputs"][name] = _snapshot(source, scratch / name, limit)
            _public_key((scratch / "public-key.pem").read_bytes())
            subject = _payload((scratch / "payload.json").read_bytes())
            if signature:
                try:
                    _base64((scratch / "signature.txt").read_text(encoding="ascii").strip(), minimum=64, maximum=128)
                except UnicodeError:
                    raise _Invalid("signature-encoding-unsupported") from None
            else:
                encoded_digest = _bundle((scratch / "bundle.json").read_bytes())
                obs["bundle_payload_digest_matches"] = (base64.b64decode(encoded_digest).hex()
                                                        == obs["inputs"]["payload.json"]["sha256"])
            def command(args, label):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _Invalid("verification-time-budget-exhausted")
                code, out, err, receipt = run([str(scratch / "cosign"), *args], cwd=scratch,
                                               timeout=remaining, limit=64_000, env=env)
                obs["receipts"].append({"operation": label, **receipt})
                return code, out, err
            code, out, _ = command(["version", "--json"], "version")
            if code != 0 or _json(out).get("gitVersion") != "v" + VERSION:
                raise _Invalid("cosign-version-mismatch")
            args = ["verify-blob", "--key", "public-key.pem", "--offline", "--insecure-ignore-tlog",
                    "--signature" if signature else "--bundle", "signature.txt" if signature else "bundle.json", "payload.json"]
            code, _, err = command(args, "verify-blob")
            if code == 0:
                obs["crypto"]["decision"] = "pass"
                matched = subject == expected
                obs["subject_binding"].update(decision="pass" if matched else "fail", observed=subject)
                obs["verification"]["decision"] = "pass" if matched else "fail"
                if not matched:
                    _gap(report, "signed-subject-mismatch")
            elif b"invalid signature when validating ASN.1 encoded signature" in err or obs.get("bundle_payload_digest_matches") is False:
                obs["crypto"]["decision"] = "fail"
                obs["verification"]["decision"] = "fail"
                _gap(report, "signature-verification-failed")
            else:
                _gap(report, "signature-tool-verification-unavailable")
    except _Invalid as exc:
        _gap(report, exc.code)
    except ToolError:
        _gap(report, "signature-tool-budget-or-execution-failed")
    except APIError:
        _gap(report, "signature-json-invalid")
    except (OSError, UnicodeError, KeyError, TypeError, ValueError):
        _gap(report, "signature-input-unavailable-or-invalid")
    report["observed_at"] = (now or datetime.now(timezone.utc)).isoformat()
    obs["collection"]["finished_at"] = report["observed_at"]
    return report
