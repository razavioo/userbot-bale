from __future__ import annotations

import pytest

from userbot_bale.vpn import tun


def test_tun_device_reports_an_unsupported_platform_without_import_failure(monkeypatch) -> None:
    monkeypatch.setattr(tun, "fcntl", None)

    with pytest.raises(RuntimeError, match="only on Linux"):
        tun.TunDevice.open("vpn0")
