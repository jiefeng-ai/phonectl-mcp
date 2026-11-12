import asyncio

import pytest
from pymobiledevice3 import lockdown
from pymobiledevice3.exceptions import PyMobileDevice3Exception
from pymobiledevice3.services import amfi

from phonectl import cli


class _Lockdown:
    """A paired iOS 27 phone with Developer Mode off."""

    paired = True
    product_version = "27.2"
    all_values = {"DeviceName": "iPhone"}

    async def get_developer_mode_status(self):
        return False

    async def close(self):
        pass


class _Amfi:
    calls = 0
    error = None

    def __init__(self, ld):
        pass

    async def reveal_developer_mode_option_in_ui(self):
        type(self).calls += 1
        if self.error:
            raise self.error


@pytest.fixture
def phone(monkeypatch):
    async def create(**kwargs):
        return _Lockdown()

    monkeypatch.setattr(lockdown, "create_using_usbmux", create)
    _Amfi.calls, _Amfi.error = 0, None
    monkeypatch.setattr(amfi, "AmfiService", _Amfi)


def _run(fix):
    r = cli._Report()
    usb = [type("Mux", (), {"serial": "x"})()]
    return asyncio.run(cli._usb_checks(r, usb, fix)), r


def test_fix_shows_the_switch(phone, capsys):
    go_on, r = _run(fix=True)
    assert (go_on, r.failed, _Amfi.calls) == (False, True, 1)
    out = capsys.readouterr().out
    assert "its switch is now shown" in out
    assert "Settings > Privacy & Security > Developer Mode" in out


def test_fix_reports_a_failed_reveal(phone, capsys):
    _Amfi.error = PyMobileDevice3Exception("no success")
    go_on, r = _run(fix=True)
    assert (go_on, r.failed) == (False, True)
    out = capsys.readouterr().out
    assert "showing its switch failed" in out and "no success" in out
    assert "doctor --fix" in out


def test_without_fix_points_to_fix(phone, capsys):
    go_on, r = _run(fix=False)
    assert (go_on, r.failed, _Amfi.calls) == (False, True, 0)
    out = capsys.readouterr().out
    assert "phonectl doctor --fix" in out
    assert "Xcode" not in out


class _Mounter:
    mounted = False

    def __init__(self, lockdown):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def is_image_mounted(self, image_type):
        return self.mounted


@pytest.fixture
def developer_mode_on(phone, monkeypatch):
    from pymobiledevice3.services import mobile_image_mounter

    async def on(self):
        return True

    monkeypatch.setattr(_Lockdown, "get_developer_mode_status", on)
    monkeypatch.setattr(mobile_image_mounter, "MobileImageMounterService", _Mounter)


@pytest.mark.parametrize("mounted, expected", [
    (True, "developer disk image is installed"),
    (False, "the server installs it below"),
])
def test_disk_image_is_reported_and_left_to_the_server(developer_mode_on, capsys, mounted, expected):
    _Mounter.mounted = mounted
    go_on, r = _run(fix=True)
    assert (go_on, r.failed) == (True, False)
    assert expected in capsys.readouterr().out
