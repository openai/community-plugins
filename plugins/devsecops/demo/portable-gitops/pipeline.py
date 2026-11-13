"""Three actual trusted-runner jobs: offline build/SBOM, scan, then publish/Git."""

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import base64
import urllib.error
import urllib.parse
import urllib.request
import ssl
import secrets
import importlib.util
import re

_policy_spec = importlib.util.spec_from_file_location(
    "devsecops_portable_scan_policy", Path(__file__).resolve().with_name("scan_policy.py")
)
SCAN_POLICY = importlib.util.module_from_spec(_policy_spec)
_policy_spec.loader.exec_module(SCAN_POLICY)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fresh(state, owner):
    now = dt.datetime.now(dt.UTC)
    if now >= dt.datetime.fromisoformat(owner["expires_at"]):
        raise RuntimeError("Disposable lab authorization expired")
    db = json.loads((state / "inputs/latest.json").read_text())
    built = dt.datetime.fromisoformat(db["built"].replace("Z", "+00:00"))
    if not built <= now < built + dt.timedelta(hours=120):
        raise RuntimeError("Retained vulnerability database expired")
    try:
        exception, exception_sha = SCAN_POLICY.load_policy(
            state / "inputs/exception.json", owner["base_manifest_digest"], at=now
        )
    except ValueError as error:
        raise RuntimeError("Invalid exact scan policy: " + str(error)) from error
    return db, exception, exception_sha


def admitted_fresh(state, owner, decision):
    db, policy, exception_sha = fresh(state, owner)
    expected_decision = "temporary-exception" if policy["allowed_high_selectors"] else "passed"
    if decision.get("decision") != expected_decision:
        raise RuntimeError("Scan admission label differs from the exact current policy")
    if exception_sha != decision["exception_sha256"] or db["built"] != decision["database_built"]:
        raise RuntimeError("The scanned exception or database identity changed before publication")


def validate_deployment_chart(api, project, revision):
    """Compare the complete chart inventory and bytes at one immutable Git revision."""
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise RuntimeError("Deployment branch did not return a complete Git revision")
    root = Path("charts/mission-status")
    local = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError("Deployment chart cannot contain symlinks")
        if path.is_file():
            local[path.as_posix()] = path.read_bytes()
    if not local:
        raise RuntimeError("Current source chart is empty")
    seen, remote = set(), set()
    for page in range(1, 12):
        query = urllib.parse.urlencode(
            {"path": root.as_posix(), "ref": revision, "recursive": "true", "per_page": 100, "page": page}
        )
        entries = api(project + "/repository/tree?" + query)
        if not isinstance(entries, list) or len(entries) > 100:
            raise RuntimeError("Invalid deployment chart tree response")
        for entry in entries:
            if not isinstance(entry, dict):
                raise RuntimeError("Invalid deployment chart tree entry")
            name = entry.get("path")
            if (
                not isinstance(name, str)
                or not name.startswith(root.as_posix() + "/")
                or ".." in name.split("/")
                or "\\" in name
                or name in seen
            ):
                raise RuntimeError("Invalid or repeated deployment chart path")
            seen.add(name)
            if len(seen) > 1024:
                raise RuntimeError("Deployment chart tree exceeds the 1024-entry bound")
            if entry.get("type") == "blob" and entry.get("mode") in {"100644", "100755"}:
                remote.add(name)
            elif entry.get("type") != "tree" or entry.get("mode") != "040000":
                raise RuntimeError("Deployment chart tree contains an unsupported entry")
        if len(entries) < 100:
            break
    if remote != set(local):
        raise RuntimeError(
            "Existing deployment chart path set differs; require an explicit reviewed chart update"
        )
    for name, expected in local.items():
        observed = api(
            project + "/repository/files/" + urllib.parse.quote(name, safe="") + "?ref=" + revision
        )
        if base64.b64decode(observed["content"], validate=True) != expected:
            raise RuntimeError("Existing deployment chart differs; require an explicit reviewed chart update")


def verify_tool(state, name):
    expected = json.loads(Path("ci/supplychain.lock.json").read_text())["tools"][name]["platforms"][
        "linux-amd64"
    ]
    path = state / "inputs" / name
    if (
        path.is_symlink()
        or path.stat().st_size != expected["binary_bytes"]
        or sha(path) != expected["binary_sha256"]
    ):
        raise RuntimeError("Scanner executable differs from the committed supply-chain lock")


def build_receipt(artifacts, owner):
    build = json.loads((artifacts / "build.json").read_text())
    if (
        build["source_revision"] != os.environ["CI_COMMIT_SHA"]
        or build["base_manifest"] != owner["base_manifest_digest"]
    ):
        raise RuntimeError("Build artifact belongs to another source or base")
    return build


def scan_binding(artifacts):
    return {
        "build_receipt_sha256": sha(artifacts / "build.json"),
        "sbom_sha256": sha(artifacts / "sbom.json"),
        "scan_sha256": sha(artifacts / "grype.json"),
    }


def run(args, *, output=None, data=None, timeout=600):
    result = subprocess.run(
        args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False
    )
    if output:
        Path(output).write_bytes(result.stdout)
    if result.returncode:
        # Store bounded raw diagnostics outside GitLab artifacts and source.
        directory = Path(os.environ["DEMO_STATE"]) / "diagnostics" / ("ci-" + secrets.token_hex(6))
        directory.mkdir(mode=0o700, parents=True)
        metadata = {}
        for name, content in (("stdout", result.stdout), ("stderr", result.stderr)):
            path = directory / name
            path.write_bytes(content[-1024 * 1024 :])
            path.chmod(0o600)
            metadata[name] = {
                "bytes": len(content),
                "retained_bytes": path.stat().st_size,
                "full_sha256": hashlib.sha256(content).hexdigest(),
                "retained_sha256": sha(path),
            }
        (directory / "receipt.json").write_text(json.dumps(metadata))
        (directory / "receipt.json").chmod(0o600)
        raise RuntimeError(
            f"{Path(args[0]).name} failed ({result.returncode}); private diagnostic {directory.name}; stderr sha256={metadata['stderr']['full_sha256']}"
        )
    return result.stdout


def main():
    state = Path(os.environ["DEMO_STATE"])
    owner = json.loads((state / "owner.json").read_text())
    db, exception, exception_sha = fresh(state, owner)
    stage = sys.argv[1]
    image = "devsecops-mission:" + os.environ["CI_COMMIT_SHA"]
    artifacts = Path("artifacts")
    artifacts.mkdir(exist_ok=True)
    if stage == "build":
        print("Building the retained base offline and generating the actual Syft SBOM.", flush=True)
        app = Path("app")
        (app / "wheelhouse").mkdir(exist_ok=True)
        wheel = state / "inputs/prometheus_client-0.26.0-py3-none-any.whl"
        if (
            hashlib.sha256(wheel.read_bytes()).hexdigest()
            != "fa93d06737aa02bacd05794768508bb97d2fbee28cb3bca04eaae92f0ca953d6"
        ):
            raise RuntimeError("Wheel identity changed")
        (app / "wheelhouse" / wheel.name).write_bytes(wheel.read_bytes())
        actual = (
            run(["docker", "image", "inspect", "devsecops-base:retained", "--format", "{{.Id}}"])
            .decode()
            .strip()
        )
        if actual != owner["base_config_digest"]:
            raise RuntimeError("Retained base identity changed")
        verify_tool(state, "syft")
        run(
            [
                "docker",
                "build",
                "--pull=false",
                "--network=none",
                "--build-arg",
                "BASE=devsecops-base:retained",
                "--label",
                "org.opencontainers.image.revision=" + os.environ["CI_COMMIT_SHA"],
                "-t",
                image,
                "app",
            ]
        )
        run(
            [str(state / "inputs/syft"), "scan", "docker:" + image, "-o", "syft-json"],
            output=artifacts / "sbom.json",
        )
        (artifacts / "build.json").write_text(
            json.dumps(
                {
                    "source_revision": os.environ["CI_COMMIT_SHA"],
                    "base_manifest": owner["base_manifest_digest"],
                    "base_config": actual,
                    "image_config": run(["docker", "image", "inspect", image, "--format", "{{.Id}}"])
                    .decode()
                    .strip(),
                }
            )
        )
        built = json.loads((artifacts / "build.json").read_text())
        sbom = json.loads((artifacts / "sbom.json").read_text())
        print(
            json.dumps(
                {
                    "source_revision": built["source_revision"],
                    "built_config_digest": built["image_config"],
                    "sbom_artifact_count": len(sbom["artifacts"]),
                }
            )
        )
    elif stage == "scan":
        print("Scanning the actual SBOM with the retained genuine Grype database.", flush=True)
        build_receipt(artifacts, owner)
        verify_tool(state, "grype")
        run(
            [str(state / "inputs/grype"), "sbom:artifacts/sbom.json", "-o", "json"],
            output=artifacts / "grype.json",
        )
        scan = json.loads((artifacts / "grype.json").read_text())
        assessment = SCAN_POLICY.evaluate_scan(scan, exception)
        _, _, current_exception_sha = fresh(state, owner)
        if current_exception_sha != exception_sha:
            raise RuntimeError("The evaluated exception changed during scanning")
        (artifacts / "scan-decision.json").write_text(
            json.dumps(
                {
                    **{key: value for key, value in assessment.items() if key != "permitted"},
                    **scan_binding(artifacts),
                    "exception_expires_at": exception["expires_at"],
                    "database_built": db["built"],
                    "exception_sha256": exception_sha,
                }
            )
        )
        if not assessment["permitted"]:
            raise RuntimeError("Image findings exceed the exact reviewed exception")
        print(
            json.dumps(
                {
                    "decision": assessment["decision"],
                    "high": assessment["high_count"],
                    "critical": assessment["critical_count"],
                    "unknown_severity": assessment["unknown_severity_count"],
                    "exception_expires_at": exception["expires_at"],
                }
            )
        )
    elif stage == "publish":
        print("Publishing the scanned image by digest and writing its deployment Git commit.", flush=True)
        decision = json.loads((artifacts / "scan-decision.json").read_text())
        if decision["decision"] not in {"passed", "temporary-exception"}:
            raise RuntimeError("Missing actual scan admission")
        admitted_fresh(state, owner, decision)
        build = build_receipt(artifacts, owner)
        if any(decision.get(k) != v for k, v in scan_binding(artifacts).items()):
            raise RuntimeError("Scanned build/SBOM/report identity changed")
        actual = run(["docker", "image", "inspect", image, "--format", "{{.Id}}"]).decode().strip()
        if actual != build["image_config"]:
            raise RuntimeError("Local image differs from the exact scanned build")
        if (
            os.environ["CI_API_V4_URL"] != owner["gitlab_origin"] + "/api/v4"
            or os.environ["CI_REGISTRY"] != owner["registry"]
        ):
            raise RuntimeError("CI API/registry origin differs from this owned lab")
        project_record = json.loads((state / "project.json").read_text())
        if (
            os.environ["CI_PROJECT_ID"] != str(project_record["id"])
            or os.environ["CI_REGISTRY_IMAGE"]
            != owner["registry"] + "/" + project_record["path_with_namespace"]
        ):
            raise RuntimeError("CI project/image repository differs from the actual owned project")
        context = ssl.create_default_context(cafile=str(state / "tls/ca.crt"))
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context), NoRedirect()
        )

        def api(path, body=None):
            req = urllib.request.Request(
                os.environ["CI_API_V4_URL"] + path,
                data=json.dumps(body).encode() if body is not None else None,
                headers={"PRIVATE-TOKEN": os.environ["DEPLOY_TOKEN"], "Content-Type": "application/json"},
            )
            with opener.open(req, timeout=60) as response:
                return json.load(response)

        registry = os.environ["CI_REGISTRY"]
        target = os.environ["CI_REGISTRY_IMAGE"] + ":" + os.environ["CI_COMMIT_SHA"]
        run(
            ["docker", "login", registry, "--username", os.environ["CI_REGISTRY_USER"], "--password-stdin"],
            data=(os.environ["CI_REGISTRY_PASSWORD"] + "\n").encode(),
        )
        run(["docker", "tag", image, target])
        admitted_fresh(state, owner, decision)
        run(["docker", "push", target])
        digests = json.loads(run(["docker", "image", "inspect", target]))[0]["RepoDigests"]
        repository = os.environ["CI_REGISTRY_IMAGE"]
        digest = next(x.split("@")[1] for x in digests if x.startswith(repository + "@"))
        values = {
            "environment": "portable-demo",
            "architecture": "amd64",
            "image": {"repository": repository, "digest": digest},
            "imagePullSecrets": [{"name": "mission-registry"}],
            "networkPolicy": {
                "healthPeer": {"namespace": "observability", "podLabels": {"app": "prometheus"}},
                "metricsPeer": {"namespace": "observability", "podLabels": {"app": "prometheus"}},
                "metricsPort": 9090,
            },
        }
        project = "/projects/" + os.environ["CI_PROJECT_ID"]
        try:
            branch = api(project + "/repository/branches/deploy")
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            admitted_fresh(state, owner, decision)
            branch = api(
                project + "/repository/branches", {"branch": "deploy", "ref": os.environ["CI_COMMIT_SHA"]}
            )
        # A retry can reuse an already-created branch. Do not silently retain a
        # different Helm chart from an older source pipeline.
        validate_deployment_chart(api, project, branch["commit"]["id"])
        value_path = (
            project + "/repository/files/" + urllib.parse.quote("deploy/values.json", safe="") + "?ref=deploy"
        )
        try:
            prior = api(value_path)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            prior = None
        if prior is not None and json.loads(base64.b64decode(prior["content"])) == values:
            commit = {"id": prior["commit_id"]}
        else:
            action = {
                "action": "update" if prior else "create",
                "file_path": "deploy/values.json",
                "content": json.dumps(values, indent=2),
            }
            if prior:
                action["last_commit_id"] = prior["last_commit_id"]
            admitted_fresh(state, owner, decision)
            commit = api(
                project + "/repository/commits",
                {
                    "branch": "deploy",
                    "commit_message": "Deploy scanned immutable mission image [skip ci]",
                    "actions": [action],
                },
            )
        (artifacts / "deployment.json").write_text(
            json.dumps(
                {
                    "image": repository + "@" + digest,
                    "git_revision": commit["id"],
                    "build_revision": os.environ["CI_COMMIT_SHA"],
                    "pipeline_id": os.environ["CI_PIPELINE_ID"],
                    "job_id": os.environ["CI_JOB_ID"],
                }
            )
        )
        print(
            json.dumps(
                {
                    "published_image": repository + "@" + digest,
                    "deployment_revision": commit["id"],
                    "pipeline_id": os.environ["CI_PIPELINE_ID"],
                }
            )
        )
    else:
        raise ValueError("Unknown job")


if __name__ == "__main__":
    main()
