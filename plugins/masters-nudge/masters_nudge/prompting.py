"""Fixed instructions and the only Actor-facing feedback rendering."""
from pathlib import Path
from .contracts import Feedback, ToolFault


def delivery_text(feedback: Feedback) -> str:
    return (
        f"Masters’ Nudge\n{feedback.message}\n"
        "First check REQUIRED against the latest task contract and use it to determine the delivered behavior. When adopting STRUCTURE, implement the stated data and responsibility relation and address its affected uses; verify that the actual results satisfy the task requirements."
    )


def load_system_prompt(*, prompt_file: Path) -> str:
    try:
        text = prompt_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ToolFault("configuration", f"無法讀取 Provider 提示：{exc}") from exc
    if not text:
        raise ToolFault("configuration", "Provider 提示是空的")
    return text
