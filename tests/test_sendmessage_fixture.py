"""
Verify RequestSendMessage encoder against live captured bytes.

Captured 2026-04-20 via mitmproxy intercepting web.bale.ai sending
text messages. Fixture stores the full WS frames; we parse them,
extract the RequestSendMessage body, and check field layout.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from userbot_bale.bale.protos import (
    OutPeer,
    RequestSendMessage,
    _encode_message_with_text,
)
from userbot_bale.bale.rpc_envelope import _dec_tag, _dec_varint

FIXTURE = Path(__file__).parent / "fixtures" / "sendmessage_frames.json"


def _walk(buf: bytes):
    """Yield (field_number, raw_bytes_or_int, wire_type) for buf."""
    pos = 0
    while pos < len(buf):
        fn, wt, pos = _dec_tag(buf, pos)
        if wt == 0:
            v, pos = _dec_varint(buf, pos)
            yield fn, v, wt
        elif wt == 2:
            ln, pos = _dec_varint(buf, pos)
            yield fn, buf[pos:pos + ln], wt
            pos += ln
        elif wt == 5:
            pos += 4
        elif wt == 1:
            pos += 8
        else:
            return


def _extract_payload(frame: bytes) -> bytes:
    """Outer: tag 1 len-delim wrapping {service, method, payload(3), metadata, seq}."""
    for fn, val, wt in _walk(frame):
        if fn == 1 and wt == 2:
            for ifn, ival, iwt in _walk(val):
                if ifn == 3 and iwt == 2:
                    return ival
    raise AssertionError("no payload field found in RPC frame")


@pytest.fixture
def captured_sends():
    if not FIXTURE.exists():
        pytest.skip("no live capture fixture (run mitmproxy to regenerate)")
    data = json.loads(FIXTURE.read_text())
    sends = [bytes.fromhex(e["hex"]) for e in data if e["from_client"]]
    assert sends, "fixture contains no outbound SendMessage frames"
    return sends


def test_captured_request_has_expected_fields(captured_sends):
    payload = _extract_payload(captured_sends[0])
    fields = {fn: (val, wt) for fn, val, wt in _walk(payload)}
    # Must have peer (1), rid (2), message (3), ex_peer (6).
    assert 1 in fields and fields[1][1] == 2  # OutPeer
    assert 2 in fields and fields[2][1] == 0  # rid varint
    assert 3 in fields and fields[3][1] == 2  # message
    assert 6 in fields and fields[6][1] == 2  # ex_peer (confirms web client sets it)


def test_captured_message_uses_text_message_tag_15(captured_sends):
    payload = _extract_payload(captured_sends[0])
    message_bytes = dict(((fn, val) for fn, val, _ in _walk(payload)))[3]
    # Message → text_message at tag 15 (wire byte 0x7a = (15<<3)|2).
    tags = {fn for fn, _, _ in _walk(message_bytes)}
    assert 15 in tags, f"expected text_message at tag 15, got tags={tags}"


def test_encoder_byte_layout_matches_capture_shape(captured_sends):
    """Encode our own RequestSendMessage with the captured rid + text
    and compare the resulting field tree to the captured one."""
    captured_payload = _extract_payload(captured_sends[0])
    captured = dict(((fn, val) for fn, val, _ in _walk(captured_payload)))

    # Reconstruct our encoder's output with the same inputs.
    peer_bytes = captured[1]
    rid = captured[2]  # varint raw value
    message_bytes = captured[3]
    # Extract text from captured message.
    text_message = dict(((fn, val) for fn, val, _ in _walk(message_bytes)))[15]
    text = dict(((fn, val) for fn, val, _ in _walk(text_message)))[1].decode("utf-8")
    peer_type = dict(((fn, val) for fn, val, _ in _walk(peer_bytes)))[1]
    user_id = dict(((fn, val) for fn, val, _ in _walk(peer_bytes)))[2]

    req = RequestSendMessage(
        peer=OutPeer(user_id=user_id, type=peer_type),
        text=text,
        rid=rid,
    )
    ours = req.encode()
    ours_fields = dict(((fn, val) for fn, val, _ in _walk(ours)))

    # Field-by-field equivalence (not byte-identical because we don't
    # emit the empty mentions(tag 2) sub-field inside TextMessage, nor
    # necessarily in the same order — but field values must match).
    assert ours_fields[1] == captured[1], "peer bytes differ"
    assert ours_fields[2] == captured[2], "rid differs"
    # Message equality modulo empty mentions: we emit only tag 1 (text).
    our_msg = ours_fields[3]
    our_textmsg = dict(((fn, val) for fn, val, _ in _walk(our_msg)))[15]
    our_text = dict(((fn, val) for fn, val, _ in _walk(our_textmsg)))[1]
    assert our_text.decode("utf-8") == text
    assert ours_fields[6] == captured[1], "ex_peer should equal peer"
