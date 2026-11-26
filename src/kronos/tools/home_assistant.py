"""Home Assistant REST API bridge.

Configure with HA_URL (e.g. http://homeassistant.local:8123) and HA_TOKEN
(a long-lived access token from your Home Assistant profile page).
"""

from __future__ import annotations

from typing import Any

import httpx

from kronos.tools.registry import ToolContext, ToolError, as_json

MAX_ENTITIES = 200


def _client(ctx: ToolContext) -> tuple[httpx.Client, str]:
    url = ctx.env.get("HA_URL", "").rstrip("/")
    token = ctx.env.get("HA_TOKEN", "")
    if not url or not token:
        raise ToolError(
            "Home Assistant is not configured on the user's machine. "
            "Set HA_URL and HA_TOKEN in the Kronos .env file."
        )
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    return httpx.Client(base_url=url, headers=headers, timeout=20.0), url


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code == 401:
        raise ToolError("Home Assistant rejected the token (401). Check HA_TOKEN.")
    if response.status_code == 404:
        raise ToolError(f"Home Assistant returned 404 for {response.request.url.path}: unknown entity or service.")
    if response.status_code >= 400:
        raise ToolError(f"Home Assistant returned {response.status_code}: {response.text[:500]}")


def _compact_state(state: dict[str, Any]) -> dict[str, Any]:
    attrs = state.get("attributes", {}) or {}
    return {
        "entity_id": state.get("entity_id"),
        "state": state.get("state"),
        "friendly_name": attrs.get("friendly_name"),
        "last_changed": state.get("last_changed"),
    }


def home_assistant(tool_input: dict[str, Any], ctx: ToolContext) -> str:
    action = tool_input.get("action")
    client, _ = _client(ctx)
    try:
        if action == "list_entities":
            response = client.get("/api/states")
            _raise_for_status(response)
            states = response.json()
            domain = tool_input.get("domain")
            if domain:
                states = [s for s in states if str(s.get("entity_id", "")).startswith(f"{domain}.")]
            total = len(states)
            compact = [_compact_state(s) for s in states[:MAX_ENTITIES]]
            return as_json({"total": total, "shown": len(compact), "entities": compact})

        if action == "get_state":
            entity_id = tool_input.get("entity_id")
            if not entity_id:
                raise ToolError("get_state needs entity_id.")
            response = client.get(f"/api/states/{entity_id}")
            _raise_for_status(response)
            return as_json(response.json())

        if action == "call_service":
            domain = tool_input.get("domain")
            service = tool_input.get("service")
            if not domain or not service:
                raise ToolError("call_service needs domain and service (e.g. light / turn_on).")
            payload: dict[str, Any] = dict(tool_input.get("service_data") or {})
            if tool_input.get("entity_id"):
                payload["entity_id"] = tool_input["entity_id"]
            response = client.post(f"/api/services/{domain}/{service}", json=payload)
            _raise_for_status(response)
            changed = response.json() if response.content else []
            return as_json(
                {
                    "called": f"{domain}.{service}",
                    "payload": payload,
                    "changed_states": [_compact_state(s) for s in changed] if isinstance(changed, list) else changed,
                }
            )

        raise ToolError(f"Unknown action {action!r}. Use list_entities, get_state or call_service.")
    except httpx.HTTPError as exc:
        raise ToolError(f"Could not reach Home Assistant: {exc}") from exc
    finally:
        client.close()
