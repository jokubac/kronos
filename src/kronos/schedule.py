"""Scheduled work the daemon fires: one-off and recurring items the agent creates,
plus the daily routines from routines.yaml. State lives in <state_dir>/schedule.json."""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

KINDS = ("call", "sms", "notify", "prompt")
REPEATS = ("none", "daily", "weekdays", "weekly")
DELIVERY = ("call", "sms", "notify", "none")
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
HEARTBEAT_STALE_SECONDS = 120


class ScheduleError(ValueError):
    pass


def parse_when(text: str, now: datetime | None = None) -> datetime:
    """Parse an agent-supplied time into a local naive datetime (minute precision)."""
    raw = str(text).strip().replace(" ", "T", 1) if "T" not in str(text) else str(text).strip()
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ScheduleError(
            f"Could not parse time {text!r}. Use ISO 8601 local time such as 2026-11-27T07:00."
        ) from exc
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    dt = dt.replace(second=0, microsecond=0)
    now = now or datetime.now()
    if dt < now - timedelta(minutes=1):
        raise ScheduleError(f"{dt:%Y-%m-%d %H:%M} is in the past (now is {now:%Y-%m-%d %H:%M}).")
    return dt


def next_occurrence(at: datetime, repeat: str, after: datetime) -> datetime | None:
    """The first occurrence of a repeating item strictly after `after`; None for one-offs."""
    if repeat == "none":
        return None
    candidate = at
    step = timedelta(days=7 if repeat == "weekly" else 1)
    while candidate <= after:
        candidate += step
    if repeat == "weekdays":
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
    return candidate


@dataclass
class ScheduledItem:
    id: str
    kind: str
    message: str
    at: str  # local naive ISO minute, e.g. 2026-11-27T07:00
    repeat: str = "none"
    deliver: str = "notify"  # only for kind == prompt
    source: str = "agent"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="minutes"))
    last_fired: str | None = None

    @property
    def at_dt(self) -> datetime:
        return datetime.fromisoformat(self.at)

    def describe(self) -> str:
        rep = "" if self.repeat == "none" else f", kartojasi: {self.repeat}"
        extra = f", pristatymas: {self.deliver}" if self.kind == "prompt" else ""
        return f"[{self.id}] {self.at.replace('T', ' ')} {self.kind}{rep}{extra}: {self.message[:120]}"


@dataclass
class Routine:
    name: str
    time: str  # "HH:MM"
    prompt: str
    days: list[str] = field(default_factory=lambda: list(DAY_NAMES))
    deliver: str = "notify"

    def fire_time(self, day: datetime) -> datetime | None:
        if DAY_NAMES[day.weekday()] not in self.days:
            return None
        hour, minute = (int(x) for x in self.time.split(":"))
        return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


def load_routines(path: Path) -> list[Routine]:
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    routines: list[Routine] = []
    for raw in data.get("routines", []) or []:
        if not raw.get("enabled", True):
            continue
        days = [str(d).lower()[:3] for d in (raw.get("days") or DAY_NAMES)]
        bad = [d for d in days if d not in DAY_NAMES]
        if bad:
            raise ScheduleError(f"Routine {raw.get('name')!r}: unknown days {bad}")
        deliver = str(raw.get("deliver", "notify"))
        if deliver not in DELIVERY:
            raise ScheduleError(f"Routine {raw.get('name')!r}: deliver must be one of {DELIVERY}")
        time_str = str(raw["time"])
        datetime.strptime(time_str, "%H:%M")
        routines.append(
            Routine(name=str(raw["name"]), time=time_str, prompt=str(raw["prompt"]).strip(), days=days, deliver=deliver)
        )
    return routines


class ScheduleStore:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / "schedule.json"
        self.heartbeat_path = self.state_dir / "daemon.json"

    # -- persistence -----------------------------------------------------------

    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"items": [], "routines_fired": {}}

    def _write(self, data: dict[str, Any]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def items(self) -> list[ScheduledItem]:
        items = [ScheduledItem(**raw) for raw in self._read().get("items", [])]
        return sorted(items, key=lambda i: i.at)

    def add(
        self,
        kind: str,
        message: str,
        when: str,
        repeat: str = "none",
        deliver: str = "notify",
        source: str = "agent",
        now: datetime | None = None,
    ) -> ScheduledItem:
        if kind not in KINDS:
            raise ScheduleError(f"kind must be one of {KINDS}")
        if repeat not in REPEATS:
            raise ScheduleError(f"repeat must be one of {REPEATS}")
        if deliver not in DELIVERY:
            raise ScheduleError(f"deliver must be one of {DELIVERY}")
        if not message.strip():
            raise ScheduleError("message must not be empty")
        at = parse_when(when, now)
        item = ScheduledItem(
            id=secrets.token_hex(3),
            kind=kind,
            message=message.strip(),
            at=at.isoformat(timespec="minutes"),
            repeat=repeat,
            deliver=deliver,
            source=source,
        )
        data = self._read()
        data.setdefault("items", []).append(asdict(item))
        self._write(data)
        return item

    def cancel(self, item_id: str) -> bool:
        data = self._read()
        before = len(data.get("items", []))
        data["items"] = [i for i in data.get("items", []) if i["id"] != item_id]
        self._write(data)
        return len(data["items"]) < before

    def due(self, now: datetime | None = None) -> list[ScheduledItem]:
        now = now or datetime.now()
        return [i for i in self.items() if i.at_dt <= now]

    def mark_fired(self, item: ScheduledItem, now: datetime | None = None) -> None:
        """Remove a one-off item, or advance a repeating one to its next occurrence."""
        now = now or datetime.now()
        data = self._read()
        kept = []
        for raw in data.get("items", []):
            if raw["id"] != item.id:
                kept.append(raw)
                continue
            nxt = next_occurrence(item.at_dt, item.repeat, now)
            if nxt is not None:
                raw["at"] = nxt.isoformat(timespec="minutes")
                raw["last_fired"] = now.isoformat(timespec="minutes")
                kept.append(raw)
        data["items"] = kept
        self._write(data)

    # -- routines ----------------------------------------------------------------

    def due_routines(self, routines: list[Routine], now: datetime | None = None) -> list[Routine]:
        now = now or datetime.now()
        fired = self._read().get("routines_fired", {})
        today = now.strftime("%Y-%m-%d")
        out = []
        for routine in routines:
            fire_at = routine.fire_time(now)
            if fire_at is None or fire_at > now or fired.get(routine.name) == today:
                continue
            # Don't fire a routine that was missed by more than 3 hours (machine was off).
            if now - fire_at > timedelta(hours=3):
                continue
            out.append(routine)
        return out

    def mark_routine_fired(self, routine: Routine, now: datetime | None = None) -> None:
        now = now or datetime.now()
        data = self._read()
        data.setdefault("routines_fired", {})[routine.name] = now.strftime("%Y-%m-%d")
        self._write(data)

    # -- daemon heartbeat ---------------------------------------------------------------

    def heartbeat(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.heartbeat_path.write_text(
            json.dumps({"pid": os.getpid(), "last_seen": datetime.now().isoformat(timespec="seconds")}), encoding="utf-8"
        )

    def daemon_alive(self, now: datetime | None = None) -> bool:
        try:
            data = json.loads(self.heartbeat_path.read_text(encoding="utf-8"))
            last = datetime.fromisoformat(data["last_seen"])
        except (OSError, ValueError, KeyError):
            return False
        return (now or datetime.now()) - last < timedelta(seconds=HEARTBEAT_STALE_SECONDS)
