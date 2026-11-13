"""Original bounded Kubernetes reader tokens, never restored from receipts.

The control-plane identity is used only to issue a token for an independently
created, selected service account. Runtime transports receive that token through
this private same-process capability, never the bootstrap administrator key.
"""

from copy import deepcopy
from datetime import datetime, timezone
import os
import json
from pathlib import Path
from weakref import WeakKeyDictionary

from .state import StateError, _read, _sha

_READERS = WeakKeyDictionary()


class OwnedRuntimeReader:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        raise TypeError("Runtime readers require original TokenRequest issuance")

    def __reduce__(self):
        raise TypeError("Runtime readers cannot be restored from evidence")


def _issue(resources, context, *, selection, serviceaccount, rights, token, expiration, ca_path, rbac_sha256):
    from .kubernetes_operations import OwnedKubernetesResources

    if type(resources) is not OwnedKubernetesResources:
        raise StateError("Runtime reader requires the original owned Kubernetes issuer")
    resources._admit(context, write=True)
    if context.phase["operation"] != "up":
        raise StateError("Runtime reader issuance requires provisioning")
    value = object.__new__(OwnedRuntimeReader)
    binding = {
        "run_id": context.run_id,
        "identities": dict(context.identities),
        "origin": resources.session.origin,
        "cluster_uid": resources.cluster_uid,
        "ca_path": str(ca_path),
        "ca_sha256": _sha(_read(ca_path, 2_000_000)),
        "namespace": serviceaccount["metadata"]["namespace"],
        "name": serviceaccount["metadata"]["name"],
        "serviceaccount_uid": serviceaccount["metadata"]["uid"],
        "role": selection["role"],
        "effective_rbac_sha256": rbac_sha256,
        "audience": selection["audience"],
        "token_expires_at": expiration,
        "expires_at": expiration,
        "selected_resource_grants": deepcopy(rights),
    }
    _READERS[value] = {
        "issuer": resources,
        "pid": os.getpid(),
        "binding": binding,
        "secret": token,
        "secret_sha256": _sha(token.encode()),
        "selection": deepcopy(selection),
        "authority": None,
        "resources": {
            row["uid"]: _sha(json.dumps(row["manifest"], sort_keys=True).encode())
            for row in resources._resources
            if row["uid"]
            in (
                {serviceaccount["metadata"]["uid"]}
                | {right[key] for right in rights for key in ("role_uid", "binding_uid")}
            )
        },
    }
    return value


def _bind(value, *, bootstrap_authority, resources):
    record = _READERS.get(value) if type(value) is OwnedRuntimeReader else None
    if record is None or record["issuer"] is not resources or record["authority"] is not None:
        raise StateError("Runtime reader cannot be rebound to another bootstrap")
    from .canonical_bootstrap import require_bootstrap_authority

    facts = require_bootstrap_authority(bootstrap_authority, resources.journal)
    if (
        facts["kubernetes"]["cluster_uid"] != record["binding"]["cluster_uid"]
        or facts["kubernetes"]["origin"] != record["binding"]["origin"]
    ):
        raise StateError("Runtime reader differs from original bootstrap cluster")
    record["authority"] = bootstrap_authority


def _require(value, *, journal, bootstrap_authority, historical=False):
    record = _READERS.get(value) if type(value) is OwnedRuntimeReader else None
    if (
        record is None
        or record["issuer"].journal is not journal
        or record["authority"] is not bootstrap_authority
        or record["pid"] != os.getpid()
    ):
        raise StateError("Original same-run runtime reader is required")
    from .canonical_bootstrap import require_bootstrap_authority

    facts = require_bootstrap_authority(bootstrap_authority, journal)
    binding, resources = record["binding"], record["issuer"]
    if (
        facts["kubernetes"]["cluster_uid"] != binding["cluster_uid"]
        or facts["kubernetes"]["origin"] != binding["origin"]
        or dict(journal.identities) != binding["identities"]
        or journal.state["run_id"] != binding["run_id"]
        or not historical
        and datetime.now(timezone.utc)
        >= datetime.fromisoformat(binding["token_expires_at"].replace("Z", "+00:00"))
        or _sha(_read(Path(binding["ca_path"]), 2_000_000)) != binding["ca_sha256"]
    ):
        raise StateError("Runtime reader origin, trust or lifetime changed")
    matches = [
        row
        for row in resources._resources
        if row["uid"] == binding["serviceaccount_uid"]
        and (historical or row["state"] == "present")
        and row["manifest"]["kind"] == "ServiceAccount"
    ]
    if (
        len(matches) != 1
        or not record["secret"]
        or _sha(record["secret"].encode()) != record["secret_sha256"]
    ):
        raise StateError("Runtime service account was removed or credential invalidated")
    for uid, expected in record["resources"].items():
        rows = [row for row in resources._resources if row["uid"] == uid]
        if (
            len(rows) != 1
            or not historical
            and rows[0]["state"] != "present"
            or _sha(json.dumps(rows[0]["manifest"], sort_keys=True).encode()) != expected
        ):
            raise StateError("Original runtime reader RBAC identity changed")
    return deepcopy(binding)


def require_runtime_reader(value, *, journal, bootstrap_authority):
    return _require(value, journal=journal, bootstrap_authority=bootstrap_authority)


def require_runtime_reader_history(value, *, journal, bootstrap_authority):
    """Audit retained observation provenance only; never returns a token/read grant."""
    return _require(value, journal=journal, bootstrap_authority=bootstrap_authority, historical=True)


def private_token(value, **scope):
    require_runtime_reader(value, **scope)
    return _READERS[value]["secret"]
