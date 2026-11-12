import asyncio
import threading

import pytest
from pymobiledevice3.exceptions import AlreadyMountedError

from phonectl import device


class _Mounter:
    IMAGE_TYPE = "Personalized"
    mounted = False
    error = None
    mounts = []

    def __init__(self, lockdown):
        pass

    async def __aenter__(self):
        if self.error:
            raise self.error
        return self

    async def __aexit__(self, *exc):
        return False

    async def is_image_mounted(self, image_type):
        assert image_type == "Personalized"
        return self.mounted

    async def mount(self, *files):
        self.mounts.append(files)


@pytest.fixture
def phone(monkeypatch):
    """An iOS 27 phone without the image; records the download thread and the install."""
    seen = {"fetch_thread": None, "installed": [], "fetch_error": None, "install_error": None}

    def fetch():
        seen["fetch_thread"] = threading.current_thread()
        if seen["fetch_error"]:
            raise seen["fetch_error"]
        return "restore-dir"

    class _Cryptexd:
        def __init__(self, rsd):
            pass

        async def auto_install_ddi(self, restore_dir=None):
            if seen["install_error"]:
                raise seen["install_error"]
            seen["installed"].append(restore_dir)

    _Mounter.mounted, _Mounter.error, _Mounter.mounts = False, None, []
    monkeypatch.setattr(device, "PersonalizedImageMounter", _Mounter)
    monkeypatch.setattr(device, "uses_cryptex_image", lambda rsd: True)
    monkeypatch.setattr(device, "fetch_cryptex_ddi", fetch)
    monkeypatch.setattr(device, "CryptexdService", _Cryptexd)
    return seen


def _ensure():
    asyncio.run(device._ensure_disk_image("rsd"))


def test_installed_image_is_left_alone(phone):
    _Mounter.mounted = True
    _ensure()
    assert phone["fetch_thread"] is None and phone["installed"] == []


def test_missing_image_is_downloaded_off_the_event_loop_then_installed_from_it(phone):
    _ensure()
    assert phone["fetch_thread"] not in (None, threading.main_thread())
    assert phone["installed"] == ["restore-dir"]


def test_before_ios_27_the_downloaded_image_is_mounted(phone, monkeypatch):
    monkeypatch.setattr(device, "uses_cryptex_image", lambda rsd: False)
    monkeypatch.setattr(device, "fetch_personalized_ddi", lambda: ("dmg", "manifest", "trustcache"))
    _ensure()
    assert _Mounter.mounts == [("dmg", "manifest", "trustcache")]


def test_failed_check_does_not_block_connecting(phone):
    _Mounter.error = OSError("mounter gone")
    _ensure()
    assert phone["fetch_thread"] is None and phone["installed"] == []


def test_failed_download_names_the_network(phone):
    phone["fetch_error"] = OSError("GitHub unreachable")
    with pytest.raises(device.DiskImageDownloadError, match="network or proxy"):
        _ensure()
    assert phone["installed"] == []


def test_image_already_installed_as_a_cryptex_is_fine(phone):
    phone["install_error"] = AlreadyMountedError("cryptex installed")
    _ensure()
    assert phone["installed"] == []


def test_link_error_while_installing_passes_through(phone):
    phone["install_error"] = ConnectionResetError("tunnel dropped")
    with pytest.raises(ConnectionResetError):
        _ensure()


def test_connect_installs_the_image_before_dvt(monkeypatch):
    order = []

    class _Tunnel:
        def __init__(self, serial):
            pass

        async def aopen(self):
            return "rsd"

        async def aclose(self):
            pass

    async def ensure(rsd):
        order.append("image")

    class _Dvt:
        def __init__(self, *args):
            order.append("dvt")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(device.native_tunnel, "NativeRemotedTunnel", _Tunnel)
    monkeypatch.setattr(device, "_ensure_disk_image", ensure)
    monkeypatch.setattr(device, "DvtProvider", _Dvt)
    monkeypatch.setattr(device, "Screenshot", _Dvt)

    async def run():
        d = device.Device()
        await d.connect()
        await d.close()

    asyncio.run(run())
    assert order[:2] == ["image", "dvt"]


def test_failed_install_closes_the_tunnel(monkeypatch):
    closed = []

    class _Tunnel:
        def __init__(self, serial):
            pass

        async def aopen(self):
            return "rsd"

        async def aclose(self):
            closed.append(True)

    async def ensure(rsd):
        raise ConnectionResetError("tunnel dropped")

    monkeypatch.setattr(device.native_tunnel, "NativeRemotedTunnel", _Tunnel)
    monkeypatch.setattr(device, "_ensure_disk_image", ensure)
    with pytest.raises(ConnectionResetError):
        asyncio.run(device.Device().connect())
    assert closed == [True]


@pytest.mark.parametrize("error, tries", [
    (device.DiskImageDownloadError("GitHub unreachable"), 1),
    (ConnectionResetError("tunnel dropped"), 2),
])
def test_reconnect_does_not_download_twice(monkeypatch, error, tries):
    calls = []

    async def connect(self):
        calls.append(True)
        raise error

    monkeypatch.setattr(device.Device, "connect", connect)
    monkeypatch.setattr(device, "RECONNECT_BACKOFF_S", 0)
    with pytest.raises(type(error)):
        asyncio.run(device.Device()._reconnect())
    assert len(calls) == tries
