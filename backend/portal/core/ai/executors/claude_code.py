"""Claude Code on this machine, in its headless print mode, under the person's own login
(DECISIONS 24, 27): for the solo phase only, while the portal runs where Claude Code is signed
in. The command and the tools it may use are settings; the portal never sees a credential.
"""

import json
import shutil
import subprocess
from typing import Any

from portal.core.ai.executors.base import (
    ExecutorError,
    ExecutorResult,
    NotConfigured,
    parse_json,
    shape_instruction,
)
from portal.core.services.settings import get_setting

TIMEOUT_SECONDS = 180


class ClaudeCodeExecutor:
    name = "claude_code"

    def __init__(self, model: str = "") -> None:
        self.model = model

    def command(self) -> list[str]:
        raw = str(get_setting("ai.claude_code_command") or "claude").strip()
        parts = raw.split()
        if not parts or (not shutil.which(parts[0]) and not parts[0].startswith("/")):
            raise NotConfigured(
                f"Claude Code was not found as {parts[0] if parts else 'claude'!r}; install "
                "it and sign in on this machine, or set ai.claude_code_command to its path."
            )
        cmd = [*parts, "-p", "--output-format", "json"]
        if self.model:
            cmd += ["--model", self.model]
        tools = str(get_setting("ai.claude_code_allowed_tools") or "").strip()
        if tools:
            cmd += ["--allowedTools", tools]
        return cmd

    def run(self, *, system: str, prompt: str, schema: dict[str, Any] | None) -> ExecutorResult:
        cmd = self.command()
        full = f"{system}\n\n{prompt}{shape_instruction(schema)}"
        try:
            done = subprocess.run(  # noqa: S603 -- the command is a setting an administrator set
                cmd,
                input=full,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                check=False,
            )
        except FileNotFoundError as exc:
            raise NotConfigured(f"Claude Code could not be started: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ExecutorError("Claude Code did not answer in time.") from exc
        if done.returncode != 0:
            raise ExecutorError(
                f"Claude Code exited with {done.returncode}: {(done.stderr or done.stdout)[:400]}"
            )
        text, usage = _unwrap(done.stdout)
        return ExecutorResult(
            text=text,
            data=parse_json(text),
            model=self.model or "claude-code",
            tokens_in=(usage or {}).get("input_tokens"),
            tokens_out=(usage or {}).get("output_tokens"),
        )


def _unwrap(stdout: str) -> tuple[str, dict[str, Any] | None]:
    """`--output-format json` prints one object with `result` (the answer) and `usage`."""
    try:
        payload = json.loads(stdout)
    except ValueError:
        return stdout, None
    if isinstance(payload, dict):
        return str(payload.get("result") or payload.get("text") or ""), payload.get("usage")
    if isinstance(payload, list):
        for entry in reversed(payload):
            if isinstance(entry, dict) and entry.get("type") == "result":
                return str(entry.get("result") or ""), entry.get("usage")
    return stdout, None
