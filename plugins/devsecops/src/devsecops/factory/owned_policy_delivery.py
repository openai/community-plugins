"""Fixed canonical inject/correction/reset delivery from direct policy source."""

from copy import deepcopy
import os
from weakref import WeakKeyDictionary

from . import deployment_commit, git_transport, git_base_possession
from .git_destination import destination_configuration
from .policy_git_source import prepare_policy_source, require_policy_source
from .state import StateError, _canonical, _exclusive, _read, _sha

_PREPARED = WeakKeyDictionary()


class PreparedPolicyGit:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        raise TypeError("Policy Git inputs require actual qualified source preparation")

    def __reduce__(self):
        raise TypeError("Policy Git origins cannot be serialized")


def require_policy_git(value, *, journal):
    record = _PREPARED.get(value) if type(value) is PreparedPolicyGit else None
    if record is None or record["journal"] is not journal or record["pid"] != os.getpid():
        raise StateError("Original same-process policy Git preparation required")
    source = record["source"].describe()
    require_policy_source(
        record["source"],
        **{
            name: source[name]
            for name in (
                "run_scope",
                "source_commit",
                "origin",
                "repository",
                "manifest_digest",
                "deployment_repository",
                "deployment_commit",
                "configuration_sha256",
            )
        },
    )
    deployment_commit.require_prepared_deployment_commit(record["commit"], **record["scope"])
    git_transport.require_prepared_git_transport(record["transport"], **record["scope"])
    git_base_possession.require_observed_git_base(
        record["base"],
        transport=record["transport"],
        **record["scope"],
        expected_config_sha256=record["destination_sha256"],
    )
    for path, pin in record["inputs"].items():
        if _sha(_read(path, 64000)) != pin:
            raise StateError("Derived policy Git input changed")
    return record["transport"], record["base"], deepcopy(record["scope"]), record["destination_sha256"]


def require_policy_git_sources(value, *, journal):
    require_policy_git(value, journal=journal)
    record = _PREPARED[value]
    # The third origin is an observed predecessor, not a republished image.
    return record["source"], record["commit"], record["previous"]


def execute_policy_delivery(context, *, operations):
    """The canonical operation caller; one fixed source, receive and MR path."""
    from .owned_operations import OwnedOperationCoordinator
    from .owned_git_delivery import (
        deliver_prepared,
        merge_reviewed,
        delivery_writer_configuration,
        require_delivery,
    )
    from .canonical_bootstrap import require_bootstrap_authority

    if type(operations) is not OwnedOperationCoordinator:
        raise StateError("Concrete canonical policy coordinator required")
    operation = operations._admit(context, context.phase["operation"])
    if context.phase["id"] not in (
        "primary-inject",
        "primary-deliver",
        "reset",
        "replay-inject",
        "replay-deliver",
    ):
        raise StateError("Fixed policy delivery phase required")
    output = operations.journal.run_dir / "operations" / operation / "policy-delivery"
    output.mkdir(mode=0o700)
    source = prepare_policy_source(context, operations=operations, output=output / "source")
    binding = source.describe()
    _, _, raw = operations.selected_component("commit")
    selected = deployment_commit.commit_configuration(raw)
    selected.update(
        deployment_repository=binding["deployment_repository"],
        deployment_commit=binding["deployment_commit"],
        values_path=binding["values_path"],
        proposal_configuration_sha256=binding["configuration_sha256"],
    )
    commit_raw = _canonical(selected)
    deployment_commit.commit_configuration(commit_raw)
    commit_path = output / "commit.json"
    _exclusive(commit_path, commit_raw)
    report, commit = deployment_commit.prepare_deployment_commit(
        proposal=source,
        **{
            key: binding[key]
            for key in (
                "run_scope",
                "source_commit",
                "origin",
                "repository",
                "manifest_digest",
                "deployment_repository",
                "deployment_commit",
            )
        },
        proposal_configuration_sha256=binding["configuration_sha256"],
        config=commit_path,
        expected_config_sha256=_sha(commit_raw),
        output=output / "commit",
        timeout=min(120, context.remaining_seconds()),
    )
    if commit is None or report["decision"] != "pass":
        raise StateError("Native policy commit preparation did not qualify")
    prepared_commit_id = commit.describe()["prepared"]["commit"]
    scope = {key: binding[key] for key in ("run_scope", "source_commit", "deployment_commit")}
    scope.update(
        proposal_configuration_sha256=binding["configuration_sha256"], configuration_sha256=_sha(commit_raw)
    )
    report, transport = git_transport.prepare_git_transport(
        prepared=commit, **scope, output=output / "transport", timeout=min(120, context.remaining_seconds())
    )
    if transport is None or report["decision"] != "pass":
        raise StateError("Native complete policy transport did not qualify")
    stage, writer_origin = operations.bootstrap.writer(context)
    facts = require_bootstrap_authority(stage, operations.journal)
    project = facts["projects"]["deployment"]
    writer, writer_sha, writer_raw = operations.selected_component("owned_git_writer")
    writer_policy = delivery_writer_configuration(writer_raw)
    _, _, raw = operations.selected_component("destination")
    destination = destination_configuration(raw)
    destination.update(
        kind="owned-git-destination-observation-configuration",
        origin=facts["origin"],
        project_id=project["id"],
        repository_path="/" + project["path_with_namespace"] + ".git",
        path_with_namespace=project["path_with_namespace"],
        base_ref="refs/heads/main",
        base_commit=binding["deployment_commit"],
        target_ref=writer_policy["target_ref_prefix"] + context.run_id + "/" + context.phase["id"],
        api_read_credential={"kind": "owned-reader", "transport": "api"},
        git_read_credential={"kind": "owned-reader", "transport": "git"},
    )
    raw = _canonical(destination)
    destination_configuration(raw)
    destination_path = output / "destination.json"
    _exclusive(destination_path, raw)
    destination_sha = _sha(raw)
    reader_authority, reader = operations.bootstrap.reader(context)
    service_route = operations.bootstrap.service_route(context, 'gitlab')
    observed_report, observed = git_base_possession.verify_git_base_possession(
        transport=transport,
        **scope,
        config=destination_path,
        expected_config_sha256=destination_sha,
        output=output / "base",
        timeout=min(120, context.remaining_seconds()),
        reader_origin=reader,
        service_route=service_route,
        bootstrap_authority=reader_authority,
        journal=operations.journal,
    )
    if observed is None or observed_report["decision"] != "pass":
        raise StateError("Actual policy base possession is unverified")
    key = (context.phase["attempt"], "baseline" if context.phase["operation"] == "inject" else "fault")
    if context.phase["operation"] == "reset":
        key = ("primary", "recovery")
    value = object.__new__(PreparedPolicyGit)
    _PREPARED[value] = dict(
        journal=operations.journal,
        pid=os.getpid(),
        source=source,
        commit=commit,
        transport=transport,
        base=observed,
        scope=scope,
        destination_sha256=destination_sha,
        previous=operations.delivery(*key),
        inputs={commit_path: _sha(commit_raw), destination_path: destination_sha},
    )
    require_policy_git(value, journal=operations.journal)
    recipe = operations._recipe()
    delivered = deliver_prepared(
        context,
        journal=operations.journal,
        prepared=value,
        bootstrap_authority=stage,
        writer_origin=writer_origin,
        service_route=service_route,
        destination=destination_path,
        destination_sha256=destination_sha,
        writer=writer,
        writer_sha256=writer_sha,
        plugin_id=recipe["plugin_id"],
        plugin_version=recipe["plugin_version"],
    )
    _, owner = operations.bootstrap.gitlab(context)
    delivered = merge_reviewed(
        context,
        journal=operations.journal,
        delivery=delivered,
        session=owner,
        required_jobs=tuple(recipe["ci"]["delivery_required_jobs"]),
        poll_seconds=recipe["ci"]["poll_seconds"],
        max_observations=recipe["ci"]["max_observations"],
    )
    final = require_delivery(delivered, journal=operations.journal)
    if (
        final["delivery_stage"] != "merged-watched-branch"
        or final["watched_revision"] != prepared_commit_id
    ):
        raise StateError("Actual policy watched revision is unobserved")
    return delivered
