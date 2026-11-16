"""Copy explicitly selected retained inputs into a new private transfer directory.

This performs no acquisition, credential lookup, exception approval or execution.
The operator must supply the genuine approved exception and official retained inputs.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    roles = {
        "base": "base.oci.tar",
        "syft": "syft",
        "grype": "grype",
        "wheel": "prometheus_client-0.26.0-py3-none-any.whl",
        "database": "database.tar.zst",
        "listing": "latest.json",
        "exception": "exception.json",
    }
    for role in roles:
        parser.add_argument("--" + role, type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    if args.output.exists() or args.output.is_symlink():
        raise ValueError("Output must be new")
    for role in roles:
        p = getattr(args, role)
        if p.is_symlink() or not p.is_file():
            raise ValueError("Select a regular retained file: " + role)
    args.output.mkdir(parents=True, mode=0o700)
    manifest = {"schema_version": 1, "files": {}}
    for role, name in roles.items():
        destination = args.output / name
        shutil.copyfile(getattr(args, role), destination)
        destination.chmod(0o600)
        digest = hashlib.sha256()
        with destination.open("rb") as f:
            while chunk := f.read(1024 * 1024):
                digest.update(chunk)
        manifest["files"][name] = {"bytes": destination.stat().st_size, "sha256": digest.hexdigest()}
    (args.output / "input-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("Prepared7 explicitly selected retained files; no approval or acquisition inferred.")


if __name__ == "__main__":
    main()
