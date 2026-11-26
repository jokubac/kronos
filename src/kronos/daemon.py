"""`kronos serve`: the always-on part of Jarvis.

Every few seconds it fires what is due:
  - items the agent scheduled with `schedule_task` (calls, SMS, notifications, prompts),
  - the daily routines in routines.yaml (a prompt run in a dedicated session, with the
    final answer delivered by call, SMS or notification).
Nobody watches these runs, so tool calls that would need approval are denied and
everything is written to <state_dir>/logs/.
"""

from __future__ import annotations

import logging
import os
import signal
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic
from rich.console import Console

from kronos.config import Settings
from kronos.schedule import Routine, ScheduledItem, ScheduleStore, load_routines
from kronos.session import JarvisSession
from kronos.tools import ToolContext, ToolRegistry, build_default_registry
from kronos.ui import UI

POLL_SECONDS = 15
ROUTINES_FILE = "routines.yaml"
SPOKEN_LIMIT = 1400
SMS_LIMIT = 1400

log = logging.getLogger("kronos.daemon")


def spoken_text(markdown: str, limit: int = SPOKEN_LIMIT) -> str:
    """Strip Markdown decorations so text-to-speech reads it cleanly."""
    lines = []
    for raw in markdown.splitlines():
        line = raw.strip().lstrip("#>-*• ").replace("**", "").replace("`", "")
        if line.startswith("|"):
            line = " ".join(cell.strip() for cell in line.strip("|").split("|") if cell.strip() and not set(cell.strip()) <= {"-", ":"})
        if line:
            lines.append(line)
    text = " ".join(lines)
    return text[:limit]


class Daemon:
    def __init__(self, settings: Settings, client: anthropic.Anthropic, routines_path: Path | None = None) -> None:
        self.settings = settings
        self.client = client
        self.store = ScheduleStore(settings.state_dir)
        self.routines_path = routines_path or self._find_routines()
        self.tools: ToolRegistry = build_default_registry()
        self.log_dir = settings.state_dir / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._stop = False
        self._session: JarvisSession | None = None

    def _find_routines(self) -> Path:
        explicit = os.environ.get("KRONOS_ROUTINES")
        if explicit:
            return Path(explicit).expanduser()
        here = Path.cwd().resolve()
        for directory in (here, *here.parents):
            if (directory / ROUTINES_FILE).is_file():
                return directory / ROUTINES_FILE
        return here / ROUTINES_FILE

    # -- helpers ------------------------------------------------------------------

    def _tool_ctx(self) -> ToolContext:
        return ToolContext(
            confirm=lambda prompt: False,  # unattended: never approve
            notify=lambda title, message: log.info("NOTIFY %s: %s", title, message),
            shell_policy="deny" if self.settings.shell_policy == "ask" else self.settings.shell_policy,
            env=dict(os.environ),
            state_dir=self.settings.state_dir,
        )

    def _run_tool(self, name: str, tool_input: dict[str, Any]) -> None:
        text, is_error = self.tools.run(name, tool_input, self._tool_ctx())
        (log.error if is_error else log.info)("%s -> %s", name, text[:300])

    def deliver(self, channel: str, title: str, text: str) -> None:
        if not text.strip() or channel == "none":
            return
        if channel == "call":
            self._run_tool("phone_call", {"message": spoken_text(text), "repeat": False})
        elif channel == "sms":
            self._run_tool("send_sms", {"message": spoken_text(text, SMS_LIMIT)})
        else:
            self._run_tool("notify_user", {"title": title, "message": text[:800]})

    def _daemon_session(self) -> JarvisSession:
        """One dedicated session per day keeps context fresh and costs bounded."""
        today = datetime.now().strftime("%Y-%m-%d")
        if self._session is None or getattr(self._session, "_day", None) != today:
            log_file = open(self.log_dir / f"{today}.log", "a", encoding="utf-8")  # noqa: SIM115
            ui = UI(console=Console(file=log_file, force_terminal=False, width=120), verbose=True, interactive=False)
            session = JarvisSession(self.client, self.settings, ui, self.tools, state_name="daemon-session.json")
            session.tool_ctx = self._tool_ctx()
            session.start(resume=True)
            try:
                existing = self.client.beta.sessions.retrieve(session.session_id)  # type: ignore[arg-type]
                if existing.created_at.date().isoformat() != today:
                    session.new_session()
            except anthropic.APIError:
                pass
            session._day = today  # type: ignore[attr-defined]
            self._session = session
        return self._session

    def run_prompt(self, title: str, prompt: str, deliver: str) -> str:
        session = self._daemon_session()
        framed = (
            f"[Automatinė užduotis: {title}, {datetime.now():%Y-%m-%d %H:%M}. Niekas nestebi šios sesijos, "
            f"tad neklausk klausimų: padaryk, kas įmanoma, ir baik trumpu rezultatu. "
            f"Rezultatas bus pristatytas kanalu „{deliver}“: rašyk taip, kad tiktų perskaityti balsu arba trumpa žinute.]\n\n{prompt}"
        )
        state = session.run_turn(framed)
        log.info("prompt %r finished: %s", title, state.stop_reason)
        return state.last_message

    # -- firing ------------------------------------------------------------------------

    def fire_item(self, item: ScheduledItem) -> None:
        log.info("firing %s", item.describe())
        if item.kind == "call":
            self._run_tool("phone_call", {"message": item.message, "urgent": True})
        elif item.kind == "sms":
            self._run_tool("send_sms", {"message": item.message})
        elif item.kind == "notify":
            self._run_tool("notify_user", {"title": "Jarvis primena", "message": item.message})
        elif item.kind == "prompt":
            try:
                answer = self.run_prompt("suplanuota užduotis", item.message, item.deliver)
                self.deliver(item.deliver, "Jarvis", answer)
            except anthropic.APIError as exc:
                log.error("prompt item %s failed: %s", item.id, exc)
        self.store.mark_fired(item)

    def fire_routine(self, routine: Routine) -> None:
        log.info("routine %r starting", routine.name)
        self.store.mark_routine_fired(routine)  # mark first so a crash doesn't loop
        try:
            answer = self.run_prompt(routine.name, routine.prompt, routine.deliver)
            self.deliver(routine.deliver, routine.name, answer)
        except anthropic.APIError as exc:
            log.error("routine %r failed: %s", routine.name, exc)

    def tick(self, now: datetime | None = None) -> int:
        """One pass: fire everything due. Returns the number of things fired."""
        now = now or datetime.now()
        self.store.heartbeat()
        fired = 0
        for item in self.store.due(now):
            self.fire_item(item)
            fired += 1
        routines = load_routines(self.routines_path)
        for routine in self.store.due_routines(routines, now):
            self.fire_routine(routine)
            fired += 1
        return fired

    # -- main loop ------------------------------------------------------------------------

    def serve(self) -> None:
        signal.signal(signal.SIGTERM, self._request_stop)
        signal.signal(signal.SIGINT, self._request_stop)
        routines = load_routines(self.routines_path)
        log.info("kronos daemon started; %d routine(s) from %s; state in %s", len(routines), self.routines_path, self.settings.state_dir)
        for r in routines:
            log.info("  routine %r at %s on %s -> %s", r.name, r.time, ",".join(r.days), r.deliver)
        while not self._stop:
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - the daemon must survive any single failure
                log.exception("tick failed: %s", exc)
            for _ in range(POLL_SECONDS):
                if self._stop:
                    break
                time.sleep(1)
        log.info("kronos daemon stopped")

    def _request_stop(self, *_: Any) -> None:
        self._stop = True


def configure_logging(state_dir: Path) -> None:
    state_dir.joinpath("logs").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(state_dir / "logs" / "daemon.log", encoding="utf-8")],
    )
