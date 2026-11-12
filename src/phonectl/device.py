"""Control of the iPhone over USB: screenshots, touch, keyboard, apps, hardware buttons, clipboard.

One long-lived tunnel and DVT session are kept open, because opening them per call is
what made the CLI take ~2 s per screenshot (a held session takes ~340 ms).

Touch and keyboard need iOS 27+: they go through the same HID path as Xcode's mirror
window, which only lets input through while a screen media stream is running. That stream
is opened on the first input (~0.6 s), held while input keeps coming (a tap is then ~50 ms),
and closed after TOUCH_IDLE_S without input, so the phone isn't left mirroring for nothing.
The virtual keyboard is attached on the first key and detached after KEY_IDLE_S without one.
Input is fire-and-forget: nothing confirms a tap landed, so callers verify with a screenshot.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import AsyncExitStack
from typing import Any, Awaitable, Callable, Optional

from pymobiledevice3.exceptions import AlreadyMountedError, ConnectionTerminatedError
from pymobiledevice3.remote import native_tunnel, userspace_tunnel
from pymobiledevice3.remote.core_device.app_service import AppServiceService
from pymobiledevice3.remote.core_device.hid_service import (
    ASCII_TO_HID,
    HID_BUTTON_STATE_DOWN,
    HID_BUTTON_STATE_UP,
    KEY_LEFT_SHIFT,
    TOUCHSCREEN_STATE_CONTACT,
    TOUCHSCREEN_STATE_RELEASE,
    IndigoHIDService,
    UniversalHIDServiceService,
    touch_session,
)
from pymobiledevice3.remote.core_device.pasteboard_service import (
    GENERAL_PASTEBOARD,
    PasteboardService,
    snapshot_text,
    text_item,
)
from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
from pymobiledevice3.services.dvt.instruments.screenshot import Screenshot
from pymobiledevice3.services.cryptexd import CryptexdService, fetch_cryptex_ddi
from pymobiledevice3.services.mobile_image_mounter import (
    PersonalizedImageMounter,
    fetch_personalized_ddi,
    uses_cryptex_image,
)

log = logging.getLogger(__name__)

# (usage_page, usage_code, hold_seconds), as used by pymobiledevice3's `hid button`.
# Lock and Siri need a longer hold or iOS reads them as bounce.
BUTTONS: dict[str, tuple[int, int, float]] = {
    "home": (0x0C, 0x40, 0.05),
    "lock": (0x0C, 0x30, 0.5),
    "volume-up": (0x0C, 0xE9, 0.05),
    "volume-down": (0x0C, 0xEA, 0.05),
    "mute": (0x0C, 0xE2, 0.05),
    "siri": (0x0C, 0xCF, 1.0),
}

# Touch coordinates are 0..NORM_MAX across the screen on both axes, whatever its resolution.
NORM_MAX = 65535
# How long the input stream stays open without input. 45 s was verified to still deliver taps;
# longer idle sessions were less reliable, so keep them short.
TOUCH_IDLE_S = float(os.environ.get("PHONECTL_TOUCH_IDLE", "30"))
# The virtual keyboard is dropped after this long without a key, while touch stays up. Some apps
# (Xianyu's chat) drop text focus while a hardware keyboard is attached, and iOS hides the
# on-screen keyboard; without one attached the phone behaves as it does in the hand.
KEY_IDLE_S = float(os.environ.get("PHONECTL_KEY_IDLE", "5"))
FRAME_S = 0.016  # one touch sample per display frame while dragging
KEY_HOLD_S = 0.04  # key down -> up, as pymobiledevice3's `type` does
KEY_GAP_S = 0.02  # between keys
# A reply that never comes (half-open link) would otherwise hang the call forever while it
# holds the device lock, and with one shared server that freezes every client. Each call gets
# this much on top of the time its gesture or typing takes by itself.
CALL_TIMEOUT_S = float(os.environ.get("PHONECTL_CALL_TIMEOUT", "15"))
# Connecting: the native tunnel is left to its own per-step limits in pymobiledevice3 (its
# work runs in threads that a cancel would not stop); the steps after it get these.
USERSPACE_TIMEOUT_S = 30.0
DVT_OPEN_TIMEOUT_S = 15.0
# Each of the two steps of installing the developer disk image (download, ~16 MB from
# GitHub; then personalizing and installing it) gets this long. With the image check and DVT
# a first connect stays under 300 s, the read timeout of doctor's MCP client.
DISK_IMAGE_TIMEOUT_S = 120.0
# Each teardown step (touch session, DVT, tunnel) gets this long; a dead link can leave a
# goodbye message unanswered forever, e.g. stopping the media stream behind touch.
CLOSE_TIMEOUT_S = 5.0
# remoted may be mid-redial when we reconnect (it and we evict each other's RSD connection),
# so a failed reconnect is tried once more after this pause.
RECONNECT_BACKOFF_S = 1.0
# Upper bound on how long typing one character takes (holds, gap, and the sends themselves).
TYPE_S_PER_CHAR = 0.15

# Errors meaning the link to the phone is gone. ConnectionTerminatedError (the DTX channel
# died: cable replugged, CoreDevice restarted by Xcode) is not an OSError, so it is listed.
# TimeoutError is an OSError, so our own call timeout lands here too (see Device._retry).
LINK_ERRORS = (ConnectionError, OSError, asyncio.IncompleteReadError, EOFError, ConnectionTerminatedError)


class InputUncertain(RuntimeError):
    """The link failed during an input call that may already have partly reached the phone."""


class _InputSent(Exception):
    """Internal: a link error raised after at least one input report went out (cause attached)."""


class _SendCounter:
    """Proxy for a HID service that counts the send_* calls that completed."""

    def __init__(self, target: Any):
        self._target = target
        self.sent = 0

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._target, name)
        if not name.startswith("send_"):
            return attr

        async def counted(*args: Any, **kwargs: Any) -> Any:
            result = await attr(*args, **kwargs)
            self.sent += 1
            return result

        return counted


async def _run_counted(svc: Any, op: Callable[[Any], Awaitable[None]]) -> None:
    """Run an input op; a link error after any report went out becomes _InputSent."""
    counter = _SendCounter(svc)
    try:
        await op(counter)
    except LINK_ERRORS as e:
        if counter.sent:
            raise _InputSent() from e
        raise


async def _close_bounded(what: str, close: Callable[..., Awaitable[Any]], *args: Any) -> None:
    """One teardown step, given CLOSE_TIMEOUT_S; a step that fails or hangs is logged and left."""
    try:
        async with asyncio.timeout(CLOSE_TIMEOUT_S):
            await close(*args)
    except TimeoutError:
        log.warning("closing the %s took over %.0f s; left behind", what, CLOSE_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 - the link is being dropped either way
        log.warning("closing the %s failed (%r)", what, e)


async def _enter_bounded(stack: AsyncExitStack, cm: Any, what: str) -> Any:
    """stack.enter_async_context(cm), but with cm's exit run through _close_bounded."""
    value = await cm.__aenter__()
    stack.push_async_callback(_close_bounded, what, cm.__aexit__, None, None, None)
    return value

# Modifier bit i of a HID keyboard report is usage 0xE0 + i (Ctrl, Shift, Alt, GUI, ...).
_MOD_USAGE_BASE = 0xE0


class DiskImageDownloadError(RuntimeError):
    """The developer disk image could not be downloaded; reconnecting at once will not help."""


async def _ensure_disk_image(rsd: Any) -> None:
    """Install the developer disk image if the phone lacks it; DVT (screenshots) needs it.

    A phone that has never met Xcode has none. On iOS 27 it is a cryptex that stays installed
    across restarts, so this normally installs it once per phone. Installing needs the tunnel
    (plain usbmux is refused), and the phone takes one tunnel at a time, so the server does it
    here rather than doctor.
    """
    try:
        async with asyncio.timeout(DVT_OPEN_TIMEOUT_S):
            async with PersonalizedImageMounter(lockdown=rsd) as mounter:
                if await mounter.is_image_mounted(mounter.IMAGE_TYPE):
                    return
    except Exception as e:  # noqa: BLE001 - if the image is really missing, DVT below says so
        log.warning("developer disk image check failed (%r); opening DVT anyway", e)
        return
    log.info("developer disk image not installed; installing it")
    # pymobiledevice3's own download is a blocking requests.get without a timeout, on the event
    # loop, which would freeze every client. So it is downloaded here in a thread, and the
    # install is handed the files: auto_mount would download again whenever the image on GitHub
    # is newer than the build id pymobiledevice3 expects, since it then treats its cache as stale.
    cryptex = uses_cryptex_image(rsd)
    try:
        async with asyncio.timeout(DISK_IMAGE_TIMEOUT_S):
            image = await asyncio.to_thread(fetch_cryptex_ddi if cryptex else fetch_personalized_ddi)
    except Exception as e:
        raise DiskImageDownloadError(
            f"downloading the developer disk image from GitHub failed ({e!r}); "
            "check the network or proxy, then `phonectl restart`"
        ) from e
    try:
        async with asyncio.timeout(DISK_IMAGE_TIMEOUT_S):
            if cryptex:  # iOS 27+
                await CryptexdService(rsd).auto_install_ddi(restore_dir=image)
            else:
                async with PersonalizedImageMounter(lockdown=rsd) as mounter:
                    await mounter.mount(*image)
    except AlreadyMountedError:
        # The image mounter said no image, but cryptexd has one installed.
        log.info("developer disk image already installed as a cryptex")
        return
    log.info("developer disk image installed")


def modifier_usages(mod_bits: int) -> tuple[int, ...]:
    return tuple(_MOD_USAGE_BASE + i for i in range(8) if mod_bits >> i & 1)

InputOp = Callable[[UniversalHIDServiceService], Awaitable[None]]


class Device:
    """Holds the tunnel and DVT session; reconnects once if a call fails on a dead link."""

    def __init__(self, serial: Optional[str] = None):
        self.serial = serial
        self._stack: Optional[AsyncExitStack] = None
        self._rsd = None
        self._shot: Optional[Screenshot] = None
        self._lock = asyncio.Lock()
        self.transport = ""
        # Touch/keyboard session, opened on demand and closed when idle (see module doc).
        self._touch_stack: Optional[AsyncExitStack] = None
        self._touch: Optional[UniversalHIDServiceService] = None
        # The keyboard has its own HID connection, so it can be detached on its own.
        self._kb_stack: Optional[AsyncExitStack] = None
        self._kb: Optional[UniversalHIDServiceService] = None
        self._keyboard_id: Optional[int] = None
        self._key_last = 0.0
        self._touch_last = 0.0
        self._reaper: Optional[asyncio.Task] = None

    async def connect(self) -> None:
        await self.close()
        stack = AsyncExitStack()
        try:
            # The macOS native tunnel measured steadier (334-386 ms per shot) than the
            # userspace one (330-1374 ms), so prefer it and fall back if unavailable.
            # Own the native tunnel instead of using establish_native_rsd: that CLI helper caches
            # one tunnel per process and hands the same dead RSD back on every reconnect, so after
            # the phone dropped off once, every call failed with "No route to host" until restart.
            try:
                tunnel = native_tunnel.NativeRemotedTunnel(serial=self.serial)
                rsd = await tunnel.aopen()
                stack.push_async_callback(_close_bounded, "native tunnel", tunnel.aclose)
                self.transport = "native"
            except Exception as e:  # noqa: BLE001 - any native failure means "use userspace"
                log.info("native tunnel unavailable (%r), using userspace", e)
                try:
                    async with asyncio.timeout(USERSPACE_TIMEOUT_S):
                        rsd = await userspace_tunnel.establish_userspace_rsd(
                            serial=self.serial, remotepairing_fallback=False
                        )
                except TimeoutError:
                    raise ConnectionError(f"userspace tunnel not up after {USERSPACE_TIMEOUT_S:.0f} s") from None
                self.transport = "userspace"
                if hasattr(rsd, "close"):
                    stack.push_async_callback(_close_bounded, "userspace tunnel", rsd.close)
            await _ensure_disk_image(rsd)
            try:
                async with asyncio.timeout(DVT_OPEN_TIMEOUT_S):
                    dvt = await _enter_bounded(stack, DvtProvider(rsd), "DVT session")
                    self._shot = await _enter_bounded(stack, Screenshot(dvt), "screenshot channel")
            except TimeoutError:
                raise ConnectionError(f"DVT session not open after {DVT_OPEN_TIMEOUT_S:.0f} s") from None
        except BaseException:
            await stack.aclose()
            raise
        self._stack, self._rsd = stack, rsd

    async def close(self) -> None:
        """Drop the link. Every teardown step is bounded, so this never hangs on a dead link."""
        await self._close_touch()
        if self._stack is not None:
            stack, self._stack, self._rsd, self._shot = self._stack, None, None, None
            await stack.aclose()

    async def _ensure(self) -> None:
        if self._stack is None:
            await self.connect()

    async def _reconnect(self) -> None:
        try:
            await self.connect()
        except DiskImageDownloadError:
            raise  # a second try would only download for minutes more while every client waits
        except Exception as e:  # noqa: BLE001 - any failure gets the one retry below
            log.warning("reconnect failed (%r); trying again in %.0f s", e, RECONNECT_BACKOFF_S)
            await asyncio.sleep(RECONNECT_BACKOFF_S)
            await self.connect()

    async def _retry(self, op, *, extra_s: float = 0.0, repeatable: bool = True):
        """Run op() with a timeout; on a dead link reconnect and run it once more.

        Input must not land twice (a doubled tap, text typed twice), so an input op is run
        again only when nothing went out: input ops report reports already sent as _InputSent,
        and pass repeatable=False because a timeout cannot tell how far they got. Then the
        link is dropped and InputUncertain tells the caller to look before repeating.
        A failed call always drops the link, so the next call starts on a fresh one.
        """
        timeout = CALL_TIMEOUT_S + extra_s
        async with self._lock:
            await self._ensure()
            retried = False
            while True:
                deadline = asyncio.timeout(timeout)
                try:
                    async with deadline:
                        return await op()
                except _InputSent as e:
                    error, uncertain = e.__cause__, True
                except LINK_ERRORS as e:
                    error, uncertain = e, False
                if deadline.expired():
                    error = TimeoutError(f"the phone did not answer within {timeout:.0f} s")
                    uncertain = not repeatable
                log.warning("device call failed (%r); dropping the link", error)
                await self.close()
                if uncertain:
                    raise InputUncertain(
                        f"{error}; the input may have partly reached the phone, "
                        "so take a screenshot before repeating it"
                    ) from error
                if retried:
                    raise ConnectionError(f"phone unreachable after reconnecting: {error}") from error
                retried = True
                await self._reconnect()

    async def screenshot_png(self) -> bytes:
        return await self._retry(lambda: self._shot.get_screenshot())

    async def launch_app(self, bundle_id: str, restart: bool = False) -> list[dict[str, Any]]:
        # pymobiledevice3 kills a running instance by default; an agent almost never wants
        # that, since it throws away whatever screen the app was on.
        async def op():
            async with AppServiceService(self._rsd) as apps:
                return await apps.launch_application(bundle_id, kill_existing=restart)

        return await self._retry(op)

    async def list_apps(self) -> list[dict[str, Any]]:
        async def op():
            async with AppServiceService(self._rsd) as apps:
                return await apps.list_apps(include_hidden_apps=False, include_internal_apps=False)

        return await self._retry(op)

    async def press_button(self, name: str) -> None:
        if name not in BUTTONS:
            raise ValueError(f"unknown button {name!r}; expected one of {sorted(BUTTONS)}")
        page, code, hold = BUTTONS[name]

        async def press(hid):
            await hid.send_button(page, code, HID_BUTTON_STATE_DOWN)
            await asyncio.sleep(hold)
            await hid.send_button(page, code, HID_BUTTON_STATE_UP)
            # dtuhidd handles messages asynchronously; closing at once can drop the UP.
            await asyncio.sleep(0.1)

        async def op():
            async with IndigoHIDService(self._rsd) as hid:
                await _run_counted(hid, press)

        await self._retry(op, extra_s=hold, repeatable=False)

    async def set_clipboard(self, text: str) -> None:
        async def op():
            async with PasteboardService(self._rsd) as pb:
                await pb.set([text_item(text)], GENERAL_PASTEBOARD)

        await self._retry(op)

    async def get_clipboard(self) -> Optional[str]:
        async def op():
            async with PasteboardService(self._rsd) as pb:
                return snapshot_text(await pb.get(GENERAL_PASTEBOARD))

        return await self._retry(op)

    # --- touch and keyboard (iOS 27+) -------------------------------------------------

    async def _close_keyboard(self) -> None:
        if self._kb_stack is not None:
            stack, self._kb_stack, self._kb, self._keyboard_id = self._kb_stack, None, None, None
            await stack.aclose()

    async def _close_touch(self) -> None:
        await self._close_keyboard()
        if self._touch_stack is not None:
            stack, self._touch_stack, self._touch = self._touch_stack, None, None
            await stack.aclose()

    async def _reap_idle_touch(self) -> None:
        while True:
            await asyncio.sleep(min(1.0, KEY_IDLE_S, TOUCH_IDLE_S))
            async with self._lock:
                if self._touch is None:
                    return
                now = time.monotonic()
                if now - self._touch_last >= TOUCH_IDLE_S:
                    log.info("closing touch session after %.0f s idle", TOUCH_IDLE_S)
                    await self._close_touch()
                    return
                if self._kb is not None and now - self._key_last >= KEY_IDLE_S:
                    log.info("detaching the keyboard after %.0f s without a key", KEY_IDLE_S)
                    await self._close_keyboard()

    async def _input(self, op: InputOp, extra_s: float = 0.0, keyboard: bool = False) -> None:
        """Run op on the touch session, opening it (and the idle reaper) if needed.

        With keyboard=True, op gets the keyboard's connection instead, and its service id is in
        self._keyboard_id. The keyboard is attached on first use and detached after KEY_IDLE_S.
        """

        async def run():
            if self._touch is None:
                stack = AsyncExitStack()
                try:
                    self._touch = await _enter_bounded(stack, touch_session(self._rsd), "touch session")
                except RuntimeError as e:
                    # touch_session raises a bare RuntimeError when the media stream that gates
                    # touch does not start; a fresh link is worth one try before giving up.
                    await stack.aclose()
                    raise ConnectionError(f"touch session did not start: {e}") from e
                except BaseException:
                    await stack.aclose()
                    raise
                self._touch_stack = stack
                if self._reaper is None or self._reaper.done():
                    self._reaper = asyncio.create_task(self._reap_idle_touch())
            if keyboard:
                if self._kb is None:
                    stack = AsyncExitStack()
                    try:
                        # Opened inside the touch session: its media stream is what lets the
                        # keyboard's reports through.
                        kb = await _enter_bounded(stack, UniversalHIDServiceService(self._rsd), "keyboard")
                        self._keyboard_id = await kb.create_keyboard_service()
                    except BaseException:
                        await stack.aclose()
                        raise
                    self._kb_stack, self._kb = stack, kb
                await _run_counted(self._kb, op)
                self._key_last = time.monotonic()
            else:
                await _run_counted(self._touch, op)
            self._touch_last = time.monotonic()

        await self._retry(run, extra_s=extra_s, repeatable=False)

    async def tap(self, x: int, y: int, hold_s: float = 0.05) -> None:
        """Touch down and up at normalised (x, y); a long hold is a long press."""

        async def op(t: UniversalHIDServiceService):
            await t.send_touchscreen(TOUCHSCREEN_STATE_CONTACT, x, y)
            await asyncio.sleep(hold_s)
            await t.send_touchscreen(TOUCHSCREEN_STATE_RELEASE, x, y)

        await self._input(op, extra_s=hold_s)

    async def drag(self, x1: int, y1: int, x2: int, y2: int, duration_s: float) -> None:
        """One finger down at (x1, y1), moved in a straight line to (x2, y2), lifted."""
        steps = max(2, round(duration_s / FRAME_S))

        async def op(t: UniversalHIDServiceService):
            for i in range(steps + 1):
                f = i / steps
                await t.send_touchscreen(TOUCHSCREEN_STATE_CONTACT, round(x1 + (x2 - x1) * f), round(y1 + (y2 - y1) * f))
                if i < steps:
                    await asyncio.sleep(duration_s / steps)
            await t.send_touchscreen(TOUCHSCREEN_STATE_RELEASE, x2, y2)

        await self._input(op, extra_s=duration_s)

    async def _press(self, t: UniversalHIDServiceService, kb: int, mods: tuple[int, ...], usage: int) -> None:
        # iOS can miss a modifier that arrives in the same report as the key, so hold it first.
        if mods:
            await t.send_keyboard(kb, mods)
            await asyncio.sleep(KEY_HOLD_S)
        await t.send_keyboard(kb, (*mods, usage))
        await asyncio.sleep(KEY_HOLD_S)
        await t.send_keyboard(kb, ())
        await asyncio.sleep(KEY_GAP_S)

    async def key(self, mod_bits: int, usage: int) -> None:
        """Press one key with modifiers (bits as in keys.MODS) on the virtual keyboard."""

        async def op(t: UniversalHIDServiceService):
            await self._press(t, self._keyboard_id, modifier_usages(mod_bits), usage)

        await self._input(op, keyboard=True)

    async def type_ascii(self, text: str) -> None:
        """Type printable ASCII (plus \\n and \\t) on the virtual keyboard, US layout."""
        bad = sorted({c for c in text if c not in ASCII_TO_HID})
        if bad:
            raise ValueError(f"not typeable on the virtual keyboard: {''.join(bad)!r}")

        async def op(t: UniversalHIDServiceService):
            kb = self._keyboard_id
            for c in text:
                usage, shift = ASCII_TO_HID[c]
                await self._press(t, kb, (KEY_LEFT_SHIFT,) if shift else (), usage)

        await self._input(op, extra_s=len(text) * TYPE_S_PER_CHAR, keyboard=True)
