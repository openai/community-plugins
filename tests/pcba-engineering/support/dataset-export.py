#!/usr/bin/env python3
"""Run an explicitly selected KiCad CLI against pinned source files.

Only export/check subcommands are invoked. The runner preserves nonzero ERC/DRC
results and verifies the source hashes after every run. It never changes source
data to make checks pass and does not invoke upstream project scripts.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess

ROOT = Path(__file__).resolve().parents[1] / "fixtures"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kicad-cli", required=True, type=Path)
    parser.add_argument("--config-dir", required=True, type=Path)
    args = parser.parse_args()
    cli = args.kicad_cli.resolve(strict=True)
    args.config_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, KICAD_CONFIG_HOME=str(args.config_dir.resolve()))
    version = subprocess.run([str(cli), "version"], capture_output=True, text=True, env=env, check=True).stdout.strip()
    for name in ("sparkfun-stcc4", "opulo-ftp"):
        directory = ROOT / name
        source = json.loads((directory / "source-manifest.json").read_text())
        pcb = next(directory / f["path"] for f in source["files"] if f["path"].endswith(".kicad_pcb"))
        sch = next(directory / f["path"] for f in source["files"] if f["path"].endswith(".kicad_sch"))
        output = directory / "exports"
        output.mkdir(exist_ok=True)
        jobs = [
            ("positions", ["pcb", "export", "pos", "--format", "csv", "--units", "mm", "--use-drill-file-origin", "--output", str(output / "positions.csv"), str(pcb)]),
            ("bom", ["sch", "export", "bom", "--fields", "Reference,Value,Footprint,PROD_ID", "--labels", "Reference,Value,Footprint,PROD_ID", "--output", str(output / "bom.csv"), str(sch)]),
            ("schematic", ["sch", "export", "svg", "--output", str(output / "schematic"), str(sch)]),
            ("board", ["pcb", "export", "svg", "--layers", "F.Cu,F.SilkS,F.Fab,Edge.Cuts", "--mode-single", "--page-size-mode", "2", "--output", str(output / "board.svg"), str(pcb)]),
            ("erc", ["sch", "erc", "--format", "json", "--severity-all", "--exit-code-violations", "--output", str(output / "erc.json"), str(sch)]),
            ("drc", ["pcb", "drc", "--format", "json", "--schematic-parity", "--severity-all", "--exit-code-violations", "--output", str(output / "drc.json"), str(pcb)]),
        ]
        records = []
        for name_, command in jobs:
            portable_command = [str(Path(part).relative_to(directory)) if part.startswith(str(directory) + os.sep) else part for part in command]
            result = subprocess.run([str(cli), *portable_command], cwd=directory, capture_output=True, text=True, env=env, timeout=180)
            log_path = output / f"{name_}.stdout.txt"
            log_path.write_text((result.stdout + result.stderr).replace(str(directory), "<dataset>"))
            normalized_command = portable_command
            records.append({"job": name_, "command": ["kicad-cli", *normalized_command], "exitCode": result.returncode, "log": log_path.name, "status": "passed" if result.returncode == 0 else "violations" if result.returncode == 5 and name_ in ("erc", "drc") else "failed"})
            print(directory.name, name_, result.returncode, flush=True)
        mutations = [f["path"] for f in source["files"] if hashlib.sha256((directory / f["path"]).read_bytes()).hexdigest() != f["sha256"]]
        if mutations:
            raise RuntimeError(f"Source files changed during export: {mutations}")
        files = [{"path": str(path.relative_to(output)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in sorted(output.rglob("*")) if path.is_file() and path.name != "export-manifest.json"]
        manifest = {"schemaVersion": 1, "tool": "KiCad CLI", "version": version, "sourceCommit": source["commit"], "jobs": records, "files": files, "sourceFilesModified": False, "qualification": "Export/check evidence only. ERC/DRC violations are retained; no physical or electrical qualification."}
        (output / "export-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

if __name__ == "__main__":
    main()
