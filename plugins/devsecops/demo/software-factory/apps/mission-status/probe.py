"""One bounded, explicitly scoped HTTP observation. No policy/recovery verdict.

Only Python tests can inject a resolver, connector or environment. Such receipts
are marked controlled-test and cannot be mistaken for live qualification.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
import queue
import re
import socket
import stat
import threading
import time

ENCODING = "devsecops-http-probe-json-sha256-v1"
MAX_REQUEST_BYTES = 65536
MAX_HEADER_BYTES = 16384
NAME = {"type": "string", "pattern": "^[a-z][a-z0-9-]{0,62}$"}
DNS_LABEL = {"type": "string", "pattern": "^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"}
DNS_NAME = {"type": "string", "maxLength": 253, "pattern": "^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$"}
UID = {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"}
SHA = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
NULL_SHA = {"anyOf": [SHA, {"type": "null"}]}
SOURCE_ENV = {key: "DEVSECOPS_PROBE_POD_" + key.upper() for key in ("namespace", "name", "uid")}
IDENTITY_KEYS = ("scenario_sha256", "assertions_sha256", "package_sha256", "lock_sha256", "config_sha256", "environment_sha256")
PATHS = ["/", "/health", "/healthz", "/metrics"]


def _object(properties, required=None):
    return {"type": "object", "properties": properties, "required": list(properties) if required is None else required,
            "additionalProperties": False}


def _array(items, minimum=0, maximum=32):
    return {"type": "array", "items": items, "minItems": minimum, "maxItems": maximum, "uniqueItems": True}


IDENTITIES = _object({key: SHA for key in IDENTITY_KEYS})
SOURCE_IDENTITY = _object({"namespace": DNS_LABEL, "name": DNS_NAME, "uid": UID})
DESTINATION = _object({"namespace": DNS_LABEL, "service": DNS_LABEL, "cluster_domain": DNS_NAME,
    "port": {"type": "integer", "minimum": 1, "maximum": 65535}, "path": {"enum": PATHS},
    "scheme": {"enum": ["http", "https"]}, "expected_addresses": _array({"type": "string", "maxLength": 45}, 1, 2)})
LIMITS = _object({**{key: {"type": "number", "minimum": 0.05, "maximum": 5} for key in
    ("dns_seconds", "connect_seconds", "header_seconds", "body_seconds")},
    "total_seconds": {"type": "number", "minimum": 0.05, "maximum": 15},
    "body_bytes": {"type": "integer", "minimum": 1, "maximum": 65536}})
REQUEST_SCHEMA = {"$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:devsecops:http-probe-request:1", **_object({
    "schema_version": {"const": 1}, "kind": {"const": "http-probe-request"},
    "run_id": UID, "attempt_id": UID, "case_id": NAME,
    "phase": {"enum": ["baseline", "fault", "recovery", "observation"]}, "identities": IDENTITIES,
    "source": _object({"expected": SOURCE_IDENTITY, "environment": _object({key: {"const": name} for key, name in SOURCE_ENV.items()})}),
    "destination": DESTINATION, "expectation": _object({"status": {"const": 200}, "body_sha256": NULL_SHA}), "limits": LIMITS})}
OUTCOMES = ["http-response", "unexpected-status", "body-mismatch", "redirect-rejected", "input-invalid",
    "unsupported-scheme", "source-identity-missing", "source-identity-mismatch", "dns-timeout", "dns-failed",
    "dns-address-mismatch", "connect-timeout", "connection-refused", "connect-failed", "peer-address-mismatch",
    "header-timeout", "body-timeout", "total-timeout", "headers-too-large", "response-too-large",
    "invalid-http-response", "unsupported-http-framing", "unsupported-content-encoding", "clock-regressed", "interrupted"]
RECEIPT_SCHEMA = {"$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:devsecops:http-probe-receipt:1", **_object({
    "schema_version": {"const": 1}, "kind": {"const": "http-probe-receipt"},
    "observation_kind": {"const": "direct-http-request"}, "execution_mode": {"enum": ["operator", "controlled-test"]},
    "execution_status": {"enum": ["complete", "partial"]}, "decision": {"const": "unknown"},
    "policy_verdict": {"const": "unknown"}, "recovery": {"const": "not-evaluated"},
    "request": {"anyOf": [REQUEST_SCHEMA, {"type": "null"}]},
    "request_artifact": _object({"sha256": NULL_SHA, "canonical_sha256": NULL_SHA,
        "encoding": {"enum": ["raw-json", ENCODING, "unavailable"]}}),
    "started_at": {"type": "string", "format": "date-time"}, "finished_at": {"type": "string", "format": "date-time"},
    "elapsed_seconds": {"type": "number", "minimum": 0},
    "source_identity": _object({"matched": {"type": "boolean"},
        "claims": {"anyOf": [SOURCE_IDENTITY, {"type": "null"}]}, "claims_source": {"const": "environment-references"},
        "authentication": {"const": "not-verified"}}),
    "network": _object({"host": {"type": ["string", "null"], "maxLength": 253},
        "dns_addresses": _array({"type": "string", "maxLength": 45}, maximum=2),
        "connected_peer": {"type": ["string", "null"], "maxLength": 45}}),
    "observation": _object({"outcome": {"enum": OUTCOMES}, "http_decision": {"enum": ["pass", "fail", "unknown"]},
        "status": {"type": ["integer", "null"], "minimum": 100, "maximum": 599},
        "body_bytes": {"type": ["integer", "null"], "minimum": 0, "maximum": 65536},
        "body_sha256": NULL_SHA, "body_sha256_matched": {"type": ["boolean", "null"]}}),
    "gaps": _array(_object({"code": NAME, "required": {"const": True}}), maximum=16), "receipt_sha256": SHA})}


class ProbeError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__("HTTP probe could not complete its declared observation")


def _canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n").encode()


def _sha(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _shape(value, schema):
    """The standalone image requires no schema-library dependency."""
    if "anyOf" in schema:
        return any(_shape(value, option) for option in schema["anyOf"])
    if "const" in schema and (value != schema["const"] or type(value) is not type(schema["const"])):
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    kind = schema.get("type")
    if isinstance(kind, list):
        return any(_shape(value, {**schema, "type": k}) for k in kind)
    if kind == "null":
        return value is None
    if kind == "object":
        if not isinstance(value, dict) or set(value) != set(schema["required"]):
            return False
        return all(_shape(v, schema["properties"][k]) for k, v in value.items())
    if kind == "array":
        return (isinstance(value, list) and schema.get("minItems", 0) <= len(value) <= schema["maxItems"]
                and all(_shape(v, schema["items"]) for v in value) and len({_sha(v) for v in value}) == len(value))
    if kind == "string":
        return (isinstance(value, str) and len(value) <= schema.get("maxLength", 4096)
                and ("pattern" not in schema or re.fullmatch(schema["pattern"], value) is not None))
    if kind in {"number", "integer"}:
        return (type(value) in ((int,) if kind == "integer" else (int, float)) and math.isfinite(value)
                and schema.get("minimum", -math.inf) <= value <= schema.get("maximum", math.inf))
    if kind == "boolean":
        return type(value) is bool
    return True


def _address(value):
    try:
        if not isinstance(value, str) or "%" in value:
            raise ValueError
        address = ipaddress.ip_address(value)
        if (address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified
                or address.is_reserved or getattr(address, "ipv4_mapped", None)
                or isinstance(address, ipaddress.IPv4Address) and address in ipaddress.ip_network("0.0.0.0/8")):
            raise ValueError
        return str(address)
    except ValueError:
        raise ProbeError("dns-address-mismatch") from None


def _dns_name(value):
    return isinstance(value, str) and len(value) <= 253 and all(re.fullmatch(DNS_LABEL["pattern"], part) for part in value.split("."))


def validate_request(value):
    try:
        if len(_canonical(value)) > MAX_REQUEST_BYTES or not _shape(value, REQUEST_SCHEMA):
            raise ValueError
        destination, source, limits = value["destination"], value["source"], value["limits"]
        addresses = destination["expected_addresses"]
        if (not _dns_name(destination["cluster_domain"]) or not _dns_name(source["expected"]["name"])
                or len(f"{destination['service']}.{destination['namespace']}.svc.{destination['cluster_domain']}") > 253
                or len(set(source["environment"].values())) != 3
                or any(_address(a) != a for a in addresses)
                or len({ipaddress.ip_address(a).version for a in addresses}) != len(addresses)
                or any(limits[k] > limits["total_seconds"] for k in ("dns_seconds", "connect_seconds", "header_seconds", "body_seconds"))):
            raise ValueError
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError, ProbeError):
        raise ProbeError("input-invalid") from None


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _load(value):
    artifact = {"sha256": None, "canonical_sha256": None, "encoding": "unavailable"}
    try:
        if isinstance(value, Path):
            descriptor = os.open(value, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_REQUEST_BYTES:
                    raise ValueError
                raw = stream.read(MAX_REQUEST_BYTES + 1)
                after = os.fstat(stream.fileno())
                if len(raw) > MAX_REQUEST_BYTES or any(getattr(before, k) != getattr(after, k) for k in ("st_size", "st_mtime_ns", "st_ctime_ns")):
                    raise ValueError
            artifact.update(sha256=hashlib.sha256(raw).hexdigest(), encoding="raw-json")
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        else:
            value = deepcopy(value)
            artifact.update(sha256=_sha(value), encoding=ENCODING)
        validate_request(value)
        artifact["canonical_sha256"] = _sha(value)
        return deepcopy(value), artifact
    except (OSError, ValueError, TypeError, RecursionError, OverflowError, ProbeError):
        return None, artifact


def _resolve(host, port):
    return sorted({item[4][0] for item in socket.getaddrinfo(host + ".", port, socket.AF_UNSPEC, socket.SOCK_STREAM, socket.IPPROTO_TCP)})


def _connect(address, port, timeout):
    family = socket.AF_INET6 if ipaddress.ip_address(address).version == 6 else socket.AF_INET
    connection = socket.socket(family, socket.SOCK_STREAM)
    try:
        connection.settimeout(timeout)
        connection.connect((address, port))
        return connection
    except BaseException:
        connection.close()
        raise


def _bounded_call(call, timeout, deadline, code, late_close=False):
    now = time.monotonic()
    expires = min(now + timeout, deadline)
    if expires <= now:
        raise ProbeError("total-timeout")
    replies, stopped, gate = queue.Queue(maxsize=1), threading.Event(), threading.Lock()
    def worker():
        try:
            if stopped.is_set() or time.monotonic() >= expires:
                return
            value = call(expires - time.monotonic())
            reply = (True, value)
        except Exception as exc:
            reply = (False, exc)
        with gate:
            if stopped.is_set():
                if reply[0] and late_close:
                    reply[1].close()
            else:
                replies.put_nowait(reply)
    threading.Thread(target=worker, daemon=True).start()
    def cancel():
        with gate:
            stopped.set()
            try:
                valid, late = replies.get_nowait()
                if valid and late_close:
                    late.close()
            except queue.Empty:
                pass
    try:
        valid, value = replies.get(timeout=max(0, expires - time.monotonic()))
    except queue.Empty:
        cancel()
        raise ProbeError("total-timeout" if expires == deadline else code) from None
    except BaseException:
        cancel()
        raise
    if time.monotonic() >= expires:
        stopped.set()
        if valid and late_close:
            value.close()
        raise ProbeError("total-timeout" if expires == deadline else code)
    if not valid:
        if isinstance(value, ConnectionRefusedError):
            raise ProbeError("connection-refused") from None
        if isinstance(value, TimeoutError):
            raise ProbeError("total-timeout" if expires == deadline else code) from None
        raise ProbeError("dns-failed" if code == "dns-timeout" else "connect-failed") from None
    return value


class _HeaderReader:
    def __init__(self, stream):
        self.stream, self.header_bytes, self.headers = stream, 0, True
        self.complete, self.expect_status = False, True

    def readline(self, limit=-1):
        if self.headers:
            bound = MAX_HEADER_BYTES - self.header_bytes + 1
            limit = min(limit, bound) if limit >= 0 else bound
        value = self.stream.readline(limit)
        if self.headers:
            self.header_bytes += len(value)
            if self.header_bytes > MAX_HEADER_BYTES:
                raise ProbeError("headers-too-large")
            # http.client's email parser accepts malformed header lines and EOF
            # as a terminator. Validate the wire block before that permissive
            # parser can discard a framing field or accept a truncated response.
            if not value.endswith(b"\r\n"):
                raise ProbeError("invalid-http-response")
            if self.expect_status:
                if not re.fullmatch(rb"HTTP/1\.[01] [0-9]{3}(?: [\t\x20-\x7e\x80-\xff]*)?\r\n", value):
                    raise ProbeError("invalid-http-response")
                self.complete, self.expect_status = False, False
            elif value == b"\r\n":
                self.complete, self.expect_status = True, True
            elif not re.fullmatch(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+:[\t\x20-\x7e\x80-\xff]*\r\n", value):
                raise ProbeError("invalid-http-response")
        return value

    def __getattr__(self, name):
        return getattr(self.stream, name)


class _Socket:
    def __init__(self, raw):
        self.raw, self.reader = raw, None

    def makefile(self, *args, **kwargs):
        self.reader = _HeaderReader(self.raw.makefile(*args, **kwargs))
        return self.reader

    def __getattr__(self, name):
        return getattr(self.raw, name)


def _socket_phase(sock, call, timeout, deadline, code):
    expires = min(time.monotonic() + timeout, deadline)
    remaining = expires - time.monotonic()
    if remaining <= 0:
        raise ProbeError("total-timeout")
    expired = threading.Event()
    def stop():
        expired.set()
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
    timer = threading.Timer(remaining, stop)
    timer.daemon = True
    timer.start()
    try:
        sock.settimeout(remaining)
        value = call()
        if expired.is_set() or time.monotonic() >= expires:
            raise ProbeError("total-timeout" if expires == deadline else code)
        return value
    except ProbeError:
        # The deadline can close a socket in the middle of a header line. Keep
        # that measured timeout distinct from an immediately malformed response.
        if expired.is_set() or time.monotonic() >= expires:
            raise ProbeError("total-timeout" if expires == deadline else code) from None
        raise
    except (OSError, http.client.HTTPException) as exc:
        if expired.is_set() or time.monotonic() >= expires or isinstance(exc, TimeoutError):
            raise ProbeError("total-timeout" if expires == deadline else code) from None
        raise ProbeError("invalid-http-response") from None
    finally:
        timer.cancel()


def _http(request, receipt, resolver, connector, environ, deadline):
    source, destination, limits = request["source"], request["destination"], request["limits"]
    claims = {key: environ.get(name) for key, name in source["environment"].items()}
    if any(value is None for value in claims.values()):
        raise ProbeError("source-identity-missing")
    if claims != source["expected"]:
        raise ProbeError("source-identity-mismatch")
    receipt["source_identity"].update(matched=True, claims=deepcopy(source["expected"]))
    if destination["scheme"] != "http":
        raise ProbeError("unsupported-scheme")
    host = f"{destination['service']}.{destination['namespace']}.svc.{destination['cluster_domain']}"
    receipt["network"]["host"] = host
    addresses = _bounded_call(lambda _: resolver(host, destination["port"]), limits["dns_seconds"], deadline, "dns-timeout")
    if not isinstance(addresses, list) or not 1 <= len(addresses) <= 16:
        raise ProbeError("dns-address-mismatch")
    normalized = {_address(address) for address in addresses}
    if normalized != set(destination["expected_addresses"]):
        raise ProbeError("dns-address-mismatch")
    receipt["network"]["dns_addresses"] = sorted(normalized)
    # The declared order selects one address; no alternate-address retries.
    address = destination["expected_addresses"][0]
    raw = _bounded_call(lambda remaining: connector(address, destination["port"], remaining),
                        limits["connect_seconds"], deadline, "connect-timeout", late_close=True)
    connection = response = None
    try:
        try:
            peer = _address(raw.getpeername()[0])
        except ProbeError:
            raise ProbeError("peer-address-mismatch") from None
        if peer not in normalized:
            raise ProbeError("peer-address-mismatch")
        receipt["network"]["connected_peer"] = peer
        wrapped = _Socket(raw)
        connection = http.client.HTTPConnection(host, destination["port"])
        connection.sock = wrapped
        def headers():
            authority = host if destination["port"] == 80 else f"{host}:{destination['port']}"
            connection.request("GET", destination["path"], headers={"Host": authority, "Connection": "close",
                "Accept": "*/*", "Accept-Encoding": "identity", "User-Agent": "devsecops-http-probe/1"})
            return connection.getresponse()
        response = _socket_phase(raw, headers, limits["header_seconds"], deadline, "header-timeout")
        if not wrapped.reader.complete or response.headers.defects:
            raise ProbeError("invalid-http-response")
        wrapped.reader.headers = False
        observation = receipt["observation"]
        if not 100 <= response.status <= 599:
            raise ProbeError("invalid-http-response")
        observation["status"] = response.status
        if 300 <= response.status < 400:
            observation["http_decision"] = "fail"
            raise ProbeError("redirect-rejected")
        if response.status != request["expectation"]["status"]:
            observation["http_decision"] = "fail"
        if response.getheader("Content-Encoding", "identity").lower() != "identity":
            raise ProbeError("unsupported-content-encoding")
        lengths = response.headers.get_all("Content-Length", [])
        if response.headers.get_all("Transfer-Encoding") or len(lengths) > 1:
            raise ProbeError("unsupported-http-framing")
        if lengths:
            if not re.fullmatch(r"[0-9]{1,10}", lengths[0]):
                raise ProbeError("invalid-http-response")
            if int(lengths[0]) > limits["body_bytes"]:
                raise ProbeError("response-too-large")
        body = _socket_phase(raw, lambda: response.read(limits["body_bytes"] + 1), limits["body_seconds"], deadline, "body-timeout")
        if len(body) > limits["body_bytes"]:
            raise ProbeError("response-too-large")
        if lengths and len(body) != int(lengths[0]):
            raise ProbeError("invalid-http-response")
        observation.update(body_bytes=len(body), body_sha256=hashlib.sha256(body).hexdigest())
        expected = request["expectation"]["body_sha256"]
        observation["body_sha256_matched"] = observation["body_sha256"] == expected if expected is not None else None
        receipt["execution_status"] = "complete"
        if response.status != request["expectation"]["status"]:
            observation.update(outcome="unexpected-status", http_decision="fail")
        elif expected is not None and not observation["body_sha256_matched"]:
            observation.update(outcome="body-mismatch", http_decision="fail")
        else:
            observation.update(outcome="http-response", http_decision="pass")
    finally:
        if response is not None:
            response.close()
        if connection is not None:
            connection.close()
        raw.close()


def probe(request, *, _resolver=None, _connector=None, _environ=None):
    """Record exactly one scoped GET; injection is test-only and always labeled."""
    started = datetime.now(timezone.utc)
    monotonic_start = time.monotonic()
    controlled = any(value is not None for value in (_resolver, _connector, _environ))
    value, artifact = _load(request)
    receipt = {"schema_version": 1, "kind": "http-probe-receipt", "observation_kind": "direct-http-request",
        "execution_mode": "controlled-test" if controlled else "operator", "execution_status": "partial", "decision": "unknown",
        "policy_verdict": "unknown", "recovery": "not-evaluated", "request": value, "request_artifact": artifact,
        "started_at": started.isoformat(), "finished_at": started.isoformat(), "elapsed_seconds": 0,
        "source_identity": {"matched": False, "claims": None, "claims_source": "environment-references", "authentication": "not-verified"},
        "network": {"host": None, "dns_addresses": [], "connected_peer": None},
        "observation": {"outcome": "input-invalid", "http_decision": "unknown", "status": None,
            "body_bytes": None, "body_sha256": None, "body_sha256_matched": None},
        "gaps": [{"code": code, "required": True} for code in ("request-authorization-unverified", "source-pod-identity-unverified",
            "destination-service-mapping-unverified", "receipt-origin-unverified", "network-policy-causality-unverified", "recovery-not-evaluated")]}
    if controlled:
        receipt["gaps"].append({"code": "controlled-test-transport", "required": True})
    if value is not None:
        try:
            _http(value, receipt, _resolver or _resolve, _connector or _connect, os.environ if _environ is None else _environ,
                  monotonic_start + value["limits"]["total_seconds"])
        except ProbeError as exc:
            receipt["observation"]["outcome"] = exc.code
            receipt["gaps"].append({"code": exc.code, "required": True})
        except KeyboardInterrupt:
            receipt["observation"]["outcome"] = "interrupted"
            receipt["gaps"].append({"code": "interrupted", "required": True})
        except (OSError, ValueError, TypeError, AttributeError, http.client.HTTPException):
            receipt["observation"]["outcome"] = "invalid-http-response"
            receipt["gaps"].append({"code": "invalid-http-response", "required": True})
    else:
        receipt["gaps"].append({"code": "input-invalid", "required": True})
    receipt["finished_at"] = datetime.now(timezone.utc).isoformat()
    receipt["elapsed_seconds"] = max(0, time.monotonic() - monotonic_start)
    if datetime.fromisoformat(receipt["finished_at"]) < started:
        receipt["observation"].update(outcome="clock-regressed", http_decision="unknown")
        receipt["execution_status"] = "partial"
        receipt["gaps"].append({"code": "clock-regressed", "required": True})
    receipt["receipt_sha256"] = _sha(receipt)
    return receipt


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "HTTP probe requires only --request PATH; request values are not printed on errors\n")


def main(argv=None):
    parser = _Parser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path, help="Explicit operator-provided JSON request")
    args = parser.parse_args(argv)
    result = probe(args.request)
    print(_canonical(result).decode(), end="", flush=True)
    return {"pass": 0, "fail": 1, "unknown": 2}[result["observation"]["http_decision"]]


if __name__ == "__main__":
    raise SystemExit(main())
