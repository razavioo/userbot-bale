from __future__ import annotations

import pytest

from baleobala.vpn.framing_vpn import (
    HEADER_SIZE,
    MAGIC,
    VpnFlag,
    VpnFrame,
    split_packet,
)


def test_single_frame_roundtrip():
    f = VpnFrame(sess_id=0x1234, seq=42, flags=VpnFlag.LAST, payload=b"hello")
    encoded = f.encode()
    assert encoded[0] == MAGIC
    assert len(encoded) == HEADER_SIZE + 5
    decoded = VpnFrame.decode(encoded)
    assert decoded == f


def test_decode_rejects_bad_magic():
    assert VpnFrame.decode(b"\x00" * HEADER_SIZE) is None
    assert VpnFrame.decode(b"") is None


def test_seq_24bit_wrap():
    f = VpnFrame(sess_id=1, seq=(1 << 24) - 1, flags=VpnFlag.LAST, payload=b"x")
    d = VpnFrame.decode(f.encode())
    assert d is not None and d.seq == (1 << 24) - 1
    with pytest.raises(ValueError):
        VpnFrame(sess_id=1, seq=1 << 24, flags=VpnFlag.LAST, payload=b"x").encode()


def test_split_single_frame_when_fits():
    frames = split_packet(b"ab", sess_id=0, start_seq=10, max_payload=100)
    assert len(frames) == 1
    assert VpnFlag.LAST in frames[0].flags
    assert VpnFlag.SPLIT not in frames[0].flags


def test_split_multi_frame():
    packet = bytes(range(256)) * 2  # 512 bytes
    frames = split_packet(packet, sess_id=7, start_seq=1000, max_payload=100)
    assert len(frames) == 6  # 5*100 + 12
    assert all(VpnFlag.SPLIT in f.flags for f in frames)
    assert VpnFlag.LAST in frames[-1].flags
    assert VpnFlag.LAST not in frames[0].flags
    assert [f.seq for f in frames] == [1000, 1001, 1002, 1003, 1004, 1005]
    assert b"".join(f.payload for f in frames) == packet


def test_flags_roundtrip():
    f = VpnFrame(
        sess_id=0, seq=0, flags=VpnFlag.SPLIT | VpnFlag.RETRY, payload=b""
    )
    d = VpnFrame.decode(f.encode())
    assert d is not None
    assert VpnFlag.SPLIT in d.flags
    assert VpnFlag.RETRY in d.flags
    assert VpnFlag.ACK not in d.flags
