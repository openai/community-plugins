from copy import deepcopy
import json

import pytest

from devsecops.checks import compare_and_check
from devsecops.resources import inventory


DIGEST = "registry.example/api@sha256:" + "a" * 64


def pod(name="api", **spec):
    return {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": name, "labels": {"app": "api"}}, "spec": {"containers": [{"name": "app", "image": DIGEST}], **spec}}


def deployment():
    return {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "api"}, "spec": {"selector": {"matchLabels": {"app": "api"}}, "template": {"metadata": {"labels": {"app": "api"}}, "spec": pod()["spec"]}}}


def service(**spec):
    return {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "api"}, "spec": {"selector": {"app": "api"}, "ports": [{"port": 80}], **spec}}


def network_policy(**spec):
    return {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": {"name": "api"}, "spec": {"podSelector": {"matchLabels": {"app": "api"}}, "policyTypes": ["Ingress"], **spec}}


def role(**rule):
    return {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role", "metadata": {"name": "reader"}, "rules": [{"apiGroups": [""], "resources": ["pods"], "verbs": ["get"], **rule}]}


def binding():
    return {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding", "metadata": {"name": "reader"}, "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "reader"}, "subjects": [{"kind": "ServiceAccount", "name": "api", "namespace": "default"}]}


def records(bodies):
    result, gaps = inventory("\n---\n".join(json.dumps(body) for body in bodies), source="fixture.yaml")
    assert not gaps
    return result


def review(base, head, **policy):
    return compare_and_check(records(base), records(head), policy)


def findings(result, rule):
    return [finding for finding in result["findings"] if finding["rule_id"] == rule]


def test_harmless_mapping_order_does_not_change_resource():
    first = pod()
    reordered = {key: first[key] for key in reversed(first)}
    result = review([first], [reordered])
    assert not result["changes"]
    assert not result["findings"]


def test_inventory_added_modified_removed_are_explicit_and_value_free():
    first = pod()
    changed = deepcopy(first)
    changed["spec"]["containers"][0]["args"] = ["SENSITIVE_SENTINEL"]
    result = review([first, pod("removed")], [changed, pod("added")])
    assert {change["change"] for change in result["changes"]} == {"added", "modified", "removed"}
    assert "SENSITIVE_SENTINEL" not in json.dumps(result)
    assert "/spec/containers/0/args" in next(change for change in result["changes"] if change["change"] == "modified")["fields"]


def test_boolean_and_integer_are_not_equal_in_structural_comparison():
    before, after = pod(), pod()
    before["spec"]["example"] = True
    after["spec"]["example"] = 1
    assert review([before], [after])["changes"][0]["fields"] == ["/spec/example"]


def test_privilege_introduced_preexisting_resolved_and_evidence():
    before, after = pod(), pod()
    after["spec"]["containers"][0]["securityContext"] = {"privileged": True}
    added = findings(review([before], [after]), "K8S-PRIVILEGED")[0]
    assert (added["severity"], added["change"]) == ("high", "introduced")
    assert added["evidence"] == {"source": "fixture.yaml", "pointer": "/spec/containers/0/securityContext/privileged", "document": 0, "side": "head"}
    assert findings(review([after], [after]), "K8S-PRIVILEGED")[0]["change"] == "preexisting"
    assert findings(review([after], [before]), "K8S-PRIVILEGED")[0]["change"] == "resolved"


def test_container_reordering_does_not_invent_new_privileged_access():
    before = pod()
    before["spec"]["containers"].append({"name": "sidecar", "image": DIGEST, "securityContext": {"privileged": True}})
    after = deepcopy(before)
    after["spec"]["containers"].reverse()
    items = findings(review([before], [after]), "K8S-PRIVILEGED")
    assert len(items) == 1 and items[0]["change"] == "preexisting"
    assert items[0]["field"] == "/spec/containers/0/securityContext/privileged"


def test_explicit_escalation_control_removal_is_worsening():
    before, after = pod(), pod()
    before["spec"]["containers"][0]["securityContext"] = {"allowPrivilegeEscalation": False}
    item = findings(review([before], [after]), "K8S-ESCALATION")[0]
    assert item["change"] == "worsened" and item["severity"] == "high"


def test_removing_redundant_container_nonroot_control_keeps_inherited_control():
    before = pod(securityContext={"runAsNonRoot": True})
    before["spec"]["containers"][0]["securityContext"] = {"runAsNonRoot": True}
    after = deepcopy(before)
    after["spec"]["containers"][0].pop("securityContext")
    assert not findings(review([before], [after]), "K8S-NONROOT-WEAKENING")


def test_removing_pod_nonroot_control_is_detected_per_affected_container():
    before, after = pod(securityContext={"runAsNonRoot": True}), pod()
    item = findings(review([before], [after]), "K8S-NONROOT-WEAKENING")[0]
    assert item["change"] == "worsened"
    assert item["evidence"]["side"] == "base"
    assert item["evidence"]["pointer"] == "/spec/securityContext/runAsNonRoot"


def test_host_access_and_hostpath_are_detected_without_exposing_paths():
    after = pod(hostPID=True, volumes=[{"name": "host", "hostPath": {"path": "/SENSITIVE_SENTINEL"}}])
    result = review([pod()], [after])
    assert findings(result, "K8S-HOST-ACCESS")
    assert findings(result, "K8S-HOSTPATH")
    assert "SENSITIVE_SENTINEL" not in json.dumps(result)


def test_nonroot_container_override_does_not_report_pod_root_setting():
    body = pod(securityContext={"runAsUser": 0})
    body["spec"]["containers"][0]["securityContext"] = {"runAsUser": 1000}
    assert not findings(review([], [body]), "K8S-ROOT-USER")


def test_removed_inherited_seccomp_points_to_real_baseline_evidence():
    before, after = pod(securityContext={"seccompProfile": {"type": "RuntimeDefault"}}), pod()
    item = findings(review([before], [after]), "K8S-SECCOMP-WEAKENING")[0]
    assert item["change"] == "worsened"
    assert item["evidence"]["side"] == "base"
    assert item["evidence"]["pointer"] == "/spec/securityContext/seccompProfile"


@pytest.mark.parametrize("selector,labels,invalid", [
    ({"matchLabels": {"app": "wrong"}}, {"app": "api"}, True),
    ({"matchExpressions": [{"key": "tier", "operator": "NotIn", "values": ["bad"]}]}, {"app": "api"}, False),
    ({"matchExpressions": [{"key": "app", "operator": "In", "values": ["worker"]}]}, {"app": "api"}, True),
    ({"matchExpressions": [{"key": "app", "operator": "Exists"}]}, {"app": "api"}, False),
    ({"matchExpressions": [{"key": "tier", "operator": "DoesNotExist"}]}, {"app": "api"}, False),
])
def test_deployment_selector_semantics(selector, labels, invalid):
    body = deployment()
    body["spec"]["selector"] = selector
    body["spec"]["template"]["metadata"]["labels"] = labels
    assert bool(findings(review([], [body]), "K8S-DEPLOYMENT-SELECTOR")) is invalid


def test_matching_selector_change_still_requires_replacement_review():
    before, after = deployment(), deployment()
    after["spec"]["selector"]["matchLabels"]["app"] = "worker"
    after["spec"]["template"]["metadata"]["labels"]["app"] = "worker"
    result = review([before], [after])
    assert findings(result, "K8S-IMMUTABLE-SELECTOR")[0]["severity"] == "high"
    assert not findings(result, "K8S-DEPLOYMENT-SELECTOR")


def test_service_match_considers_unchanged_dependencies_and_namespace():
    changed = service(selector={"app": "missing"})
    result = review([deployment(), service()], [deployment(), changed])
    item = findings(result, "K8S-SERVICE-SELECTOR")[0]
    assert item["severity"] == "medium" and "supplied" in item["message"]
    other_ns = pod()
    other_ns["metadata"]["namespace"] = "other"
    assert findings(review([], [other_ns, service()]), "K8S-SERVICE-SELECTOR")
    assert not findings(review([], [pod(), service()]), "K8S-SERVICE-SELECTOR")


@pytest.mark.parametrize("body", [service(selector={}), service(selector=None), service(type="ExternalName", externalName="example.org")])
def test_valid_selectorless_or_externalname_services_are_not_defects(body):
    assert not findings(review([], [body]), "K8S-SERVICE-SELECTOR")


def test_loadbalancer_finding_does_not_claim_public_reachability():
    body = service(type="LoadBalancer")
    body["metadata"]["annotations"] = {"provider.example/internal": "true"}
    item = findings(review([service()], [body]), "K8S-SERVICE-EXPOSURE")[0]
    assert item["change"] == "introduced"
    assert "public reachability is not established" in item["message"]


def test_rbac_wildcards_and_sensitive_permissions():
    assert findings(review([], [role(verbs=["*"])]), "K8S-RBAC-WILDCARD")
    assert findings(review([], [role(resources=["secrets"], verbs=["list"])]), "K8S-RBAC-SECRETS")
    assert findings(review([], [role(apiGroups=["rbac.authorization.k8s.io"], resources=["roles"], verbs=["escalate"])]), "K8S-RBAC-DELEGATION")


def test_rbac_resource_name_asterisk_is_not_an_authorization_wildcard():
    result = review([], [role(resourceNames=["*"])])
    assert not findings(result, "K8S-RBAC-WILDCARD")


def test_rbac_subresource_wildcard_is_broad_access():
    assert findings(review([], [role(resources=["*/scale"])]), "K8S-RBAC-WILDCARD")


def test_impersonation_checks_only_identity_resources_and_token_create():
    assert findings(review([], [role(resources=["users"], verbs=["impersonate"])]), "K8S-RBAC-IMPERSONATE")
    assert not findings(review([], [role(resources=["pods"], verbs=["impersonate"])]), "K8S-RBAC-IMPERSONATE")
    assert findings(review([], [role(resources=["serviceaccounts/token"], verbs=["create"])]), "K8S-RBAC-TOKEN")


def test_rbac_rule_reordering_does_not_invent_new_permissions():
    before = role(verbs=["*"])
    before["rules"].append({"apiGroups": ["apps"], "resources": ["deployments"], "verbs": ["get"]})
    after = deepcopy(before)
    after["rules"].reverse()
    item = findings(review([before], [after]), "K8S-RBAC-WILDCARD")
    assert len(item) == 1 and item[0]["change"] == "preexisting"


def test_binding_role_ref_mutation_and_subject_reordering():
    before = binding()
    before["subjects"].append({"kind": "User", "name": "engineer", "apiGroup": "rbac.authorization.k8s.io"})
    after = deepcopy(before)
    after["subjects"].reverse()
    assert not findings(review([before], [after]), "K8S-RBAC-BINDING-CHANGE")
    after["roleRef"]["name"] = "another-role"
    assert findings(review([before], [after]), "K8S-IMMUTABLE-ROLE-REF")[0]["severity"] == "high"


def test_added_cluster_admin_binding_is_high_without_effective_auth_claim():
    body = binding()
    body["roleRef"].update(kind="ClusterRole", name="cluster-admin")
    item = findings(review([], [body]), "K8S-RBAC-CLUSTER-ADMIN")[0]
    assert item["severity"] == "high"
    assert "actual role definition still matter" in item["message"]


def test_new_subject_to_existing_sensitive_role_is_new_broad_binding():
    sensitive_role = role(verbs=["*"])
    old_binding = binding()
    new_binding = deepcopy(old_binding)
    new_binding["subjects"].append({"kind": "ServiceAccount", "name": "new-app", "namespace": "default"})
    result = review([sensitive_role, old_binding], [sensitive_role, new_binding])
    related = findings(result, "K8S-RBAC-SENSITIVE-BINDING")
    assert {item["change"] for item in related} == {"preexisting", "introduced"}
    assert all(item["severity"] == "high" for item in related)
    assert all(item["evidence"]["related"]["pointer"] == "/rules" for item in related)
    assert findings(result, "K8S-RBAC-WILDCARD")[0]["change"] == "preexisting"


def test_binding_does_not_resolve_namespaced_role_from_another_namespace():
    sensitive_role = role(verbs=["*"])
    sensitive_role["metadata"]["namespace"] = "other"
    result = review([], [sensitive_role, binding()])
    assert not findings(result, "K8S-RBAC-SENSITIVE-BINDING")
    assert any(gap["code"] == "rbac_referenced_role_unavailable" for gap in result["gaps"])


def test_clusterrole_aggregation_is_explicitly_unvalidated():
    body = role()
    body["kind"] = "ClusterRole"
    body["aggregationRule"] = {"clusterRoleSelectors": [{"matchLabels": {"rbac.example/aggregate": "true"}}]}
    result = review([], [body])
    assert any(gap["code"] == "rbac_aggregation_unvalidated" and gap["required"] is False for gap in result["gaps"])


@pytest.mark.parametrize("rule,unrestricted", [({}, True), ({"from": [{}]}, True), ({"from": []}, True), ({"from": [{"namespaceSelector": {}}]}, False), ({"from": [{"podSelector": {}}]}, False), ({"ports": [{"port": 443}]}, False), ({"from": [{"ipBlock": {"cidr": "0.0.0.0/0", "except": ["10.0.0.0/8"]}}]}, False)])
def test_network_policy_all_peer_semantics_are_bounded(rule, unrestricted):
    result = review([], [network_policy(ingress=[rule])])
    assert bool(findings(result, "K8S-NETPOL-ALLOW-ALL")) is unrestricted


def test_policy_type_prevents_unsupported_direction_claim():
    result = review([], [network_policy(policyTypes=["Egress"], ingress=[{}], egress=[])])
    assert not findings(result, "K8S-NETPOL-ALLOW-ALL")


def test_network_policy_removal_is_declaration_change_not_connectivity_proof():
    result = review([network_policy(ingress=[])], [])
    item = findings(result, "K8S-NETPOL-CHANGE")[0]
    assert item["severity"] == "medium"
    assert "all applicable policies and enforcement" in item["message"]


@pytest.mark.parametrize("kind", ["Namespace", "PersistentVolumeClaim"])
def test_destructive_declaration_removal_does_not_assert_data_loss(kind):
    body = {"apiVersion": "v1", "kind": kind, "metadata": {"name": "data"}}
    item = findings(review([body], []), "K8S-DESTRUCTIVE-REMOVAL")[0]
    assert item["severity"] == "high" and item["change"] == "introduced"
    assert "depend on the deployment process" in item["message"]


def test_pvc_storage_class_change_requires_migration_review():
    before = {"apiVersion": "v1", "kind": "PersistentVolumeClaim", "metadata": {"name": "data"}, "spec": {"storageClassName": "standard"}}
    after = deepcopy(before)
    after["spec"]["storageClassName"] = "fast"
    assert findings(review([before], [after]), "K8S-PVC-MIGRATION")


def test_required_digest_registry_and_labels_are_exact_policy_checks():
    body = pod()
    body["spec"]["containers"][0]["image"] = "registry.example.evil/api:stable"
    result = review([], [body], require_image_digest=True, allowed_registries=["registry.example"], required_labels=["owner"])
    assert {item["rule_id"] for item in result["findings"]} >= {"POLICY-IMAGE-DIGEST", "POLICY-IMAGE-REGISTRY", "POLICY-REQUIRED-LABEL"}
    assert all(item["severity"] == "high" for item in result["findings"])
    assert "registry.example.evil" not in json.dumps(result)


def test_default_docker_registry_and_empty_present_label_are_valid():
    body = pod()
    body["metadata"]["labels"]["owner"] = ""
    body["spec"]["containers"][0]["image"] = "busybox@sha256:" + "b" * 64
    assert not review([], [body], require_image_digest=True, allowed_registries=["docker.io"], required_labels=["owner"])["findings"]


def test_mutable_reference_is_advisory_without_digest_policy():
    body = pod()
    body["spec"]["containers"][0]["image"] = "busybox:1.0"
    item = findings(review([], [body]), "K8S-IMAGE-MUTABLE")[0]
    assert item["severity"] == "low"


def test_secret_configmap_and_literal_environment_values_never_appear_in_output():
    before = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "credentials"}, "stringData": {"SENSITIVE_KEY": "SENSITIVE_OLD"}}
    after = deepcopy(before)
    after["stringData"] = {"SENSITIVE_NEW_KEY": "SENSITIVE_NEW"}
    workload = pod()
    workload["spec"]["containers"][0]["env"] = [{"name": "TOKEN", "value": "SENSITIVE_ENV"}]
    result = review([before, pod()], [after, workload])
    assert "SENSITIVE" not in json.dumps(result)
    assert next(item for item in result["changes"] if item["kind"] == "Secret")["fields"] == ["/stringData"]


def test_unsupported_kinds_have_explicit_advisory_coverage_gap():
    body = deployment()
    body["kind"] = "StatefulSet"
    result = review([], [body])
    assert result["gaps"][0]["required"] is False
    assert result["gaps"][0]["code"] == "specialized_rules_unavailable"
    assert not result["findings"]


def test_duplicate_input_records_cannot_be_misrepresented_as_deletion():
    item = records([pod()])[0]
    result = compare_and_check([item], [item, item], {})
    assert not result["changes"]
    assert not result["findings"]
    assert result["gaps"][0]["code"] == "duplicate_identity"


def test_malformed_fields_do_not_crash_static_checks():
    workload = deployment()
    workload["spec"]["template"]["metadata"]["labels"] = {"app": {"unexpected": "value"}}
    workload["spec"]["selector"] = {"matchExpressions": [{"key": "app", "operator": "In", "values": ["api"]}]}
    workload["spec"]["template"]["spec"].update(volumes="wrong", initContainers="wrong")
    result = review([], [workload])
    assert not findings(result, "K8S-DEPLOYMENT-SELECTOR")
