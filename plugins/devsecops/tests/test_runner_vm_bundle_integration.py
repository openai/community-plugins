"""Actual fixed-source bundle loading, stopped before any host command."""

import base64
import io
import json
from pathlib import Path
import socket
import subprocess
import sys

from devsecops.factory import owned_runner_vm


class DispatchBoundary(BaseException):
    pass


def test_actual_bootstrap_loads_all_fixed_helpers_and_assigns_exact_context(monkeypatch):
    root = Path(owned_runner_vm.__file__).parent
    fields = {
        "support_source": "tcg",
        "recipe_source": "runner_vm_recipe",
        "purge_source": "registry_remote",
        "driver_source": "runner_vm_remote",
        "runner_source": "runner_service_guest",
        "routes_source": "runner_vm_routes_remote",
        "guest_routes_source": "runner_vm_guest_routes",
        "acceleration_source": "qemu_acceleration",
    }
    packet = {
        field: base64.b64encode((root / (name + ".py")).read_bytes()).decode()
        for field, name in fields.items()
    }
    packet.update(binding="a" * 64, operation_id="00000000-0000-4000-8000-000000000001")

    def forbidden(*_a, **_kw):
        raise AssertionError("Bundle loading must not start a process or socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(packet).encode())))
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    names = (
        "runner_vm_support",
        "runner_vm_recipe",
        "runner_vm_purge",
        "runner_vm_driver",
        "runner_service_guest",
        "runner_vm_guest_routes",
        "runner_vm_routes_remote",
        "devsecops_qemu_acceleration",
    )
    for name in names:
        monkeypatch.setitem(sys.modules, name, None)
    captured = {}
    previous = sys.gettrace()

    def trace(frame, event, arg):
        if (
            event == "call"
            and frame.f_code.co_filename == "runner_vm_driver"
            and frame.f_code.co_name == "execute"
        ):
            captured.update(frame.f_locals)
            raise DispatchBoundary
        return trace

    try:
        sys.settrace(trace)
        try:
            exec(compile(owned_runner_vm.BOOTSTRAP, "<actual-owned-runner-vm-bootstrap>", "exec"), {})
        except DispatchBoundary:
            pass
    finally:
        sys.settrace(previous)
    assert captured, output.getvalue()
    accelerator = sys.modules["devsecops_qemu_acceleration"]
    assert captured["accelerator"] is accelerator
    assert accelerator.SOURCE_BYTES == (root / "qemu_acceleration.py").read_bytes()
    driver = sys.modules["runner_vm_driver"]
    assert captured["runner"].CONTEXT_TYPE is driver.RunnerVMRemoteContext
    assert captured["routes"].CONTEXT_TYPE is driver.RunnerVMRemoteContext
    assert captured["routes"].GUEST_SOURCE_BYTES == (root / "runner_vm_guest_routes.py").read_bytes()
    assert captured["runner"].SOURCE_BYTES == (root / "runner_service_guest.py").read_bytes()
    assert captured["routes"].SOURCE_BYTES == (root / "runner_vm_routes_remote.py").read_bytes()
    assert captured["recipes"].GUEST["control_user"] == "gitlab-runner"
    assert captured["packet"] == {"binding": "a" * 64, "operation_id": "00000000-0000-4000-8000-000000000001"}


def test_actual_vm_context_dispatches_fixed_guest_service_over_controlled_transport(
    lab,  # noqa: F811
    service_packet,  # noqa: F811
    monkeypatch,
):
    from copy import deepcopy
    import shlex
    from devsecops.factory import runner_vm_remote as remote, runner_vm_recipe as recipes
    from devsecops.factory import runner_service_guest as runner, registry_remote, tcg
    from tests.test_fresh_foundation import execute

    up = execute(lab)
    original_run = service_packet["run_id"]
    selected = json.loads(
        json.dumps(service_packet)
        .replace(original_run, lab.packet["run_id"])
        .replace(original_run.replace("-", ""), lab.packet["run_id"].replace("-", ""))
    )
    selected["runner_vm"] = up["runner_vm"]
    selected["operation_id"] = lab.packet["operation_id"]
    packet = deepcopy(lab.packet)
    packet.update(
        action="runner",
        child=selected,
        expected_root_identity=up["root_identity"],
        expected_runner_vm=up["runner_vm"],
        expected_source_assets=up["source_assets"],
    )
    original_run_command = lab.fake.run
    transmitted = []
    fixed_command = shlex.join(["python3", "-I", "-B", "-c", runner.GUEST_PROGRAM])

    def run(argv, deadline, check=True, data=None):
        if argv[0] == "ssh" and argv[-1] == fixed_command:
            assert argv[-2].startswith("gitlab-runner@")
            transmitted.append(json.loads(data))
            return lab.fake.emit(
                {"runner_vm": up["runner_vm"], "service_ready": True, "complete_cleanup": False}
            )
        return original_run_command(argv, deadline, check=check, data=data)

    monkeypatch.setattr(tcg, "_run", run)
    monkeypatch.setattr(runner, "CONTEXT_TYPE", remote.RunnerVMRemoteContext)
    actual = remote.execute(packet, tcg, recipes, registry_remote, runner=runner)
    assert actual["child"]["service_ready"]
    assert actual["runner_vm"] == up["runner_vm"]
    assert transmitted == [selected]


# Reuse controlled fixtures while retaining the actual VM context and helpers.
from tests.test_runner_vm_remote import lab as lab, foundation_lab as foundation_lab  # noqa: E402,F401
from tests.test_runner_service_guest import guest as guest, packet as service_packet  # noqa: E402,F401
