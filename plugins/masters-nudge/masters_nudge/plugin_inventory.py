"""The source inventory is the only owner of packaged runtime membership."""
GENERATED_FILES = (
    "LICENSE", "buddy-prompt.txt", "launch.py", "hook_entry.py", "mcp_entry.py", "masters_nudge_cli.py", "nudge-schema.json",
    "masters_nudge/__init__.py", "masters_nudge/codex_adapter.py", "masters_nudge/contracts.py",
    "masters_nudge/core.py", "masters_nudge/evidence.py", "masters_nudge/management.py",
    "masters_nudge/plugin_inventory.py", "masters_nudge/provider_contract.py",
    "masters_nudge/prompting.py", "masters_nudge/providers.py", "masters_nudge/read_only_repo_mcp.py",
    "masters_nudge/runtime.py", "masters_nudge/settings.py", "masters_nudge/storage.py",
    "masters_nudge/windows_process.py",
)
STATIC_FILES = (
    "README.md", ".codex-plugin/plugin.json", ".mcp.json", "hooks/hooks.json",
    "skills/doctor/SKILL.md", "skills/select-provider/SKILL.md", "skills/recent-nudges/SKILL.md",
)
RETIRED_FILES = (
    "hooks/run_python.cmd", "hooks/run_python.sh",
    ".claude-plugin/plugin.json", "hooks/claude.json", "claude_prompt.py", "source_context.py",
    "masters_nudge/claude_adapter.py", "masters_nudge/local_ollama.py",
)

def package_files(*, source=None):
    if source == "generated":
        return GENERATED_FILES
    if source == "static":
        return STATIC_FILES
    return GENERATED_FILES + STATIC_FILES

def runtime_files():
    return tuple(name for name in GENERATED_FILES if name != "LICENSE")
