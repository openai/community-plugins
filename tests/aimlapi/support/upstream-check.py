#!/usr/bin/env python3
"""Repository checks. Standard library only.

  python3 tools/check.py          # offline: frontmatter, structure, versions, banned content, routes
  python3 tools/check.py --live   # also: routes and model ids against the live API

Exit code 1 if anything fails.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.aimlapi.com"
CLIENT = "aimlapi-skills-check/1.0"
SECTIONS = ["## When to use", "## Steps", "## Rules", "## Errors", "## Check before done", "## Reference"]
NAME_RE = re.compile(r"^aimlapi-[a-z0-9]+(-[a-z0-9]+)*$")
MAX_SKILL_LINES = 300
MAX_SKILL_WORDS = 900
MAX_DESCRIPTION = 500
TEXT_SUFFIXES = {".md", ".py", ".mjs", ".js", ".ts", ".json", ".yaml", ".yml", ".txt", ".sh", ".ps1", ".toml", ""}

ALLOWED_HOSTS = {
    "api.aimlapi.com", "docs.aimlapi.com", "mcp.aimlapi.com", "aimlapi.com", "cdn.aimlapi.com", "help.aimlapi.com",
    "github.com", "cli.github.com", "agentskills.io", "code.claude.com", "platform.openai.com",
    "nodejs.org", "docs.npmjs.com", "registry.npmjs.org", "www.npmjs.com", "antigravity.google", "semver.org", "developercertificate.org", "www.apache.org",
    "example.com",
}
# (pattern, why) - anything that must never be published from this repository.
BANNED = [
    (r"api-dev\.|\.dev\.aimlapi|staging\.aimlapi|localhost:\d{4}", "non-public host"),
    (r"\bAIM-\d+\b", "internal ticket id"),
    (r"\bsk-[A-Za-z0-9_-]{20,}", "something that looks like an API key"),
    (r"whsec_[A-Za-z0-9+/=]{16,}", "something that looks like a webhook secret"),
    (r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{32}(?![0-9A-Fa-f])", "something that looks like an AI/ML API key (32 hex characters)"),
    (r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}", "a literal bearer token"),
    (r"(?i)\b[\w-]*(?:api[_-]?key|secret|token|password)[\w-]*[\"']?\s*[:=]\s*[\"'][A-Za-z0-9._~+/=-]{20,}[\"']",
     "a literal secret assigned in code or config"),
    (r"\b(BATCHES|WEBHOOKS|SANDBOX|ENDPOINTS|STORAGE)_[A-Z_]{3,}\b", "platform environment variable"),
    (r"\b(NATS|JetStream|ClickHouse|SigNoz|BullMQ)\b", "internal infrastructure name"),
    (r"\$\s?\d+(\.\d+)?\s*(/|per)\s*(1M|M\b|million|1k|token)", "a price"),
    (r"\.claude/skills|apps/api/gateway|libs/(core|infra|integrations)", "internal source path"),
]
# Files allowed to contain an otherwise banned pattern, with the pattern they may contain.
EXCEPTIONS = {
    "tools/check.py": {"*"},
    "AGENTS.md": {"localhost"},
    "cli/aimlapi-skills.mjs": {".claude/skills"},  # Claude Code's own skills folder, the install target
    "install/install.sh": {".claude/skills"},
    "tests/test_installers.py": {".claude/skills"},
}

ROUTE_RE = re.compile(r"(?<![\w.$])/(?:v\d+(?:/[A-Za-z0-9_.{}<>:()\['\]-]+)*|model/[A-Za-z0-9_{}<>:./()\['\]-]+|models/metrics\b)")
MODEL_RES = [
    re.compile(r'"model"\s*:\s*"([^"]+)"'),
    re.compile(r"\bmodel\s*[=:]\s*\"([^\"]+)\""),
    re.compile(r"[?&]id=([A-Za-z0-9_./:-]+)"),
    re.compile(r"models\.py (?:params|price|status) ([A-Za-z0-9_./:-]+)"),
]

failures: list[str] = []


def fail(where: str, msg: str) -> None:
    failures.append(f"{where}: {msg}")


def files() -> list[Path]:
    out = []
    for path in ROOT.rglob("*"):
        rel = path.relative_to(ROOT)
        if path.is_file() and rel.parts[0] not in {".git", "node_modules"} and path.suffix in TEXT_SUFFIXES and path.name != "LICENSE":
            out.append(path)
    return sorted(out)


def frontmatter(text: str) -> dict:
    if not text.startswith("---\n"):
        return {}
    block = text[4 : text.index("\n---", 4)]
    data: dict = {}
    current = None
    for line in block.splitlines():
        if not line.strip():
            continue
        if line.startswith("  ") and current is not None:
            k, _, v = line.strip().partition(":")
            data[current][k.strip()] = v.strip().strip('"')
            continue
        k, _, v = line.partition(":")
        k, v = k.strip(), v.strip()
        if v and v[0] not in "\"'" and (": " in v or " #" in v or v[0] in "[{&*!|>%@`"):
            data.setdefault("_yaml_errors", []).append(f"{k}: quote the value; ': ', ' #' or a leading special character is invalid plain YAML")
        if v == "":
            data[k] = {}
            current = k
        else:
            data[k] = v.strip('"')
            current = None
    return data


def repo_version() -> str:
    versions = {
        ".claude-plugin/plugin.json": json.loads((ROOT / ".claude-plugin/plugin.json").read_text())["version"],
        ".claude-plugin/marketplace.json": json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())["metadata"]["version"],
        ".cursor-plugin/plugin.json": json.loads((ROOT / ".cursor-plugin/plugin.json").read_text())["version"],
        ".cursor-plugin/marketplace.json": json.loads((ROOT / ".cursor-plugin/marketplace.json").read_text())["metadata"]["version"],
        "package.json": json.loads((ROOT / "package.json").read_text())["version"],
        "gemini-extension.json": json.loads((ROOT / "gemini-extension.json").read_text())["version"],
        ".codex-plugin/plugin.json": json.loads((ROOT / ".codex-plugin/plugin.json").read_text())["version"],
        ".antigravity-plugin/plugin.json": json.loads((ROOT / ".antigravity-plugin/plugin.json").read_text())["version"],
    }
    if len(set(versions.values())) != 1:
        fail("versions", f"manifests disagree: {versions}")
    return versions[".claude-plugin/plugin.json"]


def check_skills(version: str) -> None:
    dirs = sorted(p.parent for p in list(ROOT.glob("skills/*/SKILL.md")) + list(ROOT.glob("preview/*/SKILL.md")))
    if not dirs:
        fail("skills", "no skills found")
    for d in dirs:
        rel = d.relative_to(ROOT)
        text = (d / "SKILL.md").read_text()
        fm = frontmatter(text)
        for err in fm.get("_yaml_errors", []):
            fail(str(rel), err)
        name = fm.get("name", "")
        if name != d.name:
            fail(str(rel), f"name {name!r} does not match the folder")
        if not NAME_RE.match(name) or "--" in name or len(name) > 64:
            fail(str(rel), f"name {name!r} breaks the naming rules")
        desc = fm.get("description", "")
        if not 1 <= len(desc) <= 1024:
            fail(str(rel), f"description is {len(desc)} characters (1-1024)")
        if not desc.startswith("Use when"):
            fail(str(rel), "description must start with 'Use when' and list triggers only, not the workflow")
        if len(desc) > MAX_DESCRIPTION:
            fail(str(rel), f"description is {len(desc)} characters; keep it under {MAX_DESCRIPTION}")
        if fm.get("license") != "Apache-2.0":
            fail(str(rel), "license must be Apache-2.0")
        meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
        if meta.get("version") != version:
            fail(str(rel), f"metadata.version {meta.get('version')!r} != repository version {version!r}")
        in_preview = rel.parts[0] == "preview"
        if in_preview != (meta.get("status") == "preview"):
            fail(str(rel), "metadata.status: preview must be set exactly on skills under preview/")
        positions = {s: text.find("\n" + s + "\n") for s in SECTIONS}
        missing = [s for s, p in positions.items() if p == -1]
        if missing:
            fail(str(rel), f"missing sections: {missing}")
        elif not positions["## When to use"] < min(positions[s] for s in SECTIONS[1:4]) or not (
            max(positions[s] for s in SECTIONS[:4]) < positions["## Check before done"] < positions["## Reference"]
        ):
            fail(str(rel), "sections out of order: '## When to use' first, then the others, then '## Check before done' and '## Reference' last")
        lines = text.count("\n")
        if lines > MAX_SKILL_LINES:
            fail(str(rel), f"SKILL.md has {lines} lines (max {MAX_SKILL_LINES}); move detail to references/")
        words = len(text.split())
        if words > MAX_SKILL_WORDS:
            fail(str(rel), f"SKILL.md has {words} words (max {MAX_SKILL_WORDS}); move tables and detail to references/")
        for bare in re.findall(r"(?m)^\s*(scripts/\S+\.(?:py|mjs|sh))\b", text):
            fail(str(rel), f"call {bare} through its interpreter (python3/node/bash): packagers may drop the executable bit")
        for required in ("README.md", "agents/openai.yaml"):
            if not (d / required).exists():
                fail(str(rel), f"missing {required}")
        for link in re.findall(r"\]\(((?:references|scripts)/[^)#]+)\)", text):
            if not (d / link).exists():
                fail(str(rel), f"broken link {link}")


def content_problems(rel: str, text: str) -> list[tuple[str, str]]:
    """(where, why) for every banned pattern or unknown host in one file's text."""
    allowed = EXCEPTIONS.get(rel, set())
    if "*" in allowed:
        return []
    found = []
    for pattern, why in BANNED:
        for m in re.finditer(pattern, text):
            if not any(m.group(0) in a or a in m.group(0) for a in allowed):
                line = text.count("\n", 0, m.start()) + 1
                found.append((f"{rel}:{line}", f"{why}: {m.group(0)!r}"))
    for host in re.findall(r"https?://([A-Za-z0-9.-]+)", text):
        host = host.rstrip(".").lower()
        if host and re.search(r"[a-z]", host) and host not in ALLOWED_HOSTS:
            found.append((rel, f"host not on the allowlist: {host}"))
    return found


def check_content() -> None:
    for path in files():
        rel = str(path.relative_to(ROOT))
        for where, why in content_problems(rel, path.read_text(errors="replace")):
            fail(where, why)


def normalize(route: str) -> str:
    route = re.sub(r"\{[^}]*\}|<[^>]*>|:[A-Za-z_]+", "{}", route)
    route = route.split("(")[0] if "{" not in route else route
    return route.rstrip("/.,:;)]'")


def allowed_routes() -> dict[str, tuple[str, str]]:
    out = {}
    for line in (ROOT / "tools/allowed-routes.txt").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            path, state, source = line.split()
            out[path] = (state, source)
    return out


def mentioned_routes() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in files():
        rel = path.relative_to(ROOT)
        if rel.parts[0] not in {"skills", "preview"} and rel.name != "README.md":
            continue
        for m in ROUTE_RE.finditer(path.read_text()):
            found.setdefault(normalize(m.group(0)), set()).add(str(rel))
    return found


def check_routes() -> None:
    allowed = allowed_routes()
    for route, where in sorted(mentioned_routes().items()):
        if route not in allowed:
            fail(", ".join(sorted(where)), f"route {route} is not in tools/allowed-routes.txt")
            continue
        state, _ = allowed[route]
        live_users = [w for w in where if not w.startswith("preview/")]
        if state == "preview" and live_users:
            fail(", ".join(sorted(live_users)), f"route {route} is preview-only but used outside preview/")


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": CLIENT, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def check_live() -> None:
    spec = fetch(API + "/docs-yaml").decode()
    openapi = {normalize(m) for m in re.findall(r"^  (/\S+):\s*$", spec, re.M)}
    pages: dict[str, str] = {}
    for route, (state, source) in allowed_routes().items():
        if state != "live":
            continue
        if source == "openapi":
            if route not in openapi:
                fail("live", f"{route} is no longer in the public OpenAPI description")
            continue
        if source not in pages:
            pages[source] = fetch(source).decode(errors="replace")
        probe = route.split("{}")[0].rstrip("/") or route
        if probe not in pages[source]:
            fail("live", f"{route} is not mentioned on {source}")

    ids: dict[str, set[str]] = {}
    for path in files():
        rel = path.relative_to(ROOT)
        if rel.parts[0] not in {"skills", "preview"}:
            continue
        for rx in MODEL_RES:
            for m in rx.finditer(path.read_text()):
                model = m.group(1).removeprefix("sandbox/")
                if "/" in model and not re.search(r"[…<>{}]|\.\.\.|does-not-exist|^openai/gpt-4o-mini/", model):
                    ids.setdefault(model, set()).add(str(rel))
    known: set[str] = set()
    for chunk in [sorted(ids)[i : i + 20] for i in range(0, len(ids), 20)]:
        query = urllib.parse.urlencode({"id": ",".join(chunk)})
        for entry in json.loads(fetch(f"{API}/v1/models?{query}"))["data"]:
            known.add(entry["id"])
            known.update(entry.get("aliases", []))
    for model, where in sorted(ids.items()):
        if model not in known:
            fail(", ".join(sorted(where)), f"model {model} is not in GET /v1/models")
    live_routes = sum(1 for state, _ in allowed_routes().values() if state == "live")
    print(f"live: {live_routes} routes and {len(ids)} model ids checked ({', '.join(sorted(ids))})")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--live", action="store_true", help="also check routes and model ids against the live API")
    args = p.parse_args()

    version = repo_version()
    check_skills(version)
    check_content()
    check_routes()
    if args.live:
        check_live()

    for f in failures:
        print(f"FAIL {f}")
    print(f"{'FAILED' if failures else 'OK'}: {len(failures)} problem(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
