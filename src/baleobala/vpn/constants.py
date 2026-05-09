"""Cross-cutting VPN tunnel constants.

These values are part of the wire contract between the Python relay and
the Android client (and any future client). Changing them in only one
place silently breaks the tunnel — see commit cf66326, where per-peer
sess_id offsets caused every Android frame to be silently dropped at
the relay's sess_id filter.

Keep this module dependency-free so it can be imported from anywhere.
"""

from __future__ import annotations

from typing import Final

# Tunnel framing session id used by both the relay and the Android client.
# Mirrored in native/android/.../BaleVpnService.kt as
# `DEFAULT_TUNNEL_SESS_ID = 0x1111`. tests/vpn/test_constants.py asserts
# the Kotlin source still matches; do not change one without the other.
DEFAULT_TUNNEL_SESS_ID: Final[int] = 0x1111
