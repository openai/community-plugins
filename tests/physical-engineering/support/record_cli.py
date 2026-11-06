#!/usr/bin/env python3
"""Record real Codex CLI subprocess events. No fabricated playback input.

Private raw events and publication-safe events are intentionally separate.
Never publish private/, command files, or the originating operator prompt files.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import time

SCENES = {"A": "Brief", "B": "Baseline", "C": "Changed requirement",
          "D": "Revised handoff", "E": "Reproduction"}
QUERY = re.compile(r"(https?://[^\s\"'<>?]+)\?[^\s\"'<>]+")
PERSONAL_PATH = re.compile(r"/(?:Users|home|private/tmp|private/var/folders|var/folders)/[^\s\"'<>),;]+")
SECRET = re.compile(r'''(?i)(["']?(?:token|access_token|authorization)["']?\s*[:=]\s*)'''
                    r'''(?:"[^"]*"|'[^']*'|(?:Bearer|Basic)\s+[^\s,}]+|[^\s,}]+)''')


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def redacted(value, roots):
    if isinstance(value, dict):
        return {redacted(key, roots): ("[redacted]" if key.lower() in {"token", "access_token", "authorization"}
                      else redacted(item, roots)) for key, item in value.items()}
    if isinstance(value, list):
        return [redacted(item, roots) for item in value]
    if not isinstance(value, str):
        return value
    for index, root in enumerate(sorted(roots, key=len, reverse=True)):
        value = value.replace(root, "<study>" if index == 0 else "<workspace>")
    value = SECRET.sub(r"\1[redacted]", QUERY.sub(r"\1?[redacted]", value))
    return PERSONAL_PATH.sub("<local-path>", value)


def config(take):
    return json.loads((take / "private/config.json").read_text())


def append(take: Path, kind: str, **payload):
    with (take / "private/journal.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        settings = config(take)
        if (take / "capture-complete.json").exists():
            raise RuntimeError("This take is already complete and immutable")
        elapsed = (time.monotonic_ns() - settings["start_monotonic_ns"]) / 1e9
        if elapsed < 0:
            raise RuntimeError("Monotonic clock reset; start a new take")
        event = {"t": round(elapsed, 6), "utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                 "kind": kind, **payload}
        with (take / "private/raw-events.jsonl").open("a") as raw:
            raw.write(json.dumps(event, ensure_ascii=False) + "\n")
        public = redacted(event, settings["redact_roots"])
        with (take / "events.jsonl").open("a") as publish:
            publish.write(json.dumps(public, ensure_ascii=False) + "\n")
        return public


def initialize(args):
    take = args.take.resolve()
    take.mkdir(parents=True, exist_ok=False)
    (take / "private").mkdir(mode=0o700)
    (take / "artifacts").mkdir()
    source_roots = [str(path.resolve()) for path in args.source_root]
    roots = [*source_roots, *[str(path.resolve()) for path in args.redact_root], str(take)]
    settings = {"schema_version": 1, "start_monotonic_ns": time.monotonic_ns(), "start_unix_ns": time.time_ns(),
                "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                "source_roots": source_roots, "redact_roots": roots}
    (take / "private/config.json").write_text(json.dumps(settings, indent=2) + "\n")
    metadata = json.loads(args.metadata.read_text())
    metadata.update(recording_method="Recorded Codex CLI event replay",
                    native_desktop_capture=False, source="Actual codex exec --json subprocess events",
                    schema_version=1, started_utc=settings["started_utc"])
    (take / "metadata.json").write_text(json.dumps(redacted(metadata, roots), indent=2) + "\n")
    append(take, "capture_started", label="Real Codex CLI workflow capture started")
    print(json.dumps({"take": str(take), "status": "recording"}))


def phase(args):
    take = args.take.resolve()
    command = json.loads(args.command_file.read_text())
    if not isinstance(command, list) or not command or not all(isinstance(arg, str) for arg in command):
        raise ValueError("command-file must contain an argv string array")
    if Path(command[0]).name not in {"codex", "codex.exe"} or "exec" not in command or "--json" not in command:
        raise ValueError("Capture only supports actual codex exec --json commands")
    if command[-1] != "-":
        raise ValueError("Codex command must end with '-' so the recorded prompt is the actual stdin prompt")
    prompt = args.prompt_file.read_text()
    cwd = args.cwd.resolve(strict=True)
    with (take / "private/phase.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        append(take, "phase_started", scene=args.scene, title=SCENES[args.scene],
               argv=command, cwd=str(cwd))
        append(take, "operator_input", scene=args.scene, text=prompt,
               prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest())
        started = time.monotonic()
        try:
            process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, bufsize=1)
        except OSError as error:
            append(take, "phase_launch_failed", scene=args.scene, error=str(error))
            print(json.dumps({"scene": args.scene, "status": "launch_failed"}), flush=True)
            return 127
        append(take, "subprocess_started", scene=args.scene, pid=process.pid)
        pending = queue.Queue()

        def read(stream, channel):
            try:
                for line in stream:
                    pending.put((channel, line.rstrip("\n")))
            finally:
                pending.put((channel, None))

        for channel, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            threading.Thread(target=read, args=(stream, channel), daemon=True).start()
        try:
            try:
                process.stdin.write(prompt + ("" if prompt.endswith("\n") else "\n"))
                process.stdin.close()
            except BrokenPipeError:
                # A rejected CLI invocation can close stdin immediately. Its
                # real stdout/stderr and exit still belong in this capture.
                pass
            closed = 0
            while closed < 2:
                channel, line = pending.get()
                if line is None:
                    closed += 1
                    continue
                raw_name = "stdout.raw.jsonl" if channel == "stdout" else "stderr.raw.log"
                with (take / "private" / raw_name).open("a") as raw:
                    raw.write(line + "\n")
                payload = {"scene": args.scene, "channel": channel, "line": line}
                if channel == "stdout":
                    try:
                        payload["event"] = json.loads(line)
                    except ValueError:
                        pass
                public = append(take, "cli_output", **payload)
                event = public.get("event", {})
                if isinstance(event, dict) and event.get("type") in {"turn.completed", "turn.failed", "error"}:
                    print(json.dumps({"scene": args.scene, "t": public["t"], "event": event}), flush=True)
            code = process.wait()
        except BaseException:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            append(take, "phase_interrupted", scene=args.scene, returncode=process.returncode)
            raise
        append(take, "phase_finished", scene=args.scene, returncode=code,
               elapsed_seconds=round(time.monotonic() - started, 6))
        print(json.dumps({"scene": args.scene, "returncode": code, "status": "captured"}), flush=True)
        return code


def observe(args):
    take = args.take.resolve()
    if (take / "capture-complete.json").exists():
        raise RuntimeError("This take is already complete and immutable")
    settings = config(take)
    source = args.path.resolve(strict=True)
    if args.path.is_symlink() or not source.is_file():
        raise ValueError("Observe a regular, existing artifact file")
    roots = [Path(root) for root in settings["source_roots"]]
    if not any(source.is_relative_to(root) for root in roots):
        raise ValueError("Observed artifacts must belong to this take's declared source root")
    started_ns = settings.get("start_unix_ns", int(dt.datetime.fromisoformat(settings["started_utc"]).timestamp() * 1e9))
    if source.stat().st_mtime_ns < started_ns:
        raise ValueError("Artifact predates this take; generate its current output during the recorded workflow")
    if source.suffix.lower() not in {".png", ".jpg", ".jpeg", ".json", ".pdf", ".step", ".stl", ".csv"}:
        raise ValueError("Unsupported evidence artifact format")
    fingerprint = sha(source)
    relative = f"artifacts/{fingerprint[:16]}{source.suffix.lower()}"
    destination = take / relative
    shutil.copyfile(source, destination)
    if sha(destination) != fingerprint or sha(source) != fingerprint:
        raise RuntimeError("Artifact changed while being observed")
    if source.suffix.lower() in {".csv", ".step"}:
        text = destination.read_text()
        if redacted(text, settings["redact_roots"]) != text:
            raise ValueError("Text artifact contains private paths or credentials; export a reviewed public artifact first")
    data = {"scene": args.scene, "label": args.label, "artifact": relative,
            "sha256": fingerprint, "bytes": source.stat().st_size,
            "source_modified_utc": dt.datetime.fromtimestamp(source.stat().st_mtime, dt.timezone.utc).isoformat(),
            "source_relative_path": next(str(source.relative_to(root)) for root in roots if source.is_relative_to(root))}
    if source.suffix.lower() == ".json":
        report = json.loads(destination.read_text())
        # Publication keeps the real hash identity, but the copied JSON must not
        # carry local paths or credentials into the reusable session kit.
        clean = redacted(report, settings["redact_roots"])
        destination.write_text(json.dumps(clean, indent=2) + "\n")
        data["published_sha256"] = sha(destination)
        if isinstance(report, dict) and "measurements" in report:
            measured = report["measurements"]
            data["measurement_summary"] = {"status": report.get("status", "unknown"),
                **{key: measured[key] for key in ("cavity_width_mm", "polymer_mass_g", "lateral_clearance_mm", "frame_pitch_mm") if key in measured}}
    event = append(take, "artifact_observed", **data)
    print(json.dumps(event))


def finish(args):
    take = args.take.resolve()
    with (take / "private/phase.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        event = append(take, "capture_finished", label="Actual CLI event capture finished")
        complete = {"schema_version": 1, "duration_seconds": event["t"],
                    "events_sha256": sha(take / "events.jsonl"),
                    "metadata_sha256": sha(take / "metadata.json")}
        (take / "capture-complete.json").write_text(json.dumps(complete, indent=2) + "\n")
        print(json.dumps(complete))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    init = commands.add_parser("init")
    init.add_argument("--take", type=Path, required=True)
    init.add_argument("--metadata", type=Path, required=True)
    init.add_argument("--source-root", type=Path, action="append", required=True)
    init.add_argument("--redact-root", type=Path, action="append", default=[])
    run = commands.add_parser("phase")
    run.add_argument("--take", type=Path, required=True)
    run.add_argument("--scene", choices=SCENES, required=True)
    run.add_argument("--prompt-file", type=Path, required=True)
    run.add_argument("--command-file", type=Path, required=True)
    run.add_argument("--cwd", type=Path, required=True)
    artifact = commands.add_parser("observe")
    artifact.add_argument("--take", type=Path, required=True)
    artifact.add_argument("--scene", choices=SCENES, required=True)
    artifact.add_argument("--path", type=Path, required=True)
    artifact.add_argument("--label", required=True)
    end = commands.add_parser("finish")
    end.add_argument("--take", type=Path, required=True)
    args = parser.parse_args()
    return {"init": initialize, "phase": phase, "observe": observe, "finish": finish}[args.action](args) or 0


if __name__ == "__main__":
    raise SystemExit(main())
