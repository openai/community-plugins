"""Translate Codex task and completed-patch events without inferring Actor intent."""
from .contracts import SessionRef, ToolCompleted, ToolFault
from .core import NudgeCore
from .prompting import delivery_text
from .runtime import active_guard

AUDIT_MARKER_KEY = "_masters_nudge"


def session_from_payload(payload: dict) -> SessionRef:
    for key in ("session_id", "turn_id", "cwd"):
        if not isinstance(payload.get(key), str) or not payload[key].strip():
            raise ToolFault("input", f"事件缺少 {key}")
    return SessionRef(payload["session_id"], payload["turn_id"], payload["cwd"],
                      payload.get("transcript_path") or "")


class CodexAdapter:
    def __init__(self, core: NudgeCore):
        self.core = core

    def process(self, payload: dict) -> dict | None:
        if active_guard():
            return None
        try:
            name = payload.get("hook_event_name")
            if name not in ("UserPromptSubmit", "PostToolUse"):
                return None
            session = session_from_payload(payload)
            if name == "UserPromptSubmit":
                prompt = payload.get("prompt")
                if not isinstance(prompt, str):
                    raise ToolFault("input", "使用者事件缺少 prompt")
                goal = payload.get("goal") or {}
                self.core.start_round(session, prompt, goal.get("objective", "") if isinstance(goal, dict) else "")
                return None
            tool_name = payload.get("tool_name")
            if tool_name != "apply_patch":
                return None
            if not {"tool_use_id", "tool_input", "tool_response"} <= payload.keys():
                raise ToolFault("input", "修改事件資料不完整")
            tool_use_id = payload["tool_use_id"]
            if not isinstance(tool_use_id, str) or not tool_use_id:
                raise ToolFault("input", "工具編號不合法")
            event = ToolCompleted(tool_use_id, tool_name, payload["tool_input"], payload["tool_response"])
            result = self.core.process_event(session, event)
            if result is None:
                return None
            attempt, feedback = result
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": delivery_text(feedback),
                },
                AUDIT_MARKER_KEY: attempt,
            }
        except ToolFault as fault:
            self.core.log_error(str(fault))
            if self.core.settings.strict:
                raise
            return {"systemMessage": f"本輪反饋未執行（{fault.kind}）"}
