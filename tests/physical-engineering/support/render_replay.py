#!/usr/bin/env python3
"""Render a completed real CLI event capture, with an explicit replay label.

No synthetic results, narration, generated CAD views, or inserted source events.
The full replay preserves recorded wall time; the teaching cut scales that same
timeline. Both sample visual state once per second and link the complete transcript.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess

from PIL import Image, ImageDraw, ImageFont

from record_cli import SCENES, sha

WIDTH, HEIGHT = 1920, 1080
BACKGROUND = "#101720"
PANEL = "#192431"
TEXT = "#E8F0F7"
MUTED = "#9CB0C1"
BLUE = "#65C4ED"
GREEN = "#72D4AC"
GOLD = "#F4BA65"


def clock(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"


def timestamp(seconds):
    millis = max(0, round(seconds * 1000))
    return f"{millis // 3600000:02}:{millis // 60000 % 60:02}:{millis // 1000 % 60:02},{millis % 1000:03}"


def font(size, mono=False):
    candidates = (["/System/Library/Fonts/Menlo.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"]
                  if mono else ["/System/Library/Fonts/SFNS.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"])
    for path in candidates:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    raise RuntimeError("A readable system sans/monospace font is required")


FONTS = {}


def ft(size, mono=False):
    key = (size, mono)
    if key not in FONTS:
        FONTS[key] = font(size, mono)
    return FONTS[key]


def lines_for(text, size, width, mono=False):
    face = ft(size, mono)
    rows = []
    for line in str(text).replace("\t", "    ").splitlines():
        if not line:
            rows.append("")
            continue
        while line:
            if face.getlength(line) <= width:
                rows.append(line)
                break
            count = min(len(line), max(1, int(width / max(1, face.getlength("M")))))
            while count < len(line) and face.getlength(line[:count + 1]) <= width:
                count += 1
            split = line.rfind(" ", 0, count + 1)
            if split < count // 2:
                split = count
            rows.append(line[:split])
            line = line[split:].lstrip()
    return rows or [""]


def message(event, complete=False):
    kind = event["kind"]
    if kind == "operator_input":
        return "OPERATOR PROMPT", event["text"]
    if kind == "phase_started":
        return "RECORDED COMMAND", " ".join(event["argv"])
    if kind == "cli_output":
        item = event.get("event", {})
        if not isinstance(item, dict):
            return "CLI OUTPUT", event.get("line", "")
        payload = item.get("item", {})
        if isinstance(payload, dict):
            item_type = payload.get("type", "")
            if item_type == "agent_message":
                return "CODEX", str(payload.get("text", ""))
            if item_type == "command_execution":
                content = str(payload.get("command", ""))
                output = str(payload.get("aggregated_output", payload.get("output", "")))
                if output:
                    content += "\n\n" + (output if complete else output[-1800:])
                if payload.get("exit_code") is not None:
                    content += f"\n\n[Observed exit code: {payload['exit_code']}]"
                return "CODEX TOOL COMMAND", content
            if item_type == "file_change":
                return "CODEX FILE CHANGE", json.dumps(payload.get("changes", []), ensure_ascii=False, indent=2)
            if item_type == "mcp_tool_call":
                return "CODEX TOOL CALL", json.dumps(payload, ensure_ascii=False, indent=2)
        if not event.get("event") and event.get("line"):
            return "CLI STDERR" if event.get("channel") == "stderr" else "CLI OUTPUT", event["line"]
        if complete:
            return "CLI EVENT", json.dumps(item, ensure_ascii=False, indent=2)
        if item.get("type") in ("error", "turn.failed"):
            return "OBSERVED CLI ERROR", json.dumps(item, ensure_ascii=False, indent=2)
    if complete:
        return kind.upper().replace("_", " "), json.dumps(event, ensure_ascii=False, indent=2)
    return None


def load_take(take):
    complete = json.loads((take / "capture-complete.json").read_text())
    if sha(take / "events.jsonl") != complete["events_sha256"]:
        raise RuntimeError("Recorded events changed after the take was sealed")
    if sha(take / "metadata.json") != complete["metadata_sha256"]:
        raise RuntimeError("Recorded metadata changed after the take was sealed")
    events = [json.loads(line) for line in (take / "events.jsonl").read_text().splitlines() if line]
    if not events or any(events[i]["t"] > events[i+1]["t"] for i in range(len(events)-1)):
        raise RuntimeError("Capture must contain a monotonic, ordered real event timeline")
    duration = float(complete["duration_seconds"])
    if duration <= 0:
        raise RuntimeError("Capture duration must be positive")
    for event in events:
        if event["kind"] != "artifact_observed":
            continue
        artifact = (take / event["artifact"]).resolve()
        if not artifact.is_relative_to(take) or not artifact.is_file():
            raise RuntimeError("Observed artifact path escaped or is missing")
        if sha(artifact) != event.get("published_sha256", event["sha256"]):
            raise RuntimeError("Observed artifact bytes changed after capture")
    return complete, events


def states(events):
    state = {"scene": None, "status": "Capture started", "heading": "RECORDED CLI WORKFLOW",
             "body": "Waiting for the first operator prompt.", "image": None, "measurements": None}
    result = []
    for event_number, event in enumerate(events, 1):
        state = dict(state)
        state["event_number"] = event_number
        kind = event["kind"]
        if kind == "phase_started":
            state.update(scene=event["scene"], status="Codex CLI phase running")
        elif kind == "phase_finished":
            state["status"] = f"Observed CLI exit {event['returncode']}"
        elif kind == "phase_interrupted":
            state["status"] = "Capture phase interrupted"
        elif kind == "phase_launch_failed":
            state["status"] = "CLI launch failed; no subprocess started"
        elif kind == "artifact_observed":
            if Path(event["artifact"]).suffix.lower() in {".png", ".jpg", ".jpeg"}:
                state["image"] = event
            if "measurement_summary" in event:
                state["measurements"] = event
        elif kind == "capture_finished":
            state["status"] = "Recorded capture complete"
        shown = message(event)
        if shown:
            state["heading"], state["body"] = shown
        result.append((event["t"], state))
    return result


IMAGE_CACHE = {}


def render_frame(take, state, source_seconds, video_seconds, duration, compressed):
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.text((48, 28), "Codex  /  Physical engineering", font=ft(39), fill=TEXT)
    draw.text((48, 88), "Recorded Codex CLI event replay", font=ft(30), fill=BLUE)
    disclosure = ("TEACHING CUT · compressed recorded timeline" if compressed else
                  "FULL REPLAY · original elapsed time and waits")
    draw.text((1080, 38), disclosure, font=ft(25), fill=GOLD if compressed else GREEN)
    draw.text((1080, 86), f"Capture elapsed {clock(source_seconds)}  |  Video {clock(video_seconds)}",
              font=ft(25, True), fill=MUTED)
    draw.line((48, 142, 1872, 142), fill="#354858", width=2)
    scene = state.get("scene")
    title = f"{scene}  {SCENES[scene]}" if scene else "Preflight"
    draw.text((48, 165), title, font=ft(34), fill=TEXT)
    draw.text((1080, 171), state["status"], font=ft(24), fill=MUTED)
    draw.rounded_rectangle((48, 226, 1050, 988), radius=14, fill=PANEL)
    draw.rounded_rectangle((1080, 226, 1872, 988), radius=14, fill=PANEL)
    draw.text((76, 250), state["heading"], font=ft(22), fill=BLUE)
    rows = lines_for(state["body"], 26, 940, mono=True)
    for number, line in enumerate(rows[:19]):
        draw.text((76, 295 + 34 * number), line, font=ft(26, True), fill=TEXT)
    if len(rows) > 19:
        draw.text((76, 951), "Excerpt shown · complete content in transcript", font=ft(21), fill=GOLD)
    draw.text((1108, 250), "LATEST OBSERVED CAD OUTPUT", font=ft(22), fill=BLUE)
    observed = state.get("image")
    if observed:
        key = observed["artifact"]
        if key not in IMAGE_CACHE:
            with Image.open(take / key) as source:
                picture = source.convert("RGB")
                picture.thumbnail((736, 435), Image.Resampling.LANCZOS)
                IMAGE_CACHE[key] = picture.copy()
        picture = IMAGE_CACHE[key]
        draw.rounded_rectangle((1108, 294, 1844, 742), radius=8, fill="#E8ECEF")
        image.paste(picture, (1108 + (736-picture.width)//2, 294 + (448-picture.height)//2))
        for i, line in enumerate(lines_for(observed["label"], 25, 736)[:2]):
            draw.text((1108, 758 + i*30), line, font=ft(25), fill=TEXT)
        draw.text((1108, 826), f"Observed {clock(observed['t'])} · SHA256 {observed['sha256'][:12]}",
                  font=ft(19, True), fill=MUTED)
    else:
        draw.text((1120, 438), "Waiting for an observed CAD snapshot.", font=ft(26), fill=MUTED)
        draw.text((1120, 484), "Only files captured in this take appear here.", font=ft(22), fill=MUTED)
    measured = state.get("measurements")
    if measured:
        values = measured["measurement_summary"]
        status = str(values.get("status", "unknown")).upper()
        details = []
        if isinstance(values.get("cavity_width_mm"), (int, float)):
            details.append(f"Cavity {values['cavity_width_mm']:.2f} mm")
        if isinstance(values.get("polymer_mass_g"), (int, float)):
            details.append(f"Polymer {values['polymer_mass_g']:.2f} g")
        draw.text((1108, 870), f"{measured['scene']} checks: {status} · observed {clock(measured['t'])}", font=ft(24),
                  fill=GREEN if status == "PASS" else GOLD)
        draw.text((1108, 913), "  ·  ".join(details), font=ft(23), fill=TEXT)
        gap = values.get("lateral_clearance_mm")
        if isinstance(gap, list) and len(gap) == 2:
            draw.text((1108, 949), f"Lateral clearance {gap[0]:.2f} / {gap[1]:.2f} mm",
                      font=ft(22), fill=MUTED)
    draw.rectangle((48, 1016, 1872, 1021), fill="#354858")
    draw.rectangle((48, 1016, 48 + int(1824 * min(1, source_seconds / duration)), 1021), fill=BLUE)
    draw.text((48, 1037), f"Source event {state.get('event_number', 0)} · synthetic civilian subsystem · actual CLI events and observed artifacts · no native desktop footage",
              font=ft(21), fill=MUTED)
    return image


def caption(event):
    kind = event["kind"]
    if kind == "phase_started":
        return f"Scene {event['scene']}: {SCENES[event['scene']]}. Operator began the recorded phase."
    if kind == "subprocess_started":
        return "The actual Codex CLI subprocess started."
    if kind == "operator_input":
        return "Operator supplied this prompt for the recorded Codex CLI phase."
    if kind == "phase_finished":
        return f"Codex CLI phase {event['scene']} finished with observed exit code {event['returncode']}."
    if kind == "phase_interrupted":
        return f"Codex CLI phase {event['scene']} was interrupted; this event remains in the capture."
    if kind == "phase_launch_failed":
        return f"Codex CLI phase {event['scene']} failed to launch; no subprocess started."
    if kind == "artifact_observed":
        status = event.get("measurement_summary", {}).get("status")
        return f"Observed artifact: {event['label']}." + (f" Recorded verification status: {status}." if status else "")
    if kind == "capture_finished":
        return "Recorded Codex CLI capture complete."
    return None


def subtitles(events, duration, scale):
    cues = [(event["t"] * scale, caption(event)) for event in events if caption(event)]
    # Prompts follow phase-start by milliseconds: keep the latest of cues that
    # occur within one second, rather than inventing time for unreadable flashes.
    readable = []
    for start, text in cues:
        if readable and start - readable[-1][0] < 1:
            previous_start, previous_text = readable.pop()
            readable.append((start, (previous_text + " " + text)[-300:]))
        else:
            readable.append((start, text))
    output = []
    for index, (start, text) in enumerate(readable):
        end = min(duration * scale, start + 6, readable[index+1][0] if index+1 < len(readable) else duration * scale)
        if end <= start:
            continue
        output.append(f"{len(output)+1}\n{timestamp(start)} --> {timestamp(end)}\n{text}\n")
    return "\n".join(output)


def encode(take, output, events, duration, scale, ffmpeg):
    length = duration * scale
    snapshots = states(events)
    times = [row[0] for row in snapshots]
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{WIDTH}x{HEIGHT}",
               "-r", "1", "-i", "pipe:0", "-t", f"{length:.6f}",
               "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "19",
               "-pix_fmt", "yuv420p", "-r", "2", "-threads", "2", "-movflags", "+faststart", str(output)]
    log = output.with_suffix(".encoder.log")
    with log.open("w") as errors:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=errors)
        try:
            for second in range(math.ceil(length)):
                source_second = min(duration, second / scale)
                index = max(0, bisect_right(times, source_second) - 1)
                state = snapshots[index][1]
                image = render_frame(take, state, source_second, second, duration, scale < 1)
                process.stdin.write(image.tobytes())
            process.stdin.close()
            code = process.wait()
        except BaseException:
            process.terminate()
            process.wait()
            raise
    if code != 0:
        raise RuntimeError(f"Encoding failed; inspect {log}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--take", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ffmpeg", default=shutil.which("ffmpeg"))
    parser.add_argument("--ffprobe", default=shutil.which("ffprobe"))
    parser.add_argument("--teaching-seconds", type=float, default=420)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    take = args.take.resolve(strict=True)
    complete, events = load_take(take)
    duration = complete["duration_seconds"]
    if args.check_only:
        print(json.dumps({"events": len(events), "duration_seconds": duration,
                          "observed_artifacts": sum(e["kind"] == "artifact_observed" for e in events)}))
        return
    if not args.ffmpeg or not args.ffprobe:
        raise RuntimeError("ffmpeg and ffprobe are required")
    if not 360 <= args.teaching_seconds <= 480:
        raise ValueError("Teaching cut target must be 360–480 seconds")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    transcript = ["# Recorded Codex CLI workflow transcript", "",
                  "This is an event replay of actual CLI subprocesses, not native desktop footage.",
                  "Personal paths and credential-bearing URLs are redacted. There is no narrated audio.", ""]
    for event_number, event in enumerate(events, 1):
        heading, content = message(event, complete=True)
        transcript += [f"## Event {event_number} · {clock(event['t'])} · {heading}", "", "```text", content.replace("```", "` ` `"), "```", ""]
    (output / "transcript.md").write_text("\n".join(transcript))
    chapters = [{"scene": event["scene"], "title": SCENES[event["scene"]],
                 "source_seconds": event["t"], "prompt_and_events": "transcript.md"}
                for event in events if event["kind"] == "phase_started"]
    seen = {row["scene"] for row in chapters}
    if seen != set(SCENES):
        raise RuntimeError(f"Full reusable run requires real phases A–E; captured {sorted(seen)}")
    scale = min(1.0, args.teaching_seconds / duration)
    for name, factor in (("full", 1.0), ("teaching-cut", scale)):
        video = output / f"physical-engineering-{name}.mp4"
        print(f"Encoding {name}: {duration * factor:.1f} seconds", flush=True)
        encode(take, video, events, duration, factor, args.ffmpeg)
        (output / f"physical-engineering-{name}.srt").write_text(subtitles(events, duration, factor))
        (output / f"chapters-{name}.txt").write_text("\n".join(
            f"{clock(row['source_seconds'] * factor)} {row['scene']} — {row['title']}" for row in chapters) + "\n")
        subprocess.run([args.ffmpeg, "-nostdin", "-v", "error", "-i", str(video), "-f", "null", "-"], check=True)
        probed = subprocess.run([args.ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(video)],
                                check=True, capture_output=True, text=True)
        probe = json.loads(probed.stdout)
        probe.get("format", {}).pop("filename", None)
        (output / f"probe-{name}.json").write_text(json.dumps(probe, indent=2) + "\n")
    shutil.copyfile(take / "events.jsonl", output / "events.jsonl")
    shutil.copyfile(take / "metadata.json", output / "metadata.json")
    # A refused/interrupted observation can leave an unjournaled staging file.
    # Publish only the artifacts whose observed bytes load_take validated.
    for relative in sorted({event["artifact"] for event in events if event["kind"] == "artifact_observed"}):
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(take / relative, destination)
    (output / "prompts").mkdir()
    chapter_links = ["# Chapter guide", "",
                     "These links point into the actual event replay. The teaching cut compresses the same timeline.", "",
                     "| Scene | Full replay | Teaching cut | Recorded operator prompt |",
                     "|---|---|---|---|"]
    for index, chapter in enumerate(chapters):
        scene = chapter["scene"]
        next_start = chapters[index+1]["source_seconds"] if index+1 < len(chapters) else duration + 1
        operator = next((event for event in events if event["kind"] == "operator_input"
                         and event["scene"] == scene and chapter["source_seconds"] <= event["t"] < next_start), None)
        prompt_name = f"prompts/{index+1:02d}-{scene}.txt"
        if operator:
            (output / prompt_name).write_text(operator["text"] + "\n")
        else:
            raise RuntimeError(f"Scene {scene} has no captured operator input")
        start = chapter["source_seconds"]
        chapter_links.append(f"| {scene} · {chapter['title']} | [Play {clock(start)}](physical-engineering-full.mp4#t={start:.3f}) | "
                             f"[Play {clock(start*scale)}](physical-engineering-teaching-cut.mp4#t={start*scale:.3f}) | "
                             f"[Prompt]({prompt_name}) |")
    chapter_links += ["", "## Observed chapter artifacts", ""]
    for event in events:
        if event["kind"] == "artifact_observed":
            chapter_links.append(f"- {event['scene']} · {clock(event['t'])}: [{event['label']}]({event['artifact']}) "
                                 f"— source SHA-256 `{event['sha256']}`.")
    (output / "chapters.md").write_text("\n".join(chapter_links) + "\n")
    manifest = {"schema_version": 1, "recording_method": "Recorded Codex CLI event replay",
                "native_desktop_capture": False, "source_capture": complete,
                "source_duration_seconds": duration, "teaching_duration_seconds": duration*scale,
                "teaching_target_seconds": args.teaching_seconds, "teaching_target_met": 360 <= duration*scale <= 480,
                "teaching_source_time_multiplier": 1/scale,
                "edits": ["Deterministic 1920x1080 replay layout; CLI content is excerpted on screen and complete in transcript.",
                          "Full version preserves elapsed source time and waits; visual state sampled at one-second intervals.",
                          "Teaching cut applies the disclosed uniform time compression to the same recorded timeline.",
                          "No synthetic events, results, narration, or artifacts are inserted. Event captions accompany both videos."],
                "chapters": chapters,
                "artifacts": [e for e in events if e["kind"] == "artifact_observed"],
                "files": {str(path.relative_to(output)): sha(path) for path in sorted(output.rglob("*")) if path.is_file()}}
    (output / "media-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"output": str(output), "full_seconds": duration,
                      "teaching_seconds": duration*scale, "teaching_target_met": manifest["teaching_target_met"]}))


if __name__ == "__main__":
    main()
