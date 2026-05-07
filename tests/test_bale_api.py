"""Unit tests for the Bale API layer (offline-only — no server hit)."""

from __future__ import annotations

import pytest

from baleobala.bale.api import BaleApiClient, LiveKitCredentials
from baleobala.bale.endpoints import Endpoint
from baleobala.bale.messaging_backend import MessagingBackend
from baleobala.bale.protos import (
    CallCredentials, OutPeer, RequestStartLiveKitCall, parse_call_credentials,
    parse_incoming_call_offer,
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


def test_parse_call_credentials_picks_field3_room_over_earlier_uuid() -> None:
    """Regression: StartCall and AcceptCall responses have different protobuf
    field ordering, causing the generic first-UUID regex to pick a wrong UUID
    (e.g. a call/session ID that appears before the actual room UUID in one of
    the responses). The field-3 anchor (0x1a 0x24) must take priority.

    Simulates the layout observed in AcceptCall responses where an earlier UUID
    (some call-ID or outer session ID) appears before the room field."""
    CALL_ID_UUID   = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"  # appears first
    REAL_ROOM_UUID = "11111111-2222-3333-4444-555555555555"  # the actual room

    # Build a payload that has the call-id UUID earlier than the room,
    # and the room anchored with the field-3 tag (0x1a 0x24).
    room_bytes = REAL_ROOM_UUID.encode("ascii")
    assert len(room_bytes) == 36
    blob = (
        b"\x0a\x28" + CALL_ID_UUID.encode("ascii") +   # outer field 1, length 40 (UUID + extras) — just noise
        b" wss://meet-gwe.ble.ir "
        b"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9." + b"A" * 200 + b".XYZ "
        b"\x1a\x24" + room_bytes +                      # field 3, length 36 = room
        b" tail"
    )
    c = parse_call_credentials(blob)
    assert c is not None
    assert c.room == REAL_ROOM_UUID, (
        f"Expected room {REAL_ROOM_UUID!r} via field-3 anchor, got {c.room!r}"
    )


def test_parse_incoming_call_offer_with_two_byte_varint_length() -> None:
    """Regression: StartCall inline responses use 2-byte varint lengths for the
    outer field (e.g. 0xfe 0x04 = 638).  parse_incoming_call_offer previously
    read only one byte and computed the wrong inner_end, so the containment
    check always failed and it returned None — leaving the self-accept echo
    unsuppressed and causing the relay to terminate its own heartbeat LiveKit
    rooms."""
    ROOM = "c9432d6c-12c6-4b8f-8f75-4150495dc055"
    CALL_ID_VARINT = b"\xb9\x60"   # varint for 12345

    # Build inner payload: \x08 <callId varint> \x1a\x24 <room> ... wss URL
    inner = (
        b"\x08" + CALL_ID_VARINT
        + b"\x1a\x24" + ROOM.encode()
        + b" wss://meet-gwe.ble.ir"
    )
    # Encode outer field 1 with a 2-byte varint length (>= 128 bytes)
    # Pad inner so length > 127 to force a 2-byte varint.
    inner += b"\x00" * (130 - len(inner))
    assert len(inner) >= 128
    # 2-byte varint: encode len(inner)
    n = len(inner)
    length_varint = bytes([(n & 0x7F) | 0x80, (n >> 7) & 0x7F])
    buf = b"\x0a" + length_varint + inner

    call_id = parse_incoming_call_offer(buf)
    assert call_id == 12345, f"Expected 12345, got {call_id!r}"


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
