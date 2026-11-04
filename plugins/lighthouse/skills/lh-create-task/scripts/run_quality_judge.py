"""Run native RewardKit with bounded official Responses transport for the two judges."""

from __future__ import annotations

import importlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol, cast

MODELS = {"openai/gpt-6-sol", "openai/gpt-6-astra"}
INPUT_LIMIT = 42_000
OUTPUT_LIMIT = 6_000
REQUEST_LIMIT = 3


class Encoding(Protocol):
    """The installed tokenizer boundary used before any provider call."""

    def encode(self, text: str) -> list[int]:
        """Encode local text."""
        ...


class Response(Protocol):
    """The provider fields preserved by the transport."""

    output_text: str
    model: str
    id: str
    status: str
    usage: object


class Responses(Protocol):
    """The narrow official SDK boundary; injectable for offline verification."""

    async def create(self, **kwargs: object) -> Response:
        """Dispatch a structured, non-stored request."""
        ...


def mapping(value: object) -> dict[str, object]:
    """Require a structured RewardKit object before transmission."""
    if not isinstance(value, dict):
        message = "Unexpected native RewardKit request shape"
        raise TypeError(message)
    return {str(key): item for key, item in value.items()}


def responses_input(value: object) -> list[dict[str, object]]:
    """Translate RewardKit's text messages without changing their content."""
    if not isinstance(value, list):
        message = "Expected native RewardKit message list"
        raise TypeError(message)
    result = []
    for item in value:
        message = mapping(item)
        content = message["content"]
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
        if not isinstance(blocks, list) or any(
            mapping(block).get("type") != "text" or not isinstance(mapping(block).get("text"), str)
            for block in blocks
        ):
            error = "Only text evidence is supported"
            raise TypeError(error)
        result.append(
            {
                "role": message["role"],
                "content": [
                    {"type": "input_text", "text": mapping(block)["text"]} for block in blocks
                ],
            }
        )
    return result


def parameters(kwargs: dict[str, object]) -> dict[str, object]:
    """Preserve RewardKit's exact output schema and enforce Lighthouse model settings."""
    model = kwargs.get("model")
    if (
        model not in MODELS
        or not isinstance(model, str)
        or kwargs.get("reasoning_effort") != "medium"
    ):
        message = "Expected a Lighthouse quality model with medium reasoning"
        raise ValueError(message)
    native_format = mapping(kwargs.get("response_format"))
    if native_format.get("type") != "json_schema":
        message = "Expected RewardKit JSON Schema output"
        raise ValueError(message)
    schema = mapping(native_format.get("json_schema"))
    return {
        "model": model.split("/", 1)[1],
        "input": responses_input(kwargs.get("messages")),
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema["name"],
                "schema": schema["schema"],
                "strict": schema["strict"],
            }
        },
        "reasoning": {"effort": "medium"},
        "max_output_tokens": OUTPUT_LIMIT,
        "timeout": kwargs.get("timeout"),
        "service_tier": "default",
        "store": False,
    }


def usage_dict(value: object) -> object:
    """Convert SDK usage when available without retaining prompt or response content."""
    dump = getattr(value, "model_dump", None)
    return dump() if callable(dump) else value if isinstance(value, dict) else None


class Transport:
    """One stage's bounded provider calls and usage ledger, separate from learner usage."""

    def __init__(self, provider: Responses, encoding: Encoding, usage: Path) -> None:
        """Bind one independently metered native judge invocation."""
        self.provider = provider
        self.encoding = encoding
        self.usage = usage
        self.calls = 0

    def append(self, row: dict[str, object]) -> None:
        """Append accounting metadata only; never emit credentials or evidence."""
        self.usage.parent.mkdir(parents=True, exist_ok=True)
        with self.usage.open("a") as output:
            output.write(json.dumps(row) + "\n")

    async def complete(self, **kwargs: object) -> SimpleNamespace:
        """Return exactly the native response shape RewardKit's parser consumes."""
        self.calls += 1
        if self.calls > REQUEST_LIMIT:
            message = "Quality provider request cap reached"
            raise RuntimeError(message)
        request = parameters(kwargs)
        transmission = json.dumps(
            {"messages": kwargs.get("messages"), "response_format": kwargs.get("response_format")},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        ceiling = len(self.encoding.encode(transmission)) + 1_000
        if ceiling > INPUT_LIMIT:
            message = "Quality input cap reached before transmission"
            raise RuntimeError(message)
        common = {
            "index": self.calls,
            "time": datetime.now(UTC).isoformat(),
            "requested_model": kwargs.get("model"),
            "effort": "medium",
            "input_token_upper_with_overhead": ceiling,
            "output_token_cap": OUTPUT_LIMIT,
        }
        self.append({**common, "event": "dispatch"})
        try:
            response = await self.provider.create(**request)
        except Exception as exc:  # noqa: BLE001 -- Provider exceptions cross an SDK boundary; expose type/status only to avoid logging secrets.
            status = getattr(exc, "status_code", None)
            self.append(
                {
                    **common,
                    "event": "provider_error",
                    "exception_type": type(exc).__name__,
                    "http_status": status,
                }
            )
            message = f"{type(exc).__name__}: HTTP {status}"
            raise RuntimeError(message) from None
        self.append(
            {
                **common,
                "event": "complete",
                "served_model": response.model,
                "response_id": response.id,
                "finish_reason": response.status,
                "service_tier": getattr(response, "service_tier", None),
                "usage": usage_dict(response.usage),
            }
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=response.output_text))]
        )


def main() -> None:
    """Install the transport and delegate CLI, rubric, parsing and artifacts to RewardKit."""
    provider = cast(
        "Responses", importlib.import_module("openai").AsyncOpenAI(max_retries=0).responses
    )
    encoding = cast("Encoding", importlib.import_module("tiktoken").get_encoding("o200k_base"))
    transport = Transport(provider, encoding, Path(os.environ["QUALITY_USAGE_PATH"]))
    vars(importlib.import_module("litellm"))["acompletion"] = transport.complete
    importlib.import_module("rewardkit.__main__").main()


if __name__ == "__main__":
    main()
