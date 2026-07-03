import json

import httpx
import pytest
import respx
from httpx import Response

from yandex_iot_mcp import server

API = server.API_BASE


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setenv("YANDEX_IOT_TOKEN", "test-token")


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    async def fake_sleep(_seconds):
        pass

    monkeypatch.setattr(server.asyncio, "sleep", fake_sleep)


USER_INFO = {
    "status": "ok",
    "rooms": [{"id": "room-1", "name": "Server room", "devices": ["dev-1"]}],
    "households": [{"id": "hh-1", "name": "Home"}],
    "groups": [{"id": "grp-1", "name": "All sockets", "devices": ["dev-1"]}],
    "scenarios": [{"id": "scn-1", "name": "Night", "is_active": True}],
    "devices": [
        {
            "id": "dev-1",
            "name": "Socket",
            "type": "devices.types.socket",
            "room": "room-1",
            "household_id": "hh-1",
            "capabilities": [
                {
                    "type": "devices.capabilities.on_off",
                    "state": {"instance": "on", "value": True},
                }
            ],
            "properties": [
                {
                    "type": "devices.properties.float",
                    "state": {"instance": "power", "value": 42.5},
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


def _action_error(code="DEVICE_UNREACHABLE", message=None):
    payload = _action_ok()
    result = {"status": "ERROR", "error_code": code}
    if message:
        result["error_message"] = message
    payload["devices"][0]["capabilities"][0]["state"]["action_result"] = result
    return payload


@pytest.mark.asyncio
@respx.mock
async def test_list_devices():
    respx.get(f"{API}/user/info").mock(
        return_value=Response(200, json=USER_INFO)
    )
    result = json.loads(await server.list_devices())
    assert result[0]["id"] == "dev-1"
    assert result[0]["room"] == "Server room"
    assert result[0]["household"] == "Home"
    assert result[0]["capabilities"][0]["value"] is True
    assert result[0]["properties"][0]["instance"] == "power"
    assert "state" not in result[0]


@pytest.mark.asyncio
@respx.mock
async def test_list_groups():
    respx.get(f"{API}/user/info").mock(
        return_value=Response(200, json=USER_INFO)
    )
    result = json.loads(await server.list_groups())
    assert result[0]["id"] == "grp-1"


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
async def test_power_cycle_off_then_on():
    route = respx.post(f"{API}/devices/actions").mock(
        return_value=Response(200, json=_action_ok())
    )
    result = json.loads(await server.power_cycle("dev-1", off_seconds=5))
    assert result["waited_seconds"] == 5
    first = json.loads(route.calls[0].request.content)
    second = json.loads(route.calls[1].request.content)
    assert first["devices"][0]["actions"][0]["state"]["value"] is False
    assert second["devices"][0]["actions"][0]["state"]["value"] is True


@pytest.mark.asyncio
@respx.mock
async def test_power_cycle_retries_failed_turn_on():
    responses = [
        Response(200, json=_action_ok()),  # off
        Response(200, json=_action_error()),  # on attempt 1 fails
        Response(200, json=_action_ok()),  # on attempt 2 succeeds
    ]
    route = respx.post(f"{API}/devices/actions").mock(
        side_effect=responses
    )
    result = json.loads(await server.power_cycle("dev-1", off_seconds=1))
    assert result["on"][0]["status"] == "DONE"
    assert route.call_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_power_cycle_reports_device_left_off():
    responses = [Response(200, json=_action_ok())] + [
        Response(200, json=_action_error())
    ] * 4
    respx.post(f"{API}/devices/actions").mock(side_effect=responses)
    with pytest.raises(server.YandexIotError, match="LEFT POWERED OFF"):
        await server.power_cycle("dev-1", off_seconds=1)


@pytest.mark.asyncio
@respx.mock
async def test_failed_action_includes_error_code():
    respx.post(f"{API}/devices/actions").mock(
        return_value=Response(200, json=_action_error("DEVICE_UNREACHABLE"))
    )
    with pytest.raises(server.YandexIotError, match="DEVICE_UNREACHABLE"):
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
@respx.mock
async def test_transport_error_wrapped():
    respx.get(f"{API}/user/info").mock(
        side_effect=httpx.ConnectError("dns failure")
    )
    with pytest.raises(server.YandexIotError, match="network error"):
        await server.list_devices()


@pytest.mark.asyncio
async def test_missing_token(monkeypatch):
    monkeypatch.delenv("YANDEX_IOT_TOKEN")
    with pytest.raises(server.YandexIotError, match="YANDEX_IOT_TOKEN"):
        await server.list_devices()


@pytest.mark.asyncio
async def test_malformed_token_not_echoed(monkeypatch):
    monkeypatch.setenv("YANDEX_IOT_TOKEN", "bad token\nwith newline")
    with pytest.raises(server.YandexIotError) as exc_info:
        await server.list_devices()
    assert "bad token" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_invalid_device_id_rejected():
    with pytest.raises(server.YandexIotError, match="invalid device_id"):
        await server.turn_on("../evil")


@pytest.mark.asyncio
async def test_power_cycle_validates_range():
    with pytest.raises(server.YandexIotError, match="off_seconds"):
        await server.power_cycle("dev-1", off_seconds=0)
