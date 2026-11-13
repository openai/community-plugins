"""Fixed SSH-transported owned GitLab CE and shell-runner Docker operations.

No image pulls, host package changes, host Docker socket mounts, arbitrary shell
commands or receipt-based adoption. Creation intent is durable before dispatch.
"""

from __future__ import annotations
import base64
from datetime import datetime, timezone
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tarfile
import time

LABEL = "io.devsecops.services.binding"
ROLE = "io.devsecops.services.role"


def execute(packet, support, recipes):
    recipe = recipes.validate(packet["recipe"])
    selected = recipe["services"]
    action, operation = packet["action"], packet["operation_id"]
    dedicated = recipe["kind"] == "owned-bootstrap-v2"
    if dedicated and action == "runner":
        raise ValueError("Dedicated runner VM forbids the legacy Docker runner action")
    if action not in ("preflight", "up", "runner", "down", "reconcile", "token-status") or not re.fullmatch(
        "[a-f0-9-]{36}", operation
    ):
        raise ValueError("Unsupported owned services operation")
    binding = recipes.binding(
        recipe,
        run_id=packet["run_id"],
        identities=packet["identities"],
        package_modules=packet["package_modules"],
    )
    if binding != packet["binding"] or not 0 < packet["timeout"] <= 1500:
        raise ValueError("Owned services binding or deadline differs")
    deadline = time.monotonic() + packet["timeout"]
    expiry = datetime.fromisoformat(packet["expires_at"].replace("Z", "+00:00"))
    if expiry.tzinfo is None or action in ("up", "runner") and datetime.now(timezone.utc) >= expiry:
        raise ValueError("Owned service provisioning window expired")
    if os.getuid() == 0 or os.geteuid() == 0:
        raise ValueError("Owned service operator must be non-root")
    if action == "preflight":
        images = {}
        for role in ("server_image",) if dedicated else ("server_image", "runner_image"):
            chosen = selected[role]
            result = support._run(
                ["docker", "image", "inspect", chosen["image_id"]], min(deadline, time.monotonic() + 20)
            )
            rows = support._json(result.stdout)
            if (
                not isinstance(rows, list)
                or len(rows) != 1
                or rows[0].get("Id") != chosen["image_id"]
                or rows[0].get("Os") != "linux"
                or rows[0].get("Architecture") != "amd64"
                or chosen["reference"] not in rows[0].get("RepoDigests", [])
            ):
                raise ValueError(
                    "Selected owned-service image is not locally available with its exact immutable identity"
                )
            images[role] = {
                "image_id": rows[0]["Id"],
                "reference": chosen["reference"],
                "platform": "linux/amd64",
            }
        return {
            "kind": "owned-service-preconditions",
            "images": images,
            "effects": "none",
            "run_id": packet["run_id"],
        }
    root = Path(selected["remote_directory"])
    support._private(root.parent)
    if not root.exists():
        if action != "up":
            raise ValueError("Owned service state is absent; no absence inference is permitted")
        support._private(root, create=True)
        support._write(
            root / "owner.json", recipes.canonical({"binding": binding, "operation_id": operation})
        )
        support._write(root / "lock", b"")
    support._private(root)
    info = root.stat()
    owner = support._read(root / "owner.json")
    if support._json(owner).get("binding") != binding:
        raise ValueError("Owned service directory belongs to another run")
    lock = support._lock_descriptor(root / "lock")
    fcntl.flock(
        lock, (fcntl.LOCK_SH if action in ("reconcile", "token-status") else fcntl.LOCK_EX) | fcntl.LOCK_NB
    )
    state_path = root / "state.json"
    state = (
        support._json(support._read(state_path))
        if state_path.exists()
        else {
            "resources": [],
            "inflight": [],
            "up_attempted": False,
            "runner_attempted": False,
            "principal": None,
        }
    )
    prefix = "devsecops-services-" + binding[:20]

    def check():
        support._private(root)
        current = root.stat()
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino) or support._read(
            root / "owner.json"
        ) != owner:
            raise ValueError("Owned service state identity changed")
        if (
            time.monotonic() >= deadline
            or action in ("up", "runner")
            and datetime.now(timezone.utc) >= expiry
        ):
            raise ValueError("Owned service window expired")

    def save():
        check()
        support._write(state_path, recipes.canonical(state), replace=state_path.exists())

    def command(argv, *, data=None, seconds=30, required=True):
        check()
        result = support._run(argv, min(deadline, time.monotonic() + seconds), data=data, check=required)
        check()
        return result

    def inspect(kind, name):
        result = command(["docker", kind, "inspect", name], required=False)
        if result.returncode:
            if b"No such" in result.stderr or b"not found" in result.stderr:
                return None
            raise ValueError("Owned service identity observation failed")
        values = support._json(result.stdout)
        if not isinstance(values, list) or len(values) != 1:
            raise ValueError("Owned service inspection shape differs")
        return values[0]

    def identity(row, item):
        labels = (
            item.get("Config", {}).get("Labels", {}) if row["kind"] == "container" else item.get("Labels", {})
        )
        actual = item.get("Name") if row["kind"] == "volume" else item.get("Id")
        if (
            actual != row["id"]
            or labels.get(LABEL) != binding
            or labels.get(ROLE) != row["role"]
            or item.get("Name", "").removeprefix("/") != row["name"]
        ):
            raise ValueError("Owned service immutable identity or labels differ")
        if row["kind"] == "container":
            host = item.get("HostConfig", {})
            if (
                item.get("Image") != row["image_id"]
                or host.get("Privileged") is not False
                or host.get("PidMode") not in ("", None)
                or host.get("IpcMode") not in ("private", "")
                or host.get("Binds")
                or host.get("Devices")
                or host.get("NetworkMode") != prefix + "-network"
                or host.get("RestartPolicy", {}).get("Name") not in ("no", "")
                or any(
                    m.get("Type") != "volume"
                    or m.get("Name") not in [r["name"] for r in state["resources"] if r["kind"] == "volume"]
                    for m in item.get("Mounts", [])
                )
            ):
                raise ValueError("Owned service container isolation differs")
            config = item.get("Config", {})
            server = row["role"] == "server"
            wanted_roles = (
                [
                    ("config", "/etc/gitlab", True),
                    ("logs", "/var/log/gitlab", True),
                    ("data", "/var/opt/gitlab", True),
                ]
                if server
                else [
                    ("runner-config", "/etc/gitlab-runner", True),
                    ("runner-builds", "/home/gitlab-runner/builds", True),
                    ("runner-inputs", "/opt/devsecops/runner-inputs", row["role"] == "runner-setup"),
                ]
            )
            expected_mounts = sorted(
                (prefix + "-" + role, destination, writable) for role, destination, writable in wanted_roles
            )
            if (
                sorted((m.get("Name"), m.get("Destination"), m.get("RW")) for m in item.get("Mounts", []))
                != expected_mounts
            ):
                raise ValueError("Owned service exact mounts differ")
            entrypoint = (
                "/assets/wrapper"
                if server
                else "/usr/bin/true"
                if row["role"] == "runner-setup"
                else "/usr/bin/gitlab-runner"
            )
            if (
                config.get("Entrypoint") != [entrypoint]
                or host.get("CapAdd")
                or host.get("Memory") != (8589934592 if server else 4294967296)
                or host.get("MemorySwap") != host.get("Memory")
                or host.get("NanoCpus") != (4000000000 if server else 2000000000)
                or host.get("PidsLimit") != (2048 if server else 256)
            ):
                raise ValueError("Owned service command or resource bounds differ")
            if server:
                bindings = host.get("PortBindings", {})
                if (
                    set(bindings) != {"8444/tcp"}
                    or len(bindings["8444/tcp"]) != 1
                    or bindings["8444/tcp"][0].get("HostPort") != "8444"
                    or bindings["8444/tcp"][0].get("HostIp") not in ("", "0.0.0.0")
                    or config.get("User") not in ("", "0", "root", "0:0")
                    or host.get("CapDrop")
                ):
                    raise ValueError("Owned server publication or UID differs")
            elif (
                config.get("User") != "10001:10001"
                or host.get("CapDrop") != ["ALL"]
                or host.get("SecurityOpt") not in (["no-new-privileges"], ["no-new-privileges=true"])
                or host.get("PortBindings")
                or config.get("Cmd")
                != [
                    "run",
                    "--config",
                    "/etc/gitlab-runner/config.toml",
                    "--working-directory",
                    "/home/gitlab-runner/builds",
                ]
            ):
                raise ValueError("Owned runner exact non-root contract differs")
        elif row["kind"] == "volume":
            if item.get("Driver") != "local" or item.get("Options") not in (None, {}):
                raise ValueError("Owned service volume driver differs")
        elif item.get("Driver") != "bridge" or item.get("Internal") is not False:
            raise ValueError("Owned service network differs")

    def create(kind, role, args, *, image_id=None):
        name = prefix + "-" + role
        if inspect(kind, name) is not None:
            raise ValueError("Owned service destination exists; adoption is forbidden")
        intent = {"operation_id": operation, "verb": "create", "kind": kind, "name": name, "role": role}
        state["inflight"].append(intent)
        save()
        command(
            [
                "docker",
                kind,
                "create",
                *args,
                "--label",
                LABEL + "=" + binding,
                "--label",
                ROLE + "=" + role,
                *(["--name", name] if kind == "container" else []),
                *([image_id] if kind == "container" else [name]),
            ]
        )
        item = inspect(kind, name)
        if item is None:
            raise ValueError("Owned service creation has uncertain effect")
        ident = item.get("Name") if kind == "volume" else item.get("Id")
        if not isinstance(ident, str) or kind != "volume" and not re.fullmatch("[a-f0-9]{64}", ident):
            raise ValueError("Owned service created identity differs")
        row = {
            "kind": kind,
            "role": role,
            "name": name,
            "id": ident,
            "image_id": image_id,
            "state": "present",
        }
        identity(row, item)
        state["resources"].append(row)
        state["inflight"].remove(intent)
        save()
        return row

    def mutate(row, verb, args, *, data=None, seconds=30):
        item = inspect(row["kind"], row["id"])
        if item is None:
            raise ValueError("Owned service mutation target is absent")
        identity(row, item)
        intent = {
            "operation_id": operation,
            "verb": verb,
            "kind": row["kind"],
            "name": row["name"],
            "id": row["id"],
        }
        state["inflight"].append(intent)
        save()
        result = command(args, data=data, seconds=seconds)
        # Command return is not proof of the intended semantic effect. Caller
        # observes it, then acknowledges the exact retained ticket.
        return result, intent

    def ack(intent):
        state["inflight"].remove(intent)
        save()

    def image(which):
        expected = selected[which]
        item = inspect("image", expected["image_id"])
        if (
            item is None
            or item.get("Id") != expected["image_id"]
            or item.get("Os") != "linux"
            or item.get("Architecture") != "amd64"
            or expected["reference"] not in item.get("RepoDigests", [])
        ):
            raise ValueError("Owned service requires the exact preloaded locked Linux image")

    def tar(files, uid=0):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            for name, raw in files.items():
                member = tarfile.TarInfo(name)
                member.size, member.mode, member.uid, member.gid = len(raw), 0o600, uid, uid
                archive.addfile(member, io.BytesIO(raw))
        return stream.getvalue()

    def copy_files(row, files, destination, *, uid=0):
        _, intent = mutate(
            row,
            "install-selected-files",
            ["docker", "cp", "-a", "-", row["id"] + ":" + destination],
            data=tar(files, uid),
        )
        # This is only an installation step; subsequent live commands verify
        # configuration and runtime identity before bootstrap can complete.
        ack(intent)

    try:
        if action == "token-status":
            requested = packet["token_selection"]
            principal = state.get("principal")
            if (
                type(requested) is not dict
                or set(requested) != {"token_id", "owner_user_id"}
                or any(type(v) is not int or v <= 0 for v in requested.values())
                or not principal
                or any(principal.get(k) != v for k, v in requested.items())
            ):
                raise ValueError("Token observation differs from directly created principal")
            server = next((row for row in state["resources"] if row["role"] == "server"), None)
            if server is None:
                raise ValueError("Original owned GitLab server identity is unavailable")
            current = inspect("container", server["id"])
            if current is None:
                raise ValueError("Absent server cannot prove token revocation")
            identity(server, current)
            ruby = (
                "require 'json';t=PersonalAccessToken.find(" + str(requested["token_id"]) + ");"
                "raise 'identity' unless t.user_id=="
                + str(requested["owner_user_id"])
                + " && t.name=='factory-owned';"
                "puts JSON.generate({token_id:t.id,owner_user_id:t.user_id,token_name:t.name,revoked:t.revoked});"
            )
            result = command(
                ["docker", "exec", "-i", server["id"], "gitlab-rails", "runner", "-"],
                data=ruby.encode(),
                seconds=90,
            )
            value = support._json(result.stdout)
            if (
                type(value) is not dict
                or value.get("token_id") != requested["token_id"]
                or value.get("owner_user_id") != requested["owner_user_id"]
                or value.get("token_name") != "factory-owned"
                or type(value.get("revoked")) is not bool
            ):
                raise ValueError("Owned token revocation observation differs")
            return {**value, "instance_binding": binding}
        if state["inflight"] and action in ("up", "runner"):
            raise ValueError("Uncertain owned service operation must be reconciled")
        if action == "up":
            if state["up_attempted"] or state["resources"]:
                raise ValueError("Owned service bootstrap cannot be repeated")
            image("server_image")
            if not dedicated:
                image("runner_image")
            state["up_attempted"] = True
            save()
            network = create("network", "network", ["--driver", "bridge"])
            volumes = {
                role: create("volume", role, [])
                for role in (
                    ("config", "logs", "data")
                    if dedicated
                    else ("config", "logs", "data", "runner-config", "runner-builds", "runner-inputs")
                )
            }
            args = [
                "--network",
                network["name"],
                "--network-alias",
                "gitlab." + recipe["environment"] + ".test",
                "--hostname",
                "gitlab." + recipe["environment"] + ".test",
                "--restart=no",
                "--memory",
                "8589934592",
                "--memory-swap",
                "8589934592",
                "--cpus",
                "4",
                "--pids-limit",
                "2048",
                "--shm-size",
                "268435456",
                "--publish",
                "8444:8444",
                "--entrypoint",
                "/assets/wrapper",
            ]
            for role, destination in (
                ("config", "/etc/gitlab"),
                ("logs", "/var/log/gitlab"),
                ("data", "/var/opt/gitlab"),
            ):
                args.extend(["--mount", "type=volume,src=" + volumes[role]["name"] + ",dst=" + destination])
            server = create("container", "server", args, image_id=selected["server_image"]["image_id"])
            materials = {
                name: base64.b64decode(packet["materials"][name], validate=True)
                for name in ("ca", "certificate", "private_key")
            }
            if any(
                hashlib.sha256(raw).hexdigest() != selected["materials"][name]["sha256"]
                for name, raw in materials.items()
            ):
                raise ValueError("Owned service TLS material pin differs")
            password, token = packet["root_password"], packet["operator_token"]
            if not all(
                isinstance(s, str) and 20 <= len(s) <= 512 and all(33 <= ord(c) <= 126 for c in s)
                for s in (password, token)
            ):
                raise ValueError("Owned service private credential shape differs")
            config = (
                "external_url " + json.dumps(selected["origin"]) + "\nletsencrypt['enable'] = false\n"
                "nginx['ssl_certificate'] = '/etc/gitlab/factory-certificate.pem'\n"
                "nginx['ssl_certificate_key'] = '/etc/gitlab/factory-private-key.pem'\n"
                "gitlab_rails['initial_root_password'] = File.read('/etc/gitlab/factory-root-password').strip\n"
                "gitlab_rails['gitlab_signup_enabled'] = false\n"
            ).encode()
            copy_files(
                server,
                {
                    "gitlab.rb": config,
                    "factory-certificate.pem": materials["certificate"],
                    "factory-private-key.pem": materials["private_key"],
                    "factory-root-password": password.encode(),
                    "factory-operator-token": token.encode(),
                },
                "/etc/gitlab/",
            )
            _, ticket = mutate(server, "start", ["docker", "start", server["id"]])
            running = inspect("container", server["id"])
            identity(server, running)
            if running.get("State", {}).get("Running") is not True:
                raise ValueError("Owned GitLab server did not start")
            ack(ticket)
            ready = False
            while time.monotonic() + 5 < deadline:
                result = command(
                    [
                        "docker",
                        "exec",
                        server["id"],
                        "/opt/gitlab/bin/gitlab-healthcheck",
                        "--fail",
                        "--max-time",
                        "5",
                    ],
                    required=False,
                    seconds=15,
                )
                if result.returncode == 0:
                    ready = True
                    break
                time.sleep(min(2, max(0, deadline - time.monotonic())))
            if not ready:
                raise ValueError("Owned GitLab readiness has not been observed")
            # Fixed initializer targets root on this newly created empty server.
            # It never resets/replaces an existing token or accepts Ruby input.
            ruby = (
                "require 'json'; u=User.find_by!(username: 'root'); n=u.namespace; "
                "raise 'existing factory token' if u.personal_access_tokens.where(name: 'factory-owned').exists?; "
                "t=u.personal_access_tokens.create!(name: 'factory-owned', scopes: ['api','create_runner','manage_runner'], expires_at: Date.today+2); "
                "t.set_token(File.read('/etc/gitlab/factory-operator-token').strip); t.save!; "
                "puts JSON.generate({owner_user_id:u.id,namespace_id:n.id,namespace_path:n.full_path,token_id:t.id});"
            )
            result, ticket = mutate(
                server,
                "initialize-private-principal",
                ["docker", "exec", "-i", server["id"], "gitlab-rails", "runner", "-"],
                data=ruby.encode(),
                seconds=180,
            )
            principal = support._json(result.stdout)
            if (
                not isinstance(principal, dict)
                or set(principal) != {"owner_user_id", "namespace_id", "namespace_path", "token_id"}
                or principal["namespace_path"] != "root"
                or any(
                    type(principal[k]) is not int or principal[k] <= 0
                    for k in ("owner_user_id", "namespace_id", "token_id")
                )
            ):
                raise ValueError("Owned GitLab principal readback differs")
            state["principal"] = principal
            ack(ticket)
        elif action == "runner":
            if state["runner_attempted"] or state["principal"] is None:
                raise ValueError(
                    "Owned runner requires the original initialized service and one-shot registration"
                )
            image("runner_image")
            state["runner_attempted"] = True
            save()
            token = packet["runner_token"]
            if not isinstance(token, str) or not re.fullmatch(r"glrt-[A-Za-z0-9_-]{10,500}", token):
                raise ValueError("Owned runner authentication token is unsupported")
            environment = packet["runner_environment"]
            secrets = packet["runner_secrets"]
            inputs = packet["runner_inputs"]
            if (
                set(environment) != set(selected["runner_environment"])
                or set(secrets) != set(selected["runner_secret_environment"])
                or any(
                    environment[k] != selected["runner_environment"][k]
                    for k in environment
                    if k != "DEVSECOPS_GIT_INVOCATION_SHA256"
                )
                or not isinstance(inputs, dict)
                or not 1 <= len(inputs) <= 70
            ):
                raise ValueError("Owned runner installation selectors differ")
            decoded = {}
            for path, item in inputs.items():
                if (
                    not isinstance(path, str)
                    or not re.fullmatch(
                        r"/opt/devsecops/runner-inputs/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+){0,3}", path
                    )
                    or ".." in Path(path).parts
                ):
                    raise ValueError("Owned runner installation path differs")
                raw = base64.b64decode(item["content"], validate=True)
                if hashlib.sha256(raw).hexdigest() != item["sha256"]:
                    raise ValueError("Owned runner installation bytes differ")
                decoded[path.removeprefix("/opt/devsecops/runner-inputs/")] = raw
            target = environment["DEVSECOPS_GIT_INVOCATION"].removeprefix("/opt/devsecops/runner-inputs/")
            if (
                sum(map(len, decoded.values())) > 1500000
                or target not in decoded
                or hashlib.sha256(decoded[target]).hexdigest()
                != environment["DEVSECOPS_GIT_INVOCATION_SHA256"]
            ):
                raise ValueError("Owned runner invocation installation differs")
            config = (
                "concurrent = 1\ncheck_interval = 3\n[[runners]]\nname = "
                + json.dumps(packet["runner_description"])
                + "\nurl = "
                + json.dumps(selected["origin"])
                + "\n"
                "token = "
                + json.dumps(token)
                + '\nexecutor = "shell"\nshell = "bash"\nbuilds_dir = "/home/gitlab-runner/builds"\n'
                'tls-ca-file = "/etc/gitlab-runner/factory-ca.pem"\nenvironment = '
                + json.dumps([k + "=" + v for k, v in {**environment, **secrets}.items()])
                + "\n"
            ).encode()
            volumes = {row["role"]: row for row in state["resources"] if row["kind"] == "volume"}
            args = [
                "--network",
                prefix + "-network",
                "--restart=no",
                "--memory",
                "4294967296",
                "--memory-swap",
                "4294967296",
                "--cpus",
                "2",
                "--pids-limit",
                "256",
                "--cap-drop=ALL",
                "--security-opt",
                "no-new-privileges",
                "--user",
                "10001:10001",
                "--entrypoint",
                "/usr/bin/gitlab-runner",
                "--mount",
                "type=volume,src=" + volumes["runner-config"]["name"] + ",dst=/etc/gitlab-runner",
                "--mount",
                "type=volume,src=" + volumes["runner-builds"]["name"] + ",dst=/home/gitlab-runner/builds",
                "--mount",
                "type=volume,src="
                + volumes["runner-inputs"]["name"]
                + ",dst=/opt/devsecops/runner-inputs,readonly",
            ]
            # The locked runner image has a fixed default Cmd; reject alternatives.
            inspected_image = inspect("image", selected["runner_image"]["image_id"])
            if inspected_image.get("Config", {}).get("Cmd") != [
                "run",
                "--config",
                "/etc/gitlab-runner/config.toml",
                "--working-directory",
                "/home/gitlab-runner/builds",
            ]:
                raise ValueError(
                    "Owned runner image does not implement the fixed shell-runner entry contract"
                )
            setup_args = [
                value.replace(
                    ",dst=/opt/devsecops/runner-inputs,readonly", ",dst=/opt/devsecops/runner-inputs"
                )
                for value in args
            ]
            setup_args[setup_args.index("--entrypoint") + 1] = "/usr/bin/true"
            installer = create(
                "container", "runner-setup", setup_args, image_id=selected["runner_image"]["image_id"]
            )
            copy_files(installer, decoded, "/opt/devsecops/runner-inputs/", uid=10001)
            runner = create("container", "runner", args, image_id=selected["runner_image"]["image_id"])
            ca = base64.b64decode(packet["materials"]["ca"], validate=True)
            if hashlib.sha256(ca).hexdigest() != selected["materials"]["ca"]["sha256"]:
                raise ValueError("Owned runner trust differs")
            copy_files(
                runner, {"config.toml": config, "factory-ca.pem": ca}, "/etc/gitlab-runner/", uid=10001
            )
            _, ticket = mutate(runner, "start", ["docker", "start", runner["id"]])
            running = inspect("container", runner["id"])
            identity(runner, running)
            if running.get("State", {}).get("Running") is not True:
                raise ValueError("Owned runner did not start")
            ack(ticket)
            verification = "import hashlib,json,pathlib,sys; p=json.loads(sys.stdin.buffer.read(64001)); assert isinstance(p,dict) and len(p)<=70; assert all(hashlib.sha256(pathlib.Path(k).read_bytes()).hexdigest()==v for k,v in p.items()); print('owned-runner-inputs-verified')"
            verified = command(
                [
                    "docker",
                    "exec",
                    "-i",
                    runner["id"],
                    environment["DEVSECOPS_PYTHON"],
                    "-I",
                    "-B",
                    "-c",
                    verification,
                ],
                data=json.dumps({path: item["sha256"] for path, item in inputs.items()}).encode(),
            )
            if verified.stdout.strip() != b"owned-runner-inputs-verified":
                raise ValueError("Owned runner input readback differs")
            observed = ""
            for _ in range(30):
                result = command(
                    ["docker", "exec", runner["id"], "/bin/cat", "/etc/gitlab-runner/.runner_system_id"],
                    required=False,
                )
                if result.returncode == 0:
                    observed = result.stdout.decode().strip()
                    break
                if time.monotonic() + 1 >= deadline:
                    break
                time.sleep(1)
            if not re.fullmatch(r"[sr]_[A-Za-z0-9_-]{6,128}", observed):
                raise ValueError("Owned runner manager identity has not been observed")
            state["runner_system_id"] = observed
            state["runner_id"] = packet["runner_id"]
            save()
        elif action == "down":
            if state["inflight"]:
                raise ValueError("Owned service uncertain effects require read-only reconciliation")
            for row in reversed(state["resources"]):
                item = inspect(row["kind"], row["id"])
                if item is None:
                    row["state"] = "absent"
                    save()
                    continue
                identity(row, item)
                if row["kind"] == "container" and item.get("State", {}).get("Running"):
                    _, ticket = mutate(row, "stop", ["docker", "stop", "--time", "20", row["id"]], seconds=30)
                    fresh = inspect("container", row["id"])
                    identity(row, fresh)
                    if fresh.get("State", {}).get("Running"):
                        raise ValueError("Owned service stop remains unobserved")
                    ack(ticket)
                _, ticket = mutate(row, "remove", ["docker", row["kind"], "rm", row["id"]])
                if inspect(row["kind"], row["id"]) is not None:
                    raise ValueError("Owned service removal remains unobserved")
                row["state"] = "absent"
                ack(ticket)
        for row in state["resources"]:
            item = inspect(row["kind"], row["id"])
            if item is None:
                row["state"] = "absent"
            else:
                identity(row, item)
                row["state"] = "present"
        # Reconciliation observes only; no same-name adoption or implicit retry.
        complete = (
            state["up_attempted"]
            and not state["inflight"]
            and all(row["state"] == "absent" for row in state["resources"])
        )
        return {
            "bootstrap": "server-ready" if state["principal"] else "unobserved",
            "principal": state["principal"],
            "runner_id": state.get("runner_id"),
            "runner_system_id": state.get("runner_system_id"),
            "resources": [
                {
                    "identity": {k: r[k] for k in ("kind", "name", "id", "role")},
                    "disposition": "remove",
                    "state": r["state"],
                }
                for r in state["resources"]
            ],
            "unresolved_operations": state["inflight"],
            "complete_cleanup": complete,
            "mutation_effect": "applied" if not state["inflight"] else "unknown",
        }
    finally:
        os.close(lock)
