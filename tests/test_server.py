import json

import pytest
import respx
from httpx import Response

from yandex_iot_mcp import server

API = server.API_BASE


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setenv("YANDEX_IOT_TOKEN", "test-token")


USER_INFO = {
    "status": "ok",
    "rooms": [{"id": "room-1", "name": "Server room", "devices": ["dev-1"]}],
    "scenarios": [{"id": "scn-1", "name": "Night", "is_active": True}],
    "devices": [
        {
            "id": "dev-1",
            "name": "Socket",
            "type": "devices.types.socket",
            "room": "room-1",
            "state": "online",
            "capabilities": [
                {
                    "type": "devices.capabilities.on_off",
                    "state": {"instance": "on", "value": True},
                }
            ],
        }
    ],
}


def _action_ok(device_id="dev-1"):
    return {
        "status": "ok",
        "devices": [
            {
                "id": device_id,
                "capabilities": [
                    {
                        "type": "devices.capabilities.on_off",
                        "state": {
                            "instance": "on",
                            "action_result": {"status": "DONE"},
                        },
                    }
                ],
            }
        ],
    }


@pytest.mark.asyncio
@respx.mock
async def test_list_devices():
    respx.get(f"{API}/user/info").mock(
        return_value=Response(200, json=USER_INFO)
    )
    result = json.loads(await server.list_devices())
    assert result[0]["id"] == "dev-1"
    assert result[0]["room"] == "Server room"
    assert result[0]["capabilities"][0]["value"] is True


@pytest.mark.asyncio
@respx.mock
async def test_turn_off_sends_on_off_action():
    route = respx.post(f"{API}/devices/actions").mock(
        return_value=Response(200, json=_action_ok())
    )
    result = json.loads(await server.turn_off("dev-1"))
    assert result[0]["status"] == "DONE"
    sent = json.loads(route.calls[0].request.content)
    action = sent["devices"][0]["actions"][0]
    assert action["type"] == "devices.capabilities.on_off"
    assert action["state"] == {"instance": "on", "value": False}


@pytest.mark.asyncio
@respx.mock
async def test_power_cycle_off_then_on(monkeypatch):
    sleeps: list[int] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(server.asyncio, "sleep", fake_sleep)
    route = respx.post(f"{API}/devices/actions").mock(
        return_value=Response(200, json=_action_ok())
    )
    result = json.loads(await server.power_cycle("dev-1", off_seconds=5))
    assert result["waited_seconds"] == 5
    assert sleeps == [5]
    first = json.loads(route.calls[0].request.content)
    second = json.loads(route.calls[1].request.content)
    assert first["devices"][0]["actions"][0]["state"]["value"] is False
    assert second["devices"][0]["actions"][0]["state"]["value"] is True


@pytest.mark.asyncio
@respx.mock
async def test_failed_action_raises():
    failed = _action_ok()
    failed["devices"][0]["capabilities"][0]["state"]["action_result"] = {
        "status": "ERROR",
        "error_message": "device unreachable",
    }
    respx.post(f"{API}/devices/actions").mock(
        return_value=Response(200, json=failed)
    )
    with pytest.raises(server.YandexIotError, match="device unreachable"):
        await server.turn_on("dev-1")


@pytest.mark.asyncio
@respx.mock
async def test_api_error_raises():
    respx.get(f"{API}/user/info").mock(
        return_value=Response(
            403, json={"status": "error", "message": "no permission"}
        )
    )
    with pytest.raises(server.YandexIotError, match="no permission"):
        await server.list_devices()


@pytest.mark.asyncio
async def test_missing_token(monkeypatch):
    monkeypatch.delenv("YANDEX_IOT_TOKEN")
    with pytest.raises(server.YandexIotError, match="YANDEX_IOT_TOKEN"):
        await server.list_devices()


@pytest.mark.asyncio
async def test_power_cycle_validates_range():
    with pytest.raises(server.YandexIotError, match="off_seconds"):
        await server.power_cycle("dev-1", off_seconds=0)
