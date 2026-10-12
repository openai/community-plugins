"""Shared event, material and judgment data; SPEC is the behavioral authority."""
from __future__ import annotations
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

SOURCES = ("task_contract", "before_structure", "batch_change", "current_structure", "tool_result", "file_origin")
MATERIAL_MAX_CHARS = 20_000
FEEDBACK_MAX_CHARS = 200
FEEDBACK_FIELD_LIMITS = {"observed": 40, "why": 25, "structure": 61, "required": 35}
FEEDBACK_LIMIT = 3
SILENCE_LIMIT = 2


def json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class ToolFault(Exception):
    def __init__(self, kind: str, detail: str, *, evidence: dict | None = None):
        self.kind = kind
        self.detail = detail
        self.evidence = evidence or {}
        super().__init__(f"{kind}: {detail}")


@dataclass(frozen=True)
class SessionRef:
    session_id: str
    turn_id: str
    cwd: str
    transcript_path: str = ""


@dataclass(frozen=True)
class MaterialLine:
    source: str
    path: str
    line: int
    text: str

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}"


def material_lines(source: str, path: str, text: str, start: int = 1) -> tuple[MaterialLine, ...]:
    return tuple(MaterialLine(source, path, i, line) for i, line in enumerate(text.splitlines(), start))


@dataclass(frozen=True)
class ToolCompleted:
    tool_use_id: str
    tool_name: str
    tool_input: object
    tool_response: object

    @property
    def modification(self) -> str | None:
        value = self.tool_input
        patch = value.get("command") if isinstance(value, dict) else value
        if isinstance(patch, str) and patch.splitlines()[:1] == ["*** Begin Patch"] and patch.rstrip().endswith("*** End Patch"):
            return patch
        if not isinstance(value, dict):
            return None
        for key in ("patch", "diff"):
            if isinstance(value.get(key), str) and value[key].strip():
                return value[key]
        path = value.get("path") or value.get("file_path")
        if not isinstance(path, str) or not path.strip():
            return None
        if all(isinstance(value.get(k), str) for k in ("old_string", "new_string")):
            return f"path: {path}\n[before]\n{value['old_string']}\n[after]\n{value['new_string']}"
        if isinstance(value.get("content"), str):
            return f"path: {path}\n[content]\n{value['content']}"
        return None


def patch_operations(patch: str | None, cwd: str) -> tuple[tuple[str, str, bool], ...] | None:
    """Recognize complete apply_patch file operations; unknown syntax stays Provider-eligible."""
    if not isinstance(patch, str):
        return None
    lines = patch.splitlines()
    if len(lines) < 3 or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        return None
    root = Path(cwd).resolve()
    operations = []
    for line in lines[1:-1]:
        if line == "*** End of File":
            continue
        if not line.startswith("*** "):
            continue
        operation, separator, name = line[4:].partition(": ")
        if not separator or operation not in ("Add File", "Update File", "Delete File", "Move to") or not name:
            return None
        if operation == "Move to" and (not operations or operations[-1][0] != "Update File"):
            return None
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            return None
        relative = path.relative_to(root)
        normalized = os.path.normcase(str(relative))
        filename = relative.name.lower()
        test_directory = any(part.lower() in ("test", "tests", "__tests__")
                             for part in relative.parts[:-1])
        is_test = (filename.startswith("test_") or filename in ("test.py", "tests.py")
                   or filename.endswith("_test.go")
                   or ".test." in filename or ".spec." in filename
                   or (test_directory and relative.suffix.lower() in
                       (".py", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go", ".java", ".cs", ".rb", ".php", ".snap")))
        operations.append((operation, normalized, is_test))
    return tuple(operations) if operations else None


@dataclass(frozen=True)
class MaterialPacket:
    lines: tuple[MaterialLine, ...]
    workspace: str
    transcript_path: str = ""
    previous_nudges: tuple[dict, ...] = ()

    def categories(self) -> dict:
        categories = {}
        for source in SOURCES:
            grouped = []
            for line in self.lines:
                if line.source != source:
                    continue
                if (grouped and grouped[-1]["path"] == line.path
                        and line.line == grouped[-1]["start"] + len(grouped[-1]["lines"])):
                    grouped[-1]["lines"].append(line.text)
                else:
                    grouped.append({"path": line.path, "start": line.line, "lines": [line.text]})
            categories[source] = grouped
        return categories

    @property
    def material_chars(self) -> int:
        return len(json_text({**self.categories(), "previous_nudges": self.previous_nudges}))

    def render(self) -> str:
        return json_text({**self.categories(), "workspace": self.workspace,
                          "transcript_path": self.transcript_path,
                          "previous_nudges": self.previous_nudges})


@dataclass(frozen=True)
class Feedback:
    observed: str
    why: str
    structure: str
    required: str

    @property
    def message(self) -> str:
        return (f"OBSERVED: {self.observed}\nWHY: {self.why}\n"
                f"STRUCTURE: {self.structure}\nREQUIRED: {self.required}")


@dataclass(frozen=True)
class ProviderRun:
    raw_output: str
    materials: tuple[MaterialLine, ...] = ()
    usage: dict = field(default_factory=dict)
    trace: tuple[dict, ...] = ()


def find_git_root(cwd: str) -> str:
    try:
        result = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                                capture_output=True, text=True, encoding="utf-8", timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ToolFault("workspace", str(exc)) from exc
    if result.returncode:
        raise ToolFault("workspace", "工作區必須使用 Git")
    return str(Path(result.stdout.strip()).resolve())
