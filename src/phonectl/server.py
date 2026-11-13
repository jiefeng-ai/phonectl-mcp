"""MCP server that lets an agent see and drive an iPhone over USB.

Coordinates in every tool are iOS points, the size of the screenshot this server returns
(402x874 on iPhone 17 Pro). Everything goes over the USB cable (Device); taps, swipes and
typing need iOS 27+.

Run with --http as the one resident server for every client (`phonectl install-service`):
the phone keeps a single control connection on the native tunnel, so one server process per
agent session made them evict each other. Without --http it speaks stdio, for one client.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import hmac
import io
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Optional

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from PIL import Image as PILImage

from pymobiledevice3.exceptions import CoreDeviceError
from pymobiledevice3.remote.core_device.hid_service import ASCII_TO_HID

from . import __version__
from .device import NORM_MAX, Device, InputUncertain
from .keys import parse_key
from .ocr import recognize

log = logging.getLogger(__name__)

# Pixels per point: 3 on most iPhones, 2 on iPhone SE / XR / 11. Only sets how large the
# returned image is; taps are mapped from that same image, so a wrong value never misplaces them.
SCALE = int(os.environ.get("PHONECTL_SCALE", "3"))
SETTLE_S = float(os.environ.get("PHONECTL_SETTLE", "0.6"))  # wait for animations before the after-shot
JPEG_QUALITY = 80
HTTP_HOST = "127.0.0.1"  # never anything reachable from another machine
HTTP_PORT = int(os.environ.get("PHONECTL_PORT", "47321"))
TOKEN_PATH = Path(os.environ.get("PHONECTL_TOKEN_FILE", Path.home() / ".config/phonectl/token")).expanduser().resolve()

# Shown by status, so after a restart it is clear which code the server is running.
_STARTED = time.strftime("%Y-%m-%d %H:%M:%S")
_CODE_STAMP = time.strftime(
    "%Y-%m-%d %H:%M:%S",
    time.localtime(max(p.stat().st_mtime for p in Path(__file__).parent.glob("*.py"))),
)

mcp = MCPServer("phonectl")
device = Device(os.environ.get("PHONECTL_UDID") or None)
_screen_pt: Optional[tuple[float, float]] = None
# Held from putting text on the clipboard until it is pasted, so another client's clipboard
# write cannot land in between and get pasted instead.
_clipboard_lock = asyncio.Lock()


# Failures whose message says everything; anything else is a bug here and is logged in full.
_EXPECTED = (ValueError, ConnectionError, OSError, InputUncertain, CoreDeviceError)


def _tool(fn):
    """mcp.tool(), but a failure reaches the client with its cause.

    The MCP SDK reports any exception other than ToolError as a bare "Error executing tool
    X", which hides whether the phone is gone, the input was rejected or the app refused.
    """

    @functools.wraps(fn)
    async def run(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except ToolError:
            raise
        except Exception as e:
            if not isinstance(e, _EXPECTED):
                log.exception("tool %s failed", fn.__name__)
            raise ToolError(f"{type(e).__name__}: {e}") from e

    return mcp.tool()(run)


async def _capture() -> Image:
    global _screen_pt
    png = await device.screenshot_png()
    img = PILImage.open(io.BytesIO(png)).convert("RGB")
    w, h = img.size[0] / SCALE, img.size[1] / SCALE
    _screen_pt = (w, h)
    img = img.resize((round(w), round(h)), PILImage.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=JPEG_QUALITY)
    return Image(data=buf.getvalue(), format="jpeg")


async def _norm(x: float, y: float) -> tuple[int, int]:
    """Points -> the USB touch path's 0..65535 screen-normalised units."""
    if _screen_pt is None:
        await _capture()
    w, h = _screen_pt
    if not (0 <= x <= w and 0 <= y <= h):
        raise ValueError(f"({x:g}, {y:g}) is outside the {w:g}x{h:g} point screen")
    return round(x / w * NORM_MAX), round(y / h * NORM_MAX)


async def _do_tap(x: float, y: float, hold_ms: int) -> None:
    await device.tap(*await _norm(x, y), hold_s=hold_ms / 1000)


async def _do_key(spec: str) -> None:
    await device.key(*parse_key(spec))


async def _after(note: str, screenshot: bool) -> list[str | Image]:
    if not screenshot:
        return [note]
    await asyncio.sleep(SETTLE_S)
    return [note, await _capture()]


@_tool
async def screenshot(native: bool = False) -> list[str | Image]:
    """Capture the iPhone screen. The image is in iOS points; use its pixel coordinates directly in tap/swipe.
    native=True is for scripts (OCR, saving to a file): the full-resolution PNG, SCALE pixels per point.
    It is several times larger, so agents looking at the screen should leave it False."""
    if native:
        global _screen_pt
        png = await device.screenshot_png()
        w, h = PILImage.open(io.BytesIO(png)).size
        _screen_pt = (w / SCALE, h / SCALE)
        return [f"screen {_screen_pt[0]:g}x{_screen_pt[1]:g} points, {w}x{h} px png", Image(data=png, format="png")]
    img = await _capture()
    return [f"screen {_screen_pt[0]:g}x{_screen_pt[1]:g} points", img]


@_tool
async def read_text() -> str:
    """Read the text on the iPhone screen (Apple Vision OCR, Chinese and English) without an image.
    Returns a JSON array of lines {"t": text, "x": cx, "y": cy, "w": w, "h": h}, the box centre
    and size in points, so (x, y) can be passed straight to tap. Cheaper than screenshot for scripts."""
    global _screen_pt
    png = await device.screenshot_png()
    w, h = PILImage.open(io.BytesIO(png)).size
    _screen_pt = (w / SCALE, h / SCALE)
    lines = await asyncio.to_thread(recognize, png, w, h)
    for l in lines:
        for k in ("x", "y", "w", "h"):
            l[k] = round(l[k] / SCALE, 1)
    return json.dumps(lines, ensure_ascii=False)


@_tool
async def tap(x: float, y: float, screenshot: bool = True) -> list[str | Image]:
    """Tap at (x, y) in points. Returns a screenshot taken after the tap unless screenshot=False.
    Taps are not confirmed by the phone: check the screenshot to see whether it landed."""
    await _do_tap(x, y, 50)
    return await _after(f"tapped ({x:g}, {y:g})", screenshot)


@_tool
async def long_press(x: float, y: float, duration_ms: int = 800, screenshot: bool = True) -> list[str | Image]:
    """Press and hold at (x, y) in points for duration_ms."""
    await _do_tap(x, y, duration_ms)
    return await _after(f"long-pressed ({x:g}, {y:g}) for {duration_ms} ms", screenshot)


@_tool
async def swipe(x1: float, y1: float, x2: float, y2: float, duration_ms: int = 300, screenshot: bool = True) -> list[str | Image]:
    """Drag from (x1, y1) to (x2, y2) in points over duration_ms. Scroll down = swipe upward.
    Go home by swiping up from the bottom edge, or use press_button('home')."""
    await device.drag(*await _norm(x1, y1), *await _norm(x2, y2), duration_ms / 1000)
    return await _after(f"swiped ({x1:g}, {y1:g}) -> ({x2:g}, {y2:g})", screenshot)


@_tool
async def type_text(text: str, screenshot: bool = True) -> list[str | Image]:
    """Type text into the focused field (tap the field first). ASCII is typed key by key
    ('\\n' presses Return); any other text (Chinese, emoji) is put on the iPhone clipboard and
    pasted with Cmd+V, which replaces what was on the clipboard."""
    if all(c in ASCII_TO_HID for c in text):
        await device.type_ascii(text)
        how = "typed"
    else:
        async with _clipboard_lock:
            await device.set_clipboard(text)
            await _do_key("cmd+v")
        how = "pasted"
    return await _after(f"{how} {len(text)} characters", screenshot)


@_tool
async def press_key(key: str, screenshot: bool = True) -> list[str | Image]:
    """Press a keyboard key or chord, e.g. 'enter', 'backspace', 'tab', 'esc', 'up',
    'cmd+a', 'cmd+space' (Spotlight). Modifiers: ctrl, shift, alt, cmd. Keys: a-z, 0-9, enter,
    esc, backspace, tab, space, up, down, left, right, home, end, pageup, pagedown, forwarddelete."""
    await _do_key(key)
    return await _after(f"pressed {key}", screenshot)


@_tool
async def press_button(name: str, screenshot: bool = True) -> list[str | Image]:
    """Press an iPhone hardware button over USB: home, lock, volume-up, volume-down, mute, siri.
    'home' returns to the home screen; 'lock' locks the phone."""
    await device.press_button(name)
    return await _after(f"pressed {name}", screenshot)


@_tool
async def launch_app(bundle_id: str, restart: bool = False, screenshot: bool = True) -> list[str | Image]:
    """Open an app by bundle id (see list_apps). An app already running is brought to the
    front as it was; restart=True kills it first."""
    await device.launch_app(bundle_id, restart=restart)
    return await _after(f"launched {bundle_id}", screenshot)


@_tool
async def list_apps(query: str = "") -> str:
    """List user-visible apps as 'name  bundle_id', optionally filtered by a substring of either."""
    apps = await device.list_apps()
    q = query.lower()
    rows = sorted(
        (a.get("name", ""), a.get("bundleIdentifier", ""))
        for a in apps
        if not q or q in a.get("name", "").lower() or q in a.get("bundleIdentifier", "").lower()
    )
    return "\n".join(f"{n}  {b}" for n, b in rows) or "no match"


@_tool
async def get_clipboard() -> str:
    """Read the iPhone clipboard as text."""
    return (await device.get_clipboard()) or ""


@_tool
async def set_clipboard(text: str) -> str:
    """Put text on the iPhone clipboard."""
    async with _clipboard_lock:
        await device.set_clipboard(text)
    return "ok"


@_tool
async def status() -> str:
    """Report the server and the USB link to the phone."""
    lines = [f"server: phonectl-mcp {__version__}, pid {os.getpid()}, up since {_STARTED}, code from {_CODE_STAMP}"]
    try:
        await device.screenshot_png()
        lines.append(f"phone: connected over {device.transport} tunnel")
    except Exception as e:  # noqa: BLE001 - status reports whatever is wrong
        lines.append(f"phone: unavailable ({e})")
    return "\n".join(lines)


def _load_token(path: Path) -> str:
    """The shared secret clients send as 'Authorization: Bearer <token>'; made on first run."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        # O_EXCL: if two processes start at once, only one writes; the other reads its token.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        token = path.read_text().strip()
        if not token:
            raise SystemExit(f"{path} is empty; delete it and start again to make a new token")
        if path.stat().st_mode & 0o077:
            path.chmod(0o600)  # nobody else on the Mac should read it
        return token
    token = secrets.token_urlsafe(32)
    with os.fdopen(fd, "w") as f:
        f.write(token + "\n")
    log.warning("created a new access token in %s", path)
    return token


class _BearerAuth:
    """ASGI middleware: refuse any connection without the token. Only lifespan events pass."""

    def __init__(self, app, token: str):
        self.app = app
        self.token = token.encode()

    def _authorized(self, scope) -> bool:
        given = next((v for k, v in scope["headers"] if k == b"authorization"), b"")
        scheme, _, value = given.partition(b" ")
        return scheme.lower() == b"bearer" and hmac.compare_digest(value.strip(), self.token)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "lifespan" and not self._authorized(scope):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain"),
                                        (b"www-authenticate", b'Bearer realm="phonectl"')]})
                await send({"type": "http.response.body", "body": b"unauthorized\n"})
            return
        await self.app(scope, receive, send)


def _serve_http() -> None:
    import uvicorn

    hosts = [f"{HTTP_HOST}:{HTTP_PORT}", f"localhost:{HTTP_PORT}"]
    app = mcp.streamable_http_app(
        # No per-client session state, so a restarted server serves clients that are
        # already connected without them having to reconnect.
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=hosts,
            allowed_origins=[f"http://{h}" for h in hosts],
        ),
        host=HTTP_HOST,
    )
    uvicorn.run(
        _BearerAuth(app, _load_token(TOKEN_PATH)),
        host=HTTP_HOST,
        port=HTTP_PORT,
        log_level=os.environ.get("PHONECTL_LOG", "WARNING").lower(),
        access_log=False,
        # Clients hold a long-lived stream open; without a limit, shutdown waits on it forever
        # and the old server keeps the phone link while its replacement starts.
        timeout_graceful_shutdown=3,
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="phonectl-mcp")
    parser.add_argument("--http", action="store_true",
                        help=f"serve MCP over HTTP on {HTTP_HOST}:{HTTP_PORT}/mcp instead of stdio")
    args = parser.parse_args()
    logging.basicConfig(
        level=os.environ.get("PHONECTL_LOG", "WARNING").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.http:
        _serve_http()
    else:
        mcp.run()


if __name__ == "__main__":
    main()
