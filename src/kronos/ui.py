"""Terminal presentation. Everything the user sees goes through here."""

from __future__ import annotations

import sys
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text


class UI:
    def __init__(self, console: Console | None = None, verbose: bool = False, interactive: bool = True) -> None:
        self.console = console or Console(highlight=False, soft_wrap=True)
        self.verbose = verbose
        self.interactive = interactive
        self._streaming = False  # True while live deltas are being printed

    # -- agent output -------------------------------------------------------

    def agent_delta(self, text: str) -> None:
        if not self._streaming:
            self.console.print(Text("Jarvis ", style="bold cyan"), end="")
            self._streaming = True
        self.console.print(text, end="", markup=False)
        self.console.file.flush()

    def agent_message(self, full_text: str, already_printed: str | None) -> None:
        """Render the authoritative message; print only what the live preview missed."""
        if already_printed is not None and self._streaming:
            if full_text.startswith(already_printed):
                self.console.print(full_text[len(already_printed):], markup=False)
            else:
                # Preview diverged (deltas were shed); reprint the whole message.
                self.console.print()
                self.console.print(Markdown(full_text))
            self._streaming = False
            return
        if full_text.strip():
            self.console.print(Text("Jarvis", style="bold cyan"))
            self.console.print(Markdown(full_text))

    def end_streaming(self) -> None:
        if self._streaming:
            self.console.print()
            self._streaming = False

    # -- activity ------------------------------------------------------------

    def activity(self, text: str, style: str = "dim") -> None:
        self.end_streaming()
        self.console.print(Text(f"  · {text}", style=style))

    def tool(self, agent: str | None, name: str, tool_input: dict[str, Any]) -> None:
        if not self.verbose:
            return
        who = f"{agent} " if agent else ""
        self.activity(f"{who}→ {name} {summarize_input(tool_input)}")

    def delegation(self, to_agent: str, brief: str) -> None:
        first_line = brief.strip().splitlines()[0] if brief.strip() else ""
        self.activity(f"Jarvis → {to_agent}: {shorten(first_line, 90)}", style="dim magenta")

    def report(self, from_agent: str) -> None:
        self.activity(f"{from_agent} → Jarvis: ataskaita gauta", style="dim magenta")

    def thread_started(self, agent_name: str) -> None:
        self.activity(f"{agent_name} pradėjo darbą", style="dim green")

    def workflow(self, text: str) -> None:
        self.activity(f"Workflow: {text}", style="dim yellow")

    def info(self, text: str) -> None:
        self.end_streaming()
        self.console.print(Text(text, style="cyan"))

    def warn(self, text: str) -> None:
        self.end_streaming()
        self.console.print(Text(f"⚠ {text}", style="yellow"))

    def error(self, text: str) -> None:
        self.end_streaming()
        self.console.print(Text(f"✖ {text}", style="bold red"))

    def notify(self, title: str, message: str) -> None:
        self.end_streaming()
        self.console.print(Panel(message, title=f"🔔 {title}", border_style="green"))

    # -- input -------------------------------------------------------------------

    def confirm(self, prompt: str) -> bool:
        """Ask the user y/n. Deny when no one is at the terminal."""
        self.end_streaming()
        if not self.interactive or not sys.stdin.isatty():
            self.warn("Nėra interaktyvaus terminalo; veiksmas atmestas.")
            return False
        self.console.print(Panel(prompt, title="Reikia patvirtinimo", border_style="yellow"))
        try:
            answer = self.console.input("[bold yellow]Leisti? [t/N] [/]").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return answer in ("t", "taip", "y", "yes")

    def prompt(self) -> str:
        self.end_streaming()
        return self.console.input("[bold green]Jūs ›[/] ")


def shorten(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def summarize_input(tool_input: dict[str, Any], limit: int = 100) -> str:
    if not tool_input:
        return ""
    for key in ("command", "query", "url", "path", "file_path", "pattern", "entity_id"):
        if key in tool_input:
            return shorten(str(tool_input[key]), limit)
    return shorten(", ".join(f"{k}={v}" for k, v in tool_input.items()), limit)
