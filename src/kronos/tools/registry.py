from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


class ToolError(Exception):
    """A tool failed in a way the agent should hear about (sent back with is_error)."""


class Confirmer(Protocol):
    def __call__(self, prompt: str) -> bool: ...


@dataclass
class ToolContext:
    """What a tool handler may need from the host process."""

    confirm: Confirmer
    notify: Callable[[str, str], None]
    shell_policy: str = "ask"
    env: dict[str, str] = field(default_factory=dict)
    state_dir: Path | None = None


ToolHandler = Callable[[dict[str, Any], ToolContext], str]


class ToolRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, ToolHandler] = {}

    def register(self, name: str, handler: ToolHandler) -> None:
        self._handlers[name] = handler

    def names(self) -> list[str]:
        return sorted(self._handlers)

    def run(self, name: str, tool_input: dict[str, Any], ctx: ToolContext) -> tuple[str, bool]:
        """Execute a tool. Returns (text, is_error); never raises for tool failures."""
        handler = self._handlers.get(name)
        if handler is None:
            return (
                f"Unknown tool {name!r}. This Kronos client knows: {', '.join(self.names())}.",
                True,
            )
        try:
            return handler(tool_input or {}, ctx), False
        except ToolError as exc:
            return str(exc), True
        except Exception as exc:  # noqa: BLE001 - a crashing tool must not kill the session loop
            return f"{type(exc).__name__}: {exc}", True


def as_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def build_default_registry() -> ToolRegistry:
    from kronos.tools.home_assistant import home_assistant
    from kronos.tools.local_shell import local_shell
    from kronos.tools.notify import notify_user
    from kronos.tools.phone import phone_call, send_sms
    from kronos.tools.scheduling import cancel_scheduled, list_scheduled, schedule_task

    registry = ToolRegistry()
    registry.register("home_assistant", home_assistant)
    registry.register("local_shell", local_shell)
    registry.register("notify_user", notify_user)
    registry.register("phone_call", phone_call)
    registry.register("send_sms", send_sms)
    registry.register("schedule_task", schedule_task)
    registry.register("list_scheduled", list_scheduled)
    registry.register("cancel_scheduled", cancel_scheduled)
    return registry
