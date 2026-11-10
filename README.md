<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/jiefeng-ai/phonectl-mcp/main/docs/logo-dark.svg">
    <img src="https://raw.githubusercontent.com/jiefeng-ai/phonectl-mcp/main/docs/logo-light.svg" width="96" height="96" alt="phonectl-mcp logo">
  </picture>
</p>

<h1 align="center">phonectl-mcp</h1>

<p align="center"><b>Let AI agents see and drive a real iPhone over USB.</b></p>

<p align="center">
  <a href="https://pypi.org/project/phonectl-mcp/"><img src="https://img.shields.io/pypi/v/phonectl-mcp?logo=pypi&logoColor=white" alt="PyPI"></a>
  <img src="https://img.shields.io/badge/macOS-only-000?logo=apple&logoColor=white" alt="macOS only">
  <img src="https://img.shields.io/badge/iOS-27%2B-000?logo=ios&logoColor=white" alt="iOS 27+">
  <a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/MCP-server-000?logo=modelcontextprotocol&logoColor=white" alt="MCP server"></a>
  <a href="https://github.com/jiefeng-ai/phonectl-mcp/actions/workflows/test.yml"><img src="https://github.com/jiefeng-ai/phonectl-mcp/actions/workflows/test.yml/badge.svg?branch=main" alt="Tests"></a>
  <a href="https://github.com/jiefeng-ai/phonectl-mcp/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="License: MIT"></a>
</p>

<p align="center"><a href="#install">Install</a> · <a href="#tools">Tools</a> · <a href="#troubleshooting">Troubleshooting</a> · <a href="#security">Security</a> · <a href="https://github.com/jiefeng-ai/phonectl-mcp/blob/main/README.zh-CN.md">中文</a></p>

An MCP server for screenshots, on-screen text, taps, swipes, typing, buttons, apps and the clipboard on an iPhone plugged into your Mac. Nothing to install on the phone, no jailbreak.

<p align="center"><img src="https://raw.githubusercontent.com/jiefeng-ai/phonectl-mcp/main/docs/demo.gif" width="358" alt="A script applies to one job in the BOSS Zhipin app through phonectl-mcp, reading the screen with read_text and tapping"></p>
<p align="center"><sub>A script applying to a job in a recruiting app: <code>read_text</code> finds the buttons, <code>tap</code> presses them (2× speed).</sub></p>

## Requirements

- A Mac with [uv](https://docs.astral.sh/uv/)
- An iPhone on **iOS 27 or later**, with Developer Mode on
- A USB data cable (not a charge-only one)

Tested on an iPhone 17 Pro with iOS 27.2 and macOS 27.

## Install

Paste this to your coding agent (Claude Code, Codex, Cursor…):

```text
Install phonectl-mcp on this Mac by following https://github.com/jiefeng-ai/phonectl-mcp#install-by-hand.
Install uv first if it is missing; if `phonectl` is not found afterwards, run `uv tool update-shell`
or call `~/.local/bin/phonectl`. Run `phonectl doctor` and fix what it reports. For anything that
needs my hands on the phone, such as trusting this Mac or turning on Developer Mode, tell me what to
do step by step and wait for me. Then add the server to your own MCP config (streamable HTTP at
http://127.0.0.1:47321/mcp, header `Authorization: Bearer <contents of ~/.config/phonectl/token>`)
and take a screenshot of my phone to check that it works. If you have to restart to load the new
tools, tell me.
```

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

The phone keeps only one control connection to the Mac, so a single resident server owns it and every agent and script connects to that server. It does not stop two agents from tapping at once; let one drive at a time.

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
| No Developer Mode switch | Open Xcode once with the phone connected; the switch appears under Settings › Privacy & Security |
| Screenshots fail (e.g. after the phone restarts) | `phonectl doctor --fix` mounts the developer disk image |
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
| `PHONECTL_UDID` | first USB iPhone | Which phone to use when several are connected |
| `PHONECTL_PORT` | `47321` | HTTP port (127.0.0.1 only) |
| `PHONECTL_TOKEN_FILE` | `~/.config/phonectl/token` | Where the token is kept |
| `PHONECTL_SCALE` | `3` | Pixels per point; 2 on iPhone SE, XR, 11. Only affects screenshot size |
| `PHONECTL_OCR_LANGS` | `zh-Hans,en-US` | `read_text` languages, in priority order |
| `PHONECTL_SETTLE` | `0.6` | Seconds to wait before the screenshot after an action |
| `PHONECTL_TOUCH_IDLE` | `30` | Idle seconds before the input stream closes (reopening takes ~0.6 s) |
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
