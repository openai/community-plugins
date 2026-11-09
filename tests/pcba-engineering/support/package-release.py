#!/usr/bin/env python3
"""Build a deterministic plugin ZIP and qualify the actual extracted bytes.

Uses only Python's standard library and the explicitly selected Node runtime.
Fixtures, saved handoffs and qualification infrastructure stay outside the ZIP.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
import zlib


def digest(data):
    return hashlib.sha256(data).hexdigest()


def inventory(root):
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"Not a plain directory: {root}")
    files = {}
    for base, dirs, names in os.walk(root, followlinks=False):
        for name in sorted(dirs + names):
            item = Path(base) / name
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise ValueError(f"Symbolic links are forbidden: {item}")
            if stat.S_ISDIR(info.st_mode):
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError(f"Nonregular files and hard links are forbidden: {item}")
            files[item.relative_to(root).as_posix()] = digest(item.read_bytes())
    return dict(sorted(files.items()))


def archive_bytes(source, expected):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative, sha in expected.items():
            data = (source / relative).read_bytes()
            if digest(data) != sha:
                raise ValueError(f"Source changed during packaging: {relative}")
            item = zipfile.ZipInfo("pcba-engineering/" + relative, (1980, 1, 1, 0, 0, 0))
            item.create_system = 3
            item.external_attr = (stat.S_IFREG | 0o644) << 16
            item.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(item, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return output.getvalue()


def extract_checked(archive_path, target):
    with zipfile.ZipFile(archive_path) as archive:
        seen = set()
        for item in archive.infolist():
            name = PurePosixPath(item.filename)
            if (name.is_absolute() or ".." in name.parts or "\\" in item.filename
                    or not name.parts or name.parts[0] != "pcba-engineering"
                    or len(name.parts) < 2 or item.filename in seen
                    or stat.S_IFMT(item.external_attr >> 16) != stat.S_IFREG):
                raise ValueError(f"Unsafe ZIP entry: {item.filename}")
            seen.add(item.filename)
        if archive.testzip() is not None:
            raise ValueError("ZIP CRC validation failed")
        archive.extractall(target)


def run(args):
    source = args.source.absolute()
    installed_receipt = json.loads(args.installed_receipt.read_text())
    installed = Path(installed_receipt["installed"])
    expected = inventory(source)
    if len(expected) != 22 or expected != installed_receipt["files"] or expected != inventory(installed):
        raise ValueError("The exact 22-file source/installed/receipt identity does not match")
    version = json.loads((source / "plugin.json").read_text())["version"]
    if installed_receipt["version"] != version:
        raise ValueError("Source and installed receipt versions differ")
    data = archive_bytes(source, expected)
    if data != archive_bytes(source, expected):
        raise ValueError("Archive generation is not deterministic")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        if args.output.read_bytes() != data:
            raise ValueError("Refusing to replace a different release archive")
    else:
        with args.output.open("xb") as handle:
            handle.write(data)
    result = {
        "schemaVersion": 1, "status": "running", "plugin": "pcba-engineering", "version": version,
        "qualifiedAt": datetime.now(timezone.utc).isoformat(),
        "generator": {"file": "tests/pcba-engineering/support/package-release.py",
                      "sha256": digest(Path(__file__).read_bytes()), "pythonVersion": sys.version.split()[0],
                      "zlibRuntimeVersion": zlib.ZLIB_RUNTIME_VERSION},
        "archive": {"file": args.output.name, "sha256": digest(data), "bytes": len(data), "fileCount": len(expected),
                    "singleRoot": "pcba-engineering/", "deterministicRebuildEqual": True, "linksAllowed": False},
        "identity": {"sourceEqualsInstalledReceipt": True, "sourceEqualsCurrentInstalledFiles": True,
                     "installedReceiptSha256": digest(args.installed_receipt.read_bytes()), "files": expected},
        "nodeVersion": subprocess.check_output([str(args.node), "--version"], text=True).strip(),
        "commands": [], "copiedEvidence": {}, "limits": [
            "Archive execution verifies the actual extracted package. It does not repeat Codex installation or natural-language invocation.",
            "Installed package equality connects these bytes to the separately recorded installed-Codex evidence.",
            "No KiCad host, electrical qualification, material availability or physical manufacturing acceptance is established.",
        ],
    }
    with tempfile.TemporaryDirectory(prefix="pcba release cold extraction ") as temporary:
        root = Path(temporary).resolve()
        cwd = root / "unrelated working directory"
        cwd.mkdir()
        empty_bin = root / "empty executable path"
        empty_bin.mkdir()
        extract_checked(args.output, root)
        extracted = root / "pcba-engineering"
        if inventory(extracted) != expected:
            raise ValueError("Extracted archive differs from source or installed receipt")
        result["identity"]["extractedEqualsSourceAndInstalled"] = True
        # Negative probe validates the archive builder's link rejection.
        link_probe = root / "link rejection probe"
        link_probe.mkdir()
        (link_probe / "linked-input").symlink_to(extracted / "plugin.json")
        try:
            inventory(link_probe)
        except ValueError:
            result["archive"]["symbolicLinkRejectionObserved"] = True
        else:
            raise ValueError("Link rejection probe unexpectedly succeeded")
        guard = root / "deny-network.mjs"
        marker = root / "network-attempts.txt"
        guard.write_text("""import fs from 'node:fs';
import net from 'node:net';
import tls from 'node:tls';
import dns from 'node:dns';
import { syncBuiltinESMExports } from 'node:module';
const deny=()=>{fs.appendFileSync(MARKER,'attempt\\n');throw new Error('Network is unavailable for archive qualification');};
globalThis.fetch=deny;
net.connect=net.createConnection=net.Socket.prototype.connect=deny;
tls.connect=deny;
for(const api of [dns,dns.promises]) for(const key of Object.keys(api)) if(typeof api[key]==='function' && /^(lookup|resolve|reverse)/.test(key)) api[key]=deny;
syncBuiltinESMExports();
""".replace("MARKER", json.dumps(str(marker))))
        env = {"PATH": str(empty_bin), "LANG": "C", "LC_ALL": "C"}
        if "SystemRoot" in os.environ:
            env["SystemRoot"] = os.environ["SystemRoot"]
        cli = extracted / "scripts/pcba.mjs"
        replacements = [(str(root), "${COLD_WORKSPACE}"), (str(installed), "${INSTALLED_PLUGIN}"),
                        (str(source), "${SOURCE_PLUGIN}"), (str(source.parent.parent), "${CHECKOUT}")]

        def redact(text):
            for old, new in replacements:
                text = text.replace(old, new)
            return text

        def command(label, argv, restricted=True, direct=False):
            flags = ["--import", str(guard)]
            if restricted:
                flags = ["--permission", f"--allow-fs-read={root}", f"--allow-fs-write={root}"] + flags
            cmd = [str(args.node)] + flags + ([] if direct else [str(cli)]) + argv
            start = time.monotonic()
            child = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=30)
            entry = {"label": label, "arguments": [redact(x) for x in argv], "exitCode": child.returncode,
                     "durationSeconds": round(time.monotonic() - start, 4), "filesystemRestrictedToColdWorkspace": restricted,
                     "stdout": redact(child.stdout), "stderr": redact(child.stderr)}
            result["commands"].append(entry)
            if child.returncode != 0:
                raise ValueError(f"{label} failed: {redact(child.stderr)}")
            return json.loads(child.stdout)

        try:
            # Doctor runs without child-process restrictions so absent native KiCad
            # is observed naturally via the empty PATH, not an application stub.
            doctor = command("extracted CLI doctor", ["doctor"], restricted=False)
            if not doctor["coreReady"] or doctor["networkRequired"] or doctor["kicad"]["available"]:
                raise ValueError("Unexpected doctor capability result")
            if Path(doctor["pluginRoot"]).resolve() != extracted:
                raise ValueError("Doctor did not execute from the extracted package")
            denied = command("source checkout unreadable", ["--input-type=module", "--eval",
                "import fs from 'node:fs';try{fs.readFileSync(" + json.dumps(str(source / "plugin.json")) + ");console.log(JSON.stringify({denied:false}));}catch(e){console.log(JSON.stringify({denied:e.code==='ERR_ACCESS_DENIED'}));}"], direct=True)
            if not denied["denied"]:
                raise ValueError("Source-checkout read restriction was not effective")
            evidence = root / "copied final evidence"
            evidence.mkdir()
            delivery_names = args.delivery or ["sensor-delivery", "training-delivery"]
            if len(set(delivery_names)) != len(delivery_names):
                raise ValueError("Delivery names must be unique")
            for name in delivery_names:
                if name in {".", ".."} or Path(name).name != name or "\\" in name:
                    raise ValueError("Delivery names must be plain directory names")
                original = args.evidence / name
                original_files = inventory(original)
                result["copiedEvidence"][name] = {"files": original_files, "copyEquality": True}
                destination = evidence / name
                shutil.copytree(original, destination)
                if inventory(destination) != original_files:
                    raise ValueError("Copied handoff differs from saved evidence")
                verified = command("verify copied " + name, ["verify-handoff", "--directory", str(destination)])
                if not verified["valid"]:
                    raise ValueError("Saved handoff verification failed")
            fixture = root / "independent public board.json"
            shutil.copyfile(args.fixture, fixture)
            fixture_hash = digest(fixture.read_bytes())
            project = root / "fresh second board project"
            imported = command("fresh second-board import", ["import", "--input", str(fixture), "--project", str(project)])
            if imported["report"]["status"] != "pass":
                raise ValueError("Second board import did not pass")
            checked = command("fresh second-board check", ["check", "--project", str(project)])
            if checked["report"]["status"] != "pass":
                raise ValueError("Second board check did not pass")
            output = root / "fresh second board handoff"
            exported = command("fresh second-board export", ["export", "--project", str(project), "--output", str(output)])
            verified = command("fresh second-board verify", ["verify-handoff", "--directory", str(output)])
            if not verified["valid"] or verified["revision"] != exported["revision"]:
                raise ValueError("Fresh handoff verification failed")
            if digest(fixture.read_bytes()) != fixture_hash:
                raise ValueError("Input fixture changed")
            if marker.exists():
                raise ValueError("Extracted workflow attempted network access")
            if inventory(source) != expected or inventory(extracted) != expected:
                raise ValueError("Package changed during qualification")
            result.update({"status": "passed", "sourceFixtureSha256": fixture_hash,
                           "sourceInputUnchanged": True, "networkAttempts": 0,
                           "emptyPath": True, "freshProcesses": True,
                           "sourceAndExtractedPackageUnchanged": True})
        except Exception as error:
            result.update({"status": "failed", "error": redact(str(error))})
            raise
        finally:
            args.output.with_suffix(".qualification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "archive": str(args.output), "sha256": digest(data),
                      "files": len(expected), "commandsPassed": len(result["commands"])}))
    args.output.with_suffix(".zip.sha256").write_text(digest(data) + "  " + args.output.name + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["source", "installed-receipt", "evidence", "fixture", "output", "node"]:
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--delivery", action="append", help="Saved handoff directory name under --evidence; repeat for each board")
    run(parser.parse_args())
