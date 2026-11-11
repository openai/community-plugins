"""Persist the user's Provider model and reasoning choice."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile

CONFIG_FILE = "config.json"
DEFAULT_REASONING_EFFORT = "medium"
PROVIDERS = {"openai": {"id": "openai", "name": "OpenAI / Codex", "local": False}}


@dataclass(frozen=True)
class UserSettings:
    provider: str = ""
    model: str = ""
    error: str = ""
    reasoning_effort: str = DEFAULT_REASONING_EFFORT


def config_path(data_dir: Path) -> Path:
    return data_dir / CONFIG_FILE


def load_user_settings(data_dir: Path) -> UserSettings:
    try:
        value = json.loads(config_path(data_dir).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return UserSettings()
    except (OSError, ValueError) as exc:
        return UserSettings(error=str(exc))
    if not isinstance(value, dict) or not {"provider", "model"} <= value.keys():
        return UserSettings(error="設定格式錯誤")
    # Read the previous file format only to preserve an existing supported choice.
    if value.keys() - {"provider", "model", "reasoning_effort", "ollama_url", "lens"}:
        return UserSettings(error="設定含未知欄位")
    if not isinstance(value["provider"], str) or not isinstance(value["model"], str):
        return UserSettings(error="Provider 與模型必須是文字")
    if value["provider"] not in ("", "openai", "codex"):
        return UserSettings(error="第一版僅支援 OpenAI／Codex，請重新設定")
    effort = value.get("reasoning_effort", DEFAULT_REASONING_EFFORT)
    if not isinstance(effort, str) or not effort.strip():
        return UserSettings(error="Provider 深度必須是非空文字")
    model = value["model"].strip()
    return UserSettings(value["provider"], "" if model.lower() == "auto" else model,
                        reasoning_effort=effort.strip().lower())


def save_provider(data_dir: Path, provider: str, *, model: str | None = None,
                  reasoning_effort: str | None = None) -> Path:
    if provider not in ("openai", "codex"):
        raise ValueError("第一版僅支援 OpenAI／Codex")
    current = load_user_settings(data_dir)
    model = current.model if model is None else model
    effort = current.reasoning_effort if reasoning_effort is None else reasoning_effort
    if not isinstance(model, str):
        raise ValueError("Provider 模型必須是文字")
    if not isinstance(effort, str) or not effort.strip():
        raise ValueError("Provider 深度必須是非空文字")
    model = model.strip()
    if model.lower() == "auto":
        model = ""
    data_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=data_dir, delete=False) as stream:
        path = Path(stream.name)
        json.dump({"provider": "openai", "model": model,
                   "reasoning_effort": effort.strip().lower()}, stream, ensure_ascii=False)
        stream.write("\n")
    try:
        os.replace(path, config_path(data_dir))
    finally:
        path.unlink(missing_ok=True)
    return config_path(data_dir)


def reset_provider(data_dir: Path) -> Path:
    path = config_path(data_dir)
    path.unlink(missing_ok=True)
    return path
