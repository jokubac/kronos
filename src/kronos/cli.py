"""`kronos` command: an interactive terminal for talking to Jarvis."""

from __future__ import annotations

import argparse
import os
import sys

import anthropic
from rich.table import Table

from kronos.config import ConfigError, Settings, load_settings
from kronos.schedule import ScheduleStore
from kronos.session import JarvisSession
from kronos.tools import build_default_registry
from kronos.ui import UI

HELP = """\
Komandos:
  /schedule   kas suplanuota (skambučiai, priminimai, užduotys) ir ar veikia `kronos serve`
  /new        pradėti naują sesiją (sena lieka Console istorijoje)
  /status     sesijos būsena, kaina, biudžetas, nuoroda į Console
  /threads    kurie agentai dirbo šioje sesijoje
  /url        nuoroda į sesiją Anthropic Console
  /help       ši pagalba
  /quit       išeiti (arba Ctrl+D)
Ctrl+C užduoties metu nutraukia agentą; Ctrl+C prie įvesties išeina.
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kronos", description="Jarvis: multi-agent assistant on Claude Managed Agents.")
    p.add_argument(
        "message",
        nargs="*",
        help="Send this message and exit (otherwise start the interactive loop). "
        "Special words: `serve` runs the always-on daemon; `schedule` lists scheduled items.",
    )
    p.add_argument("--new", action="store_true", help="Start a fresh session instead of resuming the last one.")
    p.add_argument("--session", metavar="ID", help="Resume this session ID.")
    p.add_argument("--verbose", "-v", action="store_true", help="Show every tool call the agents make.")
    p.add_argument("--check", action="store_true", help="Print the resolved configuration and exit.")
    return p


def print_settings(ui: UI, settings: Settings) -> None:
    table = Table(title="Kronos konfigūracija", show_header=False)
    table.add_row("agent_id", settings.agent_id)
    table.add_row("environment_id", settings.environment_id)
    table.add_row("memory_store_id", settings.memory_store_id or "(nėra: atmintis išjungta)")
    table.add_row("vault_id", settings.vault_id or "(nėra)")
    table.add_row("workspace", settings.workspace)
    table.add_row("budget", f"{int(settings.budget_minor_units) / 100:.2f} USD" if settings.budget_minor_units else "be ribos")
    table.add_row("shell_policy", settings.shell_policy)
    table.add_row("lock_file", str(settings.lock_file) if settings.lock_file else "(nerastas)")
    table.add_row("Twilio (skambučiai/SMS)", "sukonfigūruotas" if all(os.environ.get(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER", "USER_PHONE_NUMBER")) else "ne (TWILIO_* ir USER_PHONE_NUMBER)")
    table.add_row("kronos serve", "veikia" if ScheduleStore(settings.state_dir).daemon_alive() else "neveikia")
    table.add_row("Home Assistant", "sukonfigūruotas" if os.environ.get("HA_URL") and os.environ.get("HA_TOKEN") else "ne (HA_URL/HA_TOKEN)")
    auth = "ANTHROPIC_API_KEY" if os.environ.get("ANTHROPIC_API_KEY") else ("ANTHROPIC_AUTH_TOKEN" if os.environ.get("ANTHROPIC_AUTH_TOKEN") else "ant auth login profilis")
    table.add_row("autentikacija", auth)
    ui.console.print(table)


def print_schedule(ui: UI, settings: Settings) -> None:
    store = ScheduleStore(settings.state_dir)
    items = store.items()
    table = Table(title="Suplanuota")
    for col in ("id", "kada", "tipas", "kartojasi", "pranešimas"):
        table.add_column(col)
    for item in items:
        table.add_row(item.id, item.at.replace("T", " "), item.kind, item.repeat, item.message[:80])
    if not items:
        ui.info("Nieko nesuplanuota.")
    else:
        ui.console.print(table)
    if store.daemon_alive():
        ui.info("`kronos serve` veikia.")
    else:
        ui.warn("`kronos serve` neveikia: suplanuoti dalykai nebus įvykdyti, kol jo nepaleisite.")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ui = UI(verbose=args.verbose)

    try:
        settings = load_settings()
    except ConfigError as exc:
        ui.error(str(exc))
        return 2

    if args.check:
        print_settings(ui, settings)
        return 0

    if args.message and args.message[0] == "schedule":
        print_schedule(ui, settings)
        return 0

    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN or an `ant auth login` profile

    if args.message and args.message[0] == "serve":
        from kronos.daemon import Daemon, configure_logging

        configure_logging(settings.state_dir)
        Daemon(settings, client).serve()
        return 0
  # ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN or an `ant auth login` profile
    session = JarvisSession(client, settings, ui, build_default_registry())

    try:
        session.start(resume=not args.new, session_id=args.session)
    except anthropic.AuthenticationError:
        ui.error("Autentikacija nepavyko. Paleiskite `ant auth login` arba nustatykite ANTHROPIC_API_KEY.")
        return 2
    except anthropic.APIError as exc:
        ui.error(f"Nepavyko pradėti sesijos: {exc}")
        return 1

    if args.message:
        state = session.run_turn(" ".join(args.message))
        return 0 if state.stop_reason in (None, "end_turn") else 1

    ui.info("Jarvis klauso. /help – komandos, /quit – išeiti.")
    while True:
        try:
            line = ui.prompt().strip()
        except (EOFError, KeyboardInterrupt):
            ui.console.print()
            break
        if not line:
            continue
        if line.startswith("/"):
            if not handle_command(line, session, ui):
                break
            continue
        try:
            session.run_turn(line)
        except anthropic.APIError as exc:
            ui.error(f"API klaida: {exc}")
    return 0


def handle_command(line: str, session: JarvisSession, ui: UI) -> bool:
    """Run a slash command. Returns False to exit the loop."""
    cmd = line.split()[0].lower()
    if cmd in ("/quit", "/exit", "/q"):
        return False
    if cmd == "/help":
        ui.console.print(HELP)
    elif cmd == "/new":
        session.new_session()
    elif cmd == "/schedule":
        print_schedule(ui, session.settings)
    elif cmd == "/url":
        if session.session_id:
            ui.info(session.settings.console_url(session.session_id))
    elif cmd == "/status":
        if not session.session_id:
            ui.warn("Sesijos nėra.")
            return True
        try:
            info = session.status()
        except anthropic.APIError as exc:
            ui.error(f"Nepavyko gauti būsenos: {exc}")
            return True
        table = Table(show_header=False, title="Sesija")
        for key, value in info.items():
            table.add_row(key, str(value) if value is not None else "-")
        ui.console.print(table)
    elif cmd == "/threads":
        if not session.session_id:
            ui.warn("Sesijos nėra.")
            return True
        try:
            threads = session.threads()
        except anthropic.APIError as exc:
            ui.error(f"Nepavyko gauti gijų: {exc}")
            return True
        table = Table(title="Agentų gijos")
        table.add_column("agentas")
        table.add_column("būsena")
        table.add_column("id")
        for t in threads:
            table.add_row(str(t["agent"]), str(t["status"]), t["id"])
        ui.console.print(table)
    else:
        ui.warn(f"Nežinoma komanda {cmd}. /help – komandų sąrašas.")
    return True


if __name__ == "__main__":
    sys.exit(main())
