import json

import pytest

from devsecops import resources
from devsecops.resources import inventory, strict_load_yaml


@pytest.mark.parametrize("text, expected", [
    ("=\n", "="),
    ("operator: =\n", {"operator": "="}),
    ("enum:\n  - =\n  - '!='\n", {"enum": ["=", "!="]}),
])
def test_plain_equality_operator_remains_a_json_string(text, expected):
    assert strict_load_yaml(text) == [expected]


@pytest.mark.parametrize("tag", ["!!value", "!<tag:yaml.org,2002:value>"])
def test_explicit_legacy_value_tags_remain_forbidden(tag):
    with pytest.raises(ValueError, match="Custom or unsupported"):
        strict_load_yaml("operator: " + tag + " =\n")


def document(kind="ConfigMap", name="settings", api="v1", **fields):
    return {"apiVersion": api, "kind": kind, "metadata": {"name": name}, **fields}


def stream(*documents):
    return "\n---\n".join(json.dumps(item) for item in documents)


def test_standard_scalar_values_are_preserved_and_dates_are_strings():
    result = strict_load_yaml("string: text\nnumber: 4\nreal: 1.5\nflag: true\nmissing: null\ndate: 2026-10-21\n")
    assert result == [{"string": "text", "number": 4, "real": 1.5, "flag": True, "missing": None, "date": "2026-10-21"}]


@pytest.mark.parametrize("text", [
    "a: 1\na: 2", "a: {b: 1, b: 2}", "a: !execute secret", "a: &ref [1]\nb: *ref",
    "a: .inf", "a: .nan", "true: value", "a: {<<: {b: 1}}", "a: !!binary ZGF0YQ==",
])
def test_ambiguous_or_unsafe_yaml_is_rejected(text):
    with pytest.raises(ValueError):
        strict_load_yaml(text)


def test_parse_errors_never_include_input_or_source():
    secret = "SENSITIVE_SENTINEL_654321"
    with pytest.raises(ValueError) as error:
        strict_load_yaml(f"value: [{secret}", source=secret)
    assert secret not in str(error.value)
    records, gaps = inventory(f"value: [{secret}")
    assert not records
    assert gaps[0]["required"] is True
    assert secret not in json.dumps(gaps)


def test_bounded_yaml(monkeypatch):
    monkeypatch.setattr(resources, "MAX_YAML_BYTES", 10)
    with pytest.raises(ValueError, match="byte limit"):
        strict_load_yaml("large: 123456789")
    monkeypatch.setattr(resources, "MAX_YAML_BYTES", 1000)
    monkeypatch.setattr(resources, "MAX_DEPTH", 3)
    with pytest.raises(ValueError, match="nesting"):
        strict_load_yaml("a: [[[[1]]]]")
    monkeypatch.setattr(resources, "MAX_DOCUMENTS", 1)
    with pytest.raises(ValueError, match="document"):
        strict_load_yaml("a: 1\n---\na: 2")


def test_namespaces_and_cluster_scope_are_distinct():
    records, gaps = inventory(stream(document(), document("Namespace", "team")), namespace="staging", source="resources.yaml")
    assert not gaps
    assert records[0]["id"] == "core/ConfigMap/staging/settings"
    assert records[1]["id"] == "core/Namespace/<cluster>/team"
    assert records[1]["namespace"] == ""


def test_explicit_namespace_overrides_target_for_namespaced_objects():
    body = document("Pod", "api")
    body["metadata"]["namespace"] = "production"
    records, gaps = inventory(stream(body), namespace="staging")
    assert not gaps
    assert records[0]["namespace"] == "production"


def test_cluster_resource_with_namespace_is_not_silently_normalized():
    body = document("Namespace", "team")
    body["metadata"]["namespace"] = "incorrect"
    records, gaps = inventory(stream(body))
    assert records[0]["scope"] == "Cluster"
    assert any(gap["code"] == "cluster_resource_namespace" for gap in gaps)


def test_duplicate_identities_across_api_versions_are_excluded():
    first = document("Deployment", "api", "apps/v1")
    second = document("Deployment", "api", "apps/v1beta1")
    records, gaps = inventory(stream(first, second, document()))
    assert [record["kind"] for record in records] == ["ConfigMap"]
    assert len([gap for gap in gaps if gap["code"] == "duplicate_identity"]) == 1


def test_list_items_have_document_and_item_evidence():
    records, gaps = inventory(stream(document(), {"apiVersion": "v1", "kind": "List", "items": [document("Service", "api"), document("Namespace", "team")]}), source="rendered.yaml")
    assert not gaps
    assert records[1]["document_index"] == 1
    assert records[1]["source_pointer"] == "/items/0"
    assert records[1]["source"] == "rendered.yaml"


@pytest.mark.parametrize("bad", [None, "text", {"apiVersion": "apps/v1", "kind": "List", "items": []}, {"apiVersion": "v1", "kind": "List", "items": "bad"}])
def test_bad_list_items_and_list_shapes_are_visible(bad):
    records, gaps = inventory(stream({"apiVersion": "v1", "kind": "List", "items": [bad]}))
    assert not records
    assert gaps and all(gap["required"] for gap in gaps)


def test_resource_limit_fails_without_partial_inventory(monkeypatch):
    monkeypatch.setattr(resources, "MAX_RESOURCES", 1)
    records, gaps = inventory(stream(document(name="one"), document(name="two")))
    assert not records
    assert gaps[-1]["code"] == "resource_limit"


def crd(scope):
    return document("CustomResourceDefinition", "widgets.example.org", "apiextensions.k8s.io/v1", spec={"group": "example.org", "names": {"kind": "Widget", "plural": "widgets"}, "scope": scope})


@pytest.mark.parametrize("scope,expected", [("Cluster", ""), ("Namespaced", "team")])
def test_custom_resource_scope_comes_from_supplied_crd(scope, expected):
    widget = document("Widget", "item", "example.org/stable")
    records, gaps = inventory(stream(widget, crd(scope)), namespace="team")
    assert not gaps
    assert records[0]["namespace"] == expected


def test_unknown_scope_never_assumes_target_namespace():
    records, gaps = inventory(stream(document("Widget", "item", "example.org/v1")), namespace="team")
    assert records[0]["namespace"] == "?"
    assert "<unknown:" in records[0]["id"]
    assert gaps[0]["code"] == "unknown_scope"
    assert gaps[0]["required"] is True


def test_conflicting_crds_do_not_choose_one_scope():
    first, second = crd("Cluster"), crd("Namespaced")
    second["metadata"]["name"] = "other.example.org"
    records, gaps = inventory(stream(first, second, document("Widget", "item", "example.org/v1")))
    assert records[-1]["namespace"] == "?"
    assert {gap["code"] for gap in gaps} >= {"conflicting_crd_scope", "unknown_scope"}


def test_unresolved_generate_name_does_not_produce_fake_identity():
    body = {"apiVersion": "v1", "kind": "Pod", "metadata": {"generateName": "job-"}}
    records, gaps = inventory(stream(body))
    assert not records
    assert gaps[0]["code"] == "invalid_identity"


def test_empty_documents_are_not_resources():
    records, gaps = inventory("# empty\n---\n\n")
    assert records == gaps == []
