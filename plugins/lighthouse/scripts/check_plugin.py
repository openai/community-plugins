"""Check the plugin's local skill discovery paths and required frontmatter."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def check_plugin(root: Path) -> list[str]:
    """Return packaging errors without importing or executing any skill helper."""
    manifest: object = json.loads((root / ".codex-plugin/plugin.json").read_text())
    if not isinstance(manifest, dict) or not isinstance(manifest.get("skills"), str):
        return ["plugin.json must declare a skills directory"]
    skill_root = (root / manifest["skills"]).resolve()
    if not skill_root.is_relative_to(root.resolve()) or not skill_root.is_dir():
        return ["plugin.json skills must resolve to a directory within this plugin"]
    skills = sorted(skill_root.glob("*/SKILL.md"))
    if not skills:
        return ["The declared skills directory contains no SKILL.md files"]
    errors = []
    for path in skills:
        content = path.read_text()
        header = re.match(r"\A---\n(.*?)\n---(?:\n|$)", content, re.DOTALL)
        if header is None:
            errors.append(f"{path.relative_to(root)}: missing YAML frontmatter")
            continue
        fields = header.group(1)
        name = re.search(r"^name:\s*(\S+)\s*$", fields, re.MULTILINE)
        if name is None or name.group(1) != path.parent.name:
            errors.append(f"{path.relative_to(root)}: name must match its directory")
        if not re.search(r"^description:\s*\S", fields, re.MULTILINE):
            errors.append(f"{path.relative_to(root)}: description is required")
    return errors


def main() -> None:
    """Validate this checkout and report errors on stderr."""
    errors = check_plugin(Path(__file__).resolve().parents[1])
    if errors:
        sys.stderr.write("\n".join(errors) + "\n")
        raise SystemExit(1)
    sys.stderr.write("Plugin skill paths and frontmatter passed.\n")


if __name__ == "__main__":
    main()
