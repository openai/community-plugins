"""Controlled filesystem/API tests; these do not qualify a live guest."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import base64
import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from devsecops.factory import service_link_guest as guest


@pytest.fixture(scope="module")
def ca(tmp_path_factory):
    path = tmp_path_factory.mktemp("public-ca")
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(path / "key"),
            "-out",
            str(path / "ca.pem"),
            "-days",
            "1",
            "-subj",
            "/CN=Controlled Test CA",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
        timeout=15,
    )
    return (path / "ca.pem").read_bytes()


@pytest.fixture
def packet(ca):
    return {
        "schema_version": 1,
        "action": "up",
        "run_id": str(uuid4()),
        "operation_id": str(uuid4()),
        "binding": "a" * 64,
        "cluster_uid": "original-cluster",
        "foundation_binding": "b" * 64,
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
        "timeout": 10,
        "services": {
            role: {
                "origin": f"https://{role}.controlled.test:{port}",
                "address": address,
                "port": port,
                "ca_b64": base64.b64encode(ca).decode(),
                "ca_sha256": guest._sha(ca),
            }
            for role, address, port in (("gitlab", "10.0.2.100", 8444), ("registry", "10.0.2.101", 8443))
        },
        "coredns_image_pins": ["rancher/hardened-coredns:v1-controlled"],
    }


def resources(packet):
    cm = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "actual-dns-config",
            "namespace": "kube-system",
            "uid": "dns-map-uid",
            "resourceVersion": "1",
        },
        "data": {"observed-config": ".:53 {\n    errors\n    reload\n    forward . /etc/resolv.conf\n}\n"},
    }
    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "actual-dns-controller",
            "namespace": "kube-system",
            "uid": "dns-controller-uid",
            "generation": 2,
            "resourceVersion": "4",
        },
        "spec": {
            "replicas": 1,
            "template": {
                "metadata": {"labels": {"actual-dns": "selected"}},
                "spec": {
                    "containers": [
                        {
                            "name": "dns",
                            "image": packet["coredns_image_pins"][0],
                            "args": ["-conf", "/actual-mount/selected-file"],
                            "volumeMounts": [{"name": "config", "mountPath": "/actual-mount"}],
                        }
                    ],
                    "volumes": [
                        {
                            "name": "config",
                            "configMap": {
                                "name": cm["metadata"]["name"],
                                "items": [{"key": "observed-config", "path": "selected-file"}],
                            },
                        }
                    ],
                },
            },
        },
        "status": {
            "observedGeneration": 2,
            "replicas": 1,
            "updatedReplicas": 1,
            "readyReplicas": 1,
            "availableReplicas": 1,
        },
    }
    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": "actual-dns-service",
            "namespace": "kube-system",
            "uid": "dns-service-uid",
            "resourceVersion": "5",
        },
        "spec": {
            "selector": {"actual-dns": "selected"},
            "clusterIPs": ["10.43.0.10"],
            "ports": [{"port": 53, "protocol": "UDP"}],
        },
    }
    return {"configmap": cm, "deployment": deployment, "service": service}


@pytest.fixture
def world(tmp_path, monkeypatch, packet):
    paths = {
        "ROOT": tmp_path / "journal",
        "HOSTS": tmp_path / "etc/hosts",
        "REGISTRIES": tmp_path / "etc/rke2/registries.yaml",
        "TRUST": tmp_path / "etc/rke2/trust",
    }
    for name, path in paths.items():
        monkeypatch.setattr(guest, name, path)
    paths["REGISTRIES"].parent.mkdir(parents=True)
    hosts = b"127.0.0.1 localhost\n# preserved original\n"
    registry = b'{"configs":{"unrelated.test":{"auth":{"password":"private-controlled"}}}}\n'
    paths["HOSTS"].write_bytes(hosts)
    paths["HOSTS"].chmod(0o644)
    paths["REGISTRIES"].write_bytes(registry)
    paths["REGISTRIES"].chmod(0o640)
    data = resources(packet)
    value = SimpleNamespace(
        data=data,
        effects=[],
        invocation="1" * 32,
        originals=(hosts, registry),
        fail_replace=None,
        fail_restart=None,
        packet=packet,
        paths=paths,
    )

    def kubectl(arguments, *, deadline, document=None):
        assert deadline > time.monotonic()
        if arguments[0] == "replace":
            value.effects.append(("replace", deepcopy(document)))
            if value.fail_replace == "before":
                raise subprocess.TimeoutExpired("controlled kubectl", 0.01)
            assert document["metadata"]["uid"] == data["configmap"]["metadata"]["uid"]
            assert document["metadata"]["resourceVersion"] == data["configmap"]["metadata"]["resourceVersion"]
            data["configmap"] = deepcopy(document)
            data["configmap"]["metadata"]["resourceVersion"] = str(
                int(document["metadata"]["resourceVersion"]) + 1
            )
            if value.fail_replace == "after":
                raise subprocess.TimeoutExpired("controlled kubectl", 0.01)
            return deepcopy(data["configmap"])
        assert arguments[0] == "get"
        kind = arguments[1]
        if kind == "namespace":
            return {"metadata": {"uid": packet["cluster_uid"]}}
        if kind == "nodes":
            return {"items": [{"status": {"conditions": [{"type": "Ready", "status": "True"}]}}]}
        if kind in ("configmaps", "deployments", "services"):
            return {"items": [deepcopy(data[kind[:-1]])]}
        assert arguments[2] == data[kind]["metadata"]["name"]
        return deepcopy(data[kind])

    def run(argv, *, deadline, **kwargs):
        assert deadline > time.monotonic()
        if argv == ["systemctl", "show", "rke2-server.service", "--property=InvocationID", "--value"]:
            return value.invocation.encode() + b"\n"
        assert argv == ["systemctl", "restart", "--no-block", "rke2-server.service"]
        value.effects.append(("restart", value.invocation))
        if value.fail_restart != "before":
            value.invocation = str(int(value.invocation[0]) + 1) * 32
        if value.fail_restart:
            raise subprocess.TimeoutExpired("controlled systemctl", 0.01)
        return b""

    def dns(address, domain, *, deadline):
        assert address == "10.43.0.10"
        role = domain.split(".")[0]
        if "devsecops-service-links" in data["configmap"]["data"]["observed-config"]:
            return {"rcode": 0, "addresses": [packet["services"][role]["address"]]}
        return {"rcode": 3, "addresses": []}

    monkeypatch.setattr(guest, "_kubectl", kubectl)
    monkeypatch.setattr(guest, "_run", run)
    monkeypatch.setattr(guest, "_dns", dns)
    monkeypatch.setattr(
        guest, "_tls_probe", lambda row, role, deadline: {"origin": row["origin"], "status": 200}
    )
    return value


def request(packet, action):
    return {**packet, "action": action, "operation_id": str(uuid4())}


def state(packet):
    return guest._json((guest.ROOT / packet["run_id"] / "state.json").read_bytes())


def mode(path):
    return path.stat().st_mode & 0o777


def test_discover_actual_mount_and_key(packet):
    data = resources(packet)
    plan = guest.discover_coredns(
        {"items": [data["deployment"]]},
        {"items": [data["service"]]},
        {"actual-dns-config": data["configmap"]},
        packet,
    )
    assert plan["key"] == "observed-config"
    text = plan["after"]["data"]["observed-config"]
    assert "gitlab.controlled.test:53" in text and "registry.controlled.test:53" in text
    assert "fallthrough" not in text and "no_reverse" in text


@pytest.mark.parametrize(
    "change",
    [
        "duplicate-controller",
        "unselected-image",
        "subpath",
        "missing-reload",
        "duplicate-service",
        "foreign-domain",
    ],
)
def test_dns_ambiguity_rejected(packet, change):
    data = resources(packet)
    deployments, services = [data["deployment"]], [data["service"]]
    if change == "duplicate-controller":
        deployments.append(deepcopy(deployments[0]))
    elif change == "unselected-image":
        deployments[0]["spec"]["template"]["spec"]["containers"][0]["image"] = "other/image:v1"
    elif change == "subpath":
        deployments[0]["spec"]["template"]["spec"]["containers"][0]["volumeMounts"][0]["subPath"] = (
            "selected-file"
        )
    elif change == "missing-reload":
        data["configmap"]["data"]["observed-config"] = ".:53 { errors }"
    elif change == "duplicate-service":
        services.append(deepcopy(services[0]))
    elif change == "foreign-domain":
        data["configmap"]["data"]["other"] = "gitlab.controlled.test"
    with pytest.raises(ValueError):
        guest.discover_coredns(
            {"items": deployments}, {"items": services}, {"actual-dns-config": data["configmap"]}, packet
        )


def test_up_reconcile_down_preserve_bytes_metadata_and_dns(world, packet):
    before = deepcopy(world.data["configmap"]["data"])
    up = guest.execute(packet)
    assert up["ready"] and up["mutation_effect"] == "applied" and not up["complete_cleanup"]
    assert mode(guest.HOSTS) == 0o644 and mode(guest.REGISTRIES) == 0o640
    assert "private-controlled" not in json.dumps(up)
    effects = deepcopy(world.effects)
    inspected = guest.execute(request(packet, "reconcile"))
    assert inspected["ready"] and inspected["mutation_effect"] == "not_applied"
    assert world.effects == effects
    down = guest.execute(request(packet, "down"))
    assert down["complete_cleanup"] and not down["unresolved_operations"]
    assert (
        guest.HOSTS.read_bytes() == world.originals[0] and guest.REGISTRIES.read_bytes() == world.originals[1]
    )
    assert mode(guest.HOSTS) == 0o644 and mode(guest.REGISTRIES) == 0o640
    assert not (guest.TRUST / packet["run_id"]).exists() and world.data["configmap"]["data"] == before
    assert len([row for row in world.effects if row[0] == "restart"]) == 2
    effects = deepcopy(world.effects)
    assert guest.execute(request(packet, "down"))["complete_cleanup"]
    assert guest.execute(request(packet, "reconcile"))["complete_cleanup"]
    assert world.effects == effects
    with pytest.raises(ValueError, match="already exists"):
        guest.execute(request(packet, "up"))


def test_absent_registry_is_removed(world, packet):
    guest.REGISTRIES.unlink()
    assert guest.execute(packet)["ready"]
    assert guest.execute(request(packet, "down"))["complete_cleanup"]
    assert not guest.REGISTRIES.exists()


@pytest.mark.parametrize(
    "change",
    ["hosts-bytes", "hosts-inode", "hosts-mode", "dns-data", "dns-uid", "dns-service", "trust-directory"],
)
def test_foreign_changes_block_cleanup(world, packet, change):
    assert guest.execute(packet)["ready"]
    if change == "hosts-bytes":
        guest.HOSTS.write_bytes(b"foreign\n")
    elif change == "hosts-inode":
        temp = guest.HOSTS.with_suffix(".other")
        temp.write_bytes(guest.HOSTS.read_bytes())
        temp.chmod(0o644)
        os.replace(temp, guest.HOSTS)
    elif change == "hosts-mode":
        guest.HOSTS.chmod(0o600)
    elif change == "dns-data":
        world.data["configmap"]["data"]["foreign"] = "later edit"
    elif change == "dns-uid":
        world.data["configmap"]["metadata"]["uid"] = "replacement"
    elif change == "dns-service":
        world.data["service"]["spec"]["clusterIPs"] = ["10.43.0.11"]
    elif change == "trust-directory":
        (guest.TRUST / packet["run_id"]).chmod(0o755)
    effects = deepcopy(world.effects)
    try:
        result = guest.execute(request(packet, "down"))
        assert not result["complete_cleanup"] and result["unresolved_operations"]
    except ValueError:
        pass
    assert world.effects == effects


@pytest.mark.parametrize("kind", ["hosts", "registry-yaml", "registry-wildcard", "registry-selected"])
def test_foreign_preimages_rejected_before_effect(world, packet, kind):
    if kind == "hosts":
        guest.HOSTS.write_bytes(b"192.0.2.1 gitlab.controlled.test\n")
    elif kind == "registry-yaml":
        guest.REGISTRIES.write_bytes(b"configs:\n  foreign: {}\n")
    elif kind == "registry-wildcard":
        guest.REGISTRIES.write_bytes(b'{"configs":{"*":{}}}')
    elif kind == "registry-selected":
        guest.REGISTRIES.write_bytes(b'{"configs":{"registry.controlled.test:8443":{}}}')
    with pytest.raises(ValueError):
        guest.execute(packet)
    assert world.effects == [] and not guest.TRUST.exists()


@pytest.mark.parametrize("phase", ["before", "after"])
def test_unknown_api_write_observed_never_replayed(world, packet, phase):
    world.fail_replace = phase
    result = guest.execute(packet)
    assert not result["ready"]
    assert bool(result["unresolved_operations"]) == (phase == "before")
    assert result["cleanup_eligible"] == (phase == "after")
    assert state(packet)["pending"]["label"] == "dns-up"
    effects = deepcopy(world.effects)
    assert not guest.execute(request(packet, "reconcile"))["ready"]
    assert world.effects == effects
    world.fail_replace = None
    down = guest.execute(request(packet, "down"))
    if phase == "before":
        assert down["unresolved_operations"] and not down["complete_cleanup"] and world.effects == effects
    else:
        assert down["complete_cleanup"]
        assert len([row for row in world.effects if row[0] == "replace"]) == 2
        assert len([row for row in world.effects if row[0] == "restart"]) == 1


@pytest.mark.parametrize("phase", ["before", "after"])
def test_unknown_restart_never_reissued(world, packet, phase):
    world.fail_restart = phase
    assert not guest.execute(packet)["ready"]
    assert state(packet)["pending"]["label"] == "restart-up"
    effects = deepcopy(world.effects)
    guest.execute(request(packet, "reconcile"))
    assert world.effects == effects
    world.fail_restart = None
    down = guest.execute(request(packet, "down"))
    if phase == "before":
        assert not down["complete_cleanup"] and down["unresolved_operations"] and world.effects == effects
    else:
        assert down["complete_cleanup"]
        assert len([row for row in world.effects if row[0] == "restart"]) == 2


def test_postrestart_verification_failure_does_not_duplicate_restart(world, packet, monkeypatch):
    assert guest.execute(packet)["ready"]
    original = guest._verify
    monkeypatch.setattr(
        guest, "_verify", lambda *a, **k: (_ for _ in ()).throw(ValueError("controlled failure"))
    )
    assert not guest.execute(request(packet, "down"))["complete_cleanup"]
    assert state(packet)["restart_down"]["after_invocation"] == world.invocation
    effects = deepcopy(world.effects)
    monkeypatch.setattr(guest, "_verify", original)
    assert guest.execute(request(packet, "down"))["complete_cleanup"]
    assert world.effects == effects


def test_intent_durable_before_file_api_and_restart_effects(world, packet, monkeypatch):
    original_write, original_kube, original_run = guest._write, guest._kubectl, guest._run
    observed = []

    def write(path, raw, **kwargs):
        if path in (guest.HOSTS, guest.REGISTRIES) or path.name == "registry-ca.crt":
            pending = state(packet)["pending"]
            assert pending and pending["operation_id"] == packet["operation_id"]
            observed.append(pending["label"])
        return original_write(path, raw, **kwargs)

    def kube(args, **kwargs):
        if args[0] == "replace":
            assert state(packet)["pending"]["label"] == "dns-up"
            observed.append("dns-up")
        return original_kube(args, **kwargs)

    def run(args, **kwargs):
        if args[1] == "restart":
            pending = state(packet)["pending"]
            assert pending["label"] == "restart-up" and pending["before_invocation"] == world.invocation
            observed.append("restart-up")
        return original_run(args, **kwargs)

    monkeypatch.setattr(guest, "_write", write)
    monkeypatch.setattr(guest, "_kubectl", kube)
    monkeypatch.setattr(guest, "_run", run)
    assert guest.execute(packet)["ready"]
    assert observed == ["ca-up", "hosts-up", "registries-up", "dns-up", "restart-up"]


def test_expiry_during_apply_prevents_next_effect(world, packet, monkeypatch):
    original, calls = guest._effect_guard, []

    def guard(*args):
        calls.append(True)
        if len(calls) >= 4:
            raise ValueError("controlled expiry")
        return original(*args)

    monkeypatch.setattr(guest, "_effect_guard", guard)
    result = guest.execute(packet)
    assert not result["ready"] and result["cleanup_eligible"]
    assert world.effects == [] and guest.HOSTS.read_bytes() == world.originals[0]


def test_foreign_journal_identity_blocked(world, packet):
    assert guest.execute(packet)["ready"]
    changed = request(packet, "down")
    changed["foundation_binding"] = "c" * 64
    with pytest.raises(ValueError, match="ownership"):
        guest.execute(changed)


def test_symlink_rejected_without_effect(world, packet):
    actual = guest.HOSTS.with_name("actual-hosts")
    guest.HOSTS.rename(actual)
    guest.HOSTS.symlink_to(actual)
    with pytest.raises(ValueError, match="symlink"):
        guest.execute(packet)
    assert world.effects == []


@pytest.mark.parametrize(
    "change", ["expired", "port", "address", "userinfo", "domain", "extra-key", "ca-hash", "images"]
)
def test_closed_packet_route_and_expiry(packet, change):
    if change == "expired":
        packet["expires_at"] = "2000-01-01T00:00:00+00:00"
    elif change == "port":
        packet["services"]["gitlab"]["port"] = 443
    elif change == "address":
        packet["services"]["gitlab"]["address"] = "127.0.0.1"
    elif change == "userinfo":
        packet["services"]["gitlab"]["origin"] = "https://user@gitlab.controlled.test:8444"
    elif change == "domain":
        packet["services"]["registry"]["origin"] = "https://registry.foreign.test:8443"
    elif change == "extra-key":
        packet["commands"] = ["arbitrary"]
    elif change == "ca-hash":
        packet["services"]["gitlab"]["ca_sha256"] = "0" * 64
    elif change == "images":
        packet["coredns_image_pins"] = ["foreign/image:v1"]
    with pytest.raises(ValueError):
        guest.validate_packet(packet)


def test_read_reconciliation_can_inspect_expired_mutation_grant(world, packet):
    assert guest.execute(packet)["ready"]
    current = request(packet, "reconcile")
    current["expires_at"] = "2000-01-01T00:00:00+00:00"
    assert guest.execute(current)["ready"]
    current["action"] = "down"
    assert guest.execute(current)["complete_cleanup"]


def test_child_output_and_absolute_time_bounded():
    with pytest.raises(ValueError, match="output exceeds"):
        guest._run(
            [sys.executable, "-c", 'import sys;sys.stdout.write("x"*100000)'],
            deadline=time.monotonic() + 5,
            limit=100,
        )
    started = time.monotonic()
    with pytest.raises(ValueError, match="deadline"):
        guest._run([sys.executable, "-c", "import time;time.sleep(5)"], deadline=started + 0.05)
    assert time.monotonic() - started < 2


@pytest.mark.parametrize(
    "chunks,passes",
    [
        ([b"HTTP/1.1 200 OK\r\n", b""], False),
        ([b"HTTP/1.1 200 OK\r\n\r\n"], True),
        ([b"HTTP/1.1 302 Redirect\r\n\r\n"], False),
    ],
)
def test_tls_exact_sni_ca_address_and_eof(packet, monkeypatch, chunks, passes):
    row, calls = packet["services"]["gitlab"], []

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def settimeout(self, timeout):
            assert 0 < timeout <= 5
            calls.append(("timeout", timeout))

        def connect(self, address):
            calls.append(("connect", address))

        def do_handshake(self):
            calls.append(("handshake", None))

        def sendall(self, data):
            calls.append(("send", data))

        def recv(self, length):
            return chunks.pop(0)

        def getpeercert(self, binary_form):
            return b"controlled peer"

    connection = Socket()

    class TLS:
        minimum_version = None

        def wrap_socket(self, socket, **kwargs):
            assert kwargs == {"server_hostname": "gitlab.controlled.test", "do_handshake_on_connect": False}
            return connection

    def context(**kwargs):
        assert kwargs == {"cadata": base64.b64decode(row["ca_b64"]).decode()}
        return TLS()

    monkeypatch.setattr(guest.ssl, "create_default_context", context)
    monkeypatch.setattr(guest.socket, "socket", lambda *args: connection)
    monkeypatch.setattr(guest, "_run", lambda argv, **kwargs: b"10.0.2.100 STREAM gitlab.controlled.test\n")
    if passes:
        assert guest._tls_probe(row, "gitlab", deadline=time.monotonic() + 10)["status"] == 200
    else:
        with pytest.raises(ValueError, match="incomplete"):
            guest._tls_probe(row, "gitlab", deadline=time.monotonic() + 10)
    assert ("connect", ("10.0.2.100", 8444)) in calls
    assert any(kind == "send" and b"Host: gitlab.controlled.test:8444\r\n" in value for kind, value in calls)


def test_wrong_resolution_never_connects(packet, monkeypatch):
    monkeypatch.setattr(guest, "_run", lambda *a, **k: b"192.0.2.1 STREAM selected\n")
    monkeypatch.setattr(guest.socket, "socket", lambda *a: pytest.fail("unselected route must not connect"))
    with pytest.raises(ValueError, match="resolution"):
        guest._tls_probe(packet["services"]["gitlab"], "gitlab", deadline=time.monotonic() + 5)


def test_known_partial_is_cleanup_eligible_without_ready_or_up_resume(world, packet, monkeypatch):
    original, calls = guest._effect_guard, []

    def guard(*args):
        calls.append(True)
        if len(calls) >= 4:
            raise ValueError("controlled expiry before file effect")
        return original(*args)

    monkeypatch.setattr(guest, "_effect_guard", guard)
    partial = guest.execute(packet)
    assert partial["cleanup_eligible"] and partial["observed_partial"] and not partial["ready"]
    assert not partial["unresolved_operations"]
    monkeypatch.setattr(guest, "_effect_guard", original)
    effects = deepcopy(world.effects)
    readback = guest.execute(request(packet, "reconcile"))
    assert readback["cleanup_eligible"] and not readback["ready"]
    assert world.effects == effects
    with pytest.raises(ValueError, match="already exists"):
        guest.execute(request(packet, "up"))
    assert guest.execute(request(packet, "down"))["complete_cleanup"]
    assert not guest.TRUST.exists()


def test_lost_cleanup_restart_response_reconciles_without_second_restart(world, packet):
    assert guest.execute(packet)["ready"]
    world.fail_restart = "after"
    partial = guest.execute(request(packet, "down"))
    assert not partial["complete_cleanup"] and partial["cleanup_eligible"]
    assert state(packet)["pending"]["label"] == "restart-down"
    effects = deepcopy(world.effects)
    assert guest.execute(request(packet, "reconcile"))["cleanup_eligible"]
    world.fail_restart = None
    assert guest.execute(request(packet, "down"))["complete_cleanup"]
    assert world.effects == effects


def test_existing_trust_parent_is_preserved(world, packet):
    guest.TRUST.mkdir(mode=0o755)
    original = guest._metadata(guest.TRUST)
    (guest.TRUST / "unrelated").write_bytes(b"preserved")
    assert guest.execute(packet)["ready"]
    assert guest.execute(request(packet, "down"))["complete_cleanup"]
    assert guest._metadata(guest.TRUST) == original
    assert (guest.TRUST / "unrelated").read_bytes() == b"preserved"


def test_tls_progress_cannot_extend_absolute_deadline(packet, monkeypatch):
    row, now = packet["services"]["gitlab"], [100.0]

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def settimeout(self, timeout):
            assert 0 < timeout <= 5

        def connect(self, address):
            pass

        def do_handshake(self):
            pass

        def sendall(self, data):
            pass

        def recv(self, length):
            now[0] += 2
            return b"H"

    connection = Socket()

    class TLS:
        minimum_version = None

        def wrap_socket(self, *args, **kwargs):
            return connection

    monkeypatch.setattr(guest.ssl, "create_default_context", lambda **kwargs: TLS())
    monkeypatch.setattr(guest.socket, "socket", lambda *args: connection)
    monkeypatch.setattr(guest, "_run", lambda *args, **kwargs: b"10.0.2.100 STREAM selected\n")
    monkeypatch.setattr(guest.time, "monotonic", lambda: now[0])
    with pytest.raises(ValueError, match="deadline"):
        guest._tls_probe(row, "gitlab", deadline=105)
    assert now[0] == 106
