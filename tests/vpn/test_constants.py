"""Cross-cutting tunnel-constant regression tests.

These guard the wire contract between the Python relay and the Android
client. cf66326 fixed a bug where per-peer sess_id offsets caused the
relay's sess_id filter to silently drop every Android frame; the fix
hardcoded the value to 0x1111 in both languages. These tests prove the
two languages still agree.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from baleobala.vpn.constants import DEFAULT_TUNNEL_SESS_ID


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ANDROID_VPN_SERVICE = (
    PROJECT_ROOT
    / "native"
    / "android"
    / "app"
    / "src"
    / "main"
    / "java"
    / "com"
    / "baleobala"
    / "vpn"
    / "BaleVpnService.kt"
)


def test_default_tunnel_sess_id_value_is_locked():
    """Locks the protocol-level value. Changing this without coordinating
    a release across all clients silently breaks every active tunnel."""
    assert DEFAULT_TUNNEL_SESS_ID == 0x1111


def test_python_relay_uses_the_central_constant():
    """Every Python entry point that builds a tunnel must source its
    sess_id from baleobala.vpn.constants — not a copy of the literal —
    so a future change to the constant propagates everywhere at once."""
    from baleobala.vpn.cli import DEFAULT_TUNNEL_SESS_ID as cli_const
    from baleobala.vpn.mesh.exit_node import DEFAULT_TUNNEL_SESS_ID as mesh_const

    assert cli_const is DEFAULT_TUNNEL_SESS_ID
    assert mesh_const is DEFAULT_TUNNEL_SESS_ID


@pytest.mark.skipif(
    not ANDROID_VPN_SERVICE.exists(),
    reason="Android sources not present in this checkout",
)
def test_android_kotlin_constant_matches_python():
    """The Android client hardcodes its own DEFAULT_TUNNEL_SESS_ID. If
    the Kotlin constant drifts from the Python one, every frame the relay
    sends gets filtered out at the Android side and tunnel_dead fires
    within ~6 seconds of bring-up — the bug fixed in cf66326."""
    src = ANDROID_VPN_SERVICE.read_text()
    match = re.search(
        r"const\s+val\s+DEFAULT_TUNNEL_SESS_ID\s*=\s*(0x[0-9A-Fa-f]+|\d+)",
        src,
    )
    assert match is not None, (
        f"DEFAULT_TUNNEL_SESS_ID not found in {ANDROID_VPN_SERVICE}; the "
        "constant may have been renamed — update this test alongside the "
        "Kotlin source."
    )
    kotlin_value = int(match.group(1), 0)
    assert kotlin_value == DEFAULT_TUNNEL_SESS_ID, (
        f"Kotlin DEFAULT_TUNNEL_SESS_ID={kotlin_value:#x} does not match "
        f"Python DEFAULT_TUNNEL_SESS_ID={DEFAULT_TUNNEL_SESS_ID:#x}; "
        "tunnels will silently drop every frame (cf66326 regression)."
    )
