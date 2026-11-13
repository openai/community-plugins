"""Actual native reconstruction with controlled GET fixtures; no live GitLab claim."""

import base64
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
import os
import pickle
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from devsecops.adapters.http import APIError, HTTPSReadClient, ReadResponse
from devsecops.factory import deployment_commit
from devsecops.factory import git_base_possession as base
from devsecops.factory import git_transport
from devsecops.runtime.context import canonical_bytes, verify_seal
from test_deployment_commit import case, git
from test_git_destination import advertisement, selection
from test_git_transport import SCOPE, inventory, prepared_case, select_new_base
from test_argocd import ca

__all__ = ["case", "ca"]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def rewrite(state):
    state.args["config"].write_bytes(canonical_bytes(state.config))
    state.args["expected_config_sha256"] = sha(state.args["config"].read_bytes())


@pytest.fixture
def subject(case, tmp_path, monkeypatch):
    # Exercise valid empty blobs and UTF-8 names with actual native objects.
    (case["repo"] / "empty.txt").write_bytes(b"")
    (case["repo"] / "café.txt").write_bytes(b"Unicode path, binary payload\x00\xff")
    git(case["repo"], "add", "--all")
    git(case["repo"], "commit", "--quiet", "-m", "Additional controlled objects")
    select_new_base(case)
    args, _, _ = prepared_case(case)
    _, plan = git_transport.prepare_git_transport(**args)
    config = selection(args["deployment_commit"])
    state = SimpleNamespace(
        case=case,
        config=config,
        plan=plan,
        calls=[],
        clients=[],
        hook=None,
        args={
            "transport": plan,
            **{key: args[key] for key in SCOPE},
            "config": tmp_path.resolve() / "destination.json",
            "output": tmp_path.resolve() / "observed",
        },
    )
    rewrite(state)
    monkeypatch.setenv("UNIT_API_READ", "SYNTHETIC_API_TOKEN_CANARY")
    monkeypatch.setenv("UNIT_GIT_USER", "synthetic-user")
    monkeypatch.setenv("UNIT_GIT_READ", "SYNTHETIC_GIT_TOKEN_CANARY")

    def reply(path, params):
        prefix = "/api/v4/projects/37"
        commit = config["base_commit"]
        if path == prefix:
            value = {
                "id": 37,
                "http_url_to_repo": config["origin"] + "/group/deploy.git",
                "path_with_namespace": "group/deploy",
                "unselected_secret": "REMOTE_UNUSED_CANARY",
            }
        elif path == "/group/deploy.git/info/refs":
            assert params == {"service": "git-upload-pack"}
            return ReadResponse(200, advertisement(commit), base._MEDIA)
        elif path == prefix + "/repository/commits/" + commit:
            value = {
                "id": commit,
                "parent_ids": plan.describe()["closure"]["base_parents_not_imported"],
                "unselected_url": "https://do-not-follow.invalid/",
            }
        elif path == prefix + "/repository/tree":
            assert params["ref"] == commit and params["recursive"] == "false" and params["per_page"] == 100
            folder = params["path"]
            rows = git(case["repo"], "ls-tree", "-z", commit + (":" + folder if folder else "^{tree}")).split(
                b"\0"
            )
            value = []
            for row in filter(None, rows):
                details, name = row.split(b"\t", 1)
                mode, kind, oid = details.decode().split()
                name = name.decode()
                value.append(
                    {
                        "id": oid,
                        "name": name,
                        "type": kind,
                        "mode": mode,
                        "path": (folder + "/" if folder else "") + name,
                    }
                )
            start = (params["page"] - 1) * 100
            value = value[start : start + 100]
            headers = {
                "x-page": str(params["page"]),
                "x-per-page": "100",
                "x-prev-page": str(params["page"] - 1) if params["page"] > 1 else "",
                "x-next-page": str(params["page"] + 1) if len(value) == 100 else "",
            }
            return ReadResponse(200, canonical_bytes(value), "application/json", pagination=headers)
        elif path.startswith(prefix + "/repository/blobs/") and path.endswith("/raw"):
            oid = path.split("/")[-2]
            return ReadResponse(200, git(case["repo"], "cat-file", "blob", oid), "application/octet-stream")
        else:
            pytest.fail("Unselected request: " + path)
        return ReadResponse(200, canonical_bytes(value), "application/json")

    state.reply = reply

    class Client:
        def __init__(self, origin, **options):
            self.origin, self.options = origin, options
            self.kind = "api" if not state.clients else "git"
            self._authorization = lambda: None
            self._bytes = 0
            self.receipts = []
            state.clients.append(self)

        def get_bytes(self, path, params=None, *, accept):
            state.calls.append(
                {"path": path, "params": params, "authorization": self._authorization(), "kind": self.kind}
            )
            self.receipts.append({"request_index": len(self.receipts) + 1, "method": "GET"})
            response = reply(path, params)
            if len(response.body) > self._max_body or self._bytes + len(response.body) > self._max_total:
                raise APIError("body-limit")
            self._bytes += len(response.body)
            return state.hook(path, params, response) if state.hook else response

    monkeypatch.setattr(base, "HTTPSReadClient", Client)
    return state


def test_complete_native_tree_blob_closure_and_narrow_direct_observation(subject):
    before = inventory(subject.case["repo"])
    transport_before = inventory(subject.args["output"].parent / "transport")
    report, capability = base.verify_git_base_possession(**subject.args)
    assert report["decision"] == "pass", report
    assert type(capability) is base.ObservedGitBase
    verify_seal(report, "observation_sha256")
    assert report["snapshots"][0] == report["snapshots"][1]
    assert report["commit_raw_bytes_retrieved"] is False
    assert report["factory_decision"] == "unknown" and report["omission_authorized"] is False
    assert report["delivery_authority"] == "not-issued" and report["principal_identity"] == "unverified"
    expected = subject.plan.describe()["closure"]["objects"]
    assert set(report["objects"]) == set(expected) - {subject.config["base_commit"]}
    retained = (subject.args["output"] / "verified-objects.bin").read_bytes()
    for oid, row in report["objects"].items():
        raw = retained[row["offset"] : row["offset"] + row["bytes"]]
        assert sha(raw) == row["sha256"] and base._object_id(row["kind"], raw) == oid
        assert {key: row[key] for key in ("kind", "bytes", "sha256")} == expected[oid]
    assert inventory(subject.case["repo"]) == before
    assert inventory(subject.args["output"].parent / "transport") == transport_before
    assert not list(subject.args["output"].parent.glob(".git-base-*"))
    assert capability.describe()["selection"] == report["selection"]
    with pytest.raises(TypeError):
        pickle.dumps(capability)
    text = json.dumps(report)
    assert "CANARY" not in text and "do-not-follow" not in text
    assert all(b"CANARY" not in p.read_bytes() for p in subject.args["output"].iterdir())


@pytest.mark.parametrize("kind", ["dict", "unissued", "pid", "expired", "scope"])
def test_direct_transport_required_before_output_and_credentials(subject, monkeypatch, kind):
    if kind == "dict":
        subject.args["transport"] = subject.plan.describe()
    elif kind == "unissued":
        subject.args["transport"] = object.__new__(git_transport.PreparedGitTransport)
    elif kind in ("pid", "expired"):
        git_transport._PLANS[subject.plan]["pid" if kind == "pid" else "expires_monotonic"] = -1
    else:
        subject.args["run_scope"] = "different-run"
    monkeypatch.setattr(base, "_authorization", lambda *_: pytest.fail("credential read before admission"))
    with pytest.raises(ValueError):
        base.verify_git_base_possession(**subject.args)
    assert not subject.calls and not subject.args["output"].exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "project",
        "base-ref",
        "commit",
        "parents",
        "missing-tree-row",
        "duplicate",
        "mode",
        "path",
        "name",
        "blob",
        "missing-blob",
        "redirect",
        "media",
        "pagination",
        "changed-final",
    ],
)
def test_remote_mismatch_never_issues_capability(subject, mutation):
    def mutate(path, params, response):
        if mutation == "project" and path == "/api/v4/projects/37":
            return replace(response, body=canonical_bytes({"id": 9}))
        if mutation == "base-ref" and path.endswith("/info/refs"):
            return replace(response, body=advertisement("b" * 40))
        if mutation in ("commit", "parents") and "/commits/" in path:
            value = json.loads(response.body)
            value["id" if mutation == "commit" else "parent_ids"] = "a" * 40 if mutation == "commit" else []
            return replace(response, body=canonical_bytes(value))
        if mutation in (
            "missing-tree-row",
            "duplicate",
            "mode",
            "path",
            "name",
            "pagination",
        ) and path.endswith("/tree"):
            rows = json.loads(response.body)
            if mutation == "missing-tree-row":
                rows.pop()
            elif mutation == "duplicate":
                rows.append(rows[0])
            elif mutation == "mode":
                rows[0]["mode"] = "160000"
            elif mutation == "path":
                rows[0]["path"] = "../escape"
            elif mutation == "name":
                rows[0]["name"] = "bad/name"
            else:
                return replace(response, pagination={"x-next-page": "1000"})
            return replace(response, body=canonical_bytes(rows))
        if mutation in ("blob", "missing-blob") and path.endswith("/raw"):
            return (
                replace(response, body=response.body + b"tamper")
                if mutation == "blob"
                else replace(response, status=404)
            )
        if mutation == "redirect":
            return replace(response, status=302)
        if mutation == "media":
            return replace(response, media_type="text/html")
        if mutation == "changed-final" and path.endswith("/info/refs") and len(subject.calls) > 3:
            return replace(response, body=advertisement(subject.config["base_commit"], target="e" * 40))
        return response

    subject.hook = mutate
    report, capability = base.verify_git_base_possession(**subject.args)
    assert report["decision"] == "unknown" and capability is None
    assert report["gaps"] and report["omission_authorized"] is False
    assert not list(subject.args["output"].parent.glob(".git-base-*"))


def test_credentials_are_frozen_and_cleared(subject, monkeypatch):
    def change(path, params, response):
        monkeypatch.setenv("UNIT_API_READ", "REPLACEMENT_API")
        monkeypatch.setenv("UNIT_GIT_READ", "REPLACEMENT_GIT")
        return response

    subject.hook = change
    report, _ = base.verify_git_base_possession(**subject.args)
    assert report["decision"] == "pass"
    encoded = "Basic " + base64.b64encode(b"synthetic-user:SYNTHETIC_GIT_TOKEN_CANARY").decode()
    assert all(
        c["authorization"] == ("Bearer SYNTHETIC_API_TOKEN_CANARY" if c["kind"] == "api" else encoded)
        for c in subject.calls
    )
    assert all(c._authorization() is None for c in subject.clients)
    assert subject.clients[0]._deadline == subject.clients[1]._deadline


@pytest.mark.parametrize("mutation", ["source", "config", "expired", "output"])
def test_terminal_audit_withdraws_receipt_and_capability(subject, monkeypatch, mutation):
    original = base.open_transport_consumer_output

    @contextmanager
    def changed(*args, **kwargs):
        with original(*args, **kwargs) as admitted:
            yield admitted
            assert (subject.args["output"] / "git-base-possession.json").exists()
            if mutation == "source":
                p = subject.args["output"].parent / "prepared/commit.txt"
                p.chmod(0o600)
                p.write_bytes(p.read_bytes() + b"changed")
            elif mutation == "config":
                (subject.args["output"] / "git-destination-config.json").write_bytes(b"{}")
            elif mutation == "expired":
                git_transport._PLANS[subject.plan]["expires_monotonic"] = -1
            else:
                p = subject.args["output"] / "verified-objects.bin"
                p.write_bytes(p.read_bytes() + b"changed")

    monkeypatch.setattr(base, "open_transport_consumer_output", changed)
    with pytest.raises(ValueError):
        base.verify_git_base_possession(**subject.args)
    assert not (subject.args["output"] / "git-base-possession.json").exists()


@pytest.mark.parametrize("limit", ["requests", "body", "tree-count", "depth"])
def test_shared_bounds_fail_closed(subject, monkeypatch, limit):
    key, value = {
        "requests": ("MAX_REQUESTS", 3),
        "body": ("MAX_TOTAL_BYTES", 100),
        "tree-count": ("MAX_TREE_ENTRIES", 1),
        "depth": ("MAX_DEPTH", 0),
    }[limit]
    monkeypatch.setattr(base, key, value)
    report, capability = base.verify_git_base_possession(**subject.args)
    assert report["decision"] == "unknown" and capability is None and report["gaps"]
    if limit == "requests":
        assert len(subject.calls) == 3


def test_current_capability_rejects_tampered_artifacts_and_saved_report(subject):
    report, capability = base.verify_git_base_possession(**subject.args)
    kwargs = {key: subject.args[key] for key in ("transport", *SCOPE, "expected_config_sha256")}
    with pytest.raises(ValueError):
        base.require_observed_git_base(report, **kwargs)
    assert base.require_observed_git_base(capability, **kwargs)["scope"] == report["scope"]
    (subject.args["output"] / "verified-objects.bin").write_bytes(b"tampered")
    with pytest.raises(ValueError):
        capability.describe()


@pytest.mark.parametrize("mutation", ["config", "utc-expiry", "artifact", "membership", "route"])
def test_require_rechecks_authority_and_all_artifacts_after_last_read(subject, monkeypatch, mutation):
    _, capability = base.verify_git_base_possession(**subject.args)
    original = base._read_retained
    changed = []

    def late_change(directory, name, ref, deadline, **kwargs):
        result = original(directory, name, ref, deadline, **kwargs)
        if name == "git-base-possession.json" and not changed:
            changed.append(True)
            if mutation == "config":
                subject.args["config"].write_bytes(b"{}")
            elif mutation == "utc-expiry":
                prepared = git_transport._PLANS[subject.plan]["prepared"]
                deployment_commit._PREPARED[prepared]["binding"]["expires_at"] = "2000-01-01T00:00:00+00:00"
            elif mutation == "artifact":
                (subject.args["output"] / "verified-objects.bin").write_bytes(b"changed after reading")
            elif mutation == "membership":
                (subject.args["output"] / "unexpected").write_bytes(b"unselected")
            else:
                subject.args["output"].rename(subject.args["output"].with_name("moved-observation"))
                subject.args["output"].mkdir()
        return result

    monkeypatch.setattr(base, "_read_retained", late_change)
    with pytest.raises(ValueError):
        capability.describe()
    assert changed == [True]


def test_failed_scratch_cleanup_closes_held_descriptors_and_cannot_pass(subject, monkeypatch):
    descriptors = []
    open_directory, directory_at, remove = base._open_directory, base._directory_at, base.shutil.rmtree

    def opened(path):
        fd = open_directory(path)
        if path == subject.args["output"].parent:
            descriptors.append(fd)
        return fd

    def child(directory, *parts):
        fd = directory_at(directory, *parts)
        descriptors.append(fd)
        return fd

    def denied(path, *, dir_fd=None, **kwargs):
        if str(path).startswith(".git-base-"):
            raise OSError("controlled cleanup failure")
        return remove(path, dir_fd=dir_fd, **kwargs)

    monkeypatch.setattr(base, "_open_directory", opened)
    monkeypatch.setattr(base, "_directory_at", child)
    monkeypatch.setattr(base.shutil, "rmtree", denied)
    report, capability = base.verify_git_base_possession(**subject.args)
    assert report["decision"] == "unknown" and capability is None
    assert report["gaps"] == ["git-base-observation-incomplete"]
    assert len(descriptors) >= 3
    for fd in descriptors:
        with pytest.raises(OSError):
            os.fstat(fd)


def test_shared_body_allowance_is_installed_before_get(subject, monkeypatch):
    # The first project payload exceeds this allowance. The actual read client
    # fixture sees the remaining hard allowance before yielding body bytes.
    monkeypatch.setattr(base, "MAX_TOTAL_BYTES", 100)
    report, capability = base.verify_git_base_possession(**subject.args)
    assert report["gaps"] == ["body-limit"] and capability is None
    assert sum(client._bytes for client in subject.clients) == 0
    assert subject.clients[0]._max_body == subject.clients[0]._max_total == 100


@pytest.mark.parametrize("raw", [b'[],"extra":0', b"{}", b"[{},{}]", b"[] trailing"])
def test_tree_page_requires_one_actual_json_array(raw):
    with pytest.raises(APIError):
        base._tree_page(raw, "", 1, {})


def test_real_loopback_tls_private_ca_and_auth_scopes(subject, ca, monkeypatch):
    observed = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            parsed = urlsplit(self.path)
            params = {k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()} or None
            if params and "page" in params:
                params["page"], params["per_page"] = int(params["page"]), int(params["per_page"])
            expected = (
                "Basic " + base64.b64encode(b"synthetic-user:SYNTHETIC_GIT_TOKEN_CANARY").decode()
                if parsed.path.endswith("/info/refs")
                else "Bearer SYNTHETIC_API_TOKEN_CANARY"
            )
            observed.append((parsed.path, self.headers.get("Authorization") == expected))
            response = subject.reply(parsed.path, params)
            self.send_response(200)
            self.send_header("Content-Type", response.media_type)
            self.send_header("Content-Length", str(len(response.body)))
            for key, value in response.pagination.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(response.body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(ca[0]), str(ca[1]))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        subject.config["origin"] = "https://127.0.0.1:" + str(server.server_port)
        subject.config["trust"] = {"mode": "bundle", "path": str(ca[0]), "sha256": sha(ca[0].read_bytes())}
        rewrite(subject)
        monkeypatch.setattr(base, "HTTPSReadClient", HTTPSReadClient)
        report, capability = base.verify_git_base_possession(**subject.args)
        assert report["decision"] == "pass", report
        assert capability and observed and all(matched for _, matched in observed)
        assert report["transport_receipts"]["api"] and report["transport_receipts"]["git"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
