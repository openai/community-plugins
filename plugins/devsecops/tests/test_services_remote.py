"""Fixed Docker transport simulation; no live services or runtime qualification."""

import base64
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tarfile
from types import SimpleNamespace
import pytest
from devsecops.factory import services_remote as remote, bootstrap_recipe as recipes, tcg
from tests.test_canonical_bootstrap import selected


class Docker:
    def __init__(self, recipe):
        self.recipe = recipe
        self.objects = {}
        self.volumes = {}
        self.calls = []
        self.revoked = False
        self.loss = None

    def run(self, argv, deadline, *, data=None, check=True, **kw):
        self.calls.append(argv)

        def result(value=b"", code=0, error=b""):
            if check and code:
                raise ValueError("controlled-command-failure")
            return SimpleNamespace(stdout=value, returncode=code, stderr=error)

        assert argv[0] == "docker"
        if argv[1:3] == ["image", "inspect"]:
            image = next(
                self.recipe["services"][n]
                for n in ("server_image", "runner_image")
                if self.recipe["services"][n]["image_id"] == argv[3]
            )
            cmd = (
                [
                    "run",
                    "--config",
                    "/etc/gitlab-runner/config.toml",
                    "--working-directory",
                    "/home/gitlab-runner/builds",
                ]
                if image is self.recipe["services"]["runner_image"]
                else []
            )
            return result(
                json.dumps(
                    [
                        {
                            "Id": image["image_id"],
                            "Os": "linux",
                            "Architecture": "amd64",
                            "RepoDigests": [image["reference"]],
                            "Config": {"Cmd": cmd},
                        }
                    ]
                ).encode()
            )
        if argv[1] in ("network", "volume", "container") and argv[2] == "inspect":
            item = self.objects.get(argv[3])
            return result(json.dumps([item]).encode()) if item else result(code=1, error=b"No such object")
        if argv[1] in ("network", "volume", "container") and argv[2] == "create":
            kind = argv[1]
            name = argv[argv.index("--name") + 1] if kind == "container" else argv[-1]
            labels = dict(argv[n + 1].split("=", 1) for n, v in enumerate(argv) if v == "--label")
            ident = name if kind == "volume" else hashlib.sha256(name.encode()).hexdigest()
            item = {
                "Id": ident,
                "Name": name,
                "Labels": labels,
                "Driver": "local" if kind == "volume" else "bridge",
                "Options": None,
                "Internal": False,
            }
            if kind == "container":

                def arg(key, default=None):
                    return argv[argv.index(key) + 1] if key in argv else default

                mounts = []
                for n, v in enumerate(argv):
                    if v == "--mount":
                        value = dict(
                            part.split("=", 1) if "=" in part else (part, True)
                            for part in argv[n + 1].split(",")
                        )
                        mounts.append(
                            {
                                "Type": "volume",
                                "Name": value["src"],
                                "Destination": value["dst"],
                                "RW": not value.get("readonly", False),
                            }
                        )
                server = labels[remote.ROLE] == "server"
                item.update(
                    Image=argv[-1],
                    Config={
                        "Labels": labels,
                        "User": arg("--user", ""),
                        "Entrypoint": [arg("--entrypoint")],
                        "Cmd": []
                        if server
                        else [
                            "run",
                            "--config",
                            "/etc/gitlab-runner/config.toml",
                            "--working-directory",
                            "/home/gitlab-runner/builds",
                        ],
                    },
                    State={"Running": False},
                    Mounts=mounts,
                    HostConfig={
                        "Privileged": False,
                        "PidMode": "",
                        "IpcMode": "private",
                        "Binds": None,
                        "Devices": [],
                        "NetworkMode": arg("--network"),
                        "RestartPolicy": {"Name": "no"},
                        "CapAdd": None,
                        "CapDrop": None if server else ["ALL"],
                        "SecurityOpt": None if server else ["no-new-privileges"],
                        "Memory": int(arg("--memory")),
                        "MemorySwap": int(arg("--memory-swap")),
                        "NanoCpus": int(float(arg("--cpus")) * 1e9),
                        "PidsLimit": int(arg("--pids-limit")),
                        "PortBindings": {"8444/tcp": [{"HostPort": "8444", "HostIp": ""}]} if server else {},
                    },
                )
            self.objects[name] = item
            self.objects[ident] = item
            if kind == "volume":
                self.volumes[name] = {}
            if self.loss == name:
                raise ValueError("controlled-lost-create-response")
            return result((ident + "\n").encode())
        if argv[1] == "cp":
            container, destination = argv[-1].split(":", 1)
            item = self.objects[container]
            mount = next(
                row for row in item["Mounts"] if row["Destination"].rstrip("/") == destination.rstrip("/")
            )
            assert mount["RW"], "staging must not write through the final read-only input mount"
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
                for entry in archive:
                    self.volumes[mount["Name"]][entry.name] = archive.extractfile(entry).read()
            return result()
        if argv[1] in ("start", "stop"):
            self.objects[argv[-1]]["State"]["Running"] = argv[1] == "start"
            return result()
        if argv[1] in ("container", "volume", "network") and argv[2] == "rm":
            item = self.objects[argv[3]]
            self.objects.pop(item["Id"], None)
            self.objects.pop(item["Name"], None)
            return result()
        if argv[1] == "exec":
            if "gitlab-rails" in argv:
                if b"PersonalAccessToken.find(" in data:
                    return result(
                        json.dumps(
                            {
                                "token_id": 9,
                                "owner_user_id": 1,
                                "token_name": "factory-owned",
                                "revoked": self.revoked,
                            }
                        ).encode()
                    )
                return result(
                    json.dumps(
                        {"owner_user_id": 1, "namespace_id": 2, "namespace_path": "root", "token_id": 9}
                    ).encode()
                )
            if any("gitlab-healthcheck" in item for item in argv):
                return result()
            if "/etc/gitlab-runner/.runner_system_id" in argv:
                return result(b"s_original-manager\n")
            if "-c" in argv:
                expected = json.loads(data)
                for path, digest in expected.items():
                    name = path.removeprefix("/opt/devsecops/runner-inputs/")
                    assert any(
                        name in files and hashlib.sha256(files[name]).hexdigest() == digest
                        for files in self.volumes.values()
                    )
                return result(b"owned-runner-inputs-verified\n")
        raise AssertionError(argv)


def fixture(tmp_path, monkeypatch):
    recipe = selected(tmp_path)
    engine = Docker(recipe)
    root = tmp_path / "remote"
    original = Path
    monkeypatch.setattr(
        remote,
        "Path",
        lambda value: root if value == recipe["services"]["remote_directory"] else original(value),
    )
    support = SimpleNamespace(
        _private=tcg._private,
        _write=tcg._write,
        _read=tcg._read,
        _json=json.loads,
        _lock_descriptor=tcg._lock_descriptor,
        _run=engine.run,
    )
    packet = {
        "recipe": recipe,
        "action": "up",
        "operation_id": "12345678-1234-1234-1234-123456789012",
        "run_id": "12345678-1234-1234-1234-123456789012",
        "identities": {},
        "package_modules": {},
        "timeout": 30,
        "expires_at": "2099-01-01T00:00:00Z",
        "root_password": "private-password-123456",
        "operator_token": "private-api-token-123456",
        "materials": {
            name: base64.b64encode(Path(ref["path"]).read_bytes()).decode()
            for name, ref in recipe["services"]["materials"].items()
        },
    }
    packet["binding"] = recipes.binding(recipe, run_id=packet["run_id"], identities={}, package_modules={})
    return recipe, engine, support, packet, root


def test_server_runner_input_staging_token_observation_and_exact_teardown(tmp_path, monkeypatch):
    recipe, engine, support, packet, root = fixture(tmp_path, monkeypatch)
    report = remote.execute(packet, support, recipes)
    assert report["bootstrap"] == "server-ready" and report["principal"]["token_id"] == 9
    runner = deepcopy(packet)
    runner.update(
        action="runner",
        runner_id=5,
        runner_token="glrt-controlled-runner-token",
        runner_description="factory-owned:controlled",
    )
    raw = b'{"controlled":true}\n'
    target = recipe["services"]["runner_environment"]["DEVSECOPS_GIT_INVOCATION"]
    runner["runner_inputs"] = {
        target: {
            "content": base64.b64encode(raw).decode(),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "private": False,
        }
    }
    runner["runner_environment"] = {
        **recipe["services"]["runner_environment"],
        "DEVSECOPS_GIT_INVOCATION_SHA256": hashlib.sha256(raw).hexdigest(),
    }
    runner["runner_secrets"] = {}
    report = remote.execute(runner, support, recipes)
    assert report["runner_id"] == 5 and report["runner_system_id"] == "s_original-manager"
    status = {**packet, "action": "token-status", "token_selection": {"token_id": 9, "owner_user_id": 1}}
    assert remote.execute(status, support, recipes)["revoked"] is False
    engine.revoked = True
    assert remote.execute(status, support, recipes)["revoked"] is True
    report = remote.execute({**packet, "action": "down"}, support, recipes)
    assert report["complete_cleanup"] and all(row["state"] == "absent" for row in report["resources"])
    assert not engine.objects
    assert not any("pull" in argv or "--privileged" in argv for argv in engine.calls)
    assert not any(
        "private-api-token" in str(argv) or "private-password" in str(argv) for argv in engine.calls
    )


def test_unknown_create_is_retained_and_prohibits_duplicate_up(tmp_path, monkeypatch):
    recipe, engine, support, packet, root = fixture(tmp_path, monkeypatch)
    engine.loss = "devsecops-services-" + packet["binding"][:20] + "-network"
    with pytest.raises(ValueError, match="lost-create"):
        remote.execute(packet, support, recipes)
    state = json.loads((root / "state.json").read_bytes())
    assert state["inflight"]
    before = len(engine.calls)
    with pytest.raises(ValueError, match="reconciled"):
        remote.execute(packet, support, recipes)
    assert len(engine.calls) == before
    observed = remote.execute({**packet, "action": "reconcile"}, support, recipes)
    assert observed["unresolved_operations"] and not observed["complete_cleanup"]


def test_replaced_owned_container_cannot_be_removed(tmp_path, monkeypatch):
    recipe, engine, support, packet, root = fixture(tmp_path, monkeypatch)
    remote.execute(packet, support, recipes)
    server = next(
        item
        for item in engine.objects.values()
        if item.get("Config", {}).get("Labels", {}).get(remote.ROLE) == "server"
    )
    server["Config"]["Labels"][remote.LABEL] = "foreign"
    before = len(engine.calls)
    with pytest.raises(ValueError, match="identity or labels"):
        remote.execute({**packet, "action": "down"}, support, recipes)
    assert not any(argv[1] == "stop" or len(argv) > 2 and argv[2] == "rm" for argv in engine.calls[before:])
