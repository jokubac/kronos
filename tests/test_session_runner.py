"""End-to-end test of JarvisSession.run_turn against a fake Anthropic client."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from conftest import ev, text_block

from kronos.config import Settings
from kronos.session import JarvisSession
from kronos.tools import ToolRegistry


class FakeEvents:
    def __init__(self, scripted: list[list]) -> None:
        self.scripted = scripted  # one list of events per stream connection
        self.sent: list[dict] = []
        self.stream_opens = 0

    @contextmanager
    def stream(self, session_id, *, event_deltas=None):
        self.stream_opens += 1
        batch = self.scripted.pop(0) if self.scripted else []
        yield iter(batch)

    def send(self, session_id, *, events):
        self.sent.extend(events)
        return SimpleNamespace(data=events)

    def list(self, session_id, **kwargs):
        return iter([])


class FakeSessions:
    def __init__(self, events: FakeEvents, existing: dict | None = None) -> None:
        self.events = events
        self.created: list[dict] = []
        self.existing = existing or {}
        self.threads = SimpleNamespace(list=lambda sid: iter([]))

    def create(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(id="sesn_new", status="idle")

    def retrieve(self, session_id):
        if session_id in self.existing:
            return self.existing[session_id]
        import anthropic

        raise anthropic.NotFoundError(message="nope", response=SimpleNamespace(status_code=404, headers={}), body=None)


def make_client(scripted, existing=None):
    events = FakeEvents(scripted)
    sessions = FakeSessions(events, existing)
    sessions.events = events
    return SimpleNamespace(beta=SimpleNamespace(sessions=sessions)), sessions, events


def settings(tmp_path: Path) -> Settings:
    return Settings(
        agent_id="agent_x",
        environment_id="env_x",
        memory_store_id="memstore_x",
        vault_id="vlt_x",
        budget_minor_units="500",
        state_dir=tmp_path / "state",
    )


def test_new_session_wires_memory_vault_and_budget(ui, tmp_path) -> None:
    client, sessions, _ = make_client([])
    js = JarvisSession(client, settings(tmp_path), ui, ToolRegistry())
    js.start(resume=True)  # nothing saved -> new session
    kwargs = sessions.created[0]
    assert kwargs["agent"] == "agent_x" and kwargs["environment_id"] == "env_x"
    assert kwargs["vault_ids"] == ["vlt_x"]
    assert kwargs["budget"] == {"type": "limit", "max_list_cost": {"amount": "500", "currency": "USD"}}
    assert kwargs["resources"][0]["type"] == "memory_store"
    assert kwargs["resources"][0]["memory_store_id"] == "memstore_x"
    assert (tmp_path / "state" / "session.json").exists()


def test_resume_reuses_live_session_but_not_terminated(ui, tmp_path) -> None:
    live = SimpleNamespace(id="sesn_live", status="idle", archived_at=None)
    dead = SimpleNamespace(id="sesn_dead", status="terminated", archived_at=None)
    client, sessions, _ = make_client([], existing={"sesn_live": live, "sesn_dead": dead})
    js = JarvisSession(client, settings(tmp_path), ui, ToolRegistry())
    assert js.start(session_id="sesn_live") == "sesn_live" and sessions.created == []
    assert js.start(session_id="sesn_dead") == "sesn_new" and len(sessions.created) == 1


def test_run_turn_sends_context_once_and_answers_custom_tools(ui, tmp_path) -> None:
    turn1 = [
        ev("session.status_running"),
        ev("agent.custom_tool_use", id="sevt_ct", name="notify_user", input={"title": "a", "message": "b"}, session_thread_id=None),
        ev("session.status_idle", stop_reason=SimpleNamespace(type="requires_action")),
        ev("agent.message", content=[text_block("Padaryta.")]),
        ev("session.status_idle", stop_reason=SimpleNamespace(type="end_turn")),
    ]
    turn2 = [
        ev("agent.message", content=[text_block("Dar kartą.")]),
        ev("session.status_idle", stop_reason=SimpleNamespace(type="end_turn")),
    ]
    client, _, events = make_client([turn1, turn2])
    tools = ToolRegistry()
    tools.register("notify_user", lambda i, c: "ok")
    js = JarvisSession(client, settings(tmp_path), ui, tools)

    state = js.run_turn("Primink man")
    assert state.done and state.stop_reason == "end_turn" and not state.terminated
    types = [e["type"] for e in events.sent]
    assert types == ["system.message", "user.message", "user.custom_tool_result"]
    assert events.sent[1]["content"][0]["text"] == "Primink man"
    assert events.sent[2]["custom_tool_use_id"] == "sevt_ct"

    js.run_turn("Antra")
    assert [e["type"] for e in events.sent[3:]] == ["user.message"]  # context sent only once


def test_run_turn_reconnects_when_stream_closes_early(ui, tmp_path) -> None:
    first = [ev("session.status_running")]  # stream drops with no terminal event
    second = [ev("agent.message", content=[text_block("ok")]), ev("session.status_idle", stop_reason=SimpleNamespace(type="end_turn"))]
    client, _, events = make_client([first, second])
    js = JarvisSession(client, settings(tmp_path), ui, ToolRegistry())
    import kronos.session as sess

    sess.time.sleep = lambda s: None  # don't actually wait in tests
    state = js.run_turn("hi")
    assert state.done and events.stream_opens == 2


def test_terminated_session_is_forgotten(ui, tmp_path) -> None:
    client, _, _ = make_client([[ev("session.status_terminated")]])
    js = JarvisSession(client, settings(tmp_path), ui, ToolRegistry())
    js.run_turn("x")
    assert js.session_id is None
