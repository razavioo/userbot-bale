"""
Unit tests for the newly added Bale proto encoders/decoders.

Covers: RequestReceiveCall, RequestGetWssURL, RequestJoinGroupCall,
RequestLeaveGroupCall, RequestLoadHistory, RequestLoadDialogs,
RequestMessageRead, and their response parsers.

Field numbers verified against the APK decompile at
re/jadx-out/sources/ai/bale/proto/MeetOuterClass$*.java and
MessagingOuterClass$*.java. No live server hit required.
"""

from __future__ import annotations

import pytest

from baleobala.bale.protos import (
    DialogInfo,
    HistoryMessage,
    OutPeer,
    RequestGetWssURL,
    RequestJoinGroupCall,
    RequestLeaveGroupCall,
    RequestLoadDialogs,
    RequestLoadHistory,
    RequestMessageRead,
    RequestReceiveCall,
    _encode_message_with_text,
    parse_get_wss_url_response,
    parse_load_dialogs_response,
    parse_load_history_response,
)
from baleobala.bale.rpc_envelope import _dec_tag, _dec_varint, _enc_len_delim, _enc_tag, _enc_varint


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _decode_varint_at(buf: bytes, pos: int) -> tuple[int, int]:
    return _dec_varint(buf, pos)


def _first_varint_field(buf: bytes, field_num: int) -> int | None:
    """Return the varint value of the first occurrence of field_num in buf."""
    pos = 0
    while pos < len(buf):
        fn, wt, pos = _dec_tag(buf, pos)
        if wt == 0:
            v, pos = _dec_varint(buf, pos)
            if fn == field_num:
                return v
        elif wt == 2:
            ln, pos = _dec_varint(buf, pos)
            pos += ln
        else:
            break
    return None


def _first_len_delim_field(buf: bytes, field_num: int) -> bytes | None:
    """Return the bytes of the first len-delim occurrence of field_num."""
    pos = 0
    while pos < len(buf):
        fn, wt, pos = _dec_tag(buf, pos)
        if wt == 2:
            # After _dec_varint, pos points to the START of the data.
            ln, pos = _dec_varint(buf, pos)
            data = buf[pos:pos + ln]
            pos += ln
            if fn == field_num:
                return data
        elif wt == 0:
            _, pos = _dec_varint(buf, pos)
        else:
            break
    return None


# ─────────────────────────────────────────────────────────────────────────────
# RequestReceiveCall
# ─────────────────────────────────────────────────────────────────────────────

def test_receive_call_encodes_call_id():
    buf = RequestReceiveCall(call_id=12345).encode()
    assert _first_varint_field(buf, 1) == 12345


def test_receive_call_encodes_zero_call_id():
    buf = RequestReceiveCall(call_id=0).encode()
    # field 1 varint 0
    assert buf == b"\x08\x00"


def test_receive_call_large_call_id():
    call_id = (1 << 50) - 1
    buf = RequestReceiveCall(call_id=call_id).encode()
    assert _first_varint_field(buf, 1) == call_id


# ─────────────────────────────────────────────────────────────────────────────
# RequestGetWssURL + parse_get_wss_url_response
# ─────────────────────────────────────────────────────────────────────────────

def test_get_wss_url_encodes_call_id():
    buf = RequestGetWssURL(call_id=99999).encode()
    assert _first_varint_field(buf, 1) == 99999


def test_get_wss_url_same_wire_as_receive_call_for_same_id():
    # Both are { field 1: callId } — same wire encoding.
    cid = 42
    assert RequestGetWssURL(call_id=cid).encode() == RequestReceiveCall(call_id=cid).encode()


def test_parse_get_wss_url_response_field1_string():
    url = "wss://meet-gwe.ble.ir"
    buf = _enc_len_delim(1, url.encode("utf-8"))
    assert parse_get_wss_url_response(buf) == url


def test_parse_get_wss_url_response_regex_fallback():
    buf = b"junk prefix wss://meet-gwe.ble.ir more stuff"
    assert parse_get_wss_url_response(buf) == "wss://meet-gwe.ble.ir"


def test_parse_get_wss_url_response_empty():
    assert parse_get_wss_url_response(b"") is None


def test_parse_get_wss_url_response_no_url():
    buf = _enc_len_delim(1, b"not a url")
    assert parse_get_wss_url_response(buf) is None


def test_parse_get_wss_url_response_prefers_field1_over_later_url():
    # Embed a second wss:// URL in a different field — parser should return
    # the one at field 1 first.
    url1 = "wss://meet-001.ble.ir"
    url2 = "wss://meet-002.ble.ir"
    buf = _enc_len_delim(1, url1.encode()) + _enc_len_delim(2, url2.encode())
    assert parse_get_wss_url_response(buf) == url1


# ─────────────────────────────────────────────────────────────────────────────
# RequestJoinGroupCall
# ─────────────────────────────────────────────────────────────────────────────

def test_join_group_call_encodes_call_id():
    buf = RequestJoinGroupCall(call_id=77777).encode()
    assert _first_varint_field(buf, 1) == 77777


def test_join_group_call_omits_name_field_when_empty():
    buf = RequestJoinGroupCall(call_id=1).encode()
    # Only field 1 present — no field 2.
    assert _first_len_delim_field(buf, 2) is None


def test_join_group_call_encodes_name_as_string_value():
    name = "alice"
    buf = RequestJoinGroupCall(call_id=1, name=name).encode()
    # Field 2 is StringValue { field 1: name }
    sv_bytes = _first_len_delim_field(buf, 2)
    assert sv_bytes is not None
    # The StringValue inner bytes should contain the name at field 1.
    inner = _first_len_delim_field(sv_bytes, 1)
    assert inner == name.encode("utf-8")


def test_join_group_call_call_id_and_name_both_present():
    buf = RequestJoinGroupCall(call_id=42, name="bob").encode()
    assert _first_varint_field(buf, 1) == 42
    assert b"bob" in buf


# ─────────────────────────────────────────────────────────────────────────────
# RequestLeaveGroupCall
# ─────────────────────────────────────────────────────────────────────────────

def test_leave_group_call_encodes_call_id():
    buf = RequestLeaveGroupCall(call_id=55555).encode()
    assert _first_varint_field(buf, 1) == 55555


def test_leave_group_call_end_false_omits_field2():
    buf = RequestLeaveGroupCall(call_id=1, end=False).encode()
    assert _first_varint_field(buf, 2) is None


def test_leave_group_call_end_true_sets_field2():
    buf = RequestLeaveGroupCall(call_id=1, end=True).encode()
    assert _first_varint_field(buf, 2) == 1


def test_leave_group_call_default_end_is_false():
    buf = RequestLeaveGroupCall(call_id=1).encode()
    assert _first_varint_field(buf, 2) is None


# ─────────────────────────────────────────────────────────────────────────────
# RequestLoadHistory + parse_load_history_response
# ─────────────────────────────────────────────────────────────────────────────

_PEER = OutPeer(user_id=460260975, type=1)


def test_load_history_encodes_peer():
    buf = RequestLoadHistory(peer=_PEER).encode()
    peer_bytes = _first_len_delim_field(buf, 1)
    assert peer_bytes is not None
    assert peer_bytes == _PEER.encode()


def test_load_history_default_date_omitted():
    buf = RequestLoadHistory(peer=_PEER).encode()
    # field 2 = date; default 0 means omitted
    assert _first_varint_field(buf, 2) is None


def test_load_history_date_included_when_nonzero():
    buf = RequestLoadHistory(peer=_PEER, date=1_700_000_000).encode()
    assert _first_varint_field(buf, 2) == 1_700_000_000


def test_load_history_default_load_mode_omitted():
    buf = RequestLoadHistory(peer=_PEER).encode()
    assert _first_varint_field(buf, 4) is None


def test_load_history_load_mode_included_when_nonzero():
    buf = RequestLoadHistory(peer=_PEER, load_mode=2).encode()
    assert _first_varint_field(buf, 4) == 2


def test_load_history_limit_encoded():
    buf = RequestLoadHistory(peer=_PEER, limit=10).encode()
    assert _first_varint_field(buf, 5) == 10


def test_parse_load_history_response_empty():
    assert parse_load_history_response(b"") == []


def _build_fake_history_message(*, rid: int, sender_uid: int, date: int, text: str) -> bytes:
    """Construct a fake history-message blob matching the expected field layout:
    sender_uid=2, date=3, rid=4, message-body=5."""
    msg_bytes = _encode_message_with_text(text)
    body = bytearray()
    body += _enc_tag(2, 0) + _enc_varint(sender_uid)
    body += _enc_tag(3, 0) + _enc_varint(date)
    body += _enc_tag(4, 0) + _enc_varint(rid)
    body += _enc_len_delim(5, msg_bytes)
    return bytes(body)


def test_parse_load_history_response_single_message():
    msg = _build_fake_history_message(rid=101, sender_uid=200, date=1700, text="hello")
    buf = _enc_len_delim(1, msg)  # wrapped in an outer field 1
    results = parse_load_history_response(buf)
    assert len(results) == 1
    assert results[0] == HistoryMessage(rid=101, sender_uid=200, date=1700, text="hello")


def test_parse_load_history_response_multiple_messages():
    msgs = [
        _build_fake_history_message(rid=i, sender_uid=10, date=i * 100, text=f"msg{i}")
        for i in range(1, 4)
    ]
    buf = b"".join(_enc_len_delim(1, m) for m in msgs)
    results = parse_load_history_response(buf)
    assert len(results) == 3
    texts = {r.text for r in results}
    assert texts == {"msg1", "msg2", "msg3"}


def test_parse_load_history_response_ignores_non_message_blobs():
    # A sub-message without rid + text_message should be silently skipped.
    noise = _enc_tag(1, 0) + _enc_varint(42)  # just a varint field, not a message
    msg = _build_fake_history_message(rid=5, sender_uid=9, date=9000, text="keep")
    buf = _enc_len_delim(99, noise) + _enc_len_delim(1, msg)
    results = parse_load_history_response(buf)
    assert any(r.text == "keep" for r in results)


def test_parse_load_history_response_descends_one_envelope_level():
    """Server may wrap the message list in an outer envelope field."""
    inner_msg = _build_fake_history_message(rid=7, sender_uid=3, date=500, text="deep")
    # Two levels: outer field 2 → inner field 1 → message
    buf = _enc_len_delim(2, _enc_len_delim(1, inner_msg))
    results = parse_load_history_response(buf)
    assert any(r.text == "deep" for r in results)


# ─────────────────────────────────────────────────────────────────────────────
# RequestLoadDialogs + parse_load_dialogs_response
# ─────────────────────────────────────────────────────────────────────────────

def test_load_dialogs_default_min_date_omitted():
    buf = RequestLoadDialogs().encode()
    assert _first_varint_field(buf, 1) is None


def test_load_dialogs_min_date_included_when_nonzero():
    buf = RequestLoadDialogs(min_date=123456).encode()
    assert _first_varint_field(buf, 1) == 123456


def test_load_dialogs_limit_encoded():
    buf = RequestLoadDialogs(limit=5).encode()
    assert _first_varint_field(buf, 2) == 5


def test_parse_load_dialogs_response_empty():
    assert parse_load_dialogs_response(b"") == []


def _build_fake_dialog(*, peer_id: int, peer_type: int = 1, unread: int = 0, date: int = 0) -> bytes:
    """Dialog: field 1=OutPeer, field 3=unreadCount, field 5=date."""
    peer_bytes = OutPeer(user_id=peer_id, type=peer_type).encode()
    body = bytearray()
    body += _enc_len_delim(1, peer_bytes)
    if unread:
        body += _enc_tag(3, 0) + _enc_varint(unread)
    if date:
        body += _enc_tag(5, 0) + _enc_varint(date)
    return bytes(body)


def test_parse_load_dialogs_response_single_dialog():
    dlg = _build_fake_dialog(peer_id=12345, unread=3, date=9000)
    buf = _enc_len_delim(1, dlg)
    results = parse_load_dialogs_response(buf)
    assert len(results) == 1
    assert results[0].peer_id == 12345
    assert results[0].unread_count == 3
    assert results[0].last_message_date == 9000


def test_parse_load_dialogs_response_multiple_dialogs():
    dlgs = [_build_fake_dialog(peer_id=i * 1000) for i in range(1, 4)]
    buf = b"".join(_enc_len_delim(1, d) for d in dlgs)
    results = parse_load_dialogs_response(buf)
    peer_ids = {r.peer_id for r in results}
    assert {1000, 2000, 3000}.issubset(peer_ids)


def test_parse_load_dialogs_response_skips_zero_peer_id():
    # A sub-message with no OutPeer should not appear in results.
    noise = _enc_tag(3, 0) + _enc_varint(5)  # just unreadCount, no peer
    dlg = _build_fake_dialog(peer_id=777)
    buf = _enc_len_delim(1, noise) + _enc_len_delim(1, dlg)
    results = parse_load_dialogs_response(buf)
    assert all(r.peer_id != 0 for r in results)
    assert any(r.peer_id == 777 for r in results)


def test_parse_load_dialogs_response_descends_one_envelope_level():
    inner_dlg = _build_fake_dialog(peer_id=99999, unread=1)
    buf = _enc_len_delim(2, _enc_len_delim(1, inner_dlg))
    results = parse_load_dialogs_response(buf)
    assert any(r.peer_id == 99999 for r in results)


# ─────────────────────────────────────────────────────────────────────────────
# RequestMessageRead
# ─────────────────────────────────────────────────────────────────────────────

def test_message_read_encodes_peer():
    buf = RequestMessageRead(peer=_PEER, date=5000).encode()
    peer_bytes = _first_len_delim_field(buf, 1)
    assert peer_bytes == _PEER.encode()


def test_message_read_encodes_date():
    buf = RequestMessageRead(peer=_PEER, date=1_700_000_000).encode()
    assert _first_varint_field(buf, 2) == 1_700_000_000


def test_message_read_peer_precedes_date():
    buf = RequestMessageRead(peer=_PEER, date=42).encode()
    # Field 1 (peer, len-delim) must appear before field 2 (date, varint).
    pos_peer = buf.index(_PEER.encode())
    # Find where the date varint is (tag for field 2 varint = 0x10)
    pos_date_tag = buf.index(b"\x10")
    assert pos_peer < pos_date_tag


def test_message_read_zero_date_still_encoded():
    # date=0 is a valid timestamp (epoch); must always be emitted.
    buf = RequestMessageRead(peer=_PEER, date=0).encode()
    assert _first_varint_field(buf, 2) == 0
