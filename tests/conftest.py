from __future__ import annotations

import itertools
from types import SimpleNamespace
from typing import Any

import pytest
from rich.console import Console

from kronos.ui import UI


_counter = itertools.count(1)


def ev(type_: str, **fields: Any) -> SimpleNamespace:
    """Build a fake stream event with the fields the real SDK objects expose."""
    fields.setdefault("id", f"sevt_{next(_counter)}")
    return SimpleNamespace(type=type_, **fields)


def text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


@pytest.fixture
def ui() -> UI:
    console = Console(file=open("/dev/null", "w"), force_terminal=False, width=100)  # noqa: SIM115
    return UI(console=console, verbose=True)


@pytest.fixture
def recorded_console() -> Console:
    return Console(record=True, force_terminal=False, width=100, file=open("/dev/null", "w"))  # noqa: SIM115
