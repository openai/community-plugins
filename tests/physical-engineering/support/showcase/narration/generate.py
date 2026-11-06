#!/usr/bin/env python3
"""Render stock Kokoro narration with explicit local inputs; never install or fetch."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import platform
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_segments(source: bytes) -> list[dict]:
    segments = json.loads(source)
    if not isinstance(segments, list) or not segments:
        raise ValueError("Narration must be a nonempty JSON array")
    previous_end = 0.0
    for index, item in enumerate(segments, 1):
        if not isinstance(item, dict) or not isinstance(item.get("text"), str) or not item["text"].strip():
            raise ValueError(f"Segment {index} requires nonempty text")
        for field in ("start", "end"):
            value = item.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"Segment {index} requires a finite numeric {field}")
        if item["start"] < previous_end or item["end"] <= item["start"]:
            raise ValueError(f"Segment {index} has overlapping or invalid timing")
        previous_end = float(item["end"])
    return segments


def check_runtime() -> dict[str, str]:
    if sys.version_info[:2] != (3, 12):
        raise ValueError("Use a separate Python 3.12 narration runtime; see narration/README.md")
    pins = dict(re.findall(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)", (HERE / "requirements.lock").read_text(), re.MULTILINE))
    if not pins:
        raise ValueError("Narration dependency lock is empty")
    for name, expected in pins.items():
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            actual = "missing"
        if actual != expected:
            raise ValueError(f"Narration needs {name}=={expected}, found {actual}; follow the manual setup in narration/README.md")
    return pins


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--segments", type=Path, required=True, help="JSON array of start/end seconds and text")
    parser.add_argument("--model", type=Path, required=True, help="Pinned kokoro-v1.0.onnx file")
    parser.add_argument("--voices", type=Path, required=True, help="Pinned voices-v1.0.bin file")
    parser.add_argument("--output", type=Path, required=True, help="New directory for WAVs and receipt")
    parser.add_argument("--voice", default="af_heart", help="Stock voice name; default af_heart")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--lead-in", type=float, default=0.2, help="Reserved scene time before speech")
    parser.add_argument("--tail-room", type=float, default=0.2, help="Minimum scene time after speech")
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Output already exists; choose a new audio take")
    if not 0.5 <= args.speed <= 2.0 or args.threads < 1:
        raise ValueError("Speed must be 0.5–2.0 and threads must be positive")
    if any(not math.isfinite(value) or value < 0 for value in (args.lead_in, args.tail_room)):
        raise ValueError("Lead-in and tail-room must be finite and nonnegative")
    source = args.segments.read_bytes()
    segments = read_segments(source)
    assets = json.loads((HERE / "assets.json").read_text())
    for role, path in (("model", args.model), ("voices", args.voices)):
        expected = assets[role]
        if path.stat().st_size != expected["bytes"] or sha256(path) != expected["sha256"]:
            raise ValueError(f"{role} does not match the pinned official release hash")
    packages = check_runtime()
    import numpy as np
    import onnxruntime as ort
    import soundfile as sf

    ort.disable_telemetry_events()
    from kokoro_onnx import Kokoro

    options = ort.SessionOptions()
    options.intra_op_num_threads = args.threads
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    session = ort.InferenceSession(str(args.model), sess_options=options, providers=["CPUExecutionProvider"])
    engine = Kokoro.from_session(session, str(args.voices))
    if args.voice not in engine.voices:
        raise ValueError("Requested stock voice is absent from the pinned voice table")
    args.output.mkdir(parents=True)
    (args.output / "narration-segments.source.json").write_bytes(source)
    records = []
    for index, item in enumerate(segments, 1):
        started = time.monotonic()
        phonemes = engine.tokenizer.phonemize(item["text"], "en-us")
        samples, rate = engine.create(item["text"], voice=args.voice, speed=args.speed, lang="en-us", trim=True)
        if samples.ndim != 1 or not samples.size or not np.isfinite(samples).all():
            raise ValueError(f"Segment {index} produced invalid samples")
        peak = float(np.abs(samples).max())
        rms = float(np.sqrt(np.mean(np.square(samples.astype(np.float64)))))
        if rms <= 0.001 or peak >= 1.0:
            raise ValueError(f"Segment {index} is silent or clipped")
        target = args.output / f"segment-{index:02d}.wav"
        sf.write(target, samples, rate, subtype="PCM_24")
        info = sf.info(target)
        duration = info.frames / info.samplerate
        slot = item["end"] - item["start"]
        record = {
            "segment": index, "start": item["start"], "end": item["end"],
            "text": item["text"], "text_changed": False, "phonemes": phonemes,
            "file": target.name, "sha256": sha256(target), "bytes": target.stat().st_size,
            "duration_seconds": round(duration, 6), "slot_seconds": slot,
            "slot_headroom_seconds": round(slot - duration, 6),
            "fits_slot": duration + args.lead_in + args.tail_room <= slot,
            "sample_rate": info.samplerate, "channels": info.channels, "subtype": info.subtype,
            "sample_peak": peak, "rms": rms, "render_seconds": round(time.monotonic() - started, 3),
        }
        records.append(record)
        print(json.dumps({key: record[key] for key in ("segment", "file", "duration_seconds", "fits_slot")}), flush=True)
    if args.segments.read_bytes() != source:
        raise ValueError("Narration source changed during generation; review the preserved source snapshot")
    result = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(), "engine": "kokoro-onnx",
        "python": platform.python_version(), "platform": platform.system(), "machine": platform.machine(),
        "voice": args.voice, "language": "en-us", "speed": args.speed,
        "source_sha256": hashlib.sha256(source).hexdigest(), "generator_sha256": sha256(Path(__file__)),
        "asset_provenance": assets, "requirements_lock_sha256": sha256(HERE / "requirements.lock"),
        "providers": session.get_providers(), "intra_op_threads": args.threads, "inter_op_threads": 1,
        "lead_in_seconds": args.lead_in, "minimum_tail_seconds": args.tail_room, "packages": packages,
        "credentials_used": False, "voice_cloning": False, "telemetry_disable_api_called": True,
        "bit_identical_regeneration_claimed": False,
        "perceptual_listening_qa": "Not performed by this generator",
        "all_segments_fit_slots": all(item["fits_slot"] for item in records),
        "speech_seconds": round(sum(item["duration_seconds"] for item in records), 6), "segments": records,
    }
    (args.output / "audio-manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    if not result["all_segments_fit_slots"]:
        raise ValueError("One or more segments exceed the scene time; inspect audio-manifest.json before mixing")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, ImportError) as exc:
        raise SystemExit(str(exc)) from None
