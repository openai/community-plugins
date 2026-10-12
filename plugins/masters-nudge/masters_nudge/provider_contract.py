"""Invalid output raises a fault; it cannot become silence."""
import json
from .contracts import Feedback, FEEDBACK_FIELD_LIMITS, FEEDBACK_MAX_CHARS, ToolFault


def parse_feedback(raw: str) -> Feedback | None:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ToolFault("output", "Provider 未回傳 JSON") from exc
    if not isinstance(value, dict) or set(value) != {"feedback"}:
        raise ToolFault("output", "必須只回傳 feedback 欄位")
    item = value["feedback"]
    if item is None:
        return None
    if not isinstance(item, dict) or set(item) != set(FEEDBACK_FIELD_LIMITS):
        raise ToolFault("output", "反饋欄位不符合契約")
    for key, limit in FEEDBACK_FIELD_LIMITS.items():
        if not isinstance(item[key], str) or not item[key].strip():
            raise ToolFault("output", f"{key} 必須是非空文字")
        if len(item[key]) > limit:
            raise ToolFault("output", f"{key} 超過 {limit} 字")
    feedback = Feedback(**item)
    if len(feedback.message) > FEEDBACK_MAX_CHARS:
        raise ToolFault("output", f"反饋超過 {FEEDBACK_MAX_CHARS} 字")
    return feedback
