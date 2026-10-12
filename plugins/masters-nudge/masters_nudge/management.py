"""Settings, recent attempts, and read-only dependency diagnostics."""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from .plugin_inventory import runtime_files
from .runtime import RuntimePaths, RuntimeSettings
from .settings import PROVIDERS, config_path, reset_provider, save_provider
from .storage import recent_nudges as read_recent
from .providers import resolve_codex_bin, _provider_process_kwargs


def list_providers() -> dict:
    return {"providers": list(PROVIDERS.values())}


def get_provider(*, host="codex", environ=None) -> dict:
    settings = RuntimeSettings.from_env(environ=environ, host=host or "codex")
    return {"provider": settings.provider, "model": settings.model,
            "source": settings.configuration_source, "error": settings.configuration_error,
            "path": str(config_path(settings.paths.settings_dir))}


def configure_provider(provider: str, *, model: str = "", environ=None) -> dict:
    paths = RuntimePaths.resolve(environ=environ)
    try:
        path = save_provider(paths.settings_dir, provider, model=model)
        return {"saved": True, "path": str(path), "error": ""}
    except (OSError, ValueError) as exc:
        return {"saved": False, "error": str(exc)}


def reset_provider_config(*, environ=None) -> dict:
    paths = RuntimePaths.resolve(environ=environ)
    try:
        reset_provider(paths.settings_dir)
        return {"reset": True, "error": ""}
    except OSError as exc:
        return {"reset": False, "error": str(exc)}


def recent_nudges(limit=20, *, environ=None) -> dict:
    paths = RuntimePaths.resolve(environ=environ)
    try:
        return {"nudges": read_recent(paths.data_dir, limit=limit), "limit": limit, "error": ""}
    except (OSError, ValueError) as exc:
        return {"nudges": [], "limit": limit, "error": str(exc)}


def _run_cli(command: list[str], environment):
    shell = command[0].lower().endswith((".cmd", ".bat"))
    return subprocess.run(subprocess.list2cmdline(command) if shell else command,
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=10, shell=shell, env=dict(environment), **_provider_process_kwargs())


def _probe_mcp(transport, env):
    """Probe Codex's resolved transport, not a separately constructed launch path."""
    if transport.get("type") != "stdio":
        raise ValueError("Masters' Nudge requires a local stdio MCP transport")
    # Codex inherits a platform baseline in addition to configured env_vars.
    # In particular, Windows Python needs SYSTEMROOT before it can initialize.
    baseline = (
        "PATH", "PATHEXT", "SHELL", "COMSPEC", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE",
        "USERNAME", "USERDOMAIN", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
        "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "PROGRAMDATA",
        "LOCALAPPDATA", "APPDATA", "TEMP", "TMP", "TMPDIR", "POWERSHELL", "PWSH",
    ) if os.name == "nt" else (
        "HOME", "LOGNAME", "PATH", "SHELL", "USER", "__CF_USER_TEXT_ENCODING",
        "LANG", "LC_ALL", "TERM", "TMPDIR", "TZ",
    )
    names = {*baseline, *transport.get("env_vars", [])}
    normalize = str.upper if os.name == "nt" else str
    names = {normalize(name) for name in names}
    child_env = {normalize(key): value for key, value in env.items()
                 if normalize(key) in names}
    child_env.update({normalize(key): value for key, value in (transport.get("env") or {}).items()})
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "masters-nudge-doctor", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    result = subprocess.run([transport["command"], *transport.get("args", [])],
                            cwd=transport.get("cwd"), env=child_env,
                            input="".join(json.dumps(row) + "\n" for row in requests),
                            capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=10, **_provider_process_kwargs())
    if result.returncode:
        raise ValueError(result.stderr.strip()[:500] or f"MCP exited with {result.returncode}")
    replies = {row.get("id"): row for line in result.stdout.splitlines()
               for row in [json.loads(line)] if isinstance(row, dict)}
    initialized = replies.get(1, {}).get("result", {}).get("serverInfo", {}).get("name")
    tools = replies.get(2, {}).get("result", {}).get("tools", [])
    if initialized != "masters-nudge" or not any(tool.get("name") == "review_patch" for tool in tools):
        raise ValueError("MCP initialization or review_patch discovery failed")
    return True


def doctor(root: Path, host="codex", *, environ=None, **_unused) -> dict:
    env = os.environ if environ is None else environ
    settings = RuntimeSettings.from_env(root, environ=env, host=host)
    binary = env.get("CODEX_CLI_PATH") or shutil.which("codex.exe", path=env.get("PATH")) or shutil.which("codex", path=env.get("PATH"))
    missing = [name for name in runtime_files() if not (root / name).is_file()]
    authenticated = False
    installed = None
    mcp_enabled = None
    mcp_ready = None
    transport = None
    error = settings.configuration_error
    if binary:
        try:
            auth = _run_cli([binary, "login", "status"], env)
            authenticated = auth.returncode == 0 and "logged in" in (auth.stdout + auth.stderr).lower()
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            error = error or str(exc)
        try:
            listing = _run_cli([binary, "plugin", "list", "--json"], env)
            if listing.returncode:
                raise ValueError(listing.stderr.strip()[:500] or "Codex plugin inventory query failed")
            value = json.loads(listing.stdout)
            entries = value.get("installed", []) if isinstance(value, dict) else value
            installed = any(row.get("enabled") is True and
                            str(row.get("pluginId", "")).startswith("masters-nudge@")
                            for row in entries)
        except (OSError, subprocess.SubprocessError, ValueError, TypeError) as exc:
            error = error or str(exc)
        try:
            listing = _run_cli([binary, "mcp", "list", "--json"], env)
            if listing.returncode:
                raise ValueError(listing.stderr.strip()[:500] or "Codex MCP inventory query failed")
            entries = json.loads(listing.stdout)
            server = next((row for row in entries if row.get("name") == "masters_nudge"), None)
            mcp_enabled = bool(server and server.get("enabled"))
            if mcp_enabled:
                transport = server["transport"]
                mcp_ready = False
                mcp_ready = _probe_mcp(transport, env)
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
            error = error or str(exc)
    return {"core_ready": bool(not error and not missing and binary and authenticated and installed and mcp_ready),
            "provider": settings.provider, "model": settings.model,
            "codex_cli": binary or "", "provider_authenticated": authenticated,
            "plugin_enabled": installed, "mcp_enabled": mcp_enabled,
            "mcp_ready": mcp_ready, "mcp_transport": transport,
            "missing_files": missing, "error": error,
            "unverified": ["PostToolUse 事件支援與完整反饋交付，須實測確認"] }
