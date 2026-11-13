"""`phonectl`: set up and check the resident server.

    phonectl install-service     run the server at login (launchd) and print client setup
    phonectl uninstall-service   stop it and remove the launchd job
    phonectl restart             restart it, e.g. after upgrading
    phonectl doctor [--fix]      check every link from the cable to the server, say what to do
"""

from __future__ import annotations

import argparse
import asyncio
import os
import platform
import plistlib
import socket
import subprocess
import sys
import time
from pathlib import Path

LABEL = "io.github.jiefeng-ai.phonectl-mcp"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG_DIR = Path.home() / "Library" / "Logs"
MIN_INPUT_IOS = 27  # taps, swipes and typing
MIN_IOS = 17  # the USB tunnel everything else goes through


def _use_service_env() -> None:
    """doctor and restart must look where the service looks: take the PHONECTL_* settings
    frozen into the launchd job over the ones in this shell."""
    if PLIST.exists():
        with PLIST.open("rb") as f:
            env = plistlib.load(f).get("EnvironmentVariables", {})
        os.environ.update({k: v for k, v in env.items() if k.startswith("PHONECTL_")})


def _server_settings():
    from . import server  # heavy imports, and reads PHONECTL_* on import

    return server


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=check)


def _listener(port: int) -> tuple[int, str] | None:
    """(pid, command) of whatever listens on the port, if anything."""
    r = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"], capture_output=True, text=True)
    rows = r.stdout.splitlines()[1:]
    return (int(rows[0].split()[1]), rows[0].split()[0]) if rows else None


def _job_pid() -> int | None:
    r = _launchctl("print", f"{_domain()}/{LABEL}", check=False)
    for line in r.stdout.splitlines():
        key, _, value = line.strip().partition(" = ")
        if key == "pid" and value.isdigit():
            return int(value)
    return None


def _wait_port(host: str, port: int, seconds: float = 10) -> bool:
    for _ in range(int(seconds / 0.25)):
        if _port_open(host, port):
            return True
        time.sleep(0.25)
    return False


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _client_help(url: str, token_path: Path) -> str:
    return f"""
Connect a client (the URL and the token header are all it needs):

  Claude Code:
    claude mcp add --scope user --transport http phone {url} \\
      --header "Authorization: Bearer $(cat {token_path})"

  Any other MCP client with streamable HTTP:
    url:     {url}
    header:  Authorization: Bearer <contents of {token_path}>
"""


def _stop_job() -> None:
    """Unload the job and wait until it is gone: bootout returns before the old server has
    exited, and a bootstrap issued meanwhile fails with an I/O error."""
    _launchctl("bootout", f"{_domain()}/{LABEL}", check=False)
    for _ in range(50):
        if _launchctl("print", f"{_domain()}/{LABEL}", check=False).returncode != 0:
            return
        time.sleep(0.2)


def install_service(_args) -> int:
    srv = _server_settings()
    exe = Path(sys.executable).parent / "phonectl-mcp"
    if not exe.exists():
        print(f"phonectl-mcp not found next to {sys.executable}; install with `uv tool install phonectl-mcp`.")
        return 1
    if "/.cache/uv/" in str(exe) or "/archive-v" in str(exe):
        print("This copy runs from uv's cache (uvx), which can be cleaned away under the service.\n"
              "Install it for good first:  uv tool install phonectl-mcp   then run  phonectl install-service")
        return 1
    srv._load_token(srv.TOKEN_PATH)  # create it now, so the client setup below works right away
    env = {k: v for k, v in os.environ.items() if k.startswith("PHONECTL_")}
    if "PHONECTL_TOKEN_FILE" in env:  # launchd starts in /, so a relative or ~ path would differ
        env["PHONECTL_TOKEN_FILE"] = str(srv.TOKEN_PATH)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    job = {
        "Label": LABEL,
        # The script itself, not `uv run`: launchd stops a job by signalling this process, and a
        # wrapper that does not pass the signal on leaves the old server holding the phone.
        "ProgramArguments": [str(exe), "--http"],
        # PHONECTL_* settings in the installing shell are kept for the service.
        "EnvironmentVariables": {"PHONECTL_LOG": "INFO", "NO_COLOR": "1", **env},
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 5,
        "StandardOutPath": str(LOG_DIR / "phonectl.log"),
        "StandardErrorPath": str(LOG_DIR / "phonectl.error.log"),
    }
    # Check before touching the running job, so a failed install leaves it as it was.
    held = _listener(srv.HTTP_PORT)
    if held and held[0] != _job_pid():
        print(f"Port {srv.HTTP_PORT} is already used by {held[1]} (pid {held[0]}). Stop it "
              "(an old phonectl-mcp started by hand?) or choose another port with PHONECTL_PORT.")
        return 1
    _stop_job()
    with PLIST.open("wb") as f:
        plistlib.dump(job, f)
    r = _launchctl("bootstrap", _domain(), str(PLIST), check=False)
    if r.returncode != 0:
        print(f"launchctl bootstrap failed: {r.stderr.strip()}")
        return 1
    if not _wait_port(srv.HTTP_HOST, srv.HTTP_PORT):
        print(f"The job is installed but nothing listens on port {srv.HTTP_PORT} yet; "
              f"see {LOG_DIR / 'phonectl.error.log'}")
        return 1
    url = f"http://{srv.HTTP_HOST}:{srv.HTTP_PORT}/mcp"
    print(f"Server running at {url} (launchd job {LABEL}, starts at login).")
    print(_client_help(url, srv.TOKEN_PATH))
    print("Run `phonectl doctor` to check the phone link.")
    return 0


def uninstall_service(_args) -> int:
    _stop_job()
    if PLIST.exists():
        PLIST.unlink()
        print(f"Removed {PLIST}.")
    else:
        print("No launchd job was installed.")
    return 0


def restart(_args) -> int:
    r = _launchctl("kickstart", "-k", f"{_domain()}/{LABEL}", check=False)
    if r.returncode != 0:
        print(f"Not running as a service ({r.stderr.strip() or r.returncode}); run `phonectl install-service`.")
        return 1
    _use_service_env()
    srv = _server_settings()
    time.sleep(1)  # the old server is still closing its port right after kickstart
    if not _wait_port(srv.HTTP_HOST, srv.HTTP_PORT):
        print(f"Restarted, but nothing listens on port {srv.HTTP_PORT}; see {LOG_DIR / 'phonectl.error.log'}.")
        return 1
    print("Restarted.")
    return 0


class _Report:
    def __init__(self) -> None:
        self.failed = False

    def ok(self, msg: str) -> None:
        print(f"  ok    {msg}")

    def warn(self, msg: str, fix: str = "") -> None:
        print(f"  warn  {msg}" + (f"\n        -> {fix}" if fix else ""))

    def fail(self, msg: str, fix: str) -> None:
        self.failed = True
        print(f"  FAIL  {msg}\n        -> {fix}")


async def _server_status(srv, url: str) -> str:
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared._httpx_utils import create_mcp_http_client

    headers = {"Authorization": f"Bearer {srv.TOKEN_PATH.read_text().strip()}"}
    async with create_mcp_http_client(headers=headers) as http:
        async with streamable_http_client(url, http_client=http) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                res = await session.call_tool("status", {})
                return "\n".join(c.text for c in res.content if c.type == "text")


async def _usb_checks(r: _Report, usb: list, fix: bool) -> bool:
    """Trust, iOS version, Developer Mode and the disk image, through usbmux. False: stop here."""
    from pymobiledevice3.lockdown import create_using_usbmux
    from pymobiledevice3.services.amfi import AmfiService
    from pymobiledevice3.services.mobile_image_mounter import MobileImageMounterService

    r.ok(f"iPhone on USB ({len(usb)} found{', using the first' if len(usb) > 1 else ''})")
    try:
        ld = await create_using_usbmux(serial=usb[0].serial, autopair=False)
    except Exception as e:  # noqa: BLE001 - every failure here is reported, none is fatal to the Mac
        r.fail(f"cannot talk to the phone ({e!r})", "Unlock the phone and replug the cable.")
        return False
    try:
        if not ld.paired:
            r.fail("this Mac is not trusted by the phone",
                   "Unlock the phone, replug the cable and tap Trust (enter the passcode if asked).")
            return False
        r.ok("Mac is trusted")
        version = ld.product_version
        name = ld.all_values.get("DeviceName") or "iPhone"
        major = int(version.split(".")[0])
        if major >= MIN_INPUT_IOS:
            r.ok(f"{name}, iOS {version}")
        elif major >= MIN_IOS:
            r.warn(f"{name}, iOS {version}: taps, swipes and typing need iOS {MIN_INPUT_IOS}+ "
                   "(screenshots and apps may work; untested)",
                   f"Update the phone to iOS {MIN_INPUT_IOS} or later for input.")
        else:
            r.fail(f"{name}, iOS {version}: too old", f"phonectl needs iOS {MIN_IOS}+, and iOS {MIN_INPUT_IOS}+ for input.")
            return False
        if await ld.get_developer_mode_status():
            r.ok("Developer Mode is on")
        elif fix:
            # A phone that has never met Xcode hides the switch; this makes Settings show it.
            # Turning it on is left to the user, on the phone.
            try:
                await AmfiService(ld).reveal_developer_mode_option_in_ui()
            except Exception as e:  # noqa: BLE001
                r.fail(f"Developer Mode is off, and showing its switch failed ({e!r})",
                       "Unlock the phone, replug the cable and run `phonectl doctor --fix` again.")
                return False
            r.fail("Developer Mode is off; its switch is now shown",
                   "On the phone: Settings > Privacy & Security > Developer Mode, turn it on and restart, "
                   "then run doctor again.")
            return False
        else:
            r.fail("Developer Mode is off",
                   "On the phone: Settings > Privacy & Security > Developer Mode, turn it on and restart. "
                   "If the switch is missing, run `phonectl doctor --fix` to show it.")
            return False
        try:
            async with MobileImageMounterService(lockdown=ld) as m:
                mounted = await m.is_image_mounted("Personalized")
        except Exception as e:  # noqa: BLE001
            r.fail(f"cannot check the developer disk image ({e!r})", "Unlock the phone and run doctor again.")
            return False
        if mounted:
            r.ok("developer disk image is installed")
        else:
            # Installing it needs the tunnel, which only the server may hold: it installs the
            # image when it connects, and the server check below makes it connect.
            r.warn("developer disk image is not installed yet; the server installs it below")
    finally:
        await ld.close()

    return True


async def _doctor(fix: bool) -> int:
    from pymobiledevice3.usbmux import list_devices

    import logging

    _use_service_env()
    srv = _server_settings()
    # A dependency turns on INFO logging for everything on import; doctor prints its own report.
    logging.getLogger().setLevel(logging.WARNING)
    r = _Report()
    print("Phone")
    if platform.system() != "Darwin":
        r.fail("not macOS", "phonectl needs macOS: the USB tunnel and the text recognition are macOS-only.")
        return 1
    try:
        usb = [d for d in await list_devices() if d.connection_type == "USB"]
    except Exception as e:  # noqa: BLE001
        r.fail(f"cannot list USB devices ({e!r})", "usbmuxd is part of macOS; restart the Mac if this persists.")
        return 1
    udid = os.environ.get("PHONECTL_UDID") or None
    if udid:
        usb = [d for d in usb if d.serial == udid]
    if not usb:
        # Without the cable the phone can still be reached over Wi-Fi through macOS's remoted,
        # which only the server's tunnel can use: opening a second one would knock the server's
        # off. So the server answers for the phone below, and the usbmux checks are skipped.
        r.warn("no iPhone on USB" + (f" with UDID {udid}" if udid else "") + "; checking over Wi-Fi through the server")
    elif not await _usb_checks(r, usb, fix):
        return 1

    print("Server")
    url = f"http://{srv.HTTP_HOST}:{srv.HTTP_PORT}/mcp"
    if PLIST.exists():
        r.ok(f"launchd job installed ({PLIST.name})")
    else:
        r.warn("not installed as a service", "Run `phonectl install-service` so every client shares one server.")
    if not _port_open(srv.HTTP_HOST, srv.HTTP_PORT):
        r.fail(f"nothing listening on {url}",
               f"Run `phonectl install-service` (or `phonectl restart`); see {LOG_DIR / 'phonectl.error.log'}.")
        return 1
    if not srv.TOKEN_PATH.exists():
        r.fail(f"no token at {srv.TOKEN_PATH}", "Run `phonectl restart`; the server creates it on start.")
        return 1
    try:
        status = await _server_status(srv, url)
    except Exception as e:  # noqa: BLE001
        r.fail(f"the server at {url} did not answer ({e!r})",
               "Check the token and that the port is not used by another program.")
        return 1
    if "phone: connected" in status:
        r.ok(f"server at {url} reaches the phone" + ("" if usb else " over Wi-Fi"))
        if not usb:
            r.warn("trust, Developer Mode and the disk image were not checked: they need the cable",
                   "Plug in the cable and run doctor again to check them.")
    elif not usb:
        r.fail("the phone is neither on USB nor reachable over Wi-Fi:\n        " + status.replace("\n", "\n        "),
               "Connect it with a data cable (not charge-only) and unlock it. For Wi-Fi, set it up over "
               "the cable once and keep the phone and the Mac on the same network. A locked phone "
               "keeps dropping off Wi-Fi: unlock it and wait a few seconds.")
    else:
        r.fail("the server is up but cannot reach the phone:\n        " + status.replace("\n", "\n        "),
               "Replug the cable, unlock the phone, then `phonectl restart`.")
    print("\nAll good." if not r.failed else "\nFix the FAIL lines above, then run `phonectl doctor` again.")
    return 1 if r.failed else 0


def doctor(args) -> int:
    return asyncio.run(_doctor(args.fix))


def main() -> None:
    ap = argparse.ArgumentParser(prog="phonectl", description="Set up and check the phonectl MCP server.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("install-service", help="run the server at login and print client setup").set_defaults(fn=install_service)
    sub.add_parser("uninstall-service", help="stop the server and remove the launchd job").set_defaults(fn=uninstall_service)
    sub.add_parser("restart", help="restart the server").set_defaults(fn=restart)
    d = sub.add_parser("doctor", help="check the phone, the USB link and the server")
    d.add_argument("--fix", action="store_true", help="show the Developer Mode switch in Settings if it is missing")
    d.set_defaults(fn=doctor)
    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
