from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from kronos.config import Settings
from kronos.daemon import Daemon, spoken_text
from kronos.schedule import ScheduleStore
from kronos.tools import ToolRegistry
from kronos.tools.scheduling import cancel_scheduled, list_scheduled, schedule_task
from kronos.tools import ToolContext


def settings(tmp_path: Path) -> Settings:
    return Settings(agent_id="a", environment_id="e", state_dir=tmp_path / "state", shell_policy="ask")


def test_spoken_text_strips_markdown() -> None:
    md = "# Rytas\n\n- **Orai**: 5 laipsniai\n| diena | patiekalas |\n|---|---|\n| pirm | sriuba |\n> pastaba"
    out = spoken_text(md)
    assert out == "Rytas Orai: 5 laipsniai diena patiekalas pirm sriuba pastaba"


def test_tick_fires_due_items_and_routines(tmp_path: Path) -> None:
    (tmp_path / "routines.yaml").write_text(
        "routines:\n  - name: Rytas\n    time: '07:30'\n    deliver: sms\n    prompt: brifingas\n"
    )
    daemon = Daemon(settings(tmp_path), client=SimpleNamespace(), routines_path=tmp_path / "routines.yaml")
    calls: list[tuple[str, dict]] = []
    daemon.tools = ToolRegistry()
    for name in ("phone_call", "send_sms", "notify_user"):
        daemon.tools.register(name, lambda i, c, name=name: calls.append((name, i)) or "ok")
    daemon.run_prompt = lambda title, prompt, deliver: f"atsakymas į {prompt} ({deliver})"  # type: ignore[method-assign]

    now = datetime(2026, 11, 26, 7, 35)
    daemon.store.add("call", "Kelkis", "2026-11-26T07:00", now=datetime(2026, 11, 26, 6, 0))
    daemon.store.add("prompt", "patikrink orus", "2026-11-26T07:20", deliver="notify", now=datetime(2026, 11, 26, 6, 0))

    fired = daemon.tick(now)
    assert fired == 3
    names = [c[0] for c in calls]
    assert names == ["phone_call", "notify_user", "send_sms"]
    assert calls[0][1]["message"] == "Kelkis" and calls[0][1]["urgent"] is True
    assert "patikrink orus" in calls[1][1]["message"]
    assert "brifingas" in calls[2][1]["message"]
    assert daemon.store.items() == []  # one-off items consumed
    assert daemon.tick(now) == 0  # routine fires once per day
    assert daemon.store.daemon_alive(now)


def test_scheduling_tools_roundtrip(tmp_path: Path) -> None:
    ctx = ToolContext(confirm=lambda p: True, notify=lambda t, m: None, env={}, state_dir=tmp_path)
    out = schedule_task({"kind": "call", "message": "Kelkis", "when": "2099-01-01T07:00"}, ctx)
    assert "Scheduled" in out and "kronos serve" in out  # daemon not running -> hint
    listed = list_scheduled({}, ctx)
    item_id = listed.split("]")[0].lstrip("[")
    assert "Kelkis" in listed
    assert cancel_scheduled({"id": item_id}, ctx) == f"Cancelled {item_id}."
    assert "Nothing is scheduled" in list_scheduled({}, ctx)
    ScheduleStore(tmp_path).heartbeat()
    assert "kronos serve" not in list_scheduled({}, ctx)
