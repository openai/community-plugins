#!/usr/bin/env python3
"""Explicit API-only owned-CI recipe output; selectors grant no authority."""

from pathlib import Path
import json
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(1, str(ROOT / "scripts"))
# Package-local imports require the explicit script/source paths above.
from publish_and_prepare_mission_git import parser  # noqa: E402
from devsecops.factory.mission_git_invocation import load_invocation  # noqa: E402
from devsecops.factory.runner_package import installed_publication_package as _installed  # noqa: E402
from devsecops.factory.mission_operation import _configuration  # noqa: E402
from devsecops.factory.mission_publication_operation import qualify_sign_and_publish  # noqa: E402
from devsecops.factory.mission_publication import PublishedMission, require_published_mission  # noqa: E402
from devsecops.factory.state import _read, _sha  # noqa: E402
from devsecops.runtime.context import canonical_bytes, verify_seal  # noqa: E402


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        selected = load_invocation(args.invocation, args.invocation_sha256)
        timeout = min(selected["timeout"], 3000)
        deadline = time.monotonic() + timeout
        _installed(
            selected["run_scope"]["package_sha256"],
            selected["plugin_id"],
            selected["plugin_version"],
            deadline,
        )
        source = args.expected_source_repository
        revision = args.expected_source_revision
        if (
            not source.is_absolute()
            or ".." in source.parts
            or not isinstance(revision, str)
            or len(revision) != 40
            or any(char not in "0123456789abcdef" for char in revision)
        ):
            raise ValueError("Exact CI checkout and source SHA required")
        names = ("build", "staging", "security", "signing", "publication")
        with tempfile.TemporaryDirectory(prefix="owned-ci-publication-inputs-") as directory:
            paths = {}
            pins = {}
            for name in names:
                ref = selected["configurations"][name]
                raw = _read(Path(ref["path"]), 64000)
                if _sha(raw) != ref["sha256"]:
                    raise ValueError("Approved CI component template changed")
                if name == "build":
                    build = _configuration(raw)
                    if build["run_scope"] != selected["run_scope"]:
                        raise ValueError("CI build differs from current selected run")
                    # The fixed recipe permits precisely these two fields to
                    # follow GitLab's current checkout. The build freezes the
                    # actual Git bytes; host admission authenticates this SHA
                    # through the original API pipeline and signature payload.
                    build["source"]["repository"] = str(source)
                    build["source"]["revision"] = revision
                    raw = canonical_bytes(build)
                path = Path(directory) / (name + ".json")
                path.write_bytes(raw)
                path.chmod(0o400)
                paths[name] = path
                pins[name] = _sha(raw)
            report, published = qualify_sign_and_publish(
                **{name + "_config": paths[name] for name in names},
                **{name + "_config_sha256": pins[name] for name in names},
                expected_source_repository=source,
                expected_source_revision=revision,
                output=Path(selected["output"]),
                runtime_root=Path(selected["runtime_root"]),
                timeout=timeout,
            )
            verify_seal(report, "publication_operation_sha256")
            if report["decision"] != "pass" or type(published) is not PublishedMission:
                return 2
            bound = published.describe()
            scope = {
                name: bound[name]
                for name in (
                    "run_scope",
                    "source_commit",
                    "architecture",
                    "origin",
                    "repository",
                    "policy_sha256",
                    "trust_policy_sha256",
                    "publication_config_sha256",
                )
            }
            bound = require_published_mission(published, **scope)
            if bound["run_scope"] != selected["run_scope"] or bound["source_commit"] != revision:
                raise ValueError("Actual publication differs from selected job source")
            selector = {name: bound[name] for name in scope}
            selector.update(
                schema_version=1,
                kind="owned-ci-publication-selector",
                manifest_digest=bound["manifest_digest"],
                artifact_manifest=bound["artifact_manifest"],
            )
            _installed(
                selected["run_scope"]["package_sha256"],
                selected["plugin_id"],
                selected["plugin_version"],
                deadline,
            )
            print(json.dumps(selector, sort_keys=True))
            return 0
    except (ValueError, OSError, TypeError, KeyError, IndexError, AttributeError, UnicodeError):
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
