"""Run a shell command on the user's own machine, with the user's approval."""

from __future__ import annotations

import subprocess
from typing import Any

from kronos.tools.registry import ToolContext, ToolError

TIMEOUT_SECONDS = 120
MAX_OUTPUT_CHARS = 20_000


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n... [truncated, {len(text) - MAX_OUTPUT_CHARS} more characters]"


def local_shell(tool_input: dict[str, Any], ctx: ToolContext) -> str:
    command = str(tool_input.get("command", "")).strip()
    purpose = str(tool_input.get("purpose", "")).strip()
    if not command:
        raise ToolError("local_shell needs a non-empty command.")

    policy = ctx.shell_policy
    if policy == "deny":
        raise ToolError(
            "The user has disabled local shell access (KRONOS_SHELL_POLICY=deny). "
            "Tell the user what you wanted to run and why, and let them run it."
        )
    if policy == "ask":
        prompt = f"Vykdyti komandą jūsų kompiuteryje?\n  $ {command}\n  Tikslas: {purpose or '(nenurodytas)'}"
        if not ctx.confirm(prompt):
            raise ToolError("The user declined to run this command.")

    try:
        completed = subprocess.run(  # noqa: S602 - running the user's approved command is the tool's purpose
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"Command timed out after {TIMEOUT_SECONDS}s: {command}") from exc

    parts = [f"exit_code: {completed.returncode}"]
    if completed.stdout:
        parts.append("stdout:\n" + _truncate(completed.stdout))
    if completed.stderr:
        parts.append("stderr:\n" + _truncate(completed.stderr))
    return "\n".join(parts)
