"""Original ephemeral read-only repository reader from owned provisioning through cleanup.

The service administrator creates this separate non-admin identity. Its secret
never appears in evidence and cannot be substituted with an owner/root token.
Unknown effects remain resident and require read-only reconciliation.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
import secrets
import threading
from urllib.parse import quote
from weakref import WeakKeyDictionary

from .cleanup_observation import ComponentObservations
from .gitlab_api import GitLabAPIError, GitLabSession
from .lifecycle import PhaseOutcome
from .state import RunJournal, StateError, _canonical, _exclusive, _read, _sha

_ORIGINS = WeakKeyDictionary()


class OwnedReaderOrigin:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        raise TypeError("Reader identity requires original owned provisioning")

    def __reduce__(self):
        raise TypeError("Reader identity cannot be restored from a report")


def require_owned_reader_origin(value, *, journal, bootstrap_authority):
    record = _ORIGINS.get(value) if type(value) is OwnedReaderOrigin else None
    if (
        record is None
        or record.journal is not journal
        or record.bootstrap is not bootstrap_authority
        or record._origin is not value
    ):
        raise StateError("Original same-run reader identity is required")
    record._audit_origin()
    return deepcopy(record._binding)


def _reader_secret(value, **scope):
    require_owned_reader_origin(value, **scope)
    return _ORIGINS[value]._secret


class OwnedGitLabReader:
    def __init__(self, journal, *, bootstrap_authority, session, expires_at, cleanup):
        from .owned_cleanup import OwnedCleanupCoordinator

        if (
            type(journal) is not RunJournal
            or type(session) is not GitLabSession
            or type(cleanup) is not OwnedCleanupCoordinator
            or cleanup.journal is not journal
        ):
            raise StateError("Reader requires original journal, session and cleanup owner")
        self.journal, self.bootstrap, self.session, self.cleanup = (
            journal,
            bootstrap_authority,
            session,
            cleanup,
        )
        self.pid, self.thread = os.getpid(), threading.get_ident()
        self._origin = self._secret = self._binding = self._up_operation = self._down_operation = None
        self._events, self._rows, self._spent = {}, {}, set()
        self._observations = ComponentObservations()
        self.policy = {
            "run_scope": dict(journal.identities),
            "environment_id": journal.state["environment_id"],
            "expires_at": expires_at,
            "scopes": ["read_api", "read_repository"],
            "access_level": 20,
        }
        self._policy_raw = _canonical(self.policy)
        self.policy_sha256 = _sha(self._policy_raw)
        self.expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if self.expires.tzinfo is None:
            raise StateError("Reader expiry must have an explicit timezone")
        self.name = "factory-reader-" + journal.state["run_id"][:32]
        self.email = self.name + "@factory.invalid"
        self.marker = "factory-owned-reader:" + journal.state["run_id"]
        self.output = journal.run_dir / ("owned-reader-" + journal.state["run_id"])
        self.output.mkdir(mode=0o700)
        info = self.output.stat()
        self._directory_identity = (info.st_dev, info.st_ino)
        self._rows = {
            kind: {
                "kind": "gitlab-reader-" + kind,
                "id": None,
                "state": "not-attempted",
                "disposition": "remove",
            }
            for kind in ("user", "membership", "token")
        }

    def __reduce__(self):
        raise TypeError("Reader ownership cannot be restored from saved documents")

    def _bound(self):
        if self.pid != os.getpid() or self.thread != threading.get_ident():
            raise StateError("Reader belongs to the original invoking process")
        if _canonical(self.policy) != self._policy_raw:
            raise StateError("Reader policy changed")
        info = self.output.stat()
        if (info.st_dev, info.st_ino) != self._directory_identity:
            raise StateError("Reader ownership directory changed")
        for path, pin in self._events.items():
            if _sha(_read(path, 64000)) != pin:
                raise StateError("Reader ownership ledger changed")

    def _owned(self, context=None):
        self._bound()
        from .canonical_bootstrap import require_bootstrap_authority

        budget = {} if context is None else {"deadline": context.deadline}
        owned = require_bootstrap_authority(self.bootstrap, self.journal, **budget)
        if self.session.origin != owned["origin"] or self.session.expected_user_id != owned["owner_user_id"]:
            raise StateError("Reader initializer differs from original owned server")
        return owned

    def _route(self, context, operation_id=None):
        owner = getattr(self.bootstrap, "_owner", None)
        if owner is not None and owner._service_links is not None:
            return {
                "service_route": owner.service_route(context, "gitlab", reconciliation_operation=operation_id)
            }
        return {}

    def _admit(self, context, action, operation_id=None):
        self._bound()
        current = self.cleanup._admit(context, action, operation_id=operation_id)
        if action == "up":
            if "provision" not in context.inputs["config"]["allowed_mutations"]:
                raise StateError("Reader provisioning is not selected")
            config_expiry = datetime.fromisoformat(
                context.inputs["config"]["expires_at"].replace("Z", "+00:00")
            )
            if datetime.now(timezone.utc) >= self.expires or self.expires > config_expiry:
                raise StateError("Reader provisioning window is invalid")
        return current

    def _event(self, name, value):
        self._bound()
        path = self.output / (name + ".json")
        _exclusive(path, _canonical(value))
        self._events[path] = _sha(_read(path, 64000))

    def _mutate(self, context, key, method, path, payload, *, expected):
        action = "up" if method == "POST" else "down"
        self._admit(context, action)
        if key in self._spent:
            raise StateError("Reader resource action was already attempted")
        self._spent.add(key)
        self._event(
            key + "-intent",
            {
                "method": method,
                "path": path,
                "payload_sha256": _sha(_canonical(payload)),
                "outcome": "unknown-until-observed",
            },
        )
        result = self.session.request(method, path, payload, expected=expected)
        self._event(key + "-response", {"observed": True})
        return result

    def _user_identity(self, value):
        row = self._rows["user"]
        if (
            type(value) is not dict
            or type(value.get("id")) is not int
            or value["id"] != row["id"]
            or not 0 < value["id"] < 2**63
            or value["id"] == self.session.expected_user_id
            or value.get("username") != self.name
            or value.get("email") != self.email
            or value.get("name") != self.marker
            or value.get("is_admin") is not False
            or value.get("state") != "active"
        ):
            raise StateError("Original reader user identity changed")

    def _token_identity(self, value, *, revoked=False):
        row = self._rows["token"]
        if (
            type(value) is not dict
            or type(value.get("id")) is not int
            or value["id"] != row["id"]
            or not 0 < value["id"] < 2**63
            or value.get("user_id") != self._rows["user"]["id"]
            or value.get("name") != self.marker
            or value.get("revoked") is not revoked
            or sorted(value.get("scopes", [])) != ["read_api", "read_repository"]
            or value.get("expires_at") != row["expires_at"]
        ):
            raise StateError("Original reader token identity changed")

    def up(self, context):
        operation = self._admit(context, "up")
        if self._up_operation is not None:
            raise StateError("Reader provisioning was already attempted")
        self._up_operation = operation
        owned = self._owned(context)
        self.session = self.session.renew_for_operation(**self._route(context))
        self._admit(context, "up")
        principal = self.session.verify_identity()["principal"]
        if principal.get("is_admin") is not True:
            raise StateError("Owned service administrator is required to create a separate reader")
        self.project_id = owned["projects"]["deployment"]["id"]
        self._rows = {
            "user": {
                "kind": "gitlab-reader-user",
                "id": None,
                "state": "not-attempted",
                "disposition": "remove",
            },
            "membership": {
                "kind": "gitlab-reader-membership",
                "id": None,
                "project_id": self.project_id,
                "state": "not-attempted",
                "disposition": "remove",
            },
            "token": {
                "kind": "gitlab-reader-token",
                "id": None,
                "state": "not-attempted",
                "disposition": "remove",
            },
        }
        self._event(
            "selection",
            {
                "run_id": context.run_id,
                "identities": dict(context.identities),
                "operation_id": operation,
                "project_id": self.project_id,
                "username": self.name,
                "email": self.email,
                "policy_sha256": self.policy_sha256,
            },
        )
        try:
            existing = self.session.get_collection("/api/v4/users?username=" + quote(self.name, safe=""))
            if existing:
                raise StateError("Existing reader username requires reconciliation")
            self._rows["user"]["state"] = "create-unknown"
            password = secrets.token_urlsafe(36)
            try:
                value = self._mutate(
                    context,
                    "create-user",
                    "POST",
                    "/api/v4/users",
                    {
                        "username": self.name,
                        "email": self.email,
                        "name": self.marker,
                        "password": password,
                        "admin": False,
                        "can_create_group": False,
                        "projects_limit": 0,
                        "skip_confirmation": True,
                    },
                    expected=(201,),
                )
            finally:
                password = None
            self._rows["user"]["id"] = value.get("id")
            self._event("user-id", {"id": value.get("id")})
            self._user_identity(value)
            self._user_identity(self.session.request("GET", "/api/v4/users/" + str(value["id"])))
            self._rows["user"]["state"] = "owned-present"
            uid = value["id"]
            self._rows["membership"].update(id=uid, state="create-unknown")
            member = self._mutate(
                context,
                "create-membership",
                "POST",
                f"/api/v4/projects/{self.project_id}/members",
                {"user_id": uid, "access_level": 20},
                expected=(201,),
            )
            if member.get("id") != uid or member.get("access_level") != 20:
                raise StateError("Reader project membership differs")
            observed = self.session.request("GET", f"/api/v4/projects/{self.project_id}/members/{uid}")
            if observed.get("id") != uid or observed.get("access_level") != 20:
                raise StateError("Reader membership readback differs")
            self._rows["membership"]["state"] = "owned-present"
            # GitLab's PAT expiry is a date. Local action-time authority remains
            # the exact shorter policy window; cleanup must revoke this PAT.
            expiry = max(
                datetime.now(timezone.utc).date() + timedelta(days=1), self.expires.date()
            ).isoformat()
            self._rows["token"].update(state="create-unknown", expires_at=expiry)
            token = self._mutate(
                context,
                "create-token",
                "POST",
                f"/api/v4/users/{uid}/personal_access_tokens",
                {"name": self.marker, "scopes": ["read_api", "read_repository"], "expires_at": expiry},
                expected=(201,),
            )
            self._rows["token"]["id"] = token.get("id")
            self._event("token-id", {"id": token.get("id"), "user_id": uid, "expires_at": expiry})
            self._token_identity(token)
            secret = token.get("token")
            if (
                not isinstance(secret, str)
                or not 1 <= len(secret) <= 8192
                or any(ord(c) < 33 or ord(c) > 126 for c in secret)
            ):
                raise StateError("Created reader secret is unavailable")
            self._token_identity(
                self.session.request("GET", "/api/v4/personal_access_tokens/" + str(token["id"]))
            )
            self._rows["token"]["state"] = "owned-present"
            self._secret = secret
            self._binding = {
                "run_id": context.run_id,
                "operation_id": operation,
                "identities": dict(context.identities),
                "environment_id": self.journal.state["environment_id"],
                "origin": owned["origin"],
                "project_id": self.project_id,
                "user_id": uid,
                "username": self.name,
                "token_id": token["id"],
                "scopes": ["read_api", "read_repository"],
                "access_level": 20,
                "policy_sha256": self.policy_sha256,
                "expires_at": self.policy["expires_at"],
                "token_expires_at": expiry,
                "path_with_namespace": owned["projects"]["deployment"]["path_with_namespace"],
            }
            self._event("reader-observed", self._binding)
            self._admit(context, "up")
            self._origin = object.__new__(OwnedReaderOrigin)
            _ORIGINS[self._origin] = self
            self._audit_origin()
            return self._outcome(context, operation, "up", False)
        except BaseException:
            self._secret = None
            self._origin = None
            raise

    def _audit_origin(self):
        self._owned()
        if (
            self._origin is None
            or self._secret is None
            or self._down_operation is not None
            or datetime.now(timezone.utc) >= self.expires
            or any(row["state"] != "owned-present" for row in self._rows.values())
        ):
            raise StateError("Reader origin is incomplete, expired or revoked")

    def origin(self):
        self._audit_origin()
        return self._origin

    def _get(self, path):
        try:
            return self.session.request("GET", path)
        except GitLabAPIError as error:
            if error.status == 404:
                return None
            raise

    def _remove(self, context, kind, *, observe_only=False):
        row = self._rows[kind]
        if row["state"] in ("absent", "not-attempted"):
            return
        if row["id"] is None or row["state"] == "create-unknown":
            return  # Never invent ownership from a matching saved name.
        uid = self._rows["user"]["id"]
        path = (
            f"/api/v4/personal_access_tokens/{row['id']}"
            if kind == "token"
            else f"/api/v4/projects/{self.project_id}/members/{uid}"
            if kind == "membership"
            else f"/api/v4/users/{uid}"
        )
        value = self._get(path)
        if value is None or kind == "token" and value.get("revoked") is True:
            if value is not None:
                self._token_identity(value, revoked=True)
            row["state"] = "absent"
            self._event(kind + "-absent", {"id": row["id"]})
            return
        if kind == "token":
            self._token_identity(value)
        elif kind == "user":
            self._user_identity(value)
        elif value.get("id") != uid or value.get("access_level") != 20:
            raise StateError("Reader membership identity changed")
        if observe_only or row["state"] == "delete-unknown":
            return
        row["state"] = "delete-unknown"
        self._mutate(
            context, "delete-" + kind, "DELETE", path, None, expected=(202, 204) if kind == "user" else (204,)
        )
        self._remove(context, kind, observe_only=True)

    def down(self, context):
        operation = self._admit(context, "down")
        if self._down_operation is not None:
            raise StateError("Reader cleanup was already attempted")
        self._down_operation = operation
        self._secret = None
        if any(row["state"] not in ("absent", "not-attempted") for row in self._rows.values()):
            self._owned(context)
            self.session = self.session.renew_for_operation(**self._route(context))
            self._admit(context, "down")
            principal = self.session.verify_identity()["principal"]
            if principal.get("is_admin") is not True:
                raise StateError("Original service administrator is required for reader cleanup")
        for kind in ("token", "membership", "user"):
            self._admit(context, "down")
            self._remove(context, kind)
            if self._rows[kind]["state"] not in ("absent", "not-attempted"):
                break
        return self._outcome(context, operation, "down", True)

    def reconcile(self, context, operation_id):
        if operation_id != self._down_operation:
            raise StateError("Original reader cleanup operation required")
        self._admit(context, "down", operation_id)
        self._owned(context)
        self.session = self.session.bounded_read_session(**self._route(context, operation_id))
        self.session.verify_identity()
        self._admit(context, "down", operation_id)
        for kind in ("token", "membership", "user"):
            self._remove(context, kind, observe_only=True)
        return self._outcome(context, operation_id, "reconcile", True)

    def _outcome(self, context, operation, action, cleaning):
        complete = cleaning and all(
            row["state"] in ("absent", "not-attempted") for row in self._rows.values()
        )
        report = {
            "run_id": context.run_id,
            "operation_id": operation,
            "identities": dict(context.identities),
            "action": action,
            "complete_cleanup": complete,
            "mutation_effect": "applied" if complete or not cleaning else "unknown",
            "unresolved_operations": [] if complete or not cleaning else [operation],
            "resources": [
                dict(row, state="absent" if row["state"] == "not-attempted" else row["state"])
                for row in self._rows.values()
            ],
        }
        name = action + "-observation-" + secrets.token_hex(8)
        self._event(name, report)
        outcome = PhaseOutcome(
            "completed" if complete or not cleaning else "incomplete",
            "owned-reader-cleanup-complete"
            if complete
            else "owned-reader-observed"
            if not cleaning
            else "owned-reader-cleanup-unresolved",
            tuple(self._events),
            mutation_effect=report["mutation_effect"],
        )
        self._observations.retain(outcome, report)
        return outcome

    def cleanup_observation(self, outcome):
        return self._observations.observe(outcome)
