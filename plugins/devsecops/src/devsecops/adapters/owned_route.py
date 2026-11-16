"""Fixed client plumbing for original process-owned service links.

A route changes only the socket destination. Logical origin, Host, SNI and the
selected CA remain exact. A dictionary or proxy URL never grants routing.
"""

from pathlib import Path
import hashlib
import math
import os
import stat
import time
from urllib.parse import urlsplit

from .http import APIError


def _binding(route, origin):
    from devsecops.factory.owned_service_links import require_service_route

    try:
        selected = require_service_route(route, origin=origin)
        if (
            selected["origin"] != origin
            or selected["connect_host"] != "127.0.0.1"
            or type(selected["connect_port"]) is not int
            or not 1 <= selected["connect_port"] <= 65535
            or type(selected["deadline"]) not in (int, float)
            or not math.isfinite(selected["deadline"])
            or time.monotonic() >= selected["deadline"]
            or type(selected["read_only"]) is not bool
        ):
            raise ValueError
        return selected
    except (ValueError, TypeError, KeyError, OSError):
        raise APIError("owned-service-route-unavailable") from None


def _ca_bytes(path):
    try:
        path = Path(path)
        if not path.is_absolute() or ".." in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 2_000_000:
                raise ValueError
            raw = stream.read(2_000_001)
            after = os.fstat(stream.fileno())
            if len(raw) > 2_000_000 or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ):
                raise ValueError
        return raw
    except (ValueError, TypeError, OSError):
        raise APIError("owned-service-route-ca") from None


def bind_service_route(client, route, *, origin, ca_bundle=None, ca_sha256=None):
    """Retain only an original route and freeze its exact selected trust bytes."""
    client._service_route = route
    client._service_route_origin = origin
    client._service_route_ca_sha256 = None
    if route is None:
        return None
    selected = _binding(route, origin)
    if ca_bundle is None and ca_sha256 is None:
        raise APIError("owned-service-route-requires-pinned-ca")
    raw = _ca_bytes(selected["ca_path"] if ca_bundle is None else ca_bundle)
    actual = hashlib.sha256(raw).hexdigest()
    if actual != selected["ca_sha256"] or ca_sha256 is not None and actual != ca_sha256:
        raise APIError("owned-service-route-ca")
    client._service_route_ca_sha256 = actual
    # Re-admit after reading trust; saved bindings are never retained authority.
    audit_service_route(client)
    return raw


def audit_service_route(client):
    route = getattr(client, "_service_route", None)
    if route is None:
        return None
    selected = _binding(route, client._service_route_origin)
    if selected["ca_sha256"] != client._service_route_ca_sha256:
        raise APIError("owned-service-route-ca")
    return selected


def service_address(client, address):
    selected = audit_service_route(client)
    if selected is None:
        return address
    parsed = urlsplit(client._service_route_origin)
    if address != (parsed.hostname, parsed.port or 443):
        raise APIError("owned-service-route-origin")
    return selected["connect_host"], selected["connect_port"]


def service_deadline(client, deadline):
    selected = audit_service_route(client)
    return deadline if selected is None else min(deadline, selected["deadline"])


def service_request(client, method):
    selected = audit_service_route(client)
    if selected is not None and selected["read_only"] and method not in ("GET", "HEAD"):
        raise APIError("owned-service-route-read-only")
    return selected
