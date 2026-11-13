"""Create private bootstrap credentials for an explicitly authorized owned lab.

Generated passwords are not API tokens and do not establish third-party access.
This module has no service client and never provisions or authenticates an account.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import stat


def initialize(directory: Path, *, environment_id: str) -> dict:
    """Create once, with private permissions; return metadata, never secrets."""
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,62}", environment_id):
        raise ValueError("Environment ID must be 3–63 lowercase letters, digits or hyphens")
    directory = Path(os.path.abspath(directory))
    # Open every component relative to its verified parent. Symlinks and a
    # concurrent ancestor replacement cannot redirect creation to another tree.
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent = os.open(directory.anchor, flags)
    descriptor = None
    try:
        for part in directory.parts[1:]:
            try:
                os.mkdir(part, 0o700, dir_fd=parent)
            except FileExistsError:
                pass
            child = os.open(part, flags, dir_fd=parent)
            os.close(parent)
            parent = child
        info = os.fstat(parent)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("Credential directory must be owned by this user and have mode 0700")
        data = {
            "schema_version": 1,
            "environment_id": environment_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "account_status": "not_provisioned",
            "purpose": "Owned disposable lab bootstrap only; not Registry1 or hosted Party Bus access",
            "credentials": {
                "gitlab_bootstrap": {"username": "root", "password": secrets.token_urlsafe(48)},
                "registry_reader": {"username": "demo-reader", "password": secrets.token_urlsafe(48)},
                "registry_operator": {"username": "demo-operator", "password": secrets.token_urlsafe(48)},
            },
        }
        descriptor = os.open("credentials.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = None
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.fsync(parent)
    except FileExistsError as error:
        raise ValueError("Credentials already exist; refusing to overwrite") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)
    return {
        "execution_status": "complete",
        "environment_id": environment_id,
        "credential_file": str(directory / "credentials.json"),
        "permissions": "0600",
        "account_status": "not_provisioned",
        "credential_count": 3,
    }
