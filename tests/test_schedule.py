from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from kronos.schedule import Routine, ScheduleError, ScheduleStore, load_routines, next_occurrence, parse_when

NOW = datetime(2026, 11, 26, 9, 0)


def test_parse_when_accepts_iso_and_space_forms() -> None:
    assert parse_when("2026-11-27T07:00", NOW) == datetime(2026, 11, 27, 7, 0)
    assert parse_when("2026-11-27 07:00:30", NOW) == datetime(2026, 11, 27, 7, 0)
    with pytest.raises(ScheduleError):
        parse_when("tomorrow at 7", NOW)
    with pytest.raises(ScheduleError):
        parse_when("2026-11-25T07:00", NOW)


def test_next_occurrence() -> None:
    at = datetime(2026, 11, 27, 7, 0)  # a Friday
    assert next_occurrence(at, "none", at) is None
    assert next_occurrence(at, "daily", at) == datetime(2026, 11, 28, 7, 0)
    assert next_occurrence(at, "weekdays", at) == datetime(2026, 11, 30, 7, 0)  # skips the weekend
    assert next_occurrence(at, "weekly", at) == datetime(2026, 12, 4, 7, 0)


def test_store_add_due_fire_cycle(tmp_path: Path) -> None:
    store = ScheduleStore(tmp_path)
    one = store.add("call", "Kelkis", "2026-11-26T09:30", now=NOW)
    rep = store.add("notify", "Vanduo", "2026-11-26T09:10", repeat="daily", now=NOW)
    assert store.due(NOW) == []
    due = store.due(datetime(2026, 11, 26, 9, 31))
    assert {i.id for i in due} == {one.id, rep.id}
    for item in due:
        store.mark_fired(item, datetime(2026, 11, 26, 9, 31))
    remaining = store.items()
    assert [i.id for i in remaining] == [rep.id]
    assert remaining[0].at == "2026-11-27T09:10"
    assert store.cancel(rep.id) and store.items() == []
    assert not store.cancel("nope")


def test_store_validates_inputs(tmp_path: Path) -> None:
    store = ScheduleStore(tmp_path)
    with pytest.raises(ScheduleError):
        store.add("fax", "x", "2026-11-27T07:00", now=NOW)
    with pytest.raises(ScheduleError):
        store.add("call", "", "2026-11-27T07:00", now=NOW)
    with pytest.raises(ScheduleError):
        store.add("prompt", "x", "2026-11-27T07:00", deliver="pigeon", now=NOW)


def test_routines_fire_once_per_day_and_skip_stale(tmp_path: Path) -> None:
    (tmp_path / "routines.yaml").write_text(
        "routines:\n"
        "  - name: Rytas\n    time: '07:30'\n    days: [mon, tue, wed, thu, fri]\n    deliver: call\n    prompt: labas\n"
        "  - name: Off\n    time: '08:00'\n    enabled: false\n    prompt: x\n"
    )
    routines = load_routines(tmp_path / "routines.yaml")
    assert [r.name for r in routines] == ["Rytas"]
    store = ScheduleStore(tmp_path)
    thursday = datetime(2026, 11, 26, 7, 35)
    assert [r.name for r in store.due_routines(routines, thursday)] == ["Rytas"]
    store.mark_routine_fired(routines[0], thursday)
    assert store.due_routines(routines, thursday) == []
    assert store.due_routines(routines, datetime(2026, 11, 27, 7, 29)) == []  # not yet
    assert store.due_routines(routines, datetime(2026, 11, 27, 12, 0)) == []  # missed by > 3h
    assert store.due_routines(routines, datetime(2026, 11, 28, 7, 35)) == []  # Saturday


def test_routine_validation(tmp_path: Path) -> None:
    (tmp_path / "r.yaml").write_text("routines:\n  - name: Bad\n    time: '07:30'\n    days: [funday]\n    prompt: x\n")
    with pytest.raises(ScheduleError):
        load_routines(tmp_path / "r.yaml")


def test_heartbeat(tmp_path: Path) -> None:
    store = ScheduleStore(tmp_path)
    assert not store.daemon_alive()
    store.heartbeat()
    assert store.daemon_alive()
