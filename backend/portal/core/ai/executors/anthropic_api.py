"""The Claude API, for when there are users or a server (DECISIONS 27). The key is only ever
read from the environment (`ANTHROPIC_API_KEY`)."""

import os
from typing import Any

from portal.core.ai.executors.base import (
    ExecutorError,
    ExecutorResult,
    NotConfigured,
    parse_json,
    shape_instruction,
)

DEFAULT_MODEL = "claude-opus-5-5"


class AnthropicExecutor:
    name = "anthropic"

    def __init__(self, model: str = "") -> None:
        self.model = model or DEFAULT_MODEL

    def run(self, *, system: str, prompt: str, schema: dict[str, Any] | None) -> ExecutorResult:
        if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
            raise NotConfigured("ANTHROPIC_API_KEY is not set, so the assistant cannot run.")
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise NotConfigured("The anthropic package is not installed.") from exc
        client = anthropic.Anthropic()
        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": prompt + shape_instruction(schema)}],
            )
        except anthropic.APIError as exc:
            raise ExecutorError(f"The Claude API could not answer: {exc}") from exc
        if getattr(response, "stop_reason", "") == "refusal":
            raise ExecutorError("The Claude API declined this request.")
        text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )
        usage = getattr(response, "usage", None)
        return ExecutorResult(
            text=text,
            data=parse_json(text),
            model=self.model,
            tokens_in=getattr(usage, "input_tokens", None),
            tokens_out=getattr(usage, "output_tokens", None),
        )
