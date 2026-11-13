"""Explicit storage routes over controlled effects; no network or VM execution."""

import base64
import json
from pathlib import Path
import ssl
import time
from types import SimpleNamespace
from uuid import uuid4

import jsonschema
import pytest

from devsecops.factory import runner_vm_recipe as recipe
from devsecops.factory import runner_vm_remote as vm
from devsecops.factory import runner_vm_guest_routes as guest
from devsecops.factory import runner_vm_routes_remote as remote
from devsecops.factory import owned_runner_service as service
from devsecops.factory import owned_runner_vm as owner
from devsecops.factory import runner_install
from devsecops.factory.state import StateError, _sha
from tests.test_runner_vm_recipe import selection
from tests.test_runner_vm_install import vm_fixture
from tests.test_owned_runner_service import staged_dependencies
from tests import test_runner_vm_routes as controlled

ca, packet = controlled.ca, controlled.packet
guest_scene, remote_scene = controlled.guest_scene, controlled.remote_scene
deny_uncontrolled_network = controlled.deny_uncontrolled_network
action = controlled.action


REPOSITORY = "ironbank/opensource/nginx/nginx"


def policy_bytes(**changes):
    value = {"schema_version": 1, "kind": "registry-blob-download-policy",
             "registry_origin": recipe.EXTERNAL_ORIGINS[0], "repositories": [REPOSITORY],
             "storage_origins": [recipe.STORAGE_ORIGIN]}
    value.update(changes)
    return recipe.canonical(value)


def add_storage(packet, ca):
    raw = policy_bytes()
    packet["blob_download_policy"] = {"sha256": _sha(raw), "document_b64": base64.b64encode(raw).decode()}
    for role in ("registry1", recipe.STORAGE_ROLE):
        address, port = guest.ROLE[role]
        packet["services"][role] = {"origin": guest.EXTERNAL[role], "address": address,
            "port": port, "ca_b64": base64.b64encode(ca).decode(), "ca_sha256": _sha(ca)}
    return raw


def selected_storage(tmp_path):
    value, _ = selection(tmp_path)
    path = tmp_path / "download-policy.json"
    raw = policy_bytes()
    path.write_bytes(raw)
    value["egress"] = {"external_origins": [recipe.EXTERNAL_ORIGINS[0], recipe.STORAGE_ORIGIN],
        "external_ca_bundle": {"path": str(tmp_path / "roots.pem"), "sha256": "e" * 64},
        "blob_download_policy": {"path": str(path), "sha256": _sha(raw)}}
    return value


def test_storage_recipe_and_seed_require_explicit_pinned_selection(tmp_path):
    value = selected_storage(tmp_path)
    assert recipe.validate(value) == value
    jsonschema.validate(value, recipe.schema())
    network = vm._netdev(value)
    assert "restrict=on" in network and "10.0.2.104:443" in network
    assert "registry1-storage" in network and "10.0.2.103" not in network
    assert vm._host_entries("controlled", value)[-1] == (
        recipe.STORAGE_ADDRESS, recipe.STORAGE_ORIGIN.removeprefix("https://"))
    assert guest.ROLE[recipe.STORAGE_ROLE] == remote.ROLES[recipe.STORAGE_ROLE] == (recipe.STORAGE_ADDRESS, 443)
    assert guest.EXTERNAL[recipe.STORAGE_ROLE] == remote.EXTERNAL[recipe.STORAGE_ROLE] == recipe.STORAGE_ORIGIN
    value["egress"].pop("blob_download_policy")
    with pytest.raises(ValueError):
        recipe.validate(value)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(value, recipe.schema())


@pytest.mark.parametrize("damage", ["storage-absent", "registry-absent", "extra-origin", "hash", "null", "traversal"])
def test_ambiguous_vm_egress_selection_fails_before_effects(tmp_path, damage):
    value = selected_storage(tmp_path)
    egress = value["egress"]
    if damage == "storage-absent":
        egress["external_origins"].remove(recipe.STORAGE_ORIGIN)
    elif damage == "registry-absent":
        egress["external_origins"].remove(recipe.EXTERNAL_ORIGINS[0])
    elif damage == "extra-origin":
        egress["external_origins"].append("https://other.amazonaws.com")
    elif damage == "hash":
        egress["blob_download_policy"]["sha256"] = "bad"
    elif damage == "null":
        egress["blob_download_policy"] = None
    else:
        egress["blob_download_policy"]["path"] = "/tmp/../policy"
    with pytest.raises(ValueError):
        recipe.validate(value)


@pytest.mark.parametrize("change", [
    {"schema_version": True}, {"registry_origin": "https://other.example"},
    {"storage_origins": ["https://other.amazonaws.com"]},
    {"storage_origins": [recipe.STORAGE_ORIGIN, recipe.STORAGE_ORIGIN]},
    {"repositories": []}, {"repositories": [REPOSITORY, REPOSITORY]},
    {"repositories": ["ironbank/../other"]}, {"extra": True},
])
def test_closed_vm_profile_rejects_policy_scope_changes(change):
    raw = policy_bytes(**change)
    with pytest.raises(ValueError):
        recipe.blob_policy_document(raw, _sha(raw))


@pytest.mark.parametrize("damage", ["missing", "hash", "document", "origin", "port", "no-registry"])
def test_guest_rejects_unbound_or_changed_storage_route(packet, ca, damage):
    add_storage(packet, ca)
    if damage == "missing":
        packet.pop("blob_download_policy")
    elif damage == "hash":
        packet["blob_download_policy"]["sha256"] = "a" * 64
    elif damage == "document":
        raw = policy_bytes(storage_origins=["https://other.amazonaws.com"])
        packet["blob_download_policy"].update(sha256=_sha(raw), document_b64=base64.b64encode(raw).decode())
    elif damage == "origin":
        packet["services"][recipe.STORAGE_ROLE]["origin"] = "https://other.amazonaws.com"
    elif damage == "port":
        packet["services"][recipe.STORAGE_ROLE]["port"] = 8443
    else:
        packet["services"].pop("registry1")
    with pytest.raises(ValueError):
        guest.validate_packet(packet)


def test_storage_trust_setup_and_owned_cleanup(guest_scene, ca):
    packet, root, calls = guest_scene
    add_storage(packet, ca)
    up = guest.execute(packet)
    assert up["ready"] and calls == ["gitlab", "registry", "registry1", recipe.STORAGE_ROLE]
    trust = Path(up["trust_directory"])
    assert (trust / "registry1-storage-ca.pem").read_bytes() == ca
    packet.update(action="down", operation_id=str(uuid4()))
    down = guest.execute(packet)
    assert down["complete_cleanup"] and not trust.exists()
    assert (root / "inputs").is_dir()


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_storage_tls_readiness_uses_only_system_roots_and_exact_peer(packet, ca, tmp_path, monkeypatch, kind):
    add_storage(packet, ca)
    row = packet["services"][recipe.STORAGE_ROLE]
    roots_file, roots_dir = tmp_path / "public-roots.pem", tmp_path / "public-roots.d"
    if kind == "file":
        roots_file.write_bytes(ca)
    else:
        roots_dir.mkdir()
    monkeypatch.setenv("SSL_CERT_FILE", "/staged/private-ca.pem")
    monkeypatch.setenv("SSL_CERT_DIR", "/staged/private-roots")
    monkeypatch.setattr(guest.ssl, "get_default_verify_paths", lambda: SimpleNamespace(
        openssl_cafile=str(roots_file), openssl_capath=str(roots_dir),
        cafile="/staged/private-ca.pem", capath="/staged/private-roots"))
    calls = []

    class TLS:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def settimeout(self, value):
            assert 0 < value <= 5

        def connect(self, destination):
            calls.append(("destination", destination))

        def do_handshake(self):
            calls.append(("handshake", True))

        def getpeercert(self, *, binary_form):
            assert binary_form
            return b"controlled-peer-certificate"

        def sendall(self, raw):
            pytest.fail("Storage readiness must not issue an object request")

    class Context:
        minimum_version = None

        def wrap_socket(self, raw, *, server_hostname, do_handshake_on_connect):
            assert self.minimum_version == ssl.TLSVersion.TLSv1_2
            assert do_handshake_on_connect is False
            calls.append(("sni", server_hostname))
            return TLS()

    def context(**kwargs):
        assert kwargs == {"cafile": str(roots_file) if kind == "file" else None,
                          "capath": str(roots_dir) if kind == "directory" else None}
        calls.append(("trust", "explicit-system-roots"))
        return Context()

    monkeypatch.setattr(guest.ssl, "create_default_context", context)
    monkeypatch.setattr(guest, "_run", lambda *args, **kwargs: b"10.0.2.104 STREAM selected.storage\n")
    monkeypatch.setattr(guest.socket, "socket", lambda *args, **kwargs: TLS())
    observed = guest._tls_probe(row, recipe.STORAGE_ROLE, time.monotonic() + 5)
    assert calls == [("trust", "explicit-system-roots"), ("destination", ("10.0.2.104", 443)),
                     ("sni", recipe.STORAGE_ORIGIN.removeprefix("https://")), ("handshake", True)]
    assert observed == {"origin": row["origin"], "address": row["address"], "port": row["port"],
                        "trust": "openssl-system-roots", "certificate_sha256": _sha(b"controlled-peer-certificate"),
                        "status": None, "probe": "tls-peer-only"}


def test_storage_tls_readiness_rejects_missing_system_roots_before_socket(packet, ca, tmp_path, monkeypatch):
    add_storage(packet, ca)
    staged = tmp_path / "staged-ca.pem"
    staged.write_bytes(ca)
    monkeypatch.setenv("SSL_CERT_FILE", str(staged))
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path))
    monkeypatch.setattr(guest.ssl, "get_default_verify_paths", lambda: SimpleNamespace(
        openssl_cafile=str(tmp_path / "absent.pem"), openssl_capath=str(tmp_path / "absent.d"),
        cafile=str(staged), capath=str(tmp_path)))
    monkeypatch.setattr(guest.ssl, "create_default_context", lambda **kwargs: pytest.fail("No ambient trust fallback"))
    monkeypatch.setattr(guest, "_run", lambda *args, **kwargs: b"10.0.2.104 STREAM selected.storage\n")
    monkeypatch.setattr(guest.socket, "socket", lambda *args, **kwargs: pytest.fail("No connection without system roots"))
    with pytest.raises(ValueError, match="existing OpenSSL system trust roots"):
        guest._tls_probe(packet["services"][recipe.STORAGE_ROLE], recipe.STORAGE_ROLE, time.monotonic() + 5)


def test_host_relay_storage_setup_and_cleanup_match_recipe(remote_scene, ca, monkeypatch):
    child, scope, process, _ = remote_scene
    raw = add_storage(child["guest"], ca)
    scope.recipe["egress"] = {"external_origins": [recipe.EXTERNAL_ORIGINS[0], recipe.STORAGE_ORIGIN],
        "external_ca_bundle": {"path": "/selected/roots.pem", "sha256": _sha(ca)},
        "blob_download_policy": {"path": "/selected/download.json", "sha256": _sha(raw)}}
    monkeypatch.setattr(remote, "external_addresses", lambda role: ["8.8.8.8"])
    up = action(remote_scene, "up")
    assert up["ready"] and process["alive"]
    config = json.loads((scope.root / "runner-routes/config.json").read_bytes())
    assert config["services"][recipe.STORAGE_ROLE]["origin"] == recipe.STORAGE_ORIGIN
    assert config["blob_download_policy_sha256"] == _sha(raw)
    down = action(remote_scene, "down")
    assert down["complete_cleanup"] and not process["alive"]
    assert not (scope.data / "service-links.json").exists()


def test_saved_relay_policy_identity_cannot_be_reselected(remote_scene, ca, monkeypatch):
    child, scope, process, calls = remote_scene
    raw = add_storage(child["guest"], ca)
    scope.recipe["egress"] = {"external_origins": [recipe.EXTERNAL_ORIGINS[0], recipe.STORAGE_ORIGIN],
        "external_ca_bundle": {"path": "/selected/roots.pem", "sha256": _sha(ca)},
        "blob_download_policy": {"path": "/selected/download.json", "sha256": _sha(raw)}}
    monkeypatch.setattr(remote, "external_addresses", lambda role: ["8.8.8.8"])
    assert action(remote_scene, "up")["ready"]
    original_calls = list(calls)
    changed = raw + b" "
    child["guest"]["blob_download_policy"] = {
        "sha256": _sha(changed), "document_b64": base64.b64encode(changed).decode()}
    scope.recipe["egress"]["blob_download_policy"]["sha256"] = _sha(changed)
    with pytest.raises(ValueError, match="target/VM/source changed"):
        action(remote_scene, "down")
    assert process["alive"] and calls == original_calls


def test_remote_rejects_different_policy_before_any_route_effect(remote_scene, ca):
    child, scope, _, _ = remote_scene
    add_storage(child["guest"], ca)
    scope.recipe["egress"] = {"external_origins": [recipe.EXTERNAL_ORIGINS[0], recipe.STORAGE_ORIGIN],
        "external_ca_bundle": {"path": "/selected/roots.pem", "sha256": _sha(ca)},
        "blob_download_policy": {"path": "/selected/download.json", "sha256": "a" * 64}}
    with pytest.raises(ValueError, match="policy differs"):
        action(remote_scene, "up")
    assert not (scope.root / "runner-routes").exists()


def configured_install(tmp_path, monkeypatch):
    stack, context, vm_path, _ = vm_fixture(tmp_path, monkeypatch)
    value = json.loads(vm_path.read_bytes())
    policy = tmp_path / "download.json"
    policy.write_bytes(policy_bytes())
    reference = {"path": str(policy), "sha256": _sha(policy.read_bytes())}
    value["egress"]["blob_download_policy"] = reference
    value["egress"]["external_origins"].append(recipe.STORAGE_ORIGIN)
    vm_path.write_bytes(recipe.canonical(value))
    bootstrap = json.loads(stack.recipe_path.read_bytes())
    bootstrap["services"]["runner_vm"]["sha256"] = _sha(vm_path.read_bytes())
    bootstrap["services"]["runner_materials"].append({"target": "/opt/devsecops/runner-inputs/download.json",
        "file": reference, "private": False})
    stack.recipe_path.write_bytes(recipe.canonical(bootstrap))
    stack.recipe_sha256 = _sha(stack.recipe_path.read_bytes())
    invocation_path = stack.driver.auxiliary_sources["mission_git_invocation"][0]
    invocation = json.loads(invocation_path.read_bytes())
    build_path = Path(invocation["configurations"]["build"]["path"])
    build = json.loads(build_path.read_bytes())
    build["blob_download_policy"] = reference
    build_path.write_bytes(recipe.canonical(build))
    invocation["configurations"]["build"]["sha256"] = _sha(build_path.read_bytes())
    invocation_path.write_bytes(recipe.canonical(invocation))
    stack.driver.auxiliary_sources["mission_git_invocation"] = (invocation_path, _sha(invocation_path.read_bytes()))
    return stack, context, policy, value


def test_install_preserves_policy_bytes_and_maps_only_selected_path(tmp_path, monkeypatch):
    stack, context, policy, _ = configured_install(tmp_path, monkeypatch)
    plan = runner_install.prepare_runner_install(stack, context)
    selected = runner_install.require_runner_install(plan, stack, context)
    root = recipe.guest_paths(context.run_id)["inputs"]
    staged = json.loads(selected["entries"][root + "/build.json"]["content"])
    assert staged["blob_download_policy"] == {"path": root + "/download.json", "sha256": _sha(policy.read_bytes())}
    assert selected["entries"][root + "/download.json"]["content"] == policy.read_bytes()
    policy.write_bytes(policy.read_bytes() + b" ")
    with pytest.raises(StateError):
        runner_install.require_runner_install(plan, stack, context)


def test_pre_dispatch_admission_requires_actual_policy_and_build(tmp_path):
    value = selected_storage(tmp_path)
    with pytest.raises(StateError, match="actual selected mission"):
        owner._admit_blob_download_policy(value, {})


def staged_storage():
    selected = staged_dependencies()
    entries = selected["entries"]
    image = {"schema_version": 1, "kind": "registry-image-policy",
             "subject": {"origin": recipe.EXTERNAL_ORIGINS[0], "repository": REPOSITORY,
                         "digest": "sha256:" + "a" * 64, "architecture": "amd64"},
             "signer": {"key_sha256": "a" * 64, "valid_from": "2026-11-01T00:00:00Z", "valid_until": "2026-11-02T00:00:00Z"},
             "predicates": [{"type": "https://example.test/evidence", "rules": [{"path": "/private", "equals": "controlled"}]}]}
    image_raw = recipe.canonical(image)
    raw = policy_bytes()
    entries["/inputs/download.json"] = {"content": raw}
    entries["/inputs/image.json"] = {"content": image_raw}
    build = json.loads(entries["/inputs/build.json"]["content"])
    build["blob_download_policy"] = {"path": "/inputs/download.json", "sha256": _sha(raw)}
    build["image"]["policy_sha256"] = _sha(image_raw)
    encoded = recipe.canonical(build)
    entries["/inputs/build.json"]["content"] = encoded
    invocation = json.loads(entries["/inputs/invocation.json"]["content"])
    invocation["configurations"]["build"]["sha256"] = _sha(encoded)
    entries["/inputs/invocation.json"]["content"] = recipe.canonical(invocation)
    facts = {"external_origins": [recipe.EXTERNAL_ORIGINS[0], recipe.STORAGE_ORIGIN],
             "blob_download_policy_sha256": _sha(raw)}
    return selected, facts


def test_installed_runner_requires_actual_pinned_storage_dependency():
    selected, facts = staged_storage()
    assert service._admit_external_dependencies(selected, facts) == tuple(sorted(facts["external_origins"]))
    facts["blob_download_policy_sha256"] = "a" * 64
    with pytest.raises(StateError, match="policy identity"):
        service._admit_external_dependencies(selected, facts)


def test_absent_build_policy_cannot_consume_storage_route():
    with pytest.raises(StateError, match="absent from"):
        service._admit_external_dependencies(staged_dependencies(), {
            "external_origins": [recipe.EXTERNAL_ORIGINS[0], recipe.STORAGE_ORIGIN],
            "blob_download_policy_sha256": "a" * 64})


def dispatch_inputs(tmp_path):
    selected, _ = staged_storage()
    entries = selected["entries"]
    build = json.loads(entries["/inputs/build.json"]["content"])
    for field, key in (("blob_download_policy", "path"), ("image", "policy")):
        original = build[field][key]
        path = tmp_path / Path(original).name
        path.write_bytes(entries[original]["content"])
        build[field][key] = str(path)
    build_path = tmp_path / "build.json"
    build_path.write_bytes(recipe.canonical(build))
    from devsecops.factory.mission_git_invocation import NAMES
    invocation = {"schema_version": 1, "kind": "mission-git-preparation-invocation",
        "configurations": {name: {"path": str(build_path), "sha256": _sha(build_path.read_bytes())} for name in NAMES},
        "run_scope": build["run_scope"], "plugin_id": "devsecops@devsecops-local", "plugin_version": "0.2.0-alpha.9",
        "source": {"repository": str(tmp_path / "checkout"), "revision": "a" * 40},
        "output": str(tmp_path / "output"), "runtime_root": str(tmp_path / "runtime"), "timeout": 3000}
    path = tmp_path / "invocation.json"
    path.write_bytes(recipe.canonical(invocation))
    vm_recipe = {"egress": {"blob_download_policy": build["blob_download_policy"]}}
    return vm_recipe, {"mission_git_invocation": (path, _sha(path.read_bytes()))}, build, build_path


def test_actual_build_image_and_policy_are_bound_before_vm_dispatch(tmp_path):
    value, sources, _, _ = dispatch_inputs(tmp_path)
    assert owner._admit_blob_download_policy(value, sources) is None


@pytest.mark.parametrize("damage", ["invocation", "build", "policy", "image", "different-ref", "absent-egress"])
def test_predispatch_rejects_stale_inputs_and_mismatched_policy(tmp_path, damage):
    value, sources, build, build_path = dispatch_inputs(tmp_path)
    if damage in {"invocation", "build", "policy", "image"}:
        path = {"invocation": sources["mission_git_invocation"][0], "build": build_path,
            "policy": Path(build["blob_download_policy"]["path"]), "image": Path(build["image"]["policy"])}[damage]
        path.write_bytes(path.read_bytes() + b" ")
    elif damage == "different-ref":
        value["egress"]["blob_download_policy"] = {**build["blob_download_policy"], "sha256": "a" * 64}
    else:
        value["egress"].clear()
    with pytest.raises((StateError, ValueError)):
        owner._admit_blob_download_policy(value, sources)


def size_policy_bytes(**changes):
    value = {"schema_version": 1, "kind": "registry-attestation-size-policy",
        "origin": recipe.EXTERNAL_ORIGINS[0], "repository": REPOSITORY,
        "subject_digest": "sha256:" + "a" * 64,
        "descriptors": [{"digest": "sha256:" + "b" * 64, "size_bytes": 8318244}]}
    value.update(changes)
    return recipe.canonical(value)


def update_staged_build(selected, build):
    entries = selected["entries"]
    encoded = recipe.canonical(build)
    entries["/inputs/build.json"]["content"] = encoded
    invocation = json.loads(entries["/inputs/invocation.json"]["content"])
    invocation["configurations"]["build"]["sha256"] = _sha(encoded)
    entries["/inputs/invocation.json"]["content"] = recipe.canonical(invocation)


def staged_size_selection(**changes):
    selected, facts = staged_storage()
    raw = size_policy_bytes(**changes)
    selected["entries"]["/inputs/sizes.json"] = {"content": raw}
    build = json.loads(selected["entries"]["/inputs/build.json"]["content"])
    build["attestation_size_policy"] = {"path": "/inputs/sizes.json", "sha256": _sha(raw)}
    update_staged_build(selected, build)
    return selected, facts, build


def test_installed_runner_admits_size_profile_without_expanding_network_routes():
    selected, facts, _ = staged_size_selection()
    assert service._admit_external_dependencies(selected, facts) == tuple(sorted(facts["external_origins"]))


@pytest.mark.parametrize("damage", ["missing", "bytes", "pin", "origin", "repository", "subject", "size"])
def test_installed_runner_rejects_unstaged_changed_or_wrong_subject_size_profile(damage):
    changes = {"origin": {"origin": "https://other.example"}, "repository": {"repository": "ironbank/other"},
        "subject": {"subject_digest": "sha256:" + "c" * 64},
        "size": {"descriptors": [{"digest": "sha256:" + "b" * 64, "size_bytes": 8388609}]}}
    selected, facts, build = staged_size_selection(**changes.get(damage, {}))
    if damage == "missing":
        selected["entries"].pop("/inputs/sizes.json")
    elif damage == "bytes":
        selected["entries"]["/inputs/sizes.json"]["content"] += b" "
    elif damage == "pin":
        build["attestation_size_policy"]["sha256"] = "c" * 64
        update_staged_build(selected, build)
    with pytest.raises(StateError):
        service._admit_external_dependencies(selected, facts)


def configured_size_install(tmp_path, monkeypatch):
    stack, context, _, _ = configured_install(tmp_path, monkeypatch)
    selected, _, _ = staged_size_selection()
    bootstrap = json.loads(stack.recipe_path.read_bytes())
    refs = {}
    for name, origin in (("sizes", "/inputs/sizes.json"), ("image", "/inputs/image.json")):
        path = tmp_path / (name + "-policy.json")
        path.write_bytes(selected["entries"][origin]["content"])
        ref = {"path": str(path), "sha256": _sha(path.read_bytes())}
        refs[name] = ref
        bootstrap["services"]["runner_materials"].append({"target": "/opt/devsecops/runner-inputs/" + name + ".json",
            "file": ref, "private": False})
    stack.recipe_path.write_bytes(recipe.canonical(bootstrap))
    stack.recipe_sha256 = _sha(stack.recipe_path.read_bytes())
    invocation_path = stack.driver.auxiliary_sources["mission_git_invocation"][0]
    invocation = json.loads(invocation_path.read_bytes())
    build_path = Path(invocation["configurations"]["build"]["path"])
    build = json.loads(build_path.read_bytes())
    build["attestation_size_policy"] = refs["sizes"]
    build["image"] = {"policy": refs["image"]["path"], "policy_sha256": refs["image"]["sha256"]}
    build_path.write_bytes(recipe.canonical(build))
    invocation["configurations"]["build"]["sha256"] = _sha(build_path.read_bytes())
    invocation_path.write_bytes(recipe.canonical(invocation))
    stack.driver.auxiliary_sources["mission_git_invocation"] = (invocation_path, _sha(invocation_path.read_bytes()))
    return stack, context, refs


def test_install_preserves_size_policy_and_image_identity_before_guest_mapping(tmp_path, monkeypatch):
    stack, context, refs = configured_size_install(tmp_path, monkeypatch)
    plan = runner_install.prepare_runner_install(stack, context)
    selected = runner_install.require_runner_install(plan, stack, context)
    root = recipe.guest_paths(context.run_id)["inputs"]
    build = json.loads(selected["entries"][root + "/build.json"]["content"])
    assert build["attestation_size_policy"] == {"path": root + "/sizes.json", "sha256": refs["sizes"]["sha256"]}
    assert selected["entries"][root + "/sizes.json"]["content"] == Path(refs["sizes"]["path"]).read_bytes()
    Path(refs["sizes"]["path"]).write_bytes(b"changed")
    with pytest.raises(StateError, match="changed"):
        runner_install.require_runner_install(plan, stack, context)


@pytest.mark.parametrize("missing", ["sizes", "image"])
def test_install_rejects_size_policy_without_explicit_original_materials(tmp_path, monkeypatch, missing):
    stack, context, refs = configured_size_install(tmp_path, monkeypatch)
    bootstrap = json.loads(stack.recipe_path.read_bytes())
    bootstrap["services"]["runner_materials"] = [row for row in bootstrap["services"]["runner_materials"]
        if row["file"]["path"] != refs[missing]["path"]]
    stack.recipe_path.write_bytes(recipe.canonical(bootstrap))
    stack.recipe_sha256 = _sha(stack.recipe_path.read_bytes())
    with pytest.raises(StateError, match="explicitly staged"):
        runner_install.prepare_runner_install(stack, context)


@pytest.mark.parametrize("damage", [None, "bytes", "subject"])
def test_size_profile_is_semantically_bound_before_vm_dispatch(tmp_path, damage):
    value, sources, build, build_path = dispatch_inputs(tmp_path)
    raw = size_policy_bytes(**({"subject_digest": "sha256:" + "c" * 64} if damage == "subject" else {}))
    path = tmp_path / "sizes.json"
    path.write_bytes(raw)
    build["attestation_size_policy"] = {"path": str(path), "sha256": _sha(raw)}
    build_path.write_bytes(recipe.canonical(build))
    invocation_path = sources["mission_git_invocation"][0]
    invocation = json.loads(invocation_path.read_bytes())
    invocation["configurations"]["build"]["sha256"] = _sha(build_path.read_bytes())
    invocation_path.write_bytes(recipe.canonical(invocation))
    sources["mission_git_invocation"] = (invocation_path, _sha(invocation_path.read_bytes()))
    if damage == "bytes":
        path.write_bytes(raw + b" ")
    if damage is None:
        assert owner._admit_blob_download_policy(value, sources) is None
    else:
        with pytest.raises(StateError, match="Attestation policy"):
            owner._admit_blob_download_policy(value, sources)
