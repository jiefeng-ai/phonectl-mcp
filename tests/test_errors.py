import asyncio

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from pymobiledevice3.exceptions import CoreDeviceError, NoDeviceConnectedError

from phonectl import device, server


def _call(name, **args):
    return asyncio.run(server.mcp.call_tool(name, args))


def test_tool_error_carries_the_cause(monkeypatch):
    async def gone(*a, **k):
        raise ConnectionError("phone unreachable after reconnecting: Device is not connected.")

    monkeypatch.setattr(server.device, "launch_app", gone)
    with pytest.raises(ToolError, match="ConnectionError: phone unreachable.*not connected"):
        _call("launch_app", bundle_id="com.apple.mobilesafari", screenshot=False)


def test_unexpected_error_still_names_its_type(monkeypatch):
    async def broken(*a, **k):
        raise NoDeviceConnectedError()

    monkeypatch.setattr(server.device, "list_apps", broken)
    with pytest.raises(ToolError, match="NoDeviceConnectedError"):
        _call("list_apps")


class _Apps:
    error = None

    def __init__(self, rsd):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def launch_application(self, bundle_id, kill_existing):
        raise self.error


def _launch(monkeypatch, error):
    monkeypatch.setattr(device, "AppServiceService", _Apps)
    monkeypatch.setattr(_Apps, "error", error)
    d = device.Device()
    d._stack = object()  # pretend connected, so _retry does not dial the phone
    return asyncio.run(d.launch_app("com.apple.Translate"))


def test_launch_with_unknown_pid_counts_as_launched(monkeypatch):
    assert _launch(monkeypatch, CoreDeviceError("pid unknown", code=device.LAUNCHED_PID_UNKNOWN)) == []


def test_other_launch_errors_are_raised(monkeypatch):
    with pytest.raises(CoreDeviceError, match="refused"):
        _launch(monkeypatch, CoreDeviceError("refused", code=1))
