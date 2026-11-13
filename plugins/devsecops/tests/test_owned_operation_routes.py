"""Caller forwarding only; original route admission is tested by client owners."""

from devsecops.factory import ci_publication as module
from devsecops.factory.gitlab_api import GitLabSession
from test_ci_publication import case as case
from test_owned_ci import wire as wire
from test_runtime_image import runtime_case as runtime_case, history_responses


def routed_reader(monkeypatch, expected):
    original = module.EvidenceReader
    observed = []

    def reader(selected, **kwargs):
        assert kwargs.pop("service_route") is expected
        observed.append(selected["origin"])
        return original(selected, **kwargs)

    monkeypatch.setattr(module, "EvidenceReader", reader)
    return observed


def test_initial_registry_verifier_uses_only_explicit_original_registry_route(case, monkeypatch):  # noqa: F811 - pytest injects the registered fixture
    route = object()
    observed = routed_reader(monkeypatch, route)
    cap = module.verify_ci_publication(**case["args"], service_route=route)
    assert cap.describe()["origin"] == observed[0]


def test_late_runtime_proof_uses_distinct_current_gitlab_and_registry_routes(runtime_case, wire, monkeypatch):  # noqa: F811 - pytest injects the registered fixture
    c = runtime_case
    registry_route, gitlab_route = object(), object()
    observed = routed_reader(monkeypatch, registry_route)
    original = GitLabSession.bounded_read_session
    reads = []

    def clone(session, **kwargs):
        assert kwargs == {"service_route": gitlab_route}
        reads.append(session)
        return original(session)

    monkeypatch.setattr(GitLabSession, "bounded_read_session", clone)
    history_responses(wire, c["base"]["args"]["selector_raw"])
    cap = module.refresh_runtime_image(
        **c["args"], service_route=registry_route, gitlab_service_route=gitlab_route
    )
    assert len(reads) == 1 and reads[0] is c["base"]["flow"].session
    assert cap.describe()["origin"] == observed[0]


def test_history_read_cannot_reset_overall_runtime_verification_budget(runtime_case, wire, monkeypatch):  # noqa: F811 - pytest injects the registered fixture
    from types import SimpleNamespace
    import time

    c = runtime_case
    clock = [time.monotonic()]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    original_history = module.refresh_ci_history

    def history(*args, **kwargs):
        result = original_history(*args, **kwargs)
        clock[0] += 3
        return result

    monkeypatch.setattr(module, "refresh_ci_history", history)
    original_verify = module._verify_publication
    budgets = []

    def verify(**kwargs):
        budgets.append(kwargs["timeout"])
        return original_verify(**kwargs)

    monkeypatch.setattr(module, "_verify_publication", verify)
    history_responses(wire, c["base"]["args"]["selector_raw"])
    cap = module.refresh_runtime_image(**c["args"])
    assert budgets == [177]
    assert cap.describe()["scope"] == "runtime-image-identity-only"
