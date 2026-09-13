"""Unit tests for the framing layer. No audio, no ggwave."""

from __future__ import annotations

import pytest

from userbot_bale.framing import (
    HEADER_SIZE,
    MAGIC,
    MAX_FRAGMENT_PAYLOAD,
    Frame,
    FrameFlag,
    Reassembler,
    fragment,
)


def test_single_frame_roundtrip():
    frames = list(fragment(b"hello", msg_id=42))
    assert len(frames) == 1
    f = frames[0]
    assert FrameFlag.SINGLE in f.flags
    assert f.frag_total == 1
    encoded = f.encode()
    assert encoded[0] == MAGIC
    assert len(encoded) == HEADER_SIZE + len(b"hello")
    decoded = Frame.decode(encoded)
    assert decoded == f


def test_multi_fragment_split_and_reassemble():
    payload = bytes(range(256)) * 3  # 768 bytes → 6 fragments of 132
    frames = list(fragment(payload, msg_id=7))
    assert len(frames) == (len(payload) + MAX_FRAGMENT_PAYLOAD - 1) // MAX_FRAGMENT_PAYLOAD
    assert FrameFlag.START in frames[0].flags
    assert FrameFlag.END in frames[-1].flags

    reasm = Reassembler()
    results = [reasm.push(Frame.decode(f.encode())) for f in frames]
    assert results[:-1] == [None] * (len(frames) - 1)
    assert results[-1] == payload


def test_reassembler_out_of_order():
    payload = b"X" * 400  # 4 fragments
    frames = list(fragment(payload, msg_id=99))
    reasm = Reassembler()
    for f in [frames[2], frames[0], frames[3], frames[1]]:
        result = reasm.push(f)
    assert result == payload


def test_reassembler_duplicate_single():
    """Duplicate delivery of the same single-frame message emits once."""
    (f,) = list(fragment(b"hi", msg_id=1))
    reasm = Reassembler()
    assert reasm.push(f) == b"hi"
    assert reasm.push(f) is None


def test_header_crc_rejects_bit_flips():
    (f,) = list(fragment(b"payload", msg_id=1))
    buf = bytearray(f.encode())
    buf[3] ^= 0x01  # flip a bit in msg_id → header CRC should mismatch
    assert Frame.decode(bytes(buf)) is None


def test_magic_rejects_foreign_payload():
    assert Frame.decode(b"\x00" * 16) is None
    assert Frame.decode(b"") is None
    assert Frame.decode(b"\xBA") is None  # too short


def test_payload_size_limits():
    with pytest.raises(ValueError):
        Frame(
            msg_id=0, frag_idx=0, frag_total=1,
            flags=FrameFlag.SINGLE,
            payload=b"x" * (MAX_FRAGMENT_PAYLOAD + 1),
        ).encode()


def test_fragment_boundary_exact():
    """Message exactly == MAX_FRAGMENT_PAYLOAD should be one SINGLE frame."""
    payload = b"a" * MAX_FRAGMENT_PAYLOAD
    frames = list(fragment(payload, msg_id=0))
    assert len(frames) == 1
    assert FrameFlag.SINGLE in frames[0].flags


def test_fragment_boundary_plus_one():
    payload = b"a" * (MAX_FRAGMENT_PAYLOAD + 1)
    frames = list(fragment(payload, msg_id=0))
    assert len(frames) == 2
    assert frames[0].flags == FrameFlag.START
    assert frames[1].flags == FrameFlag.END


def test_msg_id_wraps():
    frames = list(fragment(b"ok", msg_id=0x1_0000 + 5))
    assert frames[0].msg_id == 5
