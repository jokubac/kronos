"""Drive one Managed Agents session: create or resume it, send turns, react to events.

Shape of a turn (see the Managed Agents client patterns):
  open the stream first -> send the user message -> handle events until the session
  is idle with `end_turn` and no workflow run is open (or it terminates).
Custom tool calls are executed on this machine and answered; calls that pause for
approval are put to the user; a dropped stream is reconnected and reconciled against
the event history, deduplicating by event ID.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import anthropic

from kronos.config import Settings
from kronos.tools import ToolContext, ToolRegistry
from kronos.ui import UI

SendEvents = Callable[[list[dict[str, Any]]], None]


# --------------------------------------------------------------------------- state


@dataclass
class TurnState:
    previews: dict[str, str] = field(default_factory=dict)  # event_id -> text printed live
    threads: dict[str, str] = field(default_factory=dict)  # thread_id -> agent name
    open_runs: set[str] = field(default_factory=set)
    run_names: dict[str, str] = field(default_factory=dict)
    seen: set[str] = field(default_factory=set)
    interrupted: bool = False
    terminated: bool = False
    last_cost: str | None = None
    done: bool = False
    stop_reason: str | None = None


# ------------------------------------------------------------------- event handling


class EventHandler:
    """Turns one stream event into UI output and, when needed, a reply to the session."""

    def __init__(self, ui: UI, tools: ToolRegistry, tool_ctx: ToolContext, send: SendEvents) -> None:
        self.ui = ui
        self.tools = tools
        self.tool_ctx = tool_ctx
        self.send = send

    def handle(self, event: Any, state: TurnState) -> bool:
        """Process `event`; return True when the turn is finished."""
        event_id = getattr(event, "id", None)
        if event_id:
            if event_id in state.seen and event.type not in ("session.status_idle", "session.status_terminated"):
                return state.done
            state.seen.add(event_id)

        etype = event.type
        method = getattr(self, "on_" + etype.replace(".", "__"), None)
        if method is not None:
            method(event, state)
        return state.done

    # -- live text previews ------------------------------------------------------

    def on_event_start(self, event: Any, state: TurnState) -> None:
        preview = getattr(event, "event", None)
        if preview is not None and getattr(preview, "type", None) == "agent.message":
            state.previews[preview.id] = ""

    def on_event_delta(self, event: Any, state: TurnState) -> None:
        if event.event_id not in state.previews:
            return
        content = getattr(event.delta, "content", None)
        text = getattr(content, "text", None) if content is not None else None
        if text:
            state.previews[event.event_id] += text
            self.ui.agent_delta(text)

    def on_agent__message(self, event: Any, state: TurnState) -> None:
        text = text_of(event.content)
        self.ui.agent_message(text, state.previews.pop(event.id, None))

    def on_span__model_request_end(self, event: Any, state: TurnState) -> None:
        # Close previews that never got their buffered message (errored or interrupted turn).
        if state.previews:
            state.previews.clear()
            self.ui.end_streaming()

    # -- tools -----------------------------------------------------------------------

    def on_agent__tool_use(self, event: Any, state: TurnState) -> None:
        agent = state.threads.get(getattr(event, "session_thread_id", None) or "", None)
        self.ui.tool(agent, event.name, event.input)
        if getattr(event, "evaluated_permission", None) == "ask":
            self._confirm_tool(event, state, agent)

    def on_agent__mcp_tool_use(self, event: Any, state: TurnState) -> None:
        agent = state.threads.get(getattr(event, "session_thread_id", None) or "", None)
        self.ui.tool(agent, f"{event.mcp_server_name}/{event.name}", event.input)
        if getattr(event, "evaluated_permission", None) == "ask":
            self._confirm_tool(event, state, agent)

    def _confirm_tool(self, event: Any, state: TurnState, agent: str | None) -> None:
        who = agent or "Jarvis"
        prompt = (
            f"{who} nori vykdyti įrankį [bold]{event.name}[/]\n"
            f"{json.dumps(event.input, ensure_ascii=False, indent=2)[:1500]}"
        )
        allowed = self.ui.confirm(prompt)
        confirmation: dict[str, Any] = {
            "type": "user.tool_confirmation",
            "tool_use_id": event.id,
            "result": "allow" if allowed else "deny",
        }
        if not allowed:
            confirmation["deny_message"] = "The user declined this tool call. Explain what you needed it for and continue without it."
        self.send([confirmation])

    def on_agent__tool_result(self, event: Any, state: TurnState) -> None:
        if getattr(event, "is_error", False) and self.ui.verbose:
            self.ui.activity(f"įrankio klaida: {shorten_blocks(event.content)}", style="dim red")

    def on_agent__custom_tool_use(self, event: Any, state: TurnState) -> None:
        agent = state.threads.get(getattr(event, "session_thread_id", None) or "", None) or "Jarvis"
        self.ui.activity(f"{agent} → {event.name} (vykdoma jūsų kompiuteryje)", style="dim blue")
        text, is_error = self.tools.run(event.name, dict(event.input or {}), self.tool_ctx)
        result: dict[str, Any] = {
            "type": "user.custom_tool_result",
            "custom_tool_use_id": event.id,
            "content": [{"type": "text", "text": text}],
        }
        if is_error:
            result["is_error"] = True
        try:
            self.send([result])
        except anthropic.BadRequestError as exc:
            # The server refuses a result for a call whose thread it already closed.
            self.ui.warn(f"Įrankio {event.name} rezultatas atmestas: {exc.message}")

    # -- multi-agent activity ---------------------------------------------------------

    def on_session__thread_created(self, event: Any, state: TurnState) -> None:
        state.threads[event.session_thread_id] = event.agent_name
        if event.agent_name != "anthropic.advisor":
            self.ui.thread_started(event.agent_name)

    def on_agent__thread_message_sent(self, event: Any, state: TurnState) -> None:
        self.ui.delegation(event.to_agent_name or "agentas", text_of(event.content))

    def on_agent__thread_message_received(self, event: Any, state: TurnState) -> None:
        self.ui.report(event.from_agent_name or "agentas")

    def on_workflow_run__created(self, event: Any, state: TurnState) -> None:
        state.open_runs.add(event.workflow_run_id)
        state.run_names[event.workflow_run_id] = event.name
        phases = ", ".join(p.name for p in (event.phases or [])) or "be fazių"
        self.ui.workflow(f"pradėtas „{event.name}“ ({phases})")

    def on_workflow_run__phase_started(self, event: Any, state: TurnState) -> None:
        self.ui.workflow(f"fazė {event.workflow_run_phase_id} pradėta")

    def on_workflow_run__error(self, event: Any, state: TurnState) -> None:
        err = getattr(event, "error", None)
        self.ui.warn(f"Workflow klaida: {getattr(err, 'type', '?')}: {getattr(err, 'message', '')}")

    def on_workflow_run__status_ended(self, event: Any, state: TurnState) -> None:
        state.open_runs.discard(event.workflow_run_id)
        name = state.run_names.get(event.workflow_run_id, event.workflow_run_id)
        result_type = getattr(event.result, "type", "?")
        self.ui.workflow(f"„{name}“ baigtas: {result_type}")

    # -- session status -----------------------------------------------------------------

    def on_session__error(self, event: Any, state: TurnState) -> None:
        err = event.error
        self.ui.error(f"Sesijos klaida: {getattr(err, 'type', '?')}: {getattr(err, 'message', '')}")

    def on_session__usage(self, event: Any, state: TurnState) -> None:
        cost = getattr(event.usage, "list_cost", None)
        if cost is not None:
            state.last_cost = format_cost(cost)

    def on_session__status_idle(self, event: Any, state: TurnState) -> None:
        reason = getattr(event.stop_reason, "type", "end_turn")
        state.stop_reason = reason
        if reason == "requires_action":
            return  # waiting on us; the tool/confirmation handlers already answered
        if reason == "end_turn":
            if state.open_runs and not state.interrupted:
                self.ui.workflow(f"laukiama {len(state.open_runs)} vykdomo(-ų) workflow")
                return
            state.done = True
            return
        if reason == "budget_reached":
            self.ui.warn(
                "Sesija pasiekė biudžeto ribą ir sustojo. "
                "Pradėkite naują sesiją su /new arba padidinkite KRONOS_SESSION_BUDGET_USD."
            )
        elif reason == "retries_exhausted":
            self.ui.error("Agentas pasidavė po kelių bandymų. Pabandykite perfrazuoti arba pradėkite naują sesiją.")
        else:
            self.ui.warn(f"Sesija sustojo: {reason}")
        state.done = True

    def on_session__status_terminated(self, event: Any, state: TurnState) -> None:
        state.terminated = True
        state.done = True
        self.ui.warn("Sesija baigėsi. Kita žinutė pradės naują sesiją.")


# ---------------------------------------------------------------------- helpers


def text_of(blocks: Any) -> str:
    parts: list[str] = []
    for block in blocks or []:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
        elif getattr(block, "type", None) == "redacted":
            parts.append("[redacted]")
    return "".join(parts)


def shorten_blocks(blocks: Any, limit: int = 200) -> str:
    text = text_of(blocks)
    return text if len(text) <= limit else text[:limit] + "…"


def format_cost(cost: Any) -> str:
    amount = getattr(cost, "amount", None)
    currency = getattr(cost, "currency", "USD")
    try:
        return f"{int(amount) / 100:.2f} {currency}"
    except (TypeError, ValueError):
        return f"{amount} {currency}"


# ------------------------------------------------------------------- the session


class JarvisSession:
    RETRYABLE = (anthropic.APIConnectionError, anthropic.APITimeoutError, anthropic.InternalServerError)

    def __init__(self, client: anthropic.Anthropic, settings: Settings, ui: UI, tools: ToolRegistry) -> None:
        self.client = client
        self.settings = settings
        self.ui = ui
        self.tools = tools
        self.session_id: str | None = None
        self._context_sent = False
        self.tool_ctx = ToolContext(
            confirm=ui.confirm,
            notify=ui.notify,
            shell_policy=settings.shell_policy,
            env=dict(os.environ),
        )

    # -- lifecycle ---------------------------------------------------------------------------

    @property
    def state_file(self) -> Path:
        return self.settings.state_dir / "session.json"

    def _save_state(self) -> None:
        self.settings.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps({"session_id": self.session_id, "saved_at": now_iso()}), encoding="utf-8")

    def _load_saved_session_id(self) -> str | None:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8")).get("session_id")
        except (OSError, ValueError):
            return None

    def start(self, resume: bool = True, session_id: str | None = None) -> str:
        """Resume the last usable session, or create a new one."""
        candidate = session_id or (self._load_saved_session_id() if resume else None)
        if candidate:
            try:
                existing = self.client.beta.sessions.retrieve(candidate)
                if existing.status != "terminated" and existing.archived_at is None:
                    self.session_id = existing.id
                    self._save_state()
                    self.ui.info(f"Tęsiama sesija {existing.id}")
                    return existing.id
                self.ui.info("Ankstesnė sesija baigta; kuriama nauja.")
            except anthropic.NotFoundError:
                self.ui.info("Ankstesnė sesija nerasta; kuriama nauja.")
        return self.new_session()

    def new_session(self) -> str:
        s = self.settings
        resources: list[dict[str, Any]] = []
        if s.memory_store_id:
            resources.append(
                {
                    "type": "memory_store",
                    "memory_store_id": s.memory_store_id,
                    "access": "read_write",
                    "instructions": (
                        "Jarvis's long-term memory. Read profile.md and the relevant preferences/ file before "
                        "acting on anything personal; write durable facts back. Never store secrets."
                    ),
                }
            )
        kwargs: dict[str, Any] = {
            "agent": s.agent_id,
            "environment_id": s.environment_id,
            "title": f"Kronos {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "metadata": {"app": "kronos"},
        }
        if resources:
            kwargs["resources"] = resources
        if s.vault_id:
            kwargs["vault_ids"] = [s.vault_id]
        if s.budget_param:
            kwargs["budget"] = s.budget_param
        session = self.client.beta.sessions.create(**kwargs)
        self.session_id = session.id
        self._context_sent = False
        self._save_state()
        self.ui.info(f"Nauja sesija {session.id}")
        self.ui.info(f"Stebėti naršyklėje: {s.console_url(session.id)}")
        return session.id

    # -- sending ------------------------------------------------------------------------------

    def _send(self, events: list[dict[str, Any]]) -> None:
        assert self.session_id
        self.client.beta.sessions.events.send(self.session_id, events=events)

    def _context_message(self) -> dict[str, Any]:
        local = datetime.now().astimezone()
        tz = local.tzname() or "local"
        text = (
            f"Current date and time on the user's machine: {local.strftime('%A, %Y-%m-%d %H:%M')} ({tz}). "
            f"The user's preferred language code is '{self.settings.language}'. "
            "Custom tools (home_assistant, local_shell, notify_user) run on the user's own computer "
            "through the Kronos CLI, which the user is watching in a terminal."
        )
        return {"type": "system.message", "content": [{"type": "text", "text": text}]}

    def interrupt(self) -> None:
        if self.session_id:
            self._send([{"type": "user.interrupt"}])

    # -- a turn --------------------------------------------------------------------------------

    def run_turn(self, user_text: str) -> TurnState:
        """Send one user message and process the session until it is idle again."""
        if not self.session_id:
            self.start()
        assert self.session_id
        state = TurnState()
        handler = EventHandler(self.ui, self.tools, self.tool_ctx, self._send)
        started_at = datetime.now(timezone.utc)
        kickoff: list[dict[str, Any]] = []
        if not self._context_sent:
            kickoff.append(self._context_message())
        kickoff.append({"type": "user.message", "content": [{"type": "text", "text": user_text}]})

        sent = False
        attempt = 0
        while not state.done:
            try:
                with self.client.beta.sessions.events.stream(self.session_id, event_deltas=["agent.message"]) as stream:
                    if not sent:
                        self._send(kickoff)
                        self._context_sent = True
                        sent = True
                    else:
                        self._reconcile(handler, state, started_at)
                        if state.done:
                            break
                    for event in stream:
                        if handler.handle(event, state):
                            break
                attempt = 0
                if not state.done:
                    # Stream closed without a terminal event: reconnect.
                    time.sleep(1.0)
            except KeyboardInterrupt:
                if state.interrupted:
                    self.ui.warn("Antras Ctrl+C: nutraukiama be laukimo.")
                    state.done = True
                    break
                state.interrupted = True
                self.ui.warn("Stabdoma... (dar kartą Ctrl+C: išeiti iš laukimo)")
                try:
                    self.interrupt()
                except anthropic.APIError as exc:
                    self.ui.error(f"Nepavyko nutraukti: {exc}")
                    state.done = True
            except self.RETRYABLE as exc:
                attempt += 1
                if attempt > 5:
                    self.ui.error(f"Ryšys su sesija nutrūko: {exc}")
                    state.done = True
                    break
                delay = min(2 ** attempt, 20)
                self.ui.warn(f"Ryšys nutrūko ({type(exc).__name__}); bandoma vėl po {delay}s")
                time.sleep(delay)
        self.ui.end_streaming()
        if state.terminated:
            self.session_id = None
        return state

    def _reconcile(self, handler: EventHandler, state: TurnState, since: datetime) -> None:
        """After a reconnect, replay events emitted during the gap (deduplicated by ID)."""
        assert self.session_id
        page = self.client.beta.sessions.events.list(self.session_id, created_at_gte=since, order="asc")
        for event in page:
            if handler.handle(event, state):
                return

    # -- inspection ------------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        assert self.session_id
        session = self.client.beta.sessions.retrieve(self.session_id)
        usage = session.usage
        cost = getattr(usage, "list_cost", None) if usage else None
        return {
            "id": session.id,
            "status": session.status,
            "cost": format_cost(cost) if cost else None,
            "input_tokens": getattr(usage, "input_tokens", None) if usage else None,
            "output_tokens": getattr(usage, "output_tokens", None) if usage else None,
            "budget": format_cost(session.budget.max_list_cost) if getattr(session, "budget", None) else None,
            "url": self.settings.console_url(session.id),
        }

    def threads(self) -> list[dict[str, Any]]:
        assert self.session_id
        out = []
        for t in self.client.beta.sessions.threads.list(self.session_id):
            agent = getattr(t, "agent", None)
            out.append(
                {
                    "id": t.id,
                    "agent": getattr(agent, "name", None) or getattr(agent, "type", "?"),
                    "status": t.status,
                    "parent": getattr(t, "parent_thread_id", None),
                }
            )
        return out


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
