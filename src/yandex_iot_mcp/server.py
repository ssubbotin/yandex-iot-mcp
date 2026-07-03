"""MCP server exposing the Yandex Smart Home (IoT) API.

Wraps https://api.iot.yandex.net/v1.0 with MCP tools so that AI assistants
can inspect and control devices in a user's Yandex Smart Home.

Authentication: set the YANDEX_IOT_TOKEN environment variable to an OAuth
token that has the ``iot:view`` and ``iot:control`` scopes.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

API_BASE = "https://api.iot.yandex.net/v1.0"

mcp = FastMCP("yandex-iot")


class YandexIotError(RuntimeError):
    """Raised when the Yandex IoT API reports an error."""


def _token() -> str:
    token = os.environ.get("YANDEX_IOT_TOKEN", "").strip()
    if not token:
        raise YandexIotError(
            "YANDEX_IOT_TOKEN is not set. Create an OAuth app at "
            "https://oauth.yandex.ru with the iot:view and iot:control "
            "scopes and export the access token."
        )
    return token


async def _request(method: str, path: str, body: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {_token()}"}
    async with httpx.AsyncClient(base_url=API_BASE, timeout=30.0) as client:
        response = await client.request(method, path, headers=headers, json=body)
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text}
    if response.status_code != 200 or payload.get("status") == "error":
        message = payload.get("message", response.text)
        raise YandexIotError(
            f"Yandex IoT API {method} {path} failed "
            f"(HTTP {response.status_code}): {message}"
        )
    return payload


def _summarize_capability(capability: dict) -> dict:
    state = capability.get("state") or {}
    return {
        "type": capability.get("type"),
        "instance": state.get("instance"),
        "value": state.get("value"),
    }


def _summarize_device(device: dict, rooms: dict[str, str]) -> dict:
    return {
        "id": device.get("id"),
        "name": device.get("name"),
        "type": device.get("type"),
        "room": rooms.get(device.get("room", ""), device.get("room")),
        "state": device.get("state"),
        "capabilities": [
            _summarize_capability(c) for c in device.get("capabilities", [])
        ],
    }


def _action_results(payload: dict) -> list[dict]:
    results = []
    for device in payload.get("devices", []):
        for capability in device.get("capabilities", []):
            result = (capability.get("state") or {}).get("action_result", {})
            results.append(
                {
                    "device_id": device.get("id"),
                    "capability": capability.get("type"),
                    "status": result.get("status"),
                    "error": result.get("error_message"),
                }
            )
    return results


async def _device_action(
    device_id: str, capability_type: str, instance: str, value: Any
) -> list[dict]:
    payload = await _request(
        "POST",
        "/devices/actions",
        {
            "devices": [
                {
                    "id": device_id,
                    "actions": [
                        {
                            "type": capability_type,
                            "state": {"instance": instance, "value": value},
                        }
                    ],
                }
            ]
        },
    )
    results = _action_results(payload)
    failed = [r for r in results if r["status"] != "DONE"]
    if failed:
        raise YandexIotError(f"Action not completed: {json.dumps(failed)}")
    return results


@mcp.tool()
async def list_devices() -> str:
    """List all Smart Home devices with their rooms, types and states."""
    info = await _request("GET", "/user/info")
    rooms = {r["id"]: r["name"] for r in info.get("rooms", [])}
    devices = [_summarize_device(d, rooms) for d in info.get("devices", [])]
    return json.dumps(devices, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_device(device_id: str) -> str:
    """Get the full current state of a single device by its ID."""
    device = await _request("GET", f"/devices/{device_id}")
    return json.dumps(device, ensure_ascii=False, indent=2)


@mcp.tool()
async def turn_on(device_id: str) -> str:
    """Turn a device on (sockets, lights, switches...)."""
    results = await _device_action(
        device_id, "devices.capabilities.on_off", "on", True
    )
    return json.dumps(results, ensure_ascii=False)


@mcp.tool()
async def turn_off(device_id: str) -> str:
    """Turn a device off (sockets, lights, switches...)."""
    results = await _device_action(
        device_id, "devices.capabilities.on_off", "on", False
    )
    return json.dumps(results, ensure_ascii=False)


@mcp.tool()
async def power_cycle(device_id: str, off_seconds: int = 10) -> str:
    """Turn a device off, wait, then turn it back on.

    Useful for power-cycling equipment plugged into a smart socket
    (routers, servers and other devices that need a hard reset).
    """
    if not 1 <= off_seconds <= 300:
        raise YandexIotError("off_seconds must be between 1 and 300")
    off = await _device_action(
        device_id, "devices.capabilities.on_off", "on", False
    )
    await asyncio.sleep(off_seconds)
    on = await _device_action(
        device_id, "devices.capabilities.on_off", "on", True
    )
    return json.dumps(
        {"off": off, "waited_seconds": off_seconds, "on": on},
        ensure_ascii=False,
    )


@mcp.tool()
async def device_action(
    device_id: str, capability_type: str, instance: str, value: str
) -> str:
    """Send an arbitrary capability action to a device.

    capability_type: e.g. devices.capabilities.on_off, .range, .mode,
    .toggle, .color_setting.
    instance: capability instance, e.g. "on", "brightness", "temperature".
    value: JSON-encoded value, e.g. "true", "50", "\"white\"".
    """
    try:
        parsed = json.loads(value)
    except ValueError as exc:
        raise YandexIotError(f"value must be valid JSON: {exc}") from exc
    results = await _device_action(device_id, capability_type, instance, parsed)
    return json.dumps(results, ensure_ascii=False)


@mcp.tool()
async def list_scenarios() -> str:
    """List Smart Home scenarios."""
    info = await _request("GET", "/user/info")
    return json.dumps(info.get("scenarios", []), ensure_ascii=False, indent=2)


@mcp.tool()
async def run_scenario(scenario_id: str) -> str:
    """Run (activate) a Smart Home scenario by its ID."""
    payload = await _request("POST", f"/scenarios/{scenario_id}/actions")
    return json.dumps(payload, ensure_ascii=False)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
