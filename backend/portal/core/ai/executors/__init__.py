"""The executors: one per provider, one interface (DECISIONS 27)."""

from portal.core.ai.executors.base import Executor, ExecutorError, ExecutorResult, NotConfigured
from portal.core.services.settings import get_setting

GENERAL = "general"
DRAFTING = "drafting"

PROVIDERS = {
    "gemini": "portal.core.ai.executors.gemini.GeminiExecutor",
    "claude_code": "portal.core.ai.executors.claude_code.ClaudeCodeExecutor",
    "anthropic": "portal.core.ai.executors.anthropic_api.AnthropicExecutor",
}


def provider_for(tier: str) -> str:
    key = "ai.general_provider" if tier == GENERAL else "ai.drafting_provider"
    return str(get_setting(key) or "off").strip().lower()


def model_for(tier: str) -> str:
    key = "ai.general_model" if tier == GENERAL else "ai.drafting_model"
    return str(get_setting(key) or "").strip()


def for_tier(tier: str) -> Executor:
    """The executor the settings name for this tier; `NotConfigured` when it is off or lacks
    its key, so a caller can answer "the assistant is off" rather than fail."""
    from importlib import import_module

    name = provider_for(tier)
    if name in ("", "off", "none"):
        raise NotConfigured("The assistant is switched off for this.")
    path = PROVIDERS.get(name)
    if path is None:
        raise NotConfigured(f"Unknown assistant provider {name!r}.")
    module_name, class_name = path.rsplit(".", 1)
    cls = getattr(import_module(module_name), class_name)
    return cls(model=model_for(tier))


__all__ = [
    "DRAFTING",
    "GENERAL",
    "Executor",
    "ExecutorError",
    "ExecutorResult",
    "NotConfigured",
    "for_tier",
    "model_for",
    "provider_for",
]
