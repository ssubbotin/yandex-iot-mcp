# yandex-iot-mcp

MCP ([Model Context Protocol](https://modelcontextprotocol.io)) server for the
[Yandex Smart Home IoT API](https://yandex.ru/dev/dialogs/smart-home/doc/en/concepts/platform-about).
Lets AI assistants (Claude Code, Claude Desktop, and any other MCP client)
inspect and control devices connected to Yandex Smart Home / Alice.

## Tools

| Tool | Description |
| --- | --- |
| `list_devices` | All devices with rooms, types, capabilities and sensor values |
| `list_groups` | Device groups from the Yandex Home app |
| `get_device` | Full state of one device, including online/offline |
| `turn_on` / `turn_off` | On/off for sockets, lights, switches |
| `power_cycle` | Off → wait N seconds → on. Hard-reset gear plugged into a smart socket |
| `device_action` | Any capability action (range, mode, toggle, color_setting…) |
| `list_scenarios` / `run_scenario` | Inspect and trigger scenarios |

## Getting an OAuth token

1. Create an app at [oauth.yandex.ru](https://oauth.yandex.ru): platform
   **Web services**, redirect URI `https://oauth.yandex.ru/verification_code`.
2. Grant it the **iot:view** and **iot:control** permissions ("Умный дом").
3. Open
   `https://oauth.yandex.ru/authorize?response_type=token&client_id=<CLIENT_ID>`
   in a browser, authorize, and copy `access_token` from the address bar.

## Usage

### Claude Code

```bash
claude mcp add yandex-iot --env YANDEX_IOT_TOKEN=<token> -- \
  uvx --from git+https://github.com/ssubbotin/yandex-iot-mcp yandex-iot-mcp
```

### Claude Desktop

```json
{
  "mcpServers": {
    "yandex-iot": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/ssubbotin/yandex-iot-mcp", "yandex-iot-mcp"],
      "env": { "YANDEX_IOT_TOKEN": "<token>" }
    }
  }
}
```

### Example

> "The server is frozen again — power-cycle the socket it's plugged into."

The assistant finds the socket via `list_devices` and calls
`power_cycle(device_id, off_seconds=10)`.

## Development

```bash
uv sync
uv run pytest
YANDEX_IOT_TOKEN=<token> uv run yandex-iot-mcp
```

## License

MIT
