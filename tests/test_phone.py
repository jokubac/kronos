from __future__ import annotations

from datetime import time
from urllib.parse import parse_qs

import httpx
import pytest

from kronos.tools import ToolContext, ToolError
from kronos.tools.phone import build_twiml, in_quiet_hours, parse_quiet_hours, phone_call, send_sms

ENV = {
    "TWILIO_ACCOUNT_SID": "ACxxx",
    "TWILIO_AUTH_TOKEN": "tok",
    "TWILIO_FROM_NUMBER": "+37052000000",
    "USER_PHONE_NUMBER": "+37061234567",
}


def ctx(**extra: str) -> ToolContext:
    notes: list = []
    c = ToolContext(confirm=lambda p: True, notify=lambda t, m: notes.append((t, m)), env={**ENV, **extra})
    c.notes = notes  # type: ignore[attr-defined]
    return c


@pytest.fixture
def twilio(monkeypatch: pytest.MonkeyPatch):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["Authorization"].startswith("Basic ")
        if request.url.path.endswith("/Calls.json"):
            return httpx.Response(201, json={"sid": "CA123", "status": "queued"})
        if request.url.path.endswith("/Messages.json"):
            return httpx.Response(201, json={"sid": "SM123", "status": "queued"})
        return httpx.Response(404, json={"message": "nope"})

    real = httpx.Client

    def patched(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr("kronos.tools.phone.httpx.Client", patched)
    return requests


def test_twiml_escapes_and_repeats() -> None:
    xml = build_twiml("Labas & iki <3", "Google.lt-LT-Standard-A", "lt-LT", repeat=True)
    assert xml.count("<Say") == 2 and "&amp;" in xml and "&lt;3" in xml and 'language="lt-LT"' in xml
    assert build_twiml("x", "v", "l", repeat=False).count("<Say") == 1


def test_phone_call_posts_to_twilio(twilio) -> None:
    c = ctx()
    out = phone_call({"message": "Kelkis, susitikimas devintą."}, c)
    assert '"call_sid": "CA123"' in out
    body = parse_qs(twilio[0].content.decode())
    assert body["To"] == ["+37061234567"] and body["From"] == ["+37052000000"]
    assert "<Say" in body["Twiml"][0]
    assert c.notes[0][0] == "Skambinama"  # type: ignore[attr-defined]


def test_sms_posts_to_twilio(twilio) -> None:
    out = send_sms({"message": "Priminimas", "to": "+37069999999"}, ctx())
    assert '"message_sid": "SM123"' in out
    assert parse_qs(twilio[0].content.decode())["To"] == ["+37069999999"]


def test_unconfigured_and_bad_number() -> None:
    with pytest.raises(ToolError, match="TWILIO_ACCOUNT_SID"):
        phone_call({"message": "x"}, ToolContext(confirm=lambda p: True, notify=lambda t, m: None, env={}))
    with pytest.raises(ToolError, match="E.164"):
        phone_call({"message": "x", "to": "861234567"}, ctx())


def test_quiet_hours(monkeypatch: pytest.MonkeyPatch, twilio) -> None:
    window = parse_quiet_hours("22:30-07:00")
    assert in_quiet_hours(time(23, 0), window) and in_quiet_hours(time(6, 59), window)
    assert not in_quiet_hours(time(7, 0), window) and not in_quiet_hours(time(12, 0), window)
    assert not in_quiet_hours(time(3, 0), None)

    class FixedDatetime:
        @staticmethod
        def now():
            from datetime import datetime

            return datetime(2026, 11, 26, 23, 30)

        strptime = staticmethod(__import__("datetime").datetime.strptime)

    monkeypatch.setattr("kronos.tools.phone.datetime", FixedDatetime)
    with pytest.raises(ToolError, match="quiet hours"):
        phone_call({"message": "x"}, ctx(KRONOS_QUIET_HOURS="22:30-07:00"))
    assert "CA123" in phone_call({"message": "x", "urgent": True}, ctx(KRONOS_QUIET_HOURS="22:30-07:00"))


def test_twilio_error_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    real = httpx.Client

    def patched(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(lambda r: httpx.Response(400, json={"message": "Invalid voice"}))
        return real(*args, **kwargs)

    monkeypatch.setattr("kronos.tools.phone.httpx.Client", patched)
    with pytest.raises(ToolError, match="Invalid voice"):
        phone_call({"message": "x"}, ctx())
