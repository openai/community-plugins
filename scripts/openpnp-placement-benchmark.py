#!/usr/bin/env python3
"""Run paired fresh-JVM native direct/bridge trials; preserve every raw result and failed run."""
import argparse
import datetime
import glob
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess


def distribution(values):
    return {"n": len(values), "min": min(values), "median": statistics.median(values), "max": max(values),
            "mean": statistics.mean(values), "sample_stdev": statistics.stdev(values) if len(values) > 1 else None}


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes(paths, root):
    return {str(path.relative_to(root)): file_hash(path) for path in paths}


def public_trial(trial):
    result = dict(trial)
    result["journal_path"] = "[private native journal]" if trial.get("journal_path") else None
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--java", required=True)
    parser.add_argument("--classpath", required=True, help="Pinned upstream JAR and dependency JARs only; this runner compiles bridge classes privately")
    parser.add_argument("--sample-root", required=True)
    parser.add_argument("--output", type=Path, required=True, help="New private directory for classes, logs and exact host paths")
    parser.add_argument("--public-output", type=Path, help="Optional new directory for sanitized summary and six trial JSON files")
    parser.add_argument("--count", type=int, default=100, choices=[100, 200])
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--repetitions", type=int, default=3, choices=range(3, 7))
    parser.add_argument("--expected-job-order", default="Unsorted", help="Required actual public native JobOrderHint name for both modes")
    args = parser.parse_args()
    if not 10 <= args.warmup <= 50:
        parser.error("--warmup must be between 10 and 50")
    args.output.mkdir(parents=True, exist_ok=False)
    source_root = Path(__file__).resolve().parents[1]
    source_paths = sorted((source_root / "src/openpnp/java/org/openpnp/codex").glob("*.java"))
    source_paths += [Path(__file__).resolve(), source_root / "tests/openpnp/native/NativePlacementBenchmark.java"]
    observed_sources = source_hashes(source_paths, source_root)
    (args.output / "runner-at-measurement.py").write_bytes(Path(__file__).read_bytes())
    classes = args.output.resolve() / "classes"
    classes.mkdir()
    javac = Path(args.java).with_name("javac")
    compiler_version = subprocess.run([str(javac), "-version"], text=True, capture_output=True, check=True)
    native_files = [Path(path).resolve() for entry in args.classpath.split(os.pathsep) for path in sorted(glob.glob(entry)) if Path(path).is_file()]
    if not native_files:
        raise SystemExit("No pinned native JARs resolved from the provided classpath.")
    dependency_hashes = [{"file": path.name, "sha256": file_hash(path)} for path in native_files]
    # Resolve wildcards once before compilation and execute every JVM against that exact
    # ordered file list, so a later directory entry cannot silently change dependencies.
    resolved_native_classpath = os.pathsep.join(str(path) for path in native_files)
    compile_command = [str(javac), "--release", "11", "-cp", resolved_native_classpath, "-d", str(classes)]
    compile_command += [str(path) for path in source_paths if path.suffix == ".java"]
    compilation = subprocess.run(compile_command, text=True, capture_output=True)
    (args.output / "compilation.stdout.log").write_text(compilation.stdout)
    (args.output / "compilation.stderr.log").write_text(compilation.stderr)
    if compilation.returncode:
        raise SystemExit("Private compilation failed; retained compiler logs.")
    if source_hashes(source_paths, source_root) != observed_sources:
        raise SystemExit("Sources changed during compilation; no benchmark was run.")
    compiled_hashes = {str(path.relative_to(classes)): file_hash(path) for path in sorted(classes.rglob("*.class"))}
    if [{"file": path.name, "sha256": file_hash(path)} for path in native_files] != dependency_hashes:
        raise SystemExit("Pinned native JARs changed during compilation; no benchmark was run.")
    trial_classpath = str(classes) + os.pathsep + resolved_native_classpath
    runs, pairs = [], []
    metadata = {"started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "source_sha256": observed_sources,
                "compilation": {"compiler": (compiler_version.stdout + compiler_version.stderr).strip(), "release": 11,
                                "sources_unchanged_during_compilation": True, "compiled_class_sha256": compiled_hashes,
                                "native_classpath_artifacts": dependency_hashes,
                                "native_classpath_resolution": "Explicit ordered JAR paths resolved before compilation and reused unchanged by every trial JVM"},
                "design": "alternating paired modes; fresh JVM and fresh finite native fixture per trial; warmup excluded",
                "command_parameters": {"java": args.java, "classpath": args.classpath, "sample_root": args.sample_root,
                                       "count": args.count, "warmup": args.warmup, "repetitions": args.repetitions,
                                       "expected_job_order": args.expected_job_order},
                "recipe_scope": f"This benchmark requires the public native {args.expected_job_order} job order for the sustained-workload preordered grid; other native planning, vision, feed, place and durable journal behavior remains enabled. Default profile job order is outside this benchmark.",
                "limitations": ["Three or more pairs characterize this machine, fixture and exact classes only; no population confidence claim.",
                                "Accelerated native simulator uses zero dwell/settle times and finite virtual zero-pitch trays; physical-machine cycle times differ.",
                                "Bridge uses production durable journal, guards, artifact writes and 2 ms polling. HTTP/MCP transport is excluded.",
                                "Camera timings include settled native capture and PNG encoding; bridge additionally stores artifacts and receipts.",
                                "Other host load is not controlled; paired order reduces but does not remove scheduling and cache variation."],
                "physical_qualification": False}
    for pair_index in range(args.repetitions):
        pair = {}
        for mode in (["direct", "bridge"] if pair_index % 2 == 0 else ["bridge", "direct"]):
            label = f"pair-{pair_index + 1}-{mode}"
            command = [args.java, "-Xmx2g", "-XX:+ExitOnOutOfMemoryError", "--add-opens=java.base/java.lang=ALL-UNNAMED", "--add-opens=java.desktop/java.awt=ALL-UNNAMED",
                       "--add-opens=java.desktop/java.awt.color=ALL-UNNAMED", "-Djava.awt.headless=true", "-cp", trial_classpath,
                       "org.openpnp.codex.NativePlacementBenchmark", mode, args.sample_root, str(args.count), str(args.warmup)]
            print(f"Running {label}", flush=True)
            try:
                result = subprocess.run(command, text=True, capture_output=True, timeout=600)
            except subprocess.TimeoutExpired as error:
                (args.output / f"{label}.stdout.log").write_bytes(error.stdout or b"")
                (args.output / f"{label}.stderr.log").write_bytes(error.stderr or b"")
                (args.output / "failed-run.json").write_text(json.dumps({**metadata, "label": label, "error": "timeout", "completed_runs": runs}, indent=2))
                raise SystemExit(f"Benchmark {label} timed out; retained raw logs.")
            (args.output / f"{label}.stdout.log").write_text(result.stdout)
            (args.output / f"{label}.stderr.log").write_text(result.stderr)
            if result.returncode:
                (args.output / "failed-run.json").write_text(json.dumps({**metadata, "label": label, "exit_code": result.returncode, "completed_runs": runs}, indent=2))
                raise SystemExit(f"Benchmark {label} failed (exit {result.returncode}); retained raw logs.")
            marker = "OPENPNP_NATIVE_PLACEMENT_BENCHMARK_RESULT "
            matched = [line[len(marker):] for line in result.stdout.splitlines() if line.startswith(marker)]
            if len(matched) != 1:
                raise SystemExit(f"Missing or ambiguous result in {label}")
            trial = json.loads(matched[0]); trial["pair_index"] = pair_index + 1
            if trial["measured"]["placed"] != args.count or trial["measured_native_feeds"] != args.count:
                raise SystemExit(f"Counts did not match in {label}")
            if trial["job_order"] != args.expected_job_order:
                raise SystemExit(f"Actual native job order did not match the predeclared recipe in {label}")
            (args.output / f"{label}.json").write_text(json.dumps(trial, indent=2))
            runs.append(trial); pair[mode] = trial
            print(f"Completed {label}: {trial['measured']['seconds']:.6f}s for {args.count} placements", flush=True)
        for key in ["canonical_job_sha256", "machine_speed", "job_order", "native_processor_class", "class_sha256", "capture_params", "runtime_limits"]:
            if pair["direct"][key] != pair["bridge"][key]:
                raise SystemExit(f"Mismatched paired fixture/code parameter: {key}")
        if pair["direct"]["measured"]["native_steps"] != pair["bridge"]["measured"]["native_steps"]:
            raise SystemExit("Mismatched native processor step counts")
        direct = pair["direct"]["measured"]["seconds"]; bridge = pair["bridge"]["measured"]["seconds"]
        pairs.append({"pair_index": pair_index + 1, "direct_seconds": direct, "bridge_seconds": bridge,
                      "overhead_seconds": bridge - direct, "overhead_percent": (bridge / direct - 1) * 100})
    if source_hashes(source_paths, source_root) != observed_sources:
        raise SystemExit("Sources changed during execution; raw trials retained, final-source claim rejected.")
    if {str(path.relative_to(classes)): file_hash(path) for path in sorted(classes.rglob("*.class"))} != compiled_hashes:
        raise SystemExit("Private compiled classes changed during execution; raw trials retained, provenance claim rejected.")
    if [{"file": path.name, "sha256": file_hash(path)} for path in native_files] != dependency_hashes:
        raise SystemExit("Pinned native JARs changed during execution; raw trials retained, provenance claim rejected.")
    summary = {**metadata, "completed_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "all_exact_counts_pass": True,
               "sources_and_compiled_classes_unchanged_through_execution": True,
               "pairs": pairs, "placement_seconds": {mode: distribution([r["measured"]["seconds"] for r in runs if r["mode"] == mode]) for mode in ["direct", "bridge"]},
               "paired_overhead_percent": distribution([pair["overhead_percent"] for pair in pairs]),
               "measured_median_overhead_percent": statistics.median(pair["overhead_percent"] for pair in pairs),
               "target_added_cycle_time_percent": 5,
               "target_5_percent_met": all(pair["overhead_percent"] <= 5 for pair in pairs),
               "target_evaluation": "Every observed paired overhead must be at most 5%; this is descriptive sample evidence, not a population guarantee.",
               "simulator_throughput_placements_per_hour": {mode: {
                   "each_trial": [args.count / r["measured"]["seconds"] * 3600 for r in runs if r["mode"] == mode],
                   "median": statistics.median(args.count / r["measured"]["seconds"] * 3600 for r in runs if r["mode"] == mode),
                   "aggregate": args.count * args.repetitions / sum(r["measured"]["seconds"] for r in runs if r["mode"] == mode) * 3600,
                   "scope": "Arithmetic conversion of measured job duration; not a sustained hourly observation."
               } for mode in ["direct", "bridge"]},
               "camera_seconds": {mode: distribution([duration for r in runs if r["mode"] == mode for duration in r["native_capture_samples"]]) for mode in ["direct", "bridge"]},
               "total_measured_placements": 2 * args.repetitions * args.count, "total_warmup_placements": 2 * args.repetitions * args.warmup,
               "run_files": [f"pair-{r['pair_index']}-{r['mode']}.json" for r in runs]}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2))
    if args.public_output:
        args.public_output.mkdir(parents=True, exist_ok=False)
        public_summary = dict(summary)
        public_summary["command_parameters"] = {"java": "[cached JDK 17]/bin/java", "classpath": "[pinned upstream GUI JAR]:[pinned dependency JARs]",
                                                "sample_root": "[pinned upstream]/samples", "count": args.count, "warmup": args.warmup, "repetitions": args.repetitions,
                                                "expected_job_order": args.expected_job_order}
        public_summary["raw_log_sha256"] = {path.name: file_hash(path) for path in sorted(args.output.glob("*.log"))}
        public_summary["raw_logs_public"] = False
        public_summary["redactions"] = ["Host-specific Java, classpath, sample and journal paths replaced with labels; timing/count/hash values unchanged."]
        (args.public_output / "summary.json").write_text(json.dumps(public_summary, indent=2))
        for trial in runs:
            (args.public_output / f"pair-{trial['pair_index']}-{trial['mode']}.json").write_text(json.dumps(public_trial(trial), indent=2))
    print(json.dumps({"output": str(args.output), "paired_overhead_percent": summary["paired_overhead_percent"], "all_exact_counts_pass": True}))


if __name__ == "__main__":
    main()
