"""Exercise real source-bundle loading in an isolated interpreter, without a host."""

import base64
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from devsecops.factory import owned_fresh_foundation, owned_runner_vm
from tests.test_fresh_foundation import lab as foundation_lab  # noqa: F401
from tests.test_runner_vm_remote import lab as runner_lab  # noqa: F401


@pytest.mark.parametrize("runner", [False, True])
@pytest.mark.parametrize("include_helper", [False, True])
def test_isolated_source_bundle_reaches_actual_action_admission(request, tmp_path, runner, include_helper):
    lab = request.getfixturevalue("runner_lab" if runner else "foundation_lab")
    packet = deepcopy(lab.packet)
    packet["recipe"]["acceleration"] = {"kind": "kvm", "device_group": 108}
    packet["action"] = "source-loading-probe-never-dispatch"
    owner = owned_runner_vm if runner else owned_fresh_foundation
    root = Path(owner.__file__).parent
    if not runner:
        # The in-process lifecycle fixture uses tiny synthetic asset pins.
        # Restore real vendor metadata for the unmodified isolated parser;
        # this unsupported action never reads or launches those asset paths.
        vendor = {}
        exec(compile((root / "fresh_foundation_recipe.py").read_bytes(), "vendor_recipe", "exec"), vendor)
        packet["recipe"]["boot_image"]["sha256"] = vendor["BOOT_SHA256"]
        for name, (size, digest) in vendor["ASSETS"].items():
            packet["recipe"]["rke2_assets"][name].update(bytes=size, sha256=digest)
    fields = {
        "support_source": "tcg",
        "recipe_source": "runner_vm_recipe" if runner else "fresh_foundation_recipe",
        "purge_source" if runner else "cleanup_source": "registry_remote",
        "driver_source": "runner_vm_remote" if runner else "fresh_foundation_remote",
        "acceleration_source": "qemu_acceleration",
    }
    if runner:
        fields.update(runner_source="runner_service_guest", routes_source="runner_vm_routes_remote",
                      guest_routes_source="runner_vm_guest_routes")
    expected = "Unsupported runner-vm operation" if runner else "Unsupported fresh operation"
    # The real driver must reject this unsupported action before any host I/O.
    # A test-only observer turns that exact rejection into an inspectable result;
    # source import failures or an unavailable helper cannot produce this result.
    observer = f'''
_actual_execute = execute
def execute(*args, **kwargs):
    try:
        _actual_execute(*args, **kwargs)
    except ValueError as exc:
        if str(exc) != {expected!r}:
            raise
        assert kwargs['accelerator'].docker_arguments(args[0]['recipe']['acceleration']) == [
            '--device', '/dev/kvm:/dev/kvm:rw', '--group-add', '108']
        return {{'source_loading_only': True, 'kernel_admission_run': False}}
    raise AssertionError('The unsupported action unexpectedly executed')
'''
    for field, module in fields.items():
        raw = (root / (module + ".py")).read_bytes()
        if field == "driver_source":
            raw += observer.encode()
        packet[field] = base64.b64encode(raw).decode()
    if not include_helper:
        packet.pop("acceleration_source")
    completed = subprocess.run([sys.executable, "-I", "-B", "-c", owner.BOOTSTRAP],
                               input=json.dumps(packet).encode(), capture_output=True,
                               cwd=tmp_path, env={"PATH": "/usr/bin:/bin"}, timeout=10)
    assert not completed.stderr
    result = json.loads(completed.stdout)
    if include_helper:
        assert completed.returncode == 0
        assert result == {"binding": packet["binding"], "operation_id": packet["operation_id"],
                          "observation": {"source_loading_only": True, "kernel_admission_run": False}}
    else:
        assert completed.returncode == 2 and set(result) == {"error"}
    assert not lab.fake.calls and not lab.root.exists()
