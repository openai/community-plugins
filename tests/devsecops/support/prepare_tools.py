"""Explicit test setup; checks pinned public tools, never starts a lab or fetches a CVE database."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import urllib.request


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    plugin, cache, output = (Path(value).resolve() for value in sys.argv[1:4])
    offline = os.environ.get("DEVSECOPS_TEST_OFFLINE") == "1"
    private_root = Path.home() / ".cache/devsecops"
    if not cache.is_relative_to(private_root) or cache == private_root:
        raise ValueError("Test cache must be a child of ~/.cache/devsecops")
    for parent in (cache, *cache.parents):
        if parent.is_symlink():
            raise ValueError("Cache path must not contain symlinks")
    lock = json.loads((plugin / "toolchain.lock.json").read_text())
    target = platform.system().lower() + "-" + {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine(), platform.machine())
    if target not in lock["tools"]:
        raise ValueError("Full suite supports Linux/macOS arm64/amd64; this host is not qualified")
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")

    def run(script, *args):
        subprocess.run([sys.executable, "-B", str(plugin / "scripts" / script), *map(str, args)],
                       check=True, env=environment, timeout=900, cwd=plugin)

    def verify_helm(directory, schema_source, schemas):
        try:
            receipt = json.loads((directory / "provisioning.json").read_text())
            if receipt["platform"] != target or receipt["schema_source"] != schema_source:
                return False
            for name, item in lock["tools"][target].items():
                observed = receipt["tools"][name]
                if any(observed.get(key) != value for key, value in item.items()):
                    return False
                if sha(directory / "bin" / name) != observed["binary_sha256"]:
                    return False
            return all(sha(directory / "schemas" / item["path"]) == item["sha256"] for item in schemas)
        except (OSError, ValueError, KeyError):
            return False

    schema_locations = {}
    schema_sets = {lock["schema_source"]["kubernetes_version"]: {"schema_source": lock["schema_source"], "schemas": lock["schemas"]}, **lock.get("schema_sets", {})}
    for version, selected in schema_sets.items():
        directory = cache / ("kubernetes-" + version)
        if not verify_helm(directory, selected["schema_source"], selected["schemas"]):
            if offline:
                raise ValueError(f"Pinned tools/schema cache for {version} is missing or changed; explicit online setup is required")
            run("bootstrap.py", "--cache", directory, "--kubernetes-version", version)
        if not verify_helm(directory, selected["schema_source"], selected["schemas"]):
            raise ValueError("Provisioned tools or schema hashes differ from the packaged locks")
        schema_locations[version] = directory

    for script, directory in [("bootstrap_kustomize.py", cache / "kustomize"),
                              ("bootstrap_cosign.py", cache / "cosign"),
                              ("bootstrap_supplychain.py", cache / "supplychain")]:
        run(script, "--cache", directory, *(["--offline"] if offline else []))

    wheel = json.loads((plugin / "demo/software-factory/apps/mission-status/dependency-lock.json").read_text())
    wheel_path = private_root / "wheelhouse" / wheel["filename"]
    if wheel_path.is_symlink() or any(p.is_symlink() for p in wheel_path.parents):
        raise ValueError("Wheel path must not contain symlinks")
    if not wheel_path.is_file() or sha(wheel_path) != wheel["sha256"]:
        if offline:
            raise ValueError("The exact public mission dependency wheel has not been staged")
        request = urllib.request.Request(wheel["url"], headers={"User-Agent": "devsecops-community-tests/1"})
        class NoRedirects(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                raise ValueError("Pinned dependency wheel request must not redirect")
        with urllib.request.build_opener(NoRedirects()).open(request, timeout=60) as response:
            raw = response.read(wheel["bytes"] + 1)
        if len(raw) != wheel["bytes"] or hashlib.sha256(raw).hexdigest() != wheel["sha256"]:
            raise ValueError("Public dependency wheel hash/size mismatch")
        wheel_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        wheel_path.write_bytes(raw)
        wheel_path.chmod(0o600)
    default = schema_locations[lock["schema_source"]["kubernetes_version"]]
    values = {
        "DEVSECOPS_HELM": str(default / "bin/helm"),
        "DEVSECOPS_KUBECONFORM": str(default / "bin/kubeconform"),
        "DEVSECOPS_SCHEMAS": str(default / "schemas"),
        "DEVSECOPS_KUSTOMIZE": str(cache / "kustomize/bin/kustomize"),
        "DEVSECOPS_COSIGN": str(cache / "cosign/bin/cosign"),
        "DEVSECOPS_SYFT": str(cache / "supplychain/bin/syft"),
        "DEVSECOPS_GRYPE": str(cache / "supplychain/bin/grype"),
        "DEVSECOPS_OFFLINE_WHEEL": str(wheel_path),
        "GRYPE_DB_AUTO_UPDATE": "false",
        "GRYPE_CHECK_FOR_APP_UPDATE": "false",
        "SYFT_CHECK_FOR_APP_UPDATE": "false",
    }
    if "1.36.5" in schema_locations:
        values["DEVSECOPS_SCHEMAS_1365"] = str(schema_locations["1.36.5"] / "schemas")
    for name in ("HELM", "KUBECONFORM", "KUSTOMIZE", "COSIGN", "SYFT", "GRYPE"):
        if not os.access(values["DEVSECOPS_" + name], os.X_OK):
            raise ValueError("Required real executable is missing or not executable: " + name)
    result = {"python": sys.executable, "environment": values, "platform": target,
              "results": str(output.parent), "network_setup": not offline,
              "scope": "local-test-prerequisites-only", "live_qualification": False}
    output.write_text(json.dumps(result, indent=2) + "\n")
    output.chmod(0o600)


if __name__ == "__main__":
    main()
