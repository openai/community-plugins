"""Persist only the supported Provider selection; unsupported old choices are errors."""
from dataclasses import dataclass, asdict
import json
import os
from pathlib import Path
import tempfile

CONFIG_FILE = "config.json"
PROVIDERS = {"openai": {"id": "openai", "name": "OpenAI / Codex", "local": False}}


@dataclass(frozen=True)
class UserSettings:
    provider: str = ""
    model: str = ""
    error: str = ""


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
    if value.keys() - {"provider", "model", "ollama_url", "lens"}:
        return UserSettings(error="設定含未知欄位")
    if not isinstance(value["provider"], str) or not isinstance(value["model"], str):
        return UserSettings(error="Provider 與模型必須是文字")
    if value["provider"] not in ("", "openai", "codex"):
        return UserSettings(error="第一版僅支援 OpenAI／Codex，請重新設定")
    return UserSettings(value["provider"], value["model"])


def save_provider(data_dir: Path, provider: str, *, model: str = "") -> Path:
    if provider not in ("openai", "codex"):
        raise ValueError("第一版僅支援 OpenAI／Codex")
    data_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=data_dir, delete=False) as stream:
        path = Path(stream.name)
        json.dump({"provider": "openai", "model": model}, stream, ensure_ascii=False)
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
