"""One interface for every provider: a system instruction, a prompt, a JSON shape wanted back."""

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from portal.records.services.common import ServiceError


class ExecutorError(ServiceError):
    """The provider could not answer (network, refusal, bad output)."""


class NotConfigured(ExecutorError):
    """The provider is off or has no key; not an error to a person, a state."""


@dataclass(frozen=True)
class ExecutorResult:
    text: str
    data: dict[str, Any] | None
    model: str
    tokens_in: int | None = None
    tokens_out: int | None = None


class Executor(Protocol):
    name: str

    def run(self, *, system: str, prompt: str, schema: dict[str, Any] | None) -> ExecutorResult: ...


def parse_json(text: str) -> dict[str, Any] | None:
    """The first JSON object in a reply, leniently: fenced or bare, with prose around it."""
    if not text:
        return None
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates = [fenced.group(1)] if fenced else []
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def shape_instruction(schema: dict[str, Any] | None) -> str:
    if not schema:
        return ""
    return (
        "\n\nAnswer with one JSON object and nothing else, with exactly this shape:\n"
        + json.dumps(schema, indent=1)
    )
