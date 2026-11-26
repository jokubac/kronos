from __future__ import annotations

import json

import httpx
import pytest

from kronos.tools import ToolContext
from kronos.tools.home_assistant import home_assistant


def make_ctx() -> ToolContext:
    return ToolContext(confirm=lambda p: True, notify=lambda t, m: None, env={"HA_URL": "http://ha.test", "HA_TOKEN": "tok"})


@pytest.fixture
def fake_ha(monkeypatch: pytest.MonkeyPatch):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer tok"
        if request.url.path == "/api/states":
            return httpx.Response(200, json=[
                {"entity_id": "light.kitchen", "state": "off", "attributes": {"friendly_name": "Virtuvė"}},
                {"entity_id": "switch.fan", "state": "on", "attributes": {"friendly_name": "Ventiliatorius"}},
            ])
        if request.url.path == "/api/states/light.kitchen":
            return httpx.Response(200, json={"entity_id": "light.kitchen", "state": "off", "attributes": {}})
        if request.url.path == "/api/services/light/turn_on":
            body = json.loads(request.content)
            return httpx.Response(200, json=[{"entity_id": body["entity_id"], "state": "on", "attributes": {}}])
        return httpx.Response(404)

    real_client = httpx.Client

    def patched(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("kronos.tools.home_assistant.httpx.Client", patched)
    return calls


def test_list_entities_filters_by_domain(fake_ha) -> None:
    out = json.loads(home_assistant({"action": "list_entities", "domain": "light"}, make_ctx()))
    assert out["total"] == 1 and out["entities"][0]["friendly_name"] == "Virtuvė"


def test_get_state(fake_ha) -> None:
    out = json.loads(home_assistant({"action": "get_state", "entity_id": "light.kitchen"}, make_ctx()))
    assert out["state"] == "off"


def test_call_service_merges_entity_and_service_data(fake_ha) -> None:
    out = json.loads(home_assistant(
        {"action": "call_service", "domain": "light", "service": "turn_on", "entity_id": "light.kitchen", "service_data": {"brightness_pct": 40}},
        make_ctx(),
    ))
    assert out["payload"] == {"brightness_pct": 40, "entity_id": "light.kitchen"}
    assert out["changed_states"][0]["state"] == "on"
    sent = json.loads(fake_ha[-1].content)
    assert sent["brightness_pct"] == 40
