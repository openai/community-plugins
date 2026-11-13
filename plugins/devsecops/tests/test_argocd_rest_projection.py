"""Argo CD 3.5.6 REST projection semantics, exercised over real local TLS.

The live typed REST response has metadata/spec/status and omits both Kubernetes
TypeMeta fields. These controlled payloads preserve that observed wire shape;
no private response, UID, repository, credentials, or host is shipped.
"""
from copy import deepcopy
from urllib.parse import parse_qs, urlsplit

import pytest

from devsecops.adapters.argocd import _application_shape
from test_argocd import SHA, app, ca, codes, collect, server

__all__ = ["ca", "server"]


def rest_application():
    value = app()
    del value["apiVersion"]
    del value["kind"]
    return value


def test_real_tls_accepts_typed_rest_projection_without_inventing_typemeta(server):
    value = rest_application()
    original = deepcopy(value)
    result = collect(server, value, version="v3.5.6+f5d92a9")
    assert result["decision"] == "pass"
    assert result["execution_status"] == "complete"
    assert result["observations"]["application"]["uid"] == value["metadata"]["uid"]
    assert result["observations"]["sources"][0]["observed_revision"] == SHA
    assert all(not gap["required"] for gap in result["gaps"])
    assert value == original and set(value) == {"metadata", "spec", "status"}
    assert len(server["requests"]) == 3
    for request in server["requests"][1:]:
        route = urlsplit(request["path"])
        assert route.path == "/api/v1/applications/mission"
        assert parse_qs(route.query) == {"project": ["demo"], "appNamespace": ["argocd"]}


@pytest.mark.parametrize("metadata", [
    {"apiVersion": "argoproj.io/v1alpha1"},
    {"kind": "Application"},
    {"apiVersion": None, "kind": None},
    {"apiVersion": "", "kind": ""},
    {"apiVersion": "argoproj.io/v1beta1", "kind": "Application"},
    {"apiVersion": "argoproj.io/v1alpha1", "kind": "ApplicationSet"},
])
def test_present_or_partial_typemeta_is_still_strict(server, metadata):
    value = rest_application() | metadata
    result = collect(server, value)
    assert result["decision"] == "unknown"
    assert "application-shape" in codes(result)


@pytest.mark.parametrize(("mutation", "gap"), [
    ("namespace", "scope-mismatch"),
    ("name", "scope-mismatch"),
    ("project", "scope-mismatch"),
    ("uid", "identity-incomplete"),
    ("revision", "selected-revision-mismatch"),
    ("stale", "stale-or-unavailable"),
    ("comparison", "comparison-inputs-mismatch"),
    ("race", "identity-race"),
])
def test_rest_projection_preserves_all_binding_checks(server, mutation, gap):
    value = rest_application()
    if mutation in ("namespace", "name"):
        value["metadata"][mutation] = "other"
    elif mutation == "project":
        value["spec"]["project"] = "other"
    elif mutation == "uid":
        del value["metadata"]["uid"]
    elif mutation == "revision":
        value["status"]["sync"]["revision"] = "b" * 40
    elif mutation == "stale":
        value["status"]["reconciledAt"] = "2026-10-25T11:59:30Z"
    elif mutation == "comparison":
        value["status"]["sync"]["comparedTo"]["destination"]["namespace"] = "other"
    after = deepcopy(value)
    if mutation == "race":
        after["metadata"]["uid"] = "different-uid"
    result = collect(server, value, after)
    assert result["decision"] == "unknown"
    assert gap in codes(result)


def test_rest_projection_does_not_qualify_an_unsupported_server(server):
    result = collect(server, rest_application(), version="v3.6.0")
    assert result["decision"] == "unknown"
    assert "unsupported-version" in codes(result)


def test_omitted_typemeta_requires_the_explicit_typed_rest_context():
    assert not _application_shape(rest_application())
    assert _application_shape(rest_application(), typed_rest=True)
    assert _application_shape(app())
    assert not _application_shape([], typed_rest=True)
