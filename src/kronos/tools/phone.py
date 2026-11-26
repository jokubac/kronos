"""Outbound phone calls and SMS through Twilio, executed on the user's machine.

Configure: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER, USER_PHONE_NUMBER.
Optional: TWILIO_VOICE (default Google.lt-LT-Standard-A), TWILIO_LANGUAGE (default lt-LT),
KRONOS_QUIET_HOURS (e.g. 22:30-07:00: non-urgent calls are refused in that window).
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any
from xml.sax.saxutils import escape

import httpx

from kronos.tools.registry import ToolContext, ToolError, as_json

TWILIO_API = "https://api.twilio.com/2010-04-01/Accounts"
MAX_SPOKEN_CHARS = 1500
MAX_SMS_CHARS = 1500


def _twilio(ctx: ToolContext) -> tuple[str, str, str]:
    sid = ctx.env.get("TWILIO_ACCOUNT_SID", "")
    token = ctx.env.get("TWILIO_AUTH_TOKEN", "")
    from_number = ctx.env.get("TWILIO_FROM_NUMBER", "")
    if not (sid and token and from_number):
        raise ToolError(
            "Phone calls and SMS are not configured on the user's machine. "
            "Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER and USER_PHONE_NUMBER in the Kronos .env file."
        )
    return sid, token, from_number


def _target(ctx: ToolContext, tool_input: dict[str, Any]) -> str:
    to = str(tool_input.get("to") or ctx.env.get("USER_PHONE_NUMBER") or "").strip()
    if not to:
        raise ToolError("No phone number: pass `to` or set USER_PHONE_NUMBER.")
    if not to.startswith("+"):
        raise ToolError(f"Phone number {to!r} must be in E.164 format, e.g. +37061234567.")
    return to


def parse_quiet_hours(spec: str) -> tuple[time, time] | None:
    spec = (spec or "").strip()
    if not spec:
        return None
    try:
        start_s, end_s = spec.split("-")
        start = datetime.strptime(start_s.strip(), "%H:%M").time()
        end = datetime.strptime(end_s.strip(), "%H:%M").time()
    except ValueError as exc:
        raise ToolError(f"KRONOS_QUIET_HOURS must look like 22:30-07:00, got {spec!r}") from exc
    return start, end


def in_quiet_hours(now: time, window: tuple[time, time] | None) -> bool:
    if window is None:
        return False
    start, end = window
    if start <= end:
        return start <= now < end
    return now >= start or now < end  # window crosses midnight


def build_twiml(message: str, voice: str, language: str, repeat: bool) -> str:
    text = escape(message.strip()[:MAX_SPOKEN_CHARS])
    say = f'<Say voice="{escape(voice)}" language="{escape(language)}">{text}</Say>'
    body = say + ('<Pause length="1"/>' + say if repeat else "")
    return f"<Response>{body}</Response>"


def _post(ctx: ToolContext, resource: str, data: dict[str, str]) -> dict[str, Any]:
    sid, token, _ = _twilio(ctx)
    url = f"{TWILIO_API}/{sid}/{resource}.json"
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(url, data=data, auth=(sid, token))
    except httpx.HTTPError as exc:
        raise ToolError(f"Could not reach Twilio: {exc}") from exc
    if response.status_code >= 400:
        try:
            detail = response.json().get("message", response.text)
        except ValueError:
            detail = response.text
        raise ToolError(f"Twilio returned {response.status_code}: {detail[:400]}")
    return response.json()


def phone_call(tool_input: dict[str, Any], ctx: ToolContext) -> str:
    message = str(tool_input.get("message", "")).strip()
    if not message:
        raise ToolError("phone_call needs a message to speak.")
    _, _, from_number = _twilio(ctx)
    to = _target(ctx, tool_input)
    urgent = bool(tool_input.get("urgent", False))
    window = parse_quiet_hours(ctx.env.get("KRONOS_QUIET_HOURS", ""))
    if in_quiet_hours(datetime.now().time(), window) and not urgent:
        raise ToolError(
            f"It is quiet hours ({ctx.env.get('KRONOS_QUIET_HOURS')}) and the call is not marked urgent. "
            "Use notify_user or send_sms, schedule the call for later, or set urgent=true only for a real emergency."
        )
    voice = ctx.env.get("TWILIO_VOICE", "Google.lt-LT-Standard-A")
    language = ctx.env.get("TWILIO_LANGUAGE", "lt-LT")
    twiml = build_twiml(message, voice, language, repeat=bool(tool_input.get("repeat", True)))
    result = _post(ctx, "Calls", {"To": to, "From": from_number, "Twiml": twiml})
    ctx.notify("Skambinama", f"{to}: {message[:200]}")
    return as_json({"call_sid": result.get("sid"), "status": result.get("status"), "to": to, "spoken_chars": min(len(message), MAX_SPOKEN_CHARS)})


def send_sms(tool_input: dict[str, Any], ctx: ToolContext) -> str:
    message = str(tool_input.get("message", "")).strip()
    if not message:
        raise ToolError("send_sms needs a message.")
    _, _, from_number = _twilio(ctx)
    to = _target(ctx, tool_input)
    result = _post(ctx, "Messages", {"To": to, "From": from_number, "Body": message[:MAX_SMS_CHARS]})
    ctx.notify("SMS išsiųsta", f"{to}: {message[:200]}")
    return as_json({"message_sid": result.get("sid"), "status": result.get("status"), "to": to})
