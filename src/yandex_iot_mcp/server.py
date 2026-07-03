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
import re
from typing import Annotated, Any

import httpx
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

API_BASE = "https://api.iot.yandex.net/v1.0"
_ID_RE = re.compile(r"[A-Za-z0-9._-]+")
_TOKEN_RE = re.compile(r"[\x21-\x7e]+")

READ_ONLY = ToolAnnotations(readOnlyHint=True)
MUTATING = ToolAnnotations(destructiveHint=False, idempotentHint=True)
DESTRUCTIVE = ToolAnnotations(destructiveHint=True, idempotentHint=False)

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
    if not _TOKEN_RE.fullmatch(token):
        raise YandexIotError(
            "YANDEX_IOT_TOKEN contains whitespace or non-printable "
            "characters; check that the token was copied correctly."
        )
    return token


def _check_id(kind: str, value: str) -> str:
    if not value or not _ID_RE.fullmatch(value):
        raise YandexIotError(f"invalid {kind}: {value!r}")
    return value


async def _request(method: str, path: str, body: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {_token()}"}
    try:
        async with httpx.AsyncClient(base_url=API_BASE, timeout=30.0) as client:
            response = await client.request(
                method, path, headers=headers, json=body
            )
    except httpx.HTTPError as exc:
        raise YandexIotError(
            f"Yandex IoT API {method} {path}: network error: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
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


def _summarize_device(
    device: dict, rooms: dict[str, str], households: dict[str, str]
) -> dict:
    return {
        "id": device.get("id"),
        "name": device.get("name"),
        "type": device.get("type"),
        "room": rooms.get(device.get("room", ""), device.get("room")),
        "household": households.get(
            device.get("household_id", ""), device.get("household_id")
        ),
        "capabilities": [
            _summarize_capability(c) for c in device.get("capabilities", [])
        ],
        "properties": [
            _summarize_capability(p) for p in device.get("properties", [])
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
                    "error_code": result.get("error_code"),
                    "error": result.get("error_message"),
                }
            )
    return results


async def _device_action(
    device_id: str, capability_type: str, instance: str, value: Any
) -> list[dict]:
    _check_id("device_id", device_id)
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


@mcp.tool(annotations=READ_ONLY)
async def list_devices() -> str:
    """List all Smart Home devices: IDs, names, rooms, capabilities, sensors.

    Device IDs returned here are what every other tool takes as device_id.
    Online/offline status is not included; use get_device for that.
    """
    info = await _request("GET", "/user/info")
    rooms = {r["id"]: r["name"] for r in info.get("rooms", [])}
    households = {h["id"]: h["name"] for h in info.get("households", [])}
    devices = [
        _summarize_device(d, rooms, households)
        for d in info.get("devices", [])
    ]
    return json.dumps(devices, ensure_ascii=False, indent=2)


@mcp.tool(annotations=READ_ONLY)
async def get_device(device_id: str) -> str:
    """Get the full current state of one device, including online/offline.

    device_id: UUID from list_devices.
    """
    _check_id("device_id", device_id)
    device = await _request("GET", f"/devices/{device_id}")
    return json.dumps(device, ensure_ascii=False, indent=2)


@mcp.tool(annotations=READ_ONLY)
async def list_groups() -> str:
    """List device groups (as configured in the Yandex Home app)."""
    info = await _request("GET", "/user/info")
    return json.dumps(info.get("groups", []), ensure_ascii=False, indent=2)


@mcp.tool(annotations=MUTATING)
async def turn_on(device_id: str) -> str:
    """Turn a device on (sockets, lights, switches...).

    device_id: UUID from list_devices; device must have the on_off capability.
    """
    results = await _device_action(
        device_id, "devices.capabilities.on_off", "on", True
    )
    return json.dumps(results, ensure_ascii=False)


@mcp.tool(annotations=MUTATING)
async def turn_off(device_id: str) -> str:
    """Turn a device off (sockets, lights, switches...).

    device_id: UUID from list_devices; device must have the on_off capability.
    """
    results = await _device_action(
        device_id, "devices.capabilities.on_off", "on", False
    )
    return json.dumps(results, ensure_ascii=False)


@mcp.tool(annotations=DESTRUCTIVE)
async def power_cycle(
    device_id: str,
    off_seconds: Annotated[
        int,
        Field(ge=1, le=300, description="How long to keep power off (1-300 s)"),
    ] = 10,
    ctx: Context | None = None,
) -> str:
    """Turn a device off, wait, then turn it back on.

    Hard-resets equipment plugged into a smart socket (routers, servers...).
    device_id: UUID from list_devices; device must have the on_off capability.

    The turn-on step is retried several times; if it still fails, the device
    is left POWERED OFF and the error says so — recover with turn_on.
    """
    if not 1 <= off_seconds <= 300:
        raise YandexIotError("off_seconds must be between 1 and 300")
    off = await _device_action(
        device_id, "devices.capabilities.on_off", "on", False
    )
    waited = 0
    while waited < off_seconds:
        step = min(5, off_seconds - waited)
        await asyncio.sleep(step)
        waited += step
        if ctx is not None:
            await ctx.report_progress(progress=waited, total=off_seconds)
    on_error: Exception | None = None
    for attempt in range(4):
        if attempt:
            await asyncio.sleep(5)
        try:
            on = await _device_action(
                device_id, "devices.capabilities.on_off", "on", True
            )
            break
        except YandexIotError as exc:
            on_error = exc
    else:
        raise YandexIotError(
            f"DEVICE {device_id} MAY BE LEFT POWERED OFF: the turn-on step "
            f"failed after 4 attempts ({on_error}). "
            f"Call turn_on({device_id!r}) to restore power."
        )
    return json.dumps(
        {"off": off, "waited_seconds": off_seconds, "on": on},
        ensure_ascii=False,
    )


@mcp.tool(annotations=DESTRUCTIVE)
async def device_action(
    device_id: str, capability_type: str, instance: str, value: str
) -> str:
    """Send an arbitrary capability action to a device.

    device_id: UUID from list_devices.
    capability_type: e.g. devices.capabilities.on_off, .range, .mode,
    .toggle, .color_setting.
    instance: capability instance, e.g. "on", "brightness", "temperature".
    value: JSON-encoded value, e.g. "true", "50", "\\"eco\\"";
    for color_setting rgb use an integer like "16711680".
    """
    try:
        parsed = json.loads(value)
    except ValueError as exc:
        raise YandexIotError(f"value must be valid JSON: {exc}") from exc
    results = await _device_action(device_id, capability_type, instance, parsed)
    return json.dumps(results, ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
async def list_scenarios() -> str:
    """List Smart Home scenarios (IDs usable with run_scenario)."""
    info = await _request("GET", "/user/info")
    return json.dumps(info.get("scenarios", []), ensure_ascii=False, indent=2)


@mcp.tool(annotations=DESTRUCTIVE)
async def run_scenario(scenario_id: str) -> str:
    """Run (activate) a Smart Home scenario.

    scenario_id: ID from list_scenarios.
    """
    _check_id("scenario_id", scenario_id)
    payload = await _request("POST", f"/scenarios/{scenario_id}/actions")
    return json.dumps(payload, ensure_ascii=False)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
