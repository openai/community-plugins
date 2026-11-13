"""Explicit stages for a private, disposable, single-VM GitLab/GitOps demonstration.

No cloud API, automatic package installation, public listener, or implicit teardown.
Run stages only on the newly authorized VM. Operator state is always outside source.
"""

from __future__ import annotations
import argparse
import importlib.util
import base64
import datetime as dt
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parents[1]
VERSIONS = json.loads((HERE / "versions.json").read_text())
PRIVATE_STATE = None

_policy_spec = importlib.util.spec_from_file_location(
    "devsecops_portable_scan_policy", HERE / "scan_policy.py"
)
SCAN_POLICY = importlib.util.module_from_spec(_policy_spec)
_policy_spec.loader.exec_module(SCAN_POLICY)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def private_open(state, request, timeout=60):
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(state / "tls/ca.crt"))),
        NoRedirect(),
    )
    return opener.open(request, timeout=timeout)


def verify_tool(state, name):
    expected = json.loads((PLUGIN / "supplychain.lock.json").read_text())["tools"][name]["platforms"][
        "linux-amd64"
    ]
    path = state / "inputs" / name
    if path.is_symlink() or path.stat().st_size != expected["binary_bytes"]:
        raise ValueError("Scanner executable identity differs from packaged lock")
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected["binary_sha256"]:
        raise ValueError("Scanner executable identity differs from packaged lock")


def require_classic_store():
    driver = run(["docker", "info", "--format", "{{.Driver}}"], timeout=20).decode().strip()
    if driver != "overlay2":
        raise ValueError(
            "This demo requires the classic Docker overlay2 image store for exact config-digest binding. "
            "Docker29 defaults to containerd on fresh installs. Follow the disposable-host setup in README; "
            "do not switch a Docker daemon that has existing workloads."
        )


def import_base(state, owner):
    require_classic_store()
    source = state / "inputs/base.oci.tar"
    expected = json.loads((state / "input-manifest.json").read_text())["files"]["base.oci.tar"]
    if (
        source.is_symlink()
        or source.stat().st_size != expected["bytes"]
        or hashlib.sha256(source.read_bytes()).hexdigest() != expected["sha256"]
    ):
        raise ValueError("Retained base archive differs from the verified input manifest")
    directory = state / ("base-import-" + secrets.token_hex(6))
    directory.mkdir(mode=0o700)
    layout = directory / "oci"
    layout.mkdir(mode=0o700)
    # Older Skopeo preserves root ownership when unpacking oci-archive itself.
    # Copy only bounded OCI files, without applying archived ownership or modes.
    with tarfile.open(source) as original:
        members = original.getmembers()
        if len(members) > 1024 or len({x.name for x in members}) != len(members):
            raise ValueError("Retained OCI layout has too many or duplicate members")
        if sum(x.size for x in members) > 2 * 1024 * 1024 * 1024:
            raise ValueError("Retained OCI layout exceeds the demo input size limit")
        for member in members:
            if member.isdir() and member.name in {"blobs", "blobs/sha256"}:
                continue
            blob = re.fullmatch(r"blobs/sha256/([a-f0-9]{64})", member.name)
            if not member.isfile() or not (blob or member.name in {"index.json", "oci-layout"}):
                raise ValueError("Retained OCI layout contains an unexpected path or member type")
            target = layout / member.name
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with original.extractfile(member) as content, target.open("xb") as output:
                while chunk := content.read(1024 * 1024):
                    digest.update(chunk)
                    output.write(chunk)
            target.chmod(0o600)
            if blob and digest.hexdigest() != blob.group(1):
                raise ValueError("Retained OCI blob differs from its content address")
    archive = directory / "base.docker.tar"
    tag = "devsecops-base:retained"
    # Local archive conversion avoids the old Docker API used by Ubuntu's Skopeo.
    # Keep both archives and the original config identity; do not lower daemon API policy.
    run(
        ["skopeo", "copy", "oci:" + str(layout), "docker-archive:" + str(archive) + ":" + tag],
        timeout=300,
    )
    archive.chmod(0o600)
    with tarfile.open(archive, "r:") as converted:
        members = converted.getmembers()
        manifests = [x for x in members if x.name == "manifest.json"]
        if len(manifests) != 1 or not manifests[0].isfile() or manifests[0].size > 65536:
            raise ValueError("Converted archive has an invalid manifest")
        manifest = json.loads(converted.extractfile(manifests[0]).read())
        # Skopeo1.13 emits the canonical Docker Hub name in Docker archives.
        if len(manifest) != 1 or manifest[0].get("RepoTags") not in ([tag], ["docker.io/library/" + tag]):
            raise ValueError("Converted archive does not contain exactly the retained base tag")
        configs = [x for x in members if x.name == manifest[0]["Config"]]
        if len(configs) != 1 or not configs[0].isfile() or configs[0].size > 2 * 1024 * 1024:
            raise ValueError("Converted archive has an invalid config")
        config_digest = "sha256:" + hashlib.sha256(converted.extractfile(configs[0]).read()).hexdigest()
        if config_digest != owner["base_config_digest"]:
            raise ValueError("Converted base config differs from pinned OCI image")
    run(["docker", "image", "load", "--input", archive], timeout=300)
    actual = run(["docker", "image", "inspect", tag, "--format", "{{.Id}}"], timeout=30).decode().strip()
    if actual != owner["base_config_digest"]:
        raise ValueError("Imported base config differs from pinned OCI image")
    write(
        directory / "receipt.json",
        {
            "base_archive_sha256": expected["sha256"],
            "base_config_digest": actual,
            "docker_archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "tag": tag,
            "docker_store": "overlay2",
        },
    )


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, (dict, list)):
        value = json.dumps(value, indent=2) + "\n"
    path.write_bytes(value.encode() if isinstance(value, str) else value)
    path.chmod(0o600)


def run(args, *, data=None, timeout=120):
    r = subprocess.run(
        [str(a) for a in args], input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout
    )
    if r.returncode:
        if PRIVATE_STATE is not None and PRIVATE_STATE.is_dir():
            name = "command-failure-" + secrets.token_hex(6)
            write(PRIVATE_STATE / "diagnostics" / (name + ".stdout"), r.stdout)
            write(PRIVATE_STATE / "diagnostics" / (name + ".stderr"), r.stderr)
        # Never dump responses that might contain generated access tokens.
        raise RuntimeError(
            f"{Path(str(args[0])).name} exited {r.returncode}; stderr sha256="
            + hashlib.sha256(r.stderr).hexdigest()
        )
    return r.stdout


def now():
    return dt.datetime.now(dt.UTC)


def read_owner(state, *, expired_ok=False):
    if sys.platform != "linux":
        raise ValueError("Service stages require the newly authorized Linux VM")
    if state.is_symlink() or state.resolve() != state or state.stat().st_mode & 0o077:
        raise ValueError("State must be a canonical private directory")
    owner = json.loads((state / "owner.json").read_text())
    if owner["hostname"] != socket.gethostname() or owner["uid"] != os.getuid():
        raise ValueError("State belongs to another machine/user")
    if not expired_ok and now() >= dt.datetime.fromisoformat(owner["expires_at"]):
        raise ValueError("Disposable lab authorization expired")
    return owner


def unit(state, owner, suffix, args, *, user=False):
    name = owner["unit_prefix"] + "-" + suffix
    command = [
        "sudo",
        "systemd-run",
        "--unit",
        name,
        "--property=Restart=no",
        "--property=KillMode=control-group",
    ]
    if user:
        command += ["--property=User=" + owner["username"], "--property=SupplementaryGroups=docker"]
    with (state / "units.jsonl").open("a") as f:
        f.write(json.dumps({"unit": name, "argv": list(map(str, args))}) + "\n")
    run(command + ["--", *map(str, args)])


def kubectl(state, *args, data=None, timeout=120):
    return run(
        [state / "bin/k3s", "kubectl", "--kubeconfig", state / "kubeconfig", *args],
        data=data,
        timeout=timeout,
    )


def apply(state, objects):
    kubectl(
        state,
        "apply",
        "-f",
        "-",
        data=json.dumps({"apiVersion": "v1", "kind": "List", "items": objects}).encode(),
    )


def request(state, owner, path, body=None, *, method=None):
    headers = {"PRIVATE-TOKEN": (state / "gitlab-admin.token").read_text().strip()}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        owner["gitlab_origin"] + "/api/v4" + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method=method,
    )
    with private_open(state, req) as r:
        return json.load(r)


def create_tls(tls, ip):
    """Create strict-X509-compatible private demo certificates in an empty directory."""
    run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:3072",
            "-nodes",
            "-days",
            "2",
            "-subj",
            "/CN=Disposable DevSecOps Demo CA",
            "-keyout",
            tls / "ca.key",
            "-out",
            tls / "ca.crt",
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-addext",
            "keyUsage=critical,keyCertSign,cRLSign",
            "-addext",
            "subjectKeyIdentifier=hash",
            "-addext",
            "authorityKeyIdentifier=keyid:always",
        ]
    )
    run(
        [
            "openssl",
            "req",
            "-newkey",
            "rsa:3072",
            "-nodes",
            "-subj",
            "/CN=localhost",
            "-keyout",
            tls / "server.key",
            "-out",
            tls / "server.csr",
        ]
    )
    write(
        tls / "extensions.cnf",
        f"subjectAltName=DNS:localhost,IP:127.0.0.1,IP:{ip}\nbasicConstraints=critical,CA:FALSE\nextendedKeyUsage=serverAuth\n",
    )
    run(
        [
            "openssl",
            "x509",
            "-req",
            "-in",
            tls / "server.csr",
            "-CA",
            tls / "ca.crt",
            "-CAkey",
            tls / "ca.key",
            "-CAcreateserial",
            "-days",
            "2",
            "-out",
            tls / "server.crt",
            "-extfile",
            tls / "extensions.cnf",
        ]
    )
    for p in tls.iterdir():
        p.chmod(0o600)


def prepare(args):
    state = args.state
    if sys.platform != "linux":
        raise ValueError("Prepare private runtime state on the newly authorized Linux VM")
    if state.resolve() != state or state.is_relative_to(PLUGIN):
        raise ValueError("State must be canonical and outside plugin source")
    if state.exists() or state.is_symlink():
        raise ValueError("Choose a new private state directory")
    require_classic_store()
    ip = ipaddress.ip_address(args.private_ip)
    if not ip.is_private or ip.is_loopback or ip.version != 4:
        raise ValueError("A private VM IPv4 is required")
    interfaces = json.loads(run(["ip", "-j", "-4", "address", "show"]))
    if str(ip) not in {a["local"] for link in interfaces for a in link.get("addr_info", [])}:
        raise ValueError("The approved private IP is not assigned to this machine")
    expires = dt.datetime.fromisoformat(args.expires_at)
    if expires.tzinfo is None or not now() < expires <= now() + dt.timedelta(hours=8):
        raise ValueError("Explicit expiry within8h required")
    inputs = args.inputs.resolve()
    manifest = json.loads((inputs / "input-manifest.json").read_text())
    required = {
        "base.oci.tar",
        "syft",
        "grype",
        "prometheus_client-0.26.0-py3-none-any.whl",
        "database.tar.zst",
        "latest.json",
        "exception.json",
    }
    if set(manifest["files"]) != required:
        raise ValueError("Input manifest must bind exactly the seven required files")
    for name, expected in manifest["files"].items():
        path = inputs / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("Nonregular input")
        h = hashlib.sha256()
        with path.open("rb") as f:
            while chunk := f.read(1024 * 1024):
                h.update(chunk)
        if h.hexdigest() != expected["sha256"] or path.stat().st_size != expected["bytes"]:
            raise ValueError("Input changed: " + name)
    db = json.loads((inputs / "latest.json").read_text())
    built = dt.datetime.fromisoformat(db["built"].replace("Z", "+00:00"))
    if not built <= now() < built + dt.timedelta(hours=120):
        raise ValueError("The retained vulnerability database has expired")
    if db["checksum"] != "sha256:" + manifest["files"]["database.tar.zst"]["sha256"]:
        raise ValueError("Database archive/listing identity differs")
    digest = args.base_manifest
    if not __import__("re").fullmatch("sha256:[a-f0-9]{64}", digest):
        raise ValueError("Exact OCI manifest required")
    exception, _ = SCAN_POLICY.load_policy(inputs / "exception.json", digest)
    if dt.datetime.fromisoformat(exception["expires_at"]) < expires:
        raise ValueError("Exception expires before lab authorization")
    with tarfile.open(inputs / "base.oci.tar", "r:") as archive:

        def member(d, limit):
            item = archive.getmember("blobs/sha256/" + d.split(":")[1])
            if not item.isfile() or not 0 < item.size <= limit:
                raise ValueError("Invalid OCI metadata")
            raw = archive.extractfile(item).read(limit + 1)
            if "sha256:" + hashlib.sha256(raw).hexdigest() != d:
                raise ValueError("OCI identity mismatch")
            return json.loads(raw)

        image_manifest = member(digest, 4 * 1024 * 1024)
        config_digest = image_manifest["config"]["digest"]
        config = member(config_digest, 2 * 1024 * 1024)
        if (config["os"], config["architecture"]) != ("linux", "amd64"):
            raise ValueError("Linux AMD64 base required")
    state.mkdir(mode=0o700, parents=True)
    (state / "inputs").mkdir(mode=0o700)
    for name in required:
        shutil.copyfile(inputs / name, state / "inputs" / name)
        (state / "inputs" / name).chmod(0o600)
    for name in ("syft", "grype"):
        (state / "inputs" / name).chmod(0o700)
        verify_tool(state, name)
    write(state / "input-manifest.json", manifest)
    owner = {
        "schema_version": 1,
        "owner_id": str(uuid.uuid4()),
        "hostname": socket.gethostname(),
        "uid": os.getuid(),
        "username": pwd.getpwuid(os.getuid()).pw_name,
        "private_ip": str(ip),
        "created_at": now().isoformat(),
        "expires_at": expires.isoformat(),
        "unit_prefix": "devsecops-portable-" + secrets.token_hex(4),
        "base_manifest_digest": digest,
        "base_config_digest": config_digest,
        "gitlab_origin": f"https://{ip}:8443",
        "registry": f"{ip}:5050",
    }
    write(state / "owner.json", owner)
    (state / "tls").mkdir(mode=0o700)
    tls = state / "tls"
    create_tls(tls, str(ip))
    write(state / "root.password", secrets.token_urlsafe(28) + "\n")
    for d in ("gitlab/config", "gitlab/logs", "gitlab/data", "runner", "builds", "grype-db"):
        (state / d).mkdir(parents=True, mode=0o700)
    rb = f"""external_url '{owner["gitlab_origin"]}'
letsencrypt['enable'] = false
nginx['ssl_certificate'] = '/etc/gitlab/demo-tls/server.crt'
nginx['ssl_certificate_key'] = '/etc/gitlab/demo-tls/server.key'
gitlab_rails['initial_root_password'] = {json.dumps((state / "root.password").read_text().strip())}
registry_external_url 'https://{owner["registry"]}'
registry_nginx['ssl_certificate'] = '/etc/gitlab/demo-tls/server.crt'
registry_nginx['ssl_certificate_key'] = '/etc/gitlab/demo-tls/server.key'
prometheus_monitoring['enable'] = false
"""
    write(state / "gitlab/config/gitlab.rb", rb)
    write(
        state / "registries.yaml", {"configs": {owner["registry"]: {"tls": {"ca_file": str(tls / "ca.crt")}}}}
    )
    print("Prepared private VM state and credentials; no services started.")


def services(state, owner):
    require_classic_store()
    for exe in ("docker", "git", "openssl", "skopeo"):
        if not shutil.which(exe):
            raise ValueError("Required VM tool is missing: " + exe)
    if (state / "gitlab-container.json").exists() or (state / "units.jsonl").exists():
        raise ValueError("Services already attempted; inspect retained state before retry")
    run(["docker", "buildx", "version"])
    image = json.loads((state / "downloads/gitlab-image.json").read_text())[0]
    selected = next(x for x in image["RepoDigests"] if x.startswith("gitlab/gitlab-ce@"))
    cid = (
        run(
            [
                "docker",
                "create",
                "--name",
                owner["unit_prefix"] + "-gitlab",
                "--label",
                "devsecops.owner=" + owner["owner_id"],
                "--hostname",
                "gitlab",
                "--shm-size",
                "256m",
                "--publish",
                owner["private_ip"] + ":8443:8443",
                "--publish",
                owner["private_ip"] + ":5050:5050",
                "--volume",
                str(state / "gitlab/config") + ":/etc/gitlab",
                "--volume",
                str(state / "gitlab/logs") + ":/var/log/gitlab",
                "--volume",
                str(state / "gitlab/data") + ":/var/opt/gitlab",
                "--volume",
                str(state / "tls") + ":/etc/gitlab/demo-tls:ro",
                selected,
            ]
        )
        .decode()
        .strip()
    )
    write(state / "gitlab-container.json", {"id": cid, "image": selected, "owner_id": owner["owner_id"]})
    run(["docker", "start", cid])
    # Docker registry CA is scoped to this new lab registry only. No daemon restart.
    trust = Path("/etc/docker/certs.d") / owner["registry"]
    run(["sudo", "mkdir", "-p", trust])
    run(["sudo", "install", "-m", "0644", state / "tls/ca.crt", trust / "ca.crt"])
    unit(
        state,
        owner,
        "k3s",
        [
            state / "bin/k3s",
            "server",
            "--data-dir",
            state / "k3s",
            "--write-kubeconfig",
            state / "kubeconfig",
            "--write-kubeconfig-mode",
            "0600",
            "--private-registry",
            state / "registries.yaml",
            "--node-name",
            "portable-demo",
            "--node-ip",
            owner["private_ip"],
            "--advertise-address",
            owner["private_ip"],
            "--tls-san",
            "127.0.0.1",
            "--tls-san",
            owner["private_ip"],
            "--disable",
            "traefik",
            "--disable",
            "servicelb",
            "--disable",
            "metrics-server",
        ],
    )
    print("Started the exact owned GitLab container and native k3s unit. NetworkPolicy remains enabled.")


def readiness_timeout(deadline, maximum):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError("Service readiness deadline elapsed")
    return min(maximum, remaining)


def gitlab_readiness(state, owner, deadline):
    # GitLab monitoring endpoints intentionally admit loopback callers by default.
    # Keep that whitelist intact: verify the external UI and the owned container's
    # local all-checks endpoint, using the same CA and no redirect/proxy bypass.
    record = json.loads((state / "gitlab-container.json").read_text())
    cid = record["id"]
    if not re.fullmatch(r"[a-f0-9]{64}", cid) or record["owner_id"] != owner["owner_id"]:
        raise ValueError("GitLab container ownership record differs")
    inspected = json.loads(run(["docker", "inspect", cid], timeout=readiness_timeout(deadline, 10)))[0]
    if (
        inspected["Id"] != cid
        or inspected["Config"]["Labels"].get("devsecops.owner") != owner["owner_id"]
        or inspected["Config"]["Image"] != record["image"]
    ):
        raise ValueError("GitLab container ownership/image differs")
    if not inspected["State"]["Running"]:
        raise RuntimeError("Owned GitLab container is not running")
    with private_open(
        state, owner["gitlab_origin"] + "/users/sign_in", timeout=readiness_timeout(deadline, 10)
    ) as response:
        if response.status != 200:
            raise RuntimeError("GitLab sign-in is not ready")
    seconds = readiness_timeout(deadline, 10)
    result = json.loads(
        run(
            [
                "docker",
                "exec",
                cid,
                "curl",
                "--silent",
                "--show-error",
                "--fail",
                "--noproxy",
                "*",
                "--max-time",
                str(min(8, seconds)),
                "--cacert",
                "/etc/gitlab/demo-tls/ca.crt",
                "https://127.0.0.1:8443/-/readiness?all=1",
            ],
            timeout=seconds,
        )
    )
    checks = {key: value for key, value in result.items() if key != "status"}
    if (
        result.get("status") != "ok"
        or not {"db_check", "cache_check", "gitaly_check"} <= checks.keys()
        or any(
            not isinstance(rows, list)
            or not rows
            or any(not isinstance(row, dict) or row.get("status") != "ok" for row in rows)
            for rows in checks.values()
        )
    ):
        raise RuntimeError("GitLab dependency readiness checks are not all healthy")
    readiness_timeout(deadline, 1)
    return {
        "container_id": cid,
        "external_sign_in_status": 200,
        "local_dependency_checks": sorted(checks),
        "all_dependency_checks_ok": True,
    }


def wait_services(state, owner):
    deadline = time.monotonic() + 1200
    while time.monotonic() < deadline:
        gitlab_ready = None
        kube_ready = False
        try:
            if (state / "kubeconfig").exists():
                run(
                    ["sudo", "chown", str(owner["uid"]), state / "kubeconfig"],
                    timeout=readiness_timeout(deadline, 10),
                )
                kube_ready = (
                    kubectl(state, "get", "--raw=/readyz", timeout=readiness_timeout(deadline, 10)).strip()
                    == b"ok"
                )
            gitlab_ready = gitlab_readiness(state, owner, deadline)
        except ssl.SSLCertVerificationError:
            raise
        except urllib.error.URLError as error:
            if isinstance(error.reason, ssl.SSLCertVerificationError):
                raise
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            pass
        if gitlab_ready and kube_ready and time.monotonic() < deadline:
            write(
                state / "services-ready.json",
                {
                    "observed_at": now().isoformat(),
                    "gitlab_ready": True,
                    "kubernetes_ready": True,
                    "gitlab_observation": gitlab_ready,
                },
            )
            print("GitLab and Kubernetes ready.")
            return
        time.sleep(max(0, min(5, deadline - time.monotonic())))
    raise RuntimeError(
        "20-minute service readiness limit exceeded; owned state retained for inspection/cleanup"
    )


def configure(state, owner):
    require_classic_store()
    if (state / "project.json").exists():
        raise ValueError("Project already configured")
    cid = json.loads((state / "gitlab-container.json").read_text())["id"]
    # GitLab19.5 exposes this exact JSONB accessor on ApplicationSetting. Set
    # security controls through the supported model and verify persisted values
    # before issuing the bootstrap token or creating any project/runner.
    ruby = (
        "a=ApplicationSetting.current;"
        "a.update!(signup_enabled:false,vscode_extension_marketplace_single_origin_fallback_enabled:false);"
        "a.reload;raise 'Demo instance controls did not persist' unless "
        "a.signup_enabled == false && a.vscode_extension_marketplace_single_origin_fallback_enabled == false;"
        "u=User.find_by_username!('root');"
        "t=u.personal_access_tokens.create!(name:'disposable-demo-bootstrap',scopes:['api'],expires_at:Date.today+1);"
        "puts t.token"
    )
    token = run(["docker", "exec", cid, "gitlab-rails", "runner", ruby], timeout=180).decode().strip()
    if "\n" in token or len(token) > 200:
        raise ValueError("Unexpected token creation response")
    write(state / "gitlab-admin.token", token + "\n")
    request(state, owner, "/application/settings", {"signup_enabled": False}, method="PUT")
    project = request(
        state,
        owner,
        "/projects",
        {
            "name": "Mission telemetry",
            "path": "mission-demo",
            "visibility": "private",
            "initialize_with_readme": True,
            "default_branch": "main",
            "shared_runners_enabled": False,
        },
    )
    write(state / "project.json", project)
    pid = project["id"]
    try:
        protection = request(state, owner, f"/projects/{pid}/protected_branches/main")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        protection = request(
            state,
            owner,
            f"/projects/{pid}/protected_branches",
            {"name": "main", "push_access_level": 40, "merge_access_level": 40},
        )
    if protection.get("name") != "main" or any(
        access.get("access_level", 0) < 40
        for key in ("push_access_levels", "merge_access_levels")
        for access in protection.get(key, [])
    ):
        raise ValueError("Main must be explicitly protected for trusted Maintainers only")
    write(state / "main-protection.json", protection)
    deploy = request(
        state,
        owner,
        f"/projects/{pid}/access_tokens",
        {
            "name": "deployment-writer",
            "scopes": ["api"],
            "access_level": 40,
            "expires_at": (now() + dt.timedelta(days=1)).date().isoformat(),
        },
    )
    write(state / "deployment-writer.json", deploy)
    request(
        state,
        owner,
        f"/projects/{pid}/variables",
        {"key": "DEPLOY_TOKEN", "value": deploy["token"], "masked": True, "protected": True},
    )
    pull = request(
        state,
        owner,
        f"/projects/{pid}/deploy_tokens",
        {
            "name": "runtime-read",
            "scopes": ["read_repository", "read_registry"],
            "expires_at": owner["expires_at"],
        },
    )
    write(state / "runtime-read.json", pull)
    runner = request(
        state,
        owner,
        "/user/runners",
        {
            "runner_type": "project_type",
            "project_id": pid,
            "description": "Dedicated trusted disposable demo",
            "tag_list": ["trusted-demo"],
            "run_untagged": False,
            "access_level": "ref_protected",
        },
    )
    write(state / "runner/registration.json", runner)
    env = [
        "DEMO_STATE=" + str(state),
        "GRYPE_DB_CACHE_DIR=" + str(state / "grype-db"),
        "GRYPE_DB_AUTO_UPDATE=false",
        "GRYPE_CHECK_FOR_APP_UPDATE=false",
        "SYFT_CHECK_FOR_APP_UPDATE=false",
        "DOCKER_BUILDKIT=1",
        "DOCKER_CONFIG=" + str(state / "runner/docker"),
    ]
    cfg = f"""concurrent = 1
check_interval = 3
[[runners]]
  name = "disposable-trusted-mission"
  url = {json.dumps(owner["gitlab_origin"])}
  token = {json.dumps(runner["token"])}
  executor = "shell"
  shell = "bash"
  tls-ca-file = {json.dumps(str(state / "tls/ca.crt"))}
  builds_dir = {json.dumps(str(state / "builds"))}
  environment = {json.dumps(env)}
"""
    write(state / "runner/config.toml", cfg)
    import_base(state, owner)
    verify_tool(state, "grype")
    run(
        [
            "env",
            "GRYPE_DB_CACHE_DIR=" + str(state / "grype-db"),
            "GRYPE_DB_AUTO_UPDATE=false",
            state / "inputs/grype",
            "db",
            "import",
            state / "inputs/database.tar.zst",
        ],
        timeout=300,
    )
    unit(
        state,
        owner,
        "runner",
        [
            state / "bin/gitlab-runner",
            "run",
            "--config",
            state / "runner/config.toml",
            "--working-directory",
            state / "runner",
        ],
        user=True,
    )
    actions = []
    for sub, source in [
        ("app", PLUGIN / "demo/software-factory/apps/mission-status"),
        ("charts/mission-status", PLUGIN / "demo/software-factory/charts/mission-status"),
    ]:
        for p in sorted(source.rglob("*")):
            if not p.is_file() or p.is_symlink() or "__pycache__" in p.parts:
                continue
            if p.name == "Dockerfile":
                raw = p.read_text()
                raw = raw[raw.index("# F0 must") :]
                raw = "ARG BASE\nFROM ${BASE}\n" + raw
            else:
                raw = p.read_text()
            actions.append(
                {
                    "action": "create",
                    "file_path": sub + "/" + p.relative_to(source).as_posix(),
                    "content": raw,
                }
            )
    actions.append(
        {"action": "create", "file_path": "ci/pipeline.py", "content": (HERE / "pipeline.py").read_text()}
    )
    actions.append(
        {
            "action": "create",
            "file_path": "ci/scan_policy.py",
            "content": (HERE / "scan_policy.py").read_text(),
        }
    )
    actions.append(
        {
            "action": "create",
            "file_path": "ci/supplychain.lock.json",
            "content": (PLUGIN / "supplychain.lock.json").read_text(),
        }
    )
    ci = """workflow:
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
    - when: never
stages: [build, scan, publish]
default:
  tags: [trusted-demo]
  timeout: 15m
build:
  stage: build
  script: ["python3 ci/pipeline.py build"]
  artifacts:
    paths: [artifacts/]
    expire_in: 1 day
scan:
  stage: scan
  script: ["python3 ci/pipeline.py scan"]
  artifacts:
    when: always
    paths: [artifacts/]
    expire_in: 1 day
publish:
  stage: publish
  script: ["python3 ci/pipeline.py publish"]
  artifacts:
    paths: [artifacts/]
    expire_in: 1 day
"""
    actions.append({"action": "create", "file_path": ".gitlab-ci.yml", "content": ci})
    commit = request(
        state,
        owner,
        f"/projects/{pid}/repository/commits",
        {
            "branch": "main",
            "commit_message": "Build and scan the synthetic mission service",
            "actions": actions,
        },
    )
    write(state / "build-commit.json", {"id": commit["id"], "project_id": pid})
    print("Created the private project, scoped runner and real build/scan/publish pipeline.")


def cluster(state, owner):
    for ns in ("argocd", "observability", "mission-demo"):
        apply(state, [{"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": ns}}])
    kubectl(
        state,
        "apply",
        "--server-side",
        "-n",
        "argocd",
        "-f",
        str(state / "downloads/argocd-install.yaml"),
        timeout=180,
    )
    tls = {
        "tls.crt": base64.b64encode((state / "tls/server.crt").read_bytes()).decode(),
        "tls.key": base64.b64encode((state / "tls/server.key").read_bytes()).decode(),
    }
    objects = [
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "type": "kubernetes.io/tls",
            "metadata": {"name": "argocd-server-tls", "namespace": "argocd"},
            "data": tls,
        },
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "type": "kubernetes.io/tls",
            "metadata": {"name": "prometheus-tls", "namespace": "observability"},
            "data": tls,
        },
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "argocd-tls-certs-cm", "namespace": "argocd"},
            "data": {owner["private_ip"]: (state / "tls/ca.crt").read_text()},
        },
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "argocd-cm", "namespace": "argocd"},
            "data": {
                "accounts.demo-reader": "apiKey",
                "timeout.reconciliation": "20s",
                "application.resourceTrackingMethod": "annotation",
            },
        },
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "argocd-rbac-cm", "namespace": "argocd"},
            "data": {
                "policy.csv": "p, role:demo-read, applications, get, demo/mission-demo, allow\ng, demo-reader, role:demo-read\n",
                "policy.default": "",
            },
        },
        {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "AppProject",
            "metadata": {"name": "demo", "namespace": "argocd"},
            "spec": {
                "sourceRepos": [owner["gitlab_origin"] + "/root/mission-demo.git"],
                "destinations": [{"namespace": "mission-demo", "server": "https://kubernetes.default.svc"}],
                "namespaceResourceWhitelist": [{"group": "*", "kind": "*"}],
            },
        },
    ]
    pull = json.loads((state / "runtime-read.json").read_text())
    objects.append(
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": "mission-repository",
                "namespace": "argocd",
                "labels": {"argocd.argoproj.io/secret-type": "repository"},
            },
            "stringData": {
                "type": "git",
                "url": owner["gitlab_origin"] + "/root/mission-demo.git",
                "username": pull["username"],
                "password": pull["token"],
            },
        }
    )
    auth = base64.b64encode((pull["username"] + ":" + pull["token"]).encode()).decode()
    for ns in ("mission-demo", "observability"):
        objects.append(
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "type": "kubernetes.io/dockerconfigjson",
                "metadata": {"name": "mission-registry", "namespace": ns},
                "stringData": {
                    ".dockerconfigjson": json.dumps({"auths": {owner["registry"]: {"auth": auth}}})
                },
            }
        )
    prom_image = json.loads((state / "downloads/prometheus-image.json").read_text())[0]
    prom_ref = next(x for x in prom_image["RepoDigests"] if x.startswith("prom/prometheus@"))
    config = {
        "global": {"scrape_interval": "5s", "evaluation_interval": "5s"},
        "scrape_configs": [
            {
                "job_name": "mission-api",
                "static_configs": [
                    {
                        "targets": ["mission-api.mission-demo.svc:9090"],
                        "labels": {"namespace": "mission-demo", "service": "mission-api"},
                    }
                ],
            }
        ],
    }
    objects += [
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "prometheus-config", "namespace": "observability"},
            "data": {
                "prometheus.yml": json.dumps(config),
                "web.yml": json.dumps(
                    {"tls_server_config": {"cert_file": "/tls/tls.crt", "key_file": "/tls/tls.key"}}
                ),
            },
        },
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "prometheus", "namespace": "observability"},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {"app": "prometheus"}},
                "template": {
                    "metadata": {"labels": {"app": "prometheus"}},
                    "spec": {
                        "securityContext": {"fsGroup": 65534},
                        "containers": [
                            {
                                "name": "prometheus",
                                "image": prom_ref,
                                "args": [
                                    "--config.file=/config/prometheus.yml",
                                    "--web.config.file=/config/web.yml",
                                    "--storage.tsdb.path=/data",
                                    "--storage.tsdb.retention.time=8h",
                                ],
                                "ports": [{"containerPort": 9090}],
                                "resources": {
                                    "requests": {"cpu": "100m", "memory": "256Mi"},
                                    "limits": {"memory": "1Gi"},
                                },
                                "volumeMounts": [
                                    {"name": "config", "mountPath": "/config", "readOnly": True},
                                    {"name": "tls", "mountPath": "/tls", "readOnly": True},
                                    {"name": "data", "mountPath": "/data"},
                                ],
                            }
                        ],
                        "volumes": [
                            {"name": "config", "configMap": {"name": "prometheus-config"}},
                            {"name": "tls", "secret": {"secretName": "prometheus-tls"}},
                            {"name": "data", "emptyDir": {}},
                        ],
                    },
                },
            },
        },
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": "prometheus", "namespace": "observability"},
            "spec": {"selector": {"app": "prometheus"}, "ports": [{"port": 9090, "targetPort": 9090}]},
        },
        {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": {"name": "demo-reader", "namespace": "mission-demo"},
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "Role",
            "metadata": {"name": "demo-reader", "namespace": "mission-demo"},
            "rules": [
                {"apiGroups": [""], "resources": ["pods", "services", "events"], "verbs": ["get", "list"]},
                {
                    "apiGroups": ["apps"],
                    "resources": ["deployments", "replicasets"],
                    "verbs": ["get", "list"],
                },
                {
                    "apiGroups": ["discovery.k8s.io"],
                    "resources": ["endpointslices"],
                    "verbs": ["get", "list"],
                },
                {
                    "apiGroups": ["networking.k8s.io"],
                    "resources": ["networkpolicies"],
                    "verbs": ["get", "list"],
                },
            ],
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "RoleBinding",
            "metadata": {"name": "demo-reader", "namespace": "mission-demo"},
            "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "demo-reader"},
            "subjects": [{"kind": "ServiceAccount", "name": "demo-reader", "namespace": "mission-demo"}],
        },
    ]
    apply(state, objects)
    kubectl(
        state, "rollout", "status", "deployment/argocd-server", "-n", "argocd", "--timeout=300s", timeout=310
    )
    kubectl(
        state,
        "rollout",
        "status",
        "deployment/argocd-repo-server",
        "-n",
        "argocd",
        "--timeout=300s",
        timeout=310,
    )
    kubectl(
        state,
        "rollout",
        "status",
        "deployment/prometheus",
        "-n",
        "observability",
        "--timeout=300s",
        timeout=310,
    )
    for suffix, ns, svc, port, remote in [
        ("argo", "argocd", "argocd-server", "8444", "443"),
        ("prom", "observability", "prometheus", "8445", "9090"),
    ]:
        unit(
            state,
            owner,
            suffix,
            [
                state / "bin/k3s",
                "kubectl",
                "--kubeconfig",
                state / "kubeconfig",
                "port-forward",
                "--address",
                "127.0.0.1",
                "-n",
                ns,
                "service/" + svc,
                port + ":" + remote,
            ],
            user=True,
        )
    print("Configured actual Argo CD, Prometheus TLS, scoped read RBAC and loopback-only UI forwards.")


def phase(state, owner, name):
    pid = json.loads((state / "project.json").read_text())["id"]
    if name == "baseline":
        build = json.loads((state / "build-commit.json").read_text())
        pipelines = request(state, owner, f"/projects/{pid}/pipelines?sha={build['id']}&ref=main")
        if len(pipelines) != 1 or pipelines[0]["status"] != "success":
            raise ValueError("Require the actual successful build/scan/publish pipeline first")
        write(state / "successful-pipeline.json", pipelines[0])
        branches = request(state, owner, f"/projects/{pid}/repository/branches/deploy")
        revision = branches["commit"]["id"]
    else:
        path = f"/projects/{pid}/repository/files/" + urllib.parse.quote("deploy/values.json", safe="")
        f = request(state, owner, path + "?ref=deploy")
        values = json.loads(base64.b64decode(f["content"]))
        expected = 9090 if name == "fault" else 9091
        if values["networkPolicy"]["metricsPort"] != expected:
            raise ValueError("Phase does not follow expected Git policy state")
        values["networkPolicy"]["metricsPort"] = 9091 if name == "fault" else 9090
        result = request(
            state,
            owner,
            f"/projects/{pid}/repository/commits",
            {
                "branch": "deploy",
                "commit_message": (
                    "Inject metrics-only policy fault"
                    if name == "fault"
                    else "Restore metrics policy permission"
                )
                + " [skip ci]",
                "actions": [
                    {
                        "action": "update",
                        "file_path": "deploy/values.json",
                        "last_commit_id": f["last_commit_id"],
                        "content": json.dumps(values, indent=2),
                    }
                ],
            },
        )
        revision = result["id"]
    app = {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {"name": "mission-demo", "namespace": "argocd"},
        "spec": {
            "project": "demo",
            "source": {
                "repoURL": owner["gitlab_origin"] + "/root/mission-demo.git",
                "targetRevision": revision,
                "path": "charts/mission-status",
                "helm": {"releaseName": "mission-api", "valueFiles": ["../../deploy/values.json"]},
            },
            "destination": {"server": "https://kubernetes.default.svc", "namespace": "mission-demo"},
            "syncPolicy": {
                "automated": {"prune": True, "selfHeal": True},
                "syncOptions": ["CreateNamespace=true"],
            },
        },
    }
    apply(state, [app])
    write(
        state / f"phase-{name}.json",
        {"phase": name, "expected_revision": revision, "requested_at": now().isoformat()},
    )
    print(
        json.dumps(
            {
                "phase": name,
                "git_revision": revision,
                "observation": "pending actual Argo sync and60-second metrics window",
            }
        )
    )


def credentials(state, owner):
    if (state / "credentials.env").exists():
        raise ValueError("Scoped credentials already generated; retain them")
    lifetime = int((dt.datetime.fromisoformat(owner["expires_at"]) - now()).total_seconds())
    if lifetime < 120:
        raise ValueError("Insufficient remaining lab lifetime")
    token = (
        kubectl(state, "create", "token", "demo-reader", "-n", "mission-demo", f"--duration={lifetime}s")
        .decode()
        .strip()
    )
    kube = json.loads(kubectl(state, "config", "view", "--raw", "-o", "json"))
    write(
        state / "kubernetes-ca.crt",
        base64.b64decode(kube["clusters"][0]["cluster"]["certificate-authority-data"]),
    )
    secret = json.loads(
        kubectl(state, "get", "secret", "argocd-initial-admin-secret", "-n", "argocd", "-o", "json")
    )
    password = base64.b64decode(secret["data"]["password"]).decode()
    write(state / "argocd-admin.password", password + "\n")

    def argo(path, body, bearer=None):
        headers = {"Content-Type": "application/json"}
        if bearer:
            headers["Authorization"] = "Bearer " + bearer
        req = urllib.request.Request(
            "https://127.0.0.1:8444" + path, data=json.dumps(body).encode(), headers=headers
        )
        with private_open(state, req, timeout=30) as r:
            return json.load(r)

    session = argo("/api/v1/session", {"username": "admin", "password": password})
    lifetime = int((dt.datetime.fromisoformat(owner["expires_at"]) - now()).total_seconds())
    if lifetime < 120:
        raise ValueError("Insufficient remaining lab lifetime")
    reader = argo("/api/v1/account/demo-reader/token", {"expiresIn": lifetime}, session["token"])
    write(
        state / "credentials.env",
        "export DEVSECOPS_ARGOCD_READ_TOKEN="
        + shlex.quote(reader["token"])
        + "\n"
        + "export DEVSECOPS_KUBERNETES_READ_TOKEN="
        + shlex.quote(token)
        + "\n",
    )
    # Record server-issued JWT expiry claims separately from the requested lifetime.
    expiries = {}
    for name, value in (("kubernetes", token), ("argocd", reader["token"])):
        payload = value.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        expiries[name] = {"issued_expiry_epoch": claims.get("exp"), "lease_expires_at": owner["expires_at"]}
    write(state / "reader-expiries.json", expiries)
    print("Saved scoped reader tokens and UI passwords privately; no token values printed.")


def observer_config(state, owner, name):
    phase_record = json.loads((state / f"phase-{name}.json").read_text())
    config = {
        "schema_version": 1,
        "profile": "portable-gitops-v1",
        "argocd": {
            "connection": {
                "origin": "https://127.0.0.1:8444",
                "token_env": "DEVSECOPS_ARGOCD_READ_TOKEN",
                "ca_bundle": str(state / "tls/ca.crt"),
            },
            "project": "demo",
            "application_namespace": "argocd",
            "application": "mission-demo",
            "expected_revision": phase_record["expected_revision"],
        },
        "kubernetes": {
            "connection": {
                "origin": "https://127.0.0.1:6443",
                "token_env": "DEVSECOPS_KUBERNETES_READ_TOKEN",
                "ca_bundle": str(state / "kubernetes-ca.crt"),
            },
            "namespace": "mission-demo",
            "deployment": "mission-api",
            "service": "mission-api",
        },
        "prometheus": {
            "connection": {"origin": "https://127.0.0.1:8445", "ca_bundle": str(state / "tls/ca.crt")},
            "job": "mission-api",
            "instance": "mission-api.mission-demo.svc:9090",
            "window_seconds": 60,
            "step_seconds": 15,
            "max_sample_age_seconds": 45,
            "minimum_samples": 3,
        },
    }
    write(state / f"observer-{name}.json", config)


def network_probe_script(role, phase, expected_ip):
    """Observe source convergence and three stable samples without masking failures."""
    if role not in ("allowed", "denied") or phase not in ("baseline", "fault", "recovered"):
        raise ValueError("Unknown probe role or phase")
    if not isinstance(ipaddress.ip_address(expected_ip), ipaddress.IPv4Address):
        raise ValueError("The probe requires one observed IPv4 Service address")
    return (
        "EXPECTED_IP="
        + repr(expected_ip)
        + "\nROLE="
        + repr(role)
        + "\nPHASE="
        + repr(phase)
        + "\n"
        + r"""

import datetime as dt,http.client,json,queue,socket,threading,time
start=time.monotonic();deadline=start+25
result={'role':ROLE,'phase':PHASE,'attempts':[],'startup_converged':False,'final_observations':[],'expected_behavior_observed':False}
def emit():
 result['elapsed_seconds']=round(time.monotonic()-start,4);print(json.dumps(result))
q=queue.Queue()
def resolve():
 try:q.put(sorted({x[4][0] for x in socket.getaddrinfo('mission-api.mission-demo.svc',0,socket.AF_INET,socket.SOCK_STREAM)}))
 except OSError:q.put([])
threading.Thread(target=resolve,daemon=True).start()
try:ips=q.get(timeout=3)
except queue.Empty:ips=[]
if ips!=[EXPECTED_IP]:
 result['dns']='failed-or-changed';emit();raise SystemExit(0)
result['resolved_ip']=ips[0]
def request(port,path,until):
 remaining=min(until,deadline)-time.monotonic()
 if remaining<=0:return {'category':'deadline-exceeded'}
 try:s=socket.create_connection((ips[0],port),timeout=min(1,remaining))
 except TimeoutError:return {'category':'connect-timeout'}
 except ConnectionRefusedError:return {'category':'connection-refused'}
 except OSError:return {'category':'other-network-error'}
 c=http.client.HTTPConnection(ips[0],port,timeout=min(1,max(.001,min(until,deadline)-time.monotonic())));c.sock=s
 try:
  s.settimeout(min(1,max(.001,min(until,deadline)-time.monotonic())))
  c.request('GET','/'+path)
  s.settimeout(min(1,max(.001,min(until,deadline)-time.monotonic())))
  response=c.getresponse();value={'category':'http','status':response.status}
 except (OSError,http.client.HTTPException):value={'category':'connected-without-http-response'}
 finally:c.close()
 if time.monotonic()>min(until,deadline):return {'category':'deadline-exceeded'}
 return value
healthy={'category':'http','status':200};failures={'connect-timeout','connection-refused'}
if ROLE=='allowed':
 until=start+15
 while time.monotonic()<until:
  value=request(8080,'health',until)
  result['attempts'].append({'elapsed_seconds':round(time.monotonic()-start,4),'health':value})
  if value==healthy:result['startup_converged']=True;break
  if value['category'] not in failures:break
  time.sleep(min(.5,max(0,until-time.monotonic())))
else:
 settle_started=time.monotonic()
 time.sleep(max(0,start+15-settle_started))
 result['startup_settle_seconds']=round(time.monotonic()-settle_started,4)
 result['startup_elapsed_seconds']=round(time.monotonic()-start,4)
 result['startup_converged']=time.monotonic()<deadline
if result['startup_converged']:
 for index in range(3):
  row={'observed_at':dt.datetime.now(dt.UTC).isoformat(),'elapsed_seconds':round(time.monotonic()-start,4),'health':request(8080,'health',deadline),'metrics':request(9090,'metrics',deadline)}
  result['final_observations'].append(row)
  valid=row['health']==healthy and (row['metrics']['category'] in failures if PHASE=='fault' else row['metrics']==healthy) if ROLE=='allowed' else all(row[key]['category'] in failures for key in ('health','metrics'))
  if not valid:break
  if index<2:time.sleep(min(1,max(0,deadline-time.monotonic())))
 result['expected_behavior_observed']=len(result['final_observations'])==3 and valid and time.monotonic()<=deadline
if result['final_observations']:
 result.update({key:result['final_observations'][-1][key] for key in ('health','metrics')})
emit()
"""
    )


def expected_probes(receipts, phase):
    if phase not in ("baseline", "fault", "recovered") or len(receipts) != 2:
        return False
    if [item.get("role") for item in receipts] != ["allowed", "denied"]:
        return False
    failures = {"connect-timeout", "connection-refused"}
    healthy = {"category": "http", "status": 200}
    addresses = []
    for item in receipts:
        result = item["result"]
        if result.get("role") != item["role"] or result.get("phase") != phase:
            return False
        if not result.get("startup_converged") or result.get("elapsed_seconds", 26) > 25:
            return False
        rows = result.get("final_observations", [])
        if len(rows) != 3 or not result.get("resolved_ip"):
            return False
        if item["role"] == "denied" and result.get("startup_elapsed_seconds", 0) < 15:
            return False
        addresses.append(result["resolved_ip"])
        for row in rows:
            if item["role"] == "allowed":
                if row.get("health") != healthy:
                    return False
                if phase == "fault":
                    if row.get("metrics", {}).get("category") not in failures:
                        return False
                elif row.get("metrics") != healthy:
                    return False
            elif any(row.get(key, {}).get("category") not in failures for key in ("health", "metrics")):
                return False
        if any(result.get(key) != rows[-1].get(key) for key in ("health", "metrics")):
            return False
    return addresses[0] == addresses[1]


def prove(state, owner, name):
    record = json.loads((state / f"phase-{name}.json").read_text())
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        app = json.loads(
            kubectl(state, "get", "application", "mission-demo", "-n", "argocd", "-o", "json", timeout=15)
        )
        status = app.get("status", {})
        if (
            status.get("sync", {}).get("revision") == record["expected_revision"]
            and status.get("sync", {}).get("status") == "Synced"
        ):
            break
        time.sleep(3)
    else:
        raise RuntimeError("Argo did not synchronize the exact Git revision within300s")
    kubectl(
        state,
        "rollout",
        "status",
        "deployment/mission-api",
        "-n",
        "mission-demo",
        "--timeout=120s",
        timeout=130,
    )
    deployment = json.loads(
        kubectl(state, "get", "deployment", "mission-api", "-n", "mission-demo", "-o", "json")
    )
    image = deployment["spec"]["template"]["spec"]["containers"][0]["image"]
    if "@sha256:" not in image:
        raise ValueError("Runtime is not digest-pinned")
    service = json.loads(kubectl(state, "get", "service", "mission-api", "-n", "mission-demo", "-o", "json"))
    expected_ip = service["spec"]["clusterIP"]
    attempt = secrets.token_hex(4)
    proof_path = state / f"network-proof-{name}-{attempt}.json"
    if proof_path.exists():
        raise ValueError("Probe attempt output already exists")
    receipts = []
    for role in ("allowed", "denied"):
        job = owner["unit_prefix"] + "-" + name + "-" + attempt + "-" + role
        if (
            len(job) > 63
            or kubectl(
                state, "get", "job", job, "-n", "observability", "--ignore-not-found=true", "-o", "json"
            ).strip()
        ):
            raise ValueError("Probe Job name is invalid or already exists")
        apply(
            state,
            [
                {
                    "apiVersion": "batch/v1",
                    "kind": "Job",
                    "metadata": {
                        "name": job,
                        "namespace": "observability",
                        "labels": {"devsecops.owner": owner["owner_id"]},
                    },
                    "spec": {
                        "backoffLimit": 0,
                        "activeDeadlineSeconds": 30,
                        "template": {
                            "metadata": {
                                "labels": {"app": "prometheus" if role == "allowed" else "untrusted-probe"}
                            },
                            "spec": {
                                "restartPolicy": "Never",
                                "automountServiceAccountToken": False,
                                "imagePullSecrets": [{"name": "mission-registry"}],
                                "securityContext": {
                                    "runAsNonRoot": True,
                                    "runAsUser": 10001,
                                    "runAsGroup": 10001,
                                    "seccompProfile": {"type": "RuntimeDefault"},
                                },
                                "containers": [
                                    {
                                        "name": "probe",
                                        "image": image,
                                        "command": [
                                            "/opt/mission-status/venv/bin/python",
                                            "-c",
                                            network_probe_script(role, name, expected_ip),
                                        ],
                                        "securityContext": {
                                            "allowPrivilegeEscalation": False,
                                            "readOnlyRootFilesystem": True,
                                            "capabilities": {"drop": ["ALL"]},
                                        },
                                    }
                                ],
                            },
                        },
                    },
                }
            ],
        )
        kubectl(
            state,
            "wait",
            "--for=condition=complete",
            "job/" + job,
            "-n",
            "observability",
            "--timeout=45s",
            timeout=50,
        )
        result = json.loads(
            kubectl(state, "logs", "job/" + job, "-n", "observability", "--limit-bytes=32768")
        )
        obj = json.loads(kubectl(state, "get", "job", job, "-n", "observability", "-o", "json"))
        receipts.append({"role": role, "job_uid": obj["metadata"]["uid"], "result": result})
    valid = expected_probes(receipts, name)
    observed = now().isoformat()
    write(
        proof_path,
        {
            "observed_at": observed,
            "phase": name,
            "git_revision": record["expected_revision"],
            "deployment_uid": deployment["metadata"]["uid"],
            "image": image,
            "probes": receipts,
            "expected_behavior_observed": valid,
        },
    )
    if not valid:
        raise RuntimeError("Actual allowed/denied reachability does not match the selected phase")
    observer_config(state, owner, name)
    print(
        json.dumps(
            {
                "phase": name,
                "network_probes": "pass",
                "proof": str(proof_path),
                "not_before": observed,
                "next": "Wait at least75 seconds, then collect the genuine60-second Prometheus window.",
            }
        )
    )


def stop(state, owner):
    # Stop only recorded exact units and the exact owned container. Keep evidence/disks.
    errors = []
    if (state / "units.jsonl").exists():
        for line in reversed((state / "units.jsonl").read_text().splitlines()):
            item = json.loads(line)
            if not item["unit"].startswith(owner["unit_prefix"] + "-"):
                raise ValueError("Foreign unit")
            try:
                run(["sudo", "systemctl", "stop", item["unit"]], timeout=120)
            except (RuntimeError, subprocess.TimeoutExpired):
                errors.append(item["unit"])
    if (state / "gitlab-container.json").exists():
        record = json.loads((state / "gitlab-container.json").read_text())
        cid = record["id"]
        inspected = json.loads(run(["docker", "inspect", cid]))[0]
        if (
            inspected["Id"] != cid
            or inspected["Config"]["Labels"].get("devsecops.owner") != owner["owner_id"]
        ):
            raise ValueError("Foreign container")
        try:
            run(["docker", "stop", "--time", "30", cid], timeout=45)
        except (RuntimeError, subprocess.TimeoutExpired):
            errors.append(cid)
    write(
        state / "stopped.json",
        {
            "observed_at": now().isoformat(),
            "services_stopped": not errors,
            "unconfirmed": errors,
            "azure_resource_group_deleted": False,
        },
    )
    if errors:
        raise RuntimeError(
            "Some service stops remain unconfirmed; retain evidence and complete owned cloud deletion"
        )
    print(
        "Stopped recorded lab services; private evidence retained. Cloud owner must delete the approved resource group."
    )


def main():
    global PRIVATE_STATE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage",
        choices=[
            "prepare",
            "check",
            "services",
            "wait",
            "configure",
            "cluster",
            "credentials",
            "baseline",
            "fault",
            "recovered",
            "prove",
            "stop",
        ],
    )
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--private-ip")
    parser.add_argument("--expires-at")
    parser.add_argument("--inputs", type=Path)
    parser.add_argument("--base-manifest")
    parser.add_argument("--phase", choices=["baseline", "fault", "recovered"])
    args = parser.parse_args()
    os.umask(0o077)
    args.state = args.state.absolute()
    PRIVATE_STATE = args.state
    if args.stage == "prepare":
        return prepare(args)
    owner = read_owner(args.state, expired_ok=args.stage == "stop")
    if args.stage == "check":
        require_classic_store()
        print("Private Linux runtime ownership, expiry and classic Docker store match; no services changed.")
        return
    if args.stage in ("baseline", "fault", "recovered"):
        return phase(args.state, owner, args.stage)
    if args.stage == "prove":
        if args.phase is None:
            raise ValueError("An explicit phase is required")
        return prove(args.state, owner, args.phase)
    return {
        "services": services,
        "wait": wait_services,
        "configure": configure,
        "cluster": cluster,
        "credentials": credentials,
        "stop": stop,
    }[args.stage](args.state, owner)


if __name__ == "__main__":
    main()
