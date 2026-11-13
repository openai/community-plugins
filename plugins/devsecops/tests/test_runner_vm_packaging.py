"""Validate in-memory package membership; no archive, install or live call."""

import pytest

from scripts import build_plugin


@pytest.fixture(scope="module")
def package_members():
    files = build_plugin.package_files()
    build_plugin.validate(files)
    return files


@pytest.mark.parametrize(
    "missing",
    [
        "src/devsecops/factory/owned_runner_vm.py",
        "src/devsecops/factory/runner_vm_recipe.py",
        "src/devsecops/factory/runner_vm_remote.py",
        "src/devsecops/factory/owned_runner_service.py",
        "src/devsecops/factory/runner_service_guest.py",
        "src/devsecops/factory/runner_vm_routes.py",
        "src/devsecops/factory/runner_vm_routes_remote.py",
        "src/devsecops/factory/runner_vm_guest_routes.py",
        "schemas/owned-runner-vm.schema.json",
        "docs/interfaces/owned-runner-vm.md",
        "docs/interfaces/owned-runner-service.md",
        "docs/demo/preflight.md",
        "docs/owned-cleanup-continuation.md",
    ],
)
def test_missing_dedicated_runner_member_rejects_package(package_members, missing):
    files = dict(package_members)
    files.pop(missing)
    with pytest.raises(ValueError, match="Factory runtime|Canonical production composition"):
        build_plugin.validate(files)
