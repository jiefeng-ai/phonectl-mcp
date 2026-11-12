<p align="center">English · <a href="https://github.com/jiefeng-ai/phonectl-mcp/blob/main/README.zh-CN.md">中文</a></p>

<h1 align="center"><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/jiefeng-ai/phonectl-mcp/main/docs/logo-dark.svg?v=5"><img src="https://raw.githubusercontent.com/jiefeng-ai/phonectl-mcp/main/docs/logo-light.svg?v=5" height="54" align="middle" alt=""></picture> phonectl-mcp</h1>

<p align="center"><b>Let AI agents see and drive a real iPhone from your Mac, over USB or Wi-Fi.</b></p>

<p align="center">
  <a href="https://pypi.org/project/phonectl-mcp/"><img src="https://img.shields.io/pypi/v/phonectl-mcp?logo=pypi&logoColor=white" alt="PyPI"></a>
  <img src="https://img.shields.io/badge/macOS-only-000?logo=apple&logoColor=white" alt="macOS only">
  <img src="https://img.shields.io/badge/iOS-27%2B-000?logo=ios&logoColor=white" alt="iOS 27+">
  <a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/MCP-server-000?logo=modelcontextprotocol&logoColor=white" alt="MCP server"></a>
  <a href="https://github.com/jiefeng-ai/phonectl-mcp/actions/workflows/test.yml"><img src="https://github.com/jiefeng-ai/phonectl-mcp/actions/workflows/test.yml/badge.svg?branch=main" alt="Tests"></a>
  <a href="https://github.com/jiefeng-ai/phonectl-mcp/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="License: MIT"></a>
</p>

<p align="center"><a href="#install">Install</a> · <a href="#tools">Tools</a> · <a href="#troubleshooting">Troubleshooting</a> · <a href="#security">Security</a></p>

An MCP server for screenshots, on-screen text, taps, swipes, typing, buttons, apps and the clipboard on an iPhone connected to your Mac. Nothing to install on the phone, no jailbreak.

<table align="center">
  <tr>
    <td align="center" width="50%"><img src="https://raw.githubusercontent.com/jiefeng-ai/phonectl-mcp/main/docs/demo-rednote.gif?v=2" width="260" alt="An AI agent makes a rednote post through phonectl-mcp: text card, template, stickers, filter, title and caption"></td>
    <td align="center" width="50%"><img src="https://raw.githubusercontent.com/jiefeng-ai/phonectl-mcp/main/docs/demo.gif?v=4" width="260" alt="A script applies to one job in the BOSS Zhipin app through phonectl-mcp, reading the screen with read_text and tapping"></td>
  </tr>
  <tr>
    <td align="center"><sub>An AI agent makes a rednote post: text card, template, stickers, filter, caption. It stops before <b>Post</b>. Sped up.</sub></td>
    <td align="center"><sub>A script applies to a job in a recruiting app: <code>read_text</code> finds the buttons, <code>tap</code> presses them. 2× speed.</sub></td>
  </tr>
</table>

## Requirements

- A Mac with [uv](https://docs.astral.sh/uv/)
- An iPhone on **iOS 27 or later**, with Developer Mode on
- A USB data cable (not a charge-only one) for the first setup. After that, Wi-Fi works too

Tested on an iPhone 17 Pro with iOS 27.2 and macOS 27.

## Install

### Let your agent install it

Paste this to your coding agent (Claude Code, Codex, Cursor…):

> Install phonectl-mcp by following https://github.com/jiefeng-ai/phonectl-mcp#install-by-hand, then add it to your MCP config and take a screenshot of my phone to check it works. When I need to do something on the phone, tell me step by step and wait for me.

### Install by hand

```sh
uv tool install phonectl-mcp
phonectl install-service   # runs the server at login on http://127.0.0.1:47321/mcp
phonectl doctor            # checks cable, trust, Developer Mode, disk image and server
```

`install-service` also creates an access token in `~/.config/phonectl/token`. Then connect your client:

**Claude Code**

```sh
claude mcp add --scope user --transport http phone http://127.0.0.1:47321/mcp \
  --header "Authorization: Bearer $(cat ~/.config/phonectl/token)"
```

**Cursor** (`~/.cursor/mcp.json`) and other clients with streamable HTTP

```json
{
  "mcpServers": {
    "phone": {
      "url": "http://127.0.0.1:47321/mcp",
      "headers": { "Authorization": "Bearer <contents of ~/.config/phonectl/token>" }
    }
  }
}
```

To check, ask your agent to take a screenshot of the phone.

After one setup over the cable, it also works over Wi-Fi while the phone and the Mac share a network.

All agents and scripts share this one server, since the phone takes a single control connection; let one agent drive at a time.

## Tools

Coordinates are iOS points, the same as the pixels of the returned screenshot.

| Tool | What it does |
| --- | --- |
| `screenshot` | Captures the screen |
| `read_text` | Text on screen with positions, as JSON. Much cheaper than an image |
| `tap`, `long_press`, `swipe` | Touch. To scroll down, swipe upward |
| `type_text` | Types into the focused field. Non-ASCII text goes through the clipboard |
| `press_key` | A key or chord, such as `enter` or `cmd+a` |
| `press_button` | `home`, `lock`, `volume-up`, `volume-down`, `mute`, `siri` |
| `launch_app`, `list_apps` | Opens apps, finds bundle ids |
| `get_clipboard`, `set_clipboard` | The phone's clipboard |
| `status` | The server and the phone link |

Tools that act on the phone return a screenshot taken afterwards (`screenshot=False` skips it). Taps are not confirmed, so check that screenshot.

## Troubleshooting

Run `phonectl doctor` first. It tells you which link is broken.

| Symptom | Fix |
| --- | --- |
| No iPhone found | Use a data cable, unlock the phone, replug |
| "Mac is not trusted" | Unlock the phone, replug, tap **Trust** |
| No Developer Mode switch | Run `phonectl doctor --fix`; the switch appears under Settings › Privacy & Security |
| Disk image install fails | The server installs the developer disk image on first connect and downloads it from GitHub; check the network or proxy, then `phonectl restart` |
| Taps do nothing | Needs iOS 27+; keep the screen on and unlocked |
| Clients cannot connect | `phonectl restart`, then check `~/Library/Logs/phonectl.error.log`. A 401 means the client's token differs from `~/.config/phonectl/token` |

## Security

- The server listens only on `127.0.0.1` and rejects requests without the token.
- An agent can do anything your finger can: send messages, pay, delete. Keep its instructions narrow and watch the screen. Use at your own risk; automating third-party apps may break their terms.

## Settings

<details>
<summary>Environment variables (set them, then run <code>phonectl install-service</code> again; <code>restart</code> keeps the old values)</summary>

| Variable | Default | Meaning |
| --- | --- | --- |
| `PHONECTL_UDID` | first connected iPhone | Which phone to use when several are connected |
| `PHONECTL_PORT` | `47321` | HTTP port (127.0.0.1 only) |
| `PHONECTL_TOKEN_FILE` | `~/.config/phonectl/token` | Where the token is kept |
| `PHONECTL_SCALE` | `3` | Pixels per point; 2 on iPhone SE, XR, 11. Only affects screenshot size |
| `PHONECTL_OCR_LANGS` | `zh-Hans,en-US` | `read_text` languages, in priority order |
| `PHONECTL_SETTLE` | `0.6` | Seconds to wait before the screenshot after an action |
| `PHONECTL_TOUCH_IDLE` | `30` | Idle seconds before the input stream closes (reopening takes ~0.6 s) |
| `PHONECTL_KEY_IDLE` | `5` | Seconds without a key before the virtual keyboard detaches, so the on-screen keyboard comes back |
| `PHONECTL_CALL_TIMEOUT` | `15` | Seconds to wait for the phone, on top of the gesture time |
| `PHONECTL_LOG` | `WARNING` | Log level (`INFO` for the service); logs in `~/Library/Logs/phonectl*.log` |

</details>

Other commands: `phonectl restart` (e.g. after `uv tool upgrade phonectl-mcp`). To remove: `phonectl uninstall-service`, `uv tool uninstall phonectl-mcp`, and delete `~/.config/phonectl`.

## Development

```sh
git clone https://github.com/jiefeng-ai/phonectl-mcp
cd phonectl-mcp
uv sync
uv run pytest
```

`uv run phonectl install-service` from the clone runs your working copy as the service.

## License

MIT
