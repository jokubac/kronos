from __future__ import annotations

import sys

from kronos.tools import ToolContext, ToolError, ToolRegistry, build_default_registry
from kronos.tools.local_shell import local_shell
from kronos.tools.notify import notify_user


def ctx(confirm_answer: bool = True, policy: str = "ask", **env: str) -> ToolContext:
    notes: list[tuple[str, str]] = []
    c = ToolContext(confirm=lambda _prompt: confirm_answer, notify=lambda t, m: notes.append((t, m)), shell_policy=policy, env=env)
    c.notes = notes  # type: ignore[attr-defined]
    return c


def test_default_registry_has_the_tools_the_agents_declare() -> None:
    assert build_default_registry().names() == ["home_assistant", "local_shell", "notify_user"]


def test_unknown_tool_is_an_error_result_not_an_exception() -> None:
    text, is_error = ToolRegistry().run("nope", {}, ctx())
    assert is_error and "Unknown tool" in text


def test_registry_converts_tool_error_and_crash_to_error_results() -> None:
    reg = ToolRegistry()
    reg.register("boom", lambda i, c: (_ for _ in ()).throw(ToolError("bad input")))
    reg.register("crash", lambda i, c: 1 / 0)
    assert reg.run("boom", {}, ctx()) == ("bad input", True)
    text, is_error = reg.run("crash", {}, ctx())
    assert is_error and "ZeroDivisionError" in text


def test_local_shell_runs_when_approved() -> None:
    out = local_shell({"command": f"{sys.executable} -c \"print('hi')\"", "purpose": "test"}, ctx(True))
    assert "exit_code: 0" in out and "hi" in out


def test_local_shell_respects_decline_and_deny_policy() -> None:
    try:
        local_shell({"command": "echo no", "purpose": "t"}, ctx(False))
        raise AssertionError("expected ToolError")
    except ToolError as exc:
        assert "declined" in str(exc)
    try:
        local_shell({"command": "echo no", "purpose": "t"}, ctx(True, policy="deny"))
        raise AssertionError("expected ToolError")
    except ToolError as exc:
        assert "KRONOS_SHELL_POLICY" in str(exc)


def test_local_shell_allow_policy_skips_confirmation() -> None:
    asked = []
    c = ToolContext(confirm=lambda p: asked.append(p) or True, notify=lambda t, m: None, shell_policy="allow", env={})
    out = local_shell({"command": "echo auto", "purpose": "t"}, c)
    assert "auto" in out and asked == []


def test_notify_user_reaches_terminal(monkeypatch) -> None:
    monkeypatch.setattr("kronos.tools.notify.desktop_notify", lambda t, m: False)
    c = ctx()
    out = notify_user({"title": "Primink", "message": "Susitikimas 15:00"}, c)
    assert c.notes == [("Primink", "Susitikimas 15:00")]  # type: ignore[attr-defined]
    assert "terminal" in out


def test_home_assistant_unconfigured_is_a_clear_error() -> None:
    text, is_error = build_default_registry().run("home_assistant", {"action": "list_entities"}, ctx())
    assert is_error and "HA_URL" in text
