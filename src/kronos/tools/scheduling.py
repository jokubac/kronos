"""Let the agent schedule future calls, messages, notifications and tasks.

Items are stored in <state_dir>/schedule.json and fired by `kronos serve`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kronos.schedule import ScheduleError, ScheduleStore
from kronos.tools.registry import ToolContext, ToolError

DAEMON_HINT = (
    "WARNING: the Kronos daemon (`kronos serve`) is not running on the user's machine, so nothing will fire "
    "until they start it. Tell the user."
)


def _store(ctx: ToolContext) -> ScheduleStore:
    state_dir = ctx.state_dir or Path(ctx.env.get("KRONOS_STATE_DIR", "~/.kronos")).expanduser()
    return ScheduleStore(state_dir)


def schedule_task(tool_input: dict[str, Any], ctx: ToolContext) -> str:
    store = _store(ctx)
    try:
        item = store.add(
            kind=str(tool_input.get("kind", "notify")),
            message=str(tool_input.get("message", "")),
            when=str(tool_input.get("when", "")),
            repeat=str(tool_input.get("repeat", "none")),
            deliver=str(tool_input.get("deliver", "notify")),
        )
    except ScheduleError as exc:
        raise ToolError(str(exc)) from exc
    lines = [f"Scheduled: {item.describe()}"]
    if not store.daemon_alive():
        lines.append(DAEMON_HINT)
    return "\n".join(lines)


def list_scheduled(tool_input: dict[str, Any], ctx: ToolContext) -> str:
    store = _store(ctx)
    items = store.items()
    if not items:
        text = "Nothing is scheduled."
    else:
        text = "\n".join(item.describe() for item in items)
    if not store.daemon_alive():
        text += "\n" + DAEMON_HINT
    return text


def cancel_scheduled(tool_input: dict[str, Any], ctx: ToolContext) -> str:
    item_id = str(tool_input.get("id", "")).strip()
    if not item_id:
        raise ToolError("cancel_scheduled needs the item id (see list_scheduled).")
    if _store(ctx).cancel(item_id):
        return f"Cancelled {item_id}."
    raise ToolError(f"No scheduled item with id {item_id!r}.")
