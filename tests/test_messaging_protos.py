"""
Unit tests for RequestSendMessage encoding and UpdateMessage-shaped
payload extraction. Wire layout verified against the decompiled
protos at re/jadx-out/.../MessagingOuterClass$*.java — not yet
against a live capture, so this will also be the fixture to refine
once someone sniffs a real SendMessage round-trip.
"""

from __future__ import annotations

from userbot_bale.bale.protos import (
    InboundMessage,
    OutPeer,
    RequestSendMessage,
    _encode_message_with_text,
    _parse_update_message,
    find_inbound_messages,
)
from userbot_bale.bale.rpc_envelope import _enc_len_delim, _enc_tag, _enc_varint


def _build_fake_update_message(
    *, peer_id: int, sender_uid: int, rid: int, text: str
) -> bytes:
    """Construct bytes that match UpdateMessage's field layout so the
    parser can find them (peer=1, sender_uid=2, rid=4, message=5)."""
    peer_bytes = OutPeer(user_id=peer_id, type=1).encode()
    msg_bytes = _encode_message_with_text(text)
    body = bytearray()
    body += _enc_len_delim(1, peer_bytes)
    body += _enc_tag(2, 0) + _enc_varint(sender_uid)
    body += _enc_tag(4, 0) + _enc_varint(rid)
    body += _enc_len_delim(5, msg_bytes)
    return bytes(body)


def test_request_send_message_encodes_expected_structure():
    req = RequestSendMessage(
        peer=OutPeer(user_id=12345, type=1),
        text="hello",
        rid=42,
    )
    buf = req.encode()
    # Must contain the peer, rid, and text bytes somewhere.
    assert b"hello" in buf
    # rid varint encoding of 42 is \x2a
    assert b"\x2a" in buf
    # Peer user_id 12345 as varint = \xb9\x60
    assert b"\xb9\x60" in buf


def test_parse_update_message_roundtrip():
    raw = _build_fake_update_message(
        peer_id=1000, sender_uid=1000, rid=777, text="frame-data"
    )
    m = _parse_update_message(raw)
    assert m == InboundMessage(
        peer_user_id=1000, sender_uid=1000, rid=777, text="frame-data"
    )


def test_find_inbound_messages_descends_into_wrappers():
    inner = _build_fake_update_message(
        peer_id=42, sender_uid=42, rid=1, text="nested"
    )
    # Simulate SeqUpdate / outer envelope at arbitrary tags.
    wrapped = _enc_len_delim(3, _enc_len_delim(7, inner))
    results = find_inbound_messages(wrapped)
    assert len(results) == 1
    assert results[0].text == "nested"
    assert results[0].peer_user_id == 42


def test_find_inbound_messages_deduplicates():
    inner = _build_fake_update_message(
        peer_id=5, sender_uid=5, rid=9, text="dup"
    )
    # Wrap the same inner twice at different depths — recursive walk
    # will hit both but dedup on (rid, peer, text).
    wrapped = _enc_len_delim(1, inner) + _enc_len_delim(2, _enc_len_delim(3, inner))
    results = find_inbound_messages(wrapped)
    assert len(results) == 1


def test_find_inbound_messages_ignores_non_message_bytes():
    # Random garbage should not yield spurious InboundMessages.
    results = find_inbound_messages(b"\x00" * 50 + b"garbage")
    assert results == []
