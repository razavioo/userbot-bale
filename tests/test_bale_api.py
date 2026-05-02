"""Unit tests for the Bale API layer (offline-only — no server hit)."""

from __future__ import annotations

import pytest

from baleobala.bale.api import BaleApiClient, LiveKitCredentials
from baleobala.bale.endpoints import Endpoint
from baleobala.bale.messaging_backend import MessagingBackend
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


def test_parse_call_credentials_extracts_peer_id_when_present() -> None:
    blob = (
        b"\x0a\x06\x08\x01\x10\xb9\x60 "
        b"wss://meet-gwe.ble.ir "
        b"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9." + b"A" * 200 + b".XYZ "
        b"c9432d6c-12c6-4b8f-8f75-4150495dc055"
    )
    c = parse_call_credentials(blob)
    assert c is not None
    assert c.peer_id == 12345


def test_parse_call_credentials_returns_none_without_match() -> None:
    assert parse_call_credentials(b"just some random bytes") is None


def test_rid_dedup_evicts_after_cap_and_is_thread_safe() -> None:
    """Regression: RID dedup buffer used to be cap=1024 with no lock.
    Confirm the cap evicts oldest, lookups remain consistent under
    concurrent writers, and the lock prevents set/list desync."""
    import threading

    client = BaleApiClient.__new__(BaleApiClient)
    client._seen_rids = set()
    client._seen_rids_order = []
    client._seen_rids_lock = threading.Lock()

    # Hammer with 5000 unique RIDs from 8 threads.
    def worker(start: int) -> None:
        for i in range(start, start + 625):
            client._remember_rid(i)

    threads = [threading.Thread(target=worker, args=(i * 625,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Cap is 1024; both views must agree on size.
    assert len(client._seen_rids) == 1024
    assert len(client._seen_rids_order) == 1024
    assert set(client._seen_rids_order) == client._seen_rids
    # The earliest RIDs (0..k) must have been evicted.
    assert 0 not in client._seen_rids


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
        pytest.skip("no network to ep.bale.ai")
    client = BaleApiClient()
    try:
        eps = client.bootstrap()
    except OSError as exc:
        pytest.skip(f"endpoint bootstrap unavailable: {exc}")
    assert len(eps) >= 1
    assert all(e.host.endswith(".bale.ai") for e in eps)


def test_api_client_bootstrap_caches_endpoint_fetch(monkeypatch) -> None:
    expected = [
        Endpoint(
            scheme="tls",
            pin="a" * 64,
            host="rpc-ssl-c002.bale.ai",
            ip="2.189.68.117",
            port=443,
            id=1013,
        )
    ]
    calls = {"count": 0}

    def fake_fetch_endpoints() -> list[Endpoint]:
        calls["count"] += 1
        return expected

    monkeypatch.setattr("baleobala.bale.api.fetch_endpoints", fake_fetch_endpoints)
    client = BaleApiClient()

    first = client.bootstrap()
    second = client.bootstrap()

    assert first == expected
    assert second is first
    assert calls["count"] == 1


def test_bale_api_client_satisfies_messaging_backend_protocol() -> None:
    client = BaleApiClient(jwt="token")
    assert isinstance(client, MessagingBackend)
