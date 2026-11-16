"""Bounded same-secret GitLab PAT identity/role observation; no write authority."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import math
import os
from pathlib import Path
import re
import ssl
import time

from devsecops.adapters.artifacts import _open_directory
from devsecops.adapters.http import APIError, HTTPSReadClient, _json
from devsecops.adapters.vulnerability_policy import checkpoint
from devsecops.factory.git_destination import (
    MAX_CA_BYTES,
    _authorization,
    _now,
    _project,
    _read_input,
    _ref,
    _sha,
    destination_configuration,
)
from devsecops.factory.git_transport import (
    open_transport_consumer_output,
    require_prepared_git_transport,
)
from devsecops.factory.mission_git_operation import _installed
from devsecops.factory.mission_security import _read_retained
from devsecops.factory.owned_linux import _withdraw_terminal
from devsecops.runtime.cli import _verify_output_path
from devsecops.factory.state import IDENTITY_KEYS
from devsecops.runtime.context import canonical_bytes, seal

MAX_BODY = 64 * 1024
MAX_REQUESTS = 10
PROFILE = "classic-personal-access-token"
SCOPES = ("read_api", "write_repository")
_SHA = re.compile(r"[a-f0-9]{64}")
_ENV = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
_VERSION = re.compile(r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}(?:-ee)?")


def writer_configuration(raw):
    selected = _json(raw)
    fields = {
        "schema_version",
        "kind",
        "token_profile",
        "token_env",
        "expected_user_id",
        "expected_token_id",
        "expected_gitlab_version",
        "expected_project_access_level",
        "expected_scopes",
        "target_ref_prefix",
        "destination_configuration_sha256",
    }
    if (
        type(selected) is not dict
        or set(selected) != fields
        or type(selected["schema_version"]) is not int
        or selected["schema_version"] != 1
        or selected["kind"] != "mission-git-writer-observation-configuration"
        or selected["token_profile"] != PROFILE
        or type(selected["expected_project_access_level"]) is not int
        or selected["expected_project_access_level"] != 30
        or type(selected["expected_scopes"]) is not list
        or any(not isinstance(scope, str) for scope in selected["expected_scopes"])
        or sorted(selected["expected_scopes"]) != list(SCOPES)
    ):
        raise ValueError(
            "Only the explicit classic PAT read-api/write-repository Developer profile is supported"
        )
    if (
        any(
            type(selected[name]) is not int or not 1 <= selected[name] <= 2**63 - 1
            for name in ("expected_user_id", "expected_token_id")
        )
        or not isinstance(selected["token_env"], str)
        or not _ENV.fullmatch(selected["token_env"])
        or not isinstance(selected["expected_gitlab_version"], str)
        or not _VERSION.fullmatch(selected["expected_gitlab_version"])
        or not isinstance(selected["destination_configuration_sha256"], str)
        or not _SHA.fullmatch(selected["destination_configuration_sha256"])
    ):
        raise ValueError(
            "Exact independently selected principal, token, version and destination are required"
        )
    prefix = selected["target_ref_prefix"]
    if (
        not isinstance(prefix, str)
        or not prefix.startswith("refs/heads/")
        or not prefix.endswith("/")
        or not 12 < len(prefix) <= 256
    ):
        raise ValueError("A nonempty explicit review-branch prefix is required")
    _ref(prefix + "probe")
    return selected


def _future_date(value, now, *, nullable):
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise APIError("writer-expiry-unverified")
    try:
        expiry = date.fromisoformat(value)
    except ValueError:
        raise APIError("writer-expiry-unverified") from None
    # Conservative UTC whole-day profile: expiry on the current date is refused.
    if expiry <= now.date():
        raise APIError("writer-expired-or-expiring-today")
    return value


def _projection(kind, raw, selected, destination, now):
    value = _json(raw)
    if type(value) is not dict:
        raise APIError("writer-response-shape")
    if kind == "version":
        revision = value.get("revision")
        if (
            value.get("version") != selected["expected_gitlab_version"]
            or not isinstance(revision, str)
            or not re.fullmatch(r"[a-f0-9]{7,64}", revision)
        ):
            raise APIError("writer-version-mismatch")
        return {"version": value["version"], "revision": revision}
    if kind == "user":
        if (
            type(value.get("id")) is not int
            or value["id"] != selected["expected_user_id"]
            or value.get("state") != "active"
            or value.get("bot") is not False
            or ("is_admin" in value and value["is_admin"] is not False)
        ):
            raise APIError("writer-principal-mismatch")
        return {
            "id": value["id"],
            "state": "active",
            "bot": False,
            "admin_status": "reported-false" if value.get("is_admin") is False else "not-established",
        }
    if kind == "token":
        scopes = value.get("scopes")
        if (
            type(value.get("id")) is not int
            or value["id"] != selected["expected_token_id"]
            or type(value.get("user_id")) is not int
            or value["user_id"] != selected["expected_user_id"]
            or value.get("active") is not True
            or value.get("revoked") is not False
            or type(scopes) is not list
            or any(not isinstance(item, str) for item in scopes)
            or sorted(scopes) != list(SCOPES)
            or ("granular" in value and value["granular"] is not False)
        ):
            raise APIError("writer-token-mismatch")
        return {
            "id": value["id"],
            "user_id": value["user_id"],
            "active": True,
            "revoked": False,
            "scopes": list(SCOPES),
            "expires_at": _future_date(value.get("expires_at"), now, nullable=False),
        }
    if kind == "project":
        return _project(raw, destination)
    if kind == "member":
        if (
            type(value.get("id")) is not int
            or value["id"] != selected["expected_user_id"]
            or type(value.get("access_level")) is not int
            or value["access_level"] != 30
            or value.get("state") != "active"
            or value.get("membership_state", "active") != "active"
            or value.get("member_role_id") is not None
            or "expires_at" not in value
        ):
            raise APIError("writer-membership-mismatch")
        return {
            "id": value["id"],
            "access_level": 30,
            "state": "active",
            "membership_state": value.get("membership_state", "not-returned"),
            "custom_role_field": "reported-null" if "member_role_id" in value else "not-returned",
            "expires_at": _future_date(value.get("expires_at"), now, nullable=True),
        }
    raise ValueError("Unknown fixed writer observation route")


def observe_git_writer(
    *,
    transport,
    run_scope,
    source_commit,
    deployment_commit,
    proposal_configuration_sha256,
    configuration_sha256,
    plugin_id,
    plugin_version,
    destination_config,
    expected_destination_config_sha256,
    config,
    expected_config_sha256,
    output,
    timeout=60,
):
    """Return diagnostic observations only, never a saved or live write grant.

    The selected PAT is captured once and used only for these ten fixed GETs.
    A future writer must perform its own same-secret action-time admission.
    """
    if (
        type(timeout) not in (int, float)
        or not math.isfinite(timeout)
        or not 1 <= timeout <= 60
        or type(run_scope) is not dict
        or set(run_scope) != IDENTITY_KEYS
        or any(not isinstance(pin, str) or not _SHA.fullmatch(pin) for pin in run_scope.values())
        or any(
            not isinstance(pin, str) or not _SHA.fullmatch(pin)
            for pin in (expected_config_sha256, expected_destination_config_sha256)
        )
        or any(
            not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts
            for path in (config, destination_config, output)
        )
    ):
        raise ValueError(
            "Current direct transport, exact installed/run/config identities and bounded paths required"
        )
    started_utc = datetime.now(timezone.utc)
    utc_deadline, deadline = started_utc + timedelta(seconds=timeout), time.monotonic() + timeout
    scope = deepcopy(
        dict(
            run_scope=run_scope,
            source_commit=source_commit,
            deployment_commit=deployment_commit,
            proposal_configuration_sha256=proposal_configuration_sha256,
            configuration_sha256=configuration_sha256,
        )
    )
    binding = require_prepared_git_transport(transport, **scope, deadline=deadline)
    _installed(scope["run_scope"]["package_sha256"], plugin_id, plugin_version, deadline)
    raw, identity = _read_input(config, MAX_BODY, deadline)
    destination_raw, destination_identity = _read_input(destination_config, MAX_BODY, deadline)
    if _sha(raw) != expected_config_sha256 or _sha(destination_raw) != expected_destination_config_sha256:
        raise ValueError("Selected writer or destination configuration changed")
    selected, destination = writer_configuration(raw), destination_configuration(destination_raw)
    read_names = {
        value
        for key in ("api_read_credential", "git_read_credential")
        for field, value in destination[key].items()
        if field.endswith("_env")
    }
    if (
        selected["destination_configuration_sha256"] != expected_destination_config_sha256
        or binding["base_commit"] != destination["base_commit"]
        or deployment_commit != destination["base_commit"]
        or not destination["target_ref"].startswith(selected["target_ref_prefix"])
        or selected["token_env"] in read_names
    ):
        raise ValueError(
            "Writer selection differs from explicit destination, base or separate credential reference"
        )
    inputs = [
        (config, MAX_BODY, _sha(raw), identity),
        (destination_config, MAX_BODY, _sha(destination_raw), destination_identity),
    ]
    report = {
        "schema_version": 1,
        "kind": "mission-git-writer-observation",
        "decision": "unknown",
        "execution_status": "partial",
        "scope": "same-secret-api-principal-token-and-project-role-observation-only",
        "factory_decision": "unknown",
        "write_authority": "not-issued",
        "delivery_authority": "not-issued",
        "branch_permission": "unverified",
        "least_privilege": "unverified",
        "automation_authorization": "not-established",
        "credential_profile_provenance": "operator-selection-plus-response-compatibility",
        "token_profile": PROFILE,
        "configuration_sha256": expected_config_sha256,
        "destination_configuration_sha256": expected_destination_config_sha256,
        "transport_scope": deepcopy(scope),
        "new_commit": binding["new_commit"],
        "selection": {
            key: deepcopy(destination[key])
            for key in (
                "origin",
                "project_id",
                "repository_path",
                "path_with_namespace",
                "base_ref",
                "base_commit",
                "target_ref",
            )
        },
        "target_ref_prefix": selected["target_ref_prefix"],
        "snapshots": [],
        "responses": [],
        "gaps": [],
        "atomic_snapshot": False,
        "started_at": _now(),
    }
    client, authorization = None, None
    guard_fd, terminal_identity, final_error = None, None, None
    try:
        with open_transport_consumer_output(transport, **scope, output=output, deadline=deadline) as admitted:
            retained = {}

            def audit(*, context_active=True):
                checkpoint(deadline)
                now = datetime.now(timezone.utc)
                if not started_utc <= now < utc_deadline:
                    raise ValueError("Writer observation UTC window changed or expired")
                if context_active:
                    admitted.audit()
                else:
                    _verify_output_path(output, guard_fd)
                    current_terminal = os.stat(
                        "git-writer-observation.json", dir_fd=guard_fd, follow_symlinks=False
                    )
                    if (current_terminal.st_dev, current_terminal.st_ino) != terminal_identity:
                        raise ValueError("Issued writer observation terminal identity changed")
                    expected = {**retained, "git-writer-observation.json": terminal_ref}
                    if set(os.listdir(guard_fd)) != set(expected):
                        raise ValueError("Writer observation output membership changed")
                    for name, ref in expected.items():
                        _read_retained(guard_fd, name, ref, deadline)
                if require_prepared_git_transport(transport, **scope, deadline=deadline) != binding:
                    raise ValueError("Direct transport binding changed")
                for path, maximum, pin, original in inputs:
                    contents, current = _read_input(path, maximum, deadline)
                    if current != original or _sha(contents) != pin:
                        raise ValueError("Selected writer observation input changed")
                for snapshot in report["snapshots"]:
                    _future_date(snapshot["token"]["expires_at"], now, nullable=False)
                    _future_date(snapshot["member"]["expires_at"], now, nullable=True)
                _installed(scope["run_scope"]["package_sha256"], plugin_id, plugin_version, deadline)
                checkpoint(deadline)
                if not started_utc <= datetime.now(timezone.utc) < utc_deadline:
                    raise ValueError("Writer observation expired during final audit")

            def store(name, contents):
                audit()
                ref = admitted.write_bytes(name, contents)
                retained[name] = ref
                audit()
                return ref

            routes = (
                ("version", "/api/v4/version"),
                ("user", "/api/v4/user"),
                ("token", "/api/v4/personal_access_tokens/self"),
                ("project", f"/api/v4/projects/{destination['project_id']}"),
                (
                    "member",
                    f"/api/v4/projects/{destination['project_id']}/members/all/{selected['expected_user_id']}",
                ),
            )
            total = 0
            try:
                store("writer-config.json", raw)
                store("destination-config.json", destination_raw)
                context = None
                if destination["trust"]["mode"] == "bundle":
                    ca_path = Path(destination["trust"]["path"])
                    ca, original = _read_input(ca_path, MAX_CA_BYTES, deadline)
                    if _sha(ca) != destination["trust"]["sha256"]:
                        raise ValueError("Selected CA changed")
                    inputs.append((ca_path, MAX_CA_BYTES, _sha(ca), original))
                    store("ca.pem", ca)
                    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                    context.minimum_version = ssl.TLSVersion.TLSv1_2
                    context.load_verify_locations(cadata=ca.decode("ascii"))
                client = HTTPSReadClient(
                    destination["origin"],
                    timeout=min(30, timeout),
                    total_timeout=timeout,
                    max_requests=MAX_REQUESTS,
                    max_body_bytes=MAX_BODY,
                    max_total_bytes=MAX_REQUESTS * MAX_BODY,
                )
                if client.origin != destination["origin"]:
                    raise ValueError("Writer origin normalized")
                client._deadline = deadline
                if context is not None:
                    client._context = context
                audit()
                authorization = _authorization({"kind": "bearer", "token_env": selected["token_env"]})
                client._authorization = lambda: authorization
                for iteration in range(2):
                    snapshot = {}
                    for kind, path in routes:
                        audit()
                        if len(report["responses"]) >= MAX_REQUESTS:
                            raise APIError("request-limit")
                        response = client.get_bytes(path, accept=("application/json",))
                        audit()
                        if (
                            response.status != 200
                            or response.media_type != "application/json"
                            or response.pagination
                            or type(response.body) is not bytes
                            or not 0 < len(response.body) <= MAX_BODY
                        ):
                            raise APIError("writer-response-incomplete")
                        total += len(response.body)
                        if total > MAX_REQUESTS * MAX_BODY:
                            raise APIError("body-limit")
                        projection = _projection(
                            kind, response.body, selected, destination, datetime.now(timezone.utc)
                        )
                        ref = store(f"snapshot-{iteration + 1}-{kind}.json", canonical_bytes(projection))
                        report["responses"].append(
                            {
                                "index": len(report["responses"]) + 1,
                                "kind": kind,
                                "response_body_sha256": _sha(response.body),
                                "body_bytes": len(response.body),
                                "projection": ref,
                            }
                        )
                        snapshot[kind] = projection
                    if report["snapshots"] and snapshot != report["snapshots"][0]:
                        raise APIError("writer-observation-changed")
                    report["snapshots"].append(snapshot)
                    audit()
                report["decision"], report["execution_status"] = "pass", "complete"
                report["observed_body_bytes"] = total
            except APIError as error:
                report["gaps"].append(error.code)
            except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError, ssl.SSLError):
                report["gaps"].append("writer-observation-incomplete")
            finally:
                report["transport_receipts"] = client.receipts if client is not None else []
                if client is not None:
                    client._authorization = lambda: None
                authorization = None
            if report["gaps"]:
                report["decision"], report["execution_status"] = "unknown", "partial"
            report["retained"], report["finished_at"] = deepcopy(retained), _now()
            report = seal(report, "observation_sha256")
            audit()
            terminal_ref = admitted.write_bytes(
                "git-writer-observation.json", canonical_bytes(report), terminal=True
            )
            audit()
            terminal_identity = admitted.terminal_identity("git-writer-observation.json")
            guard_fd = _open_directory(output)
            observed, admitted_info = os.fstat(guard_fd), os.fstat(admitted.directory_fd)
            if (observed.st_dev, observed.st_ino) != (admitted_info.st_dev, admitted_info.st_ino):
                raise ValueError("Final observer output descriptor differs")
        # The consumer's normal exit has now completed its own audits and fsync.
        # Hold an independent descriptor only to audit and withdraw this exact
        # owned terminal on failure; never write through the borrowed descriptor.
        audit(context_active=False)
    except BaseException as error:
        final_error = error
        if guard_fd is not None and terminal_identity is not None:
            _withdraw_terminal(guard_fd, "git-writer-observation.json", terminal_identity, error)
        raise
    finally:
        if guard_fd is not None:
            try:
                os.close(guard_fd)
            except BaseException as close_error:
                if final_error is None:
                    raise
                final_error.add_note(
                    "Final observation descriptor close failed: " + type(close_error).__name__
                )
    return report
