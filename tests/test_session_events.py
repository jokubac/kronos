from __future__ import annotations

from types import SimpleNamespace

from conftest import ev, text_block

from kronos.session import EventHandler, TurnState, format_cost, text_of
from kronos.tools import ToolContext, ToolRegistry


def make_handler(ui, sent: list, tools: ToolRegistry | None = None, confirm: bool = True) -> EventHandler:
    tools = tools or ToolRegistry()
    ctx = ToolContext(confirm=lambda p: confirm, notify=lambda t, m: None, env={})
    ui.confirm = lambda prompt: confirm  # type: ignore[method-assign]
    return EventHandler(ui, tools, ctx, lambda events: sent.extend(events))


def test_custom_tool_call_is_executed_and_answered(ui) -> None:
    sent: list = []
    tools = ToolRegistry()
    tools.register("notify_user", lambda i, c: f"shown {i['title']}")
    h = make_handler(ui, sent, tools)
    state = TurnState()
    done = h.handle(ev("agent.custom_tool_use", id="sevt_t1", name="notify_user", input={"title": "Hi", "message": "m"}, session_thread_id=None), state)
    assert done is False
    assert sent == [{
        "type": "user.custom_tool_result",
        "custom_tool_use_id": "sevt_t1",
        "content": [{"type": "text", "text": "shown Hi"}],
    }]


def test_failed_custom_tool_is_flagged_as_error(ui) -> None:
    sent: list = []
    h = make_handler(ui, sent)  # empty registry -> unknown tool
    h.handle(ev("agent.custom_tool_use", id="sevt_t2", name="ghost", input={}, session_thread_id=None), TurnState())
    assert sent[0]["is_error"] is True and "Unknown tool" in sent[0]["content"][0]["text"]


def test_tool_confirmation_allow_and_deny(ui) -> None:
    for answer, expected in ((True, "allow"), (False, "deny")):
        sent: list = []
        h = make_handler(ui, sent, confirm=answer)
        h.handle(ev("agent.tool_use", id="sevt_ask", name="bash", input={"command": "ls"}, evaluated_permission="ask", session_thread_id=None), TurnState())
        assert sent[0]["type"] == "user.tool_confirmation"
        assert sent[0]["tool_use_id"] == "sevt_ask"
        assert sent[0]["result"] == expected
        assert ("deny_message" in sent[0]) is (not answer)


def test_allowed_tool_use_sends_nothing(ui) -> None:
    sent: list = []
    h = make_handler(ui, sent)
    h.handle(ev("agent.tool_use", id="sevt_ok", name="read", input={"path": "/x"}, evaluated_permission="allow", session_thread_id=None), TurnState())
    assert sent == []


def test_idle_gating(ui) -> None:
    h = make_handler(ui, [])
    state = TurnState()
    requires = ev("session.status_idle", id="i1", stop_reason=SimpleNamespace(type="requires_action"))
    end = ev("session.status_idle", id="i2", stop_reason=SimpleNamespace(type="end_turn"))
    assert h.handle(requires, state) is False
    # a workflow run is open: end_turn does not finish the turn yet
    h.handle(ev("workflow_run.created", id="w1", workflow_run_id="wrun_1", name="scan", description=None, phases=[]), state)
    assert h.handle(end, state) is False
    h.handle(ev("workflow_run.status_ended", id="w2", workflow_run_id="wrun_1", result=SimpleNamespace(type="completed")), state)
    end2 = ev("session.status_idle", id="i3", stop_reason=SimpleNamespace(type="end_turn"))
    assert h.handle(end2, state) is True
    assert state.stop_reason == "end_turn"


def test_budget_reached_and_terminated_finish_the_turn(ui) -> None:
    h = make_handler(ui, [])
    s1 = TurnState()
    assert h.handle(ev("session.status_idle", id="b", stop_reason=SimpleNamespace(type="budget_reached")), s1) is True
    s2 = TurnState()
    assert h.handle(ev("session.status_terminated", id="t"), s2) is True
    assert s2.terminated


def test_preview_then_buffered_message_prints_only_the_suffix(recorded_console) -> None:
    from kronos.ui import UI

    ui = UI(console=recorded_console)
    h = make_handler(ui, [])
    state = TurnState()
    h.handle(ev("event_start", id=None, event=SimpleNamespace(type="agent.message", id="sevt_m1")), state)
    h.handle(ev("event_delta", id=None, event_id="sevt_m1", delta=SimpleNamespace(content=SimpleNamespace(type="text", text="Labas, "))), state)
    h.handle(ev("event_delta", id=None, event_id="sevt_m1", delta=SimpleNamespace(content=SimpleNamespace(type="text", text="aš Jar"))), state)
    h.handle(ev("agent.message", id="sevt_m1", content=[text_block("Labas, aš Jarvis.")]), state)
    out = recorded_console.export_text()
    assert out.count("Labas") == 1
    assert "Jarvis." in out
    assert state.previews == {}


def test_duplicate_events_are_ignored(ui) -> None:
    sent: list = []
    tools = ToolRegistry()
    calls = []
    tools.register("notify_user", lambda i, c: calls.append(1) or "ok")
    h = make_handler(ui, sent, tools)
    state = TurnState()
    e = ev("agent.custom_tool_use", id="sevt_dup", name="notify_user", input={}, session_thread_id=None)
    h.handle(e, state)
    h.handle(e, state)
    assert len(calls) == 1 and len(sent) == 1


def test_thread_names_are_tracked_for_tool_attribution(ui) -> None:
    h = make_handler(ui, [])
    state = TurnState()
    h.handle(ev("session.thread_created", id="c1", session_thread_id="thr_1", agent_name="Researcher", workflow_run_id=None), state)
    assert state.threads == {"thr_1": "Researcher"}


def test_helpers() -> None:
    assert text_of([text_block("a"), SimpleNamespace(type="redacted"), text_block("b")]) == "a[redacted]b"
    assert format_cost(SimpleNamespace(amount="1250", currency="USD")) == "12.50 USD"
