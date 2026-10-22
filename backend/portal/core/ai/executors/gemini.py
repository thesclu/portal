"""The Gemini API, for the constant checks now and as a drafting fallback. The key is only
ever read from the environment (`GEMINI_API_KEY`)."""

import os
from typing import Any

from portal.core.ai.executors.base import (
    ExecutorError,
    ExecutorResult,
    NotConfigured,
    parse_json,
    shape_instruction,
)

DEFAULT_MODEL = "gemini-2.5-flash-lite"


class GeminiExecutor:
    name = "gemini"

    def __init__(self, model: str = "") -> None:
        self.model = model or DEFAULT_MODEL

    def run(self, *, system: str, prompt: str, schema: dict[str, Any] | None) -> ExecutorResult:
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            raise NotConfigured("GEMINI_API_KEY is not set, so the assistant cannot run.")
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:  # pragma: no cover
            raise NotConfigured("The google-genai package is not installed.") from exc
        client = genai.Client(api_key=key)
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json" if schema else None,
            temperature=0.2,
        )
        try:
            response = client.models.generate_content(
                model=self.model, contents=prompt + shape_instruction(schema), config=config
            )
        except Exception as exc:  # noqa: BLE001 -- the SDK's own error families vary by version
            raise ExecutorError(f"Gemini could not answer: {exc}") from exc
        text = getattr(response, "text", "") or ""
        usage = getattr(response, "usage_metadata", None)
        return ExecutorResult(
            text=text,
            data=parse_json(text),
            model=self.model,
            tokens_in=getattr(usage, "prompt_token_count", None),
            tokens_out=getattr(usage, "candidates_token_count", None),
        )
