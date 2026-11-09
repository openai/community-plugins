#!/usr/bin/env python3
"""Fetch explicitly pinned, data-only upstream reference files. Never runs upstream code."""
from pathlib import Path
import hashlib
import json
import urllib.request

ROOT = Path(__file__).resolve().parents[1] / "fixtures"
DATASETS = {
    "sparkfun-stcc4": {
        "repo": "https://github.com/sparkfun/SparkFun_CO2_Sensor_STCC4_SHT40_Qwiic",
        "commit": "836b63c8f684d57779cf0e699144ac056e0808ba",
        "license": "CC-BY-SA-4.0 (hardware); MIT (software)",
        "files": ["LICENSE.md", "README.md", "Hardware/SparkFun_Qwiic_CO2_Sensor_STCC4.kicad_pcb", "Hardware/SparkFun_Qwiic_CO2_Sensor_STCC4.kicad_sch"],
        "omittedFiles": [{"path": "Hardware/SparkFun_Qwiic_CO2_Sensor_STCC4.kicad_pro", "reason": "Machine-local project settings are not needed for the assembly-data fixture. The unmodified project remains available at the pinned upstream revision."}],
    },
    "opulo-ftp": {
        "repo": "https://github.com/opulo-inc/lumenpnp",
        "commit": "93f8124f24e04245a856cc3c9731e8de32d38726",
        "license": "CERN-OHL-W-2.0 (hardware); see upstream notice for separate libraries and branding",
        "files": ["LICENSE", "pnp/pcb/ftp/ftp.kicad_pcb", "pnp/pcb/ftp/ftp.kicad_sch", "pnp/pcb/ftp/ftp.kicad_pro"],
        "omittedFiles": [{"path": "pnp/pcb/ftp/fp-lib-table", "reason": "Machine-local footprint-library paths are not needed for the assembly-data fixture. The unmodified table remains available at the pinned upstream revision."}],
        "extra": [("ftp-top.pos", "https://github.com/opulo-inc/lumenpnp/releases/download/v4.1.0/ftp-top.pos")],
    },
}

def main():
    for name, dataset in DATASETS.items():
        records = []
        manifest_path = ROOT / name / "source-manifest.json"
        expected = {}
        if manifest_path.exists():
            previous = json.loads(manifest_path.read_text())
            if previous["commit"] != dataset["commit"]:
                raise ValueError(f"Pinned commit differs from retained manifest: {name}")
            expected = {record["path"]: record["sha256"] for record in previous["files"]}
        sources = [(p, f"https://raw.githubusercontent.com/{dataset['repo'].split('github.com/')[1]}/{dataset['commit']}/{p}") for p in dataset["files"]]
        sources += dataset.get("extra", [])
        for source_path, url in sources:
            target = ROOT / name / "source" / source_path
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                request = urllib.request.Request(url, headers={"User-Agent": "pcba-engineering-dataset-qualification"})
                with urllib.request.urlopen(request, timeout=60) as response:
                    target.write_bytes(response.read())
            data = target.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            relative = f"source/{source_path}"
            if relative in expected and digest != expected[relative]:
                raise ValueError(f"Source hash differs from retained manifest: {name}/{relative}")
            records.append({"path": relative, "sha256": digest, "url": url, "bytes": len(data)})
            print(name, source_path, len(data), flush=True)
        manifest = {key: dataset[key] for key in ("repo", "commit", "license")}
        manifest["files"] = records
        manifest["omittedFiles"] = [{**item, "url": f"https://raw.githubusercontent.com/{dataset['repo'].split('github.com/')[1]}/{dataset['commit']}/{item['path']}"} for item in dataset.get("omittedFiles", [])]
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

if __name__ == "__main__":
    main()
