"""Pack known facts; the Provider chooses relevant repository structure."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
from .contracts import (
    MATERIAL_MAX_CHARS, MaterialPacket, SessionRef, ToolCompleted, ToolFault,
    find_git_root, json_text, material_lines, patch_operations,
)


def capture_file_baseline(session: SessionRef) -> dict:
    """Record file existence before Actor edits, including nonignored untracked files."""
    root = Path(session.cwd).resolve()
    baseline = {"workspace": str(root), "turn_id": session.turn_id, "files": None}
    try:
        root = Path(find_git_root(session.cwd)).resolve()
        baseline["workspace"] = str(root)
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True, timeout=15,
        )
        if result.returncode == 0:
            baseline["files"] = sorted({
                os.path.normcase(name).replace("\\", "/")
                for name in result.stdout.decode("utf-8").split("\0")
                if name and (root / name).is_file()
            })
    except (ToolFault, OSError, UnicodeError, subprocess.SubprocessError):
        pass
    return baseline


def file_origin_material(session: SessionRef, task: dict, events: tuple[ToolCompleted, ...], workspace: str):
    """Attach existence facts only for patch paths; absence of a snapshot stays unknown."""
    baseline = json.loads(task.get("file_baseline", "{}"))
    root = Path(workspace).resolve()
    known = (isinstance(baseline.get("files"), list)
             and Path(baseline["workspace"]).resolve() == root)
    files = set(baseline["files"]) if known else set()
    seen = set()
    for event in events:
        for _, name, _ in patch_operations(event.modification, session.cwd) or ():
            path = (Path(session.cwd) / name).resolve()
            if not path.is_relative_to(root):
                continue
            relative = path.relative_to(root).as_posix()
            normalized = os.path.normcase(relative).replace("\\", "/")
            if normalized in seen:
                continue
            seen.add(normalized)
            yield material_lines("file_origin", relative, json_text({
                "existed_at_task_start": normalized in files if known else None,
                "baseline_turn_id": baseline.get("turn_id") if known else None,
            }))


def changed_file_material(patch: str, cwd: str, workspace: str):
    """Transport complete changed files only when Git permits reading them."""
    operations = patch_operations(patch, cwd) or ()
    root = Path(workspace).resolve()
    for operation, name, _ in operations:
        if operation not in ("Update File", "Move to"):
            continue
        path = (Path(cwd) / name).resolve()
        if not path.is_relative_to(root) or ".git" in path.relative_to(root).parts or not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        try:
            if path.stat().st_size > MATERIAL_MAX_CHARS:
                continue
            listed = subprocess.run(
                ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", relative],
                capture_output=True, timeout=15,
            )
            ignored = subprocess.run(
                ["git", "-C", str(root), "check-ignore", "--no-index", "--", relative],
                capture_output=True, timeout=15,
            )
            if (listed.returncode or relative.encode("utf-8") not in listed.stdout.split(b"\0")
                    or ignored.returncode != 1):
                continue
            yield material_lines("current_structure", relative, path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, subprocess.SubprocessError):
            continue


def value_lines(source: str, path: str, value: object):
    """Keep returned text verbatim instead of hiding it behind JSON escapes."""
    if isinstance(value, str):
        return material_lines(source, path, value)
    if isinstance(value, dict) and value:
        lines = []
        for key, item in value.items():
            child = f"{path}/{key}"
            lines.extend(material_lines(source, child, item if isinstance(item, str) else json_text(item)))
        return tuple(lines)
    return material_lines(source, path, json_text(value))


def build_packet(session: SessionRef, task: dict, events: tuple[ToolCompleted, ...]) -> MaterialPacket:
    lines = []
    workspace = find_git_root(session.cwd)
    if task["goal"] != task["request"]:
        lines.extend(material_lines("task_contract", "task/original", task["goal"]))
    lines.extend(material_lines("task_contract", "task/latest", task["request"]))
    for event in events:
        if event.modification is not None:
            lines.extend(material_lines("batch_change", f"tool/{event.tool_use_id}/input", event.modification))
        lines.extend(material_lines("tool_result", f"tool/{event.tool_use_id}/name", event.tool_name))
        if event.modification is None:
            lines.extend(value_lines("tool_result", f"tool/{event.tool_use_id}/input", event.tool_input))
        lines.extend(value_lines("tool_result", f"tool/{event.tool_use_id}/output", event.tool_response))
    for material in file_origin_material(session, task, events, workspace):
        lines.extend(material)
    packet = MaterialPacket(
        tuple(lines), workspace, session.transcript_path,
        previous_nudges=tuple(task.get("previous_nudges", ())),
    )
    if packet.material_chars < MATERIAL_MAX_CHARS:
        for event in events:
            if event.modification:
                for material in changed_file_material(event.modification, session.cwd, packet.workspace):
                    candidate = replace(packet, lines=packet.lines + material)
                    if candidate.material_chars <= MATERIAL_MAX_CHARS:
                        packet = candidate
    packet = fit_packet(packet)
    if packet.material_chars <= MATERIAL_MAX_CHARS:
        return packet
    # Only an explicit successful exit permits dropping verbose output.
    # Arbitrary text is not classified as success by keyword matching.
    compressed_paths = {f"tool/{event.tool_use_id}/output": event.tool_response
                        for event in events if isinstance(event.tool_response, dict)
                        and type(event.tool_response.get("exit_code")) is int
                        and event.tool_response["exit_code"] == 0}
    kept = [line for line in packet.lines if not any(
        line.path == path or line.path.startswith(path + "/") for path in compressed_paths)]
    for path in compressed_paths:
        kept.extend(material_lines("tool_result", f"{path}/summary", '{"exit_code":0,"output_omitted":true}'))
    return fit_packet(replace(packet, lines=tuple(kept)))


def fit_packet(packet: MaterialPacket) -> MaterialPacket:
    if packet.material_chars <= MATERIAL_MAX_CHARS:
        return packet
    packet = replace(packet, lines=tuple(line for line in packet.lines if line.source != "before_structure"))
    if packet.material_chars <= MATERIAL_MAX_CHARS:
        return packet
    # Necessary originals remain intact even when they consume the whole target.
    return packet
