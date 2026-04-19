"""Unit tests for the Bale API layer (offline-only — no server hit)."""

from __future__ import annotations

from baleobala.bale.api import BaleApiClient, LiveKitCredentials
from baleobala.bale.protos import (
    CallCredentials, OutPeer, RequestStartLiveKitCall, parse_call_credentials,
)


def test_livekit_credentials_shape() -> None:
    c = LiveKitCredentials(url="wss://x", token="t", room="r", identity="i")
    assert c.url == "wss://x"


def test_outpeer_encode() -> None:
    peer = OutPeer(user_id=460260975, type=1)
    # Matches the capture bytes (0a 08 prefix added by the wrapping
    # RequestStartLiveKitCall encode; here we just verify the inner).
    assert peer.encode().hex() == "080110ef8cbcdb01"


def test_start_livekit_call_matches_capture() -> None:
    """Byte-identical to the captured StartCall payload from the web
    client (captures/ws-live/00167_cl_*.bin)."""
    req = RequestStartLiveKitCall(
        peer=OutPeer(user_id=460260975, type=1),
        rid=431786129,
        video=False,
        invite_enable=True,
    )
    expected = bytes.fromhex(
        "32140a08080110ef8cbcdb01109191f2cd0122020801"
    )
    assert req.encode_as_rpc_payload() == expected


def test_parse_call_credentials_from_capture_bytes() -> None:
    # Minimal fixture containing a wss URL + JWT + room uuid — the
    # parser scans byte-wise so we don't need a real push frame.
    blob = (
        b"foo wss://meet-gwe.ble.ir bar "
        b"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9." + b"A" * 200 + b".XYZ "
        b"c9432d6c-12c6-4b8f-8f75-4150495dc055 tail"
    )
    c = parse_call_credentials(blob)
    assert c is not None
    assert c.url == "wss://meet-gwe.ble.ir"
    assert c.token.startswith("eyJhbGci")
    assert c.room == "c9432d6c-12c6-4b8f-8f75-4150495dc055"


def test_parse_call_credentials_returns_none_without_match() -> None:
    assert parse_call_credentials(b"just some random bytes") is None


def test_api_client_requires_jwt() -> None:
    client = BaleApiClient(jwt=None)
    try:
        client.start()
    except RuntimeError as e:
        assert "JWT" in str(e)
        return
    raise AssertionError("expected RuntimeError")


def test_api_client_bootstraps_endpoints() -> None:
    """Endpoint fetch still works regardless of WS auth state."""
    # This hits the live network; skip if unreachable.
    import socket
    try:
        socket.create_connection(("ep.bale.ai", 80), timeout=3).close()
    except OSError:
        import pytest
        pytest.skip("no network to ep.bale.ai")
    client = BaleApiClient()
    eps = client.bootstrap()
    assert len(eps) >= 1
    assert all(e.host.endswith(".bale.ai") for e in eps)
